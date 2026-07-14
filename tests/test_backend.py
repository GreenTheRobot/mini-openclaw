import httpx
import pytest

from backend.client import DeepSeekAPIError, DeepSeekBackend


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