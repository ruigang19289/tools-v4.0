from __future__ import print_function

import os

from .common import FAIL, PASS, WARN, ERROR, NodeReport


def run_node_dry_run(node, all_nodes, only, verbose=False):
    report = NodeReport(node)
    if verbose:
        print("[{0}] dry-run start".format(node.mgmt_ip))
    for category in only or []:
        print("[{0}] dry-run start {1} check".format(node.mgmt_ip, category))
        if category == "bios":
            run_bios_dry_run(report)
        elif category == "os":
            run_os_dry_run(report)
        elif category == "hardware":
            run_hardware_dry_run(report)
        elif category == "network":
            run_network_dry_run(report)
            report.add("NET-06", "network", WARN, "dry-run", "所有配置节点通过 rdma-diag", "dry-run: 未上传或执行 diag-all.sh")
        elif category == "disk":
            run_disk_dry_run(report)
        print("[{0}] dry-run finish {1} check".format(node.mgmt_ip, category))
    if verbose:
        print("[{0}] dry-run done".format(node.mgmt_ip))
    return report


def run_node_disk_perf_dry_run(node, timeout, verbose, run_id=None):
    local_dir = os.path.abspath("disk_perf_logs_dry_run_{0}_{1}".format(run_id or "manual", node.mgmt_ip))
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)
    with open(os.path.join(local_dir, "disk_perf_detail.log"), "w") as fh:
        fh.write("dry-run: skip blkdiscard, fio, iostat and NVMe log scan\n")
    return {
        "ip": node.mgmt_ip,
        "status": WARN,
        "errors": ["未发现非系统盘 NVMe"],
        "disks": [],
        "fio": "dry-run:fio",
        "log_dir": local_dir,
    }


def run_network_perf_dry_run(storage_nodes, compute_nodes, stornets, ptnets, timeout, verbose, run_id=None):
    local_log_dir = os.path.abspath("rdma_perf_logs_dry_run_{0}".format(run_id or "manual"))
    if not os.path.exists(local_log_dir):
        os.makedirs(local_log_dir)
    result = {
        "status": WARN,
        "executor": storage_nodes[0].mgmt_ip if storage_nodes else "",
        "reason": "dry-run: skip archive upload and RDMA scripts",
        "log_dir": local_log_dir,
        "remote_dir": "dry-run",
        "rounds": [],
    }
    with open(os.path.join(local_log_dir, "network_perf_detail.log"), "w") as fh:
        fh.write("dry-run: no RDMA devices, no tool upload, no scripts executed\n")
    return result


def run_bios_dry_run(report):
    report.add("BIOS-ACCESS", "bios", ERROR, "dry-run", "Redfish BIOS JSON", "Redfish 访问未执行")


def run_os_dry_run(report):
    report.add("OS-01", "os", PASS, "500G", ">=400G")
    report.add("OS-02", "os", PASS, "2048 kB", "2048 kB")
    report.add("OS-03", "os", PASS, "SELINUX=disabled", "SELINUX=disabled")
    report.add("OS-04", "os", PASS, "Disabled", "Disabled")
    report.add("OS-05", "os", PASS, "active=inactive, enabled=disabled", "firewalld closed")
    report.add("OS-06", "os", PASS, "all ok", "all nodes passwordless")
    report.add("OS-07", "os", PASS, "LANG=en_US.UTF-8", "LANG=en_US.UTF-8")
    for idx in ["OS-08", "OS-09", "OS-10", "OS-11"]:
        report.add(idx, "os", PASS, "installed", "installed")
    report.add("OS-12", "os", PASS, "1", "1")
    report.add("OS-13", "os", PASS, "2", "2")
    report.add("OS-14", "os", PASS, "0", "0")


def run_hardware_dry_run(report):
    report.add("HW-01", "hardware", WARN, "未发现高速物理网卡", "NUMA balanced", "没有可检查设备")
    report.add("HW-02", "hardware", WARN, "未发现非系统 NVMe", "NUMA balanced", "没有可检查设备")
    report.add("HW-03", "hardware", WARN, "未发现高速物理网卡", "current PCIe link equals max", "没有可检查设备")
    report.add("HW-04", "hardware", WARN, "未发现非系统 NVMe", "current PCIe link equals max", "没有可检查设备")


def run_network_dry_run(report):
    report.add("NET-00", "network", PASS, "万兆网口数量=2; mlx5_0 speed=100000Mb/s model=Mellanox Technologies MT27800 Family [ConnectX-5]; mlx5_1 speed=100000Mb/s model=Mellanox Technologies MT27800 Family [ConnectX-5]", "明确万兆网口数量和型号")
    report.add("NET-01", "network", PASS, "dry-run ok", "ibv_devices/ibdev2netdev/rdma link ok")
    report.add("NET-02", "network", PASS, "rdma-core-2410mlnx54-1.2410068.x86_64", "rdma-core-2410mlnx54-1.2410068.x86_64")
    report.add("NET-03", "network", WARN, "未发现 >=25000Mb/s 网卡", ">=25GE UP", "没有可检查的高速网卡")
    report.add("NET-04", "network", FAIL, "未发现 RDMA 设备", "ibstat Link layer", "没有 RDMA 设备")
    report.add("NET-05", "network", FAIL, "Loaded: unloaded", "Loaded: loaded", "enable-rocev2.service 未处于 loaded 状态")


def run_disk_dry_run(report):
    report.add("DISK-01", "disk", WARN, "未发现非系统盘 NVMe", "data NVMe disks", "没有可检查的数据盘")
    report.add("DISK-02", "disk", WARN, "未发现非系统盘 NVMe", "no data signature", "没有可检查的数据盘")
    report.add("DISK-03", "disk", WARN, "未发现非系统盘 NVMe", ">=80% remaining", "没有可检查的数据盘")
