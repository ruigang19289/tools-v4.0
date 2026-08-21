#!/usr/bin/env python3
"""Storage node pre-check tool entrypoint."""

from __future__ import print_function

import argparse
import concurrent.futures
import os
import sys

from node_precheck.checks import run_node
from node_precheck.common import PASS
from node_precheck.common import DEFAULT_PARALLEL, DEFAULT_TIMEOUT, CATEGORY_ORDER, FAIL, ERROR
from node_precheck.config import parse_conf
from node_precheck.disk_perf import DISK_PERF_REPORT, run_node_disk_perf, write_disk_perf_report
from node_precheck.dry_run import run_node_dry_run, run_node_disk_perf_dry_run, run_network_perf_dry_run
from node_precheck.network_perf import run_network_perf
from node_precheck.inventory import collect_node, collect_local_node, inventory_rows, write_csv
import time
from node_precheck.report_console import print_report, print_remediation_table, print_reboot_summary, print_table, print_multiline_table


# inventory detail rows are rendered separately to keep one disk/NIC per line.
from node_precheck.remediation import (
    add_performance_recommendations, build_plan, ExecutionLogger, execute_node, latest_plan_path, load_plan, plan_by_node,
    plan_log_dir, reboot_nodes, update_verification, validate_plan_nodes,
    write_artifacts,
)


REMEDIATION_CATEGORIES = ["bios", "os", "hardware", "network", "disk"]


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Storage node pre-check tool")
    parser.add_argument("--conf", help="node config file: mgmt_ip root ssh_password ipmi_ip ipmi_user ipmi_password role")
    parser.add_argument("--local", action="store_true", help="collect this host locally; use only with --collect-info")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="SSH/command timeout seconds")
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL, help="node parallelism, default 10")
    parser.add_argument("--verbose", action="store_true", help="show progress details")
    parser.add_argument("--disk-perf", action="store_true", help="alias for --only disk-perf")
    parser.add_argument("--steady-perf", action="store_true", help="run single-disk steady-state performance test (destructive: blkdiscard + full-disk fill + 4x1h models)")
    parser.add_argument("--steady-node", help="node mgmt IP for --steady-perf (default: first storage node)")
    parser.add_argument("--steady-disk", help="target disk for --steady-perf, e.g. /dev/nvme1n1")
    parser.add_argument("--dry-run", action="store_true", help="simulate checks without connecting to nodes or running perf tests")
    parser.add_argument("--only", help="run selected items, comma separated: bios,os,hardware,network,disk,disk-perf,network-perf,steady-perf")
    parser.add_argument("--apply-remediation", action="store_true", help="load latest remediation plan, confirm interactively, and apply supported remediation")
    parser.add_argument("--collect-info", action="store_true", help="collect read-only hardware and software inventory from all nodes")
    return parser.parse_args(argv)


def run_parallel(nodes, workers, func):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {}
        for node in nodes:
            future = executor.submit(func, node)
            future_map[future] = node
        for future in concurrent.futures.as_completed(future_map):
            results.append(future.result())
    return results


def confirm_remediation():
    if not sys.stdin.isatty():
        print("整改取消: --apply-remediation 必须在交互终端中确认", file=sys.stderr)
        return False
    print("\n将对本次检查中所有支持自动化整改的失败项执行配置修改。")
    print("不支持自动整改、需人工处理和已跳过的项目不会执行。")
    print("所有需重启项只提示，不会自动重启节点。")
    sys.stdout.write("是否继续执行整改？[y/N]: ")
    sys.stdout.flush()
    return sys.stdin.readline().strip().lower() == "y"


def parse_only_items(value):
    all_items = CATEGORY_ORDER + ["disk-perf", "network-perf", "steady-perf"]
    if not value:
        # 默认不包含 steady-perf：它是破坏性长时测试，需显式指定
        return [item for item in all_items if item != "steady-perf"]
    items = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in items if item not in all_items]
    if invalid:
        raise ValueError("--only 包含非法项: {0}，支持: {1}".format(",".join(invalid), ",".join(all_items)))
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def print_inventory_table(items):
    rows = inventory_rows(items)
    headers = ["检查参数"] + ["{0} ({1})".format(item["node"], item["role"]) for item in items]
    print_multiline_table(headers, [[label] + values for label, values in rows])


def collect_local_inventory(timeout):
    item = collect_local_node(timeout)
    output_path = os.path.abspath("node_precheck_inventory_{0}.csv".format(time.strftime("%Y%m%d_%H%M%S")))
    path = write_csv([item], output_path)
    print_inventory_table([item])
    print("本地信息采集 CSV：{0}".format(path))
    return 0


def collect_inventory(nodes, timeout, workers):
    items = run_parallel(nodes, workers, lambda node: collect_node(node, timeout))
    items = sorted(items, key=lambda item: item.get("node", ""))
    output_path = os.path.abspath("node_precheck_inventory_{0}.csv".format(time.strftime("%Y%m%d_%H%M%S")))
    path = write_csv(items, output_path)
    print_inventory_table(items)
    print("信息采集 CSV：{0}".format(path))
    return 0


def apply_remediation(nodes, timeout, workers, verbose):
    latest_path = latest_plan_path()
    if not os.path.exists(latest_path):
        print("整改计划不存在: {0}。请先执行全量检查或 --only 检查。".format(latest_path), file=sys.stderr)
        return 2
    try:
        plan = load_plan(latest_path)
        validate_plan_nodes(plan, nodes)
    except RuntimeError as exc:
        print("整改计划错误: {0}".format(exc), file=sys.stderr)
        return 2

    try:
        log_dir = plan_log_dir(plan, time.strftime("%Y%m%d_%H%M%S"))
    except RuntimeError as exc:
        print("整改计划错误: {0}。请先重新执行检查。".format(exc), file=sys.stderr)
        return 2

    print_remediation_table(plan)
    if not confirm_remediation():
        print("整改已取消，未修改节点配置。")
        return 0

    entries_by_node = plan_by_node(plan)
    logger = ExecutionLogger(log_dir)
    run_parallel(
        nodes,
        workers,
        lambda node: execute_node(node, entries_by_node.get(node.mgmt_ip, []), timeout, logger),
    )
    affected_categories = sorted(set([
        entry["category"] for entry in plan["entries"]
        if entry["status"] in ("APPLIED", "VERIFIED")
    ]))
    if affected_categories:
        verified_reports = run_parallel(
            nodes,
            workers,
            lambda node: run_node(node, nodes, timeout, affected_categories, verbose),
        )
        update_verification(plan, verified_reports)

    paths = write_artifacts(plan, log_dir, latest_path)
    print_remediation_table(plan)
    print_reboot_summary(reboot_nodes(plan))
    print("整改执行日志：{0}".format(os.path.join(log_dir, "remediation_execution.log")))
    return 0


def main(argv):
    args = parse_args(argv)
    if args.local:
        if not args.collect_info or args.conf or args.dry_run or args.disk_perf or args.only or args.apply_remediation or args.steady_perf or args.steady_node or args.steady_disk:
            print("配置错误: --local 只能单独与 --collect-info 一起使用，且不需要 --conf", file=sys.stderr)
            return 2
        return collect_local_inventory(args.timeout)
    if not args.conf:
        print("配置错误: 非 --local 模式必须指定 --conf", file=sys.stderr)
        return 2
    try:
        config = parse_conf(args.conf, allow_compute_only=True)
        nodes = config.nodes
        if not [node for node in nodes if node.role == "storage"] and not args.collect_info:
            print("配置中只有 compute 节点，跳过不支持的存储节点配置检查。")
            return 0
        storage_nodes = [node for node in nodes if node.role == "storage"]
        compute_nodes = [node for node in nodes if node.role == "compute"]
    except ValueError as exc:
        print("配置错误: {0}".format(exc), file=sys.stderr)
        return 2

    workers = args.parallel if args.parallel > 0 else DEFAULT_PARALLEL
    if args.apply_remediation and (args.dry_run or args.disk_perf or args.only or args.collect_info):
        print("配置错误: --apply-remediation 不能与 --dry-run、--disk-perf、--only 或 --collect-info 一起使用", file=sys.stderr)
        return 2
    if args.collect_info and (args.dry_run or args.disk_perf or args.only):
        print("配置错误: --collect-info 不能与 --dry-run、--disk-perf 或 --only 一起使用", file=sys.stderr)
        return 2
    if args.disk_perf and args.steady_perf:
        print("配置错误: --disk-perf 与 --steady-perf 不能同时使用", file=sys.stderr)
        return 2
    if args.collect_info:
        return collect_inventory(nodes, args.timeout, workers)
    if args.apply_remediation:
        return apply_remediation(storage_nodes, args.timeout, workers, args.verbose)
    if args.disk_perf:
        args.only = "disk-perf"
    if args.steady_perf:
        args.only = "steady-perf"
    try:
        selected = parse_only_items(args.only)
    except ValueError as exc:
        print("配置错误: {0}".format(exc), file=sys.stderr)
        return 2

    normal_items = [item for item in selected if item in CATEGORY_ORDER]
    run_disk_perf_item = "disk-perf" in selected
    run_network_perf_item = "network-perf" in selected
    run_steady_perf_item = "steady-perf" in selected

    if args.dry_run:
        print("DRY-RUN: 不连接节点、不上传工具、不执行 fio/RDMA 性能测试。")

    run_id = time.strftime("%Y%m%d_%H%M%S")
    reports = None
    detail_log = None
    run_log_root = os.path.abspath("node_precheck_logs_{0}".format(run_id))
    if not os.path.exists(run_log_root):
        os.makedirs(run_log_root)
    if normal_items:
        if args.dry_run:
            reports = run_parallel(storage_nodes, workers, lambda node: run_node_dry_run(node, storage_nodes, normal_items, args.verbose))
        else:
            reports = run_parallel(storage_nodes, workers, lambda node: run_node(node, storage_nodes, args.timeout, normal_items, args.verbose))
        if "network" in normal_items and not args.dry_run:
            from node_precheck.checks import run_rdma_cluster_diagnostic
            diag = run_rdma_cluster_diagnostic(storage_nodes[0], nodes, args.timeout, run_log_root, run_id)
            failed_nodes = set(diag.get("failed_nodes", []))
            for report in reports:
                node_failed = report.node.mgmt_ip in failed_nodes
                if diag["status"] == FAIL:
                    status = FAIL if node_failed else PASS
                    actual = "rdma-diag FAILED on: {0}".format(report.node.mgmt_ip) if node_failed else "rdma-diag passed"
                    reason = diag["reason"] if node_failed else ""
                else:
                    status = diag["status"]
                    actual = diag["actual"]
                    reason = diag["reason"]
                report.add("NET-06", "network", status, actual, diag["expected"], reason, diag["suggestion"])
        detail_log = write_detail_log(reports, run_id)

    remediation_log_dir = run_log_root
    remediation_plan = build_plan(reports, remediation_log_dir) if reports is not None else {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "log_dir": remediation_log_dir,
        "entries": [],
    }
    remediation_paths = None
    rc = 0
    if reports is not None:
        for report in reports:
            for result in report.results:
                if result.status in (FAIL, ERROR):
                    rc = 1
                    break
            if rc:
                break

    perf_results = None
    net_result = None
    if run_disk_perf_item:
        if args.dry_run:
            perf_results, perf_rc = run_disk_perf(storage_nodes, workers, args.timeout, args.verbose, True, run_log_root)
        else:
            perf_results, perf_rc = run_disk_perf(storage_nodes, workers, args.timeout, args.verbose, False, run_log_root)
        if perf_rc != 0:
            rc = perf_rc
    if run_network_perf_item:
        net_result, net_rc = run_net_perf(storage_nodes, compute_nodes, config.stornets, config.ptnets, args.timeout, args.verbose, args.dry_run, run_log_root)
        if net_rc != 0:
            rc = net_rc
    if run_steady_perf_item:
        if args.dry_run:
            print("DRY-RUN: 跳过 steady-perf（破坏性长时测试不做 dry-run 模拟）")
        else:
            from node_precheck.steady_perf import run_steady_perf
            _, steady_rc = run_steady_perf(storage_nodes, args.timeout, args.verbose, args.steady_node, args.steady_disk, run_id, run_log_root)
            if steady_rc != 0:
                rc = steady_rc

    add_performance_recommendations(remediation_plan, perf_results, net_result)
    remediation_paths = write_artifacts(
        remediation_plan,
        remediation_log_dir,
        latest_plan_path(),
    )
    print_final_summary(reports, perf_results, net_result, detail_log)
    return rc


def run_disk_perf(nodes, workers, timeout, verbose, dry_run=False, log_root=None):
    run_id = time.strftime("%Y%m%d_%H%M%S")
    disk_log_root = os.path.join(log_root, "disk_perf") if log_root else os.path.abspath("disk_perf_logs_{0}".format(run_id))
    if not os.path.exists(disk_log_root):
        os.makedirs(disk_log_root)
    if dry_run:
        perf_results = run_parallel(nodes, workers, lambda node: run_node_disk_perf_dry_run(node, timeout, verbose, run_id))
    else:
        perf_results = run_parallel(nodes, workers, lambda node: run_node_disk_perf(node, timeout, verbose, run_id, log_root))
    perf_results = sorted(perf_results, key=lambda item: item.get("ip", ""))
    timestamp_report = os.path.join(disk_log_root, "disk_perf_report_{0}.html".format(run_id))
    write_disk_perf_report(perf_results, timestamp_report)
    write_disk_perf_report(perf_results, os.path.join(disk_log_root, DISK_PERF_REPORT))
    write_disk_perf_report(perf_results, DISK_PERF_REPORT)
    rc = 1 if any(item.get("status") in (FAIL, ERROR) for item in perf_results) else 0
    return perf_results, rc


def run_net_perf(storage_nodes, compute_nodes, stornets, ptnets, timeout, verbose, dry_run=False, log_root=None):
    run_id = time.strftime("%Y%m%d_%H%M%S")
    if dry_run:
        result = run_network_perf_dry_run(storage_nodes, compute_nodes, stornets, ptnets, timeout, verbose, run_id)
    else:
        result = run_network_perf(storage_nodes, compute_nodes, stornets, ptnets, timeout, verbose, run_id, log_root)
    rc = 1 if result.get("status") in (FAIL, ERROR) else 0
    return result, rc


def write_detail_log(reports, run_id):
    log_dir = os.path.abspath("node_precheck_logs_{0}".format(run_id))
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    path = os.path.join(log_dir, "node_precheck_detail.log")
    with open(path, "w") as fh:
        for report in sorted(reports, key=lambda r: r.node.mgmt_ip):
            if report.error:
                fh.write("node={0} error={1}\n".format(report.node.mgmt_ip, report.error))
            for result in report.results:
                fh.write("node={0} check={1} name={2} category={3} status={4} actual={5} expected={6} reason={7} suggestion={8}\n".format(
                    report.node.mgmt_ip, result.check_id, result.name, result.category, result.status,
                    result.actual, result.expected, result.reason, result.suggestion))
    return path


def print_final_summary(reports, perf_results, net_result, detail_log=None):
    print("\n========== 最终汇总 ==========")
    if reports is not None:
        print("\n配置满足项：")
        print_report(reports)
        if detail_log:
            print("详细日志：{0}".format(detail_log))
    if net_result is not None:
        print("\n网络测试通过情况：")
        print("- executor: {0}".format(net_result.get("executor") or "-"))
        print("- status: {0}".format(net_result.get("status")))
        if net_result.get("log_dir"):
            print("- log: {0}".format(net_result.get("log_dir")))
        for item in net_result.get("rounds", []):
            print("- {0}: {1} executor={2}".format(item.get("name"), item.get("status"), item.get("executor")))
    if perf_results is not None:
        print("\n硬盘测试节点通过情况：")
        for item in perf_results:
            line = "- {0}: {1}".format(item.get("ip"), item.get("status"))
            if disk_perf_needs_fio(item):
                line += "  **需要安装 fio**"
            print(line)
        print_disk_perf_failed_disks(perf_results)
        log_dirs = sorted(set([item.get("log_dir") for item in perf_results if item.get("log_dir")]))
        for log_dir in log_dirs:
            print("日志目录：{0}".format(log_dir))
        print("HTML报告：{0}".format(DISK_PERF_REPORT))


def print_disk_perf_failed_disks(perf_results):
    rows = []
    for item in perf_results:
        node = item.get("ip")
        for disk in item.get("disks", []):
            statuses = []
            for model in disk.get("models", {}).values():
                status = model.get("status")
                if status in (FAIL, ERROR) and status not in statuses:
                    statuses.append(status)
            if statuses:
                rows.append("- {0} {1}: {2}".format(node, disk.get("path"), "/".join(statuses)))
    if rows:
        print("\n硬盘异常盘：")
        for row in rows:
            print(row)


def disk_perf_needs_fio(item):
    for error in item.get("errors", []):
        if "需要安装 fio" in error or "未安装 fio" in error:
            return True
    return False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
