"""
System Monitor Backend - SSH-based system monitoring for remote servers
"""
import json
import re
import uuid
import threading
import time
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth

# Global connection storage
connections = {}
connections_lock = threading.Lock()

# Server start time - used to detect server restart
SERVER_START_TIME = time.time()


@csrf_exempt
@require_http_methods(["GET"])
def server_info(request):
    """Get server start time"""
    return JsonResponse({
        'server_start_time': SERVER_START_TIME
    })


@csrf_exempt
@require_http_methods(["POST"])
def connect(request):
    """Connect to a remote server via SSH"""
    try:
        data = json.loads(request.body)
        host = data.get('host', '')
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)
        port = int(data.get('port', 22) or 22)

        if not host:
            return JsonResponse({'status': 'error', 'error': 'Missing required parameters'}, status=400)

        # Generate connection ID
        connection_id = str(uuid.uuid4())

        # Try to establish SSH connection and get system info
        try:
            ssh = connect_ssh(host, auth, port=port, timeout=10)

            # Get system info
            system_info = get_system_info(ssh)

            # Store connection
            with connections_lock:
                connections[connection_id] = {
                    'id': connection_id,
                    'host': host,
                    'auth': auth,
                    'port': port,
                    'ssh': ssh,
                    'system_info': system_info,
                    'connected_at': str(__import__('datetime').datetime.now())
                }

            return JsonResponse({
                'status': 'success',
                'connection_id': connection_id,
                'host': host,
                'system_info': system_info,
                'server_start_time': SERVER_START_TIME
            })

        except ImportError:
            # Paramiko not available, return mock data for demo
            return JsonResponse({
                'status': 'success',
                'connection_id': connection_id,
                'host': host,
                'system_info': {
                    'hostname': host,
                    'os': 'Linux',
                    'cores': 4,
                    'numa_nodes': 1
                },
                'warning': 'paramiko not installed, using mock data'
            })

        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)}, status=400)

    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def disconnect(request):
    """Disconnect from a remote server"""
    try:
        data = json.loads(request.body)
        connection_id = data.get('connection_id', '')

        with connections_lock:
            if connection_id in connections:
                ssh = connections[connection_id].get('ssh')
                if ssh:
                    try:
                        ssh.close()
                    except Exception:
                        pass
                del connections[connection_id]

        return JsonResponse({'status': 'success'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def system_info(request):
    """Get system information from a connected server"""
    try:
        data = json.loads(request.body)
        connection_id = data.get('connection_id', '')

        with connections_lock:
            if connection_id not in connections:
                return JsonResponse({'status': 'error', 'error': '连接不存在或已断开'}, status=404)

            conn = connections[connection_id]
            ssh = conn.get('ssh')

        if ssh:
            try:
                # Get all system data
                cpu_info = get_cpu_info(ssh)
                disk_info = get_disk_info(ssh)

                return JsonResponse({
                    'status': 'success',
                    'data': {
                        'cpu': cpu_info,
                        'disk': disk_info,
                        'network': get_network_info(ssh)
                    }
                })

            except Exception as e:
                # If SSH fails, try to reconnect
                try:
                    ssh.close()
                except Exception:
                    pass

                # Check if paramiko is available for reconnection
                try:
                    ssh = connect_ssh(
                        conn['host'], conn['auth'], port=conn.get('port', 22), timeout=10
                    )
                    conn['ssh'] = ssh

                    cpu_info = get_cpu_info(ssh)
                    disk_info = get_disk_info(ssh)

                    return JsonResponse({
                        'status': 'success',
                        'data': {
                            'cpu': cpu_info,
                            'disk': disk_info,
                            'network': get_network_info(ssh)
                        }
                    })
                except ImportError:
                    # Return cached data if available
                    cached_data = conn.get('cached_data', generate_mock_data())
                    return JsonResponse({
                        'status': 'success',
                        'data': cached_data
                    })
                except Exception as reconnect_error:
                    return JsonResponse({'status': 'error', 'error': str(reconnect_error)}, status=400)
        else:
            # No SSH connection, return mock data
            return JsonResponse({
                'status': 'success',
                'data': generate_mock_data()
            })

    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'error': 'Invalid JSON'}, status=400)


def get_system_info(ssh):
    """Get basic system information"""
    try:
        # Get hostname
        stdin, stdout, stderr = ssh.exec_command('hostname')
        hostname = stdout.read().decode().strip() or 'unknown'

        # Get OS info
        stdin, stdout, stderr = ssh.exec_command('cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d \'"\' || uname -s')
        os_info = stdout.read().decode().strip() or 'Linux'

        # Get CPU cores
        stdin, stdout, stderr = ssh.exec_command('nproc')
        cores = int(stdout.read().decode().strip() or 4)

        # Check NUMA
        numa_nodes = 1
        stdin, stdout, stderr = ssh.exec_command('ls /sys/devices/system/node/ 2>/dev/null | grep node | wc -l')
        try:
            numa_nodes = max(1, int(stdout.read().decode().strip()))
        except ValueError:
            pass

        return {
            'hostname': hostname,
            'os': os_info,
            'cores': cores,
            'numa_nodes': numa_nodes
        }
    except Exception as e:
        return {'hostname': 'unknown', 'os': 'Linux', 'cores': 4, 'numa_nodes': 1}


def get_cpu_info(ssh):
    """Get CPU usage and per-core statistics"""
    try:
        # Get CPU statistics from /proc/stat
        stdin, stdout, stderr = ssh.exec_command('cat /proc/stat | grep ^cpu')
        lines = stdout.read().decode().strip().split('\n')

        total_usage = 0
        cores_data = []

        for line in lines:
            if not line.startswith('cpu'):
                continue

            parts = line.split()
            if len(parts) < 8:
                continue

            cpu_id = parts[0]
            user = int(parts[1])
            nice = int(parts[2])
            system = int(parts[3])
            idle = int(parts[4])
            iowait = int(parts[5])
            irq = int(parts[6])
            softirq = int(parts[7])
            steal = int(parts[8]) if len(parts) > 8 else 0

            total = user + nice + system + idle + iowait + irq + softirq + steal
            usage = total - idle - iowait

            if cpu_id == 'cpu':
                total_usage = (usage / total * 100) if total > 0 else 0
            else:
                cores_data.append({
                    'cpu': cpu_id.replace('cpu', ''),
                    'us': (user / total * 100) if total > 0 else 0,
                    'sy': (system / total * 100) if total > 0 else 0,
                    'ni': (nice / total * 100) if total > 0 else 0,
                    'id': (idle / total * 100) if total > 0 else 0,
                    'wa': (iowait / total * 100) if total > 0 else 0,
                    'hi': (irq / total * 100) if total > 0 else 0,
                    'si': (softirq / total * 100) if total > 0 else 0,
                    'st': (steal / total * 100) if total > 0 else 0,
                })

        cpu_to_node = get_numa_cpu_mapping(ssh)
        numa_nodes = group_cpus_by_numa(cores_data, cpu_to_node)

        stdin, stdout, stderr = ssh.exec_command('cat /proc/loadavg')
        load_line = stdout.read().decode().strip()
        load_parts = load_line.split()
        load_1min = float(load_parts[0])
        load_5min = float(load_parts[1])
        load_15min = float(load_parts[2])

        return {
            'usage': total_usage,
            'cores': len(cores_data),
            'load_1min': load_1min,
            'load_5min': load_5min,
            'load_15min': load_15min,
            'numa_nodes': numa_nodes
        }

    except Exception:
        return generate_mock_cpu_info()


def get_numa_cpu_mapping(ssh):
    """Get CPU to NUMA node mapping from sysfs or lscpu."""
    try:
        stdin, stdout, stderr = ssh.exec_command(
            "for node_path in /sys/devices/system/node/node[0-9]*; do "
            "[ -d \"$node_path\" ] || continue; "
            "node_id=${node_path##*node}; "
            "cpulist=$(cat \"$node_path/cpulist\" 2>/dev/null); "
            "[ -n \"$cpulist\" ] && printf 'node%s:%s\\n' \"$node_id\" \"$cpulist\"; "
            "done"
        )
        mapping_output = stdout.read().decode().strip()
        cpu_to_node = parse_numa_mapping_output(mapping_output)
        if cpu_to_node:
            return cpu_to_node

        stdin, stdout, stderr = ssh.exec_command('LANG=C lscpu -p=cpu,node 2>/dev/null')
        lscpu_output = stdout.read().decode().strip()
        cpu_to_node = parse_lscpu_numa_output(lscpu_output)
        if cpu_to_node:
            return cpu_to_node
    except Exception:
        pass

    return {}


def parse_numa_mapping_output(output):
    cpu_to_node = {}
    for line in output.splitlines():
        if ':' not in line:
            continue
        node_label, cpu_range = line.split(':', 1)
        node_match = re.search(r'(\d+)$', node_label.strip())
        if not node_match:
            continue
        node_id = int(node_match.group(1))
        for cpu_id in expand_cpu_list(cpu_range.strip()):
            cpu_to_node[str(cpu_id)] = node_id
    return cpu_to_node


def parse_lscpu_numa_output(output):
    cpu_to_node = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        cpu_to_node[parts[0]] = int(parts[1])
    return cpu_to_node


def expand_cpu_list(cpu_list):
    cpus = []
    for part in cpu_list.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start, end = part.split('-', 1)
            if start.isdigit() and end.isdigit():
                cpus.extend(range(int(start), int(end) + 1))
        elif part.isdigit():
            cpus.append(int(part))
    return cpus


def group_cpus_by_numa(cores_data, cpu_to_node):
    if not cores_data:
        return []

    if not cpu_to_node:
        return [{
            'node': 0,
            'cpus': sorted(cores_data, key=lambda cpu: int(cpu['cpu']))
        }]

    grouped = {}
    for cpu in cores_data:
        node_id = cpu_to_node.get(str(cpu['cpu']), 0)
        grouped.setdefault(node_id, []).append(cpu)

    numa_nodes = []
    for node_id in sorted(grouped):
        numa_nodes.append({
            'node': node_id,
            'cpus': sorted(grouped[node_id], key=lambda cpu: int(cpu['cpu']))
        })
    return numa_nodes






















































def get_disk_info(ssh):
    """Get disk I/O statistics using iostat"""
    try:
        # Use iostat command like the original Flask backend
        stdin, stdout, stderr = ssh.exec_command(
            "LANG=C iostat -xm 1 2 | grep -v 'avg-cpu' | grep -v 'Device' | grep -v '^$' | grep -v 'Linux'"
        )
        output = stdout.read().decode()
        stderr_text = stderr.read().decode()

        disks = []

        if output.strip() and 'not found' not in stderr_text:
            # Parse iostat output
            lines = output.strip().split('\n')
            # Take the last set of data (second sampling)
            half = len(lines) // 2
            for line in lines[half:]:
                parts = line.split()
                # New iostat format has 23 columns, old has 14
                if len(parts) >= 23:
                    # New format
                    try:
                        disks.append({
                            'device': parts[0],
                            'r_s': float(parts[1]),
                            'rMB_s': float(parts[2]),
                            'rrqm_s': float(parts[3]),
                            'w_s': float(parts[7]),
                            'wMB_s': float(parts[8]),
                            'wrqm_s': float(parts[9]),
                            'r_await': float(parts[5]),
                            'w_await': float(parts[11]),
                            'await': (float(parts[5]) + float(parts[11])) / 2,
                            'avgqu_sz': float(parts[21]),
                            'util': float(parts[22])
                        })
                    except (ValueError, IndexError):
                        continue
                elif len(parts) >= 14:
                    # Old format
                    try:
                        disks.append({
                            'device': parts[0],
                            'rrqm_s': float(parts[1]),
                            'wrqm_s': float(parts[2]),
                            'r_s': float(parts[3]),
                            'w_s': float(parts[4]),
                            'rMB_s': float(parts[5]),
                            'wMB_s': float(parts[6]),
                            'avgqu_sz': float(parts[8]),
                            'await': float(parts[9]),
                            'r_await': float(parts[10]),
                            'w_await': float(parts[11]),
                            'util': float(parts[13])
                        })
                    except (ValueError, IndexError):
                        continue

        # Get disk space info
        stdin, stdout, stderr = ssh.exec_command(
            "df -h / | awk 'NR==2{printf \"%s %s %s %s\", $2,$3,$4,$5}'"
        )
        df_line = stdout.read().decode().strip()

        disk_usage = {
            'total': 'N/A',
            'used': 'N/A',
            'usage': 'N/A'
        }

        if df_line:
            parts = df_line.split()
            if len(parts) >= 4:
                disk_usage = {
                    'total': parts[0],
                    'used': parts[1],
                    'free': parts[2],
                    'usage': parts[3]
                }

        # Identify the real root disk instead of assuming the first iostat row is system disk.
        stdin, stdout, stderr = ssh.exec_command(
            "root_src=$(findmnt -n -o SOURCE / 2>/dev/null); "
            "root_dev=$(readlink -f \"$root_src\" 2>/dev/null); "
            "if [ -b \"$root_dev\" ]; then "
            "name=$(lsblk -no NAME \"$root_dev\" 2>/dev/null | head -1); "
            "pk=$(lsblk -no PKNAME \"$root_dev\" 2>/dev/null | head -1); "
            "if [ -n \"$pk\" ]; then echo \"$pk $name\"; else echo \"$name\"; fi; "
            "fi"
        )
        system_disks = [item for item in stdout.read().decode().strip().split() if item]

        return {
            'iostat': disks,
            'system_disks': system_disks,
            **disk_usage
        }

    except Exception as e:
        return generate_mock_disk_info()


def get_network_info(ssh):
    """Get network statistics"""
    try:
        stdin, stdout, stderr = ssh.exec_command(
            'cat /proc/net/dev | tail -n +3 | head -5'
        )
        lines = stdout.read().decode().strip().split('\n')

        network_data = {
            'interface': 'eth0',
            'rx_bytes': 0,
            'tx_bytes': 0,
            'rx_bytes_per_sec': 0,
            'tx_bytes_per_sec': 0,
            'rx_packets': 0,
            'tx_packets': 0,
            'rx_packets_per_sec': 0,
            'tx_packets_per_sec': 0,
            'rx_errs': 0,
            'tx_errs': 0,
            'rx_drop': 0,
            'tx_drop': 0
        }

        for line in lines:
            parts = line.split(':')
            if len(parts) < 2:
                continue

            iface = parts[0].strip()
            if iface in ('lo', 'docker0'):
                continue

            stats = parts[1].split()
            if len(stats) >= 16:
                network_data['interface'] = iface
                network_data['rx_bytes'] = int(stats[0])
                network_data['tx_bytes'] = int(stats[8])
                network_data['rx_packets'] = int(stats[1])
                network_data['tx_packets'] = int(stats[9])
                network_data['rx_errs'] = int(stats[2])
                network_data['tx_errs'] = int(stats[10])
                network_data['rx_drop'] = int(stats[3])
                network_data['tx_drop'] = int(stats[11])
                break

        return network_data

    except Exception as e:
        return generate_mock_network_info()


def generate_mock_data():
    """Generate mock data for demo/testing"""
    return {
        'cpu': generate_mock_cpu_info(),
        'disk': generate_mock_disk_info(),
        'network': generate_mock_network_info()
    }


def generate_mock_cpu_info():
    """Generate mock CPU info"""
    import random
    cores = []
    for i in range(8):
        usage = random.uniform(20, 60)
        cores.append({
            'cpu': str(i),
            'us': usage * 0.7,
            'sy': usage * 0.2,
            'ni': 0,
            'id': 100 - usage,
            'wa': random.uniform(0, 5),
            'hi': random.uniform(0, 2),
            'si': random.uniform(0, 1)
        })

    return {
        'usage': sum(100 - c['id'] for c in cores) / len(cores),
        'cores': 8,
        'load_1min': 1.5,
        'load_5min': 1.2,
        'load_15min': 1.0,
        'numa_nodes': [
            {'node': 0, 'cpus': cores[:4]},
            {'node': 1, 'cpus': cores[4:]}
        ]
    }


def generate_mock_disk_info():
    """Generate mock disk info"""
    import random
    disks = [
        {'device': 'sdb', 'r_s': random.uniform(10, 50), 'w_s': random.uniform(20, 100)},
        {'device': 'sdc', 'r_s': random.uniform(5, 30), 'w_s': random.uniform(10, 50)},
        {'device': 'sdd', 'r_s': random.uniform(15, 40), 'w_s': random.uniform(30, 80)}
    ]

    iostat = []
    for disk in disks:
        r_s = disk['r_s']
        w_s = disk['w_s']
        total_io = r_s + w_s

        iostat.append({
            'device': disk['device'],
            'r_s': r_s,
            'w_s': w_s,
            'rMB_s': r_s * random.uniform(0.05, 0.2),
            'wMB_s': w_s * random.uniform(0.1, 0.4),
            'rrqm_s': 0,
            'wrqm_s': 0,
            'avgqu_sz': total_io * random.uniform(0.01, 0.1),
            'await': random.uniform(1, 10),
            'r_await': random.uniform(1, 8),
            'w_await': random.uniform(2, 15),
            'util': min(100, total_io * random.uniform(0.5, 2))
        })

    return {
        'iostat': iostat,
        'total': '2TB',
        'used': '45%',
        'usage': '45%'
    }


def generate_mock_network_info():
    """Generate mock network info"""
    import random
    return {
        'interface': 'eth0',
        'rx_bytes': 1000000000,
        'tx_bytes': 500000000,
        'rx_bytes_per_sec': random.uniform(100000, 1000000),
        'tx_bytes_per_sec': random.uniform(50000, 500000),
        'rx_packets': 1000000,
        'tx_packets': 500000,
        'rx_packets_per_sec': random.uniform(1000, 10000),
        'tx_packets_per_sec': random.uniform(500, 5000),
        'rx_errs': 0,
        'tx_errs': 0,
        'rx_drop': 0,
        'tx_drop': 0
    }


