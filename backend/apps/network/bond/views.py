"""
Network Bond Configuration Backend - Configure network bonding/aggregation
VERSION: 2.0 - Updated IP splitting logic
"""

import json
import re
import time
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth


# SSH connection helper
def ssh_connect(host, auth, port=22, timeout=10):
    """Establish SSH connection"""
    try:
        return connect_ssh(host, auth, port=port, timeout=timeout), None
    except Exception as exc:
        return None, str(exc)


def execute_ssh_command(client, command):
    """Execute SSH command and return stdout, stderr, returncode"""
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=60)
        stdout_str = stdout.read().decode("utf-8", errors="ignore")
        stderr_str = stderr.read().decode("utf-8", errors="ignore")
        return stdout_str, stderr_str, stdout.channel.recv_exit_status()
    except Exception as e:
        return "", str(e), 1


def get_network_interfaces(client):
    """
    Get network interface list and configuration info
    Returns: {
        'interfaces': [{'name': 'eth0', 'ip': '192.168.1.100/24', 'status': 'UP', 'type': 'physical', 'mac': '...', 'speed': '1000Mb/s'}],
        'bonds': [{'name': 'bond0', 'mode': '1', 'slaves': ['eth0', 'eth1'], 'ip': '192.168.1.10'}]
    }
    """
    interfaces = []
    bonds = []
    gateway = None
    dns_servers = []

    try:
        # First get all Bond interface names from bonding_masters
        bond_interface_names = []
        stdout, stderr, code = execute_ssh_command(
            client, "cat /sys/class/net/bonding_masters 2>/dev/null || echo ''"
        )
        if code == 0 and stdout.strip():
            bond_interface_names = stdout.strip().split()

        # Get all network interfaces
        stdout, stderr, code = execute_ssh_command(client, "ip -o link show")
        if code == 0:
            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                # Parse: 2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc ... link/ether aa:bb:cc:dd:ee:ff
                match = re.match(r"\d+:\s+(\S+):\s+<([^>]+)>", line)
                if match:
                    name = match.group(1).split("@")[0]
                    flags = match.group(2)

                    # Skip lo interface
                    if name == "lo":
                        continue

                    status = "UP" if "UP" in flags else "DOWN"

                    # Determine interface type
                    if name in bond_interface_names:
                        iface_type = "bond"
                    elif name.startswith(("eth", "ens", "enp", "eno", "em")):
                        iface_type = "physical"
                    elif name.startswith(("vlan", "br", "docker", "virbr", "veth")):
                        iface_type = "virtual"
                    else:
                        iface_type = "other"

                    # Extract MAC address
                    mac_match = re.search(r"link/ether\s+([0-9a-f:]+)", line)
                    mac = mac_match.group(1) if mac_match else None

                    interfaces.append(
                        {
                            "name": name,
                            "status": status,
                            "type": iface_type,
                            "ip": None,
                            "mac": mac,
                            "speed": None,
                            "gateway": None,
                        }
                    )

        # Get IP addresses
        stdout, stderr, code = execute_ssh_command(client, "ip -o -4 addr show")
        if code == 0:
            for line in stdout.strip().split("\n"):
                if not line:
                    continue
                match = re.search(r"\d+:\s+(\S+)\s+inet\s+(\S+)", line)
                if match:
                    name = match.group(1)
                    ip_cidr = match.group(2)
                    for iface in interfaces:
                        if iface["name"] == name:
                            iface["ip"] = ip_cidr
                            break

        # Get interface speeds
        for iface in interfaces:
            if iface["type"] in ["physical", "bond"]:
                stdout, stderr, code = execute_ssh_command(
                    client,
                    f"ethtool {iface['name']} 2>/dev/null | grep Speed || echo ''",
                )
                if code == 0 and stdout.strip():
                    speed_match = re.search(r"Speed:\s+(\S+)", stdout)
                    if speed_match:
                        iface["speed"] = speed_match.group(1)

        # Get default gateway
        stdout, stderr, code = execute_ssh_command(client, "ip route show default")
        if code == 0 and stdout.strip():
            match = re.search(r"default via\s+(\S+)\s+dev\s+(\S+)", stdout)
            if match:
                gateway = match.group(1)
                gateway_dev = match.group(2)
                for iface in interfaces:
                    if iface["name"] == gateway_dev:
                        iface["gateway"] = gateway

        # Get DNS servers
        stdout, stderr, code = execute_ssh_command(
            client,
            "cat /etc/resolv.conf 2>/dev/null | grep nameserver | awk '{print $2}'",
        )
        if code == 0 and stdout.strip():
            dns_servers = stdout.strip().split("\n")

        # Add gateway/dns to all interfaces
        for iface in interfaces:
            if iface["gateway"] is None:
                iface["gateway"] = gateway
            if iface["type"] == "bond":
                iface["dns"] = ", ".join(dns_servers) if dns_servers else None

        # Get Bond details
        for bond_name in bond_interface_names:
            bond_info = {"name": bond_name, "mode": None, "slaves": [], "ip": None}

            # Get bond mode
            stdout, stderr, code = execute_ssh_command(
                client,
                f"cat /sys/class/net/{bond_name}/bonding/mode 2>/dev/null || echo ''",
            )
            if code == 0 and stdout.strip():
                bond_info["mode"] = stdout.strip()

            # Get slaves
            stdout, stderr, code = execute_ssh_command(
                client,
                f"cat /sys/class/net/{bond_name}/bonding/slaves 2>/dev/null || echo ''",
            )
            if code == 0 and stdout.strip():
                bond_info["slaves"] = stdout.strip().split()

            # Get bond IP
            for iface in interfaces:
                if iface["name"] == bond_name:
                    bond_info["ip"] = iface["ip"]
                    break

            bonds.append(bond_info)

        return {"interfaces": interfaces, "bonds": bonds}

    except Exception as e:
        print(f"Error getting network interfaces: {e}")
        return {"interfaces": interfaces, "bonds": bonds}


def assign_ip_from_range(start_ip, end_ip, index):
    """Assign IP from range based on index"""

    def ip_to_int(ip):
        parts = ip.split(".")
        return (
            (int(parts[0]) << 24)
            + (int(parts[1]) << 16)
            + (int(parts[2]) << 8)
            + int(parts[3])
        )

    def int_to_ip(num):
        return f"{(num >> 24) & 0xFF}.{(num >> 16) & 0xFF}.{(num >> 8) & 0xFF}.{num & 0xFF}"

    start_int = ip_to_int(start_ip)
    assigned_int = start_int + index
    return int_to_ip(assigned_int)


def check_network_service(client):
    """
    Check NetworkManager and network service status
    Returns: {
        'has_networkmanager': bool,
        'networkmanager_active': bool,
        'has_network': bool,
        'network_active': bool,
        'recommendation': str,
        'action': str  # 'proceed', 'warning', 'error'
    }
    """
    result = {
        "has_networkmanager": False,
        "networkmanager_active": False,
        "has_network": False,
        "network_active": False,
        "recommendation": "",
        "action": "proceed",
    }

    try:
        stdout, stderr, code = execute_ssh_command(
            client, "systemctl | grep -E 'network\\.service|NetworkManager\\.service'"
        )

        lines = stdout.strip().split("\n")
        for line in lines:
            if "NetworkManager.service" in line:
                result["has_networkmanager"] = True
                if "loaded" in line and "active" in line and "running" in line:
                    result["networkmanager_active"] = True
            elif "network.service" in line:
                result["has_network"] = True
                if "loaded" in line and "active" in line:
                    result["network_active"] = True

        if result["has_networkmanager"] and result["has_network"]:
            if result["networkmanager_active"]:
                result["recommendation"] = (
                    "系统同时存在NetworkManager和network服务，将自动卸载NetworkManager后使用network服务进行配置"
                )
                result["action"] = "warning"
            else:
                result["recommendation"] = (
                    "系统同时存在NetworkManager和network服务，将使用network服务进行配置"
                )
                result["action"] = "proceed"
        elif result["has_network"]:
            result["recommendation"] = "系统使用network服务，将使用network服务进行配置"
            result["action"] = "proceed"
        elif result["has_networkmanager"]:
            result["recommendation"] = (
                "警告：系统仅有NetworkManager服务，建议安装network-scripts包。继续配置可能导致网络异常！"
            )
            result["action"] = "warning"
        else:
            result["recommendation"] = (
                "错误：系统未检测到NetworkManager或network服务，无法进行配置！"
            )
            result["action"] = "error"

        return result

    except Exception as e:
        result["recommendation"] = f"检查网络服务失败: {str(e)}"
        result["action"] = "error"
        return result


def create_bond_interface(client, bond_config, logs=None):
    """Create a bond interface with slave interfaces"""
    if logs is None:
        logs = []

    def add_log(message, level="info"):
        logs.append(
            {
                "timestamp": int(time.time()),
                "type": "step",
                "level": level,
                "message": message,
            }
        )

    bond_name = bond_config["name"]
    mode = bond_config.get("mode", "1")
    nics = bond_config.get("nics", [])
    ip = bond_config.get("ip", "")
    gateway = bond_config.get("gateway", "")
    dns = bond_config.get("dns", "")

    try:
        # Check if NICs exist
        add_log(f"检查网卡 {', '.join(nics)} 是否存在...", "info")
        for nic in nics:
            stdout, stderr, code = execute_ssh_command(
                client, f"ip link show {nic} 2>/dev/null || echo 'not found'"
            )
            if "not found" in stdout:
                return False, f"网卡 {nic} 不存在", logs

        # Step 1: Backup existing configs with simplified format (only for interfaces with IP)
        add_log("备份现有网卡配置...", "info")
        timestamp = int(time.time())
        for nic in nics:
            # Check if interface has an IP address
            stdout, stderr, code = execute_ssh_command(
                client, f"ip -4 addr show {nic} | grep -oP 'inet \K[\d.]+' || echo ''"
            )
            has_ip = stdout.strip() != ""

            if has_ip:
                # Create simplified backup config only if interface has IP
                backup_config = f"""DEVICE={nic}
TYPE=Ethernet
ONBOOT=no
"""
                execute_ssh_command(
                    client,
                    f"cat > /etc/sysconfig/network-scripts/ifcfg-{nic}.bak.{timestamp} << 'EOF'\n{backup_config}EOF",
                )
                add_log(f"已备份 {nic} 配置", "info")
            else:
                # Delete any existing backups for interfaces without IP
                execute_ssh_command(
                    client,
                    f"rm -f /etc/sysconfig/network-scripts/ifcfg-{nic}.bak.* 2>/dev/null || true",
                )
                add_log(f"{nic} 无IP地址，跳过备份并删除旧备份", "info")

        # Backup bond config if exists (keep original for bond)
        stdout, stderr, code = execute_ssh_command(
            client,
            f"if [ -f /etc/sysconfig/network-scripts/ifcfg-{bond_name} ]; then cp /etc/sysconfig/network-scripts/ifcfg-{bond_name} /etc/sysconfig/network-scripts/ifcfg-{bond_name}.bak.{timestamp}; fi",
        )

        # Step 2: Unconfigure existing bond if exists
        stdout, stderr, code = execute_ssh_command(
            client, f"ip link show {bond_name} 2>/dev/null || echo 'not found'"
        )
        if "not found" not in stdout:
            add_log(f"发现已存在的Bond接口 {bond_name}，正在删除...", "warning")
            execute_ssh_command(
                client, f"ip link set {bond_name} down 2>/dev/null || true"
            )
            execute_ssh_command(client, f"ifdown {bond_name} 2>/dev/null || true")
            execute_ssh_command(
                client, f"ip link delete {bond_name} 2>/dev/null || true"
            )
            execute_ssh_command(
                client, f"rm -f /etc/sysconfig/network-scripts/ifcfg-{bond_name}"
            )

        # Step 3: Release NICs from existing bonds
        add_log("释放网卡原有的Bond配置...", "info")
        for nic in nics:
            stdout, stderr, code = execute_ssh_command(
                client,
                f"ip link show {nic} 2>/dev/null | grep -i 'master' || echo 'no master'",
            )
            if "no master" not in stdout:
                execute_ssh_command(
                    client, f"ip link set {nic} nomaster 2>/dev/null || true"
                )

        # Step 4: Create bond configuration file
        add_log(
            f"创建Bond配置文件 /etc/sysconfig/network-scripts/ifcfg-{bond_name}...",
            "info",
        )
        bonding_opts = f"miimon=100 mode={mode}"
        if mode == "4":
            bonding_opts += " xmit_hash_policy=layer3+4"

        bond_config_content = f"""NAME=\"{bond_name}\"
DEVICE=\"{bond_name}\"
TYPE=\"Bond\"
BOOTPROTO=\"static\"
BONDING_MASTER=\"yes\"
BONDING_OPTS=\"{bonding_opts}\"
ONBOOT=\"yes\"
PEERDNS=\"no\"
IPV6INIT=\"no\"
NM_CONTROLLED=\"no\"
IPADDR={ip.split("/")[0] if ip else ""}
PREFIX={ip.split("/")[1] if ip and "/" in ip else "24"}
"""

        if gateway:
            bond_config_content += f"GATEWAY={gateway}\n"

        if dns:
            bond_config_content += f"DNS1={dns.split(',')[0].strip()}\n"

        execute_ssh_command(
            client,
            f"cat > /etc/sysconfig/network-scripts/ifcfg-{bond_name} <<'EOF'\n{bond_config_content}EOF",
        )
        add_log(f"Bond配置文件已创建", "info")

        # Step 5: Configure slave NICs
        add_log("配置网卡为Bond从接口...", "info")
        for nic in nics:
            # Get MAC address from system
            stdout, stderr, code = execute_ssh_command(
                client,
                f"ip link show {nic} 2>/dev/null | grep -o 'link/ether [0-9a-f:]*' | awk '{{print $2}}' || echo ''",
            )
            mac_addr = stdout.strip()

            nic_config_content = f"""NAME=\"{nic}\"
DEVICE=\"{nic}\"
TYPE=\"Ethernet\"
ONBOOT=\"yes\"
MASTER=\"{bond_name}\"
SLAVE=\"yes\"
NM_CONTROLLED=\"no\"
"""

            if mac_addr:
                nic_config_content += f"HWADDR={mac_addr}\n"

            # Create the config file
            cmd = f"cat > /etc/sysconfig/network-scripts/ifcfg-{nic} << 'EOF'\n{nic_config_content}EOF"
            stdout, stderr, code = execute_ssh_command(client, cmd)
            if code != 0:
                add_log(f"警告: 创建 {nic} 配置文件失败: {stderr}", "warning")
            else:
                add_log(f"网卡 {nic} 配置文件已创建", "info")

            # Verify the file was created
            stdout, stderr, code = execute_ssh_command(
                client,
                f"test -f /etc/sysconfig/network-scripts/ifcfg-{nic} && echo 'exists' || echo 'not found'",
            )
            if "not found" in stdout:
                add_log(f"错误: {nic} 配置文件创建失败", "error")
            else:
                add_log(f"验证: {nic} 配置文件存在", "info")

        # Step 6: Load bonding module
        add_log("加载bonding内核模块...", "info")
        execute_ssh_command(client, "modprobe bonding 2>/dev/null || true")

        # Step 7: Create bond interface
        add_log(f"创建Bond接口 {bond_name}...", "info")
        execute_ssh_command(
            client, f"ip link add {bond_name} type bond 2>/dev/null || true"
        )
        execute_ssh_command(client, f"ip link set {bond_name} down 2>/dev/null || true")

        # Set bond mode and hash policy
        execute_ssh_command(
            client,
            f"echo {mode} > /sys/class/net/{bond_name}/bonding/mode 2>/dev/null || true",
        )
        if mode == "4":
            execute_ssh_command(
                client,
                f"echo layer3+4 > /sys/class/net/{bond_name}/bonding/xmit_hash_policy 2>/dev/null || true",
            )

        # Add slaves to bond
        add_log("将网卡添加到Bond...", "info")
        for nic in nics:
            execute_ssh_command(client, f"ip link set {nic} down 2>/dev/null || true")
            execute_ssh_command(
                client, f"ip link set {nic} master {bond_name} 2>/dev/null || true"
            )

        # Step 8: Bring up interfaces
        add_log("启动网络接口...", "info")
        for nic in nics:
            execute_ssh_command(
                client,
                f"ifup ifcfg-{nic} 2>/dev/null || ip link set {nic} up 2>/dev/null || true",
            )

        execute_ssh_command(
            client,
            f"ifup ifcfg-{bond_name} 2>/dev/null || (ip link set {bond_name} up && ip addr add {ip} dev {bond_name} 2>/dev/null) || true",
        )

        # Add gateway if specified
        if gateway:
            add_log(f"配置网关 {gateway}...", "info")
            execute_ssh_command(
                client,
                f"ip route add default via {gateway} dev {bond_name} 2>/dev/null || true",
            )

        # Show result
        add_log(f"Bond {bond_name} 配置完成！", "success")
        add_log(f"模式: Mode {mode}", "info")
        add_log(f"从接口: {', '.join(nics)}", "info")
        if ip:
            add_log(f"IP地址: {ip}", "info")

        return True, f"Bond {bond_name} 配置成功", logs

    except Exception as e:
        add_log(f"配置失败: {str(e)}", "error")
        return False, f"配置失败: {str(e)}", logs


def clear_all_bonds(client):
    """Clear all bond configurations"""
    logs = []

    def add_log(message, level="info"):
        logs.append(
            {
                "timestamp": int(time.time()),
                "type": "step",
                "level": level,
                "message": message,
            }
        )

    try:
        # Get all bond interfaces
        stdout, stderr, code = execute_ssh_command(
            client, "cat /sys/class/net/bonding_masters 2>/dev/null || echo ''"
        )
        if code != 0 or not stdout.strip():
            return True, "没有发现Bond配置", [], logs

        bond_names = stdout.strip().split()
        details = []

        for bond_name in bond_names:
            add_log(f"处理Bond: {bond_name}", "info")

            # Get slaves
            slaves = []
            stdout, stderr, code = execute_ssh_command(
                client,
                f"cat /sys/class/net/{bond_name}/bonding/slaves 2>/dev/null || echo ''",
            )
            if code == 0 and stdout.strip():
                slaves = stdout.strip().split()

            # Delete bond interface
            add_log(f"关闭并删除Bond接口 {bond_name}...", "info")
            execute_ssh_command(
                client, f"ip link set {bond_name} down 2>/dev/null || true"
            )
            execute_ssh_command(client, f"ifdown {bond_name} 2>/dev/null || true")
            execute_ssh_command(
                client, f"ip link delete {bond_name} 2>/dev/null || true"
            )

            # Delete bond config file
            execute_ssh_command(
                client, f"rm -f /etc/sysconfig/network-scripts/ifcfg-{bond_name}"
            )

            # Delete slave interface config files
            for slave in slaves:
                add_log(f"删除网卡 {slave} 配置文件...", "info")
                execute_ssh_command(
                    client, f"rm -f /etc/sysconfig/network-scripts/ifcfg-{slave}"
                )
                details.append(f"已删除网卡 {slave} 配置文件")

            add_log(f"Bond {bond_name} 已清除", "success")

        return True, f"已清除 {len(bond_names)} 个Bond配置", details, logs

    except Exception as e:
        add_log(f"清除失败: {str(e)}", "error")
        return False, f"清除失败: {str(e)}", [], logs


# Global storage for async operations
async_operations = {}
async_operations_lock = threading.Lock()


@csrf_exempt
@require_http_methods(["POST"])
def get_nics(request):
    """Get network interface information"""
    try:
        data = json.loads(request.body)
        host = data.get("host")
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        port = int(data.get("port") or 22)

        if not host:
            return JsonResponse(
                {"status": "error", "error": "缺少必要参数"}, status=400
            )

        # SSH connection
        client, error = ssh_connect(host, auth, port=port)
        if error:
            return JsonResponse({"status": "error", "error": error}, status=401)

        try:
            # Get network interface info
            network_info = get_network_interfaces(client)

            # Add slaves information to bond interfaces
            for bond in network_info.get("bonds", []):
                for iface in network_info["interfaces"]:
                    if iface["name"] == bond["name"]:
                        iface["slaves"] = bond.get("slaves", [])
                        break

            # Extract physical NIC names
            nics = [
                iface["name"]
                for iface in network_info["interfaces"]
                if iface["type"] in ["physical", "bond"]
            ]

            return JsonResponse(
                {
                    "status": "success",
                    "nics": nics,
                    "interfaces": network_info["interfaces"],
                    "bonds": network_info["bonds"],
                    "host": host,
                }
            )

        finally:
            client.close()

    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def check_service(request):
    """Check network service status on servers"""
    try:
        data = json.loads(request.body)
        servers = data.get("servers", [])
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        port = int(data.get("port") or 22)

        if not servers:
            return JsonResponse(
                {"status": "error", "error": "缺少必要参数"}, status=400
            )

        results = []

        # Check each server
        for server in servers:
            server_result = {"server": server, "status": "checking"}

            try:
                client, error = ssh_connect(server, auth, port=port)
                if error:
                    server_result["status"] = "error"
                    server_result["error"] = error
                    results.append(server_result)
                    continue

                try:
                    check_result = check_network_service(client)
                    server_result.update(check_result)
                    server_result["status"] = "checked"

                finally:
                    client.close()

            except Exception as e:
                server_result["status"] = "error"
                server_result["error"] = str(e)

            results.append(server_result)

        # Determine if can proceed
        can_proceed = True
        warnings = []

        for result in results:
            if result["status"] == "error":
                can_proceed = False
                warnings.append(f"{result['server']}: 连接失败")
            elif not result.get("has_network") and not result.get("has_networkmanager"):
                can_proceed = False
                warnings.append(f"{result['server']}: 未检测到网络服务")
            elif not result.get("has_network") and result.get("has_networkmanager"):
                warnings.append(
                    f"{result['server']}: 仅有NetworkManager，建议安装network-scripts"
                )

        return JsonResponse(
            {
                "status": "success",
                "results": results,
                "can_proceed": can_proceed,
                "warnings": warnings,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def apply_bond(request):
    """Apply bond configuration to servers"""
    try:
        data = json.loads(request.body)

        # DEBUG: Return raw data to see what was received
        servers = data.get("servers", [])
        server_index_param = data.get("server_index", None)
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        port = int(data.get("port") or 22)
        bond_configs = data.get("bond_configs", [])

        # DEBUG: Check all keys in data
        received_keys = list(data.keys())

        if not all([servers, bond_configs]):
            return JsonResponse(
                {
                    "status": "error",
                    "error": "缺少必要参数",
                    "debug_received_keys": received_keys,
                    "debug_data": data,
                },
                status=400,
            )

        results = []

        # Apply config to each server
        for local_index, server in enumerate(servers):
            # Use the server_index parameter if provided, otherwise use local index
            server_index = (
                server_index_param if server_index_param is not None else local_index
            )

            # DEBUG: Write to file
            import os

            debug_file = os.path.join(os.path.dirname(__file__), "debug.log")
            with open(debug_file, "a") as f:
                f.write(
                    f"Processing server={server}, local_index={local_index}, server_index_param={server_index_param}, final_server_index={server_index}\n"
                )

            server_result = {"server": server, "status": "processing", "bonds": []}

            try:
                client, error = ssh_connect(server, auth, port=port)
                if error:
                    server_result["status"] = "error"
                    server_result["error"] = error
                    results.append(server_result)
                    continue

                try:
                    # Apply each bond config
                    for bond_config in bond_configs:
                        bond_config_for_server = bond_config.copy()

                        # Map 'slaves' to 'nics' for compatibility
                        if "slaves" in bond_config_for_server:
                            bond_config_for_server["nics"] = bond_config_for_server.pop(
                                "slaves"
                            )

                        # Parse IP address range
                        ip_cidr = bond_config_for_server.get("ip", "")

                        # DEBUG: Write to file
                        import os

                        debug_file = os.path.join(
                            os.path.dirname(__file__), "debug.log"
                        )
                        with open(debug_file, "a") as f:
                            f.write(
                                f"  IP parsing: ip_cidr={ip_cidr}, server_index={server_index}\n"
                            )

                        if ip_cidr and "-" in ip_cidr:
                            if "/" in ip_cidr:
                                ip_range, netmask = ip_cidr.rsplit("/", 1)
                                start_ip, end_ip = ip_range.split("-")
                                assigned_ip = assign_ip_from_range(
                                    start_ip.strip(), end_ip.strip(), server_index
                                )
                                bond_config_for_server["ip"] = (
                                    f"{assigned_ip}/{netmask}"
                                )

                                # DEBUG: Write to file
                                with open(debug_file, "a") as f:
                                    f.write(f"  Assigned IP: {assigned_ip}/{netmask}\n")
                            else:
                                start_ip, end_ip = ip_cidr.split("-")
                                assigned_ip = assign_ip_from_range(
                                    start_ip.strip(), end_ip.strip(), server_index
                                )
                                bond_config_for_server["ip"] = assigned_ip

                                # DEBUG: Write to file
                                with open(debug_file, "a") as f:
                                    f.write(f"  Assigned IP: {assigned_ip}\n")

                        bond_logs = []
                        success, message, bond_logs = create_bond_interface(
                            client, bond_config_for_server, bond_logs
                        )
                        server_result["bonds"].append(
                            {
                                "name": bond_config_for_server["name"],
                                "success": success,
                                "message": message,
                                "logs": bond_logs,
                            }
                        )

                    # Check if all bonds succeeded
                    all_success = all(b["success"] for b in server_result["bonds"])
                    server_result["status"] = "success" if all_success else "partial"

                finally:
                    client.close()

            except Exception as e:
                server_result["status"] = "error"
                server_result["error"] = str(e)

            results.append(server_result)

        # Summary
        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = sum(1 for r in results if r["status"] == "error")
        partial_count = sum(1 for r in results if r["status"] == "partial")

        return JsonResponse(
            {
                "status": "completed",
                "results": results,
                "debug_server_index_param": server_index_param,
                "debug_received_keys": received_keys,
                "summary": {
                    "total": len(servers),
                    "success": success_count,
                    "error": error_count,
                    "partial": partial_count,
                },
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def clear_bonds(request):
    """Clear all bond configurations"""
    try:
        data = json.loads(request.body)
        servers = data.get("servers", [])
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"status": "error", "error": str(exc)}, status=400)
        port = int(data.get("port") or 22)

        if not servers:
            return JsonResponse(
                {"status": "error", "error": "缺少必要参数"}, status=400
            )

        results = []

        # Clear bonds on each server
        for server in servers:
            server_result = {"server": server, "status": "processing"}

            try:
                client, error = ssh_connect(server, auth, port=port)
                if error:
                    server_result["status"] = "error"
                    server_result["error"] = error
                    results.append(server_result)
                    continue

                try:
                    clear_success, message, details, clear_logs = clear_all_bonds(
                        client
                    )

                    server_result["success"] = clear_success
                    server_result["message"] = message
                    server_result["details"] = details
                    server_result["logs"] = clear_logs
                    server_result["status"] = "success" if clear_success else "failed"

                finally:
                    client.close()

            except Exception as e:
                server_result["status"] = "error"
                server_result["error"] = str(e)

            results.append(server_result)

        # Summary
        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = sum(1 for r in results if r["status"] in ["error", "failed"])

        return JsonResponse(
            {
                "status": "completed",
                "results": results,
                "summary": {
                    "total": len(servers),
                    "success": success_count,
                    "error": error_count,
                },
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=500)


# Import os for path checking
import os
