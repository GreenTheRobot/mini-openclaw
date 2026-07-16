"""Restricted HTTPS PDF downloader for reproducible literature workflows."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx

from .base import Tool, ToolResult

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
PDF_HOSTS = {
    "arxiv.org", "export.arxiv.org", "biorxiv.org", "medrxiv.org",
    "openreview.net", "openaccess.thecvf.com", "proceedings.mlr.press",
    "aclanthology.org", "zenodo.org", "osf.io", "github.com",
    "raw.githubusercontent.com",
}
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_ALLOWED_CONTENT_TYPES = {
    "application/pdf", "application/x-pdf", "application/octet-stream", "",
}


def _normalized_host(hostname: str | None) -> str:
    if not hostname:
        return ""
    return hostname.rstrip(".").encode("idna").decode("ascii").lower()


def is_allowed_pdf_host(hostname: str | None) -> bool:
    host = _normalized_host(hostname)
    return any(host == allowed or host.endswith("." + allowed) for allowed in PDF_HOSTS)


def _validate_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = _normalized_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("URL 端口无效") from None
    if parsed.scheme.lower() != "https":
        raise ValueError("download_file 只允许 HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("下载 URL 不允许包含用户名或密码")
    if port not in {None, 443}:
        raise ValueError("下载 URL 只允许默认 HTTPS 端口 443")
    if not host or not is_allowed_pdf_host(host):
        raise PermissionError(f"PDF 下载域名不在白名单：{host or url}")
    return host, parsed.geturl()


def _validate_public_dns(host: str, resolver: Callable[..., Any]) -> None:
    try:
        records = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ConnectionError(f"无法解析下载域名 {host}：{exc}") from exc
    addresses = {record[4][0] for record in records if record and len(record) > 4}
    if not addresses:
        raise ConnectionError(f"下载域名没有可用地址：{host}")
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise PermissionError(f"下载域名解析到非公网地址：{raw}")


def _safe_target(path: str) -> tuple[Path, Path]:
    workdir = Path.cwd().resolve()
    raw = Path(path)
    target = (workdir / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        relative = target.relative_to(workdir)
    except ValueError:
        raise PermissionError(f"下载路径越过工作目录：{target}") from None
    if target.suffix.lower() != ".pdf":
        raise ValueError("download_file 只允许保存为 .pdf 文件")
    if target.exists():
        raise FileExistsError(f"目标文件已存在，拒绝覆盖：{relative}")
    if raw.is_symlink() or target.is_symlink():
        raise PermissionError("下载目标不能是符号链接")
    current = workdir
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise PermissionError(f"下载路径包含符号链接目录：{current}")
    return target, relative


def _failure(message: str, category: str) -> ToolResult:
    return ToolResult(message, False, category)


def _download_file(
    url: str,
    path: str,
    *,
    _transport: httpx.BaseTransport | None = None,
    _resolver: Callable[..., Any] = socket.getaddrinfo,
) -> ToolResult:
    try:
        initial_host, current_url = _validate_url(url)
        target, relative = _safe_target(path)
    except PermissionError as exc:
        return _failure(str(exc), "host_or_path_denied")
    except (ValueError, FileExistsError) as exc:
        category = "target_exists" if isinstance(exc, FileExistsError) else "invalid_request"
        return _failure(str(exc), category)

    temp: Path | None = None
    try:
        current_host = initial_host
        with httpx.Client(
            transport=_transport,
            follow_redirects=False,
            timeout=30.0,
            trust_env=False,
            headers={"User-Agent": "mini-openclaw/0.1", "Accept": "application/pdf", "Accept-Encoding": "identity"},
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                _validate_public_dns(current_host, _resolver)
                with client.stream("GET", current_url) as response:
                    if response.status_code in _REDIRECT_CODES:
                        location = response.headers.get("location")
                        if not location:
                            return _failure("重定向响应缺少 Location", "invalid_redirect")
                        next_url = urljoin(current_url, location)
                        try:
                            next_host, next_url = _validate_url(next_url)
                        except (ValueError, PermissionError) as exc:
                            return _failure(str(exc), "redirect_denied")
                        if next_host != current_host:
                            payload = json.dumps({
                                "message": "重定向到新域名，需要单独授权后重新下载",
                                "redirect_url": next_url,
                                "from_host": current_host,
                                "to_host": next_host,
                            }, ensure_ascii=False)
                            return _failure(payload, "redirect_requires_confirmation")
                        current_url = next_url
                        continue

                    if response.status_code >= 400:
                        return _failure(f"下载返回 HTTP {response.status_code}", "http_error")

                    raw_length = response.headers.get("content-length", "")
                    if raw_length.isdigit() and int(raw_length) > MAX_PDF_BYTES:
                        return _failure(f"PDF 超过 {MAX_PDF_BYTES} 字节上限", "size_limit")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type not in _ALLOWED_CONTENT_TYPES:
                        return _failure(f"响应不是 PDF：Content-Type={content_type or 'missing'}", "content_type")

                    target.parent.mkdir(parents=True, exist_ok=True)
                    temp = target.with_name(f".{target.name}.{uuid4().hex}.part")
                    digest = hashlib.sha256()
                    prefix = bytearray()
                    total = 0
                    with temp.open("xb") as file:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > MAX_PDF_BYTES:
                                raise OverflowError(f"PDF 超过 {MAX_PDF_BYTES} 字节上限")
                            if len(prefix) < 1024:
                                prefix.extend(chunk[:1024 - len(prefix)])
                            digest.update(chunk)
                            file.write(chunk)
                    if b"%PDF-" not in bytes(prefix):
                        return _failure("下载内容缺少 PDF 文件签名", "invalid_pdf")
                    temp.replace(target)
                    temp = None
                    payload = {
                        "path": str(relative),
                        "bytes": total,
                        "sha256": digest.hexdigest(),
                        "final_url": current_url,
                        "content_type": content_type or "unknown",
                    }
                    return ToolResult(json.dumps(payload, ensure_ascii=False, indent=2), True, "ok")
            return _failure(f"重定向超过 {MAX_REDIRECTS} 次", "too_many_redirects")
    except OverflowError as exc:
        return _failure(str(exc), "size_limit")
    except PermissionError as exc:
        return _failure(str(exc), "unsafe_address")
    except (httpx.HTTPError, OSError, ConnectionError) as exc:
        return _failure(f"PDF 下载失败：{exc}", "network_error")
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


download_file_tool = Tool(
    name="download_file",
    description="通过受控 HTTPS GET 下载学术 PDF 到工作目录；不支持上传、请求体、自定义请求头或覆盖已有文件。",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string"}, "path": {"type": "string"}},
        "required": ["url", "path"],
        "additionalProperties": False,
    },
    run=_download_file,
)