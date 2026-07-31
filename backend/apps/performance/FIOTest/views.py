import json
import uuid
import threading
import time
import logging
import asyncio
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from channels.generic.websocket import AsyncWebsocketConsumer
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth

logger = logging.getLogger(__name__)

# 全局存储活动测试
active_tests = {}
active_tests_lock = threading.Lock()

# 存储 consumer 引用
fio_consumers = {}


def ssh_connect(host, auth, port=22, timeout=10):
    """SSH 连接"""
    try:
        return connect_ssh(host, auth, port=port, timeout=timeout), None
    except Exception as exc:
        return None, str(exc)


def scan_dm_devices(ssh):
    """只按 multipath -ll 中的 dm-* 行收集多路径盘。"""
    script = r"""
multipath -ll 2>/dev/null | awk '/ dm-[0-9]+/ {print}' | while read -r line; do
  alias=$(echo "$line" | awk '{print $1}')
  wwid=$(echo "$line" | sed -n 's/.*(\([^)]*\)).*/\1/p')
  dm=$(echo "$line" | grep -o 'dm-[0-9]\+' | head -1)
  [ -n "$dm" ] || continue
  path="/dev/$dm"
  size=$(lsblk -dn -o SIZE "$path" 2>/dev/null | awk '{$1=$1;print}')
  printf '%s|%s|%s|%s|%s\n' "$path" "$dm" "$size" "$alias" "$wwid"
done | sort -V -t '|' -k2,2
"""
    stdin, stdout, stderr = ssh.exec_command(script)
    output = stdout.read().decode(errors='ignore')
    devices = []
    for line in output.splitlines():
        parts = line.split('|')
        if len(parts) < 5:
            continue
        path, name, size, alias, wwid = parts[:5]
        devices.append({
            'path': path,
            'name': name,
            'size': size,
            'alias': alias,
            'wwid': wwid,
            'label': f'{path} ({alias}, {size})' if size else f'{path} ({alias})',
        })
    return devices


def detect_fio_cpu_range(ssh):
    """选择远端存在的预留 CPU 范围；均不存在时不绑定。"""
    script = r"""
online=$(lscpu -p=CPU,ONLINE 2>/dev/null | awk -F, '$1 !~ /^#/ && ($2 == "Y" || $2 == "") {print $1}')
has_range() {
  start=$1
  end=$2
  for cpu in $(seq "$start" "$end"); do
    echo "$online" | grep -qx "$cpu" || return 1
  done
}
if has_range 20 29; then
  echo 20-29
elif has_range 30 39; then
  echo 30-39
fi
"""
    stdin, stdout, stderr = ssh.exec_command(script)
    value = stdout.read().decode(errors='ignore').strip()
    return value if value in ('20-29', '30-39') else ''


@csrf_exempt
@require_http_methods(["POST"])
def validate_hosts(request):
    """验证主机连接"""
    data = json.loads(request.body)
    hosts = data.get('hosts', [])
    try:
        auth = parse_ssh_auth(data)
    except ValueError as exc:
        return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)
    port = int(data.get('port', 22) or 22)

    results = []

    for host in hosts:
        ssh, error = ssh_connect(host, auth, port=port)
        if ssh:
            stdin, stdout, stderr = ssh.exec_command("which fio")
            has_fio = stdout.read().decode().strip() != ''
            devices = scan_dm_devices(ssh) if has_fio else []
            recommended_cpus = detect_fio_cpu_range(ssh) if has_fio else ''

            results.append({
                'host': host,
                'status': 'success' if has_fio else 'warning',
                'message': '连接成功' + ('，FIO 已安装' if has_fio else '，FIO 未安装') + (f'，发现 {len(devices)} 个 dm 设备' if devices else '，未发现 dm 设备'),
                'has_fio': has_fio,
                'devices': devices,
                'recommended_cpus': recommended_cpus
            })
            ssh.close()
        else:
            results.append({
                'host': host,
                'status': 'error',
                'message': error or '连接失败'
            })

    return JsonResponse({'status': 'success', 'results': results})


def _send_to_consumer(task_id, message):
    """发送消息到 WebSocket consumer"""
    with active_tests_lock:
        if task_id not in active_tests:
            return
        consumer = active_tests[task_id].get('consumer')

    if consumer:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                consumer.send(text_data=json.dumps(message))
            )
            loop.close()
        except Exception as e:
            logger.error(f"Send to consumer error: {e}")


def run_fio_test(task_id, host, auth, port, params):
    """运行 FIO 测试（后台线程）"""
    _send_to_consumer(task_id, {
        'type': 'output',
        'data': f'[{host}] 正在连接...\n'
    })

    ssh, error = ssh_connect(host, auth, port=port)
    if not ssh:
        with active_tests_lock:
            if task_id in active_tests:
                active_tests[task_id]['failed_hosts'].append({'host': host, 'error': error})
                active_tests[task_id]['completed_hosts'] += 1
                total_hosts = len(active_tests[task_id]['hosts'])
                finished = active_tests[task_id]['completed_hosts'] >= total_hosts
                active_tests[task_id]['status'] = 'error' if finished else 'partial'
                active_tests[task_id]['error'] = error
        _send_to_consumer(task_id, {
            'type': 'output',
            'data': f'[{host}] 连接失败: {error}\n',
            'host': host
        })
        if finished:
            _send_to_consumer(task_id, {
                'type': 'error',
                'error': error,
                'host': host
            })
        return

    with active_tests_lock:
        active_tests[task_id]['status'] = 'running'

    _send_to_consumer(task_id, {
        'type': 'output',
        'data': f'[{host}] 连接成功，开始测试...\n'
    })

    ioengine = params.get('ioengine', 'libaio')
    filename = params.get('filename', '/dev/sdb')
    pool = params.get('pool', 'pool-rbdtest1')
    rw = params.get('rw', 'randread')
    rwmixread = params.get('rwmixread', 70)
    bs = params.get('bs', '4k')
    iodepth = params.get('iodepth', 64)
    numjobs = params.get('numjobs', 4)
    runtime = params.get('runtime', 60)
    selected_devices = params.get('selected_devices') or []
    cpus_allowed = params.get('cpus_allowed', '')
    cpus_allowed_by_host = params.get('cpus_allowed_by_host') or {}
    cpus_allowed = cpus_allowed_by_host.get(host, cpus_allowed)

    if selected_devices:
        filename = ':'.join(selected_devices)

    def _parse_size_to_bytes(value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        value = str(value).strip()
        if not value:
            return None
        import re
        m = re.match(r'^(\d+(?:\.\d+)?)([KkMmGgTtPp]?)$', value)
        if not m:
            return None
        number = float(m.group(1))
        unit = m.group(2).upper()
        factor = {
            '': 1,
            'K': 1024,
            'M': 1024 ** 2,
            'G': 1024 ** 3,
            'T': 1024 ** 4,
            'P': 1024 ** 5,
        }.get(unit)
        if factor is None:
            return None
        return int(number * factor)

    if ioengine == 'rbd':
        cmd = f'fio --direct=1 --ioengine=rbd --pool={pool} --rbdname={filename}'
    else:
        cmd = f'fio --direct=1 --filename={filename} --ioengine=libaio'
        if filename.startswith('/dev/'):
            devices_to_check = [dev for dev in filename.split(':') if dev]
            _send_to_consumer(task_id, {
                'type': 'output',
                'data': f'[{host}] 检测到裸设备测试: {", ".join(devices_to_check)}\n'
            })
            for dev in devices_to_check:
                stdin2, stdout2, stderr2 = ssh.exec_command(f'test -b {dev} && echo OK || echo MISSING')
                if stdout2.read().decode().strip() != 'OK':
                    _send_to_consumer(task_id, {
                        'type': 'output',
                        'data': f'[{host}] 警告: {dev} 不是可用块设备\n'
                    })

    cmd += f' --iodepth={iodepth} --numjobs={numjobs} --rw={rw} --bs={bs}'
    if rw == 'randrw':
        cmd += f' --rwmixread={rwmixread}'
    cmd += ' --group_reporting --name=mytest'
    cmd += ' --status-interval=1'

    try:
        runtime_int = int(runtime)
    except (TypeError, ValueError):
        runtime_int = 60

    # The UI runtime is authoritative: fio stops by --time_based, timeout is only a guard.
    if runtime_int > 0:
        cmd += f' --runtime={runtime_int} --time_based'

    if cpus_allowed and cpus_allowed.strip():
        cmd += f' --cpus_allowed={cpus_allowed} --cpus_allowed_policy=split'

    if runtime_int > 0:
        guard_timeout = runtime_int + 15
        cmd = f'timeout -s INT -k 10 {guard_timeout}s {cmd}'

    with active_tests_lock:
        if task_id in active_tests:
            active_tests[task_id].setdefault('commands', {})[host] = cmd

    _send_to_consumer(task_id, {
        'type': 'command',
        'command': cmd,
        'host': host
    })
    _send_to_consumer(task_id, {
        'type': 'output',
        'data': f'[{host}] 执行命令: {cmd}\n',
        'host': host
    })

    output_lines = []
    stderr_lines = []
    exit_status = None
    test_stats = {
        'iops': 0,
        'bw_mb': 0,
        'latency_us': 0,
        'cpu_util': 0,
        'read_iops': 0,
        'write_iops': 0,
        'read_bw_mb': 0,
        'write_bw_mb': 0,
    }

    channel = ssh.get_transport().open_session()
    channel.settimeout(0.0)
    channel.exec_command(cmd)

    start_time = time.time()
    timeout = runtime_int + 20 if runtime_int > 0 else 3600

    try:
        while time.time() - start_time < timeout:
            with active_tests_lock:
                if task_id not in active_tests or active_tests[task_id].get('stop'):
                    break

            if channel.recv_ready():
                output = channel.recv(4096).decode('utf-8', errors='ignore')
                if output:
                    output_lines.append(output)

                    _send_to_consumer(task_id, {
                        'type': 'output',
                        'data': output,
                        'host': host
                    })

                    try:
                        import re

                        def parse_iops(value, unit):
                            unit = unit.lower()
                            if unit == 'k':
                                return value * 1000
                            if unit == 'm':
                                return value * 1000000
                            return value

                        def parse_bw_mb(value, unit):
                            if unit == 'M':
                                return value
                            if unit == 'K':
                                return value / 1024
                            if unit == 'G':
                                return value * 1024
                            return value

                        section_matches = re.findall(r'(read|write):.*?IOPS=([\d.]+)([kKmM]?).*?BW=([\d.]+)([KMG])iB/s', output, re.S)
                        if section_matches:
                            for section, iops_val, iops_unit, bw_val, bw_unit in section_matches:
                                parsed_iops = parse_iops(float(iops_val), iops_unit)
                                parsed_bw = parse_bw_mb(float(bw_val), bw_unit)
                                if section == 'read':
                                    test_stats['read_iops'] = parsed_iops
                                    test_stats['read_bw_mb'] = parsed_bw
                                elif section == 'write':
                                    test_stats['write_iops'] = parsed_iops
                                    test_stats['write_bw_mb'] = parsed_bw
                            test_stats['iops'] = test_stats['read_iops'] + test_stats['write_iops']
                            test_stats['bw_mb'] = test_stats['read_bw_mb'] + test_stats['write_bw_mb']
                        else:
                            iops_match = re.search(r'IOPS=([\d.]+)([kKmM]?)', output)
                            if iops_match:
                                test_stats['iops'] = parse_iops(float(iops_match.group(1)), iops_match.group(2))

                            bw_match = re.search(r'BW=([\d.]+)([KMG])iB/s', output)
                            if bw_match:
                                test_stats['bw_mb'] = parse_bw_mb(float(bw_match.group(1)), bw_match.group(2))

                            if rw == 'randrw':
                                test_stats['read_iops'] = test_stats['iops']
                                test_stats['read_bw_mb'] = test_stats['bw_mb']
                                test_stats['write_iops'] = 0
                                test_stats['write_bw_mb'] = 0

                        lat_match = re.search(r'\s+lat\s*\((usec|msec)\):.*?avg=([\d.]+)', output)
                        if lat_match:
                            unit = lat_match.group(1)
                            val = float(lat_match.group(2))
                            if unit == 'usec':
                                test_stats['latency_us'] = val
                            elif unit == 'msec':
                                test_stats['latency_us'] = val * 1000

                        # fio output may be split across SSH recv chunks. Push as soon as
                        # IOPS/BW is available instead of waiting for a latency line.
                        if test_stats['iops'] > 0 or test_stats['bw_mb'] > 0:
                            with active_tests_lock:
                                if task_id in active_tests:
                                    active_tests[task_id].setdefault('host_stats', {})[host] = test_stats.copy()
                                    active_tests[task_id]['stats'] = test_stats.copy()

                            _send_to_consumer(task_id, {
                                'type': 'stats',
                                'stats': test_stats.copy(),
                                'host': host
                            })
                    except Exception as e:
                        logger.error(f"Failed to parse FIO output: {e}")

            if channel.recv_stderr_ready():
                err_output = channel.recv_stderr(4096).decode('utf-8', errors='ignore')
                if err_output:
                    stderr_lines.append(err_output)
                    output_lines.append(err_output)
                    _send_to_consumer(task_id, {
                        'type': 'output',
                        'data': err_output,
                        'host': host
                    })

            if channel.exit_status_ready():
                # Drain remaining stdout/stderr after fio exits so final summary is not lost.
                while channel.recv_ready():
                    output = channel.recv(4096).decode('utf-8', errors='ignore')
                    if output:
                        output_lines.append(output)
                        _send_to_consumer(task_id, {
                            'type': 'output',
                            'data': output,
                            'host': host
                        })
                while channel.recv_stderr_ready():
                    err_output = channel.recv_stderr(4096).decode('utf-8', errors='ignore')
                    if err_output:
                        stderr_lines.append(err_output)
                        output_lines.append(err_output)
                        _send_to_consumer(task_id, {
                            'type': 'output',
                            'data': err_output,
                            'host': host
                        })
                break

            time.sleep(0.1)
        else:
            _send_to_consumer(task_id, {
                'type': 'output',
                'data': f'[{host}] 超过前端设置时长 {runtime_int}s 后仍未退出，保护超时触发\n',
                'host': host
            })
            try:
                channel.close()
            except Exception:
                pass

    except Exception as e:
        with active_tests_lock:
            active_tests[task_id]['error'] = str(e)
        _send_to_consumer(task_id, {
            'type': 'output',
            'data': f'[{host}] 错误: {str(e)}\n'
        })

    try:
        exit_status = channel.recv_exit_status() if not channel.closed else 124
    except Exception:
        exit_status = None

    channel.close()
    ssh.close()

    combined_output = ''.join(output_lines)
    fio_summary_ok = 'Run status group' in combined_output and 'err= 0' in combined_output
    effective_exit_status = 0 if exit_status == 124 and fio_summary_ok else exit_status

    final_status = 'running'
    error_text = ''
    all_hosts_finished = False
    with active_tests_lock:
        if task_id in active_tests:
            test = active_tests[task_id]
            test['completed_hosts'] += 1
            test['output'] = output_lines
            test['stats'] = test_stats
            test['completed_at'] = time.time()
            total_hosts = len(test['hosts'])
            all_hosts_finished = test['completed_hosts'] >= total_hosts

            if effective_exit_status not in (None, 0):
                error_text = f'[{host}] fio 退出码 {effective_exit_status}'
                if stderr_lines:
                    error_text += '\n' + ''.join(stderr_lines[-5:])
                test['failed_hosts'].append({'host': host, 'error': error_text})
                test['error'] = error_text

            if not all_hosts_finished:
                final_status = 'running'
            elif test.get('stop'):
                final_status = 'stopped'
            elif not test['failed_hosts']:
                final_status = 'completed'
            elif len(test['failed_hosts']) < total_hosts:
                final_status = 'partial'
            else:
                final_status = 'error'
            test['status'] = final_status

    if effective_exit_status not in (None, 0):
        _send_to_consumer(task_id, {
            'type': 'output',
            'data': error_text + '\n',
            'host': host
        })

    _send_to_consumer(task_id, {
        'type': 'output',
        'data': f'[{host}] 测试完成\n',
        'host': host
    })

    if all_hosts_finished:
        _send_to_consumer(task_id, {
            'type': 'completed',
            'stats': active_tests.get(task_id, {}).get('stats', test_stats),
            'status': final_status
        })


@csrf_exempt
@require_http_methods(["POST"])
def start_test(request):
    """开始 FIO 测试"""
    data = json.loads(request.body)
    hosts = data.get('hosts', [])
    try:
        auth = parse_ssh_auth(data)
    except ValueError as exc:
        return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)
    port = int(data.get('port', 22) or 22)
    test_params = data.get('params', {})

    if not hosts:
        return JsonResponse({'status': 'error', 'error': '请添加至少一台主机'}, status=400)

    task_id = str(uuid.uuid4())

    active_tests[task_id] = {
        'id': task_id,
        'hosts': hosts,
        'status': 'starting',
        'output': [],
        'stats': {},
        'host_stats': {},
        'commands': {},
        'stop': False,
        'start_time': time.time(),
        'completed_hosts': 0,
        'failed_hosts': [],
        'auth': auth,
        'port': port,
    }

    for i, host in enumerate(hosts):
        thread_params = test_params.copy()
        thread = threading.Thread(
            target=run_fio_test,
            args=(task_id, host, auth, port, thread_params)
        )
        thread.daemon = True
        thread.start()

    return JsonResponse({
        'status': 'success',
        'task_id': task_id,
        'message': f'已在 {len(hosts)} 台主机启动测试'
    })


def _stop_remote_fio(host, auth, port):
    """Stop all fio processes on one test host and verify they exited."""
    ssh, error = ssh_connect(host, auth, port)
    if not ssh:
        return {'host': host, 'success': False, 'error': f'SSH 连接失败: {error}'}

    command = (
        "killall -INT fio 2>/dev/null || true; "
        "for i in 1 2 3 4 5; do pgrep -x fio >/dev/null || exit 0; sleep 1; done; "
        "killall -KILL fio 2>/dev/null || true; sleep 1; "
        "pgrep -x fio >/dev/null && exit 1 || exit 0"
    )
    try:
        stdin, stdout, stderr = ssh.exec_command(command, timeout=12)
        error_text = stderr.read().decode('utf-8', errors='ignore').strip()
        exit_status = stdout.channel.recv_exit_status()
        return {
            'host': host,
            'success': exit_status == 0,
            'error': error_text or ('fio 进程仍然存在' if exit_status else ''),
        }
    except Exception as exc:
        return {'host': host, 'success': False, 'error': str(exc)}
    finally:
        ssh.close()


@csrf_exempt
@require_http_methods(["POST"])
def stop_test(request):
    """停止任务，并在所有源端终止 fio 进程。"""
    data = json.loads(request.body)
    task_id = data.get('task_id')

    with active_tests_lock:
        test = active_tests.get(task_id)
        if not test:
            return JsonResponse({'status': 'error', 'error': '任务不存在'}, status=404)
        test['stop'] = True
        test['status'] = 'stopping'
        hosts = list(test.get('hosts', []))
        auth = test.get('auth')
        port = test.get('port', 22)

    results = [_stop_remote_fio(host, auth, port) for host in hosts]
    failed = [result for result in results if not result['success']]

    with active_tests_lock:
        if task_id in active_tests:
            active_tests[task_id]['status'] = 'stop_failed' if failed else 'stopped'
            active_tests[task_id]['stop_results'] = results

    if failed:
        return JsonResponse({
            'status': 'error',
            'error': '部分源端 fio 停止失败',
            'results': results,
        }, status=500)
    return JsonResponse({'status': 'success', 'results': results})


@csrf_exempt
@require_http_methods(["GET"])
def get_output(request):
    """获取测试输出"""
    task_id = request.GET.get('task_id')

    if not task_id or task_id not in active_tests:
        return JsonResponse({'status': 'error', 'error': '任务不存在'}, status=404)

    test = active_tests[task_id]

    return JsonResponse({
        'status': test['status'],
        'output': test.get('output', []),
        'stats': test.get('stats', {}),
        'host_stats': test.get('host_stats', {}),
        'commands': test.get('commands', {}),
        'failed_hosts': test.get('failed_hosts', []),
        'elapsed': time.time() - test.get('start_time', time.time()),
    })


@csrf_exempt
@require_http_methods(["GET"])
def get_results(request):
    """获取测试结果"""
    task_id = request.GET.get('task_id')

    if not task_id or task_id not in active_tests:
        return JsonResponse({'status': 'error', 'error': '任务不存在'}, status=404)

    test = active_tests[task_id]

    return JsonResponse({
        'status': test['status'],
        'stats': test.get('stats', {}),
        'host_stats': test.get('host_stats', {}),
        'commands': test.get('commands', {}),
        'failed_hosts': test.get('failed_hosts', []),
        'elapsed': time.time() - test.get('start_time', time.time()),
    })


class FIOTestConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for FIO test real-time output"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_id = None
        self.task_id = None

    async def connect(self):
        self.client_id = str(uuid.uuid4())
        fio_consumers[self.client_id] = self
        await self.accept()
        logger.info(f"FIO WebSocket connected: {self.client_id}")

    async def disconnect(self, close_code):
        logger.info(f"FIO WebSocket disconnected: {self.client_id}")
        fio_consumers.pop(self.client_id, None)

        if self.task_id:
            with active_tests_lock:
                if self.task_id in active_tests:
                    active_tests[self.task_id].pop('consumer', None)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'join':
                await self.handle_join(data)
            elif action == 'stop':
                await self.handle_stop(data)

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({'type': 'error', 'error': 'Invalid JSON'}))
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")
            await self.send(text_data=json.dumps({'type': 'error', 'error': str(e)}))

    async def handle_join(self, data):
        task_id = data.get('task_id')

        if not task_id:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': '缺少 task_id'
            }))
            return

        with active_tests_lock:
            if task_id in active_tests:
                self.task_id = task_id
                active_tests[task_id]['consumer'] = self
                logger.info(f"WebSocket joined FIO task: {task_id}")

                await self.send(text_data=json.dumps({
                    'type': 'joined',
                    'task_id': task_id
                }))
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'error': '任务不存在'
                }))

    async def handle_stop(self, data):
        task_id = data.get('task_id')

        if task_id:
            with active_tests_lock:
                if task_id in active_tests:
                    active_tests[task_id]['stop'] = True
                    active_tests[task_id]['status'] = 'stopped'

            await self.send(text_data=json.dumps({
                'type': 'stopped',
                'task_id': task_id
            }))
