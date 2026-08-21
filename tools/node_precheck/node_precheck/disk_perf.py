from __future__ import print_function

import math
import os
import re
import time
import traceback

from .checks import list_nvme_disks
from .common import PASS, WARN, FAIL, ERROR, shell_quote
from .ssh import SSHClient
from .report_html import generate_disk_perf_html

DISK_PERF_REPORT = "disk_perf_report_latest.html"
DISK_PERF_MODELS = [
    {"id": "4k_randread", "name": "4K Random Read", "ioengine": "libaio", "rw": "randread", "bs": "4K", "numjobs": 24, "iodepth": 32, "runtime": 300, "latency_metric": "r_await_us", "pcie4_latency_us": 700.0, "pcie5_latency_us": 600.0, "latency_over_threshold": 5, "performance_metric": "r_s", "pcie4_min_performance": 900000.0, "pcie5_min_performance": 1500000.0},
    {"id": "1m_read", "name": "128K Random Read", "ioengine": "libaio", "rw": "randread", "bs": "128K", "numjobs": 8, "iodepth": 2, "runtime": 300, "latency_metric": "r_await_us", "latency_us": 500.0, "latency_over_threshold": 1, "performance_metric": "rMB_s", "pcie4_min_performance": 6000.0, "pcie5_min_performance": 11000.0},
    {"id": "4k_randwrite", "name": "4K Random Write", "ioengine": "libaio", "rw": "randwrite", "bs": "4K", "numjobs": 6, "iodepth": 16, "runtime": 300, "latency_metric": "w_await_us", "pcie4_latency_us": 200.0, "pcie5_latency_us": 100.0, "latency_over_threshold": 5, "performance_metric": "w_s", "pcie4_min_performance": 150000.0, "pcie5_min_performance": 400000.0},
    {"id": "1m_write", "name": "128K Random Write", "ioengine": "libaio", "rw": "randwrite", "bs": "128K", "numjobs": 8, "iodepth": 2, "runtime": 600, "latency_metric": "w_await_us", "latency_us": 500.0, "latency_over_threshold": 1, "performance_metric": "wMB_s", "pcie4_min_performance": 4000.0, "pcie5_min_performance": 6000.0},
]
AQU_SZ_THRESHOLD = 500.0
CV_THRESHOLD = 0.10
WARMUP_SECONDS = 120

# Kept as a compatibility alias for existing callers/report consumers.
VOLATILITY_THRESHOLD = CV_THRESHOLD
TAIL_SKIP_SECONDS = 10


def run_node_disk_perf(node, timeout, verbose, run_id=None, log_root=None):
    """Run disk tests and store this node's artifacts below one run root."""
    result = {"ip": node.mgmt_ip, "status": PASS, "errors": [], "disks": [], "fio": "", "log_dir": ""}
    if run_id is None:
        run_id = time.strftime("%Y%m%d_%H%M%S")
    local_dir = os.path.join(log_root, "disk_perf", node.mgmt_ip) if log_root else os.path.abspath("disk_perf_logs_{0}_{1}".format(run_id, node.mgmt_ip))
    result["log_dir"] = local_dir
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)
    detail_log = os.path.join(local_dir, "disk_perf_detail.log")
    fio_cleanup_dir = ""
    log_stage(node.mgmt_ip, "disk-perf start")
    try:
        with SSHClient(node, timeout) as ssh:
            fio_bin, fio_desc, fio_cleanup_dir = prepare_fio(ssh, run_id, local_dir, timeout, verbose)
            result["fio"] = fio_desc
            append_detail(detail_log, "fio={0}".format(fio_desc))
            log_stage(node.mgmt_ip, "discover NVMe disks")
            disks = list_nvme_disks(ssh, include_system=False)
            disk_paths = [d["path"] for d in disks]
            append_detail(detail_log, "selected all data disks={0}".format(",".join(disk_paths)))
            log_stage(node.mgmt_ip, "selected {0} data disks: {1}".format(len(disk_paths), ",".join(disk_paths)))
            if not disks:
                result["status"] = WARN
                result["errors"].append("未发现用于性能测试的非系统盘 NVMe")
                return result
            log_stage(node.mgmt_ip, "found {0} data disks: {1}".format(len(disk_paths), ",".join(disk_paths)))

            discard_nvme_disks(ssh, node.mgmt_ip, disk_paths)
            pcie_map = detect_nvme_pcie_generations(ssh, disks)
            for disk in disks:
                disk["pcie_generation"] = pcie_map.get(disk["path"], "unknown")
                disk["models"] = {}
                result["disks"].append(disk)
            for model in DISK_PERF_MODELS:
                log_stage(node.mgmt_ip, "start {0} runtime={1}s".format(model["id"], model["runtime"]))
                model_result = run_fio_iostat_model(ssh, fio_bin, disk_paths, model, timeout, local_dir)
                log_stage(node.mgmt_ip, "parse {0} iostat".format(model["id"]))
                for disk in result["disks"]:
                    disk_model = model_result.get("disks", {}).get(disk["path"], empty_disk_model("no iostat data"))
                    evaluate_disk_model(disk_model, disk.get("pcie_generation", "unknown"), model)
                    disk["models"][model["id"]] = disk_model
                    append_detail(detail_log, "model={0} disk={1} status={2} error={3}".format(model["id"], disk["path"], disk_model.get("status"), disk_model.get("error", "")))
                    if disk_model.get("status") == FAIL:
                        result["status"] = FAIL
                    elif disk_model.get("status") == ERROR and result["status"] != FAIL:
                        result["status"] = ERROR
                    elif disk_model.get("status") == WARN and result["status"] == PASS:
                        result["status"] = WARN
                if model_result.get("error"):
                    result["errors"].append("{0}: {1}".format(model["id"], model_result.get("error")))
                    if result["status"] == PASS:
                        result["status"] = ERROR
                log_stage(node.mgmt_ip, "finish {0} status={1}".format(model["id"], result["status"]))
            log_stage(node.mgmt_ip, "scan /var/log/messages for NVMe I/O errors")
            io_error_result = check_nvme_io_errors(ssh, disk_paths)
            append_detail(detail_log, "nvme_io_error status={0} error={1}".format(io_error_result.get("status"), io_error_result.get("error", "")))
            if io_error_result.get("status") == FAIL:
                result["status"] = FAIL
                result["errors"].append(io_error_result.get("error"))
            elif io_error_result.get("status") == WARN and result["status"] == PASS:
                result["status"] = WARN
                result["errors"].append(io_error_result.get("error"))
            if fio_cleanup_dir:
                ssh.run("rm -rf {0}".format(shell_quote(fio_cleanup_dir)), timeout=60)
    except Exception as exc:
        result["status"] = ERROR
        result["errors"].append(str(exc))
        append_detail(detail_log, "error={0}".format(exc))
        if verbose:
            traceback.print_exc()
    log_stage(node.mgmt_ip, "disk-perf done status={0}".format(result["status"]))
    return result


def log_stage(ip, message, verbose=False):
    print("[{0}] {1}".format(ip, message))
    try:
        sys_stdout_flush = getattr(__import__("sys"), "stdout").flush
        sys_stdout_flush()
    except Exception:
        pass


def append_detail(path, message):
    with open(path, "a") as fh:
        fh.write("{0}\n".format(message))


def discard_nvme_disks(ssh, ip, disk_paths, verbose=False):
    for path in disk_paths:
        log_stage(ip, "blkdiscard {0}".format(path))
        code, out, err = ssh.run("blkdiscard -f {0}".format(shell_quote(path)), timeout=600)
        if code != 0:
            raise RuntimeError("blkdiscard failed on {0}: {1}".format(path, err or out))


def prepare_fio(ssh, run_id, local_dir, timeout, verbose=False):
    code, out, err = ssh.run("command -v fio 2>/dev/null || true", timeout=30)
    remote_fio = out.strip().splitlines()[0] if out.strip() else ""
    if remote_fio:
        return remote_fio, "remote:{0}".format(remote_fio), ""

    local_fio = find_local_fio()
    if not local_fio:
        raise RuntimeError("需要安装 fio：远端未安装 fio，且本地同目录未找到可上传的 fio")

    remote_dir = "/tmp/node_precheck_fio_{0}".format(run_id)
    remote_path = remote_dir + "/fio"
    code, out, err = ssh.run("rm -rf {0}; mkdir -p {0}".format(shell_quote(remote_dir)), timeout=60)
    if code != 0:
        raise RuntimeError("准备远端 fio 目录失败: {0}".format(err or out))
    ssh.put_file(local_fio, remote_path, timeout=max(timeout, 300))
    code, out, err = ssh.run("chmod +x {0}".format(shell_quote(remote_path)), timeout=30)
    if code != 0:
        raise RuntimeError("远端 fio 授权失败: {0}".format(err or out))
    append_detail(os.path.join(local_dir, "disk_perf_detail.log"), "uploaded local fio {0} to {1}".format(local_fio, remote_path))
    log_stage(ssh.node.mgmt_ip, "uploaded local fio {0}".format(remote_path))
    return remote_path, "uploaded:{0}".format(remote_path), remote_dir


def find_local_fio():
    candidates = [
        os.path.abspath("fio"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fio"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fio"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def check_nvme_io_errors(ssh, disk_paths=None):
    cmd = r'''
if [ ! -r /var/log/messages ]; then
  echo __MESSAGES_UNREADABLE__
  exit 0
fi
# Only accept kernel-originated messages. User-space audit/Ansible command text can
# contain an NVMe error pattern without reporting an actual device error.
grep -Eai 'kernel(:|\[[0-9]+\]:).*nvme.*(I/O error|timeout|reset|abort)' /var/log/messages 2>/dev/null | tail -50
'''
    code, out, err = ssh.run(cmd, timeout=60)
    if code != 0:
        return {"status": WARN, "error": "NVMe I/O error log scan failed: {0}".format(err or out)}
    lines = [line for line in out.splitlines() if line.strip()]
    if lines and lines[0].strip() == "__MESSAGES_UNREADABLE__":
        return {"status": WARN, "error": "/var/log/messages 不可读，无法检查 NVMe I/O error"}
    if lines:
        devices = extract_nvme_devices(lines, disk_paths or [])
        device_text = ",".join(devices) if devices else "未能从内核日志解析盘符"
        return {"status": FAIL, "error": "/var/log/messages 发现 NVMe 内核 I/O 异常，异常盘={0}: {1}".format(device_text, " | ".join(lines))}
    return {"status": PASS, "error": ""}


def extract_nvme_devices(lines, disk_paths):
    """Return affected tested NVMe paths referenced by kernel error messages."""
    known = dict((os.path.basename(path), path) for path in disk_paths)
    found = []
    for line in lines:
        names = re.findall(r"\bnvme\d+n\d+(?:p\d+)?\b", line, re.IGNORECASE)
        names += re.findall(r"\bnvme\d+\b", line, re.IGNORECASE)
        for name in names:
            base = name.lower()
            disk_name = re.sub(r"p\d+$", "", base)
            path = known.get(disk_name)
            if path is None and re.match(r"^nvme\d+n\d+$", disk_name):
                path = "/dev/" + disk_name
            if path and path not in found:
                found.append(path)
    return found


def detect_nvme_pcie_generations(ssh, disks):
    cmd = r'''
for dev in {devices}; do
  name=${{dev##*/}}
  path=$(readlink -f /sys/class/block/$name/device 2>/dev/null || true)
  cur=$path
  speed=""
  while [ -n "$cur" ] && [ "$cur" != "/" ]; do
    if [ -f "$cur/current_link_speed" ]; then
      speed=$(cat "$cur/current_link_speed" 2>/dev/null)
      break
    fi
    cur=${{cur%/*}}
  done
  gen="unknown"
  case "$speed" in
    *32.0*) gen="5.0" ;;
    *16.0*) gen="4.0" ;;
    *8.0*) gen="3.0" ;;
  esac
  printf '%s|%s|%s\n' "$dev" "$gen" "$speed"
done
'''.format(devices=" ".join([shell_quote(d["path"]) for d in disks]))
    code, out, err = ssh.run(cmd, timeout=30)
    result = {}
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) >= 2:
            result[parts[0]] = parts[1]
    return result


def build_fio_job_file(disk_paths, model):
    """One job per disk so every disk gets the same numjobs/iodepth, regardless of disk count."""
    lines = [
        "[global]",
        "group_reporting=1",
        "ioengine={0}".format(model.get("ioengine", "libaio")),
        "direct=1",
        "rw={0}".format(model["rw"]),
        "bs={0}".format(model["bs"]),
        "numjobs={0}".format(model["numjobs"]),
        "iodepth={0}".format(model["iodepth"]),
        "time_based=1",
        "runtime={0}".format(model["runtime"]),
        "",
    ]
    if model["rw"] in ("randread", "randwrite"):
        # fio keeps a per-process bitmap of visited blocks for random I/O; on
        # multi-TB disks that is ~1.7GB per worker and OOMs with many jobs.
        lines.append("norandommap=1")
        lines.append("")
    for path in disk_paths:
        lines.append("[{0}]".format(os.path.basename(path)))
        lines.append("filename={0}".format(path))
        lines.append("")
    return "\n".join(lines)


def parse_fio_log(text, bandwidth=False):
    points = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            timestamp = float(parts[0]) / 1000.0
            value = float(parts[1])
        except (ValueError, TypeError):
            continue
        if bandwidth:
            value = value / 1024.0
        points.append({"time": timestamp, "value": value})
    return points


def merge_fio_series(iops_points, bw_points, model):
    series = {}
    iops_key = "read_iops" if model["rw"] in ("read", "randread") else "write_iops"
    bw_key = "read_bw_mb_s" if model["rw"] in ("read", "randread") else "write_bw_mb_s"
    for point in iops_points:
        series.setdefault(point["time"], {"time": point["time"]})[iops_key] = point["value"]
    for point in bw_points:
        series.setdefault(point["time"], {"time": point["time"]})[bw_key] = point["value"]
    return [series[key] for key in sorted(series)]


def summarize_fio_points(points):
    summary = {}
    stable_points = get_stable_points(points)
    for key in ["read_iops", "write_iops", "read_bw_mb_s", "write_bw_mb_s"]:
        values = [point.get(key, 0.0) for point in points if key in point]
        stable_values = [point.get(key, 0.0) for point in stable_points if key in point]
        summary[key] = summarize_values(values, stable_values)
    return summary


def summarize_values(values, stable_values):
    avg = sum(values) / len(values) if values else 0.0
    max_v = max(values) if values else 0.0
    min_v = min(values) if values else 0.0
    stable_avg = sum(stable_values) / len(stable_values) if stable_values else 0.0
    stable_max = max(stable_values) if stable_values else 0.0
    stable_min = min(stable_values) if stable_values else 0.0
    stable_stddev = math.sqrt(sum((value - stable_avg) ** 2 for value in stable_values) / (len(stable_values) - 1)) if len(stable_values) > 1 else 0.0
    cv = stable_stddev / stable_avg if stable_avg > 0 else 0.0
    range_ratio = (stable_max - stable_min) / stable_avg if stable_avg > 0 else 0.0
    return {"avg": avg, "max": max_v, "min": min_v, "stable_avg": stable_avg, "stable_max": stable_max, "stable_min": stable_min, "stable_stddev": stable_stddev, "cv": cv, "range_ratio": range_ratio}

def run_fio_iostat_model(ssh, fio_bin, disk_paths, model, timeout, local_dir):
    fio_job = build_fio_job_file(disk_paths, model)
    disk_names = " ".join([shell_quote(os.path.basename(p)) for p in disk_paths])
    total_timeout = int(model["runtime"]) + 120
    cmd = r'''
tmp=$(mktemp -d /tmp/node_precheck_perf.XXXXXX) || exit 1
iostat_log="$tmp/{model_id}.iostat.log"
fio_log="$tmp/{model_id}.fio.log"
fio_job="$tmp/perf.fio"
iostat_pid=""
cleanup() {{
  [ -n "$iostat_pid" ] && kill "$iostat_pid" >/dev/null 2>&1 || true
}}
trap cleanup EXIT
(iostat -xmd 1 {disk_names} > "$iostat_log" 2>&1) &
iostat_pid=$!
sleep 2
cat > "$fio_job" <<'JOB_EOF'
{jobfile}
JOB_EOF
{fio_bin} "$fio_job" > "$fio_log" 2>&1
fio_rc=$?
cleanup
trap - EXIT
echo FIO_RC=$fio_rc
echo REMOTE_DIR=$tmp
exit 0
'''.format(model_id=model["id"], disk_names=disk_names, jobfile=fio_job, fio_bin=shell_quote(fio_bin))
    code, out, err = ssh.run(cmd, timeout=max(total_timeout, timeout))
    fio_rc = 1
    remote_dir = ""
    match = re.search(r"FIO_RC=(\d+)", out or "")
    if match:
        fio_rc = int(match.group(1))
    match = re.search(r"REMOTE_DIR=(\S+)", out or "")
    if match:
        remote_dir = match.group(1)
    data = {"fio_rc": fio_rc, "error": "", "fio_tail": "", "disks": {}}
    if code != 0:
        data["error"] = "remote command failed rc={0}: {1}".format(code, err or out)
    if fio_rc != 0:
        data["error"] = "fio failed rc={0}".format(fio_rc)
    iostat_text = ""
    fio_text = ""
    if remote_dir:
        iostat_local = os.path.join(local_dir, "{0}.iostat.log".format(model["id"]))
        fio_local = os.path.join(local_dir, "{0}.fio.log".format(model["id"]))
        try:
            ssh.fetch_file(remote_dir + "/{0}.iostat.log".format(model["id"]), iostat_local, timeout=120)
            ssh.fetch_file(remote_dir + "/{0}.fio.log".format(model["id"]), fio_local, timeout=120)
            iostat_text = read_text_file(iostat_local)
            fio_text = read_text_file(fio_local)
        except Exception as exc:
            data["error"] = "performance log fetch failed: {0}".format(exc)
        ssh.run("rm -rf {0}".format(shell_quote(remote_dir)), timeout=30)
    else:
        data["error"] = data["error"] or "remote log directory missing"
    series = parse_iostat(iostat_text, [os.path.basename(p) for p in disk_paths])
    data["fio_tail"] = fio_text[-4000:]
    for path in disk_paths:
        name = os.path.basename(path)
        points = series.get(name, [])
        data["disks"][path] = {"status": PASS, "error": data["error"], "series": points, "summary": summarize_points(points)}
    return data

def read_text_file(path):
    with open(path, "r") as fh:
        return fh.read()


def extract_block(text, begin, end):
    if begin not in text or end not in text:
        return ""
    return text.split(begin, 1)[1].split(end, 1)[0]


def parse_iostat(text, device_names):
    wanted = set(device_names)
    result = dict((name, []) for name in device_names)
    headers = []
    index = {}
    sample = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Device"):
            headers = line.split()
            index = dict((name, i) for i, name in enumerate(headers))
            sample += 1
            continue
        if not headers:
            continue
        parts = line.split()
        dev = parts[0]
        if dev not in wanted:
            continue
        point = {"time": sample}
        for key in ["r_await", "w_await", "r/s", "w/s", "rMB/s", "wMB/s", "aqu-sz"]:
            point[metric_key(key)] = read_iostat_float(parts, index, key)
        point["r_await_us"] = point.get("r_await", 0.0) * 1000.0
        point["w_await_us"] = point.get("w_await", 0.0) * 1000.0
        result[dev].append(point)
    return result


def metric_key(key):
    return key.replace("/", "_").replace("-", "_")


def read_iostat_float(parts, index, key):
    if key not in index:
        return 0.0
    i = index[key]
    if i >= len(parts):
        return 0.0
    try:
        return float(parts[i])
    except Exception:
        return 0.0


def get_stable_points(points):
    if not points:
        return []
    max_time = max([p.get("time", 0) for p in points])
    stable_points = [p for p in points if p.get("time", 0) > WARMUP_SECONDS and p.get("time", 0) <= max_time - TAIL_SKIP_SECONDS]
    return stable_points if stable_points else points


def summarize_points(points):
    summary = {}
    stable_points = get_stable_points(points)
    for key in ["r_await_us", "w_await_us", "r_s", "w_s", "rMB_s", "wMB_s", "aqu_sz"]:
        values = [p.get(key, 0.0) for p in points]
        stable_values = [p.get(key, 0.0) for p in stable_points]
        if values:
            avg = sum(values) / len(values)
            max_v = max(values)
            min_v = min(values)
        else:
            avg = max_v = min_v = 0.0
        if stable_values:
            stable_avg = sum(stable_values) / len(stable_values)
            stable_max = max(stable_values)
            stable_min = min(stable_values)
            stable_stddev = math.sqrt(sum([(value - stable_avg) ** 2 for value in stable_values]) / (len(stable_values) - 1)) if len(stable_values) > 1 else 0.0
            cv = (stable_stddev / stable_avg) if stable_avg > 0 else 0.0
            range_ratio = ((stable_max - stable_min) / stable_avg) if stable_avg > 0 else 0.0
        else:
            stable_avg = stable_max = stable_min = stable_stddev = cv = range_ratio = 0.0
        summary[key] = {
            "avg": avg,
            "max": max_v,
            "min": min_v,
            "stable_avg": stable_avg,
            "stable_max": stable_max,
            "stable_min": stable_min,
            "stable_stddev": stable_stddev,
            "cv": cv,
            "range_ratio": range_ratio,
            "volatility": cv,
        }
    return summary


def empty_disk_model(error):
    return {"status": ERROR, "error": error, "series": [], "summary": summarize_points([]), "thresholds": {}}


def evaluate_disk_model(disk_model, pcie_generation, model):
    if pcie_generation == "5.0":
        min_performance = model["pcie5_min_performance"]
        latency_limit = model.get("pcie5_latency_us", model.get("latency_us"))
    else:
        min_performance = model["pcie4_min_performance"]
        latency_limit = model.get("pcie4_latency_us", model.get("latency_us"))
    performance_metric = model["performance_metric"]
    latency_over_threshold = model["latency_over_threshold"]
    thresholds = {
        performance_metric: min_performance,
        model["latency_metric"]: latency_limit,
        "latency_over_threshold": latency_over_threshold,
        "aqu_sz": AQU_SZ_THRESHOLD,
        "cv": CV_THRESHOLD,
        "volatility": CV_THRESHOLD,
        "warmup_seconds": WARMUP_SECONDS,
        "tail_skip_seconds": TAIL_SKIP_SECONDS,
    }
    disk_model["thresholds"] = thresholds
    if disk_model.get("error"):
        disk_model["status"] = ERROR
        return
    points = disk_model.get("series", [])
    if not points:
        disk_model["status"] = ERROR
        disk_model["error"] = "no iostat data"
        return
    stable_points = get_stable_points(points)
    summary = disk_model.get("summary", {})
    stable_performance = summary.get(performance_metric, {}).get("stable_avg", 0.0)
    bad_performance = stable_performance < min_performance
    bad_latency = [point for point in stable_points if point.get(model["latency_metric"], 0.0) >= latency_limit]
    bad_aqu = [p for p in stable_points if p.get("aqu_sz", 0.0) >= AQU_SZ_THRESHOLD]
    bad_cv = collect_bad_cv(summary, model)
    disk_model["performance_metric"] = performance_metric
    disk_model["performance_value"] = stable_performance
    disk_model["performance_minimum"] = min_performance
    latency_over_count = len(bad_latency)
    bad_latency_count = latency_over_count >= latency_over_threshold
    disk_model["latency_over_count"] = latency_over_count
    disk_model["latency_over_threshold"] = latency_over_threshold
    disk_model["latency_limit"] = latency_limit
    if bad_performance or bad_latency_count or bad_aqu or bad_cv:
        disk_model["status"] = FAIL
        disk_model["error"] = "performance={0:.2f}, performance_min={1:.2f}, latency_over={2}, latency_over_threshold={3}, latency_limit_us={4:.2f}, aqu_over={5}, cv_over={6}".format(
            stable_performance, min_performance, latency_over_count, latency_over_threshold, latency_limit, len(bad_aqu), ",".join(bad_cv) if bad_cv else "0")
    else:
        disk_model["status"] = PASS


def collect_bad_cv(summary, model):
    if model["rw"] in ("read", "randread"):
        keys = ["r_s", "rMB_s"]
    else:
        keys = ["w_s", "wMB_s"]
    bad = []
    for key in keys:
        item = summary.get(key, {})
        if item.get("stable_avg", 0.0) <= 0:
            continue
        if item.get("cv", 0.0) >= CV_THRESHOLD:
            bad.append("{0}={1:.2%}".format(key, item.get("cv", 0.0)))
    return bad


def collect_bad_volatility(summary, model):
    """Compatibility wrapper; CV is now the primary volatility metric."""
    return collect_bad_cv(summary, model)


def write_disk_perf_report(results, output_path):
    generate_disk_perf_html(results, DISK_PERF_MODELS, output_path, AQU_SZ_THRESHOLD, CV_THRESHOLD)
