from tools.base import Tool, ToolResult, normalize_tool_result, validate_arguments


def test_validate_arguments_reports_nested_errors():
    schema = {"type": "object", "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
    }, "required": ["name", "count"], "additionalProperties": False}
    assert validate_arguments(schema, {"name": 3, "extra": True}) == [
        "arguments 缺少必填字段 count",
        "arguments 包含未知字段 extra",
        "arguments.name 应为 string，实际为 int",
    ]


def test_tool_validate_accepts_valid_arguments():
    tool = Tool("demo", "", {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"]}, lambda x: str(x))
    assert tool.validate({"x": 1.5}) == []

def test_normalize_tool_result_preserves_explicit_failure():
    result = normalize_tool_result(ToolResult("command failed", success=False, category="nonzero_exit"))
    assert result.success is False
    assert result.category == "nonzero_exit"


def test_normalize_tool_result_detects_legacy_failure_prefix():
    result = normalize_tool_result("[失败] cannot read file")
    assert result.success is False
    assert result.category == "legacy_error"