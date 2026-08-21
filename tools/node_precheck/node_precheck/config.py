from __future__ import print_function

import os

from .common import Node, is_ip


class PrecheckConfig(object):
    def __init__(self, nodes, stornets, ptnets):
        self.nodes = nodes
        self.stornets = stornets
        self.ptnets = ptnets


def parse_conf(path, allow_compute_only=False):
    """Parse configuration, optionally allowing compute-only inventory runs."""
    if not os.path.exists(path):
        raise ValueError("配置文件不存在: {0}".format(path))
    text = open(path, "r").read()
    nodes = parse_nodes_array(text)
    stornets = parse_array(text, "STORNETS")
    ptnets = parse_array(text, "PTNETS")
    if not nodes:
        raise ValueError("配置文件中没有有效节点，请配置 NODES=(...)，每行 7 列")
    if not allow_compute_only and not [n for n in nodes if n.role == "storage"]:
        raise ValueError("至少需要配置一个 role=storage 的节点")
    if not allow_compute_only and not stornets:
        raise ValueError("配置文件缺少 STORNETS，请配置需要测试的 RDMA 存储网段")
    if not allow_compute_only and not ptnets:
        raise ValueError("配置文件缺少 PTNETS，请配置需要测试的 RDMA 网段名称")
    if not allow_compute_only:
        validate_ptnets(stornets, ptnets)
    return PrecheckConfig(nodes, stornets, ptnets)


def parse_nodes_array(text):
    lines = parse_array(text, "NODES")
    nodes = []
    seen = set()
    for idx, line in enumerate(lines, 1):
        parts = line.split()
        if len(parts) != 7:
            raise ValueError("NODES 第 {0} 行格式错误，需要 7 列: {1}".format(idx, line))
        mgmt_ip, ssh_user, ssh_password, ipmi_ip, ipmi_user, ipmi_password, role = parts
        if not is_ip(mgmt_ip):
            raise ValueError("NODES 第 {0} 行管理 IP 非法: {1}".format(idx, mgmt_ip))
        if role not in ("storage", "compute"):
            raise ValueError("NODES 第 {0} 行 role 非法: {1}，仅支持 storage/compute".format(idx, role))
        if role == "storage" and not is_ip(ipmi_ip):
            raise ValueError("NODES 第 {0} 行 IPMI IP 非法: {1}".format(idx, ipmi_ip))
        if ssh_user != "root":
            raise ValueError("NODES 第 {0} 行 SSH 用户必须为 root: {1}".format(idx, ssh_user))
        if mgmt_ip in seen:
            raise ValueError("NODES 第 {0} 行管理 IP 重复: {1}".format(idx, mgmt_ip))
        seen.add(mgmt_ip)
        nodes.append(Node(idx, mgmt_ip, ssh_user, ssh_password, ipmi_ip, ipmi_user, ipmi_password, role))
    return nodes


def parse_array(text, name):
    start = None
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if raw.strip().startswith(name + "=("):
            start = i + 1
            break
    if start is None:
        return []
    values = []
    for raw in lines[start:]:
        line = strip_comment(raw).strip()
        if line == ")":
            break
        if not line:
            continue
        if '"' in line or "'" in line:
            raise ValueError("{0} 配置项不要使用引号: {1}".format(name, line))
        values.append(line)
    return values


def parse_scalar(text, name):
    for raw in text.splitlines():
        line = strip_comment(raw).strip()
        if not line.startswith(name + "="):
            continue
        value = line.split("=", 1)[1].strip()
        if '"' in value or "'" in value:
            raise ValueError("{0} 配置项不要使用引号: {1}".format(name, line))
        return value
    return ""


def strip_comment(line):
    return line.split("#", 1)[0]


def validate_ptnets(stornets, ptnets):
    names = [item.split(":", 1)[0] for item in stornets if ":" in item]
    for item in stornets:
        if ":" not in item:
            raise ValueError("STORNETS 格式错误，需要 net名称:CIDR: {0}".format(item))
    invalid = [item for item in ptnets if item not in names]
    if invalid:
        raise ValueError("PTNETS 中存在不属于 STORNETS 的网段名称: {0}，STORNETS={1}".format(",".join(invalid), ",".join(names)))
