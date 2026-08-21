from __future__ import print_function

import base64
import json
import os
import re
import socket
import ssl
import traceback

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, URLError, HTTPError

from .common import PASS, WARN, FAIL, ERROR, CATEGORY_ORDER, NodeReport, first_int, shell_quote, values_equal
from .ssh import SSHClient

BIOS_RULES_INTEL = [
    ("BIOS-01", ["WorkloadProfileConfiguration", "PowerProfile", "ProcessorEppProfile"], ["Custom", "Performance"]),
    ("BIOS-02", ["PowerPerformanceTuning", "PwrPerfTuning"], ["BIOS Controls EPB"]),
    ("BIOS-03", ["AltEngPerfBIAS", "PwrEnergyPerf"], ["Performance"]),
    ("BIOS-04", ["HardwarePStates", "ProcessorHWPMEnable"], ["Native Mode"]),
    ("BIOS-05", ["TurboMode", "TurboModeEnTurboMode"], ["Enabled"]),
    ("BIOS-06", ["EnableMonitorMWait", "ProcessorMwait", "MonitorMWait"], ["Enabled"]),
    ("BIOS-07", ["C1E", "ProcessorC1E", "ProcessorC1eEnable"], ["Disabled"]),
    ("BIOS-08", ["CPUC6Report", "C6En", "C6Enable"], ["Disabled"]),
    ("BIOS-09", ["VTdSupport"], ["Disabled"]),
    ("BIOS-10", ["PCIeHotPlug", "HotplugEn", "SurpriseHotPlugSupport"], ["Enabled"]),
    ("BIOS-11", ["CurrentBootMode", "BootType"], ["UEFI", "UEFIBoot", "Uefi", "UEFI only"]),
]
BIOS_RULES_AMD = [
    ("BIOS-AMD-01", ["OC Mode"], ["Normal Operation"]),
    ("BIOS-AMD-02", ["Power Profile Selection", "Power/Performance Profile"], ["High Performance Mode", "CUSTOM"]),
    ("BIOS-AMD-03", ["GlobalCstateControl"], ["Disabled"]),
    ("BIOS-AMD-04", ["IOMMU"], ["Disabled"]),
    ("BIOS-AMD-05", ["BiosHotPlugSupport", "Hot-Plug Support"], ["Enabled"]),
    ("BIOS-AMD-06", ["BootMode", "Boot option filter"], ["UEFI", "Uefi Only"]),
]
BIOS_RULES_KUNPENG = [
    ("BIOS-KP-01", ["CustomPowerPolicy"], ["Performance"]),
    ("BIOS-KP-02", ["CPUPrefetchConfig"], ["Enabled"]),
    ("BIOS-KP-03", ["HotPlug"], ["Enabled"]),
    ("BIOS-KP-04", ["EnableSpcr"], ["Disabled"]),
    ("BIOS-KP-05", ["EnableSMMU"], ["Disabled"]),
]
RDMA_VERSION_OPENEULER = "rdma-core-58mlnx43-1.58415"
RDMA_VERSION_INTEL_X86 = "rdma-core-2410mlnx54-1.2410068.x86_64"


def run_node(node, all_nodes, timeout, only, verbose):
    report = NodeReport(node)
    if verbose:
        print("[{0}] start".format(node.mgmt_ip))
    try:
        with SSHClient(node, timeout) as ssh:
            categories = only if only else CATEGORY_ORDER
            for category in categories:
                print("[{0}] start {1} check".format(node.mgmt_ip, category))
                if category == "bios":
                    check_bios(ssh, report, node, timeout)
                elif category == "os":
                    check_os(ssh, report, all_nodes)
                elif category == "hardware":
                    check_hardware(ssh, report)
                elif category == "network":
                    check_network(ssh, report)
                elif category == "disk":
                    check_disk(ssh, report)
                print("[{0}] finish {1} check".format(node.mgmt_ip, category))
    except Exception as exc:
        report.error = str(exc)
        report.add("NODE-ACCESS", "node", ERROR, str(exc), "SSH root login and command execution", "节点连接或认证失败", "检查管理 IP、root 密码和 SSH 22 端口")
        if verbose:
            traceback.print_exc()
    if verbose:
        print("[{0}] done".format(node.mgmt_ip))
    return report


def fetch_redfish_bios(node, timeout):
    url = "https://{0}/redfish/v1/Systems/1/Bios".format(node.ipmi_ip)
    userpass = "{0}:{1}".format(node.ipmi_user, node.ipmi_password).encode("utf-8")
    token = base64.b64encode(userpass).decode("ascii")
    req = Request(url, headers={"Authorization": "Basic {0}".format(token), "Accept": "application/json"})
    ctx = ssl._create_unverified_context()
    with urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def flatten_dict(obj, prefix=""):
    data = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = str(key)
            if isinstance(value, dict):
                data.update(flatten_dict(value, prefix + name + "."))
            elif isinstance(value, list):
                data[prefix + name] = json.dumps(value, ensure_ascii=False)
            else:
                data[prefix + name] = value
    return data


def find_bios_fields(bios_data, field_names):
    attrs = {}
    if isinstance(bios_data, dict):
        if isinstance(bios_data.get("Attributes"), dict):
            attrs.update(bios_data.get("Attributes"))
        attrs.update(flatten_dict(bios_data))
    matches = []
    seen = set()
    wanted = set(field_names)
    for key, value in attrs.items():
        short_key = key.split(".")[-1]
        if key in wanted or short_key in wanted:
            marker = (short_key, str(value))
            if marker in seen:
                continue
            seen.add(marker)
            matches.append((short_key, value))
    return matches


def detect_platform(ssh, bios_data):
    cmd = "lscpu 2>/dev/null || true; dmidecode -s processor-manufacturer -s processor-version 2>/dev/null || true"
    code, out, err = ssh.run(cmd, timeout=30)
    cpu_text = out.lower()
    if "kunpeng" in cpu_text or "architecture:        aarch64" in cpu_text or "architecture:        arm" in cpu_text:
        return "kunpeng"
    if "authenticamd" in cpu_text or "amd epyc" in cpu_text or "advanced micro devices" in cpu_text:
        return "amd"
    if "genuineintel" in cpu_text or "intel(r)" in cpu_text or "intel corporation" in cpu_text:
        return "intel"

    # Only use explicit Redfish inventory fields as fallback. BIOS attribute names can
    # contain strings from other vendors, so scanning the whole JSON may misclassify AMD.
    inventory_text = collect_platform_inventory_text(bios_data).lower()
    if "kunpeng" in inventory_text or "aarch64" in inventory_text:
        return "kunpeng"
    if "authenticamd" in inventory_text or "amd epyc" in inventory_text or "advanced micro devices" in inventory_text:
        return "amd"
    if "genuineintel" in inventory_text or "intel(r)" in inventory_text or "intel corporation" in inventory_text:
        return "intel"
    return "unknown"


def collect_platform_inventory_text(obj):
    keys = set(["manufacturer", "model", "name", "processorarchitecture", "processormanufacturer", "processormodel"])
    values = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in keys or key_l.endswith("summary"):
                values.append(str(value))
            if isinstance(value, (dict, list)):
                child = collect_platform_inventory_text(value)
                if child:
                    values.append(child)
    elif isinstance(obj, list):
        for item in obj:
            child = collect_platform_inventory_text(item)
            if child:
                values.append(child)
    return "\n".join(values)


def check_bios(ssh, report, node, timeout):
    try:
        bios_data = fetch_redfish_bios(node, timeout)
    except (HTTPError, URLError, ValueError, ssl.SSLError, socket.timeout) as exc:
        report.add("BIOS-ACCESS", "bios", ERROR, type(exc).__name__, "Redfish BIOS JSON", "Redfish 访问失败，请手动确认 BIOS", "手动检查 BIOS，并确认 IPMI IP、账号密码和 Redfish 服务")
        return
    except Exception as exc:
        report.add("BIOS-ACCESS", "bios", ERROR, type(exc).__name__, "Redfish BIOS JSON", "Redfish 访问失败，请手动确认 BIOS", "手动检查 BIOS，并确认 IPMI IP、账号密码和 Redfish 服务")
        return
    report.add("BIOS-ACCESS", "bios", PASS, "Redfish JSON", "Redfish BIOS JSON")
    platform = detect_platform(ssh, bios_data)
    report.facts["platform"] = platform
    if platform == "kunpeng":
        check_bios_kunpeng(report, bios_data)
    elif platform == "amd":
        check_bios_amd(report, bios_data)
    elif platform == "intel":
        check_bios_intel(report, bios_data)
    else:
        report.add("BIOS-PLATFORM", "bios", ERROR, "unknown", "intel/amd/kunpeng", "无法识别 BIOS 平台，避免误按 Intel 规则检查", "确认 lscpu 和 Redfish 返回中的 CPU 厂商信息")


def check_bios_intel(report, bios_data):
    check_bios_rules(report, bios_data, [rule for rule in BIOS_RULES_INTEL if rule[0] != "BIOS-09"])
    check_bios_iommu_prerequisites(report, bios_data)


def check_bios_iommu_prerequisites(report, bios_data):
    attrs = bios_data.get("Attributes", {}) if isinstance(bios_data, dict) else {}
    extended = attrs.get("ExtendedAPIC")
    vtd = attrs.get("VTdSupport")
    actual = "ExtendedAPIC={0}; VTdSupport={1}".format(
        extended if extended is not None else "missing",
        vtd if vtd is not None else "missing",
    )
    ok = values_equal(extended, "Disabled") and values_equal(vtd, "Disabled")
    report.add(
        "BIOS-09", "bios", PASS if ok else FAIL, actual,
        "ExtendedAPIC=Disabled; VTdSupport=Disabled",
        "Extended APIC 或 VT-d 未关闭" if not ok else "",
        "先关闭 Extended APIC，再关闭 VT-d；重启后生效" if not ok else "",
    )


def check_bios_amd(report, bios_data):
    check_bios_rules(report, bios_data, BIOS_RULES_AMD)


def check_bios_kunpeng(report, bios_data):
    check_bios_rules(report, bios_data, BIOS_RULES_KUNPENG)


def check_bios_rules(report, bios_data, rules):
    for check_id, fields, expected_values in rules:
        check_bios_rule(report, bios_data, check_id, fields, expected_values)


def check_bios_rule(report, bios_data, check_id, fields, expected_values):
    matches = find_bios_fields(bios_data, fields)
    expected = "{0}={1}".format("|".join(fields), "/".join(expected_values))
    if not matches:
        report.add(check_id, "bios", ERROR, "字段不存在", expected, "BIOS 字段获取失败，需要人工确认", "检查 Redfish 返回字段或手动确认 BIOS")
        return
    bad = []
    values = []
    for field, value in matches:
        values.append("{0}={1}".format(field, value))
        if not any(values_equal(value, exp) for exp in expected_values):
            bad.append("{0}={1}".format(field, value))
    actual = "; ".join(values)
    if bad:
        report.add(check_id, "bios", FAIL, actual, expected, "BIOS 配置不满足要求或字段值不一致", "按期望值调整 BIOS 配置")
    else:
        report.add(check_id, "bios", PASS, actual, expected)


def check_os(ssh, report, all_nodes):
    code, out, err = ssh.run("df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9'", timeout=20)
    avail = first_int(out)
    if avail is None:
        report.add("OS-01", "os", ERROR, out or err, ">=400G", "根目录可用空间采集失败")
    elif avail >= 400:
        report.add("OS-01", "os", PASS, "{0}G".format(avail), ">=400G")
    else:
        report.add("OS-01", "os", FAIL, "{0}G".format(avail), ">=400G", "根目录可用空间不足", "清理或扩容根目录")
    code, out, err = ssh.run("awk 'tolower($1)==\"hugepagesize:\" {print $2\" \"$3}' /proc/meminfo", timeout=20)
    report.add("OS-02", "os", PASS if out.strip() == "2048 kB" else FAIL, out, "2048 kB", "HugePage 单页大小不满足要求" if out.strip() != "2048 kB" else "")
    code, out, err = ssh.run("grep '^SELINUX=' /etc/selinux/config 2>/dev/null | tail -1", timeout=20)
    actual = out.strip()
    report.add("OS-03", "os", PASS if actual.lower() == "selinux=disabled" else FAIL, actual or err, "SELINUX=disabled", "SELinux 配置文件未关闭" if actual.lower() != "selinux=disabled" else "", "修改 /etc/selinux/config")
    code, out, err = ssh.run("getenforce 2>/dev/null || echo UNKNOWN", timeout=20)
    actual = out.strip()
    report.add("OS-04", "os", PASS if actual.lower() == "disabled" else FAIL, actual, "Disabled", "SELinux 运行状态未关闭" if actual.lower() != "disabled" else "", "关闭 SELinux")
    code, out, err = ssh.run("systemctl is-active firewalld 2>/dev/null || true; systemctl is-enabled firewalld 2>/dev/null || true", timeout=20)
    states = [x.strip().lower() for x in out.splitlines() if x.strip()]
    active = states[0] if len(states) > 0 else "unknown"
    enabled = states[1] if len(states) > 1 else "unknown"
    ok = active in ("inactive", "failed", "unknown") and enabled in ("disabled", "masked", "unknown")
    report.add("OS-05", "os", PASS if ok else FAIL, "active={0}, enabled={1}".format(active, enabled), "firewalld closed", "firewalld 未关闭" if not ok else "", "关闭 firewalld")
    check_ssh_mesh(ssh, report, all_nodes)
    check_locale(ssh, report)
    for idx, pkg in [("OS-08", "tar"), ("OS-09", "rsync"), ("OS-10", "dmidecode"), ("OS-11", "ipcalc")]:
        code, out, err = ssh.run("rpm -q {0}".format(pkg), timeout=20)
        report.add(idx, "os", PASS if code == 0 else FAIL, out or err, "installed", "依赖包未安装" if code != 0 else "", "安装 {0}".format(pkg))
    code, out, err = ssh.run("lscpu | awk -F: 'tolower($1) ~ /thread\\(s\\) per core/ {gsub(/ /,\"\",$2); print $2; exit}'", timeout=20)
    actual = first_int(out)
    report.add("OS-12", "os", PASS if actual == 1 else FAIL, out or err, "1", "CPU 超线程未关闭" if actual != 1 else "", "关闭超线程")
    code, out, err = ssh.run("lscpu | awk -F: 'tolower($1) ~ /numa node\\(s\\)/ {gsub(/ /,\"\",$2); print $2; exit}'", timeout=20)
    actual = first_int(out)
    report.add("OS-13", "os", PASS if actual == 2 else FAIL, out or err, "2", "NUMA 节点数不满足要求" if actual != 2 else "", "确认 BIOS NUMA 配置")
    code, out, err = ssh.run("cat /proc/sys/kernel/numa_balancing 2>/dev/null", timeout=20)
    actual = out.strip()
    report.add("OS-14", "os", PASS if actual == "0" else FAIL, actual or err, "0", "NUMA balancing 未关闭" if actual != "0" else "", "执行 sysctl -w kernel.numa_balancing=0，并持久化到 sysctl 配置")


def check_ssh_mesh(ssh, report, all_nodes):
    failures = []
    for target in all_nodes:
        if target.mgmt_ip == report.node.mgmt_ip:
            continue
        cmd = "ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{0} 'echo ok' 2>/dev/null".format(target.mgmt_ip)
        code, out, err = ssh.run(cmd, timeout=10)
        if code != 0 or out.strip() != "ok":
            failures.append(target.mgmt_ip)
    if failures:
        report.add("OS-06", "os", FAIL, ",".join(failures), "all nodes passwordless", "节点间 root 免密不完整", "为失败目标配置 SSH 免密")
    else:
        report.add("OS-06", "os", PASS, "all ok", "all nodes passwordless")


def check_locale(ssh, report):
    cmd = "for f in /etc/locale.conf /etc/default/locale; do [ -f $f ] && grep '^LANG=' $f; done | tail -1"
    code, out, err = ssh.run(cmd, timeout=20)
    actual = out.strip()
    normalized = actual
    if actual.startswith("LANG="):
        normalized = "LANG=" + actual.split("=", 1)[1].strip().strip('"').strip("'")
    ok = normalized == "LANG=en_US.UTF-8"
    report.add("OS-07", "os", PASS if ok else FAIL, actual or err, "LANG=en_US.UTF-8", "系统 locale 配置不满足要求" if not ok else "", "修改系统 locale 配置")


def list_interfaces(ssh):
    cmd = r'''
for n in /sys/class/net/*; do
  iface=${n##*/}
  [ "$iface" = "lo" ] && continue
  [ -e "$n/device" ] || continue
  [ -d "$n/bonding" ] && continue
  devtype=$(cat "$n/type" 2>/dev/null || echo unknown)
  [ "$devtype" = "1" ] || continue
  speed=$(cat "$n/speed" 2>/dev/null || echo unknown)
  mtu=$(cat "$n/mtu" 2>/dev/null || echo unknown)
  numa=$(cat "$n/device/numa_node" 2>/dev/null || echo unknown)
  state=$(cat "$n/operstate" 2>/dev/null || echo unknown)
  vendor=$(cat "$n/device/vendor" 2>/dev/null || echo unknown)
  device=$(cat "$n/device/device" 2>/dev/null || echo unknown)
  model=$(lspci -s "$(basename "$(readlink -f "$n/device")")" 2>/dev/null | sed 's/^[^ ]* //;s/|//g' || true)
  [ -n "$model" ] || model="$vendor $device"
  printf '%s|%s|%s|%s|%s|%s\n' "$iface" "$speed" "$mtu" "$numa" "$state" "$model"
done
'''
    code, out, err = ssh.run(cmd, timeout=30)
    items = []
    for line in out.splitlines():
        parts = line.split("|", 5)
        if len(parts) == 6:
            items.append({"name": parts[0], "speed": parts[1], "mtu": parts[2], "numa": parts[3], "state": parts[4], "model": parts[5]})
    return items


def parse_speed(value):
    try:
        return int(value)
    except Exception:
        return 0


def high_speed_nics(ssh):
    return [n for n in list_interfaces(ssh) if parse_speed(n.get("speed")) >= 25000]


def rdma_device_status(ssh):
    for cmd in ["ibv_devices", "ibdev2netdev", "rdma"]:
        code, out, err = ssh.run("command -v {0} >/dev/null 2>&1".format(cmd), timeout=10)
        if code != 0:
            return "error"
    code, out, err = ssh.run("rdma link show 2>/dev/null || true", timeout=30)
    if code != 0:
        return "error"
    if out.strip():
        return "present"
    code, out, err = ssh.run("ibdev2netdev 2>/dev/null || true", timeout=30)
    if code != 0:
        return "error"
    if "==>" in out:
        return "present"
    return "absent"


def check_hardware(ssh, report):
    nics = high_speed_nics(ssh)
    check_numa_balance(report, "HW-01", "hardware", [(n["name"], n["numa"]) for n in nics], "高速网卡")
    nvmes = list_nvme_disks(ssh, include_system=False)
    check_numa_balance(report, "HW-02", "hardware", [(d["name"], d["numa"]) for d in nvmes], "NVMe")
    check_nic_pcie_link(ssh, report)
    check_nvme_pcie_link(ssh, report, nvmes)


def check_nic_pcie_link(ssh, report):
    nics = high_speed_nics(ssh)
    if not nics:
        report.add("HW-03", "hardware", WARN, "未发现高速物理网卡", "current PCIe link equals max", "没有可检查设备")
        return
    names = [n["name"] for n in nics]
    links = collect_pcie_links(ssh, "net", names)
    add_pcie_link_result(report, "HW-03", "硬件网卡", links, "hardware")


def check_nvme_pcie_link(ssh, report, nvmes):
    if not nvmes:
        report.add("HW-04", "hardware", WARN, "未发现非系统 NVMe", "current PCIe link equals max", "没有可检查设备")
        return
    names = [d["name"] for d in nvmes]
    links = collect_pcie_links(ssh, "block", names)
    add_pcie_link_result(report, "HW-04", "NVMe", links, "hardware")


def collect_pcie_links(ssh, cls, names):
    devices = " ".join([shell_quote(name) for name in names])
    cmd = r'''for name in {devices}; do
  sys="/sys/class/{cls}/$name/device"
  cur=$(readlink -f "$sys" 2>/dev/null || true)
  speed=""; width=""; max_speed=""; max_width=""; pci=""
  while [ -n "$cur" ] && [ "$cur" != "/" ]; do
    if [ -f "$cur/current_link_speed" ] || [ -f "$cur/current_link_width" ]; then
      pci=$(basename "$cur")
      speed=$(cat "$cur/current_link_speed" 2>/dev/null || true)
      width=$(cat "$cur/current_link_width" 2>/dev/null || true)
      max_speed=$(cat "$cur/max_link_speed" 2>/dev/null || true)
      max_width=$(cat "$cur/max_link_width" 2>/dev/null || true)
      break
    fi
    cur=${{cur%/*}}
  done
  lnkcap=""; lnksta=""
  if [ -n "$pci" ] && command -v lspci >/dev/null 2>&1; then
    lnkcap=$(lspci -s "$pci" -vvv 2>/dev/null | grep -m1 'LnkCap:' || true)
    lnksta=$(lspci -s "$pci" -vvv 2>/dev/null | grep -m1 'LnkSta:' || true)
  fi
  printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$name" "$pci" "$speed" "$width" "$max_speed" "$max_width" "$lnkcap" "$lnksta"
done
'''.format(cls=cls, devices=devices)
    code, out, err = ssh.run(cmd, timeout=30)
    links = []
    for line in out.splitlines():
        parts = line.split("|", 7)
        if len(parts) != 8:
            continue
        cap_speed, cap_width = parse_lspci_link(parts[6])
        sta_speed, sta_width = parse_lspci_link(parts[7])
        links.append({
            "name": parts[0], "pci": parts[1], "speed": parts[2], "width": parts[3],
            "max_speed": parts[4], "max_width": parts[5], "cap_speed": cap_speed,
            "cap_width": cap_width, "sta_speed": sta_speed, "sta_width": sta_width,
        })
    return links


def parse_lspci_link(line):
    speed = re.search(r"Speed\s+([0-9]+(?:\.[0-9]+)?GT/s)", line or "")
    width = re.search(r"Width\s+x([0-9]+)", line or "")
    return (speed.group(1) if speed else "", width.group(1) if width else "")


def add_pcie_link_result(report, check_id, label, links, category="hardware"):
    if not links:
        report.add(check_id, category, ERROR, "无 PCIe 链路数据", "LnkSta equals LnkCap", "PCIe 链路信息采集失败")
        return
    bad = []
    unknown = []
    details = []
    for item in links:
        cap = "{0} x{1}".format(item.get("cap_speed") or "unknown", item.get("cap_width") or "unknown")
        sta = "{0} x{1}".format(item.get("sta_speed") or "unknown", item.get("sta_width") or "unknown")
        details.append("{0}({1}): LnkCap={2}, LnkSta={3}".format(item.get("name"), item.get("pci") or "unknown", cap, sta))
        cap_speed = parse_link_speed(item.get("cap_speed", ""))
        sta_speed = parse_link_speed(item.get("sta_speed", ""))
        cap_width = first_int(item.get("cap_width", ""))
        sta_width = first_int(item.get("sta_width", ""))
        if cap_speed is None or sta_speed is None or cap_width is None or sta_width is None:
            unknown.append(item.get("name"))
            continue
        reasons = []
        if sta_speed < cap_speed:
            reasons.append("speed")
        if sta_width < cap_width:
            reasons.append("width")
        if reasons:
            bad.append("{0}({1})".format(item.get("name"), "/".join(reasons)))
    if bad:
        report.add(check_id, category, FAIL, "; ".join(details), "LnkSta speed and width equal LnkCap", "{0} PCIe 链路降级: {1}".format(label, ",".join(bad)), "检查 PCIe 插槽、线缆、转接卡或 BIOS PCIe 配置")
    elif unknown:
        report.add(check_id, category, ERROR, "; ".join(details), "LnkSta speed and width equal LnkCap", "{0} LnkCap/LnkSta 信息不完整: {1}".format(label, ",".join(unknown)), "安装 pciutils 并检查 lspci -vvv 输出")
    else:
        report.add(check_id, category, PASS, "; ".join(details), "LnkSta speed and width equal LnkCap")


def parse_link_speed(value):
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", value or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def check_numa_balance(report, check_id, category, pairs, label):
    if not pairs:
        report.add(check_id, category, WARN, "未发现{0}".format(label), "NUMA balanced", "没有可检查设备")
        return
    counts = {}
    for name, numa in pairs:
        counts[numa] = counts.get(numa, 0) + 1
    detail = ", ".join(["{0}:{1}".format(name, numa) for name, numa in pairs])
    nums = list(counts.values())
    balanced = True
    if len(nums) == 1 and sum(nums) > 1:
        balanced = False
    elif max(nums) - min(nums) > 1:
        balanced = False
    status = PASS if balanced else FAIL
    report.add(check_id, category, status, detail, "两个 NUMA 节点数量差不超过 1", "" if balanced else "{0} NUMA 分布不均衡".format(label), "" if balanced else "调整为 NUMA 均衡")


def update_remote_hosts(ssh, remote_config, hosts):
    encoded = json.dumps(hosts).encode("utf-8").hex()
    command = "python3 - <<'PY'\nimport binascii, json, re\npath={0!r}\nhosts=json.loads(binascii.unhexlify({1!r}).decode('utf-8'))\ntext=open(path).read()\nblock='HOSTS=(\\n' + ''.join('    '+host+'\\n' for host in hosts) + ')\\n'\ntext=re.sub(r'(?ms)^HOSTS=\\(.*?^\\)\\s*', block, text, count=1)\nopen(path,'w').write(text)\nPY".format(remote_config, encoded)
    code, out, err = ssh.run(command, timeout=30)
    if code != 0:
        raise RuntimeError(err or out)


def run_rdma_cluster_diagnostic(node, all_nodes, timeout, log_root, run_id):
    """Run diag-all once on a copied RDMA tools tree without affecting perf tests."""
    from .network_perf import RDMA_TOOLS_ARCHIVE

    log_dir = os.path.join(log_root, "network_diag")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    archive = os.path.abspath(RDMA_TOOLS_ARCHIVE)
    result = {"status": ERROR, "actual": "", "expected": "所有配置节点通过 rdma-diag", "reason": "", "suggestion": "查看 network_diag/diag-all.log 中的详细输出", "failed_nodes": []}
    remote_base = "/tmp/node_precheck_rdma_diag_{0}".format(run_id)
    try:
        if not os.path.exists(archive):
            raise RuntimeError("缺少 {0}".format(RDMA_TOOLS_ARCHIVE))
        with SSHClient(node, timeout) as ssh:
            ssh.run("rm -rf {0}; mkdir -p {0}".format(shell_quote(remote_base)), timeout=60)
            remote_archive = remote_base + "/rdma-tools-latest.tar.gz"
            ssh.put_file(archive, remote_archive, timeout=300)
            code, out, err = ssh.run("cd {0} && tar -xzf rdma-tools-latest.tar.gz".format(shell_quote(remote_base)), timeout=120)
            if code != 0:
                raise RuntimeError(err or out)
            update_remote_hosts(ssh, remote_base + "/rdma-tools/config", [item.mgmt_ip for item in all_nodes])
            diag_log = remote_base + "/diag-all.log"
            command = "cd {0}/rdma-tools && bash diag-all.sh > {1} 2>&1; echo RC=$?".format(shell_quote(remote_base), shell_quote(diag_log))
            code, out, err = ssh.run(command, timeout=max(timeout, 3600))
            ssh.fetch_file(diag_log, os.path.join(log_dir, "diag-all.log"), timeout=120)
            ssh.fetch_file(remote_base + "/rdma-tools/config", os.path.join(log_dir, "config"), timeout=120)
            output = open(os.path.join(log_dir, "diag-all.log"), "r").read()
            match = re.search(r"rdma-diag FAILED on:\s*(.*)", output)
            if code == 0 and not match:
                result["status"] = PASS
                result["actual"] = "rdma-diag passed on all hosts"
                result["reason"] = ""
            elif match:
                failed_nodes = match.group(1).split()
                result["failed_nodes"] = failed_nodes
                result["status"] = FAIL
                result["actual"] = "rdma-diag FAILED on: {0}".format(" ".join(failed_nodes))
                result["reason"] = "以下节点 rdma-diag 检查失败"
            else:
                result["status"] = ERROR
                result["actual"] = output[-1000:]
                result["reason"] = "diag-all.sh 执行失败且未找到失败节点列表"
    except Exception as exc:
        result["reason"] = "RDMA 集群诊断执行失败: {0}".format(exc)
    finally:
        try:
            with SSHClient(node, timeout) as ssh:
                ssh.run("rm -rf {0}".format(shell_quote(remote_base)), timeout=60)
        except Exception:
            pass
    return result


def check_network(ssh, report):
    nics = high_speed_nics(ssh)
    report_high_speed_nic_inventory(report, nics)
    check_rdma_tools(ssh, report)
    check_rdma_version(ssh, report)
    rdma_status = rdma_device_status(ssh)
    if not nics:
        report.add("NET-03", "network", WARN, "未发现 >=25000Mb/s 网卡", ">=25GE UP", "没有可检查的高速网卡")
    elif rdma_status != "present":
        report.add("NET-03", "network", FAIL, ", ".join(["{0}:{1}".format(n["name"], n["state"]) for n in nics]), ">=25GE RDMA UP", "发现 >=25GE 网卡，但未发现 RDMA 设备", "安装并加载 RDMA 驱动，确认高速网卡为 RDMA 设备")
    else:
        bad = ["{0}:{1}".format(n["name"], n["state"]) for n in nics if n["state"].lower() != "up"]
        report.add("NET-03", "network", PASS if not bad else FAIL, ", ".join(["{0}:{1}".format(n["name"], n["state"]) for n in nics]), "all RDMA NICs UP", "高速网卡未全部 UP" if bad else "", "检查网卡链路")
    check_mtu_by_link_layer(ssh, report, nics)
    check_nic_pcie_network(ssh, report, nics)
    check_rocev2_service(ssh, report)



def check_nic_pcie_network(ssh, report, nics):
    if not nics:
        report.add("NET-07", "network", WARN, "未发现高速物理网卡", "LnkSta speed and width equal LnkCap", "没有可检查设备")
        return
    links = collect_pcie_links(ssh, "net", [n["name"] for n in nics])
    add_pcie_link_result(report, "NET-07", "网络网卡", links, "network")

def report_high_speed_nic_inventory(report, nics):
    if not nics:
        report.add("NET-00", "network", WARN, "未发现万兆网口", "万兆网口数量和型号", "没有可检查的高速网卡")
        return
    lines = ["万兆网口数量={0}".format(len(nics))]
    for nic in nics:
        lines.append("{0}: speed={1}Mb/s model={2}".format(nic.get("name"), nic.get("speed"), nic.get("model") or "unknown"))
    report.add("NET-00", "network", PASS, "; ".join(lines), "万兆网口数量和型号")


def check_rdma_tools(ssh, report):
    failures = []
    outputs = []
    for cmd in ["ibv_devices", "ibdev2netdev", "rdma link"]:
        code, out, err = ssh.run("command -v {0} >/dev/null 2>&1 && {0}".format(cmd), timeout=30)
        if code != 0:
            failures.append(cmd)
            outputs.append("{0}: {1}".format(cmd, err or out))
        else:
            outputs.append("{0}: ok".format(cmd))
    report.add("NET-01", "network", PASS if not failures else FAIL, "; ".join(outputs), "ibv_devices/ibdev2netdev/rdma link ok", "" if not failures else "MLNX/RDMA 工具不可用", "" if not failures else "安装并加载 MLNX/RDMA 驱动")


def check_rdma_version(ssh, report):
    code, os_release, err = ssh.run("cat /etc/os-release 2>/dev/null || true", timeout=20)
    code, arch, err = ssh.run("uname -m", timeout=20)
    arch = arch.strip()
    expected = None
    if "openeuler" in os_release.lower():
        expected = RDMA_VERSION_OPENEULER
        if arch == "aarch64":
            expected = expected + ".aarch64"
    elif arch in ("x86_64", "i386", "i686"):
        expected = RDMA_VERSION_INTEL_X86
    code, out, err = ssh.run("rpm -q rdma-core", timeout=20)
    actual = out.strip() or err.strip()
    if not expected:
        report.add("NET-02", "network", WARN, actual, "openEuler or Intel x86 known version", "当前系统未定义 rdma-core 期望版本")
    else:
        report.add("NET-02", "network", PASS if actual == expected else FAIL, actual, expected, "RDMA 驱动版本不符合要求" if actual != expected else "", "安装指定版本 rdma-core")


def parse_ibstat_link_layers(text):
    ports = []
    ca = ""
    port = ""
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"CA ['\"]?([^'\"]+)['\"]?", line)
        if m:
            ca = m.group(1)
        m = re.match(r"Port\s+(\d+):", line)
        if m:
            port = m.group(1)
        if line.lower().startswith("link layer:"):
            ports.append({"device": ca, "port": port, "layer": line.split(":", 1)[1].strip()})
    return ports


def parse_ibdev2netdev(text):
    names = []
    for line in text.splitlines():
        m = re.search(r"==>\s+(\S+)", line)
        if m:
            names.append(m.group(1))
    return names


def check_mtu_by_link_layer(ssh, report, high_nics):
    rdma_status = rdma_device_status(ssh)
    if rdma_status != "present":
        report.add("NET-04", "network", FAIL, "未发现 RDMA 设备", "ibstat Link layer", "没有 RDMA 设备")
        return
    code, ibstat_out, err = ssh.run("ibstat 2>/dev/null", timeout=30)
    if code != 0 or not ibstat_out.strip():
        report.add("NET-04", "network", ERROR, err or ibstat_out, "ibstat Link layer", "无法获取 RDMA Link layer")
        return
    ports = parse_ibstat_link_layers(ibstat_out)
    if not ports:
        report.add("NET-04", "network", ERROR, ibstat_out, "Link layer", "ibstat 未返回 Link layer")
        return
    layers = set([p["layer"] for p in ports])
    if len(layers) > 1:
        detail = ", ".join(["{0}/port{1}:{2}".format(p["device"], p["port"], p["layer"]) for p in ports])
        report.add("NET-04", "network", FAIL, detail, "all same Link layer", "RDMA 端口 Link layer 不一致", "人工判断是否调整为一致")
        return
    layer = list(layers)[0]
    expected_mtu = "4092" if layer == "InfiniBand" else "4200" if layer == "Ethernet" else "unknown"
    code, map_out, err = ssh.run("ibdev2netdev 2>/dev/null", timeout=30)
    target_names = parse_ibdev2netdev(map_out) if code == 0 else []
    if not target_names:
        target_names = [n["name"] for n in high_nics]
    bad = []
    details = []
    for iface in target_names:
        code, mtu, err = ssh.run("cat /sys/class/net/{0}/mtu 2>/dev/null || echo unknown".format(iface), timeout=10)
        mtu = mtu.strip()
        details.append("{0}:{1}".format(iface, mtu))
        if expected_mtu == "unknown" or mtu != expected_mtu:
            bad.append("{0}:{1}".format(iface, mtu))
    report.add("NET-04", "network", PASS if not bad else FAIL, "layer={0}; {1}".format(layer, ", ".join(details)), "MTU {0}".format(expected_mtu), "MTU 不满足组网要求" if bad else "", "按 {0} 组网调整 MTU".format(layer))


def check_rocev2_service(ssh, report):
    code, out, err = ssh.run("systemctl status enable-rocev2.service 2>/dev/null | grep -i 'Loaded:' || true", timeout=20)
    loaded = "loaded" in out.lower()
    report.add("NET-05", "network", PASS if loaded else FAIL, out or err, "Loaded: loaded", "enable-rocev2.service 未处于 loaded 状态" if not loaded else "", "检查流控配置服务")


def list_nvme_disks(ssh, include_system=False):
    cmd = r'''
sys_src=$(findmnt -n -o SOURCE / 2>/dev/null || true)
sys_pk=$(lsblk -no PKNAME "$sys_src" 2>/dev/null | head -1)
[ -z "$sys_pk" ] && sys_pk=$(basename "$sys_src" | sed 's/p[0-9]*$//')
for d in /sys/class/block/nvme*n1; do
  [ -e "$d" ] || continue
  name=${d##*/}
  numa=$(cat "$d/device/numa_node" 2>/dev/null || echo unknown)
  is_sys=0
  [ "$name" = "$sys_pk" ] && is_sys=1
  printf '%s|%s|%s\n' "$name" "$numa" "$is_sys"
done
'''
    code, out, err = ssh.run(cmd, timeout=30)
    disks = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            item = {"name": parts[0], "path": "/dev/" + parts[0], "numa": parts[1], "is_system": parts[2] == "1"}
            if include_system or not item["is_system"]:
                disks.append(item)
    return disks


def check_disk(ssh, report):
    data_disks = list_nvme_disks(ssh, include_system=False)
    if not data_disks:
        report.add("DISK-01", "disk", WARN, "未发现非系统盘 NVMe", "data NVMe disks", "没有可检查的数据盘")
        report.add("DISK-02", "disk", WARN, "未发现非系统盘 NVMe", "no data signature", "没有可检查的数据盘")
        report.add("DISK-03", "disk", WARN, "未发现非系统盘 NVMe", ">=80% remaining", "没有可检查的数据盘")
        return
    report.add("DISK-01", "disk", PASS, ", ".join([d["path"] for d in data_disks]), "non-system NVMe disks")
    check_disk_signatures(ssh, report, data_disks)
    check_nvme_lifetime(ssh, report, data_disks)


def check_disk_signatures(ssh, report, disks):
    bad = []
    details = []
    for disk in disks:
        path = disk["path"]
        cmd = r'''
dev="{0}"
issue=""
parts=$(lsblk -nr "$dev" 2>/dev/null | awk 'NR>1 {{print $1}}' | tr '\n' ',')
[ -n "$parts" ] && issue="$issue partitions=$parts"
mounts=$(lsblk -nr -o MOUNTPOINT "$dev" 2>/dev/null | grep -v '^$' | tr '\n' ',')
[ -n "$mounts" ] && issue="$issue mounts=$mounts"
wipes=$(wipefs -n "$dev" 2>/dev/null | awk 'NR>2 {{print $0}}' | tr '\n' ';')
[ -n "$wipes" ] && issue="$issue signatures=$wipes"
blk=$(blkid "$dev" 2>/dev/null || true)
[ -n "$blk" ] && issue="$issue blkid=$blk"
pv=$(pvs --noheadings "$dev" 2>/dev/null || true)
[ -n "$pv" ] && issue="$issue lvm=$pv"
md=$(mdadm --examine "$dev" 2>/dev/null | head -3 || true)
[ -n "$md" ] && issue="$issue raid=$md"
echo "$issue"
'''.format(path)
        code, out, err = ssh.run(cmd, timeout=30)
        if out.strip():
            bad.append("{0} 盘存在数据分区，请格式化".format(path))
            details.append("{0}: {1}".format(path, out.strip()))
    if bad:
        report.add("DISK-02", "disk", FAIL, "; ".join(details), "no data signature", "; ".join(bad), "格式化对应 NVMe 盘")
    else:
        report.add("DISK-02", "disk", PASS, "无数据痕迹", "no data signature")


def check_nvme_lifetime(ssh, report, disks):
    failures = []
    errors = []
    details = []
    for disk in disks:
        path = disk["path"]
        code, out, err = ssh.run("nvme smart-log {0} 2>/dev/null | grep -i 'percentage_used' || true".format(path), timeout=30)
        pct = first_int(out)
        if pct is None:
            errors.append("{0}: {1}".format(path, err or out or "percentage_used missing"))
            continue
        remaining = 100 - pct
        details.append("{0}: used={1}%, remaining={2}%".format(path, pct, remaining))
        if remaining < 80:
            failures.append("{0}: remaining={1}%".format(path, remaining))
    if errors:
        report.add("DISK-03", "disk", ERROR, "; ".join(errors), ">=80% remaining", "NVMe 寿命信息获取失败", "检查 nvme-cli 和设备 SMART 信息")
    elif failures:
        report.add("DISK-03", "disk", FAIL, "; ".join(details), ">=80% remaining", "硬盘寿命低于 80%: " + "; ".join(failures), "更换寿命不足的 NVMe 盘")
    else:
        report.add("DISK-03", "disk", PASS, "; ".join(details), ">=80% remaining")
