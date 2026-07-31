"""
WebSocket Consumer for Network Bandwidth Test
实时输出 iperf3 测试结果
"""

import json
import asyncio
import threading
import time
import re
from channels.generic.websocket import AsyncWebsocketConsumer
from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth


class BandwidthTestConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ssh_port = 22

    async def connect(self):
        await self.accept()
        print("WebSocket connected for bandwidth test")

    async def disconnect(self, close_code):
        print(f"WebSocket disconnected: {close_code}")

    def get_test_ip(self, ssh_ip, test_network, auth):
        """
        从主机上查询指定网段的第一个 IP 地址
        如果未指定测试网段，则返回 SSH IP
        """
        if not test_network or test_network.strip() == "":
            return ssh_ip

        # 提取网段前缀 (例如: 192.168.34.0/24 -> 192.168.34)
        network_prefix = ".".join(test_network.split("/")[0].split(".")[:-1])

        try:
            # SSH 连接到主机
            client = connect_ssh(ssh_ip, auth, port=self.ssh_port, timeout=10)

            # 查询该网段的第一个 IP 地址
            cmd = f"ip addr show | grep \"inet {network_prefix}\\.\" | awk '{{print $2}}' | cut -d'/' -f1 | head -1"
            stdin, stdout, stderr = client.exec_command(cmd)
            test_ip = stdout.read().decode("utf-8").strip()

            client.close()

            if not test_ip:
                raise Exception(f"主机 {ssh_ip} 上未找到网段 {test_network} 的 IP 地址")

            return test_ip

        except Exception as e:
            raise Exception(f"查询主机 {ssh_ip} 的测试 IP 失败: {str(e)}")

    async def receive(self, text_data):
        """接收前端消息并启动测试"""
        try:
            data = json.loads(text_data)
            action = data.get("action")

            if action == "start_test":
                # 启动测试
                auth = parse_ssh_auth(data)
                config = {
                    "hosts": data.get("hosts", []),
                    "test_network": data.get("test_network", ""),
                    "test_mode": data.get("test_mode", "one2one"),
                    "port_min": data.get("port_min", 15000),
                    "cnum": data.get("cnum", 2),
                    "duration": data.get("duration", 10),
                    "core_min": data.get("core_min", 0),
                    "use_cpu_binding": data.get("use_cpu_binding", False),
                    "auth": auth,
                    "port": data.get("port", 22),
                }

                # 在后台线程运行测试
                thread = threading.Thread(
                    target=self.run_test_sync, args=(config,), daemon=True
                )
                thread.start()

        except ValueError as exc:
            await self.send(
                text_data=json.dumps({"type": "error", "message": str(exc)})
            )
        except Exception as exc:
            await self.send(
                text_data=json.dumps({"type": "error", "message": f"测试启动失败: {exc}"})
            )

    def run_test_sync(self, config):
        """在同步线程中运行测试"""
        asyncio.run(self.run_test(config))

    async def run_test(self, config):
        """执行带宽测试"""
        try:
            hosts = config["hosts"]
            test_network = config["test_network"]
            test_mode = config["test_mode"]
            port_min = config["port_min"]
            cnum = config["cnum"]
            duration = config["duration"]
            core_min = config["core_min"]
            use_cpu_binding = config["use_cpu_binding"]
            auth = config["auth"]
            self.ssh_port = int(config.get("port", 22))

            if len(hosts) < 2:
                await self.send_message("error", "至少需要 2 台主机")
                return

            # 检查依赖
            await self.send_message("log", "[检查] 开始检查主机依赖...")

            for host in hosts:
                # 检查 iperf3
                result = self.execute_ssh_command(
                    host, "which iperf3", auth
                )
                if result["success"] and result["output"].strip():
                    await self.send_message("log", f"[检查] {host}: ✓ iperf3 已安装")
                else:
                    await self.send_message(
                        "error",
                        f"[错误] {host}: iperf3 未安装，请执行: yum install iperf3 -y",
                    )
                    return

                # 检查 taskset (如果需要)
                if use_cpu_binding:
                    result = self.execute_ssh_command(
                        host, "which taskset", auth
                    )
                    if not (result["success"] and result["output"].strip()):
                        await self.send_message(
                            "log", f"[警告] {host}: taskset 未安装，将禁用 CPU 绑定"
                        )
                        use_cpu_binding = False

            await self.send_message("log", "[检查] 依赖检查通过")

            # 如果指定了测试网段，查询每个主机的测试 IP
            host_test_ip_map = {}
            if test_network and test_network.strip():
                await self.send_message("log", f"[查询] 查询主机测试网段 IP...")
                for host in hosts:
                    try:
                        test_ip = self.get_test_ip(
                            host, test_network, auth
                        )
                        host_test_ip_map[host] = test_ip
                        await self.send_message("log", f"[查询] {host} -> {test_ip}")
                    except Exception as e:
                        await self.send_message("error", f"[错误] {str(e)}")
                        return
            else:
                # 如果未指定测试网段，使用 SSH IP
                for host in hosts:
                    host_test_ip_map[host] = host

            # 根据测试模式执行测试
            if test_mode == "one2one":
                await self.run_one2one_test(
                    hosts,
                    host_test_ip_map,
                    port_min,
                    cnum,
                    duration,
                    core_min,
                    use_cpu_binding,
                    auth,
                )
            elif test_mode == "roundrobin":
                await self.run_roundrobin_test(
                    hosts,
                    host_test_ip_map,
                    port_min,
                    cnum,
                    duration,
                    core_min,
                    use_cpu_binding,
                    auth,
                )
            elif test_mode == "alltest":
                await self.run_one2one_test(
                    hosts,
                    host_test_ip_map,
                    port_min,
                    cnum,
                    duration,
                    core_min,
                    use_cpu_binding,
                    auth,
                )
                await self.run_roundrobin_test(
                    hosts,
                    host_test_ip_map,
                    port_min,
                    cnum,
                    duration,
                    core_min,
                    use_cpu_binding,
                    auth,
                )

            await self.send_message("completed", "测试完成")

        except Exception as e:
            import traceback

            await self.send_message("error", f"测试失败: {str(e)}")
            await self.send_message("log", f"详细错误: {traceback.format_exc()}")

    async def run_one2one_test(
        self,
        hosts,
        host_test_ip_map,
        port_min,
        cnum,
        duration,
        core_min,
        use_cpu_binding,
        auth,
    ):
        """执行 one2one 测试"""
        await self.send_message("log", f"[one2one] 开始测试...")

        server_host = hosts[0]
        client_hosts = hosts[1:]

        ports = list(range(port_min, port_min + cnum))

        # 启动服务端
        await self.send_message("log", f"[one2one] 在 {server_host} 启动服务端...")
        server_cmd = self.start_iperf3_servers(
            server_host, ports, core_min, use_cpu_binding, auth
        )
        await self.send_message("log", f"[命令] {server_host}: {server_cmd}")
        self.execute_ssh_command(server_host, server_cmd, auth)

        await asyncio.sleep(2)  # 等待服务端启动

        # 依次测试每个客户端
        results = []
        for client_host in client_hosts:
            # 获取测试 IP
            server_test_ip = host_test_ip_map[server_host]

            await self.send_message(
                "log", f"[one2one] 测试 {client_host} --> {server_test_ip}..."
            )

            stats = await self.run_iperf3_client(
                client_host,
                server_test_ip,
                ports,
                duration,
                core_min,
                use_cpu_binding,
                auth,
            )

            result = {
                "client": client_host,
                "server": server_test_ip,
                "sender_transfer_gb": round(stats["sender_transfer"], 2),
                "sender_bandwidth_gbps": round(stats["sender_bandwidth"], 2),
                "receiver_transfer_gb": round(stats["receiver_transfer"], 2),
                "receiver_bandwidth_gbps": round(stats["receiver_bandwidth"], 2),
                "retransmissions": stats["retransmissions"],
            }
            results.append(result)

            await self.send_message("result", {"test_mode": "one2one", **result})

        # 清理服务端进程
        self.cleanup_iperf3(server_host, auth)

        await self.send_message("log", f"[one2one] 测试完成")

    async def run_roundrobin_test(
        self,
        hosts,
        host_test_ip_map,
        port_min,
        cnum,
        duration,
        core_min,
        use_cpu_binding,
        auth,
    ):
        """执行 roundrobin 测试"""
        await self.send_message("log", f"[roundrobin] 开始测试...")

        ports = list(range(port_min, port_min + cnum))

        # 在所有主机上启动服务端
        await self.send_message("log", f"[roundrobin] 在所有主机上启动服务端...")
        for host in hosts:
            server_cmd = self.start_iperf3_servers(
                host, ports, core_min, use_cpu_binding, auth
            )
            await self.send_message("log", f"[命令] {host}: {server_cmd}")
            self.execute_ssh_command(host, server_cmd, auth)

        await asyncio.sleep(2)  # 等待服务端启动

        # 环形测试
        results = []
        for i, client_host in enumerate(hosts):
            server_host = hosts[(i + 1) % len(hosts)]

            # 获取测试 IP
            server_test_ip = host_test_ip_map[server_host]

            await self.send_message(
                "log", f"[roundrobin] 测试 {client_host} --> {server_test_ip}..."
            )

            stats = await self.run_iperf3_client(
                client_host,
                server_test_ip,
                ports,
                duration,
                core_min,
                use_cpu_binding,
                auth,
            )

            result = {
                "client": client_host,
                "server": server_test_ip,
                "sender_transfer_gb": round(stats["sender_transfer"], 2),
                "sender_bandwidth_gbps": round(stats["sender_bandwidth"], 2),
                "receiver_transfer_gb": round(stats["receiver_transfer"], 2),
                "receiver_bandwidth_gbps": round(stats["receiver_bandwidth"], 2),
                "retransmissions": stats["retransmissions"],
            }
            results.append(result)

            await self.send_message("result", {"test_mode": "roundrobin", **result})

        # 清理所有服务端进程
        for host in hosts:
            self.cleanup_iperf3(host, auth)

        await self.send_message("log", f"[roundrobin] 测试完成")

    def start_iperf3_servers(
        self, host, ports, core_min, use_cpu_binding, auth
    ):
        """启动 iperf3 服务端"""
        commands = []
        for i, port in enumerate(ports):
            if use_cpu_binding:
                core = core_min + i
                cmd = f"taskset -c {core} iperf3 -s -p {port} -D"
            else:
                cmd = f"iperf3 -s -p {port} -D"
            commands.append(cmd)

        full_command = " && ".join(commands)

        # 注意：这里是同步函数，不能直接 await，所以我们需要改造
        # 暂时先记录命令，稍后在调用处打印
        return full_command

    async def run_iperf3_client(
        self,
        client_host,
        server_host,
        ports,
        duration,
        core_min,
        use_cpu_binding,
        auth,
    ):
        """运行 iperf3 客户端并实时输出"""
        import time

        client = connect_ssh(client_host, auth, port=self.ssh_port, timeout=10)

        # 使用单个 iperf3 命令，-P 参数指定并发数
        cnum = len(ports)
        port = ports[0]  # 使用第一个端口

        if use_cpu_binding:
            core = core_min
            cmd = f"taskset -c {core} iperf3 -c {server_host} -p {port} -t {duration} -P {cnum}"
        else:
            cmd = f"iperf3 -c {server_host} -p {port} -t {duration} -P {cnum}"

        # 打印执行的命令
        await self.send_message("log", f"[命令] {client_host}: {cmd}")

        # 使用 channel 执行命令（类似 FIO 的方式）
        channel = client.get_transport().open_session()
        channel.get_pty()  # 请求伪终端，使 iperf3 实时输出（行缓冲）
        channel.settimeout(0.0)  # 非阻塞模式
        channel.exec_command(cmd)

        start_time = time.time()
        timeout = duration + 30

        output_lines = []
        full_output = ""
        summary_started = False
        summary_lines = []

        try:
            while time.time() - start_time < timeout:
                if channel.recv_ready():
                    # 直接读取并发送，不做过多处理
                    output = channel.recv(4096).decode("utf-8", errors="ignore")
                    if output:
                        full_output += output

                        # 直接发送原始输出到测试日志
                        await self.send_message("log", output)

                        # 收集用于解析
                        output_lines.append(output)

                # 检查命令是否完成
                if channel.exit_status_ready():
                    # 读取剩余数据
                    while channel.recv_ready():
                        output = channel.recv(4096).decode("utf-8", errors="ignore")
                        if output:
                            full_output += output
                            await self.send_message("log", output)
                            output_lines.append(output)
                    break

                # 短暂等待
                await asyncio.sleep(0.01)

        except Exception as e:
            await self.send_message("log", f"[错误] 读取输出失败: {str(e)}")

        finally:
            channel.close()
            client.close()

        # 提取最终汇总部分
        lines = full_output.split("\n")
        for i, line in enumerate(lines):
            if (
                "[ ID] Interval" in line
                and "Transfer" in line
                and "Bandwidth" in line
                and "Retr" in line
            ):
                # 找到汇总开始，收集后续所有行
                summary_lines = lines[i:]
                break

        # 发送最终汇总到测试输出
        if summary_lines:
            summary_text = "\n".join(line for line in summary_lines if line.strip())
            await self.send_message("summary", summary_text)

        # 解析详细统计信息
        stats = self.parse_iperf3_stats(full_output)

        return stats

    def parse_iperf3_stats(self, output):
        """解析 iperf3 输出获取详细统计信息"""
        try:
            stats = {
                "sender_transfer": 0,
                "sender_bandwidth": 0,
                "receiver_transfer": 0,
                "receiver_bandwidth": 0,
                "retransmissions": 0,
            }

            # 查找 [SUM] 行
            lines = output.split("\n")
            for line in lines:
                if "[SUM]" in line and "sender" in line:
                    # [SUM]   0.00-30.00  sec  13.9 GBytes  3.98 Gbits/sec  9574             sender
                    parts = line.split()
                    if len(parts) >= 8:
                        # 传输量: parts[3] = 13.9, parts[4] = GBytes
                        transfer_value = float(parts[3])
                        transfer_unit = parts[4]
                        if "GBytes" in transfer_unit:
                            stats["sender_transfer"] = transfer_value
                        elif "MBytes" in transfer_unit:
                            stats["sender_transfer"] = transfer_value / 1024

                        # 带宽: parts[5] = 3.98, parts[6] = Gbits/sec
                        bandwidth_value = float(parts[5])
                        bandwidth_unit = parts[6]
                        if "Gbits/sec" in bandwidth_unit:
                            stats["sender_bandwidth"] = bandwidth_value
                        elif "Mbits/sec" in bandwidth_unit:
                            stats["sender_bandwidth"] = bandwidth_value / 1000

                        # 重传数: parts[7] = 9574
                        if len(parts) >= 8:
                            try:
                                stats["retransmissions"] = int(parts[7])
                            except ValueError:
                                pass

                elif "[SUM]" in line and "receiver" in line:
                    # [SUM]   0.00-30.00  sec  13.9 GBytes  3.97 Gbits/sec                  receiver
                    parts = line.split()
                    if len(parts) >= 7:
                        # 传输量: parts[3] = 13.9, parts[4] = GBytes
                        transfer_value = float(parts[3])
                        transfer_unit = parts[4]
                        if "GBytes" in transfer_unit:
                            stats["receiver_transfer"] = transfer_value
                        elif "MBytes" in transfer_unit:
                            stats["receiver_transfer"] = transfer_value / 1024

                        # 带宽: parts[5] = 3.97, parts[6] = Gbits/sec
                        bandwidth_value = float(parts[5])
                        bandwidth_unit = parts[6]
                        if "Gbits/sec" in bandwidth_unit:
                            stats["receiver_bandwidth"] = bandwidth_value
                        elif "Mbits/sec" in bandwidth_unit:
                            stats["receiver_bandwidth"] = bandwidth_value / 1000

            return stats
        except Exception as e:
            print(f"Failed to parse iperf3 stats: {e}")
            return {
                "sender_transfer": 0,
                "sender_bandwidth": 0,
                "receiver_transfer": 0,
                "receiver_bandwidth": 0,
                "retransmissions": 0,
            }

    def parse_iperf3_bandwidth(self, output):
        """解析 iperf3 输出获取带宽"""
        try:
            # 匹配 receiver 行: [  5]   0.00-10.00  sec  1.09 GBytes  0.937 Gbits/sec  receiver
            pattern = r"receiver.*?(\d+\.?\d*)\s+(Gbits|Mbits|Kbits)/sec"
            matches = re.findall(pattern, output, re.IGNORECASE)

            total_bandwidth = 0
            for value, unit in matches:
                bandwidth = float(value)
                if "Kbits" in unit:
                    bandwidth /= 1000000
                elif "Mbits" in unit:
                    bandwidth /= 1000
                # Gbits is already correct

                total_bandwidth += bandwidth

            return total_bandwidth
        except Exception as e:
            print(f"Failed to parse bandwidth: {e}")
            return 0

    def cleanup_iperf3(self, host, auth):
        """清理 iperf3 进程"""
        self.execute_ssh_command(host, "pkill iperf3", auth)

    def execute_ssh_command(self, host, command, auth):
        """执行 SSH 命令"""
        try:
            client = connect_ssh(host, auth, port=self.ssh_port, timeout=10)

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

    async def send_message(self, msg_type, content):
        """发送消息到前端"""
        await self.send(text_data=json.dumps({"type": msg_type, "content": content}))
