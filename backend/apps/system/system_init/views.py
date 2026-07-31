"""
系统初始化工具 - 后端API
功能：
- SSH连接验证
- 批量修改主机名
- NTP时钟同步配置
- SSH免密登录配置
- SELinux关闭
- 安全加固（防火墙规则）
"""
import json
import threading
import time
import uuid
import re
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import ipaddress
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth

# 活跃任务存储
active_tasks = {}


def get_size_str(size):
    """将字节转换为可读大小字符串"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def parse_ip_range(host_input):
    """解析IP范围字符串，返回IP数组"""
    # 检查是否是 IP 范围格式 (如 192.168.1.5-192.168.1.10)
    range_match = re.match(r'^(\d+\.\d+\.\d+\.)(\d+)-(\d+\.\d+\.\d+\.)(\d+)$', host_input)
    if range_match:
        prefix1 = range_match.group(1)
        start = int(range_match.group(2))
        prefix2 = range_match.group(3)
        end = int(range_match.group(4))

        if prefix1 == prefix2:
            hosts = []
            for i in range(start, end + 1):
                hosts.append(prefix1 + str(i))
            return hosts

    # 检查简化格式 (如 192.168.1.5-10)
    simple_range_match = re.match(r'^(\d+\.\d+\.\d+\.)(\d+)-(\d+)$', host_input)
    if simple_range_match:
        prefix = simple_range_match.group(1)
        start = int(simple_range_match.group(2))
        end = int(simple_range_match.group(3))

        hosts = []
        for i in range(start, end + 1):
            hosts.append(prefix + str(i))
        return hosts

    # CIDR格式
    if '/' in host_input:
        try:
            network = ipaddress.ip_network(host_input, strict=False)
            return [str(ip) for ip in network]
        except ValueError:
            pass

    # 不是范围格式，返回单个主机
    return [host_input]


def ssh_connect(host, port, auth, timeout=10):
    """创建SSH连接并返回客户端"""
    try:
        return connect_ssh(host, auth, port=port, timeout=timeout), None
    except Exception as exc:
        return None, str(exc)


def get_host_auth(host_info):
    """Parse the shared authentication fields carried by a host entry."""
    return parse_ssh_auth(host_info)


def validate_hosts_auth(hosts):
    """Validate each host entry before starting a batch operation."""
    for host_info in hosts:
        get_host_auth(host_info)


def execute_ssh_command(ssh, command, timeout=60):
    """执行SSH命令"""
    try:
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        return output, error, None
    except Exception as e:
        return '', str(e), e


@csrf_exempt
@require_http_methods(["POST"])
def validate_hosts(request):
    """验证主机SSH连接"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        results = []
        for host_info in hosts:
            ip = host_info.get('ip')
            port = int(host_info.get('port', 22))
            auth = get_host_auth(host_info)

            ssh, error = ssh_connect(ip, port, auth)

            if ssh:
                # 获取主机名
                _, hostname_output, _ = execute_ssh_command(ssh, 'hostname')
                hostname = hostname_output.strip() or ip

                ssh.close()
                results.append({
                    'ip': ip,
                    'hostname': hostname,
                    'status': 'success',
                    'message': '连接成功'
                })
            else:
                results.append({
                    'ip': ip,
                    'hostname': ip,
                    'status': 'error',
                    'message': error
                })

        return JsonResponse({
            'status': 'success',
            'results': results
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


def generate_hostname_from_ip(ip, index=0, prefix='node'):
    """根据IP地址生成主机名
    
    Args:
        ip: IP地址
        index: 基于IP排序后的序号（从0开始）
        prefix: 主机名前缀
    """
    # 使用前缀 + 序号（从1开始）
    hostname = f'{prefix}{index + 1}'
    return hostname


def ip_to_int(ip):
    """将IP转换为整数用于排序"""
    parts = ip.split('.')
    if len(parts) == 4:
        return int(parts[0]) << 24 | int(parts[1]) << 16 | int(parts[2]) << 8 | int(parts[3])
    return 0


def sort_hosts_by_ip(hosts):
    """按IP大小排序主机列表"""
    return sorted(hosts, key=lambda h: ip_to_int(h.get('ip', '')))


def process_host_init(ssh, host_info, config, result_container, task_id):
    """处理单个主机的初始化操作"""
    ip = host_info.get('ip')
    hostname = host_info.get('hostname', generate_hostname_from_ip(ip))
    auth = get_host_auth(host_info)

    result = {
        'ip': ip,
        'hostname': hostname,
        'success': True,
        'message': '',
        'logs': []
    }

    try:
        # 1. 修改主机名
        if config.get('modify_hostname') and hostname:
            new_hostname = hostname if hostname != ip else generate_hostname_from_ip(ip)
            # 临时修改主机名
            execute_ssh_command(ssh, f'hostname {new_hostname}')
            # 修改 /etc/hostname
            execute_ssh_command(ssh, f'echo "{new_hostname}" > /etc/hostname')
            # 修改 /etc/hosts
            execute_ssh_command(ssh,
                f'sed -i "s/127.0.0.1.*localhost/127.0.0.1 localhost {new_hostname}/" /etc/hosts')
            execute_ssh_command(ssh,
                f'grep -q "{ip}" /etc/hosts || echo "{ip} {new_hostname}" >> /etc/hosts')
            result['logs'].append(f'[OK] 主机名已修改为: {new_hostname}')

        # 2. 配置NTP
        if config.get('configure_ntp'):
            ntp_servers_str = config.get('ntp_servers', 'ntp.aliyun.com')
            # 解析 NTP 服务器列表
            ntp_servers = [s.strip() for s in ntp_servers_str.split('\n') if s.strip()]

            # 安装chrony
            execute_ssh_command(ssh, 'which chronyd || yum install -y chrony 2>/dev/null || apt-get install -y chrony 2>/dev/null')

            # 备份并删除原配置
            execute_ssh_command(ssh, 'cp /etc/chrony.conf /etc/chrony.conf.bak.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true')
            execute_ssh_command(ssh, 'rm -f /etc/chrony.conf')

            # 写入新的精简配置（逐行写入）
            # 第一个服务器添加 prefer 参数
            if len(ntp_servers) > 0:
                first_server = ntp_servers[0]
                if len(ntp_servers) >= 2:
                    execute_ssh_command(ssh, f'echo "server {first_server} iburst prefer" > /etc/chrony.conf')
                else:
                    execute_ssh_command(ssh, f'echo "server {first_server} iburst" > /etc/chrony.conf')

                # 其他服务器不添加 prefer
                for server in ntp_servers[1:]:
                    execute_ssh_command(ssh, f'echo "server {server} iburst" >> /etc/chrony.conf')

            execute_ssh_command(ssh, 'echo "#allow 192.168.0.0/16" >> /etc/chrony.conf')
            execute_ssh_command(ssh, 'echo "#local stratum 10" >> /etc/chrony.conf')
            execute_ssh_command(ssh, 'echo "logdir /var/log/chrony" >> /etc/chrony.conf')
            execute_ssh_command(ssh, 'echo "makestep 1.0 3" >> /etc/chrony.conf')
            execute_ssh_command(ssh, 'echo "rtcsync" >> /etc/chrony.conf')
            execute_ssh_command(ssh, 'echo "driftfile /var/lib/chrony/drift" >> /etc/chrony.conf')

            # 启动并重启服务
            execute_ssh_command(ssh, 'systemctl enable chronyd 2>/dev/null')
            execute_ssh_command(ssh, 'systemctl restart chronyd 2>/dev/null')

            # 启用NTP同步
            execute_ssh_command(ssh, 'timedatectl set-ntp true 2>/dev/null')

            # 强制同步一次
            execute_ssh_command(ssh, 'chronyc -a makestep 2>/dev/null || true')

            result['logs'].append(f'[OK] NTP同步已配置: {", ".join(ntp_servers)}')

        # 3. SSH免密登录配置
        if config.get('configure_ssh'):
            # 生成SSH密钥
            execute_ssh_command(ssh, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
            execute_ssh_command(ssh, 'ssh-keygen -t rsa -N "" -f ~/.ssh/id_rsa -q')
            # 收集公钥
            pubkey, _, _ = execute_ssh_command(ssh, 'cat ~/.ssh/id_rsa.pub')
            # 将公钥写入authorized_keys
            execute_ssh_command(ssh, f'echo "{pubkey.strip()}" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys')
            # 完善SSH配置
            ssh_config = '''
Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
'''
            execute_ssh_command(ssh, f'echo "{ssh_config}" >> ~/.ssh/config')
            result['logs'].append('[OK] SSH免密登录已配置')

        # 4. 关闭SELinux
        if config.get('disable_selinux'):
            # 临时关闭 SELinux
            output, error, _ = execute_ssh_command(ssh, 'setenforce 0 2>&1')
            if error or 'setenforce: command not found' in output:
                result['logs'].append('[SKIP] setenforce 命令不可用或 SELinux 未安装')
            else:
                result['logs'].append('[OK] 已执行 setenforce 0（临时关闭）')

            # 永久关闭 SELinux（修改配置文件）
            config_output, _, _ = execute_ssh_command(ssh, 'test -f /etc/selinux/config && echo "exists" || echo "not found"')
            if 'exists' in config_output:
                execute_ssh_command(ssh, 'sed -i "s/SELINUX=enforcing/SELINUX=disabled/g" /etc/selinux/config')
                execute_ssh_command(ssh, 'sed -i "s/SELINUX=permissive/SELINUX=disabled/g" /etc/selinux/config')
                result['logs'].append('[OK] 已修改配置文件，永久生效需重启主机')
            else:
                result['logs'].append('[SKIP] SELinux 配置文件不存在')

        # 5. 防火墙配置（包含 SELinux 和防火墙）
        if config.get('configure_firewall'):
            # 关闭 SELinux
            output, error, _ = execute_ssh_command(ssh, 'setenforce 0 2>&1')
            if error or 'setenforce: command not found' in output:
                result['logs'].append('[SKIP] setenforce 命令不可用或 SELinux 未安装')
            else:
                result['logs'].append('[OK] 已执行 setenforce 0（临时关闭）')

            config_output, _, _ = execute_ssh_command(ssh, 'test -f /etc/selinux/config && echo "exists" || echo "not found"')
            if 'exists' in config_output:
                execute_ssh_command(ssh, 'sed -i "s/SELINUX=enforcing/SELINUX=disabled/g" /etc/selinux/config')
                execute_ssh_command(ssh, 'sed -i "s/SELINUX=permissive/SELINUX=disabled/g" /etc/selinux/config')
                result['logs'].append('[OK] 已修改 SELinux 配置文件，永久生效需重启主机')

            # 停止并禁用 firewalld
            execute_ssh_command(ssh, 'systemctl stop firewalld 2>/dev/null || true')
            execute_ssh_command(ssh, 'systemctl disable firewalld 2>/dev/null || true')
            result['logs'].append('[OK] 已停止并禁用 firewalld')

        # 6. 安全加固
        if config.get('security_hardening'):
            iptables_commands = config.get('iptables_commands', [])
            result['logs'].append(f'[INFO] 收到 {len(iptables_commands)} 条 iptables 命令')

            if iptables_commands:
                # 执行所有 iptables 命令（检查是否已存在）
                executed_count = 0
                for i, cmd in enumerate(iptables_commands, 1):
                    # 将 iptables -I/-A 命令转换为 -C 命令来检查规则是否存在
                    check_cmd = cmd.replace('iptables -I', 'iptables -C').replace('iptables -A', 'iptables -C')
                    
                    # 检查规则是否已存在（使用 && || 判断退出码）
                    check_result, _, _ = execute_ssh_command(ssh, f'{check_cmd} 2>/dev/null && echo "EXISTS" || echo "NOT_EXISTS"')
                    
                    # 如果规则已存在，跳过
                    if 'EXISTS' in check_result:
                        continue
                    
                    # 规则不存在，执行命令
                    output, error, _ = execute_ssh_command(ssh, cmd)
                    if error and 'already exists' not in error.lower():
                        result['logs'].append(f'[WARNING] 命令 {i}: {error[:100]}')
                    else:
                        executed_count += 1
                
                result['logs'].append(f'[OK] 已执行 {executed_count} 条新的 iptables 规则')

                # chmod +x /etc/rc.d/rc.local
                output, error, _ = execute_ssh_command(ssh, 'chmod +x /etc/rc.d/rc.local')
                if error:
                    result['logs'].append(f'[WARNING] chmod 错误: {error}')
                result['logs'].append('[OK] 已设置 /etc/rc.d/rc.local 可执行权限')

                # 写入 /etc/rc.local（检测是否已存在）
                added_count = 0
                for cmd in iptables_commands:
                    # 检查命令是否已存在于 /etc/rc.local（使用 grep -Fx 返回匹配的行）
                    escaped_cmd_check = cmd.replace("'", "'\''")
                    check_cmd = f"grep -Fx '{escaped_cmd_check}' /etc/rc.local 2>/dev/null || true"
                    output, error, _ = execute_ssh_command(ssh, check_cmd)
                    
                    # 如果 output 不为空，说明命令已存在
                    if output.strip():
                        continue
                    
                    # 命令不存在，写入
                    echo_cmd = f"echo '{escaped_cmd_check}' >> /etc/rc.local"
                    output, error, _ = execute_ssh_command(ssh, echo_cmd)
                    if not error:
                        added_count += 1
                
                if added_count > 0:
                    result['logs'].append(f'[OK] 已将 {added_count} 条新规则写入 /etc/rc.local')
                else:
                    result['logs'].append('[OK] 所有规则已存在于 /etc/rc.local')

                # 执行 /etc/rc.local 使规则立即生效
                output, error, _ = execute_ssh_command(ssh, 'bash /etc/rc.local')
                if error:
                    result['logs'].append(f'[WARNING] 执行 /etc/rc.local 错误: {error[:100]}')
                else:
                    result['logs'].append('[OK] 已执行 /etc/rc.local，规则立即生效')

                result['logs'].append('[OK] 安全加固已完成')
            else:
                result['logs'].append('[WARNING] 未提供 iptables 命令')

        result['message'] = '\n'.join(result['logs']) if result['logs'] else '操作已完成'

    except Exception as e:
        result['success'] = False
        result['message'] = f'错误: {str(e)}'

    result_container.append(result)


@csrf_exempt
@require_http_methods(["POST"])
def full_init(request):
    """执行完整系统初始化"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])
        hostname_prefix = data.get('hostname_prefix', 'node')
        ntp_servers = data.get('ntp_servers', 'ntp.aliyun.com')
        management_cidr = data.get('management_cidr', '10.255.0.0/24')
        harden_etcd = data.get('harden_etcd', True)
        harden_postgresql = data.get('harden_postgresql', True)
        harden_elasticsearch = data.get('harden_elasticsearch', True)

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        # 按IP排序并生成主机名
        sorted_hosts = sort_hosts_by_ip(hosts)
        for index, host in enumerate(sorted_hosts):
            host['hostname'] = generate_hostname_from_ip(host.get('ip'), index, hostname_prefix)

        all_results = []

        # 1. 修改主机名和更新 hosts 文件
        host_mappings = []
        for host_info in sorted_hosts:
            ip = host_info.get('ip')
            hostname = host_info.get('hostname')
            if hostname:
                host_mappings.append((ip, hostname))

        for host_info in sorted_hosts:
            ip = host_info.get('ip')
            auth = get_host_auth(host_info)
            port = int(host_info.get('port', 22))
            hostname = host_info.get('hostname')

            result = {
                'ip': ip,
                'hostname': hostname,
                'success': True,
                'message': '',
                'logs': []
            }

            ssh, error = ssh_connect(ip, port, auth)
            if not ssh:
                result['success'] = False
                result['message'] = f'连接失败: {error}'
                all_results.append(result)
                continue

            try:
                # 修改主机名
                if hostname and hostname != ip:
                    execute_ssh_command(ssh, f'hostname {hostname}')
                    execute_ssh_command(ssh, f'echo "{hostname}" > /etc/hostname')
                    execute_ssh_command(ssh, f'sed -i "s/127.0.0.1.*localhost/127.0.0.1 localhost {hostname}/" /etc/hosts')
                    result['logs'].append(f'[OK] 主机名已修改为: {hostname}')

                # 更新 /etc/hosts 文件
                for map_ip, map_hostname in host_mappings:
                    # 检查是否已存在该 IP 的条目
                    check_output, _, _ = execute_ssh_command(ssh, f'grep "^{map_ip}[[:space:]]" /etc/hosts || true')
                    if check_output.strip():  # 已存在，更新
                        execute_ssh_command(ssh, f'sed -i "s/^{map_ip}[[:space:]].*/{map_ip} {map_hostname}/" /etc/hosts')
                    else:  # 不存在，添加
                        execute_ssh_command(ssh, f'echo "{map_ip} {map_hostname}" >> /etc/hosts')
                result['logs'].append(f'[OK] hosts 文件已更新')

            except Exception as e:
                result['success'] = False
                result['message'] = f'主机名配置错误: {str(e)}'
            finally:
                ssh.close()
                all_results.append(result)

        # 2. 配置其他功能（NTP、SELinux、防火墙）
        config = {
            'configure_ntp': True,
            'ntp_servers': ntp_servers,
            'disable_selinux': True,
            'configure_firewall': True,
            'security_hardening': False,  # 暂时禁用，因为缺少 iptables_commands
            'management_cidr': management_cidr,
            'harden_etcd': harden_etcd,
            'harden_postgresql': harden_postgresql,
            'harden_elasticsearch': harden_elasticsearch,
        }

        other_results = execute_parallel_init(sorted_hosts, config)

        # 合并结果
        # 合并结果（使用 IP 匹配）
        for other_result in other_results:
            result = next((r for r in all_results if r['ip'] == other_result.get('ip')), None)
            if result:
                result['logs'].extend(other_result.get('logs', []))
                if not other_result.get('success', True):
                    result['success'] = False
                result['message'] = '\n'.join(result['logs']) if result['logs'] else '操作已完成'
        all_pubkeys = []
        for host_info in sorted_hosts:
            ip = host_info.get('ip')
            auth = get_host_auth(host_info)
            port = int(host_info.get('port', 22))

            result = next((r for r in all_results if r['ip'] == ip), None)
            if not result:
                continue

            ssh, error = ssh_connect(ip, port, auth)
            if not ssh:
                continue

            try:
                execute_ssh_command(ssh, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
                execute_ssh_command(ssh, 'test -f ~/.ssh/id_rsa || ssh-keygen -t rsa -N "" -f ~/.ssh/id_rsa -q')
                pubkey, _, _ = execute_ssh_command(ssh, 'cat ~/.ssh/id_rsa.pub')
                if pubkey:
                    all_pubkeys.append(pubkey.strip())
                ssh_config = '''Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
'''
                execute_ssh_command(ssh, f'echo "{ssh_config}" > ~/.ssh/config && chmod 644 ~/.ssh/config')
            except Exception:
                pass
            finally:
                ssh.close()

        # 分发公钥
        if all_pubkeys:
            for host_info in sorted_hosts:
                ip = host_info.get('ip')
                auth = get_host_auth(host_info)
                port = int(host_info.get('port', 22))

                result = next((r for r in all_results if r['ip'] == ip), None)
                if not result:
                    continue

                ssh, error = ssh_connect(ip, port, auth)
                if not ssh:
                    continue

                try:
                    execute_ssh_command(ssh, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
                    execute_ssh_command(ssh, '> ~/.ssh/authorized_keys')
                    for pubkey in all_pubkeys:
                        execute_ssh_command(ssh, f'echo "{pubkey}" >> ~/.ssh/authorized_keys')
                    execute_ssh_command(ssh, 'chmod 600 ~/.ssh/authorized_keys')
                    result['logs'].append(f'[OK] SSH免密登录已配置')
                    result['message'] = '\n'.join(result['logs'])
                except Exception:
                    pass
                finally:
                    ssh.close()

        return JsonResponse({'status': 'success', 'results': all_results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def modify_hostnames(request):
    """修改主机名"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])
        hostname_prefix = data.get('hostname_prefix', 'node')

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        # 按IP排序并生成主机名
        sorted_hosts = sort_hosts_by_ip(hosts)
        for index, host in enumerate(sorted_hosts):
            host['hostname'] = generate_hostname_from_ip(host.get('ip'), index, hostname_prefix)

        # 构建所有主机的 IP 和主机名映射
        host_mappings = []
        for host_info in sorted_hosts:
            ip = host_info.get('ip')
            hostname = host_info.get('hostname')
            if hostname:
                host_mappings.append((ip, hostname))

        # 修改每台主机的主机名和 hosts 文件
        results = []
        for host_info in sorted_hosts:
            ip = host_info.get('ip')
            auth = get_host_auth(host_info)
            port = int(host_info.get('port', 22))
            hostname = host_info.get('hostname')

            result = {
                'ip': ip,
                'hostname': hostname,
                'success': True,
                'message': '',
                'logs': []
            }

            ssh, error = ssh_connect(ip, port, auth)
            if not ssh:
                result['success'] = False
                result['message'] = f'连接失败: {error}'
                results.append(result)
                continue

            try:
                # 修改主机名
                if hostname and hostname != ip:
                    execute_ssh_command(ssh, f'hostname {hostname}')
                    execute_ssh_command(ssh, f'echo "{hostname}" > /etc/hostname')
                    execute_ssh_command(ssh, f'sed -i "s/127.0.0.1.*localhost/127.0.0.1 localhost {hostname}/" /etc/hosts')
                    result['logs'].append(f'[OK] 主机名已修改为: {hostname}')

                # 更新 /etc/hosts 文件，添加所有主机的映射
                for map_ip, map_hostname in host_mappings:
                    # 检查是否已存在该 IP 的条目
                    check_output, _, _ = execute_ssh_command(ssh, f'grep "^{map_ip}[[:space:]]" /etc/hosts || true')

                    if check_output.strip():  # 已存在，更新
                        execute_ssh_command(ssh, f'sed -i "s/^{map_ip}[[:space:]].*/{map_ip} {map_hostname}/" /etc/hosts')
                        result['logs'].append(f'[OK] 已更新 hosts 条目: {map_ip} {map_hostname}')
                    else:  # 不存在，添加
                        execute_ssh_command(ssh, f'echo "{map_ip} {map_hostname}" >> /etc/hosts')
                        result['logs'].append(f'[OK] 已添加 hosts 条目: {map_ip} {map_hostname}')

                result['message'] = '\n'.join(result['logs']) if result['logs'] else '操作已完成'

            except Exception as e:
                result['success'] = False
                result['message'] = f'错误: {str(e)}'
            finally:
                ssh.close()

            results.append(result)

        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def configure_ntp(request):
    """配置NTP同步"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])
        ntp_servers = data.get('ntp_servers', 'ntp.aliyun.com')

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        config = {'configure_ntp': True, 'ntp_servers': ntp_servers}
        results = execute_parallel_init(hosts, config)
        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def configure_ssh(request):
    """配置SSH免密登录"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        # 第一步：生成密钥并收集公钥
        all_pubkeys = []
        results = []

        for host_info in hosts:
            ip = host_info.get('ip')
            auth = get_host_auth(host_info)
            port = int(host_info.get('port', 22))

            result = {
                'ip': ip,
                'hostname': host_info.get('hostname', ip),
                'success': True,
                'message': '',
                'logs': []
            }

            ssh, error = ssh_connect(ip, port, auth)
            if not ssh:
                result['success'] = False
                result['message'] = f'连接失败: {error}'
                results.append(result)
                continue

            try:
                # 生成SSH密钥（如果不存在）
                execute_ssh_command(ssh, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
                execute_ssh_command(ssh, 'test -f ~/.ssh/id_rsa || ssh-keygen -t rsa -N "" -f ~/.ssh/id_rsa -q')

                # 收集公钥
                pubkey, _, _ = execute_ssh_command(ssh, 'cat ~/.ssh/id_rsa.pub')
                if pubkey:
                    all_pubkeys.append(pubkey.strip())
                    result['logs'].append(f'[OK] 已收集公钥')

                # 配置SSH客户端
                ssh_config = '''Host *
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
'''
                execute_ssh_command(ssh, f'echo "{ssh_config}" > ~/.ssh/config && chmod 644 ~/.ssh/config')
                result['logs'].append('[OK] SSH客户端配置完成')

            except Exception as e:
                result['success'] = False
                result['message'] = f'错误: {str(e)}'
            finally:
                ssh.close()

            results.append(result)

        # 第二步：将所有公钥分发到所有主机
        if all_pubkeys:
            for host_info in hosts:
                ip = host_info.get('ip')
                auth = get_host_auth(host_info)
                port = int(host_info.get('port', 22))

                # 找到对应的result
                result = next((r for r in results if r['ip'] == ip), None)
                if not result or not result['success']:
                    continue

                ssh, error = ssh_connect(ip, port, auth)
                if not ssh:
                    continue

                try:
                    # 清空并重建authorized_keys
                    execute_ssh_command(ssh, 'mkdir -p ~/.ssh && chmod 700 ~/.ssh')
                    execute_ssh_command(ssh, '> ~/.ssh/authorized_keys')

                    # 写入所有公钥
                    for pubkey in all_pubkeys:
                        execute_ssh_command(ssh, f'echo "{pubkey}" >> ~/.ssh/authorized_keys')

                    execute_ssh_command(ssh, 'chmod 600 ~/.ssh/authorized_keys')
                    result['logs'].append(f'[OK] 已配置 {len(all_pubkeys)} 个主机的免密登录')
                    result['message'] = '\n'.join(result['logs'])

                except Exception as e:
                    result['success'] = False
                    result['message'] = f'分发公钥失败: {str(e)}'
                finally:
                    ssh.close()

        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def disable_selinux(request):
    """关闭SELinux"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        config = {'disable_selinux': True}
        results = execute_parallel_init(hosts, config)
        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def configure_firewall(request):
    """配置防火墙（包含 SELinux 和防火墙规则）"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])
        management_cidr = data.get('management_cidr', '10.255.0.0/24')
        harden_etcd = data.get('harden_etcd', True)
        harden_postgresql = data.get('harden_postgresql', True)
        harden_elasticsearch = data.get('harden_elasticsearch', True)

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        config = {
            'configure_firewall': True,
            'disable_selinux': True,
            'management_cidr': management_cidr,
            'harden_etcd': harden_etcd,
            'harden_postgresql': harden_postgresql,
            'harden_elasticsearch': harden_elasticsearch,
        }
        results = execute_parallel_init(hosts, config)
        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def security_hardening(request):
    """应用安全加固"""
    try:
        data = json.loads(request.body)
        hosts = data.get('hosts', [])
        management_cidr = data.get('management_cidr', '10.255.0.0/24')
        harden_etcd = data.get('harden_etcd', True)
        harden_postgresql = data.get('harden_postgresql', True)
        harden_elasticsearch = data.get('harden_elasticsearch', True)
        harden_chronyd = data.get('harden_chronyd', False)
        block_ports = data.get('block_ports', '')
        iptables_commands = data.get('iptables_commands', [])

        if not hosts:
            return JsonResponse({'status': 'error', 'error': '请提供主机列表'}, status=400)

        try:
            validate_hosts_auth(hosts)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)

        config = {
            'security_hardening': True,
            'management_cidr': management_cidr,
            'harden_etcd': harden_etcd,
            'harden_postgresql': harden_postgresql,
            'harden_elasticsearch': harden_elasticsearch,
            'harden_chronyd': harden_chronyd,
            'block_ports': block_ports,
            'iptables_commands': iptables_commands,
        }
        results = execute_parallel_init(hosts, config)
        return JsonResponse({'status': 'success', 'results': results})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


def execute_parallel_init(hosts, config):
    """并行执行初始化操作"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []
    threads = min(len(hosts), 50)

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = []
        for host_info in hosts:
            futures.append(executor.submit(process_host_task, host_info, config))

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({
                    'ip': 'unknown',
                    'hostname': 'unknown',
                    'success': False,
                    'message': f'执行错误: {str(e)}',
                    'logs': []
                })

    return results


def process_host_task(host_info, config):
    """处理单个主机初始化任务"""
    ip = host_info.get('ip')
    auth = get_host_auth(host_info)
    port = int(host_info.get('port', 22))

    result_container = []

    ssh, error = ssh_connect(ip, port, auth)

    if not ssh:
        return {
            'ip': ip,
            'hostname': host_info.get('hostname', ip),
            'success': False,
            'message': f'连接失败: {error}',
            'logs': []
        }

    try:
        process_host_init(ssh, host_info, config, result_container, None)
    except Exception as e:
        return {
            'ip': ip,
            'hostname': host_info.get('hostname', ip),
            'success': False,
            'message': f'执行错误: {str(e)}',
            'logs': []
        }
    finally:
        ssh.close()

    # 返回 process_host_init 创建的 result
    return result_container[0] if result_container else {
        'ip': ip,
        'hostname': host_info.get('hostname', ip),
        'success': False,
        'message': '未知错误',
        'logs': []
    }
