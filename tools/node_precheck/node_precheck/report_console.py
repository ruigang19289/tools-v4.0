from __future__ import print_function

from .common import PASS, WARN, FAIL, ERROR, INFO, CHECK_NAMES


def _display_width(value):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in str(value))


def _pad_display(value, width):
    return str(value) + " " * max(0, width - _display_width(value))


def _wrap_display(value, width):
    lines, current, current_width = [], "", 0
    for char in str(value):
        char_width = _display_width(char)
        if current and current_width + char_width > width:
            lines.append(current)
            current, current_width = "", 0
        current += char
        current_width += char_width
    lines.append(current)
    return lines or [""]


def _print_display_row(cells, widths):
    return "|" + "|".join(" " + _pad_display(cell, widths[index]) + " " for index, cell in enumerate(cells)) + "|"


def collect_check_order(reports):
    order = []
    seen = set()
    predefined = [
        "BIOS-ACCESS", "BIOS-PLATFORM", "BIOS-01", "BIOS-02", "BIOS-03", "BIOS-04", "BIOS-05", "BIOS-06", "BIOS-07", "BIOS-08", "BIOS-09", "BIOS-10", "BIOS-11",
        "BIOS-AMD-01", "BIOS-AMD-02", "BIOS-AMD-03", "BIOS-AMD-04", "BIOS-AMD-05", "BIOS-AMD-06",
        "BIOS-KP-01", "BIOS-KP-02", "BIOS-KP-03", "BIOS-KP-04", "BIOS-KP-05",
        "OS-01", "OS-02", "OS-03", "OS-04", "OS-05", "OS-06", "OS-07", "OS-08", "OS-09", "OS-10", "OS-11", "OS-12", "OS-13", "OS-14",
        "HW-01", "HW-02", "HW-03", "HW-04", "NET-00", "NET-01", "NET-02", "NET-03", "NET-04", "NET-05", "DISK-01", "DISK-02", "DISK-03", "NODE-ACCESS",
    ]
    present = set()
    for report in reports:
        for result in report.results:
            present.add(result.check_id)
    for check_id in predefined:
        if check_id in present and check_id not in seen:
            order.append(check_id)
            seen.add(check_id)
    for report in reports:
        for result in report.results:
            if result.check_id not in seen:
                order.append(result.check_id)
                seen.add(result.check_id)
    return order


def print_report(reports):
    reports = sorted(reports, key=lambda r: r.node.mgmt_ip)
    check_order = collect_check_order(reports)
    result_maps = dict((r.node.mgmt_ip, r.result_map()) for r in reports)
    headers = ["Check ID", "Check Name"] + [r.node.mgmt_ip for r in reports]
    rows = []
    for check_id in check_order:
        name = CHECK_NAMES.get(check_id, check_id)
        row = [check_id, name]
        for report in reports:
            result = result_maps[report.node.mgmt_ip].get(check_id)
            row.append(result.status if result else "-")
        rows.append(row)
    print_network_inventory(reports)
    print_table(headers, rows)
    print("")
    print_summary(reports)


def print_remediation_table(plan):
    rows = []
    for entry in plan.get("entries", []):
        if entry.get("status") == "NOT_REQUIRED":
            continue
        rows.append([
            entry.get("node", "-"),
            "{0} {1}".format(entry.get("check_id", "-"), entry.get("check_name", "")),
            entry.get("execution_result", "已跳过"),
            entry.get("verification_result", "未复检"),
        ])
    if not rows:
        return
    print("\n整改结果：")
    print_table(["节点", "检查项目", "执行结果", "复检结果"], rows)


def print_reboot_summary(nodes):
    if not nodes:
        return
    print("\n需要手动重启的节点：{0}".format("、".join(nodes)))
    print("请在维护窗口手动重启上述节点；工具不会自动重启。重启完成后重新运行检查。")


def print_network_inventory(reports):
    rows = []
    for report in reports:
        result = report.result_map().get("NET-00")
        if not result:
            continue
        rows.extend(network_inventory_rows(report.node.mgmt_ip, result))
    if not rows:
        return
    print("万兆网口数量和型号：")
    print_table(["Node", "Status", "Total", "NIC", "Speed(Mb/s)", "Model"], rows)
    print("")


def network_inventory_rows(node_ip, result):
    parts = [p.strip() for p in result.actual.split(";") if p.strip()]
    total = ""
    rows = []
    for part in parts:
        if "网口数量=" in part or "网卡数量=" in part:
            total = part.split("=", 1)[1].strip()
            continue
        nic, speed, model = parse_nic_inventory_part(part)
        rows.append([node_ip, result.status, total, nic, speed, model])
    if rows:
        return rows
    return [[node_ip, result.status, total or "-", "-", "-", result.actual or result.reason]]


def parse_nic_inventory_part(part):
    name = part.split(":", 1)[0].strip() if ":" in part else part
    speed = ""
    model = ""
    for field in part.split():
        if field.startswith("speed="):
            speed = field.split("=", 1)[1].replace("Mb/s", "")
    if "model=" in part:
        model = part.split("model=", 1)[1].strip()
    return name or "-", speed or "-", model or "-"


def print_table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    sep = "+" + "+".join(["-" * (w + 2) for w in widths]) + "+"
    print(sep)
    print("|" + "|".join([" {0:<{1}} ".format(headers[i], widths[i]) for i in range(len(headers))]) + "|")
    print(sep)
    for row in rows:
        print("|" + "|".join([" {0:<{1}} ".format(str(row[i]), widths[i]) for i in range(len(row))]) + "|")
    print(sep)


def print_multiline_table(headers, rows, max_width=42):
    """Print a bounded-width table while preserving one detail item per line."""
    normalized = []
    widths = [min(_display_width(header), max_width) for header in headers]
    for row in rows:
        cells = []
        for index, value in enumerate(row):
            lines = []
            for part in str(value).splitlines() or [""]:
                lines.extend(_wrap_display(part, max_width))
            cells.append(lines)
            widths[index] = min(max(widths[index], max([_display_width(line) for line in lines] or [0])), max_width)
        normalized.append(cells)

    sep = "+" + "+".join(["-" * (width + 2) for width in widths]) + "+"
    print(sep)
    header_cells = [_wrap_display(header, width) for header, width in zip(headers, widths)]
    for line_number in range(max(len(cell) for cell in header_cells)):
        print(_print_display_row([cell[line_number] if line_number < len(cell) else "" for cell in header_cells], widths))
    print(sep)
    for cells in normalized:
        for line_number in range(max(len(cell) for cell in cells)):
            print(_print_display_row([cell[line_number] if line_number < len(cell) else "" for cell in cells], widths))
        print(sep)

def print_summary(reports):
    print("Summary:")
    for report in reports:
        counts = {PASS: 0, WARN: 0, FAIL: 0, ERROR: 0, INFO: 0}
        for result in report.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        final = PASS
        if counts.get(ERROR, 0) > 0:
            final = ERROR
        if counts.get(FAIL, 0) > 0:
            final = FAIL
        elif counts.get(WARN, 0) > 0:
            final = WARN
        print("- {0}: {1} (PASS={2}, WARN={3}, FAIL={4}, ERROR={5})".format(
            report.node.mgmt_ip, final, counts.get(PASS, 0), counts.get(WARN, 0), counts.get(FAIL, 0), counts.get(ERROR, 0)))


def print_details(reports):
    print("")
    print("Details:")
    has_detail = False
    for report in sorted(reports, key=lambda r: r.node.mgmt_ip):
        for result in report.results:
            if result.status in (WARN, FAIL, ERROR):
                has_detail = True
                parts = ["[{0}] {1} {2} {3}".format(result.status, report.node.mgmt_ip, result.check_id, result.name)]
                if result.actual:
                    parts.append("actual={0}".format(result.actual))
                if result.expected:
                    parts.append("expected={0}".format(result.expected))
                if result.reason:
                    parts.append("reason={0}".format(result.reason))
                if result.suggestion:
                    parts.append("suggestion={0}".format(result.suggestion))
                print("; ".join(parts))
    if not has_detail:
        print("- No WARN/FAIL/ERROR details.")
