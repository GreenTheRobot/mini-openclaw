from __future__ import annotations

import json
import os
import ssl
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import Tool


FILE_TRANSFER_ASSISTANT = "\u6587\u4ef6\u4f20\u8f93\u52a9\u624b"


def _is_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _default_bridge_url() -> str:
    configured = os.environ.get("WX_BRIDGE_URL")
    if configured:
        return configured.rstrip("/")

    # WSL usually reaches the Windows host through the nameserver in resolv.conf.
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            is_wsl = "microsoft" in f.read().lower()
        if is_wsl:
            if _is_port_open("127.0.0.1", 8765):
                return "http://127.0.0.1:8765"
            with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("nameserver "):
                        return f"http://{line.split()[1]}:8765"
    except OSError:
        pass

    return "http://127.0.0.1:8765"


def _send_file_transfer_message(
    message: str,
    target: str = FILE_TRANSFER_ASSISTANT,
    bridge_url: str | None = None,
    token: str | None = None,
    verify_tls: bool = False,
    timeout: int = 15,
) -> str:
    if not message:
        return "error: message must not be empty"

    base_url = (bridge_url or _default_bridge_url()).rstrip("/")
    payload = json.dumps(
        {"message": message, "target": target, "exact": False},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    auth_token = token or os.environ.get("WX_BRIDGE_TOKEN", "")
    if auth_token:
        headers["X-OpenClaw-Token"] = auth_token

    request = Request(
        f"{base_url}/send_to_file_transfer",
        data=payload,
        headers=headers,
        method="POST",
    )

    context = None
    if base_url.startswith("https://") and not verify_tls:
        context = ssl._create_unverified_context()

    try:
        with urlopen(request, timeout=timeout, context=context) as resp:
            text = resp.read().decode("utf-8")
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"WeChat bridge returned HTTP {e.code}: {body}"
    except URLError as e:
        return f"cannot connect to WeChat bridge {base_url}: {e.reason}"

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return text

    if result.get("ok"):
        return f"sent to {target}: {message}"
    return f"send failed: {result.get('error', result)}"


wechat_file_transfer_tool = Tool(
    name="wechat_file_transfer",
    description=(
        "Send a text message to WeChat File Transfer Assistant through the "
        "Windows wxauto4 bridge service."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text message to send"},
            "target": {
                "type": "string",
                "description": "WeChat chat name; defaults to File Transfer Assistant",
                "default": FILE_TRANSFER_ASSISTANT,
            },
            "bridge_url": {
                "type": "string",
                "description": (
                    "Windows bridge URL; defaults to WX_BRIDGE_URL or the inferred "
                    "Windows host from WSL"
                ),
            },
            "token": {
                "type": "string",
                "description": "Optional auth token; defaults to WX_BRIDGE_TOKEN",
            },
            "verify_tls": {
                "type": "boolean",
                "description": (
                    "Whether to verify TLS certificates for HTTPS; self-signed "
                    "certificates usually use false"
                ),
                "default": False,
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds",
                "default": 15,
            },
        },
        "required": ["message"],
    },
    run=_send_file_transfer_message,
)
