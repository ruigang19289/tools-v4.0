"""Ansible management API: host validation, commands, file transfer, and playbooks."""
import json
import logging
import os
import re
import socket
import subprocess
import tempfile

import paramiko
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)
KEY_AUTH_METHOD = 'ssh_key'
PASSWORD_AUTH_METHOD = 'password'
DEFAULT_PRIVATE_KEY_PATH = '/root/.ssh/id_ed25519'


def get_private_key_path():
    """Return the operator-provided key path; never accept this from the web request."""
    path = os.environ.get('TOOLS_ANSIBLE_PRIVATE_KEY_PATH', DEFAULT_PRIVATE_KEY_PATH)
    if not os.path.isfile(path):
        return None, f'SSH 私钥不存在：{path}。请在容器中只读挂载私钥并设置 TOOLS_ANSIBLE_PRIVATE_KEY_PATH。'
    if not os.access(path, os.R_OK):
        return None, f'SSH 私钥不可读：{path}'
    return path, None


def normalize_host(host_info):
    """Validate one host and resolve password/key authentication server-side."""
    if not isinstance(host_info, dict):
        host_info = {'ip': str(host_info)}
    ip = str(host_info.get('ip') or '').strip()
    username = str(host_info.get('username') or 'root').strip()
    auth_method = str(host_info.get('auth_method') or PASSWORD_AUTH_METHOD).strip()
    password = str(host_info.get('password') or '')
    try:
        port = int(host_info.get('port') or 22)
    except (TypeError, ValueError):
        return None, 'SSH 端口格式错误'
    if not ip:
        return None, 'IP 不能为空'
    if not username:
        return None, '用户名不能为空'
    if not 1 <= port <= 65535:
        return None, 'SSH 端口范围应为 1-65535'
    if auth_method == KEY_AUTH_METHOD:
        key_path, error = get_private_key_path()
        if error:
            return None, error
        return {'ip': ip, 'username': username, 'port': port, 'auth_method': auth_method, 'key_path': key_path}, None
    if auth_method != PASSWORD_AUTH_METHOD:
        return None, '不支持的认证方式'
    if not password:
        return None, '密码不能为空'
    return {'ip': ip, 'username': username, 'port': port, 'auth_method': auth_method, 'password': password}, None


def ssh_connect(host, timeout=10):
    """Connect with password or the server-side mounted SSH private key."""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            'hostname': host['ip'], 'port': host['port'], 'username': host['username'],
            'timeout': timeout, 'banner_timeout': timeout, 'auth_timeout': timeout,
            'look_for_keys': False, 'allow_agent': False,
        }
        if host['auth_method'] == KEY_AUTH_METHOD:
            kwargs['key_filename'] = host['key_path']
            kwargs['password'] = None
        else:
            kwargs['password'] = host['password']
        ssh.connect(**kwargs)
        return ssh, None
    except paramiko.AuthenticationException:
        return None, '认证失败，请检查远端授权或认证信息'
    except paramiko.PasswordRequiredException:
        return None, '当前 SSH 私钥带有口令，容器模式暂不支持交互式输入私钥口令'
    except paramiko.SSHException as exc:
        return None, f'SSH 协议错误: {exc}'
    except socket.timeout:
        return None, '连接超时'
    except OSError as exc:
        return None, f'网络错误: {exc}'
    except Exception as exc:
        logger.exception('SSH connect failed for host %s', host['ip'])
        return None, str(exc)


def execute_ssh_command(ssh, command, timeout=60):
    try:
        _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        return (stdout.read().decode('utf-8', errors='ignore'),
                stderr.read().decode('utf-8', errors='ignore'), stdout.channel.recv_exit_status())
    except Exception as exc:
        return '', str(exc), 1


def split_ansible_output_by_host(stdout, stderr, host_ips, success):
    combined = stdout or stderr or ''
    chunks = {ip: [] for ip in host_ips}
    current_ip = None
    header_re = re.compile(r'^(?P<ip>\S+)\s+\|\s+(?:SUCCESS|CHANGED|FAILED|UNREACHABLE)')
    for line in combined.splitlines():
        match = header_re.match(line)
        if match and match.group('ip') in chunks:
            current_ip = match.group('ip')
            chunks[current_ip].append(line)
        elif current_ip:
            chunks[current_ip].append(line)
    fallback = combined.strip()
    return [{'ip': ip, 'success': success and 'UNREACHABLE' not in ('\n'.join(chunks[ip]) or fallback) and 'FAILED' not in ('\n'.join(chunks[ip]) or fallback), 'output': '\n'.join(chunks[ip]).strip() or fallback} for ip in host_ips]


def run_ansible_command(cmd_args):
    try:
        env = os.environ.copy()
        env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
        if cmd_args and cmd_args[0] in ('ansible', 'ansible-playbook'):
            cmd_args = [os.path.join(os.path.dirname(os.sys.executable), cmd_args[0]), *cmd_args[1:]]
        result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=300, env=env)
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return 1, '', str(exc)


def make_inventory(path, hosts):
    """Use JSON inventory so request values cannot be parsed as INI variables."""
    hostvars = {}
    for host in hosts:
        variables = {'ansible_user': host['username'], 'ansible_port': host['port'], 'ansible_python_interpreter': '/usr/bin/python3'}
        if host['auth_method'] == KEY_AUTH_METHOD:
            variables['ansible_ssh_private_key_file'] = host['key_path']
        else:
            variables['ansible_ssh_pass'] = host['password']
        hostvars[host['ip']] = variables
    with open(path, 'w', encoding='utf-8') as inventory:
        # Ansible's YAML inventory plugin requires all.hosts to be a mapping,
        # not a list. Host variables belong directly under each host name.
        json.dump({'all': {'hosts': hostvars, 'vars': {}}}, inventory)


def parse_hosts(raw_hosts):
    if not isinstance(raw_hosts, list) or not raw_hosts:
        return None, '请提供主机列表'
    hosts = []
    for item in raw_hosts:
        host, error = normalize_host(item)
        if error:
            return None, error
        hosts.append(host)
    return hosts, None


@csrf_exempt
@require_http_methods(['POST'])
def validate_hosts(request):
    try:
        data = json.loads(request.body or b'{}')
    except json.JSONDecodeError as exc:
        return JsonResponse({'status': 'error', 'error': f'请求 JSON 格式错误: {exc}'}, status=400)
    raw_hosts = data.get('hosts', [])
    results = []
    for item in raw_hosts if isinstance(raw_hosts, list) else []:
        host, error = normalize_host(item)
        ip = str(item.get('ip') or 'unknown') if isinstance(item, dict) else str(item)
        if error:
            results.append({'ip': ip, 'status': 'error', 'message': error})
            continue
        ssh, error = ssh_connect(host)
        if ssh:
            ssh.close()
            results.append({'ip': host['ip'], 'status': 'success', 'message': '连接成功'})
        else:
            results.append({'ip': host['ip'], 'status': 'error', 'message': error or '连接失败'})
    if not isinstance(raw_hosts, list) or not raw_hosts:
        return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)
    return JsonResponse({'status': 'success', 'results': results})


@csrf_exempt
@require_http_methods(['POST'])
def execute_command(request):
    try:
        data = json.loads(request.body or b'{}')
        hosts, error = parse_hosts(data.get('hosts', []))
        module, command = data.get('module', 'shell'), data.get('command', '')
        if error:
            return JsonResponse({'status': 'error', 'error': error}, status=400)
        if module not in ['ping', 'setup'] and not command:
            return JsonResponse({'status': 'error', 'error': '请提供命令'}, status=400)
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory_path = os.path.join(tmpdir, 'inventory.json')
            make_inventory(inventory_path, hosts)
            args = ['ansible', 'all', '-i', inventory_path, '-m', module]
            if module not in ['ping', 'setup']:
                args += ['-a', command]
            code, stdout, stderr = run_ansible_command(args)
        return JsonResponse({'status': 'success', 'results': split_ansible_output_by_host(stdout, stderr, [host['ip'] for host in hosts], code == 0)})
    except Exception as exc:
        logger.exception('execute_command failed')
        return JsonResponse({'status': 'error', 'error': str(exc)}, status=500)


@csrf_exempt
@require_http_methods(['POST'])
def file_transfer(request):
    try:
        data = json.loads(request.body or b'{}')
        hosts, error = parse_hosts(data.get('hosts', []))
        action, source, dest, backup = data.get('action', 'push'), data.get('source', ''), data.get('dest', ''), data.get('backup', True)
        if error or not source or not dest:
            return JsonResponse({'status': 'error', 'error': error or '参数不完整'}, status=400)
        if action not in ('push', 'pull'):
            return JsonResponse({'status': 'error', 'error': '不支持的文件传输方式'}, status=400)
        results = []
        for host in hosts:
            result = {'ip': host['ip'], 'success': False}
            ssh, error = ssh_connect(host)
            if not ssh:
                result['error'] = f'连接失败: {error}'
                results.append(result)
                continue
            try:
                if action == 'push' and backup:
                    execute_ssh_command(ssh, f'test -f {dest} && cp {dest} {dest}.bak || true')
                sftp = ssh.open_sftp()
                try:
                    if action == 'push':
                        sftp.put(source, dest)
                        result.update(success=True, message=f'文件已推送到 {dest}')
                    else:
                        sftp.get(dest, source)
                        result.update(success=True, message=f'文件已拉取到 {source}')
                finally:
                    sftp.close()
            except Exception as exc:
                result['error'] = str(exc)
            finally:
                ssh.close()
                results.append(result)
        return JsonResponse({'status': 'success', 'results': results})
    except Exception as exc:
        logger.exception('file_transfer failed')
        return JsonResponse({'status': 'error', 'error': str(exc)}, status=500)


@csrf_exempt
@require_http_methods(['POST'])
def run_playbook(request):
    try:
        data = json.loads(request.body or b'{}')
        hosts, error = parse_hosts(data.get('hosts', []))
        playbook = data.get('playbook', '')
        if error or not playbook:
            return JsonResponse({'status': 'error', 'error': error or '请提供 Playbook'}, status=400)
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory_path, playbook_path = os.path.join(tmpdir, 'inventory.json'), os.path.join(tmpdir, 'playbook.yml')
            make_inventory(inventory_path, hosts)
            with open(playbook_path, 'w', encoding='utf-8') as output:
                output.write(playbook)
            code, stdout, stderr = run_ansible_command(['ansible-playbook', playbook_path, '-i', inventory_path, '-v'])
        results = split_ansible_output_by_host(stdout, stderr, [host['ip'] for host in hosts], code == 0)
        return JsonResponse({'status': 'success', 'results': results} if code == 0 else {'status': 'error', 'error': stderr or stdout, 'results': results}, status=200 if code == 0 else 500)
    except Exception as exc:
        logger.exception('run_playbook failed')
        return JsonResponse({'status': 'error', 'error': str(exc)}, status=500)


def parse_playbook(playbook_text):
    import yaml
    try:
        return [task for play in yaml.safe_load(playbook_text) for task in play.get('tasks', [])]
    except Exception:
        return []
