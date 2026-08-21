from __future__ import print_function

"""Read-only, cross-platform node information collection."""

import csv
import os
import re
import time

from .ssh import SSHClient


class LocalCommandExecutor(object):
    """Run inventory commands directly on the execution host."""

    def run(self, command, timeout=None):
        import subprocess
        try:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        except Exception as exc:
            return 1, "", str(exc)


def _run(ssh, command, timeout=30):
    try:
        code, out, err = ssh.run(command, timeout=timeout)
        return code, out.strip(), err.strip()
    except Exception as exc:
        return 1, "", str(exc)


def _field(text, name):
    for line in text.splitlines():
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def _human_bytes(value):
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return "unknown"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(amount)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return "{0:.2f} {1}".format(size, unit)
        size /= 1024.0
    return "unknown"


def _generation(speed):
    speeds = {"2.5": "Gen1", "5.0": "Gen2", "8.0": "Gen3", "16.0": "Gen4", "32.0": "Gen5", "64.0": "Gen6"}
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", speed or "")
    return speeds.get(match.group(1), speed or "unknown") if match else "unknown"


def collect_node(node, timeout):
    result = {"node": node.mgmt_ip, "role": node.role, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "errors": []}
    try:
        with SSHClient(node, timeout) as ssh:
            result.update(_collect_common(ssh, node.role))
            if node.role == "storage":
                result["nvmes"] = _collect_nvmes(ssh)
            else:
                result["nvmes"] = []
    except Exception as exc:
        result["errors"].append(str(exc))
        result.setdefault("cpu", {})
        result.setdefault("memory", {})
        result.setdefault("os", {})
        result.setdefault("nics", [])
        result.setdefault("port_18000", {"status": "unknown", "listeners": "-"})
    return result


def collect_local_node(timeout):
    result = {"node": os.uname()[1], "role": "local", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "errors": []}
    executor = LocalCommandExecutor()
    try:
        result.update(_collect_common(executor, "local"))
        result["nvmes"] = _collect_nvmes(executor)
    except Exception as exc:
        result["errors"].append(str(exc))
        result.setdefault("cpu", {})
        result.setdefault("memory", {})
        result.setdefault("os", {})
        result.setdefault("nics", [])
        result.setdefault("nvmes", [])
    return result


def _collect_port_18000(ssh):
    command = "if command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null; elif command -v netstat >/dev/null 2>&1; then netstat -lnt 2>/dev/null; else echo __PORT_CHECK_UNAVAILABLE__; fi"
    code, output, error = _run(ssh, command)
    if "__PORT_CHECK_UNAVAILABLE__" in output:
        return {"status": "unknown", "listeners": "检查命令不可用"}
    listeners = []
    for line in output.splitlines():
        if re.search(r"(?:^|:)18000(?:\s|$)", line):
            listeners.append(line.strip())
    return {"status": "已占用" if listeners else "未占用", "listeners": "; ".join(listeners) or "-"}


def _collect_common(ssh, role):
    code, lscpu, err = _run(ssh, "lscpu 2>/dev/null")
    cpu = _parse_cpu(lscpu)
    os_info = _collect_os(ssh)
    memory = _collect_memory(ssh)
    nics = _collect_nics(ssh)
    return {"cpu": cpu, "os": os_info, "memory": memory, "nics": nics, "rdma_version": _collect_rdma_version(ssh), "port_18000": _collect_port_18000(ssh), "role": role}


def _parse_cpu(text):
    sockets = _field(text, "Socket(s)")
    cores_per_socket = _field(text, "Core(s) per socket")
    threads = _field(text, "Thread(s) per core")
    architecture = _field(text, "Architecture")
    model = _field(text, "Model name")
    if model == "unknown":
        model = _field(text, "CPU")
    try:
        core_count = int(sockets) * int(cores_per_socket)
    except ValueError:
        core_count = "unknown"
    if threads == "1":
        hyperthreading = "未开启"
    elif threads == "2":
        hyperthreading = "已开启"
    else:
        hyperthreading = "unknown"
    return {"model": model, "architecture": architecture, "sockets": sockets, "cores": str(core_count), "threads_per_core": threads, "hyperthreading": hyperthreading, "numa_nodes": _field(text, "NUMA node(s)")}

def _collect_os(ssh):
    code, release, err = _run(ssh, "cat /etc/os-release 2>/dev/null")
    values = {}
    for line in release.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    code, kernel, err = _run(ssh, "uname -r")
    return {"name": values.get("PRETTY_NAME", values.get("NAME", "unknown")), "version": values.get("VERSION_ID", "unknown"), "kernel": kernel or "unknown"}


def _collect_memory(ssh):
    # Parse raw DMI blocks in Python; this avoids distribution-specific awk behavior.
    code, out, err = _run(ssh, "dmidecode -t memory 2>/dev/null", timeout=60)
    records, current, in_device = [], {}, False
    for raw in out.splitlines():
        line = raw.strip()
        if line == "Memory Device":
            if in_device and _installed_dimm(current):
                records.append(current)
            current, in_device = {}, True
            continue
        if in_device and ":" in line:
            key, value = line.split(":", 1)
            if key in ("Size", "Type", "Speed", "Configured Memory Speed"):
                current[key] = value.strip()
    if in_device and _installed_dimm(current):
        records.append(current)

    code, numa, numa_err = _run(ssh, "numactl -H 2>/dev/null || true")
    numa_memory = []
    for line in numa.splitlines():
        match = re.match(r"node\s+(\d+)\s+size:\s+(.+)", line.strip())
        if match:
            numa_memory.append("N{0}:unknown/{1}".format(match.group(1), match.group(2)))
    specs = [
        "{0} {1}; 额定={2}; 实际={3}".format(
            record.get("Size", "unknown"), record.get("Type", "unknown"),
            record.get("Speed", "unknown"), record.get("Configured Memory Speed", "unknown"),
        ) for record in records
    ]
    counts = {}
    for spec in specs:
        counts[spec] = counts.get(spec, 0) + 1
    specification = "\n".join(["{0} * {1}".format(spec, count) for spec, count in sorted(counts.items())])
    return {"count": str(len(records)) if records else "unknown", "specification": specification or "unknown", "numa_summary": "; ".join(numa_memory) or "unknown"}


def _installed_dimm(record):
    size = record.get("Size", "")
    return bool(size) and size not in ("No Module Installed", "Unknown", "Not Installed")

def _collect_pcie(ssh, sys_path):
    command = r'''p=$(readlink -f "{path}" 2>/dev/null || true); while [ -n "$p" ] && [ "$p" != / ]; do
if [ -f "$p/current_link_speed" ] || [ -f "$p/max_link_speed" ]; then
  pci=$(basename "$p"); cap=""; sta=""
  if command -v lspci >/dev/null 2>&1; then
    cap=$(lspci -s "$pci" -vvv 2>/dev/null | grep -m1 'LnkCap:' || true)
    sta=$(lspci -s "$pci" -vvv 2>/dev/null | grep -m1 'LnkSta:' || true)
  fi
  printf '%s|%s|%s|%s|%s|%s|%s
' "$pci" "$(cat "$p/current_link_speed" 2>/dev/null)" "$(cat "$p/current_link_width" 2>/dev/null)" "$(cat "$p/max_link_speed" 2>/dev/null)" "$(cat "$p/max_link_width" 2>/dev/null)" "$cap" "$sta"; break; fi; p=${{p%/*}}; done'''.format(path=sys_path)
    code, out, err = _run(ssh, command)
    parts = out.split("|", 6)
    if len(parts) != 7:
        return {"address": "unknown", "current": "unknown", "maximum": "unknown", "lnkcap": "unknown", "lnksta": "unknown", "sysfs_status": "unknown", "lspci_status": "unknown", "status": "unknown"}
    cap_speed, cap_width = _parse_lspci_link(parts[5])
    sta_speed, sta_width = _parse_lspci_link(parts[6])
    sysfs_status = _pcie_link_status(parts[3], parts[4], parts[1], parts[2])
    lspci_status = _pcie_link_status(cap_speed, cap_width, sta_speed, sta_width)
    if sysfs_status == "ok" and lspci_status == "ok":
        status = "ok"
    elif sysfs_status == lspci_status and sysfs_status != "unknown":
        status = sysfs_status
    elif sysfs_status != "unknown" and lspci_status != "unknown":
        status = "mismatch ({0}/{1})".format(sysfs_status, lspci_status)
    else:
        status = "unknown"
    return {
        "address": parts[0] or "unknown",
        "current": "{0} x{1}".format(_generation(parts[1]), parts[2] or "unknown"),
        "maximum": "{0} x{1}".format(_generation(parts[3]), parts[4] or "unknown"),
        "lnkcap": "{0} x{1}".format(_generation(cap_speed), cap_width or "unknown"),
        "lnksta": "{0} x{1}".format(_generation(sta_speed), sta_width or "unknown"),
        "sysfs_status": sysfs_status,
        "lspci_status": lspci_status,
        "status": status,
    }


def _parse_lspci_link(line):
    speed = re.search(r"Speed\s+([0-9]+(?:\.[0-9]+)?GT/s)", line or "")
    width = re.search(r"Width\s+x([0-9]+)", line or "")
    return (speed.group(1) if speed else "", width.group(1) if width else "")


def _pcie_link_status(cap_speed, cap_width, sta_speed, sta_width):
    def numeric_speed(value):
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value or "")
        return float(match.group(1)) if match else None
    def numeric_width(value):
        match = re.search(r"x?([0-9]+)", value or "")
        return int(match.group(1)) if match else None
    try:
        cap_width = numeric_width(cap_width)
        sta_width = numeric_width(sta_width)
    except (TypeError, ValueError):
        return "unknown"
    cap_speed = numeric_speed(cap_speed)
    sta_speed = numeric_speed(sta_speed)
    if cap_speed is None or sta_speed is None or cap_width is None or sta_width is None:
        return "unknown"
    reasons = []
    if sta_speed < cap_speed:
        reasons.append("speed")
    if sta_width < cap_width:
        reasons.append("width")
    return "degraded ({0})".format("/".join(reasons)) if reasons else "ok"

def _collect_nics(ssh):
    command = r'''for n in /sys/class/net/*; do
name=${n##*/}; [ "$name" = lo ] && continue; [ -e "$n/device" ] || continue; [ -d "$n/bonding" ] && continue
speed=$(cat "$n/speed" 2>/dev/null || true); case "$speed" in ''|*[!0-9]*) continue;; esac; [ "$speed" -ge 10000 ] || continue
pci=$(basename "$(readlink -f "$n/device")"); numa=$(cat "$n/device/numa_node" 2>/dev/null || echo unknown)
model=$(lspci -s "$pci" 2>/dev/null | sed 's/^[^ ]* //'); driver=$(ethtool -i "$name" 2>/dev/null || true)
firmware=$(printf '%s\n' "$driver" | awk -F': ' '/^firmware-version:/ {print $2}'); drv=$(printf '%s\n' "$driver" | awk -F': ' '/^driver:/ {print $2}'); ver=$(printf '%s\n' "$driver" | awk -F': ' '/^version:/ {print $2}')
printf '%s|%s|%s|%s|%s|%s|%s\n' "$name" "$speed" "$numa" "${model:-unknown}" "${drv:-unknown}" "${ver:-unknown}" "${firmware:-unknown}"
done'''
    code, out, err = _run(ssh, command)
    nics = []
    for line in out.splitlines():
        parts = line.split("|", 6)
        if len(parts) != 7:
            continue
        pcie = _collect_pcie(ssh, "/sys/class/net/{0}/device".format(parts[0]))
        nics.append({"interface": parts[0], "speed": parts[1] + " Mb/s", "numa": parts[2], "model": parts[3], "driver": parts[4], "driver_version": parts[5], "firmware": parts[6], "pcie": pcie})
    return nics


def _collect_rdma_version(ssh):
    code, out, err = _run(ssh, "command -v ofed_info >/dev/null 2>&1 && ofed_info -s")
    if code == 0 and out:
        return out
    code, out, err = _run(ssh, "if command -v rpm >/dev/null 2>&1; then rpm -qa | grep -Ei 'mlnx|ofed|rdma-core' | head -20; elif command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${Package}=${Version}\\n' 2>/dev/null | grep -Ei 'mlnx|ofed|rdma-core' | head -20; fi")
    return "; ".join(out.splitlines()) if out else "unknown"


def _collect_nvmes(ssh):
    command = r'''root=$(findmnt -n -o SOURCE / 2>/dev/null || true); rootpk=$(lsblk -no PKNAME "$root" 2>/dev/null | head -1)
for d in /sys/class/block/nvme*n1; do [ -e "$d" ] || continue; name=${d##*/}; [ "$name" = "$rootpk" ] && continue
model=$(cat "$d/device/model" 2>/dev/null || echo unknown); fw=$(cat "$d/device/firmware_rev" 2>/dev/null || echo unknown); sectors=$(cat "$d/size" 2>/dev/null || echo 0); numa=$(cat "$d/device/numa_node" 2>/dev/null || echo unknown)
printf '%s|%s|%s|%s|%s\n' "$name" "$model" "$fw" "$sectors" "$numa"; done'''
    code, out, err = _run(ssh, command)
    disks = []
    for line in out.splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        try:
            bytes_value = int(parts[3]) * 512
        except ValueError:
            bytes_value = 0
        pcie = _collect_pcie(ssh, "/sys/class/block/{0}/device".format(parts[0]))
        disks.append({"path": "/dev/" + parts[0], "model": parts[1], "firmware": parts[2], "capacity_bytes": bytes_value, "capacity": _human_bytes(bytes_value), "numa": parts[4], "pcie": pcie})
    return disks


def _counted_summary(items, fields):
    """Compact identical device attributes as specification * count lines."""
    counts = {}
    for item in items:
        values = tuple(str(item.get(field, "unknown")) for field in fields)
        counts[values] = counts.get(values, 0) + 1
    lines = []
    for values, count in sorted(counts.items()):
        lines.append("{0} * {1}".format("; ".join(values), count))
    return "\n".join(lines) or "unknown"


def _nic_summary(nics):
    return _counted_summary(nics, ["model", "speed", "numa", "firmware"])


def _pcie_summary(pcie):
    return "sysfs={0}/{1} ({2}); LnkCap={3}; LnkSta={4} ({5}); {6}".format(
        pcie.get("current", "unknown"), pcie.get("maximum", "unknown"), pcie.get("sysfs_status", "unknown"),
        pcie.get("lnkcap", "unknown"), pcie.get("lnksta", "unknown"), pcie.get("lspci_status", "unknown"),
        pcie.get("status", "unknown"))


def _nic_pcie_summary(nics):
    return _counted_summary([
        {"pcie": _pcie_summary(n["pcie"])} for n in nics
    ], ["pcie"])


def _nvme_summary(disks, field):
    return _counted_summary(disks, [field])


def _nvme_pcie_summary(disks):
    return _counted_summary([
        {"pcie": _pcie_summary(d["pcie"])} for d in disks
    ], ["pcie"])


def _nvme_numa_summary(disks):
    return _counted_summary(disks, ["numa"])


def inventory_detail_rows(items):
    rows = []
    for item in items:
        for nic in item.get("nics", []):
            rows.append(("{0} 网卡".format(item["node"]), [
                item["node"], item["role"], nic.get("interface", "unknown"), nic.get("model", "unknown"),
                nic.get("speed", "unknown"), nic.get("numa", "unknown"), nic.get("firmware", "unknown"),
                _pcie_summary(nic.get("pcie", {})),
            ]))
        for disk in item.get("nvmes", []):
            rows.append(("{0} NVMe".format(item["node"]), [
                item["node"], item["role"], disk.get("path", "unknown"), disk.get("model", "unknown"),
                disk.get("firmware", "unknown"), disk.get("capacity", "unknown"), disk.get("numa", "unknown"),
                _pcie_summary(disk.get("pcie", {})),
            ]))
    return rows


def inventory_rows(items):
    rows = [
        ("节点角色", lambda item: item["role"]),
        ("采集状态/错误", lambda item: "成功" if not item.get("errors") else "; ".join(item["errors"])),
        ("内存条数", lambda item: item["memory"].get("count", "unknown")),
        ("内存规格/频率", lambda item: item["memory"].get("specification", "unknown")),
        ("NUMA 内存分布", lambda item: item["memory"].get("numa_summary", "unknown")),
        ("CPU 型号", lambda item: item["cpu"].get("model", "unknown")),
        ("CPU 插槽数", lambda item: item["cpu"].get("sockets", "unknown")),
        ("超线程", lambda item: item["cpu"].get("hyperthreading", "unknown")),
        ("NUMA 数", lambda item: item["cpu"].get("numa_nodes", "unknown")),
        ("操作系统/版本", lambda item: "{0} {1}".format(item["os"].get("name", "unknown"), item["os"].get("version", "unknown"))),
        ("内核版本", lambda item: item["os"].get("kernel", "unknown")),
        ("OFED/RDMA 驱动版本", lambda item: item.get("rdma_version", "unknown")),
        ("TCP 18000 端口", lambda item: "{0}; {1}".format(item.get("port_18000", {}).get("status", "unknown"), item.get("port_18000", {}).get("listeners", "-"))),
        ("万兆及以上网卡数量", lambda item: str(len(item.get("nics", []))) if item.get("nics") else "0"),
        ("网卡型号/速率/NUMA/固件", lambda item: _nic_summary(item.get("nics", []))),
        ("网卡 PCIe sysfs/LnkCap/LnkSta", lambda item: _nic_pcie_summary(item.get("nics", []))),
        ("NVMe 数据盘数量", lambda item: str(len(item.get("nvmes", []))) if item["role"] in ("storage", "local") else "不适用"),
        ("NVMe 型号", lambda item: _nvme_summary(item.get("nvmes", []), "model") if item["role"] in ("storage", "local") else "不适用"),
        ("NVMe 固件", lambda item: _nvme_summary(item.get("nvmes", []), "firmware") if item["role"] in ("storage", "local") else "不适用"),
        ("NVMe 单盘标称容量", lambda item: _nvme_summary(item.get("nvmes", []), "capacity") if item["role"] in ("storage", "local") else "不适用"),
        ("NVMe NUMA", lambda item: _nvme_numa_summary(item.get("nvmes", [])) if item["role"] in ("storage", "local") else "不适用"),
        ("NVMe PCIe sysfs/LnkCap/LnkSta", lambda item: _nvme_pcie_summary(item.get("nvmes", [])) if item["role"] in ("storage", "local") else "不适用"),
    ]
    return [(label, [getter(item) for item in items]) for label, getter in rows]


def write_csv(items, path):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["检查参数"] + ["{0} ({1})".format(item["node"], item["role"]) for item in items])
        for label, values in inventory_rows(items):
            writer.writerow([label] + values)
    return path
