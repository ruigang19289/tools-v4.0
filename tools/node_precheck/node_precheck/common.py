from __future__ import print_function

import re
import socket

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
ERROR = "ERROR"
INFO = "INFO"

DEFAULT_TIMEOUT = 30
DEFAULT_PARALLEL = 10
SSH_PORT = 22
CATEGORY_ORDER = ["bios", "os", "hardware", "network", "disk"]

CHECK_NAMES = {
    "BIOS-ACCESS": "Redfish BIOS Access",
    "BIOS-PLATFORM": "BIOS Platform Detection",
    "BIOS-01": "Workload Profile",
    "BIOS-02": "Power Performance Tuning",
    "BIOS-03": "Energy Performance Bias",
    "BIOS-04": "Hardware P States",
    "BIOS-05": "Turbo Mode",
    "BIOS-06": "Monitor/Mwait",
    "BIOS-07": "C1E",
    "BIOS-08": "CPU C6 Report",
    "BIOS-09": "Intel VT-d",
    "BIOS-10": "PCIe Hot Plug",
    "BIOS-11": "Boot Mode Select",
    "BIOS-AMD-01": "AMD OC Mode",
    "BIOS-AMD-02": "AMD Power Profile",
    "BIOS-AMD-03": "AMD Global C-state",
    "BIOS-AMD-04": "AMD IOMMU",
    "BIOS-AMD-05": "AMD Hot-Plug Support",
    "BIOS-AMD-06": "AMD Boot Mode Select",
    "BIOS-KP-01": "Kunpeng Power Policy",
    "BIOS-KP-02": "Kunpeng CPU Prefetch",
    "BIOS-KP-03": "Kunpeng Hot-Plug",
    "BIOS-KP-04": "Kunpeng Support SPCR",
    "BIOS-KP-05": "Kunpeng SMMU",
    "OS-01": "Root Available Space",
    "OS-02": "HugePage Size",
    "OS-03": "SELinux Config",
    "OS-04": "SELinux Runtime",
    "OS-05": "Firewalld Closed",
    "OS-06": "SSH Passwordless Mesh",
    "OS-07": "System Locale",
    "OS-08": "Package tar",
    "OS-09": "Package rsync",
    "OS-10": "Package dmidecode",
    "OS-11": "Package ipcalc",
    "OS-12": "CPU Hyperthreading",
    "OS-13": "NUMA Node Count",
    "OS-14": "NUMA Balancing",
    "HW-01": "NIC NUMA Balance",
    "HW-02": "NVMe NUMA Balance",
    "HW-03": "NIC PCIe Link",
    "HW-04": "NVMe PCIe Link",
    "NET-00": "10GE+ Port Inventory",
    "NET-01": "MLNX/RDMA Tools",
    "NET-02": "RDMA Core Version",
    "NET-03": "Port Link UP",
    "NET-04": "RDMA Link Layer MTU",
    "NET-05": "RoCEv2 Service Loaded",
    "NET-06": "RDMA Cluster Diagnostic",
    "NET-07": "NIC PCIe Link (Network)",
    "DISK-01": "NVMe Data Disks",
    "DISK-02": "Disk Data Signature",
    "DISK-03": "NVMe Lifetime",
}


class Node(object):
    def __init__(self, line_no, mgmt_ip, ssh_user, ssh_password, ipmi_ip, ipmi_user, ipmi_password, role):
        self.line_no = line_no
        self.mgmt_ip = mgmt_ip
        self.ssh_user = ssh_user
        self.ssh_password = ssh_password
        self.ipmi_ip = ipmi_ip
        self.ipmi_user = ipmi_user
        self.ipmi_password = ipmi_password
        self.role = role


class CheckResult(object):
    def __init__(self, check_id, name, category, status, actual="", expected="", reason="", suggestion=""):
        self.check_id = check_id
        self.name = name
        self.category = category
        self.status = status
        self.actual = str(actual) if actual is not None else ""
        self.expected = str(expected) if expected is not None else ""
        self.reason = str(reason) if reason is not None else ""
        self.suggestion = str(suggestion) if suggestion is not None else ""


class NodeReport(object):
    def __init__(self, node):
        self.node = node
        self.results = []
        self.facts = {}
        self.error = ""

    def add(self, check_id, category, status, actual="", expected="", reason="", suggestion=""):
        self.results.append(CheckResult(
            check_id,
            CHECK_NAMES.get(check_id, check_id),
            category,
            status,
            actual,
            expected,
            reason,
            suggestion,
        ))

    def result_map(self):
        return dict((r.check_id, r) for r in self.results)


def is_ip(value):
    try:
        socket.inet_aton(value)
        return value.count(".") == 3
    except socket.error:
        return False


def normalize_value(value):
    value = str(value).strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", value).lower()


def values_equal(actual, expected):
    aliases = {
        "disable": "disabled",
        "enable": "enabled",
        "enabled": "enabled",
        "disabled": "disabled",
        "native": "native mode",
        "native mode": "native mode",
        "bios controls epb": "bios controls epb",
        "uefiboot": "uefi",
        "uefi boot": "uefi",
        "uefi only": "uefi",
        "uefi only boot": "uefi",
    }
    actual_n = normalize_value(actual)
    expected_n = normalize_value(expected)
    actual_n = aliases.get(actual_n, actual_n)
    expected_n = aliases.get(expected_n, expected_n)
    return actual_n == expected_n


def first_int(text):
    match = re.search(r"-?\d+", text or "")
    if not match:
        return None
    return int(match.group(0))


def shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"
