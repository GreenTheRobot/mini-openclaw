"""DeepSeek OpenAI-compatible chat backend."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from agent.context import validate_tool_protocol


class DeepSeekAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.response_body = response_body
        detail = f"DeepSeek API {status_code}: {message}"
        if response_body:
            detail += f"\n服务端响应：{response_body[:2000]}"
        super().__init__(detail)


class DeepSeekBackend:
    supports_tools = True

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
        self._client = httpx.Client(timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        protocol_errors = validate_tool_protocol(messages)
        if protocol_errors:
            raise ValueError("发送前消息协议校验失败：" + "; ".join(protocol_errors))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_openai_messages(messages),
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        response = self._client.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        if response.is_error:
            body = self._error_body(response)
            raise DeepSeekAPIError(response.status_code, response.reason_phrase, body)
        data = response.json()
        try:
            result = self._normalize(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekAPIError(
                response.status_code,
                "响应缺少 choices[0].message",
                json.dumps(data, ensure_ascii=False)[:2000],
            ) from exc
        result["usage"] = data.get("usage", {})
        result["model"] = data.get("model", self.model)
        return result

    @staticmethod
    def _error_body(response: httpx.Response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                error = data.get("error")
                if isinstance(error, dict):
                    message = error.get("message") or error.get("type")
                    if message:
                        return str(message)
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return response.text[:2000]

    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                output.append({
                    "role": "tool",
                    "content": str(message.get("content", "")),
                    "tool_call_id": str(message.get("tool_call_id", "")),
                })
            elif role == "assistant" and message.get("tool_calls"):
                output.append({
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": self._to_openai_tool_calls(message["tool_calls"]),
                })
            else:
                output.append({
                    "role": role,
                    "content": self._to_openai_content(message.get("content", "")),
                })
        return output

    @staticmethod
    def _to_openai_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        output = []
        for block in content:
            if block.get("type") == "text":
                output.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "image":
                source = block.get("source", {})
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                output.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            else:
                output.append(block)
        return output

    @staticmethod
    def _to_openai_tool_calls(calls: list[dict]) -> list[dict]:
        output = []
        for index, call in enumerate(calls):
            call_id = str(call.get("id") or f"call_{index}")
            output.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                },
            })
        return output

    @staticmethod
    def _normalize(message: dict[str, Any]) -> dict[str, Any]:
        tool_calls = []
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append({
                "id": tool_call.get("id"),
                "name": function.get("name"),
                "arguments": arguments,
            })
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
        }