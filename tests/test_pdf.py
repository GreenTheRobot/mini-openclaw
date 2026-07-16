from pathlib import Path

from tools.base import ToolResult
from tools.pdf import _pdf_extract_text


def test_pdf_auto_prefers_markitdown_when_gpu_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.7 fake")
    monkeypatch.setattr("tools.pdf._gpu_status", lambda: {"available": False, "reason": "test"})
    monkeypatch.setattr("tools.pdf._markitdown_convert", lambda path: ("# extracted", {}))
    monkeypatch.setattr("tools.pdf._extract_pymupdf_images", lambda *args: [])

    result = _pdf_extract_text("paper.pdf", output_dir="literature/paper")

    assert isinstance(result, str)
    assert "literature/paper/paper.md" in result
    saved = (tmp_path / "literature" / "paper" / "paper.md").read_text(encoding="utf-8")
    assert "parser: markitdown" in saved
    assert (tmp_path / "literature" / "paper" / "image_manifest.json").exists()


def test_pdf_explicit_marker_is_rejected_without_gpu(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.7 fake")
    monkeypatch.setattr("tools.pdf._gpu_status", lambda: {"available": False, "reason": "no cuda"})

    result = _pdf_extract_text("paper.pdf", parser="marker")

    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.category == "gpu_unavailable"
