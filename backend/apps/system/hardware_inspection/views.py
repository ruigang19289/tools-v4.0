"""Read-only hardware inspection APIs."""
import base64
import concurrent.futures
import json
import re

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from backend.utils.ssh_auth import connect_ssh, parse_ssh_auth


REMOTE_SCRIPT = r'''#!/bin/bash
export LC_ALL=C LANG=C
set -o pipefail
field() { lscpu 2>/dev/null | awk -F: -v key="$1" '{name=$1; gsub(/^[ \t]+|[ \t]+$/, "", name); if (name == key) {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}}'; }
value() { cat "$1" 2>/dev/null || printf '%s' "${2:-unavailable}"; }
pcie_gen() {
  speed=$(printf '%s' "$1" | sed 's/[[:space:]]*(.*//; s/[[:space:]]//g')
  case "$speed" in 2.5GT/s) echo 'PCIe 1.0';; 5GT/s) echo 'PCIe 2.0';; 8GT/s) echo 'PCIe 3.0';; 16GT/s) echo 'PCIe 4.0';; 32GT/s) echo 'PCIe 5.0';; 64GT/s) echo 'PCIe 6.0';; *) echo '?';; esac
}
pcie_bandwidth() {
  awk -v speed="$1" -v width="$2" 'BEGIN {
    gsub(/[^0-9.]/, "", speed); gsub(/[^0-9]/, "", width)
    if (speed == "" || width == "") { print "?"; exit }
    s = speed + 0; w = width + 0
    if (s == 2.5) eff = 2.0; else if (s == 5) eff = 4.0; else if (s == 8) eff = 7.877; else if (s == 16) eff = 15.754; else if (s == 32) eff = 31.508; else if (s == 64) eff = 63.015; else eff = s * .985
    printf "%.2f GB/s", eff * w / 8
  }'
}
command -v lspci >/dev/null 2>&1 && HAS_LSPCI=1 || HAS_LSPCI=0
printf '== OVERVIEW ==\n'
printf 'HOSTNAME: %s\n' "$(hostname)"
printf 'OS: %s\n' "$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
printf 'KERNEL: %s\n' "$(uname -r)"
cpu_model=$(field 'Model name'); [ -n "$cpu_model" ] || cpu_model=$(field 'BIOS Model name')
printf 'CPU: %s\n' "${cpu_model:-unknown}"
printf 'ARCH: %s\n' "$(uname -m)"
cpu_total=$(field 'CPU(s)'); [ -n "$cpu_total" ] || cpu_total=$(nproc 2>/dev/null || echo '?')
sockets=$(field 'Socket(s)'); [ -n "$sockets" ] || sockets=$(awk -F: '/^physical id/{gsub(/ /,"",$2); print $2}' /proc/cpuinfo | sort -u | wc -l)
[ "${sockets:-0}" -gt 0 ] 2>/dev/null || sockets='?'
cores=$(field 'Core(s) per socket')
[ -n "$cores" ] || cores=$(awk -F: '/^cpu cores/{gsub(/ /,"",$2); print $2; exit}' /proc/cpuinfo)
threads=$(field 'Thread(s) per core')
if [ -z "$threads" ] && [ "${cpu_total:-?}" != '?' ] && [ "${sockets:-?}" != '?' ] && [ "${cores:-?}" != '?' ]; then
  threads=$(awk -v total="$cpu_total" -v socket="$sockets" -v core="$cores" 'BEGIN { if (socket*core > 0 && total%(socket*core) == 0) print total/(socket*core) }')
fi
[ -n "$cores" ] || cores='?'; [ -n "$threads" ] || threads='?'
printf 'CPU_TOPOLOGY: CPUs=%s sockets=%s cores_per_socket=%s threads_per_core=%s\n' "$cpu_total" "$sockets" "$cores" "$threads"
numa=$(field 'NUMA node(s)'); [ -n "$numa" ] || numa=$(find /sys/devices/system/node -maxdepth 1 -type d -name 'node[0-9]*' 2>/dev/null | wc -l)
printf 'NUMA_NODES: %s\n' "${numa:-?}"
printf 'GOVERNOR: %s\n' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
printf 'IRQBALANCE: %s\n' "$(systemctl is-active irqbalance 2>/dev/null || echo unavailable)"
printf 'TUNED: %s\n' "$(tuned-adm active 2>/dev/null | sed 's/.*: //' || echo unavailable)"
printf 'IOMMU_GROUPS: %s\n' "$(find /sys/kernel/iommu_groups -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
mem_total=$(awk '/^MemTotal:/{printf "%.0f GiB", $2/1024/1024; exit}' /proc/meminfo)
printf 'MEMORY_TOTAL: %s\n' "${mem_total:-?}"
numa_memory=''
for node in /sys/devices/system/node/node[0-9]*; do
  [ -d "$node" ] || continue
  name=$(basename "$node")
  size=$(awk '/MemTotal:/{printf "%.0fGiB", $4/1024/1024; exit}' "$node/meminfo" 2>/dev/null)
  free=$(awk '/MemFree:/{printf "%.0fGiB", $4/1024/1024; exit}' "$node/meminfo" 2>/dev/null)
  numa_memory="${numa_memory}${numa_memory:+,}${name}=${size:-?}/${free:-?}"
done
printf 'NUMA_MEMORY: %s\n' "${numa_memory:-未采集}"
printf '\n== CPU POWER / NUMA DETAILS ==\n'
printf '%-20s %s\n' 'CPU governor' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
printf '%-20s %s\n' 'CPUfreq driver' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver)"
printf '%-20s %s kHz\n' 'Min frequency' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq)"
printf '%-20s %s kHz\n' 'Max frequency' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq)"
printf '%-20s %s kHz\n' 'Current frequency' "$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq)"
printf '%-20s %s\n' 'CPU boost' "$(value /sys/devices/system/cpu/cpufreq/boost)"
printf '%-20s %s\n' 'Intel pstate' "$(value /sys/devices/system/cpu/intel_pstate/status)"
printf '%-20s %s (0=enabled)\n' 'Intel no_turbo' "$(value /sys/devices/system/cpu/intel_pstate/no_turbo)"
printf '%-20s %s\n' 'Energy perf bias' "$(value /sys/devices/system/cpu/cpu0/power/energy_perf_bias)"
printf '%-20s %s\n' 'irqbalance enabled' "$(systemctl is-enabled irqbalance 2>/dev/null || echo unavailable)"
printf '%-20s %s\n' 'irqbalance active' "$(systemctl is-active irqbalance 2>/dev/null || echo unavailable)"
printf '%-20s %s\n' 'tuned profile' "$(tuned-adm active 2>/dev/null | sed 's/.*: //' || echo unavailable)"
printf '%-20s %s\n' 'Hyperthreading' "$( [ "$threads" = 2 ] && echo enabled || echo disabled_or_unsupported )"
if command -v numactl >/dev/null 2>&1; then
  echo 'NUMA hardware:'; numactl --hardware 2>/dev/null || true
else
  echo '[LIMIT] numactl 未安装，NUMA 内存仅通过 sysfs 采集'
fi

printf '\n== MEMORY DIMMS ==\n'
printf '%-18s %-12s %-10s %-10s %-10s %-16s\n' LOCATION SIZE SPEED TYPE MANUFACTURER PART_NUMBER
dimm_found=0
if command -v dmidecode >/dev/null 2>&1; then
  while IFS='|' read -r locator size speed type manufacturer part; do
    [ -n "$locator" ] || continue
    dimm_found=1
    printf '%-18s %-12s %-10s %-10s %-10s %-16s\n' "$locator" "$size" "$speed" "$type" "$manufacturer" "$part"
  done < <(dmidecode -t memory 2>/dev/null | awk -F: '
    /^	Locator:/{loc=$2; gsub(/^[ 	]+/,"",loc)}
    /^	Size:/{size=$2; gsub(/^[ 	]+/,"",size)}
    /^	Type:/{type=$2; gsub(/^[ 	]+/,"",type)}
    /^	Speed:/{speed=$2; gsub(/^[ 	]+/,"",speed)}
    /^	Manufacturer:/{mfg=$2; gsub(/^[ 	]+/,"",mfg)}
    /^	Part Number:/{part=$2; gsub(/^[ 	]+|[ 	]+$/, "", part)}
    /^	Memory Device$/{if (loc && size && size !~ /No Module|Not Installed/) print loc "|" size "|" speed "|" type "|" mfg "|" part; loc=size=speed=type=mfg=part=""}
    END{if (loc && size && size !~ /No Module|Not Installed/) print loc "|" size "|" speed "|" type "|" mfg "|" part}' )
else
  echo '[LIMIT] dmidecode 未安装，无法核对内存条物理位置'
fi
[ "$dimm_found" -eq 1 ] || echo '(未发现已安装内存条或无法读取 DMI)'
nvme_compare=''
printf '\n== NVME / PCIE ==\n'
printf '%-8s %-14s %-12s %-12s %-7s %-7s %-13s %-8s %-8s\n' DEVICE BDF CAP ACTUAL CAP_GEN ACT_GEN ACTUAL_BW STATUS USED TEMP
found=0
for dev in /sys/class/nvme/nvme*; do
  [ -e "$dev" ] || continue; found=1; ctrl=$(basename "$dev"); bdf=$(basename "$(readlink -f "$dev/device" 2>/dev/null)")
  cap='?'; actual='?'; status='LIMIT'; cap_speed='?'; actual_speed='?'; actual_width='?'
  if [ "$HAS_LSPCI" -eq 1 ]; then
    cap=$(lspci -vv -s "$bdf" 2>/dev/null | awk '/LnkCap:/{s="?";w="?"; if(match($0,/Speed [^,]+/))s=substr($0,RSTART+6,RLENGTH-6); if(match($0,/Width x[0-9]+/))w=substr($0,RSTART+6,RLENGTH-6); print s"/"w; exit}')
    actual=$(lspci -vv -s "$bdf" 2>/dev/null | awk '/LnkSta:/{s="?";w="?"; if(match($0,/Speed [^,]+/))s=substr($0,RSTART+6,RLENGTH-6); if(match($0,/Width x[0-9]+/))w=substr($0,RSTART+6,RLENGTH-6); print s"/"w; exit}')
    cap_speed=${cap%/*}; actual_speed=${actual%/*}; actual_width=${actual#*/}
    cap_speed=$(printf '%s' "$cap_speed" | sed 's/[[:space:]]*(.*//; s/[[:space:]]//g')
    actual_speed=$(printf '%s' "$actual_speed" | sed 's/[[:space:]]*(.*//; s/[[:space:]]//g')
    actual_width=$(printf '%s' "$actual_width" | sed 's/[[:space:]]//g')
    [ "$cap_speed/$actual_width" = "$actual_speed/$actual_width" ] && status=OK || status=WARN
  fi
  used='?'; temp='?'
  if command -v nvme >/dev/null 2>&1; then
    smart=$(nvme smart-log "/dev/$ctrl" 2>/dev/null || true); used=$(echo "$smart" | awk -F: '/percentage_used/{gsub(/[ %]/,"",$2); print $2; exit}'); temp=$(echo "$smart" | awk -F: '/^temperature/{match($2,/[0-9]+/); print substr($2,RSTART,RLENGTH); exit}')
  fi
  cap_gen=$(pcie_gen "$cap_speed"); actual_gen=$(pcie_gen "$actual_speed"); actual_bw=$(pcie_bandwidth "$actual_speed" "$actual_width")
  printf '%-8s %-14s %-12s %-12s %-7s %-7s %-13s %-8s %-8s\n' "$ctrl" "$bdf" "${cap:-?}" "${actual:-?}" "$cap_gen" "$actual_gen" "$actual_bw" "$status" "${used:-?}" "${temp:-?}"
  nvme_compare="${nvme_compare}COMPARE_NVME|${ctrl}|${bdf}|${cap}|${actual}|${cap_gen}|${actual_gen}|${actual_bw}|${status}\n"
done
[ "$found" -eq 1 ] || echo '(未发现 NVMe 控制器)'
printf '\n== NVME SMART / FIRMWARE ==\n'
printf '%-8s %-7s %-7s %-12s %-12s %-12s %-12s\n' DEVICE USED TEMP MEDIA_ERR LOG_ERR CRIT_WARN FIRMWARE
for dev in /sys/class/nvme/nvme*; do
  [ -e "$dev" ] || continue; ctrl=$(basename "$dev"); smart=''; id=''
  if command -v nvme >/dev/null 2>&1; then smart=$(nvme smart-log "/dev/$ctrl" 2>/dev/null || true); id=$(nvme id-ctrl "/dev/$ctrl" 2>/dev/null || true); fi
  used=$(echo "$smart" | awk -F: '/percentage_used/{gsub(/[ %]/,"",$2);print $2;exit}')
  temp=$(echo "$smart" | awk -F: '/^temperature/{match($2,/[0-9]+/);print substr($2,RSTART,RLENGTH);exit}')
  media=$(echo "$smart" | awk -F: '/media_and_data_integrity_errors/{gsub(/[ ,]/,"",$2);print $2;exit}')
  logs=$(echo "$smart" | awk -F: '/num_err_log_entries/{gsub(/[ ,]/,"",$2);print $2;exit}')
  crit=$(echo "$smart" | awk -F: '/critical_warning/{gsub(/[ ]/,"",$2);print $2;exit}')
  fw=$(echo "$id" | awk -F: '/^fr[[:space:]]*:/{gsub(/^[ \t]+/,"",$2);print $2;exit}')
  printf '%-8s %-7s %-7s %-12s %-12s %-12s %-12s\n' "$ctrl" "${used:-?}" "${temp:-?}C" "${media:-?}" "${logs:-?}" "${crit:-?}" "${fw:-?}"
done
rdma_ifaces=''
if command -v rdma >/dev/null 2>&1; then
  # rdma link is the authoritative OS-side mapping from RDMA device to netdev.
  rdma_ifaces=$(rdma link show 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="netdev"&&(i+1)<=NF)print $(i+1)}' | sort -u | xargs || true)
fi
is_rdma_iface() { case " $rdma_ifaces " in *" $1 "*) return 0;; *) return 1;; esac; }
nic_compare=''
printf '\n== NETWORK ==\n' 
printf '%-12s %-14s %-8s %-8s %-6s %-10s %-14s %-10s %-12s %-12s %-10s %-10s\n' IFACE BDF ROLE MTU STATE SPEED PCIE DRIVER DRIVER_VER FIRMWARE RX_ERR TX_ERR
for sysdev in /sys/class/net/*; do
  [ -L "$sysdev/device" ] || continue; iface=$(basename "$sysdev"); bdf=$(basename "$(readlink -f "$sysdev/device" 2>/dev/null)")
  pcie='?'; [ "$HAS_LSPCI" -eq 1 ] && pcie=$(lspci -vv -s "$bdf" 2>/dev/null | awk '/LnkSta:/{s="?";w="?"; if(match($0,/Speed [^,]+/))s=substr($0,RSTART+6,RLENGTH-6); if(match($0,/Width x[0-9]+/))w=substr($0,RSTART+6,RLENGTH-6); print s"/"w; exit}')
  pcie_speed=${pcie%/*}; pcie_width=${pcie#*/}; pcie_bw=$(pcie_bandwidth "$pcie_speed" "$pcie_width")
  mtu=$(value "$sysdev/mtu" '?')
  if is_rdma_iface "$iface"; then role=RDMA; else role=ETHERNET; fi
  speed=$(ethtool "$iface" 2>/dev/null | awk -F: '/Speed:/{gsub(/[ \t]/,"",$2); print $2; exit}')
  info=$(ethtool -i "$iface" 2>/dev/null || true)
  driver=$(echo "$info" | awk -F: '/^driver:/{gsub(/^[ \t]+/,"",$2);print $2;exit}')
  driver_ver=$(echo "$info" | awk -F: '/^version:/{gsub(/^[ \t]+/,"",$2);print $2;exit}')
  firmware=$(echo "$info" | awk -F: '/^firmware-version:/{sub(/^[^:]*:[ \t]*/,"");print;exit}')
  state=$(value "$sysdev/operstate" '?'); rxerr=$(value "$sysdev/statistics/rx_errors" 0); txerr=$(value "$sysdev/statistics/tx_errors" 0)
  printf '%-12s %-14s %-8s %-8s %-6s %-10s %-14s %-10s %-12s %-12s %-10s %-10s\n' "$iface" "$bdf" "$role" "$mtu" "$state" "${speed:-?}" "${pcie:-?}" "${driver:-?}" "${driver_ver:-?}" "${firmware:-?}" "$rxerr" "$txerr"
  nic_compare="${nic_compare}COMPARE_NIC|${iface}|${bdf}|${role}|${mtu}|${state}|${speed:-?}|${pcie:-?}|${pcie_bw:-?}|${driver:-?}|${driver_ver:-?}|${firmware:-?}|${rxerr}|${txerr}\n"
done
printf '\n== COMPARISON DATA ==\n'; printf '%b' "$nvme_compare$nic_compare"
printf '\n== WARNINGS ==\n'
gov=$(value /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor); [ "$gov" = performance ] || echo "[WARN] CPU governor=$gov"
irq=$(systemctl is-active irqbalance 2>/dev/null || true); [ "$irq" = active ] || echo "[WARN] irqbalance=$irq"
[ "$HAS_LSPCI" -eq 1 ] || echo '[LIMIT] lspci 未安装，无法检查 PCIe 链路'
command -v nvme >/dev/null 2>&1 || echo '[LIMIT] nvme-cli 未安装，无法读取 NVMe SMART'
aer=0; for f in /sys/bus/pci/devices/*/aer_dev_correctable /sys/bus/pci/devices/*/aer_dev_fatal /sys/bus/pci/devices/*/aer_dev_nonfatal; do [ -r "$f" ] || continue; n=$(awk '$1 ~ /^TOTAL_ERR_/{s+=$2} END{print s+0}' "$f"); [ "$n" -eq 0 ] || aer=$((aer+1)); done; [ "$aer" -eq 0 ] || echo "[WARN] PCIe AER 非零计数文件=$aer"
printf '\n== PCIE AER / KERNEL EVENTS ==\n'
for f in /sys/bus/pci/devices/*/aer_dev_correctable /sys/bus/pci/devices/*/aer_dev_fatal /sys/bus/pci/devices/*/aer_dev_nonfatal; do
  [ -r "$f" ] || continue; count=$(awk '$1 ~ /^TOTAL_ERR_/{sum += $2} END{print sum+0}' "$f")
  [ "$count" -eq 0 ] || printf 'AER %s total=%s\n' "$(basename "$(dirname "$f")")/$(basename "$f")" "$count"
done
dmesg -T 2>/dev/null | grep -iE 'AER:.*(fatal|non-fatal|corrected)|nvme.*(timeout|reset|I/O error)|link.*(down|downgrad|retrain)|tx timeout' | tail -30 | sed 's/^/[KERNEL] /' || true
printf '\n== PCIe THROUGHPUT NOTES ==\n'
echo '说明：LnkSta 是当前 PCIe 链路；理论带宽需结合代际和 lane 宽度评估，非 fio/iperf3 实测值。'

'''


def run_command(ssh, command):
    _, stdout, stderr = ssh.exec_command(command, timeout=45)
    output = stdout.read().decode("utf-8", errors="replace")
    error = stderr.read().decode("utf-8", errors="replace")
    return output, error, stdout.channel.recv_exit_status()


def inspect_host(host, auth, port):
    try:
        ssh = connect_ssh(host, auth, port=port, timeout=12)
        try:
            encoded = base64.b64encode(REMOTE_SCRIPT.encode()).decode()
            command = "echo %s | base64 -d | bash" % encoded
            report, error, code = run_command(ssh, command)
        finally:
            ssh.close()
        if code:
            return {"host": host, "status": "error", "error": error or report}
        warnings = [line for line in report.splitlines() if line.startswith("[WARN]")]
        return {"host": host, "status": "warning" if warnings else "success", "warnings": warnings, "report": report}
    except Exception as exc:
        return {"host": host, "status": "error", "error": str(exc)}


def parse_fingerprint(report):
    """Extract stable overview fields and structured PCIe/NIC records."""
    values = {"NVME_ITEMS": {}, "NIC_ITEMS": {}}
    for line in report.splitlines():
        if line.startswith("COMPARE_NVME|"):
            parts = line.split("|")
            if len(parts) == 9:
                _, device, bdf, cap, actual, cap_gen, actual_gen, bandwidth, status = parts
                cap_display = f"{cap}（{cap_gen}）" if cap_gen != "?" else cap
                actual_display = f"{actual}（{actual_gen}）" if actual_gen != "?" else actual
                values["NVME_ITEMS"][device] = {"bdf": bdf, "cap": cap_display, "actual": actual_display, "cap_gen": cap_gen, "actual_gen": actual_gen, "bandwidth": bandwidth, "status": status}
            continue
        if line.startswith("COMPARE_NIC|"):
            parts = line.split("|")
            if len(parts) == 14:
                _, iface, bdf, role, mtu, state, speed, pcie, pcie_bw, driver, version, firmware, rxerr, txerr = parts
                pcie_speed_match = re.search(r"[0-9.]+GT/s", pcie)
                pcie_speed = pcie_speed_match.group(0) if pcie_speed_match else ""
                pcie_gen_map = {"2.5GT/s": "PCIe 1.0", "5GT/s": "PCIe 2.0", "8GT/s": "PCIe 3.0", "16GT/s": "PCIe 4.0", "32GT/s": "PCIe 5.0", "64GT/s": "PCIe 6.0"}
                pcie_display = f"{pcie}（{pcie_gen_map[pcie_speed]}）" if pcie_speed in pcie_gen_map else pcie
                values["NIC_ITEMS"][iface] = {"bdf": bdf, "role": role, "mtu": mtu, "state": state, "speed": speed, "pcie": pcie_display, "pcie_bw": pcie_bw, "driver": driver, "version": version, "firmware": firmware, "rxerr": rxerr, "txerr": txerr}
            continue
        if ": " not in line or line.startswith("=="):
            continue
        key, value = line.split(": ", 1)
        if key in {"OS", "KERNEL", "CPU", "ARCH", "CPU_TOPOLOGY", "NUMA_NODES", "GOVERNOR", "IRQBALANCE", "TUNED", "IOMMU_GROUPS", "MEMORY_TOTAL", "NUMA_MEMORY"}:
            values[key] = value.strip()
    values["NVME_COUNT"] = str(len(values["NVME_ITEMS"]))
    values["NIC_COUNT"] = str(len(values["NIC_ITEMS"]))
    return values


def device_comparison(fingerprints, item_key, label, fields):
    names = sorted({name for data in fingerprints.values() for name in data[item_key]})
    output = []
    for name in names:
        values = {}
        serialized = []
        present = {host: data[item_key].get(name) for host, data in fingerprints.items()}
        differing_fields = []
        for key, title in fields:
            field_values = [item.get(key, "?") if item else "未发现" for item in present.values()]
            if len(set(field_values)) > 1:
                differing_fields.append(title)
        for host, item in present.items():
            if not item:
                values[host] = "未发现"
                serialized.append("未发现")
            else:
                values[host] = "\n".join(f"{title}: {item.get(key, '?')}" for key, title in fields)
                serialized.append(json.dumps(item, sort_keys=True, ensure_ascii=False))
        output.append({"name": f"{label} {name}", "values": values, "same": len(set(serialized)) == 1, "differing_fields": differing_fields})
    return output


def comparison(results):
    overview = []
    successful = [item for item in results if item.get("status") != "error"]
    for item in results:
        if item.get("status") == "error":
            overview.append(f"{item['host']}: 连接或采集失败 - {item['error']}")
        elif item.get("warnings"):
            overview.append(f"{item['host']}: {len(item['warnings'])} 项告警")
        else:
            overview.append(f"{item['host']}: 未发现明显告警")
    fields = [
        ("OS", "操作系统"), ("KERNEL", "内核"), ("CPU", "CPU 型号"),
        ("ARCH", "CPU 架构"), ("CPU_TOPOLOGY", "CPU 拓扑"), ("NUMA_NODES", "NUMA 节点"),
        ("GOVERNOR", "CPU governor"), ("IRQBALANCE", "irqbalance"),
        ("TUNED", "tuned 策略"), ("IOMMU_GROUPS", "IOMMU groups"),
        ("MEMORY_TOTAL", "内存总量"), ("NUMA_MEMORY", "NUMA 内存分布（总量/空闲）"),
        ("NVME_COUNT", "NVMe 数量"),
        ("NIC_COUNT", "物理网卡数量"),
    ]
    fingerprints = {item["host"]: parse_fingerprint(item["report"]) for item in successful}
    same, different = [], []
    for key, label in fields:
        values = {host: data.get(key, "未采集") for host, data in fingerprints.items()}
        if key == "NUMA_MEMORY":
            numa_nodes = sorted({
                segment.split("=", 1)[0]
                for value in values.values()
                for segment in value.split(",")
                if "=" in segment
            })
            for node in numa_nodes:
                node_values = {}
                for host, value in values.items():
                    mapping = dict(segment.split("=", 1) for segment in value.split(",") if "=" in segment)
                    node_values[host] = mapping.get(node, "未采集")
                if len(set(node_values.values())) == 1:
                    same.append({"name": f"NUMA 内存 {node}（总量/空闲）", "value": next(iter(node_values.values()))})
                else:
                    different.append({"name": f"NUMA 内存 {node}（总量/空闲）", "values": node_values})
            continue
        unique = set(values.values())
        if len(unique) == 1 and values:
            same.append({"name": label, "value": next(iter(unique))})
        elif values:
            different.append({"name": label, "values": values})
    nvme = device_comparison(fingerprints, "NVME_ITEMS", "NVMe", [("bdf", "BDF"), ("cap", "能力"), ("actual", "实际"), ("bandwidth", "实际带宽"), ("status", "状态")])
    nics = device_comparison(fingerprints, "NIC_ITEMS", "网卡", [("bdf", "BDF"), ("role", "角色"), ("mtu", "MTU"), ("state", "状态"), ("speed", "速率"), ("pcie", "PCIe"), ("pcie_bw", "PCIe 带宽"), ("driver", "驱动"), ("version", "驱动版本"), ("firmware", "固件"), ("rxerr", "RX_ERR"), ("txerr", "TX_ERR")])
    bandwidth = []
    for item in nvme + nics:
        per_host = {}
        for host, data in fingerprints.items():
            key = item["name"].split(" ", 1)[1]
            source = data["NVME_ITEMS"].get(key) if item["name"].startswith("NVMe ") else data["NIC_ITEMS"].get(key)
            if source:
                per_host[host] = f"{source.get('actual', source.get('pcie', '?'))} / {source.get('bandwidth', source.get('pcie_bw', '?'))}"
            else:
                per_host[host] = "未发现"
        bandwidth.append({"name": item["name"], "values": per_host, "same": len(set(per_host.values())) == 1})
    return {"overview": overview, "same": same, "different": different, "nvme": nvme, "nics": nics, "bandwidth": bandwidth}



@csrf_exempt
@require_http_methods(["POST"])
def inspect(request):
    try:
        data = json.loads(request.body or b"{}")
        hosts = [str(host).strip() for host in data.get("hosts", []) if str(host).strip()]
        if not hosts:
            return JsonResponse({"status": "error", "error": "请提供至少一台主机"}, status=400)
        auth = parse_ssh_auth(data)
        port = int(data.get("port", 22) or 22)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(hosts), 8)) as executor:
            results = list(executor.map(lambda host: inspect_host(host, auth, port), hosts))
        return JsonResponse({"status": "success", "results": results, "comparison": comparison(results)})
    except ValueError as exc:
        return JsonResponse({"status": "error", "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"status": "error", "error": str(exc)}, status=500)
