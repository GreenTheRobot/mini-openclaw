from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import shlex
import ssl
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import Tool


FILE_TRANSFER_ASSISTANT = "\u6587\u4ef6\u4f20\u8f93\u52a9\u624b"
DEFAULT_BRIDGE_START_TIMEOUT_SECONDS = 300
_bridge_process: subprocess.Popen | None = None
_bridge_cleanup_registered = False


def _running_in_wsl() -> bool:
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _split_targets(raw: str) -> list[str]:
    targets: list[str] = []
    for chunk in raw.replace(";", ",").split(","):
        target = chunk.strip()
        if target and target not in targets:
            targets.append(target)
    return targets


def allowed_targets() -> list[str]:
    targets = [FILE_TRANSFER_ASSISTANT]
    configured_default = os.environ.get("WX_FILE_TRANSFER_TARGET", "").strip()
    if configured_default and configured_default not in targets:
        targets.append(configured_default)
    for target in _split_targets(os.environ.get("WX_ALLOWED_TARGETS", "")):
        if target not in targets:
            targets.append(target)
    return targets


def trusted_targets() -> list[str]:
    targets = [FILE_TRANSFER_ASSISTANT]
    for target in _split_targets(os.environ.get("WX_TRUSTED_TARGETS", "")):
        if target not in targets:
            targets.append(target)
    return targets


def is_trusted_target(target: str | None = None) -> bool:
    try:
        selected = _resolve_target(target)
    except ValueError:
        return False
    return selected in trusted_targets()


def _resolve_target(target: str | None = None) -> str:
    selected = target or os.environ.get("WX_FILE_TRANSFER_TARGET", FILE_TRANSFER_ASSISTANT) or FILE_TRANSFER_ASSISTANT
    allowed = allowed_targets()
    if selected not in allowed:
        allowed_text = ", ".join(allowed)
        raise ValueError(f"微信目标不在允许列表中：{selected}；允许：{allowed_text}")
    return selected


def _log_dry_run(target: str, message: str) -> None:
    print("[wechat dry-run] would send message", file=sys.stderr)
    print(f"[wechat dry-run] target: {target}", file=sys.stderr)
    print(f"[wechat dry-run] message: {message}", file=sys.stderr)


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
    if _running_in_wsl():
        if _is_port_open("127.0.0.1", 8765):
            return "http://127.0.0.1:8765"
        nameserver_url = _wsl_nameserver_bridge_url()
        if nameserver_url:
            return nameserver_url

    return "http://127.0.0.1:8765"


def _wsl_nameserver_bridge_url() -> str | None:
    try:
        with open("/etc/resolv.conf", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("nameserver "):
                    return f"http://{line.split()[1]}:8765"
    except OSError:
        return None
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _windows_path(path: Path) -> str:
    if os.name == "nt":
        return str(path)
    try:
        result = subprocess.run(
            ["wslpath", "-w", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return str(path)


def _default_bridge_start_command() -> tuple[str | list[str] | None, bool]:
    script = _repo_root() / "services" / "wechat_bridge" / "start.ps1"
    if not script.exists():
        return None, False
    script_path = _windows_path(script)
    if os.name != "nt" and _running_in_wsl() and Path("/init").exists():
        return [
            "/init",
            "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
        ], False
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_path,
    ]
    if os.name != "nt" and _running_in_wsl():
        return " ".join(shlex.quote(part) for part in command), True
    return command, False


def _bridge_start_command() -> tuple[str | list[str] | None, bool]:
    configured = os.environ.get("WECHAT_BRIDGE_START_CMD", "").strip()
    if configured:
        return configured, True
    return _default_bridge_start_command()


def _bridge_start_timeout_seconds() -> int:
    raw = os.environ.get("WECHAT_BRIDGE_START_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_BRIDGE_START_TIMEOUT_SECONDS
    try:
        return max(10, int(raw))
    except ValueError:
        return DEFAULT_BRIDGE_START_TIMEOUT_SECONDS


def _health_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/health"


def _bridge_is_ready(base_url: str, verify_tls: bool = False, timeout: float = 0.5) -> bool:
    context = None
    if base_url.startswith("https://") and not verify_tls:
        context = ssl._create_unverified_context()
    try:
        with urlopen(_health_url(base_url), timeout=timeout, context=context) as resp:
            return 200 <= resp.status < 300
    except (OSError, HTTPError, URLError):
        return False


def _bridge_url_candidates(preferred_url: str) -> list[str]:
    candidates = [preferred_url.rstrip("/"), "http://127.0.0.1:8765"]
    nameserver_url = _wsl_nameserver_bridge_url()
    if nameserver_url:
        candidates.append(nameserver_url)

    result = []
    for url in candidates:
        if url not in result:
            result.append(url)
    return result


def _ready_bridge_url(preferred_url: str, verify_tls: bool = False) -> str | None:
    for url in _bridge_url_candidates(preferred_url):
        if _bridge_is_ready(url, verify_tls=verify_tls):
            return url
    return None


def _stop_bridge(process: subprocess.Popen | None = None) -> None:
    global _bridge_process
    process = process or _bridge_process
    if process is None:
        return
    if process.poll() is not None:
        if process is _bridge_process:
            _bridge_process = None
        return
    print("[wechat bridge] stopping auto-started bridge", file=sys.stderr)
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
    if process is _bridge_process:
        _bridge_process = None


def _start_bridge(base_url: str, verify_tls: bool = False) -> subprocess.Popen | None:
    global _bridge_process, _bridge_cleanup_registered
    if _bridge_process and _bridge_process.poll() is None:
        if _ready_bridge_url(base_url, verify_tls=verify_tls):
            return _bridge_process

    command, use_shell = _bridge_start_command()
    if not command:
        return None

    print(f"[wechat bridge] starting: {command}", file=sys.stderr)
    log_dir = _repo_root() / "services"
    stdout_log = log_dir / "wx_bridge.out.log"
    stderr_log = log_dir / "wx_bridge.err.log"
    try:
        with stdout_log.open("ab") as stdout, stderr_log.open("ab") as stderr:
            process = subprocess.Popen(command, shell=use_shell, stdout=stdout, stderr=stderr)
            _bridge_process = process
            if not _bridge_cleanup_registered:
                atexit.register(_stop_bridge)
                _bridge_cleanup_registered = True
    except OSError as e:
        print(f"[wechat bridge] start failed: {e}", file=sys.stderr)
        _bridge_process = None
        return None

    deadline = time.monotonic() + _bridge_start_timeout_seconds()
    while time.monotonic() < deadline:
        if _ready_bridge_url(base_url, verify_tls=verify_tls):
            return process
        if process.poll() is not None:
            print(f"[wechat bridge] exited early with code {process.returncode}", file=sys.stderr)
            _bridge_process = None
            return None
        time.sleep(0.25)
    print("[wechat bridge] start timed out", file=sys.stderr)
    _stop_bridge(process)
    return None


def _send_file_transfer_message(
    message: str,
    target: str | None = None,
    bridge_url: str | None = None,
    token: str | None = None,
    verify_tls: bool = False,
    timeout: int = 15,
) -> str:
    if not message:
        return "error: message must not be empty"

    try:
        target = _resolve_target(target)
    except ValueError as e:
        return f"error: {e}"
    if os.environ.get("WECHAT_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}:
        _log_dry_run(target, message)
        return f"sent to {target}: {message}"

    base_url = (bridge_url or _default_bridge_url()).rstrip("/")
    payload = json.dumps(
        {"message": message, "target": target, "exact": False},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    auth_token = token or os.environ.get("WX_BRIDGE_TOKEN", "")
    if auth_token:
        headers["X-OpenClaw-Token"] = auth_token

    context = None
    if base_url.startswith("https://") and not verify_tls:
        context = ssl._create_unverified_context()

    def post_message() -> str:
        request = Request(
            f"{base_url}/send_to_file_transfer",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout, context=context) as resp:
            return resp.read().decode("utf-8")

    try:
        text = post_message()
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return f"WeChat bridge returned HTTP {e.code}: {body}"
    except URLError as e:
        bridge_process = _start_bridge(base_url, verify_tls=verify_tls)
        if bridge_process:
            ready_url = _ready_bridge_url(base_url, verify_tls=verify_tls)
            if ready_url:
                base_url = ready_url
            try:
                text = post_message()
            except HTTPError as retry_e:
                body = retry_e.read().decode("utf-8", errors="replace")
                return f"WeChat bridge returned HTTP {retry_e.code}: {body}"
            except URLError as retry_e:
                return f"cannot connect to WeChat bridge {base_url}: {retry_e.reason}"
        else:
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
    description="经本机受控桥接服务向微信固定允许列表内的会话发送文本通知。默认发送到文件传输助手。",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "要发送的通知文本"},
            "target": {
                "type": "string",
                "enum": allowed_targets(),
                "description": "微信会话名；只能选择预先允许的固定目标。未提供时使用 WX_FILE_TRANSFER_TARGET 或默认文件传输助手",
            },
            "timeout": {"type": "integer", "description": "请求超时秒数", "default": 15},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
    run=_send_file_transfer_message,
)
