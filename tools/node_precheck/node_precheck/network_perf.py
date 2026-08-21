from __future__ import print_function

import concurrent.futures
import os
import re
import time

from .common import PASS, FAIL, ERROR, WARN, shell_quote
from .checks import high_speed_nics, rdma_device_status
from .ssh import SSHClient


class PerfEligibility(object):
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason



RDMA_TOOLS_ARCHIVE = "rdma-tools-latest.tar.gz"


def run_network_perf(storage_nodes, compute_nodes, stornets, ptnets, timeout, verbose, run_id=None, log_root=None):
    if run_id is None:
        run_id = time.strftime("%Y%m%d_%H%M%S")
    result = {"status": PASS, "executor": "", "reason": "", "log_dir": "", "remote_dir": "", "rounds": []}
    archive = os.path.abspath(RDMA_TOOLS_ARCHIVE)
    if not storage_nodes:
        result["status"] = ERROR
        result["reason"] = "配置文件中没有 role=storage 的节点"
        return result

    probe_node = storage_nodes[0]
    with SSHClient(probe_node, timeout) as ssh:
        nics = high_speed_nics(ssh)
        if not nics:
            result["status"] = WARN
            result["reason"] = "未发现 >=25GE 网卡，跳过 RDMA 网络性能测试"
            return result
        rdma_status = rdma_device_status(ssh)
        if rdma_status != "present":
            result["status"] = ERROR
            result["reason"] = ">=25GE 网卡存在，但未发现 RDMA 设备或驱动"
            return result

    if not os.path.exists(archive):
        result["status"] = ERROR
        result["reason"] = "缺少 rdma-tools-latest.tar.gz，请提前准备"
        return result

    has_compute = bool(compute_nodes)
    rounds = build_test_rounds(storage_nodes, compute_nodes)
    result["executor"] = rounds[0]["executor"].mgmt_ip
    local_log_dir = os.path.join(log_root, "network_perf") if log_root else os.path.abspath("rdma_perf_logs_{0}".format(run_id))
    if not os.path.exists(local_log_dir):
        os.makedirs(local_log_dir)

    log_stage(result["executor"], "network-perf start")
    if has_compute:
        first_wave = run_rounds_parallel(rounds[:2], archive, stornets, ptnets, run_id, local_log_dir, timeout, verbose)
        second_wave = [run_network_round(rounds[2], archive, stornets, ptnets, run_id, local_log_dir, timeout, verbose)]
        round_results = first_wave + second_wave
    else:
        round_results = [run_network_round(rounds[0], archive, stornets, ptnets, run_id, local_log_dir, timeout, verbose)]

    write_network_detail_log(local_log_dir, round_results)

    result["rounds"] = round_results
    result["log_dir"] = local_log_dir
    result["remote_dir"] = ";".join([item.get("remote_dir", "") for item in round_results if item.get("remote_dir")])
    result["status"], result["reason"] = summarize_round_results(round_results)
    log_stage(result["executor"], "network-perf done status={0}".format(result["status"]))
    return result


def build_test_rounds(storage_nodes, compute_nodes):
    storage_hosts = [n.mgmt_ip for n in storage_nodes]
    if not compute_nodes:
        return [{
            "name": "storage-internal",
            "executor": storage_nodes[0],
            "hosts": storage_hosts,
            "targets": [],
            "script": "run-all-bw-tests-parallel.sh",
        }]

    compute_hosts = [n.mgmt_ip for n in compute_nodes]
    return [
        {
            "name": "compute-internal",
            "executor": compute_nodes[0],
            "hosts": compute_hosts,
            "targets": [],
            "script": "run-all-bw-tests-parallel.sh",
        },
        {
            "name": "storage-internal",
            "executor": storage_nodes[0],
            "hosts": storage_hosts,
            "targets": [],
            "script": "run-all-bw-tests-parallel.sh",
        },
        {
            "name": "compute-to-storage",
            "executor": compute_nodes[0],
            "hosts": compute_hosts,
            "targets": storage_hosts,
            "script": "run-all-bw-tests-parallel.sh test-rdma-bw-group2group.sh",
        },
    ]


def run_rounds_parallel(rounds, archive, stornets, ptnets, run_id, local_log_dir, timeout, verbose=False):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rounds)) as executor:
        futures = [executor.submit(run_network_round, item, archive, stornets, ptnets, run_id, local_log_dir, timeout, verbose) for item in rounds]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    order = dict((rounds[i]["name"], i) for i in range(len(rounds)))
    return sorted(results, key=lambda item: order.get(item.get("name"), 999))


def run_network_round(round_info, archive, stornets, ptnets, run_id, parent_log_dir, timeout, verbose=False):
    name = round_info["name"]
    executor = round_info["executor"]
    result = {"name": name, "status": PASS, "executor": executor.mgmt_ip, "reason": "", "log_dir": "", "remote_dir": ""}
    local_log_dir = os.path.join(parent_log_dir, name)
    if not os.path.exists(local_log_dir):
        os.makedirs(local_log_dir)

    log_stage(executor.mgmt_ip, "network-perf {0} start".format(name))
    try:
        with SSHClient(executor, timeout) as ssh:
            remote_base = "/tmp/node_precheck_rdma_{0}_{1}".format(run_id, name)
            remote_archive = remote_base + "/rdma-tools-latest.tar.gz"
            log_stage(executor.mgmt_ip, "{0}: prepare remote dir {1}".format(name, remote_base))
            code, out, err = ssh.run("rm -rf {0}; mkdir -p {0}".format(shell_quote(remote_base)), timeout=60)
            if code != 0:
                raise RuntimeError(err or out)

            log_stage(executor.mgmt_ip, "{0}: upload rdma-tools-latest.tar.gz".format(name))
            ssh.put_file(archive, remote_archive, timeout=300)
            log_stage(executor.mgmt_ip, "{0}: extract rdma tools".format(name))
            code, out, err = ssh.run("cd {0} && tar -xzf rdma-tools-latest.tar.gz".format(shell_quote(remote_base)), timeout=120)
            if code != 0:
                raise RuntimeError(err or out)

            rdma_dir = remote_base + "/rdma-tools"
            log_stage(executor.mgmt_ip, "{0}: update rdma config".format(name))
            config_overrides = build_config_overrides(round_info["hosts"], round_info["targets"], stornets, ptnets)
            update_remote_config(ssh, rdma_dir + "/config", config_overrides)

            script = round_info["script"]
            log_stage(executor.mgmt_ip, "{0}: run rdma-perf/{1}".format(name, script))
            run_log = remote_base + "/" + name + ".log"
            cmd = "cd {0}/rdma-perf && ./{1} > {2} 2>&1; echo RC=$?".format(shell_quote(rdma_dir), script, shell_quote(run_log))
            code, out, err = ssh.run(cmd, timeout=3600)
            rc = parse_rc(out)
            script_rcs = [(script, rc)]
            log_stage(executor.mgmt_ip, "{0}: {1} finished rc={2}".format(name, script, rc))

            try:
                ssh.fetch_file(run_log, os.path.join(local_log_dir, name + ".log"), timeout=120)
                ssh.fetch_file(rdma_dir + "/config", os.path.join(local_log_dir, "config"), timeout=120)
            except Exception as exc:
                raise RuntimeError("回传 RDMA 日志失败: {0}".format(exc))

            result["log_dir"] = local_log_dir
            result["remote_dir"] = remote_base
            result["status"], result["reason"] = analyze_rdma_logs(local_log_dir, script_rcs)
            ssh.run("rm -rf {0}".format(shell_quote(remote_base)), timeout=60)
    except Exception as exc:
        result["status"] = ERROR
        result["reason"] = str(exc)

    log_stage(executor.mgmt_ip, "network-perf {0} done status={1}".format(name, result["status"]))
    return result


def summarize_round_results(round_results):
    if not round_results:
        return ERROR, "未执行 RDMA 网络性能测试"
    errors = [item for item in round_results if item.get("status") == ERROR]
    fails = [item for item in round_results if item.get("status") == FAIL]
    if errors:
        return ERROR, "; ".join(["{0}: {1}".format(item.get("name"), item.get("reason")) for item in errors])
    if fails:
        return FAIL, "; ".join(["{0}: {1}".format(item.get("name"), item.get("reason")) for item in fails])
    return PASS, "所有 RDMA 网络性能测试轮次通过"


def write_network_detail_log(local_log_dir, round_results):
    path = os.path.join(local_log_dir, "network_perf_detail.log")
    with open(path, "w") as fh:
        for item in round_results:
            fh.write("round={0} executor={1} status={2} reason={3} log_dir={4} remote_dir={5}\n".format(
                item.get("name"), item.get("executor"), item.get("status"), item.get("reason", ""), item.get("log_dir", ""), item.get("remote_dir", "")))


def log_stage(ip, message, verbose=False):
    print("[{0}] {1}".format(ip, message))


def detect_stornets(ssh):
    cmd = r'''
idx=1
for n in /sys/class/net/*; do
  iface=${n##*/}
  [ "$iface" = "lo" ] && continue
  [ -e "$n/device" ] || continue
  speed=$(cat "$n/speed" 2>/dev/null || echo 0)
  case "$speed" in ''|*[!0-9]*) speed=0 ;; esac
  [ "$speed" -lt 100000 ] && continue
  ip -o -4 addr show dev "$iface" 2>/dev/null | awk '{print $4}' | while read cidr; do
    python3 - <<PY "$cidr" "$idx"
import ipaddress, sys
cidr=sys.argv[1]
idx=sys.argv[2]
net=ipaddress.ip_network(cidr, strict=False)
print('net%s:%s' % (idx, net.with_prefixlen))
PY
    idx=$((idx+1))
  done
done
'''
    code, out, err = ssh.run(cmd, timeout=60)
    seen = []
    for line in out.splitlines():
        line = line.strip()
        if re.match(r"^net\d+:.+/\d+$", line) and line not in seen:
            seen.append(line)
    # Re-number after de-dup because shell subshells can repeat idx.
    renum = []
    for i, item in enumerate(seen, 1):
        renum.append("net{0}:{1}".format(i, item.split(":", 1)[1]))
    return renum


def build_config_overrides(hosts, targets, stornets, ptnets):
    lines = []
    lines.append("HOSTS=(")
    for host in hosts:
        lines.append("    {0}".format(host))
    lines.append(")")
    lines.append("")
    lines.append("TARGETS=(")
    for target in targets:
        lines.append("    {0}".format(target))
    lines.append(")")
    lines.append("")
    lines.append("STORNETS=(")
    for net in stornets:
        lines.append("    {0}".format(net))
    lines.append(")")
    lines.append("")
    lines.append("PTNETS=(")
    for net in ptnets:
        lines.append("    {0}".format(net))
    lines.append(")")
    return "\n".join(lines) + "\n"


def update_remote_config(ssh, remote_config, override_text):
    encoded = override_text.encode("utf-8").hex()
    cmd = "python3 - <<'PY'\nimport binascii, re\npath={0!r}\noverride=binascii.unhexlify({1!r}).decode('utf-8')\ntext=open(path).read()\nfor name in ['HOSTS','TARGETS','STORNETS','PTNETS']:\n    text=re.sub(r'(?ms)^%s=\\(.*?^\\)\\s*' % name, '', text)\nmarker='# node_precheck override begin\\n'\nif marker in text:\n    text=text.split(marker,1)[0].rstrip()+'\\n'\ntext=text.rstrip()+'\\n\\n'+marker+override+'# node_precheck override end\\n'\nopen(path,'w').write(text)\nPY".format(remote_config, encoded)
    code, out, err = ssh.run(cmd, timeout=30)
    if code != 0:
        raise RuntimeError(err or out)


def parse_rc(text):
    m = re.search(r"RC=(\d+)", text or "")
    return int(m.group(1)) if m else 1


def analyze_rdma_logs(local_log_dir, script_rcs):
    texts = []
    for root, dirs, files in os.walk(local_log_dir):
        for name in files:
            if name.endswith(".log"):
                path = os.path.join(root, name)
                try:
                    texts.append(open(path, "r").read())
                except Exception:
                    pass
    text = "\n".join(texts)
    lower = text.lower()
    bad_markers = ["not so good", "less than 80%", "a little bad", "bad", "oops"]
    failed_scripts = ["{0} rc={1}".format(script, rc) for script, rc in script_rcs if rc != 0]
    if failed_scripts:
        return ERROR, "RDMA 测试脚本执行失败: {0}".format("; ".join(failed_scripts))
    for marker in bad_markers:
        if marker in lower:
            return FAIL, "RDMA 测试带宽低于 80%，需要优化: {0}".format(marker)
    if "very good" in lower or "good" in lower:
        return PASS, "RDMA 测试带宽达到 80% 以上"
    return ERROR, "未在 RDMA 测试日志中找到 Good/Very Good 结果，请人工确认"
