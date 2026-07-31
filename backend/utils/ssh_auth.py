"""Shared SSH authentication helpers for server-side remote connections."""

import os



DEFAULT_PRIVATE_KEY_PATH = "/root/.ssh/id_ed25519"
PRIVATE_KEY_PATH = os.environ.get(
    "TOOLS_ANSIBLE_PRIVATE_KEY_PATH", DEFAULT_PRIVATE_KEY_PATH
)


def parse_ssh_auth(data, default_username="root"):
    """Parse supported request authentication without accepting a key path."""
    auth_method = data.get("auth_method", "password")
    if auth_method not in ("password", "key"):
        raise ValueError("auth_method must be 'password' or 'key'")

    username = str(data.get("username") or default_username)
    if not username:
        raise ValueError("username is required")

    password = data.get("password") if auth_method == "password" else None
    if auth_method == "password" and not password:
        raise ValueError("password is required for password authentication")

    return {
        "auth_method": auth_method,
        "username": username,
        "password": password,
    }


def get_ssh_connect_kwargs(auth, port=22, timeout=10):
    """Return Paramiko connect kwargs for password or server-mounted key auth."""
    kwargs = {
        "port": int(port or 22),
        "username": auth["username"],
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if auth["auth_method"] == "key":
        if not os.path.isfile(PRIVATE_KEY_PATH):
            raise ValueError(
                f"SSH 私钥不存在：{PRIVATE_KEY_PATH}。请在容器中只读挂载私钥并设置 TOOLS_ANSIBLE_PRIVATE_KEY_PATH。"
            )
        kwargs["key_filename"] = PRIVATE_KEY_PATH
    else:
        kwargs["password"] = auth["password"]
    return kwargs


def connect_ssh(host, auth, port=22, timeout=10):
    """Create a Paramiko SSH client using the selected authentication method."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, **get_ssh_connect_kwargs(auth, port, timeout))
    return client
