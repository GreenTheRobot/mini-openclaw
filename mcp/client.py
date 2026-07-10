"""最小 MCP 客户端（Day8）。

MCP（Model Context Protocol）让工具集从"写死在代码里"变成"可插拔的外部 server"。
本文件实现一个最小客户端：通过 stdio 跟 server 通信，做 JSON-RPC。

要实现的握手与调用：
  1. 启动 server 子进程（stdio transport）
  2. initialize 握手
  3. tools/list  —— 拉取 server 暴露的工具
  4. tools/call  —— 把某次调用转发给 server，拿回结果
然后在 agent/loop 里，把这些 MCP 工具**透明合并**进内置 ToolRegistry。
"""
from __future__ import annotations
import json
import subprocess
from typing import Any

from tools.base import Tool, ToolRegistry


class MCPClient:
    def __init__(self, command: list[str]):
        self.command = command
        self.proc: subprocess.Popen | None = None
        self._id = 0

    def start(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,          # 行缓冲，配合一行一条消息
            )
        except OSError as e:
            raise RuntimeError(f"MCP server 启动失败：{self.command}；{e}") from e
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mini-openclaw", "version": "0.1"},
        })
        self._notify("notifications/initialized")   # 通知，无需等 result

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("MCP server 尚未启动。")
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
        except OSError as e:
            raise RuntimeError(f"MCP 请求发送失败：{method}；{e}") from e
        line = self.proc.stdout.readline()
        if not line:
            detail = ""
            if self.proc.poll() is not None and self.proc.stderr is not None:
                detail = self.proc.stderr.read().strip()
            suffix = f" stderr={detail}" if detail else ""
            raise RuntimeError(f"MCP server 已退出或未返回响应：{self.command}.{suffix}")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP 响应不是合法 JSON：{line.strip()}") from e
        if "error" in resp:
            err = resp["error"]
            if isinstance(err, dict):
                msg = err.get("message", err)
            else:
                msg = err
            raise RuntimeError(f"MCP 调用 {method} 出错：{msg}")
        if "result" not in resp:
            raise RuntimeError(f"MCP 响应缺少 result：{resp}")
        return resp["result"]

    def _notify(self, method: str, params: dict | None = None) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("MCP server 尚未启动。")
        req = {"jsonrpc": "2.0", "method": method, "params": params or {}}  # 无 id
        try:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
        except OSError as e:
            raise RuntimeError(f"MCP 通知发送失败：{method}；{e}") from e

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts) if parts else str(result)


def register_mcp_tools(registry: ToolRegistry, client: MCPClient) -> None:
    """把一个 MCP server 的工具包装成内置 Tool 并注册，实现透明合并。"""
    for spec in client.list_tools():
        name = spec["name"]
        registry.register(Tool(
            name=f"mcp__{name}",            # 命名空间避免和内置工具撞名
            description=spec.get("description", ""),
            parameters=spec.get("inputSchema", {"type": "object", "properties": {}}),
            run=lambda _n=name, **kw: client.call_tool(_n, kw),
        ))
