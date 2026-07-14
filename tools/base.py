"""工具抽象、参数校验、结构化执行结果与默认注册表。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def validate_arguments(schema: dict[str, Any], value: Any, path: str = "arguments") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected in _JSON_TYPES:
        python_type = _JSON_TYPES[expected]
        valid = isinstance(value, python_type)
        if expected in {"integer", "number"} and isinstance(value, bool):
            valid = False
        if not valid:
            return [f"{path} 应为 {expected}，实际为 {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} 必须是 {schema['enum']} 之一")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path} 缺少必填字段 {key}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path} 包含未知字段 {key}")
        for key, child in value.items():
            if key in properties:
                errors.extend(validate_arguments(properties[key], child, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            errors.extend(validate_arguments(schema["items"], child, f"{path}[{index}]"))
    return errors


@dataclass(frozen=True)
class ToolResult:
    """工具的显式结果，避免把失败字符串误判为成功。"""
    content: str
    success: bool = True
    category: str = "ok"

    def __str__(self) -> str:
        return self.content


def normalize_tool_result(value: Any) -> ToolResult:
    if isinstance(value, ToolResult):
        return value
    text = str(value)
    lowered = text.strip().lower()
    legacy_error_prefixes = (
        "[失败]", "[错误]", "[沙箱]", "[超时]", "[grep 出错]",
        "error:", "cannot connect", "send failed", "wechat bridge returned",
    )
    failed = any(lowered.startswith(prefix.lower()) for prefix in legacy_error_prefixes)
    return ToolResult(text, success=not failed, category="legacy_error" if failed else "ok")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., Any]

    def validate(self, arguments: Any) -> list[str]:
        return validate_arguments(self.parameters, arguments)

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }}


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool

    def remove(self, name: str) -> Tool | None:
        return self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    from .fs import read_tool, write_tool
    from .shell import bash_tool
    from .edit import edit_tool
    from .grep import grep_tool
    from .glob import glob_tool
    from .web import web_fetch_tool, web_search_tool
    from .arxiv import arxiv_search_tool
    from .download import download_file_tool
    from .wechat import wechat_file_transfer_tool
    from .memory import remember_tool
    from .todo import todo_write_tool, update_todo_tool
    from .pdf import pdf_extract_text_tool, pdf_metadata_tool
    from .figure import paper_figure_analyze_tool
    from .schedule import schedule_task_tool
    from .experiment import (
        experiment_prepare_tool, experiment_smoke_test_tool, experiment_start_tool,
        experiment_status_tool, experiment_report_tool,
    )
    for tool in (
        read_tool, write_tool, bash_tool, edit_tool, grep_tool, glob_tool,
        arxiv_search_tool, web_search_tool, web_fetch_tool, download_file_tool, wechat_file_transfer_tool,
        remember_tool, todo_write_tool, update_todo_tool, pdf_extract_text_tool, pdf_metadata_tool,
        paper_figure_analyze_tool,
        schedule_task_tool,
        experiment_prepare_tool, experiment_smoke_test_tool, experiment_start_tool,
        experiment_status_tool, experiment_report_tool,
    ):
        registry.register(tool)
    return registry
