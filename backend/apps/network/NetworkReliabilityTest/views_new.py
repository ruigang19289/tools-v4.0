"""
Network Bandwidth Test Backend - Based on iperf3-bench
支持 one2one, roundrobin, alltest 三种测试模式
"""

import json
import uuid
import subprocess
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth

# Global test tasks storage
test_tasks = {}
test_tasks_lock = threading.Lock()


def execute_ssh_command(host, command, auth, port=22):
    """通过 SSH 执行远程命令。"""
    try:
        client = connect_ssh(host, auth, port=port, timeout=10)

        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode("utf-8", errors="ignore")
        error = stderr.read().decode("utf-8", errors="ignore")
        exit_code = stdout.channel.recv_exit_status()

        client.close()

        return {
            "success": exit_code == 0,
            "output": output,
            "error": error,
            "exit_code": exit_code,
        }
    except Exception as e:
        return {"success": False, "output": "", "error": str(e), "exit_code": -1}


def start_iperf3_server(
    host, ports, core_min=0, use_cpu_binding=False, auth=None, ssh_port=22
):
    """
    在远程主机启动 iperf3 服务端
    ports: 端口列表，如 [15000, 15001, 15002]
    use_cpu_binding: 是否使用 CPU 绑定
    """
    commands = []
    for i, port in enumerate(ports):
        if use_cpu_binding:
            core = core_min + i
            cmd = f"taskset -c {core} iperf3 -s -1 -f g -p {port} 2>&1 | grep -w receiver &"
        else:
            cmd = f"iperf3 -s -1 -f g -p {port} 2>&1 | grep -w receiver &"
        commands.append(cmd)

    full_command = " ".join(commands)
    result = execute_ssh_command(host, full_command, auth, ssh_port)

    return result


def start_iperf3_client(
    host,
    server_ip,
    ports,
    duration,
    core_min=0,
    use_cpu_binding=False,
    auth=None,
    ssh_port=22,
):
    """
    在远程主机启动 iperf3 客户端
    use_cpu_binding: 是否使用 CPU 绑定
    """
    commands = []
    for i, port in enumerate(ports):
        if use_cpu_binding:
            core = core_min + i
            cmd = f"taskset -c {core} iperf3 -c {server_ip} -f g -t {duration} -p {port} 2>&1 | grep -w receiver"
        else:
            cmd = f"iperf3 -c {server_ip} -f g -t {duration} -p {port} 2>&1 | grep -w receiver"
        commands.append(cmd + " &")

    # 等待所有客户端完成
    full_command = " ".join(commands) + " wait"
    result = execute_ssh_command(host, full_command, auth, ssh_port)

    return result


def parse_iperf3_output(output):
    """
    解析 iperf3 输出，提取带宽信息
    查找 receiver 行的带宽数据
    """
    try:
        # 匹配格式: [  5]   0.00-10.00  sec  1.09 GBytes  0.937 Gbits/sec                  receiver
        pattern = r"receiver.*?([\d.]+)\s+([KMG])bits/sec"
        matches = re.findall(pattern, output, re.IGNORECASE)

        total_bandwidth = 0
        for value, unit in matches:
            bandwidth = float(value)
            if unit.upper() == "K":
                bandwidth /= 1000000  # Convert to Gbits/sec
            elif unit.upper() == "M":
                bandwidth /= 1000  # Convert to Gbits/sec
            # G is already in Gbits/sec

            total_bandwidth += bandwidth

        return total_bandwidth
    except Exception as e:
        print(f"Failed to parse iperf3 output: {e}")
        return 0


def check_dependencies(hosts, auth, ssh_port=22):
    """
    检查所有主机是否安装了必要的依赖
    返回: (success: bool, results: list)
    """
    results = []
    all_ok = True

    # 需要检查的命令: (命令名, 是否必需)
    commands_to_check = [
        ("iperf3", True, "性能测试工具"),
        ("grep", True, "文本过滤工具"),
        ("taskset", False, "CPU 绑定工具（可选）"),
    ]

    for host in hosts:
        host_result = {"host": host, "commands": {}, "messages": []}

        for cmd, required, description in commands_to_check:
            result = execute_ssh_command(host, f"which {cmd}", auth, ssh_port)
            is_installed = result["success"] and result["output"].strip()

            host_result["commands"][cmd] = is_installed

            if is_installed:
                host_result["messages"].append(
                    f"✓ {cmd}: {result['output'].strip()} - {description}"
                )
            else:
                if required:
                    host_result["messages"].append(
                        f"✗ {cmd}: 未安装 - {description} [必需]"
                    )
                    all_ok = False
                else:
                    host_result["messages"].append(
                        f"⚠ {cmd}: 未安装 - {description} [可选]"
                    )

        results.append(host_result)

    return all_ok, results


def run_one2one_test(task_id, config):
    """
    执行 one2one 测试：一台服务端接收，其他主机发送
    """
    try:
        task = test_tasks[task_id]
        task["status"] = "running"
        task["current_test"] = "one2one"

        hosts = config["hosts"]
        server_host = hosts[0]  # 第一台作为服务端
        client_hosts = hosts[1:]  # 其他作为客户端

        port_min = config.get("port_min", 15000)
        cnum = config.get("cnum", 2)
        duration = config.get("duration", 10)
        core_min = config.get("core_min", 0)
        use_cpu_binding = config.get("use_cpu_binding", False)
        auth = config["auth"]
        ssh_port = config.get("port", 22)

        ports = list(range(port_min, port_min + cnum))

        results = []

        # 启动服务端
        task["log"].append(f"[one2one] Starting iperf3 servers on {server_host}...")
        server_result = start_iperf3_server(
            server_host, ports, core_min, use_cpu_binding, auth, ssh_port
        )

        if not server_result["success"]:
            task["log"].append(
                f"[ERROR] Failed to start server on {server_host}: {server_result['error']}"
            )
            task["status"] = "error"
            return

        time.sleep(2)  # 等待服务端启动

        # 依次启动客户端
        for client_host in client_hosts:
            task["log"].append(f"[one2one] Testing {client_host} --> {server_host}...")

            client_result = start_iperf3_client(
                client_host,
                server_host,
                ports,
                duration,
                core_min,
                use_cpu_binding,
                auth,
                ssh_port,
            )

            if client_result["success"]:
                bandwidth = parse_iperf3_output(client_result["output"])
                results.append(
                    {
                        "client": client_host,
                        "server": server_host,
                        "bandwidth_gbps": round(bandwidth, 2),
                    }
                )
                task["log"].append(
                    f"[one2one] {client_host} --> {server_host}: {bandwidth:.2f} Gb/s"
                )
            else:
                task["log"].append(
                    f"[ERROR] Client test failed: {client_result['error']}"
                )

        task["results"]["one2one"] = results
        task["log"].append(f"[one2one] Test completed")

    except Exception as e:
        task["log"].append(f"[ERROR] one2one test failed: {str(e)}")
        task["status"] = "error"


def run_roundrobin_test(task_id, config):
    """
    执行 roundrobin 测试：每台主机互相发送（环形测试）
    """
    try:
        task = test_tasks[task_id]
        task["current_test"] = "roundrobin"

        hosts = config["hosts"]
        port_min = config.get("port_min", 15000)
        cnum = config.get("cnum", 2)
        duration = config.get("duration", 10)
        core_min = config.get("core_min", 0)
        use_cpu_binding = config.get("use_cpu_binding", False)
        auth = config["auth"]
        ssh_port = config.get("port", 22)

        ports = list(range(port_min, port_min + cnum))

        results = []

        # 在所有主机上启动服务端
        task["log"].append(f"[roundrobin] Starting iperf3 servers on all hosts...")
        for host in hosts:
            server_result = start_iperf3_server(
                host, ports, core_min, use_cpu_binding, auth, ssh_port
            )
            if not server_result["success"]:
                task["log"].append(
                    f"[ERROR] Failed to start server on {host}: {server_result['error']}"
                )

        time.sleep(2)  # 等待服务端启动

        # 每台主机向下一台主机发送（环形）
        task["log"].append(f"[roundrobin] Starting client tests...")
        for i, client_host in enumerate(hosts):
            server_host = hosts[(i + 1) % len(hosts)]  # 下一台主机

            task["log"].append(
                f"[roundrobin] Testing {client_host} --> {server_host}..."
            )

            client_result = start_iperf3_client(
                client_host,
                server_host,
                ports,
                duration,
                core_min,
                use_cpu_binding,
                auth,
                ssh_port,
            )

            if client_result["success"]:
                bandwidth = parse_iperf3_output(client_result["output"])
                results.append(
                    {
                        "client": client_host,
                        "server": server_host,
                        "bandwidth_gbps": round(bandwidth, 2),
                    }
                )
                task["log"].append(
                    f"[roundrobin] {client_host} --> {server_host}: {bandwidth:.2f} Gb/s"
                )
            else:
                task["log"].append(
                    f"[ERROR] Client test failed: {client_result['error']}"
                )

        task["results"]["roundrobin"] = results
        task["log"].append(f"[roundrobin] Test completed")

    except Exception as e:
        task["log"].append(f"[ERROR] roundrobin test failed: {str(e)}")
        task["status"] = "error"


def run_bandwidth_test(task_id, config):
    """
    执行带宽测试（后台线程）
    """
    try:
        task = test_tasks[task_id]

        # 检查依赖
        task["log"].append("[检查] 开始检查主机依赖...")
        hosts = config["hosts"]
        auth = config["auth"]
        ssh_port = config.get("port", 22)
        use_cpu_binding = config.get("use_cpu_binding", False)

        all_ok, dep_results = check_dependencies(hosts, auth, ssh_port)

        for host_result in dep_results:
            task["log"].append(f"[检查] {host_result['host']}:")
            for msg in host_result["messages"]:
                task["log"].append(f"  {msg}")

        if not all_ok:
            task["log"].append("[错误] 部分主机缺少必要依赖，无法继续测试")
            task["log"].append("[提示] 请在缺少依赖的主机上安装: yum install iperf3 -y")
            task["status"] = "error"
            return

        # 如果启用 CPU 绑定但 taskset 不可用，给出警告
        if use_cpu_binding:
            taskset_missing = [
                r["host"]
                for r in dep_results
                if not r["commands"].get("taskset", False)
            ]
            if taskset_missing:
                task["log"].append(
                    f"[警告] 以下主机未安装 taskset，将禁用 CPU 绑定: {', '.join(taskset_missing)}"
                )
                config["use_cpu_binding"] = False

        task["log"].append("[检查] 依赖检查通过，开始测试...")

        test_mode = config.get("test_mode", "one2one")

        if test_mode == "one2one":
            run_one2one_test(task_id, config)
        elif test_mode == "roundrobin":
            run_roundrobin_test(task_id, config)
        elif test_mode == "alltest":
            run_one2one_test(task_id, config)
            if test_tasks[task_id]["status"] != "error":
                run_roundrobin_test(task_id, config)

        with test_tasks_lock:
            if test_tasks[task_id]["status"] != "error":
                test_tasks[task_id]["status"] = "completed"
                test_tasks[task_id]["completed_at"] = time.time()

    except Exception as e:
        with test_tasks_lock:
            test_tasks[task_id]["status"] = "error"
            test_tasks[task_id]["log"].append(f"[错误] 测试失败: {str(e)}")
            import traceback

            test_tasks[task_id]["log"].append(
                f"[错误] 详细信息: {traceback.format_exc()}"
            )


@csrf_exempt
@require_http_methods(["POST"])
def start_bandwidth_test(request):
    """启动带宽测试"""
    try:
        data = json.loads(request.body)

        hosts = data.get("hosts", [])
        test_mode = data.get("test_mode", "one2one")
        port_min = data.get("port_min", 15000)
        cnum = data.get("cnum", 2)
        duration = data.get("duration", 10)
        core_min = data.get("core_min", 0)
        use_cpu_binding = data.get("use_cpu_binding", False)
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        ssh_port = int(data.get("port", 22) or 22)

        if len(hosts) < 2:
            return JsonResponse({"error": "至少需要2台主机"}, status=400)

        # 创建测试任务
        task_id = str(uuid.uuid4())

        config = {
            "hosts": hosts,
            "test_mode": test_mode,
            "port_min": port_min,
            "cnum": cnum,
            "duration": duration,
            "core_min": core_min,
            "use_cpu_binding": use_cpu_binding,
            "auth": auth,
            "port": ssh_port,
        }

        test_tasks[task_id] = {
            "task_id": task_id,
            "config": config,
            "status": "initializing",
            "current_test": "",
            "results": {},
            "log": [],
            "start_time": time.time(),
        }

        # 启动后台测试线程
        thread = threading.Thread(
            target=run_bandwidth_test, args=(task_id, config), daemon=True
        )
        thread.start()

        return JsonResponse(
            {"status": "started", "task_id": task_id, "message": "测试已启动"}
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def get_test_results(request, task_id):
    """获取测试结果"""
    with test_tasks_lock:
        if task_id not in test_tasks:
            return JsonResponse({"error": "测试任务不存在"}, status=404)

        task = test_tasks[task_id]

        return JsonResponse(
            {
                "task_id": task_id,
                "status": task["status"],
                "current_test": task.get("current_test", ""),
                "results": task.get("results", {}),
                "log": task.get("log", []),
                "start_time": task["start_time"],
                "completed_at": task.get("completed_at"),
            }
        )


@csrf_exempt
@require_http_methods(["DELETE"])
def cancel_test(request, task_id):
    """取消测试任务"""
    with test_tasks_lock:
        if task_id in test_tasks:
            test_tasks[task_id]["status"] = "cancelled"
            del test_tasks[task_id]

    return JsonResponse({"status": "cancelled"})


@csrf_exempt
@require_http_methods(["POST"])
def validate_hosts(request):
    """验证主机连接"""
    try:
        data = json.loads(request.body)
        hosts = data.get("hosts", [])
        try:
            auth = parse_ssh_auth(data)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        port = data.get("port", 22)

        results = []

        for host in hosts:
            result = execute_ssh_command(host, 'echo "OK"', auth, port)

            results.append(
                {
                    "host": host,
                    "status": "success" if result["success"] else "error",
                    "message": "Connection successful"
                    if result["success"]
                    else result["error"],
                }
            )

        return JsonResponse({"status": "success", "results": results})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
