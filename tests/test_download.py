import json
import socket
from pathlib import Path

import httpx

from tools.base import build_default_registry
from tools.download import MAX_PDF_BYTES, _download_file


def public_resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def test_download_valid_pdf_and_returns_reproducibility_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = b"%PDF-1.7\nacademic paper\n%%EOF"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=data, headers={"content-type": "application/pdf"}, request=request,
    ))
    result = _download_file(
        "https://arxiv.org/pdf/1234.5678.pdf", "papers/paper.pdf",
        _transport=transport, _resolver=public_resolver,
    )
    assert result.success is True
    payload = json.loads(result.content)
    assert payload["bytes"] == len(data)
    assert len(payload["sha256"]) == 64
    assert (tmp_path / "papers" / "paper.pdf").read_bytes() == data


def test_download_rejects_cross_domain_redirect_until_reauthorized(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "https://export.arxiv.org/pdf/1234.5678.pdf"}, request=request,
    ))
    result = _download_file(
        "https://arxiv.org/pdf/1234.5678.pdf", "paper.pdf",
        _transport=transport, _resolver=public_resolver,
    )
    assert result.success is False
    assert result.category == "redirect_requires_confirmation"
    assert not (tmp_path / "paper.pdf").exists()


def test_download_rejects_unsafe_request_and_bad_content(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _download_file("http://arxiv.org/a.pdf", "a.pdf").success is False
    assert _download_file("https://arxiv.org/a.pdf", "../a.pdf").success is False
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=b"<html>error</html>", headers={"content-type": "text/html"}, request=request,
    ))
    result = _download_file(
        "https://arxiv.org/a.pdf", "a.pdf", _transport=transport, _resolver=public_resolver,
    )
    assert result.success is False
    assert result.category == "content_type"
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_declared_oversize_and_existing_target(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200,
        content=b"%PDF-1.7",
        headers={"content-type": "application/pdf", "content-length": str(MAX_PDF_BYTES + 1)},
        request=request,
    ))
    result = _download_file(
        "https://arxiv.org/a.pdf", "a.pdf", _transport=transport, _resolver=public_resolver,
    )
    assert result.category == "size_limit"
    (tmp_path / "existing.pdf").write_bytes(b"old")
    existing = _download_file("https://arxiv.org/a.pdf", "existing.pdf")
    assert existing.category == "target_exists"


def test_default_registry_contains_download_file():
    assert "download_file" in build_default_registry().names()