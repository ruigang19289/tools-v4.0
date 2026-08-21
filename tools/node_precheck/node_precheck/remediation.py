from __future__ import print_function

"""Isolated remediation planning and execution.

This module consumes existing read-only check reports without changing their data
models or check functions. It owns remediation state, execution, and artifacts.
"""

import base64
import json
import os
import re
import socket
import ssl
import threading
import time

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import HTTPError, URLError, Request, urlopen

from .common import ERROR, FAIL, PASS, WARN, CHECK_NAMES, shell_quote
from .ssh import SSHClient

NOT_REQUIRED = "NOT_REQUIRED"
PLANNED = "PLANNED"
APPLIED = "APPLIED"
VERIFIED = "VERIFIED"
FAILED = "FAILED"
SKIPPED_ERROR = "SKIPPED_ERROR"
MANUAL_REQUIRED = "MANUAL_REQUIRED"
PENDING_REBOOT = "PENDING_REBOOT"

AUTO_HANDLERS = set([
    "BIOS-01", "BIOS-02", "BIOS-03", "BIOS-04", "BIOS-05", "BIOS-06", "BIOS-07", "BIOS-08", "BIOS-09", "BIOS-10", "BIOS-11",
    "BIOS-AMD-01", "BIOS-AMD-02", "BIOS-AMD-03", "BIOS-AMD-04", "BIOS-AMD-05", "BIOS-AMD-06",
    "BIOS-KP-01", "BIOS-KP-02", "BIOS-KP-03", "BIOS-KP-04", "BIOS-KP-05",
    "OS-02", "OS-03", "OS-04", "OS-05", "OS-07", "OS-12", "OS-13", "OS-14", "NET-04",
])

BIOS_REMEDIATION_RULES = {
    "BIOS-01": [
        ("WorkloadProfileConfiguration", "Custom"),
        ("PowerProfile", "Performance"),
        ("ProcessorEppProfile", "Performance"),
    ],
    "BIOS-02": [("PowerPerformanceTuning", "BIOS Controls EPB"), ("PwrPerfTuning", "BIOS Controls EPB")],
    "BIOS-03": [("AltEngPerfBIAS", "Performance"), ("PwrEnergyPerf", "Performance")],
    "BIOS-04": [("HardwarePStates", "Native Mode"), ("ProcessorHWPMEnable", "Native Mode")],
    "BIOS-05": [("TurboMode", "Enabled"), ("TurboModeEnTurboMode", "Enabled")],
    "BIOS-06": [("EnableMonitorMWait", "Enabled"), ("ProcessorMwait", "Enabled"), ("MonitorMWait", "Enabled")],
    "BIOS-07": [("C1E", "Disabled"), ("ProcessorC1E", "Disabled"), ("ProcessorC1eEnable", "Disabled")],
    "BIOS-08": [("CPUC6Report", "Disabled"), ("C6En", "Disabled"), ("C6Enable", "Disabled")],
    "BIOS-10": [("PCIeHotPlug", "Enabled"), ("HotplugEn", "Enabled"), ("SurpriseHotPlugSupport", "Enabled")],
    "BIOS-11": [("CurrentBootMode", "UEFI"), ("BootType", "UEFI")],
    "BIOS-AMD-01": [("OC Mode", "Normal Operation")],
    "BIOS-AMD-02": [("Power Profile Selection", "High Performance Mode"), ("Power/Performance Profile", "CUSTOM")],
    "BIOS-AMD-03": [("GlobalCstateControl", "Disabled")],
    "BIOS-AMD-04": [("IOMMU", "Disabled")],
    "BIOS-AMD-05": [("BiosHotPlugSupport", "Enabled"), ("Hot-Plug Support", "Enabled")],
    "BIOS-AMD-06": [("BootMode", "UEFI"), ("Boot option filter", "Uefi Only")],
    "BIOS-KP-01": [("CustomPowerPolicy", "Performance")],
    "BIOS-KP-02": [("CPUPrefetchConfig", "Enabled")],
    "BIOS-KP-03": [("HotPlug", "Enabled")],
    "BIOS-KP-04": [("EnableSpcr", "Disabled")],
    "BIOS-KP-05": [("EnableSMMU", "Disabled")],
}
MANUAL_CHECKS = set(["OS-01", "OS-06", "OS-08", "OS-09", "OS-10", "OS-11", "NET-05"])

MANUAL_SUGGESTIONS = {
    "HW-01": "人工检查并调整网卡槽位、PCIe 通道和平台 NUMA 配置，使网卡 NUMA 分布均衡。",
    "HW-02": "人工检查并调整 NVMe 盘位、PCIe 通道和平台 NUMA 配置，使硬盘 NUMA 分布均衡。",
    "HW-03": "人工检查网卡插槽、转接卡、线缆、固件和 BIOS PCIe 配置，恢复 PCIe 链路速率。",
    "HW-04": "人工检查 NVMe 盘位、背板、转接卡、固件和 BIOS PCIe 配置，恢复 PCIe 链路速率。",
    "NET-01": "由客户人工安装并加载兼容的 RDMA/OFED 驱动和工具；工具不自动安装驱动。",
    "NET-02": "由客户人工确认兼容性后安装目标 RDMA 驱动版本；工具不自动升级或降级驱动。",
    "NET-03": "人工检查光模块、线缆、交换机端口、物理链路和网卡驱动状态。",
    "DISK-01": "人工确认数据盘规划、硬盘型号和可用性。",
    "DISK-02": "确认数据已备份后，由人工清理 LVM 或数据签名残留；工具不自动擦除磁盘。",
    "DISK-03": "由人工确认业务影响后更换寿命不足的 NVMe 硬盘。",
    "OS-01": "人工分析根目录占用并清理或评估扩容；工具不自动删除文件或扩容。",
    "OS-02": "人工确认 GRUB 配置入口和默认启动项后设置 2M HugePage 参数。",
    "OS-06": "人工配置节点间 root SSH 免密；工具不生成或分发密钥。",
    "OS-08": "由客户按环境规范人工安装 tar。",
    "OS-09": "由客户按环境规范人工安装 rsync。",
    "OS-10": "由客户按环境规范人工安装 dmidecode。",
    "OS-11": "由客户按环境规范人工安装 ipcalc。",
    "OS-13": "人工确认平台 NUMA/SNC/NPS 设置和实际 BIOS 枚举值后调整。",
    "NET-05": "由客户先安装 OFED；确认本地 rdma-tools 工具包含 enable_rocev2.sh 后再处理服务。",
}

BIOS_OS12_RULES = {
    "intel": [
        ("HyperThreading", "Single LP"),
        ("MultiThreaded", "Disabled"),
        ("ProcessorHyperThreadingDisable", "Enabled"),
    ],
    "amd": [("SMTControl", "Disabled"), ("SMT Control", "Disabled")],
}


def _entry(report, result):
    status = NOT_REQUIRED
    supported = False
    suggestion = result.suggestion or MANUAL_SUGGESTIONS.get(result.check_id, "请根据检查结果由人工确认整改方式。")
    if result.status == PASS:
        status = NOT_REQUIRED
    elif result.status == ERROR:
        status = SKIPPED_ERROR
    elif result.check_id == "OS-12" and report.facts.get("platform") not in ("intel", "amd"):
        status = MANUAL_REQUIRED
        suggestion = "ARM/Kunpeng 或未知平台不自动处理超线程 BIOS 配置。"
    elif result.check_id == "OS-13" and report.facts.get("platform") not in ("intel", "amd"):
        status = MANUAL_REQUIRED
        suggestion = "ARM/Kunpeng 或未知平台不自动设置 NUMA 节点数量。"
    elif result.check_id in AUTO_HANDLERS and result.status == FAIL:
        status = PLANNED
        supported = True
    elif result.check_id in MANUAL_CHECKS or result.category in ("bios", "hardware", "disk"):
        status = MANUAL_REQUIRED
    elif result.status in (FAIL, WARN):
        status = MANUAL_REQUIRED
    else:
        status = SKIPPED_ERROR
    return {
        "node": report.node.mgmt_ip,
        "check_id": result.check_id,
        "check_name": result.name,
        "category": result.category,
        "check_status": result.status,
        "supported": supported,
        "status": status,
        "execution_result": _execution_label(status),
        "verification_result": "未复检",
        "requires_reboot": False,
        "actual": result.actual,
        "expected": result.expected,
        "reason": result.reason,
        "suggestion": suggestion,
        "detail": "",
    }


def build_plan(reports, log_dir=None):
    """Return an external remediation plan keyed independently from reports."""
    entries = []
    for report in sorted(reports, key=lambda item: item.node.mgmt_ip):
        for result in report.results:
            entries.append(_entry(report, result))
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "log_dir": os.path.abspath(log_dir) if log_dir else "",
        "entries": entries,
    }


def _execution_label(status):
    labels = {
        NOT_REQUIRED: "无需整改",
        PLANNED: "待执行",
        MANUAL_REQUIRED: "需人工处理",
        SKIPPED_ERROR: "已跳过",
        APPLIED: "成功",
        VERIFIED: "成功",
        PENDING_REBOOT: "成功",
        FAILED: "失败",
    }
    return labels.get(status, "已跳过")


def add_performance_recommendations(plan, perf_results=None, net_result=None):
    """Add manual-only recommendations without changing performance modules."""
    for result in perf_results or []:
        if result.get("status") not in (FAIL, ERROR, WARN):
            continue
        plan["entries"].append({
            "node": result.get("ip", "-"),
            "check_id": "DISK-PERF",
            "check_name": "Disk Performance",
            "category": "disk-perf",
            "check_status": result.get("status"),
            "supported": False,
            "status": MANUAL_REQUIRED,
            "execution_result": "需人工处理",
            "verification_result": "未复检",
            "requires_reboot": False,
            "actual": "; ".join(result.get("errors", [])),
            "expected": "disk performance requirements",
            "reason": "磁盘性能不满足要求或采集异常",
            "suggestion": "检查磁盘型号、固件、RAID 和队列配置",
            "detail": "",
        })
    if net_result and net_result.get("status") in (FAIL, ERROR, WARN):
        plan["entries"].append({
            "node": net_result.get("executor") or "-",
            "check_id": "NETWORK-PERF",
            "check_name": "Network Performance",
            "category": "network-perf",
            "check_status": net_result.get("status"),
            "supported": False,
            "status": MANUAL_REQUIRED,
            "execution_result": "需人工处理",
            "verification_result": "未复检",
            "requires_reboot": False,
            "actual": net_result.get("reason", ""),
            "expected": "network performance requirements",
            "reason": "网络性能不满足要求或采集异常",
            "suggestion": "检查 RDMA 链路、流控、MTU 和网络拓扑",
            "detail": "",
        })
    return plan


def pending_entries(plan):
    return [item for item in plan["entries"] if item["status"] != NOT_REQUIRED]


def plan_by_node(plan):
    grouped = {}
    for entry in plan["entries"]:
        grouped.setdefault(entry["node"], []).append(entry)
    return grouped


class ExecutionLogger(object):
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.nodes_dir = os.path.join(log_dir, "remediation_execution")
        if not os.path.exists(self.nodes_dir):
            os.makedirs(self.nodes_dir)
        self.summary_path = os.path.join(log_dir, "remediation_execution.log")
        self._lock = threading.Lock()

    def write(self, node, check_id, phase, action, result="", code="", stdout="", stderr="", detail="", request="", response=""):
        record = (
            "time={time}\nnode={node}\ncheck={check}\nphase={phase}\naction={action}\n"
            "request={request}\ncode={code}\nstdout={stdout}\nstderr={stderr}\n"
            "response={response}\ndetail={detail}\nresult={result}\n---\n"
        ).format(
            time=time.strftime("%Y-%m-%dT%H:%M:%S"), node=node, check=check_id,
            phase=phase, action=_safe_text(action), request=_safe_text(request), code=code,
            stdout=_safe_text(stdout), stderr=_safe_text(stderr), response=_safe_text(response),
            detail=_safe_text(detail), result=result,
        )
        with self._lock:
            with open(self.summary_path, "a") as fh:
                fh.write(record)
            with open(os.path.join(self.nodes_dir, "{0}.log".format(node)), "a") as fh:
                fh.write(record)


class LoggedSSH(object):
    def __init__(self, ssh, node, check_id, logger):
        self.ssh = ssh
        self.node = node
        self.check_id = check_id
        self.logger = logger

    def run(self, command, timeout=None):
        if self.logger:
            self.logger.write(self.node.mgmt_ip, self.check_id, "ssh_request", "remote command", request=command)
        try:
            code, out, err = self.ssh.run(command, timeout=timeout)
        except Exception as exc:
            if self.logger:
                self.logger.write(self.node.mgmt_ip, self.check_id, "ssh_error", "remote command", result="FAILED", request=command, detail=exc)
            raise
        if self.logger:
            self.logger.write(self.node.mgmt_ip, self.check_id, "ssh_response", "remote command", code=code, request=command, stdout=out, stderr=err, result="SUCCESS" if code == 0 else "FAILED")
        return code, out, err


def execute_node(node, entries, timeout, logger=None):
    """Execute all supported actions for one node serially."""
    executable = [item for item in entries if item["supported"] and item["status"] == PLANNED]
    if not executable:
        return entries
    bios_failed = False
    try:
        with SSHClient(node, timeout) as ssh:
            for item in executable:
                if item["category"] == "bios" or item["check_id"] == "OS-12":
                    if bios_failed:
                        item["status"] = MANUAL_REQUIRED
                        item["execution_result"] = "需人工处理"
                        item["detail"] = "同节点前序 BIOS 整改失败，已停止后续 BIOS 整改"
                        continue
                try:
                    if logger:
                        logger.write(node.mgmt_ip, item["check_id"], "apply", "start remediation")
                    _execute_action(LoggedSSH(ssh, node, item["check_id"], logger), node, timeout, item)
                    if logger:
                        logger.write(node.mgmt_ip, item["check_id"], "complete", "remediation completed", result=item["status"])
                except Exception as exc:
                    item["status"] = FAILED
                    item["execution_result"] = "失败"
                    item["verification_result"] = "未通过"
                    item["detail"] = _safe_text(exc)
                    if logger:
                        logger.write(node.mgmt_ip, item["check_id"], "failed", "remediation failed", result="FAILED", detail=exc)
                    if item["category"] == "bios" or item["check_id"] in ("OS-12", "OS-13"):
                        bios_failed = True
    except Exception as exc:
        for item in executable:
            item["status"] = FAILED
            item["execution_result"] = "失败"
            item["verification_result"] = "未通过"
            item["detail"] = _safe_text(exc)
            if logger:
                logger.write(node.mgmt_ip, item["check_id"], "connect", "SSH remediation connection", result="FAILED", detail=exc)
    return entries


def _execute_action(ssh, node, timeout, item):
    check_id = item["check_id"]
    logger = getattr(ssh, "logger", None)
    if check_id == "BIOS-09":
        _apply_bios_vtd(node, timeout, item, logger)
    elif check_id in BIOS_REMEDIATION_RULES:
        _apply_bios_rule(node, timeout, item, logger)
    elif check_id == "OS-02":
        _set_hugepage_size(ssh, item)
    elif check_id == "OS-12":
        _apply_bios_hyperthreading(ssh, node, timeout, item, logger)
    elif check_id == "OS-13":
        _apply_bios_numa(ssh, node, timeout, item, logger)
    elif check_id == "OS-03":
        _set_selinux_config(ssh, item)
    elif check_id == "OS-04":
        _run_checked(ssh, "setenforce 0", item)
        _verify_command(ssh, "getenforce 2>/dev/null", lambda value: value.lower() in ("permissive", "disabled"), item)
    elif check_id == "OS-05":
        _run_checked(ssh, "systemctl disable --now firewalld", item)
        _verify_command(ssh, "systemctl is-active firewalld 2>/dev/null || true", lambda value: value.lower() in ("inactive", "failed", ""), item)
    elif check_id == "OS-07":
        _set_locale(ssh, item)
    elif check_id == "OS-14":
        _set_numa_balancing(ssh, item)
    elif check_id == "NET-04":
        _set_rdma_mtu(ssh, item)
    else:
        item["status"] = MANUAL_REQUIRED
        item["execution_result"] = "需人工处理"


def _set_hugepage_size(ssh, item):
    """Safely update the default GRUB entry through grubby when available."""
    code, kernel, err = ssh.run("grubby --default-kernel 2>/dev/null", timeout=30)
    kernel = kernel.strip()
    if code != 0 or not kernel or kernel == "(none)":
        item["status"] = MANUAL_REQUIRED
        item["execution_result"] = "需人工处理"
        item["detail"] = "无法通过 grubby 确认默认内核，未修改 GRUB 配置"
        return
    code, info, err = ssh.run("grubby --info {0} 2>/dev/null".format(shell_quote(kernel)), timeout=30)
    if code != 0 or "args=" not in info:
        item["status"] = MANUAL_REQUIRED
        item["execution_result"] = "需人工处理"
        item["detail"] = "无法读取默认内核启动参数，未修改 GRUB 配置"
        return
    # grubby edits the selected default entry and preserves unrelated arguments.
    command = (
        "grubby --update-kernel={kernel} "
        "--remove-args='default_hugepagesz=[^ ]* hugepagesz=[^ ]*' "
        "--args='default_hugepagesz=2M hugepagesz=2M'"
    ).format(kernel=shell_quote(kernel))
    _run_checked(ssh, command, item)
    code, updated, err = ssh.run("grubby --info {0} 2>/dev/null".format(shell_quote(kernel)), timeout=30)
    args = "\n".join([line for line in updated.splitlines() if line.startswith("args=")])
    if code != 0 or args.count("default_hugepagesz=2M") != 1 or args.count("hugepagesz=2M") != 1:
        raise RuntimeError("GRUB HugePage parameters verification failed")
    item["status"] = PENDING_REBOOT
    item["execution_result"] = "成功"
    item["verification_result"] = "等待手动重启"
    item["requires_reboot"] = True


def _redfish_request(node, method, path, timeout, payload=None, logger=None, check_id=""):
    relative_path = path if path.startswith("/") else re.sub(r"^https://[^/]+", "", path)
    url = path if path.startswith("https://") else "https://{0}{1}".format(node.ipmi_ip, path)
    userpass = "{0}:{1}".format(node.ipmi_user, node.ipmi_password).encode("utf-8")
    token = base64.b64encode(userpass).decode("ascii")
    headers = {"Authorization": "Basic {0}".format(token), "Accept": "application/json"}
    data = None
    request_body = json.dumps(payload, sort_keys=True) if payload is not None else ""
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = request_body.encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    context = ssl._create_unverified_context()
    if logger:
        logger.write(node.mgmt_ip, check_id, "redfish_request", "Redfish {0}".format(method), request="{0} {1} {2}".format(method, relative_path, request_body))
    try:
        with urlopen(request, timeout=timeout, context=context) as response:
            body = response.read().decode("utf-8", "replace")
            parsed = json.loads(body) if body.strip() else {}
            if logger:
                logger.write(node.mgmt_ip, check_id, "redfish_response", "Redfish {0}".format(method), code=response.getcode(), response=body, result="SUCCESS")
            return response.getcode(), parsed
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        extended = _extended_info(body)
        if logger:
            logger.write(node.mgmt_ip, check_id, "redfish_response", "Redfish {0}".format(method), code=exc.code, response=json.dumps(extended, ensure_ascii=False), detail=_redfish_error(body), result="FAILED")
        raise RuntimeError("Redfish HTTP {0}: {1}; ExtendedInfo={2}".format(exc.code, _redfish_error(body), json.dumps(extended, ensure_ascii=False)))
    except (URLError, ValueError, ssl.SSLError, socket.timeout) as exc:
        if logger:
            logger.write(node.mgmt_ip, check_id, "redfish_error", "Redfish {0}".format(method), request="{0} {1} {2}".format(method, relative_path, request_body), result="FAILED", detail=exc)
        raise


def _extended_info(body):
    try:
        data = json.loads(body)
        error = data.get("error", data) if isinstance(data, dict) else {}
        details = error.get("@Message.ExtendedInfo", []) if isinstance(error, dict) else []
        return details if isinstance(details, list) else []
    except ValueError:
        return []


def _redfish_error(body):
    try:
        data = json.loads(body)
        error = data.get("error", data)
        details = error.get("@Message.ExtendedInfo", []) if isinstance(error, dict) else []
        messages = [item.get("Message", "") for item in details if isinstance(item, dict)]
        return "; ".join([msg for msg in messages if msg]) or str(error)
    except ValueError:
        return body[:300]


def _apply_bios_rule(node, timeout, item, logger=None):
    rules = BIOS_REMEDIATION_RULES.get(item["check_id"], [])
    attrs, settings_uri = _bios_data(node, timeout, logger, item["check_id"])
    matches = [(key, value) for key, value in rules if key in attrs]
    if not matches:
        raise RuntimeError("BIOS Attribute is absent for {0}".format(item["check_id"]))
    for key, value in matches:
        _patch_bios_setting(node, timeout, settings_uri, key, value, logger, item["check_id"])
    item["status"] = PENDING_REBOOT
    item["execution_result"] = "成功"
    item["verification_result"] = "等待手动重启"
    item["requires_reboot"] = True
    item["detail"] = "Redfish PATCH {0}".format(", ".join(["{0}={1}".format(key, value) for key, value in matches]))


def _bios_data(node, timeout, logger=None, check_id=""):
    code, data = _redfish_request(node, "GET", "/redfish/v1/Systems/1/Bios", timeout, logger=logger, check_id=check_id)
    if code < 200 or code >= 300 or not isinstance(data, dict):
        raise RuntimeError("invalid BIOS GET response")
    attrs = data.get("Attributes")
    if not isinstance(attrs, dict):
        raise RuntimeError("Redfish BIOS response has no writable Attributes")
    settings = data.get("@Redfish.Settings", {})
    settings_object = settings.get("SettingsObject", {}) if isinstance(settings, dict) else {}
    uri = settings_object.get("@odata.id") if isinstance(settings_object, dict) else None
    return attrs, uri or "/redfish/v1/Systems/1/Bios/Settings"


def _patch_bios_attribute(node, timeout, key, value, logger=None, check_id=""):
    attrs, settings_uri = _bios_data(node, timeout, logger, check_id)
    code, response = _redfish_request(
        node, "PATCH", settings_uri, timeout,
        {"Attributes": {key: value}}, logger, check_id,
    )
    if code < 200 or code >= 300:
        raise RuntimeError("Redfish PATCH returned HTTP {0}".format(code))
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(_redfish_error(json.dumps(response)))


def _single_attribute(attrs, candidates):
    matches = [(key, attrs[key]) for key in candidates if key in attrs]
    if len(matches) != 1:
        return None
    return matches[0]


def _bios_active_pending(node, timeout, logger, check_id):
    active, settings_uri = _bios_data(node, timeout, logger, check_id)
    code, pending_data = _redfish_request(node, "GET", settings_uri, timeout, logger=logger, check_id=check_id)
    pending = pending_data.get("Attributes", {}) if isinstance(pending_data, dict) else {}
    if not isinstance(pending, dict):
        pending = {}
    if logger:
        logger.write(
            node.mgmt_ip, check_id, "bios_state", "read Active and Pending BIOS settings",
            stdout="active={0}; pending={1}".format(
                json.dumps({key: active.get(key) for key in ("ExtendedAPIC", "VTdSupport")}),
                json.dumps({key: pending.get(key) for key in ("ExtendedAPIC", "VTdSupport")}),
            ),
        )
    return active, pending, settings_uri


def _patch_bios_setting(node, timeout, settings_uri, key, value, logger, check_id):
    try:
        code, response = _redfish_request(
            node, "PATCH", settings_uri, timeout, {"Attributes": {key: value}}, logger, check_id,
        )
    except socket.timeout:
        # A slow BMC can commit the pending value before its PATCH response times out.
        code, response = _redfish_request(node, "GET", settings_uri, timeout, logger=logger, check_id=check_id)
        pending = response.get("Attributes", {}) if isinstance(response, dict) else {}
        if pending.get(key) == value:
            if logger:
                logger.write(node.mgmt_ip, check_id, "bios_timeout_verify", "Redfish PATCH timeout verified", stdout="{0}={1}".format(key, value), result="SUCCESS")
            return
        raise RuntimeError("Redfish PATCH timed out and pending {0} is not {1}".format(key, value))
    if code < 200 or code >= 300 or (isinstance(response, dict) and response.get("error")):
        raise RuntimeError("Redfish PATCH failed for {0}".format(key))


def _apply_bios_vtd(node, timeout, item, logger=None):
    active, pending, settings_uri = _bios_active_pending(node, timeout, logger, item["check_id"])
    extended = _single_attribute(active, ["ExtendedAPIC"])
    vtd = _single_attribute(active, ["VTdSupport"])
    if not extended:
        item["status"] = MANUAL_REQUIRED
        item["execution_result"] = "需人工处理"
        item["detail"] = "ExtendedAPIC 不存在或存在歧义，未修改 VT-d"
        return
    if not vtd:
        item["status"] = MANUAL_REQUIRED
        item["execution_result"] = "需人工处理"
        item["detail"] = "VTdSupport 不存在或存在歧义"
        return

    extended_key, extended_active = extended
    if str(extended_active).lower() != "disabled" and str(pending.get(extended_key, "")).lower() != "disabled":
        _patch_bios_setting(node, timeout, settings_uri, extended_key, "Disabled", logger, item["check_id"])
    elif logger:
        logger.write(node.mgmt_ip, item["check_id"], "bios_skip", "ExtendedAPIC already disabled or staged", stdout="active={0}; pending={1}".format(extended_active, pending.get(extended_key, "<missing>")))

    # Re-read after the prerequisite patch before staging VT-d.
    active, pending, settings_uri = _bios_active_pending(node, timeout, logger, item["check_id"])
    vtd = _single_attribute(active, ["VTdSupport"])
    if not vtd:
        raise RuntimeError("VTdSupport disappeared after ExtendedAPIC update")
    vtd_key, vtd_active = vtd
    if str(vtd_active).lower() != "disabled" and str(pending.get(vtd_key, "")).lower() != "disabled":
        _patch_bios_setting(node, timeout, settings_uri, vtd_key, "Disabled", logger, item["check_id"])
    elif logger:
        logger.write(node.mgmt_ip, item["check_id"], "bios_skip", "VTdSupport already disabled or staged", stdout="active={0}; pending={1}".format(vtd_active, pending.get(vtd_key, "<missing>")))
    _pending_reboot(item)


def _platform(ssh):
    code, out, err = ssh.run("lscpu 2>/dev/null || true; dmidecode -s processor-manufacturer 2>/dev/null || true", timeout=30)
    text = out.lower()
    if "authenticamd" in text or "amd epyc" in text or "advanced micro devices" in text:
        return "amd"
    if "genuineintel" in text or "intel(r)" in text or "intel corporation" in text:
        return "intel"
    return "unknown"


def _apply_bios_hyperthreading(ssh, node, timeout, item, logger=None):
    platform = _platform(ssh)
    candidates = BIOS_OS12_RULES.get(platform)
    if not candidates:
        raise RuntimeError("platform does not support automatic hyperthreading remediation")
    attrs, _ = _bios_data(node, timeout, logger, item["check_id"])
    matches = [(key, target) for key, target in candidates if key in attrs]
    if len(matches) != 1:
        raise RuntimeError("hyperthreading BIOS Attribute is absent or ambiguous")
    key, target = matches[0]
    _patch_bios_attribute(node, timeout, key, target, logger, item["check_id"])
    _pending_reboot(item)


def _socket_count(ssh):
    code, out, err = ssh.run(
        "lscpu | awk -F: 'tolower($1) ~ /^socket\\(s\\)$/ {gsub(/ /,\"\",$2); print $2; exit}'",
        timeout=30,
    )
    match = re.search(r"^([0-9]+)$", out.strip())
    return int(match.group(1)) if code == 0 and match else None


def _apply_bios_numa(ssh, node, timeout, item, logger=None):
    platform = _platform(ssh)
    if platform == "intel":
        candidates = ["SNC", "Snc", "SncEn"]
        target = "1"
    elif platform == "amd":
        sockets = _socket_count(ssh)
        if sockets == 1:
            target = "NPS2"
        elif sockets == 2:
            target = "NPS1"
        else:
            raise RuntimeError("unsupported AMD CPU socket count for NPS: {0}".format(sockets))
        candidates = ["NUMAnodespersocket", "NUMA nodes per socket"]
    else:
        raise RuntimeError("platform does not support automatic NUMA node remediation")
    attrs, _ = _bios_data(node, timeout, logger, item["check_id"])
    match = _single_attribute(attrs, candidates)
    if not match:
        raise RuntimeError("NUMA BIOS Attribute is absent or ambiguous")
    key, current = match
    _patch_bios_attribute(node, timeout, key, target, logger, item["check_id"])
    _pending_reboot(item)


def _pending_reboot(item):
    item["status"] = PENDING_REBOOT
    item["execution_result"] = "成功"
    item["verification_result"] = "等待手动重启"
    item["requires_reboot"] = True


def _run_checked(ssh, command, item):
    code, out, err = ssh.run(command, timeout=60)
    if code != 0:
        raise RuntimeError(err or out or "remote command failed")
    item["status"] = APPLIED
    item["execution_result"] = "成功"


def _verify_command(ssh, command, predicate, item):
    code, out, err = ssh.run(command, timeout=30)
    value = out.strip()
    if code != 0 or not predicate(value):
        raise RuntimeError(err or out or "verification failed")
    item["status"] = VERIFIED
    item["execution_result"] = "成功"
    item["verification_result"] = "通过"


def _backup_path(path):
    return "{0}.node_precheck.{1}.bak".format(path, time.strftime("%Y%m%d_%H%M%S"))


def _set_selinux_config(ssh, item):
    backup = _backup_path("/etc/selinux/config")
    command = (
        "test -f /etc/selinux/config && cp -a /etc/selinux/config {backup}; "
        "if grep -q '^[[:space:]]*SELINUX=' /etc/selinux/config; then "
        "sed -ri 's/^[[:space:]]*SELINUX=.*/SELINUX=disabled/' /etc/selinux/config; "
        "else printf '\\nSELINUX=disabled\\n' >> /etc/selinux/config; fi"
    ).format(backup=shell_quote(backup))
    _run_checked(ssh, command, item)
    _verify_command(ssh, "grep '^SELINUX=' /etc/selinux/config | tail -1", lambda value: value.lower() == "selinux=disabled", item)
    item["requires_reboot"] = True
    item["status"] = PENDING_REBOOT
    item["verification_result"] = "等待手动重启"


def _set_locale(ssh, item):
    code, out, err = ssh.run("command -v localectl >/dev/null 2>&1", timeout=20)
    if code == 0:
        _run_checked(ssh, "localectl set-locale LANG=en_US.UTF-8", item)
    else:
        backup = _backup_path("/etc/locale.conf")
        command = (
            "test -f /etc/locale.conf && cp -a /etc/locale.conf {backup}; "
            "if test -f /etc/locale.conf && grep -q '^LANG=' /etc/locale.conf; then "
            "sed -ri 's/^LANG=.*/LANG=en_US.UTF-8/' /etc/locale.conf; "
            "else printf 'LANG=en_US.UTF-8\\n' >> /etc/locale.conf; fi"
        ).format(backup=shell_quote(backup))
        _run_checked(ssh, command, item)
    _verify_command(ssh, "grep '^LANG=' /etc/locale.conf 2>/dev/null | tail -1", lambda value: value == "LANG=en_US.UTF-8", item)


def _set_numa_balancing(ssh, item):
    path = "/etc/sysctl.d/99-node-precheck.conf"
    backup = _backup_path(path)
    command = (
        "test -f {path} && cp -a {path} {backup}; "
        "printf 'kernel.numa_balancing = 0\\n' > {path}; "
        "sysctl -w kernel.numa_balancing=0"
    ).format(path=shell_quote(path), backup=shell_quote(backup))
    _run_checked(ssh, command, item)
    _verify_command(ssh, "sysctl -n kernel.numa_balancing", lambda value: value == "0", item)


def _set_rdma_mtu(ssh, item):
    code, ibstat, err = ssh.run("ibstat 2>/dev/null", timeout=30)
    layers = re.findall(r"(?im)^\s*Link layer:\s*(\S+)", ibstat)
    if code != 0 or not layers or len(set(layers)) != 1:
        raise RuntimeError("unable to determine a unique RDMA link layer")
    expected = "4092" if layers[0] == "InfiniBand" else "4200" if layers[0] == "Ethernet" else ""
    if not expected:
        raise RuntimeError("unsupported RDMA link layer: {0}".format(layers[0]))
    code, mapping, err = ssh.run("ibdev2netdev 2>/dev/null", timeout=30)
    interfaces = re.findall(r"==>\s+(\S+)", mapping) if code == 0 else []
    interfaces = sorted(set([name for name in interfaces if re.match(r"^[A-Za-z0-9_.:-]+$", name)]))
    if not interfaces:
        raise RuntimeError("unable to map RDMA devices to interfaces")
    for iface in interfaces:
        _run_checked(ssh, "ip link set dev {0} mtu {1}".format(shell_quote(iface), expected), item)
        _verify_command(ssh, "cat /sys/class/net/{0}/mtu".format(shell_quote(iface)), lambda value: value == expected, item)


def _safe_text(value):
    text = str(value).replace("\n", " ")
    text = re.sub(r"(https?://)[^/@\s]+@", r"\1<credentials-redacted>@", text)
    return text[:500]


def update_verification(plan, reports):
    """Update executed remediation entries from a fresh read-only check run."""
    results = {}
    for report in reports:
        for result in report.results:
            results[(report.node.mgmt_ip, result.check_id)] = result
    for entry in plan["entries"]:
        if entry["status"] in (NOT_REQUIRED, MANUAL_REQUIRED, SKIPPED_ERROR, FAILED, PENDING_REBOOT):
            continue
        result = results.get((entry["node"], entry["check_id"]))
        if result and result.status == PASS:
            entry["status"] = VERIFIED
            entry["execution_result"] = "成功"
            entry["verification_result"] = "通过"
        else:
            entry["status"] = FAILED
            entry["execution_result"] = "失败"
            entry["verification_result"] = "未通过"
            entry["detail"] = result.reason if result else "复检结果缺失"
    return plan


LATEST_PLAN_FILE = "node_precheck_remediation_latest.json"
PLAN_VERSION = 1


def _write_json(path, plan):
    temp_path = path + ".tmp"
    with open(temp_path, "w") as fh:
        json.dump(plan, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(temp_path, path)


def write_artifacts(plan, log_dir, latest_path=None):
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    plan["plan_version"] = PLAN_VERSION
    json_path = os.path.join(log_dir, "remediation_plan.json")
    report_path = os.path.join(log_dir, "remediation_report.txt")
    _write_json(json_path, plan)
    if latest_path:
        _write_json(latest_path, plan)
    with open(report_path, "w") as fh:
        for entry in pending_entries(plan):
            fh.write("{node}\t{check_id}\t{execution}\t{verification}\n".format(
                node=entry["node"], check_id=entry["check_id"],
                execution=entry["execution_result"], verification=entry["verification_result"]))
    return json_path, report_path


def load_plan(path):
    try:
        with open(path, "r") as fh:
            plan = json.load(fh)
    except (IOError, OSError, ValueError) as exc:
        raise RuntimeError("无法读取整改计划: {0}".format(exc))
    if not isinstance(plan, dict) or not isinstance(plan.get("entries"), list):
        raise RuntimeError("整改计划格式无效")
    return plan


def validate_plan_nodes(plan, nodes):
    allowed = set([node.mgmt_ip for node in nodes if node.role == "storage"])
    planned = set([entry.get("node") for entry in plan["entries"] if entry.get("node") and entry.get("node") != "-"])
    missing = sorted(planned - allowed)
    if missing:
        raise RuntimeError("整改计划节点不在当前 storage 配置中: {0}".format(",".join(missing)))
    return True


def latest_plan_path(base_dir=None):
    return os.path.abspath(os.path.join(base_dir or os.getcwd(), LATEST_PLAN_FILE))


def plan_log_dir(plan, fallback_run_id):
    origin = plan.get("log_dir", "")
    base = os.path.abspath(os.getcwd())
    if origin:
        origin = os.path.abspath(origin)
        if origin.startswith(base + os.sep) and os.path.basename(origin).startswith("node_precheck_logs_") and os.path.isdir(origin):
            return origin
        raise RuntimeError("整改计划来源检查目录无效或不存在")
    stamp = re.sub(r"[^0-9]", "", plan.get("generated_at", ""))[:14] or fallback_run_id
    return os.path.abspath("node_precheck_logs_{0}".format(stamp))


def write_artifacts_for_plan(plan, log_dir, latest_path):
    return write_artifacts(plan, log_dir, latest_path)


def reboot_nodes(plan):
    return sorted(set([entry["node"] for entry in plan["entries"] if entry.get("requires_reboot")]))
