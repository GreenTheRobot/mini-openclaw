"""Qwen 视觉模型后端（OpenAI 兼容接口）。

配置占位符：
  QWEN_API_KEY       后续填入你的 Qwen/DashScope API Key
  QWEN_BASE_URL      后续填入 DashScope/OpenAI-compatible base_url
  QWEN_CHAT_URL      可选；填入后作为完整 HTTP 请求 URL 原样使用
  QWEN_VISION_MODEL  后续填入视觉模型名
"""
from __future__ import annotations
import os

from .client import DeepSeekBackend


class QwenVisionBackend(DeepSeekBackend):
    supports_tools = False

    def __init__(self,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 model: str | None = None,
                 timeout: float = 60.0):
        super().__init__(
            api_key=api_key or os.environ.get("QWEN_API_KEY", "YOUR_QWEN_API_KEY"),
            base_url=base_url or os.environ.get("QWEN_BASE_URL", "YOUR_QWEN_BASE_URL"),
            model=model or os.environ.get("QWEN_VISION_MODEL", "YOUR_QWEN_VISION_MODEL"),
            timeout=timeout,
        )
        if self.api_key == "YOUR_QWEN_API_KEY":
            raise RuntimeError("缺少 QWEN_API_KEY：请配置你的 Qwen 视觉模型 API Key")
        if self.base_url == "YOUR_QWEN_BASE_URL":
            raise RuntimeError("缺少 QWEN_BASE_URL：请配置 OpenAI 兼容 base url")
        if self.model == "YOUR_QWEN_VISION_MODEL":
            raise RuntimeError("缺少 QWEN_VISION_MODEL：请配置视觉模型名")

    def _chat_url(self) -> str:
        configured = os.environ.get("QWEN_CHAT_URL", "").strip()
        if configured:
            return configured.rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"
