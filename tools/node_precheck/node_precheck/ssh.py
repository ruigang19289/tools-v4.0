from __future__ import print_function

import os
import subprocess

from .common import SSH_PORT


class SSHClient(object):
    def __init__(self, node, timeout):
        self.node = node
        self.timeout = timeout

    def __enter__(self):
        code, out, err = self.run("echo ok", timeout=self.timeout)
        if code != 0 or out.strip() != "ok":
            raise RuntimeError("SSH login failed: {0}".format(err or out))
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def _base_ssh_args(self):
        return [
            "ssh",
            "-p", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout={0}".format(self.timeout),
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "PreferredAuthentications=password,publickey",
            "root@{0}".format(self.node.mgmt_ip),
        ]

    def _with_password(self, args):
        if self.node.ssh_password and self.node.ssh_password != "-" and _has_command("sshpass"):
            return ["sshpass", "-e"] + args, _sshpass_env(self.node.ssh_password)
        return args, os.environ.copy()

    def run(self, command, timeout=None):
        if timeout is None:
            timeout = self.timeout
        args = self._base_ssh_args() + [command]
        args, env = self._with_password(args)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError("SSH command timeout after {0}s: {1}".format(timeout, command[:200]))
        return proc.returncode, out.decode("utf-8", "replace").strip(), err.decode("utf-8", "replace").strip()

    def put_file(self, local_path, remote_path, timeout=None):
        if timeout is None:
            timeout = self.timeout
        args = [
            "scp",
            "-P", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout={0}".format(self.timeout),
            "-o", "NumberOfPasswordPrompts=1",
            local_path,
            "root@{0}:{1}".format(self.node.mgmt_ip, remote_path),
        ]
        args, env = self._with_password(args)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError("SCP upload timeout after {0}s: {1}".format(timeout, local_path))
        if proc.returncode != 0:
            raise RuntimeError("SCP upload failed: {0}".format(err.decode("utf-8", "replace").strip()))
        return remote_path

    def fetch_dir(self, remote_path, local_path, timeout=None):
        if timeout is None:
            timeout = self.timeout
        parent = os.path.dirname(local_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        args = [
            "scp",
            "-r",
            "-P", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout={0}".format(self.timeout),
            "-o", "NumberOfPasswordPrompts=1",
            "root@{0}:{1}".format(self.node.mgmt_ip, remote_path),
            local_path,
        ]
        args, env = self._with_password(args)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError("SCP dir timeout after {0}s: {1}".format(timeout, remote_path))
        if proc.returncode != 0:
            raise RuntimeError("SCP dir failed: {0}".format(err.decode("utf-8", "replace").strip()))
        return local_path

    def fetch_file(self, remote_path, local_path, timeout=None):
        if timeout is None:
            timeout = self.timeout
        parent = os.path.dirname(local_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        args = [
            "scp",
            "-P", str(SSH_PORT),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout={0}".format(self.timeout),
            "-o", "NumberOfPasswordPrompts=1",
            "root@{0}:{1}".format(self.node.mgmt_ip, remote_path),
            local_path,
        ]
        args, env = self._with_password(args)
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise RuntimeError("SCP timeout after {0}s: {1}".format(timeout, remote_path))
        if proc.returncode != 0:
            raise RuntimeError("SCP failed: {0}".format(err.decode("utf-8", "replace").strip()))
        return local_path


def _has_command(name):
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(path, name)
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return True
    return False


def _sshpass_env(password):
    env = os.environ.copy()
    env["SSHPASS"] = password
    return env
