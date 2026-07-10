"""一个稍复杂的 MCP server，用于 npx 不可用时测试多工具注册。

通过 stdio + JSON-RPC 暴露 add(a,b) 和 multiply(a,b) 两个工具。
"""
from __future__ import annotations
import json
import sys

TOOLS = [
    {
        "name": "add",
        "description": "返回 a + b 的结果。",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "multiply",
        "description": "返回 a * b 的结果。",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
]


def _number(value: object, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} 必须是数字") from e


def _text_result(rid: object, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": text}]}}


def handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2024-11-05",
                           "serverInfo": {"name": "calc", "version": "0.1"},
                           "capabilities": {"tools": {}}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            a = _number(args.get("a"), "a")
            b = _number(args.get("b"), "b")
            if name == "add":
                return _text_result(rid, str(a + b))
            if name == "multiply":
                return _text_result(rid, str(a * b))
        except ValueError as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"unknown tool: {name}"}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "method not found"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
