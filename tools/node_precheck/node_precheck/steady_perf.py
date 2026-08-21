from __future__ import print_function

import json
import math
import os
import re
import sys
import time

from .checks import list_nvme_disks
from .common import PASS, WARN, FAIL, ERROR, shell_quote
from .ssh import SSHClient
from .disk_perf import prepare_fio
from .steady_report_html import MODEL_LABELS, build_steady_report, build_steady_report_data, generate_steady_perf_html

STEADY_PERF_REPORT = "steady_perf_report_latest.html"

STEADY_FILL_BS = "1m"
STEADY_FILL_NUMJOBS = 1
STEADY_FILL_IODEPTH = 16
STEADY_FILL_LOOPS = 2
STEADY_RUNTIME = 1800
STEADY_RAMP_TIME = 30
STEADY_LOG_AVG_MSEC = 1000
STEADY_HIST_MSEC = 1000

# fio 3.19+ 的 log_hist_msec 直方图桶数（29 组 × 64 子桶）
FIO_IO_U_PLAT_BITS = 6
FIO_IO_U_PLAT_VAL = 1 << FIO_IO_U_PLAT_BITS
FIO_IO_U_PLAT_GROUP_NR = 29
FIO_IO_U_PLAT_NR = FIO_IO_U_PLAT_GROUP_NR * FIO_IO_U_PLAT_VAL

POLL_INTERVAL = int(os.environ.get("NODECHECK_STEADY_POLL_INTERVAL", "30"))
FILL_TIMEOUT_SECONDS = int(os.environ.get("NODECHECK_STEADY_FILL_TIMEOUT", "28800"))
MODEL_TIMEOUT_SECONDS = int(os.environ.get("NODECHECK_STEADY_MODEL_TIMEOUT", "5400"))
REMOTE_BASE_DIR = os.environ.get("NODECHECK_STEADY_REMOTE_DIR", "/tmp/node_precheck_steady")

# 短跑调试：正式模型压到 30s、预埋只写 512M，用于快速冒烟
SHORT_MODE = os.environ.get("NODECHECK_STEADY_SHORT") == "1"
SHORT_RUNTIME = 30
SHORT_FILL_LIMIT = "512M"

# 顺序即执行顺序
STEADY_MODELS = [
    {"key": "randwrite", "rw": "randwrite", "bs": "128k", "numjobs": 8, "iodepth": 2},
    {"key": "randread", "rw": "randread", "bs": "128K", "numjobs": 8, "iodepth": 2},
    {"key": "randwrite-4k", "rw": "randwrite", "bs": "4k", "numjobs": 6, "iodepth": 16},
    {"key": "randread-4k", "rw": "randread", "bs": "4k", "numjobs": 16, "iodepth": 64},
]


def log_stage(ip, message):
    print("[{0}] {1}".format(ip, message))
    try:
        sys.stdout.flush()
    except Exception:
        pass


def run_steady_perf(storage_nodes, timeout, verbose, steady_node=None, steady_disk=None, run_id=None, log_root=None):
    """Entry: pick target node(s)/disk(s), run, write report."""
    if run_id is None:
        run_id = time.strftime("%Y%m%d_%H%M%S")
    if log_root is None:
        log_root = os.path.abspath("node_precheck_logs_{0}".format(run_id))
    targets = pick_targets(storage_nodes, steady_node, steady_disk, timeout, verbose)
    if not targets:
        print("配置错误: 未找到稳态测试目标{0}".format(" " + steady_node if steady_node else ""), file=sys.stderr)
        return None, 2
    disk_results = []
    for node, disk_path in targets:
        disk_results.append(run_node_steady_perf(node, timeout, verbose, disk_path, run_id, log_root))
    disk_reports = [r["report"] for r in disk_results if r.get("report")]
    if not disk_reports:
        print("稳态测试未产出任何报告", file=sys.stderr)
        return disk_results, 2
    write_steady_report(build_steady_report(disk_reports), log_root)
    print_steady_summary(disk_results)
    return disk_results, 0


def pick_targets(nodes, steady_node, steady_disk, timeout, verbose):
    """Resolve (node, disk) targets: explicit node/disk, or every allowed disk on each node."""
    selected = [n for n in nodes if n.mgmt_ip == steady_node] if steady_node else list(nodes)
    targets = []
    for node in selected:
        disks = discover_data_disks(node, timeout, verbose)
        if steady_disk:
            if steady_disk in disks:
                targets.append((node, steady_disk))
        else:
            for disk_path in disks:
                targets.append((node, disk_path))
    return targets


def discover_data_disks(node, timeout, verbose):
    try:
        with SSHClient(node, timeout) as ssh:
            disks = list_nvme_disks(ssh, include_system=False)
    except Exception:
        return []
    return [d["path"] for d in disks]


def run_node_steady_perf(node, timeout, verbose, disk_path, run_id=None, log_root=None, restart=False):
    result = {
        "ip": node.mgmt_ip,
        "disk": disk_path,
        "status": PASS,
        "errors": [],
        "log_dir": "",
        "report": None,
    }
    if run_id is None:
        run_id = time.strftime("%Y%m%d_%H%M%S")
    local_dir = os.path.join(log_root, "steady_perf", node.mgmt_ip, os.path.basename(disk_path)) if log_root else os.path.abspath("steady_perf_logs_{0}_{1}_{2}".format(run_id, node.mgmt_ip, os.path.basename(disk_path)))
    result["log_dir"] = local_dir
    if not os.path.exists(local_dir):
        os.makedirs(local_dir)
    status_path = os.path.join(local_dir, "steady_status.json")
    status = load_status(status_path)
    resume = (not restart) and status is not None and status.get("disk") == disk_path and status.get("finished") is not True
    if not resume:
        status = new_status(disk_path)
    log_stage(node.mgmt_ip, "steady-perf start disk={0} resume={1}".format(disk_path, resume))
    fio_cleanup_dir = ""
    try:
        with SSHClient(node, timeout) as ssh:
            fio_bin, fio_desc, fio_cleanup_dir = prepare_fio(ssh, run_id, local_dir, timeout, verbose)
            result["fio"] = fio_desc
            log_stage(node.mgmt_ip, "fio={0}".format(fio_desc))
            disks = list_nvme_disks(ssh, include_system=False)
            paths = [d["path"] for d in disks]
            if disk_path not in paths:
                raise RuntimeError("目标盘 {0} 不是节点 {1} 的数据盘，已取消".format(disk_path, node.mgmt_ip))
            check_disk_unmounted(ssh, disk_path)
            model = query_disk_model(ssh, disk_path)
            size_text = query_disk_size(ssh, disk_path)
            log_stage(node.mgmt_ip, "disk model={0} size={1}".format(model, size_text))
            started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            remote_base = os.path.join(REMOTE_BASE_DIR, time.strftime("%Y%m%d"))
            ensure_remote_dir(ssh, remote_base)
            if not resume:
                run_blkdiscard(ssh, disk_path, timeout, verbose)

            fill = run_stage_fill(ssh, fio_bin, node.mgmt_ip, disk_path, model, remote_base, status, local_dir, timeout, verbose)
            models = []
            for m in STEADY_MODELS:
                stage = run_stage_model(ssh, fio_bin, node.mgmt_ip, disk_path, model, m, remote_base, status, local_dir, timeout, verbose)
                models.append(stage)
                if stage["status"] != "done":
                    result["status"] = ERROR if stage["status"] == "failed" else WARN

            status["finished"] = True
            save_status(status_path, status)
            meta = {
                "node": node.mgmt_ip,
                "disk": disk_path,
                "disk_model": model,
                "disk_size": size_text,
                "started_at": started_at,
                "total_duration_s": fill.get("duration_s", 0) + sum(m.get("duration_s", 0) for m in models),
            }
            result["report"] = build_steady_report_data(meta, fill, models, result["errors"])
            if fio_cleanup_dir:
                ssh.run("rm -rf {0}".format(shell_quote(fio_cleanup_dir)), timeout=60)
    except Exception as exc:
        result["status"] = ERROR
        result["errors"].append(str(exc))
        log_stage(node.mgmt_ip, "steady-perf error: {0}".format(exc))
    save_status(status_path, status)
    log_stage(node.mgmt_ip, "steady-perf done status={0}".format(result["status"]))
    return result


def new_status(disk_path):
    return {"disk": disk_path, "finished": False, "stages": {}}


def load_status(path):
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_status(path, status):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def stage_state(status, key):
    stage = status.get("stages", {}).get(key, {})
    return stage.get("state", "pending"), stage


def mark_stage(status, key, state, **extra):
    status.setdefault("stages", {})
    entry = status["stages"].setdefault(key, {})
    entry["state"] = state
    entry.update(extra)
    return entry


def ensure_remote_dir(ssh, remote_dir):
    code, out, err = ssh.run("mkdir -p {0}".format(shell_quote(remote_dir)), timeout=60)
    if code != 0:
        raise RuntimeError("创建远端日志目录失败 {0}: {1}".format(remote_dir, err or out))


def check_disk_unmounted(ssh, disk_path):
    code, out, err = ssh.run(
        "findmnt -n {0} >/dev/null 2>&1 && echo MOUNTED || echo OK".format(shell_quote(disk_path)),
        timeout=30,
    )
    if "MOUNTED" in out:
        raise RuntimeError("目标盘 {0} 已挂载，拒绝 blkdiscard/写盘".format(disk_path))


def run_blkdiscard(ssh, disk_path, timeout, verbose):
    log_stage(ssh.node.mgmt_ip, "blkdiscard {0}".format(disk_path))
    code, out, err = ssh.run("blkdiscard -f {0}".format(shell_quote(disk_path)), timeout=1800)
    if code != 0:
        raise RuntimeError("blkdiscard 失败 {0}: {1}".format(disk_path, err or out))


def query_disk_model(ssh, disk_path):
    name = os.path.basename(disk_path)
    code, out, err = ssh.run("cat /sys/class/block/{0}/device/model 2>/dev/null || true".format(shell_quote(name)), timeout=30)
    model = out.strip()
    if not model or model.lower() in ("unknown", "unknowndevice"):
        model = "unknown"
    return re.sub(r"\s+", "_", model)


def query_disk_size(ssh, disk_path):
    name = os.path.basename(disk_path)
    code, out, err = ssh.run("cat /sys/class/block/{0}/size 2>/dev/null || true".format(shell_quote(name)), timeout=30)
    try:
        sectors = int(out.strip())
    except (ValueError, TypeError):
        return ""
    bytes_total = sectors * 512
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if bytes_total < 1024.0 or unit == "PB":
            if unit == "B":
                return "{0} {1}".format(bytes_total, unit)
            return "{0:.2f} {1}".format(bytes_total, unit)
        bytes_total /= 1024.0
    return ""


def job_base_name(ip, disk_path, model):
    name = "{0}-{1}-{2}".format(ip.replace(".", "-"), os.path.basename(disk_path), model)
    return name


def build_fill_job_file(base_name, disk_path, remote_base, remote_dir):
    lines = [
        "[global]",
        "ioengine=libaio",
        "direct=1",
        "rw=write",
        "bs={0}".format(STEADY_FILL_BS),
        "numjobs={0}".format(STEADY_FILL_NUMJOBS),
        "iodepth={0}".format(STEADY_FILL_IODEPTH),
        "loops={0}".format(STEADY_FILL_LOOPS),
        "group_reporting=1",
        "per_job_logs=0",
        "log_avg_msec={0}".format(STEADY_LOG_AVG_MSEC),
        "log_unix_epoch=1",
        "",
        "[{0}-precond]".format(base_name),
        "filename={0}".format(disk_path),
    ]
    if SHORT_MODE:
        lines[7] = "loops=1"
        lines.append("size={0}".format(SHORT_FILL_LIMIT))
    lines.append("write_bw_log={0}/precond_bw.log".format(remote_dir))
    lines.append("write_lat_log={0}/precond_lat.log".format(remote_dir))
    return "\n".join(lines) + "\n"


def build_model_job_file(base_name, disk_path, model, remote_dir):
    suffix = "{0}_{1}_{2}".format(model["bs"], model["numjobs"], model["iodepth"])
    log_base = "{0}/{1}-{2}_{3}".format(remote_dir, base_name, model["key"], suffix)
    runtime = SHORT_RUNTIME if SHORT_MODE else STEADY_RUNTIME
    lines = [
        "[global]",
        "ioengine=libaio",
        "direct=1",
        "bs={0}".format(model["bs"]),
        "numjobs={0}".format(model["numjobs"]),
        "iodepth={0}".format(model["iodepth"]),
        "time_based=1",
        "runtime={0}".format(runtime),
        "ramp_time={0}".format(STEADY_RAMP_TIME),
        "group_reporting=1",
        "per_job_logs=0",
        "log_avg_msec={0}".format(STEADY_LOG_AVG_MSEC),
        "log_unix_epoch=1",
        "lat_percentiles=1",
        "log_hist_msec={0}".format(STEADY_HIST_MSEC),
        "norandommap=1",
        "",
        "[{0}-{1}]".format(base_name, model["key"]),
        "rw={0}".format(model["rw"]),
        "filename={0}".format(disk_path),
        "write_iops_log={0}.iops.log".format(log_base),
        "write_bw_log={0}.bw.log".format(log_base),
        "write_lat_log={0}.lat.log".format(log_base),
        "write_hist_log={0}.hist.log".format(log_base),
    ]
    return "\n".join(lines) + "\n"


def build_start_cmd(job_name, jobfile, fio_bin):
    # fio 输出与退出码写在同一个日志里（FIO_RC= 标记），完成判定只看日志，
    # 不依赖独立 rc 文件（避免写 rc 与进程退出之间的竞态）。
    # fio 路径通过位置参数传入 sh -c，避免路径带引号时破坏外层单引号。
    return r'''
tmp=$(mktemp -d /tmp/node_precheck_steady.XXXXXX) || exit 1
cat > "$tmp/{job}.fio" <<'JOB_EOF'
{jobfile}
JOB_EOF
nohup sh -c '"$1" "$2" > "$3" 2>&1; echo "FIO_RC=$?" >> "$3"' fio-runner {fio} "$tmp/{job}.fio" "$tmp/{job}.log" >/dev/null 2>&1 &
pid=$!
sleep 1
kill -0 $pid 2>/dev/null
echo PID=$pid
echo REMOTE_DIR=$tmp
exit 0
'''.format(job=job_name, jobfile=jobfile, fio=shell_quote(fio_bin))


def start_remote_fio(ssh, fio_bin, job_name, jobfile):
    cmd = build_start_cmd(job_name, jobfile, fio_bin)
    code, out, err = ssh.run(cmd, timeout=60)
    pid = ""
    remote_dir = ""
    for line in (out or "").splitlines():
        if line.startswith("PID="):
            pid = line.split("=", 1)[1].strip()
        elif line.startswith("REMOTE_DIR="):
            remote_dir = line.split("=", 1)[1].strip()
    if code != 0 or not pid or not remote_dir:
        raise RuntimeError("远端启动 fio 失败 {0}: {1}".format(job_name, err or out))
    return pid, remote_dir


def fetch_log_tail(ssh, remote_dir, job_name):
    code, out, err = ssh.run("tail -c 8192 {0}/{1}.log 2>/dev/null || true".format(shell_quote(remote_dir), job_name), timeout=60)
    return out or ""


def fetch_fio_logs(ssh, remote_dir, local_dir, file_name):
    """Fetch one fio output log and return its text; empty on failure."""
    remote_path = "{0}/{1}".format(remote_dir, file_name)
    local_path = os.path.join(local_dir, file_name)
    try:
        ssh.fetch_file(remote_path, local_path, timeout=120)
        with open(local_path, "r") as fh:
            return fh.read()
    except Exception:
        return ""


def save_local_file(local_dir, file_name, content):
    """Write a generated artifact (e.g. jobfile) into the local log dir for traceability."""
    try:
        with open(os.path.join(local_dir, file_name), "w") as fh:
            fh.write(content)
    except Exception:
        pass


def wait_remote_fio(ssh, pid, log_dir, job_name, timeout_seconds, verbose=False):
    """轮询 fio 输出日志直到结束（出现 FIO_RC= 标记）或进程消失。

    log_dir 为 fio stdout 日志所在目录（tmp_dir）。返回 (rc, 日志尾部)；
    进程异常退出（无完成标记）时 rc 为 None。
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        tail = fetch_log_tail(ssh, log_dir, job_name)
        match = re.search(r"FIO_RC=(\d+)", tail)
        if match:
            return int(match.group(1)), tail
        if not process_alive(ssh, pid):
            return None, tail
        if verbose and tail.strip():
            lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
            log_stage(ssh.node.mgmt_ip, "{0} 运行中: {1}".format(job_name, (lines[-1] if lines else "")[:120]))
        time.sleep(POLL_INTERVAL)
    kill_remote(ssh, pid)
    raise RuntimeError("{0} 超时（{1}s），已 kill 远端进程".format(job_name, timeout_seconds))


def process_alive(ssh, pid):
    if not pid:
        return False
    code, out, err = ssh.run("kill -0 {0} 2>/dev/null; echo alive=$?".format(pid), timeout=30)
    return "alive=0" in (out or "")


def kill_remote(ssh, pid):
    try:
        ssh.run("kill -9 {0} 2>/dev/null || true".format(pid), timeout=30)
    except Exception:
        pass


def run_stage_fill(ssh, fio_bin, ip, disk_path, model, remote_base, status, local_dir, timeout, verbose):
    key = "fill"
    state, entry = stage_state(status, key)
    base = job_base_name(ip, disk_path, model)
    if state == "done" and entry.get("result"):
        log_stage(ip, "fill 已完成，跳过")
        return entry["result"]
    remote_dir = entry.get("remote_dir") if state == "running" else None
    pid = entry.get("pid") if state == "running" else None
    tmp_dir = entry.get("tmp_dir") if state == "running" else None
    rc = None
    fio_text = ""
    if state == "running":
        rc, tail = wait_remote_fio(ssh, pid, tmp_dir, "precond", FILL_TIMEOUT_SECONDS, verbose=verbose)
        if rc is None:
            log_stage(ip, "fill 上次进程异常退出（无完成标记），重新启动")
    if rc is None:
        remote_dir = os.path.join(remote_base, base + "-precond")
        ensure_remote_dir(ssh, remote_dir)
        jobfile = build_fill_job_file(base, disk_path, remote_base, remote_dir)
        save_local_file(local_dir, "precond.fio", jobfile)
        pid, tmp_dir = start_remote_fio(ssh, fio_bin, "precond", jobfile)
        mark_stage(status, key, "running", remote_dir=remote_dir, tmp_dir=tmp_dir, pid=pid)
        save_status(os.path.join(local_dir, "steady_status.json"), status)
        log_stage(ip, "fill 启动 pid={0}".format(pid))
        rc, tail = wait_remote_fio(ssh, pid, tmp_dir, "precond", FILL_TIMEOUT_SECONDS, verbose=verbose)
    if rc is None:
        raise RuntimeError("fill 进程异常退出（无完成标记），请重新运行续跑")
    fio_text = fetch_fio_logs(ssh, tmp_dir, local_dir, "precond.log")
    # fio 3.19+ 按日志类型追加后缀；per_job_logs=0 时无 ".N" 索引（否则是 "xxx.log_xxx.1.log"）
    bw_text = fetch_fio_logs(ssh, remote_dir, local_dir, "precond_bw.log_bw.log")
    lat_text = fetch_fio_logs(ssh, remote_dir, local_dir, "precond_lat.log_clat.log")
    result = parse_fill_result(bw_text, lat_text, fio_text, rc)
    cleanup_remote_dir(ssh, remote_dir)
    cleanup_remote_dir(ssh, tmp_dir)
    if rc != 0:
        result["status"] = "failed"
        result["error"] = "fio failed rc={0}".format(rc)
    else:
        result["status"] = "done"
    mark_stage(status, key, "done", result=result, rc=rc)
    return result


def run_stage_model(ssh, fio_bin, ip, disk_path, model, m, remote_base, status, local_dir, timeout, verbose):
    key = m["key"]
    state, entry = stage_state(status, key)
    base = job_base_name(ip, disk_path, model)
    if state == "done" and entry.get("result"):
        log_stage(ip, "{0} 已完成，跳过".format(key))
        return entry["result"]
    remote_dir = entry.get("remote_dir") if state == "running" else None
    pid = entry.get("pid") if state == "running" else None
    tmp_dir = entry.get("tmp_dir") if state == "running" else None
    rc = None
    if state == "running":
        rc, _ = wait_remote_fio(ssh, pid, tmp_dir, key, model_timeout(), verbose=verbose)
        if rc is None:
            log_stage(ip, "{0} 上次进程异常退出（无完成标记），重新启动".format(key))
    if rc is None:
        remote_dir = os.path.join(remote_base, base + "-" + key)
        ensure_remote_dir(ssh, remote_dir)
        jobfile = build_model_job_file(base, disk_path, m, remote_dir)
        save_local_file(local_dir, "{0}.fio".format(key), jobfile)
        pid, tmp_dir = start_remote_fio(ssh, fio_bin, key, jobfile)
        mark_stage(status, key, "running", remote_dir=remote_dir, tmp_dir=tmp_dir, pid=pid)
        save_status(os.path.join(local_dir, "steady_status.json"), status)
        log_stage(ip, "{0} 启动 pid={1}".format(key, pid))
        rc, _ = wait_remote_fio(ssh, pid, tmp_dir, key, model_timeout(), verbose=verbose)
    if rc is None:
        raise RuntimeError("{0} 进程异常退出（无完成标记），请重新运行续跑".format(key))
    fio_text = fetch_fio_logs(ssh, tmp_dir, local_dir, "{0}.log".format(key))
    local_name = "{0}-{1}".format(job_base_name(ip, disk_path, model), key)
    result = parse_model_result(ssh, remote_dir, local_dir, local_name, m, rc, fio_text)
    cleanup_remote_dir(ssh, remote_dir)
    cleanup_remote_dir(ssh, tmp_dir)
    if rc != 0:
        result["status"] = "failed"
        result["error"] = "fio failed rc={0}".format(rc)
    else:
        result["status"] = "done"
    mark_stage(status, key, "done", result=result, rc=rc)
    return result


def model_timeout():
    runtime = SHORT_RUNTIME if SHORT_MODE else STEADY_RUNTIME
    return max(MODEL_TIMEOUT_SECONDS, runtime + STEADY_RAMP_TIME + 600)


def cleanup_remote_dir(ssh, remote_dir):
    try:
        ssh.run("rm -rf {0}".format(shell_quote(remote_dir)), timeout=30)
    except Exception:
        pass


def parse_fill_result(bw_text, lat_text, fio_text, rc):
    bw_points = parse_timeseries_text(bw_text, True)
    lat_points = parse_timeseries_text(lat_text, False)
    for point in lat_points:
        point["value"] = point["value"] / 1000000.0  # ns -> ms
    merged = {}
    for point in aggregate_timeseries(bw_points, mode="sum"):
        merged.setdefault(point["time"], {"time": point["time"]})["bw_mb_s"] = point["value"]
    for point in aggregate_timeseries(lat_points, mode="avg"):
        merged.setdefault(point["time"], {"time": point["time"]})["lat_ms"] = point["value"]
    series = [merged[t] for t in sorted(merged)]
    bw_values = [p["bw_mb_s"] for p in series if "bw_mb_s" in p]
    lat_values = [p["lat_ms"] for p in series if "lat_ms" in p]
    _, sum_bw_mib = parse_fio_summary(fio_text)
    avg_bw = sum_bw_mib if sum_bw_mib is not None else (sum(bw_values) / len(bw_values) if bw_values else 0.0)
    avg_lat = sum(lat_values) / len(lat_values) if lat_values else 0.0
    total_gb = parse_io_size_gb(fio_text)
    duration_s = len(series)
    return {"status": "running", "series": series, "avg_bw_mb_s": avg_bw, "avg_lat_ms": avg_lat, "total_gb": total_gb, "duration_s": duration_s, "error": ""}


def parse_io_size_gb(fio_text):
    # fio summary: "WRITE: ... io=7864MiB (8246MB)" or plain "I/O size=..."
    match = re.search(r"\bio=([\d.]+)([KMGTP]?)iB", fio_text or "")
    if not match:
        match = re.search(r"I/O size[^0-9]*([\d.]+)\s*([KMGTP]?)iB", fio_text or "")
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = match.group(2)
    scale = {"": 1.0, "K": 1.0 / 1024 / 1024, "M": 1.0 / 1024, "G": 1.0, "T": 1024.0, "P": 1024.0 * 1024.0}
    return value * scale.get(unit, 1.0)


def parse_fio_summary(fio_text):
    """从 fio 汇总行提取聚合 IOPS 与带宽（统一转 MiB/s）。

    fio 3.x 汇总行形如:
      "  write: IOPS=12.5k, BW=1560MiB/s (1636MB/s)(914GiB/600002msec); 0 zone resets"
      "  read:  IOPS=80.7k, BW=9.86GiB/s (10.6GB/s)(5914GiB/600001msec)"
    IOPS 的 k/M 为十进制；BW 为二进制单位，统一换算成 MiB/s。
    找不到时返回 (None, None)。
    """
    if not fio_text:
        return None, None
    iops = bw_mib = None
    for line in (fio_text or "").splitlines():
        if "IOPS=" not in line or "BW=" not in line:
            continue
        match = re.search(r"IOPS=([\d.]+)([kM]?)", line)
        if match:
            iops = float(match.group(1))
            if match.group(2) == "k":
                iops *= 1000.0
            elif match.group(2) == "M":
                iops *= 1000000.0
        match = re.search(r"BW=([\d.]+)([KMGT]?)iB/s", line)
        if match:
            bw_mib = float(match.group(1))
            bw_mib *= {"K": 1.0 / 1024.0, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024.0}.get(match.group(2), 1.0)
    return iops, bw_mib


def parse_model_result(ssh, remote_dir, local_dir, local_name, m, rc, fio_text):
    log_base = os.path.join(remote_dir, local_name + "_" + "{0}_{1}_{2}".format(m["bs"], m["numjobs"], m["iodepth"]))
    # fio 3.19+ 按日志类型追加后缀：iops.log -> iops.log_iops.log，lat 取 clat，hist 取 clat 直方图
    iops_text = fetch_remote_file(ssh, log_base + ".iops.log_iops.log", os.path.join(local_dir, local_name + ".iops.log"))
    bw_text = fetch_remote_file(ssh, log_base + ".bw.log_bw.log", os.path.join(local_dir, local_name + ".bw.log"))
    lat_text = fetch_remote_file(ssh, log_base + ".lat.log_clat.log", os.path.join(local_dir, local_name + ".lat.log"))
    hist_text = fetch_remote_file(ssh, log_base + ".hist.log_clat_hist.log", os.path.join(local_dir, local_name + ".hist.log"))

    iops_points = parse_timeseries_text(iops_text, False)
    bw_points = parse_timeseries_text(bw_text, True)
    lat_points = parse_timeseries_text(lat_text, False)
    for point in lat_points:
        point["value"] = point["value"] / 1000000.0  # ns -> ms
    series = merge_series(iops_points, bw_points, lat_points)
    pct_curve = parse_hist_text(hist_text, m["rw"])

    p99_us, p999_us, p9999_us = parse_clat_percentiles(fio_text)
    if p99_us is None and pct_curve:
        last = pct_curve[-1]
        p99_us = last.get("p99")
        p999_us = last.get("p999")
        p9999_us = last.get("p9999")

    iops_vals = [p["iops"] for p in series if "iops" in p]
    bw_vals = [p["bw_mb_s"] for p in series if "bw_mb_s" in p]
    lat_vals = [p["lat_ms"] for p in series if "lat_ms" in p]
    sum_iops, sum_bw_mib = parse_fio_summary(fio_text)
    avg_iops = sum_iops if sum_iops is not None else (sum(iops_vals) / len(iops_vals) if iops_vals else 0.0)
    avg_bw = sum_bw_mib if sum_bw_mib is not None else (sum(bw_vals) / len(bw_vals) if bw_vals else 0.0)
    avg_lat = sum(lat_vals) / len(lat_vals) if lat_vals else 0.0
    return {
        "key": m["key"],
        "status": "failed" if rc != 0 else "done",
        "series": series,
        "pct_curve": pct_curve,
        "avg_iops": avg_iops,
        "avg_bw_mb_s": avg_bw,
        "avg_lat_ms": avg_lat,
        "p99_us": p99_us,
        "p999_us": p999_us,
        "p9999_us": p9999_us,
        "duration_s": len(series),
        "fio_rc": rc,
        "error": "",
    }


def fetch_remote_file(ssh, remote_path, local_path):
    try:
        ssh.fetch_file(remote_path, local_path, timeout=120)
        with open(local_path, "r") as fh:
            return fh.read()
    except Exception:
        return ""


def parse_timeseries_text(text, bandwidth):
    points = []
    for line in (text or "").splitlines():
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            timestamp = float(parts[0])
            value = float(parts[1])
        except (ValueError, TypeError):
            continue
        if bandwidth:
            value = value / 1024.0
        points.append({"time": timestamp, "value": value})
    return points


def _window_key(timestamp, window_ms):
    return int(round(float(timestamp) / window_ms)) * window_ms


def aggregate_timeseries(points, window_ms=STEADY_LOG_AVG_MSEC, mode="sum"):
    """Group raw per-job log lines into per-window values.

    fio 在 per_job_logs=0 + 多 job 时，每个 log_avg_msec 窗口每个 job 写一行，
    各 job 的时间戳只差几 ms。必须按窗口分桶后求和（IOPS/带宽）或求均值（时延），
    否则只取到单个 job 的贡献，曲线会出现毛刺且数值偏小约 1/numjobs。
    """
    buckets = {}
    counts = {}
    for point in points:
        key = _window_key(point["time"], window_ms)
        buckets[key] = buckets.get(key, 0.0) + point["value"]
        counts[key] = counts.get(key, 0) + 1
    if mode == "avg":
        return [{"time": key, "value": buckets[key] / counts[key]} for key in sorted(buckets)]
    return [{"time": key, "value": buckets[key]} for key in sorted(buckets)]


def merge_series(iops_points, bw_points, lat_points):
    merged = {}
    for point in aggregate_timeseries(iops_points, mode="sum"):
        merged.setdefault(point["time"], {"time": point["time"]})["iops"] = point["value"]
    for point in aggregate_timeseries(bw_points, mode="sum"):
        merged.setdefault(point["time"], {"time": point["time"]})["bw_mb_s"] = point["value"]
    for point in aggregate_timeseries(lat_points, mode="avg"):
        merged.setdefault(point["time"], {"time": point["time"]})["lat_ms"] = point["value"]
    return [merged[t] for t in sorted(merged)]


def hist_bucket_upper_us(bucket):
    # fio 3.19+ 直方图桶 index(0..1855)：group=index>>6, sub=index&63，
    # 桶覆盖 [2^(group+5)*(1+sub/64), 2^(group+5)*(1+(sub+1)/64)) ns，返回上界 usec。
    group = bucket >> FIO_IO_U_PLAT_BITS
    sub = bucket & (FIO_IO_U_PLAT_VAL - 1)
    base = 1 << (group + (FIO_IO_U_PLAT_BITS - 1))
    upper_ns = base * (FIO_IO_U_PLAT_VAL + sub + 1) / float(FIO_IO_U_PLAT_VAL)
    return upper_ns / 1000.0


def parse_hist_text(text, rw):
    """Parse per-window P99/P999/P9999 curves from the fio histogram log.

    fio 每个窗口每个 job 写一行直方图；按窗口把各 job 的桶计数求和后，再计算分位数。
    """
    want_dir = "1" if rw == "randwrite" else "0"
    windows = {}
    for raw in (text or "").splitlines():
        parts = raw.split(",")
        if len(parts) < 4:
            continue
        # 字段布局: time, direction, blocksize, bucket[0..1855]
        if parts[1].strip() != want_dir:
            continue
        try:
            timestamp = float(parts[0])
            buckets = [int(p) for p in parts[3:]]
        except (ValueError, TypeError):
            continue
        key = _window_key(timestamp, STEADY_LOG_AVG_MSEC)
        agg = windows.get(key)
        if agg is None:
            agg = [0] * FIO_IO_U_PLAT_NR
            windows[key] = agg
        for idx, count in enumerate(buckets):
            if idx < len(agg):
                agg[idx] += count
    pct_points = []
    for key in sorted(windows):
        agg = windows[key]
        total = sum(agg)
        if total <= 0:
            continue
        cumulative = 0
        p99 = p999 = p9999 = None
        for idx, count in enumerate(agg):
            cumulative += count
            if p99 is None and cumulative >= total * 0.99:
                p99 = hist_bucket_upper_us(idx)
            if p999 is None and cumulative >= total * 0.999:
                p999 = hist_bucket_upper_us(idx)
            if p9999 is None and cumulative >= total * 0.9999:
                p9999 = hist_bucket_upper_us(idx)
            if p99 is not None and p999 is not None and p9999 is not None:
                break
        pct_points.append({"time": key, "p99": p99, "p999": p999, "p9999": p9999})
    return pct_points


def parse_clat_percentiles(fio_text):
    if not fio_text:
        return None, None, None
    # fio summary: " 99.00th=[ 123] 99.90th=[ 456] 99.99th=[ 789]" (usec, no unit suffix)
    p99 = p999 = p9999 = None
    for line in (fio_text or "").splitlines():
        if "99.00th=" in line:
            match = re.search(r"99\.00th=\[ *([\d.]+)", line)
            if match:
                p99 = float(match.group(1))
        if "99.90th=" in line:
            match = re.search(r"99\.90th=\[ *([\d.]+)", line)
            if match:
                p999 = float(match.group(1))
        if "99.99th=" in line:
            match = re.search(r"99\.99th=\[ *([\d.]+)", line)
            if match:
                p9999 = float(match.group(1))
    return p99, p999, p9999


def write_steady_report(report, log_root):
    if not report or not report.get("disks"):
        return
    timestamp_path = os.path.join(log_root, "steady_perf_report_{0}.html".format(time.strftime("%Y%m%d_%H%M%S")))
    generate_steady_perf_html(report, timestamp_path)
    generate_steady_perf_html(report, os.path.join(log_root, STEADY_PERF_REPORT))
    generate_steady_perf_html(report, STEADY_PERF_REPORT)


def print_steady_summary(results):
    print("\n========== 稳态性能测试汇总 ==========")
    for result in results:
        report = result.get("report") or {}
        meta = report.get("meta", {})
        print("- 节点: {0} 磁盘: {1} 型号: {2}".format(result.get("ip"), result.get("disk"), meta.get("disk_model")))
        print("  - 状态: {0}".format(result.get("status")))
        for error in result.get("errors", []):
            print("  - 错误: {0}".format(error))
        fill = report.get("fill", {})
        if fill:
            print("  - 预埋: {0} 写满量={1} GB 平均带宽={2} MB/s 平均时延={3} ms".format(
                fill.get("status"), fmt_num(fill.get("total_gb")), fmt_num(fill.get("avg_bw_mb_s")), fmt_num(fill.get("avg_lat_ms"))))
        for m in report.get("models", []):
            line = "  - {0}: {1}".format(MODEL_LABELS.get(m.get("key"), m.get("key")), m.get("status"))
            if m.get("status") == "done":
                line += " iops={0} bw={1} MB/s lat={2} ms p99={3} us p999={4} us p9999={5} us".format(
                    fmt_num(m.get("avg_iops")), fmt_num(m.get("avg_bw_mb_s")), fmt_num(m.get("avg_lat_ms")),
                    fmt_num(m.get("p99_us")), fmt_num(m.get("p999_us")), fmt_num(m.get("p9999_us")))
            print(line)
    print("- HTML报告: {0}".format(STEADY_PERF_REPORT))


def fmt_num(value):
    if value is None:
        return "-"
    return "{0:,.2f}".format(value) if isinstance(value, float) else str(value)
