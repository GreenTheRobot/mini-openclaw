import httpx

from tools.web import _web_fetch


def test_web_fetch_denied_host_is_explicit_failure():
    result = _web_fetch("https://arxiv.org.evil.test/page")
    assert result.success is False
    assert result.category == "host_denied"


def test_web_fetch_cross_host_redirect_requires_new_authorization():
    def handler(request):
        return httpx.Response(
            302,
            headers={"location": "https://arxiv.org/abs/1234.5678"},
            request=request,
        )
    result = _web_fetch(
        "https://graph-robots.github.io/gap/",
        _transport=httpx.MockTransport(handler),
    )
    assert result.success is False
    assert result.category == "redirect_requires_confirmation"


def test_web_fetch_allows_same_host_redirect():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(200, text="<h1>Paper</h1>", request=request)
    result = _web_fetch(
        "https://arxiv.org/start",
        _transport=httpx.MockTransport(handler),
    )
    assert result.success is True
    assert "Paper" in result.content


def test_web_fetch_classifies_404_separately():
    def handler(request):
        return httpx.Response(404, text="missing", request=request)

    result = _web_fetch(
        "https://api.github.com/repos/example/project/contents/src",
        _transport=httpx.MockTransport(handler),
    )
    assert result.success is False
    assert result.category == "http_not_found"
    assert "HTTP 404" in result.content


def test_web_fetch_classifies_timeout_separately():
    def handler(request):
        raise httpx.ReadTimeout("slow response", request=request)

    result = _web_fetch(
        "https://arxiv.org/abs/1234.5678",
        _transport=httpx.MockTransport(handler),
    )
    assert result.success is False
    assert result.category == "network_timeout"
