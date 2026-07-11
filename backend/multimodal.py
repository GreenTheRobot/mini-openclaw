"""多模态消息辅助：图片缩放并编码为内容块。"""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image


def _guess_media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def image_block(path: str, media_type: str | None = None,
                max_side: int = 1568) -> dict:
    """把图片编码为 Anthropic 风格内容块；大图先缩放到长边 <= max_side。"""
    media_type = media_type or _guess_media_type(path)
    with Image.open(path) as img:
        img = img.copy()
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))

        fmt = "PNG"
        if media_type == "image/jpeg":
            fmt = "JPEG"
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
        elif media_type == "image/webp":
            fmt = "WEBP"

        buf = BytesIO()
        img.save(buf, format=fmt)

    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image", "source":
            {"type": "base64", "media_type": media_type, "data": b64}}
