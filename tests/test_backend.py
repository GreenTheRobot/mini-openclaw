import httpx
import pytest

from backend.client import DeepSeekAPIError, DeepSeekBackend
from backend.qwen_vision import QwenVisionBackend


def test_backend_rejects_orphan_tool_message_before_http_request():
    backend = DeepSeekBackend(api_key="test")
    try:
        with pytest.raises(ValueError, match="消息协议"):
            backend.chat([
                {"role": "system", "content": "system"},
                {"role": "tool", "tool_call_id": "orphan", "content": "bad"},
            ])
    finally:
        backend._client.close()


def test_backend_exposes_deepseek_error_message_without_secret():
    def handler(request):
        return httpx.Response(
            400,
            json={"error": {"message": "Missing tool call for tool message"}},
            request=request,
        )

    backend = DeepSeekBackend(api_key="secret-key")
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DeepSeekAPIError) as error:
            backend.chat([
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ])
        assert error.value.status_code == 400
        assert "Missing tool call" in str(error.value)
        assert "secret-key" not in str(error.value)
    finally:
        backend._client.close()


def test_qwen_backend_posts_to_chat_completions_for_dashscope_base_url():
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            request=request,
        )

    backend = QwenVisionBackend(
        api_key="sk-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-test",
    )
    backend._client.close()
    backend._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        backend.chat([{"role": "user", "content": "hello"}])
        assert seen_urls == ["https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"]
    finally:
        backend._client.close()


def test_qwen_backend_does_not_duplicate_full_chat_url():
    backend = QwenVisionBackend(
        api_key="sk-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        model="qwen-test",
    )
    try:
        assert backend._chat_url() == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    finally:
        backend._client.close()


def test_qwen_chat_url_override_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("QWEN_CHAT_URL", "https://example.test/custom")
    backend = QwenVisionBackend(
        api_key="sk-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-test",
    )
    try:
        assert backend._chat_url() == "https://example.test/custom"
    finally:
        backend._client.close()
