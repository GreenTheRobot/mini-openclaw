r"""Small Windows-side bridge for sending WeChat messages.

Run this with the Windows virtual environment that has wxauto4 installed:

    E:\wxauto-mcp\wxauto_env\Scripts\python.exe services\wx_file_transfer_server.py

The mini-openclaw tool running in WSL calls this process over HTTP/HTTPS.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import RLock
from typing import Any


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_TARGET = "\u6587\u4ef6\u4f20\u8f93\u52a9\u624b"

_wx = None
_lock = RLock()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _get_wx():
    global _wx
    if _wx is None:
        try:
            from wxauto4 import WeChat
        except ImportError as e:
            raise RuntimeError("wxauto4 is not installed in this Python environment") from e
        _wx = WeChat()
    return _wx


def _safe_chat_info(wx) -> dict[str, Any]:
    try:
        info = wx.ChatInfo()
        return info if isinstance(info, dict) else {"raw": repr(info)}
    except Exception as e:
        return {"error": str(e)}


def send_message(message: str, target: str, exact: bool = False) -> dict[str, Any]:
    if not message:
        raise ValueError("message must not be empty")
    if not target:
        raise ValueError("target must not be empty")

    with _lock:
        wx = _get_wx()
        attempts = []
        for attempt in range(2):
            try:
                response = wx.SendMsg(msg=message, who=target, exact=exact)
                chat_info = _safe_chat_info(wx)
                return {
                    "target": target,
                    "chat_info": chat_info,
                    "response": repr(response),
                    "attempt": attempt + 1,
                }
            except Exception as e:
                attempts.append(
                    {
                        "stage": "SendMsg",
                        "attempt": attempt + 1,
                        "error": str(e),
                        "chat_info": _safe_chat_info(wx),
                    }
                )
                time.sleep(0.5)

        try:
            wx.ChatWith(target, exact=exact)
            chat_info = _safe_chat_info(wx)
            response = wx.SendMsg(msg=message)
            return {
                "target": target,
                "chat_info": chat_info,
                "response": repr(response),
                "fallback": "ChatWith+SendMsg",
                "attempts": attempts,
            }
        except Exception as e:
            attempts.append(
                {
                    "stage": "ChatWith+SendMsg",
                    "error": str(e),
                    "chat_info": _safe_chat_info(wx),
                }
            )
            raise RuntimeError(f"send failed after retries: {attempts}") from e


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenClawWxBridge/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)

    def _write_json(self, code: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _authorized(self) -> bool:
        token = getattr(self.server, "token", "")
        if not token:
            return True
        return self.headers.get("X-OpenClaw-Token") == token

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(200, {"ok": True, "service": "wx_file_transfer"})
            return
        self._write_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/send_to_file_transfer":
            self._write_json(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            self._write_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            payload = self._read_json()
            message = str(payload.get("message", ""))
            target = str(payload.get("target") or getattr(self.server, "target", DEFAULT_TARGET))
            exact = bool(payload.get("exact", False))
            data = send_message(message, target=target, exact=exact)
            self._write_json(200, {"ok": True, "message": "sent", "data": data})
        except Exception as e:
            self._write_json(500, {"ok": False, "error": str(e)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows WeChat bridge for mini-openclaw")
    parser.add_argument("--host", default=os.getenv("WX_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("WX_BRIDGE_PORT", DEFAULT_PORT)))
    parser.add_argument("--target", default=os.getenv("WX_FILE_TRANSFER_TARGET", DEFAULT_TARGET))
    parser.add_argument("--token", default=os.getenv("WX_BRIDGE_TOKEN", ""))
    parser.add_argument("--certfile", default=os.getenv("WX_BRIDGE_CERTFILE", ""))
    parser.add_argument("--keyfile", default=os.getenv("WX_BRIDGE_KEYFILE", ""))
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.target = args.target
    server.token = args.token
    server.quiet = args.quiet

    scheme = "http"
    if args.certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.certfile, args.keyfile or None)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"

    print(f"wx_file_transfer bridge listening on {scheme}://{args.host}:{args.port}")
    print(f"default target: {args.target}")
    if args.token:
        print("token auth: enabled")
    server.serve_forever()


if __name__ == "__main__":
    main()
