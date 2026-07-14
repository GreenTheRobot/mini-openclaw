"""Read both legacy event traces and v2 span traces without side effects."""
from __future__ import annotations

import json
import os
from html import escape
from pathlib import Path
from typing import Any

from agent.tracer import redact_for_trace


def _display_safe(value: Any) -> Any:
    """Older traces predate write-time redaction; never expose them in a view."""
    return redact_for_trace(value, Path.cwd())


def load_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Trace 第 {line_number} 行不是合法 JSON") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Trace 第 {line_number} 行必须是 JSON 对象")
        records.append(item)
    return records


def _child_trace_paths(path: Path) -> list[Path]:
    directory = path.parent / "subagents"
    if not directory.exists():
        return []
    return sorted(child for child in directory.glob(f"{path.stem}.*.jsonl") if child.is_file())


def _trace_paths(path: str | Path, include_children: bool = False) -> list[Path]:
    trace_path = Path(path)
    return [trace_path] + (_child_trace_paths(trace_path) if include_children else [])


def _load_trace_records(path: str | Path, include_children: bool = False) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _trace_paths(path, include_children):
        records.extend(load_records(item))
    return records


def spans_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    starts = {str(item.get("span_id")): item for item in records if item.get("event") == "span_start"}
    spans: list[dict[str, Any]] = []
    for item in records:
        if item.get("event") != "span_end":
            continue
        start = starts.get(str(item.get("span_id")), {})
        spans.append({
            "span_id": item.get("span_id"),
            "parent_span_id": start.get("parent_span_id"),
            "kind": item.get("kind", start.get("kind", "internal")),
            "name": item.get("name", start.get("name", "?")),
            "status": item.get("status", "ok"),
            "duration_ms": item.get("duration_ms") or 0,
            "usage": item.get("usage") or {},
            "input_preview": start.get("input_preview", ""),
            "output_preview": item.get("output_preview", ""),
            "error_preview": item.get("error_preview", ""),
            "attributes": item.get("attributes") or start.get("attributes") or {},
            "sequence": item.get("sequence", 0),
        })
    return spans


def _legacy_spans(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for item in records:
        if item.get("event") == "step":
            spans.append({
                "kind": "llm", "name": "decide", "status": "ok" if item.get("success", True) else "error",
                "duration_ms": item.get("duration_ms", 0),
                "usage": {"prompt_tokens": item.get("prompt_tokens", 0), "completion_tokens": item.get("completion_tokens", 0)},
                "attributes": _display_safe({"turn": item.get("step"), "tool_calls": item.get("tool_calls", [])}),
                "sequence": item.get("sequence", 0),
            })
        elif item.get("event") == "tool_result":
            spans.append({
                "kind": "tool", "name": item.get("tool", "?"),
                "status": "ok" if item.get("success") else "error",
                "duration_ms": item.get("duration_ms", 0), "usage": {},
                "input_preview": str(_display_safe(item.get("arguments", ""))),
                "output_preview": str(_display_safe(item.get("observation", ""))),
                "attributes": _display_safe({"turn": item.get("step"), "tool_call_id": item.get("tool_call_id", "")}),
                "sequence": item.get("sequence", 0),
            })
    return spans


def _price_config() -> tuple[float | None, float | None, str]:
    raw_input = os.environ.get("OPENCLAW_INPUT_USD_PER_MILLION", "").strip()
    raw_output = os.environ.get("OPENCLAW_OUTPUT_USD_PER_MILLION", "").strip()
    if not raw_input and not raw_output:
        return None, None, "unpriced"
    try:
        input_price = float(raw_input or "0")
        output_price = float(raw_output or "0")
    except ValueError as exc:
        raise ValueError("OPENCLAW_*_USD_PER_MILLION 必须是数字") from exc
    if input_price == 0 and output_price == 0:
        return None, None, "unpriced"
    return input_price, output_price, "estimated"


def _usage_metrics(usage: dict[str, Any]) -> dict[str, int]:
    def number(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 0
        return 0

    prompt = number("prompt_tokens", "input_tokens")
    completion = number("completion_tokens", "output_tokens")
    total = number("total_tokens") or prompt + completion
    cached = number("cached_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens")
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total, "cached_tokens": cached}


def enrich_spans(spans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    input_price, output_price, pricing_status = _price_config()
    enriched: list[dict[str, Any]] = []
    for span in spans:
        item = dict(span)
        usage = _usage_metrics(dict(item.get("usage") or {})) if item.get("kind") == "llm" else {}
        item["usage"] = {**dict(item.get("usage") or {}), **usage}
        if item.get("kind") == "llm" and pricing_status == "estimated":
            item["estimated_cost_usd"] = round(
                (usage["prompt_tokens"] * float(input_price) + usage["completion_tokens"] * float(output_price)) / 1_000_000,
                10,
            )
        else:
            item["estimated_cost_usd"] = None
        enriched.append(item)
    return enriched, {
        "status": pricing_status,
        "input_usd_per_million": input_price,
        "output_usd_per_million": output_price,
    }


def _wall_duration_ms(records: list[dict[str, Any]], spans: list[dict[str, Any]]) -> float:
    run_starts = [float(item["ts"]) for item in records if item.get("event") == "run_start" and item.get("ts") is not None]
    run_ends = [float(item["ts"]) for item in records if item.get("event") == "run_end" and item.get("ts") is not None]
    if run_starts and run_ends:
        return round(max(run_ends) * 1000 - min(run_starts) * 1000, 2)
    roots = [span for span in spans if span.get("kind") == "agent" and span.get("name") == "run"]
    if roots:
        return round(max(float(span.get("duration_ms", 0) or 0) for span in roots), 2)
    return round(sum(float(span.get("duration_ms", 0) or 0) for span in spans), 2)


def summarize(path: str | Path, include_children: bool = False) -> dict[str, Any]:
    records = _load_trace_records(path, include_children)
    raw_spans = spans_from_records(records) or _legacy_spans(records)
    spans, pricing = enrich_spans(raw_spans)
    llm = [span for span in spans if span.get("kind") == "llm"]
    tools = [span for span in spans if span.get("kind") == "tool"]
    prompt_tokens = sum(int((span.get("usage") or {}).get("prompt_tokens", 0) or 0) for span in llm)
    completion_tokens = sum(int((span.get("usage") or {}).get("completion_tokens", 0) or 0) for span in llm)
    cached_tokens = sum(int((span.get("usage") or {}).get("cached_tokens", 0) or 0) for span in llm)
    costs = [float(span["estimated_cost_usd"]) for span in llm if span.get("estimated_cost_usd") is not None]
    errors = sum(1 for span in spans if span.get("status") not in {"ok", "success", "completed"})
    visible_spans = [span for span in spans if not (span.get("kind") == "agent" and span.get("name") == "run")]
    slowest = max(visible_spans, key=lambda span: float(span.get("duration_ms", 0) or 0), default=None)
    priciest = max((span for span in llm if span.get("estimated_cost_usd") is not None), key=lambda span: float(span["estimated_cost_usd"]), default=None)
    prefix_digests = [str((span.get("attributes") or {}).get("stable_prefix_digest", "")) for span in llm]
    known_prefixes = [digest for digest in prefix_digests if digest]
    adjacent_pairs = max(0, len(known_prefixes) - 1)
    matching_pairs = sum(1 for left, right in zip(known_prefixes, known_prefixes[1:]) if left == right)
    final = next((record for record in reversed(records) if record.get("event") == "run_end"), {})
    return {
        "run_status": final.get("status", "unknown"),
        "run_reason": final.get("reason", ""),
        "events": len(records),
        "trace_files": len(_trace_paths(path, include_children)),
        "spans": len(spans),
        "steps": len(llm),
        "tool_calls": len(tools),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cached_tokens": cached_tokens,
        "pricing": pricing,
        "estimated_cost_usd": round(sum(costs), 10) if costs else None,
        "errors": errors,
        "duration_ms": _wall_duration_ms(records, spans),
        "llm_duration_ms": round(sum(float(span.get("duration_ms", 0) or 0) for span in llm), 2),
        "tool_duration_ms": round(sum(float(span.get("duration_ms", 0) or 0) for span in tools), 2),
        "aggregate_span_duration_ms": round(sum(float(span.get("duration_ms", 0) or 0) for span in visible_spans), 2),
        "slowest_span": _span_summary(slowest),
        "priciest_span": _span_summary(priciest),
        "prompt_token_series": [int((span.get("usage") or {}).get("prompt_tokens", 0) or 0) for span in llm],
        "prefix_cache": {
            "available": bool(known_prefixes),
            "unique_stable_prefixes": len(set(known_prefixes)),
            "adjacent_matching_pairs": matching_pairs,
            "adjacent_pairs": adjacent_pairs,
            "adjacent_match_ratio": round(matching_pairs / adjacent_pairs, 3) if adjacent_pairs else None,
            "stable_prefix_chars": max((int((span.get("attributes") or {}).get("stable_prefix_chars", 0) or 0) for span in llm), default=0),
        },
    }


def _span_summary(span: dict[str, Any] | None) -> dict[str, Any] | None:
    if not span:
        return None
    return {
        "kind": span.get("kind"), "name": span.get("name"), "status": span.get("status"),
        "duration_ms": span.get("duration_ms"), "estimated_cost_usd": span.get("estimated_cost_usd"),
        "turn": (span.get("attributes") or {}).get("turn"),
    }


def cost_report(path: str | Path) -> dict[str, Any]:
    """Return per-LLM cost rows plus the aggregate report for CLI and renderers."""
    records = load_records(path)
    spans, pricing = enrich_spans(spans_from_records(records) or _legacy_spans(records))
    llm = [span for span in spans if span.get("kind") == "llm"]
    return {
        "pricing": pricing,
        "spans": [
            {
                "turn": (span.get("attributes") or {}).get("turn"),
                "name": span.get("name"),
                "prompt_tokens": (span.get("usage") or {}).get("prompt_tokens", 0),
                "completion_tokens": (span.get("usage") or {}).get("completion_tokens", 0),
                "cached_tokens": (span.get("usage") or {}).get("cached_tokens", 0),
                "estimated_cost_usd": span.get("estimated_cost_usd"),
            }
            for span in llm
        ],
        "summary": summarize(path),
    }


def simulate(path: str | Path) -> dict[str, Any]:
    """Validate a recorded call/result sequence without invoking any tool or model."""
    records = load_records(path)
    pending: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    consumed = 0
    for record in records:
        if record.get("event") == "step":
            for index, call in enumerate(record.get("tool_calls") or []):
                call_id = str(call.get("id") or f"legacy-{record.get('step')}-{index}")
                if call_id in pending:
                    issues.append({"kind": "duplicate_call_id", "call_id": call_id})
                pending[call_id] = {"tool": call.get("name"), "step": record.get("step")}
        elif record.get("event") == "tool_result":
            call_id = str(record.get("tool_call_id") or "")
            if call_id and call_id in pending:
                pending.pop(call_id)
                consumed += 1
            elif call_id:
                issues.append({"kind": "orphan_tool_result", "call_id": call_id, "tool": record.get("tool")})
            else:
                # Legacy records did not retain IDs. Match the oldest same-name
                # call so old traces remain replayable, while marking ambiguity.
                candidates = [key for key, value in pending.items() if value.get("tool") == record.get("tool")]
                if len(candidates) == 1:
                    pending.pop(candidates[0])
                    consumed += 1
                elif candidates:
                    pending.pop(candidates[0])
                    consumed += 1
                    issues.append({"kind": "legacy_ambiguous_tool_result", "tool": record.get("tool")})
    for call_id, call in pending.items():
        issues.append({"kind": "missing_tool_result", "call_id": call_id, **call})
    final = next((record for record in reversed(records) if record.get("event") == "run_end"), {})
    return {
        "mode": "mock",
        "side_effects": False,
        "model_calls_reexecuted": 0,
        "tool_calls_reexecuted": 0,
        "tool_results_consumed": consumed,
        "issues": issues,
        "final_status": final.get("status", "unknown"),
    }


def diagnose(path: str | Path, *, slow_ms: float = 30_000) -> dict[str, Any]:
    """Produce deterministic debugging findings from a persisted trace."""
    records = load_records(path)
    raw_spans = spans_from_records(records) or _legacy_spans(records)
    spans, _ = enrich_spans(raw_spans)
    summary = summarize(path)
    findings: list[dict[str, Any]] = []
    for span in spans:
        duration = float(span.get("duration_ms", 0) or 0)
        if duration >= slow_ms:
            findings.append({
                "severity": "warning", "kind": "slow_span", "name": span.get("name"),
                "kind_detail": span.get("kind"), "duration_ms": duration,
                "turn": (span.get("attributes") or {}).get("turn"),
            })
        if span.get("status") not in {"ok", "success", "completed"}:
            findings.append({
                "severity": "error", "kind": "failed_span", "name": span.get("name"),
                "status": span.get("status"), "output": span.get("output_preview", "")[:240],
            })
    series = summary["prompt_token_series"]
    if len(series) >= 3 and series[-1] > series[0] * 1.5:
        findings.append({
            "severity": "warning", "kind": "prompt_growth",
            "first_prompt_tokens": series[0], "last_prompt_tokens": series[-1],
            "growth_ratio": round(series[-1] / max(1, series[0]), 2),
        })
    prefix = summary["prefix_cache"]
    if prefix["available"] and prefix["unique_stable_prefixes"] > 1:
        findings.append({
            "severity": "warning", "kind": "unstable_prefix",
            "unique_stable_prefixes": prefix["unique_stable_prefixes"],
            "adjacent_match_ratio": prefix["adjacent_match_ratio"],
        })
    if summary["tool_duration_ms"] > summary["duration_ms"] * 1.01:
        findings.append({
            "severity": "warning", "kind": "duration_inconsistency",
            "wall_duration_ms": summary["duration_ms"], "tool_duration_ms": summary["tool_duration_ms"],
        })
    signatures: dict[str, int] = {}
    for record in records:
        if record.get("event") != "step":
            continue
        for call in record.get("tool_calls") or []:
            signature = json.dumps([call.get("name"), _display_safe(call.get("arguments", {}))], ensure_ascii=False, sort_keys=True)
            signatures[signature] = signatures.get(signature, 0) + 1
    for signature, count in signatures.items():
        if count > 1:
            findings.append({"severity": "info", "kind": "repeated_tool_signature", "count": count, "signature": signature[:300]})
    for record in records:
        if record.get("event") in {"final_blocked", "protocol_repaired", "compaction", "error_budget_exhausted"}:
            findings.append({"severity": "info", "kind": record["event"], "details": _display_safe(record)})
    simulation = simulate(path)
    findings.extend({"severity": "error", "kind": issue["kind"], "details": issue} for issue in simulation["issues"])
    return {"summary": summary, "findings": findings, "simulation": simulation}


def replay(path: str | Path) -> None:
    print(render_terminal(path, details=True))


def _duration(value: Any) -> str:
    milliseconds = float(value or 0)
    if milliseconds >= 60_000:
        return f"{milliseconds / 60_000:.2f}m"
    if milliseconds >= 1_000:
        return f"{milliseconds / 1_000:.2f}s"
    return f"{milliseconds:.0f}ms"


def _usage_text(span: dict[str, Any]) -> str:
    usage = span.get("usage") or {}
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    if not prompt and not completion:
        return "-"
    cost = span.get("estimated_cost_usd")
    suffix = f" · ${float(cost):.6f}" if cost is not None else " · 未计价"
    return f"{prompt}+{completion} tok{suffix}"


def render_terminal(path: str | Path, *, details: bool = False) -> str:
    records = load_records(path)
    spans, _ = enrich_spans(spans_from_records(records) or _legacy_spans(records))
    lines = ["# Trace Replay", "", "序号  类型    名称                  状态       耗时       Token"]
    for index, span in enumerate(spans, 1):
        lines.append(
            f"{index:>4}  {str(span.get('kind', '?')).upper():<6} "
            f"{str(span.get('name', '?'))[:20]:<20} {str(span.get('status', '?')).upper():<10} "
            f"{_duration(span.get('duration_ms')):>8}  {_usage_text(span)}"
        )
        if details:
            attributes = span.get("attributes") or {}
            if attributes:
                lines.append("      属性: " + json.dumps(attributes, ensure_ascii=False, sort_keys=True)[:500])
            if span.get("input_preview"):
                lines.append("      输入: " + str(span["input_preview"])[:300])
            if span.get("output_preview"):
                lines.append("      输出: " + str(span["output_preview"])[:300])
            if span.get("error_preview"):
                lines.append("      错误: " + str(span["error_preview"])[:300])
    lines.extend(["", "# Summary", json.dumps(summarize(path), ensure_ascii=False, indent=2)])
    return "\n".join(lines)


def render_markdown(path: str | Path) -> str:
    records = load_records(path)
    spans, _ = enrich_spans(spans_from_records(records) or _legacy_spans(records))
    lines = ["# Trace Replay", "", "| # | 类型 | 名称 | 状态 | 耗时 | Token |", "|---:|---|---|---|---:|---:|"]
    for index, span in enumerate(spans, 1):
        lines.append(
            f"| {index} | {span.get('kind', '?')} | {span.get('name', '?')} | {span.get('status', '?')} "
            f"| {_duration(span.get('duration_ms'))} | {_usage_text(span)} |"
        )
    lines.extend(["", "## Summary", "", "```json", json.dumps(summarize(path), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines)


def render_html(path: str | Path) -> str:
    records = load_records(path)
    spans, _ = enrich_spans(spans_from_records(records) or _legacy_spans(records))
    rows = []
    for index, span in enumerate(spans, 1):
        status = str(span.get("status", "?"))
        detail = "<br>".join(
            part for part in [
                f"<b>属性</b> {escape(json.dumps(span.get('attributes') or {}, ensure_ascii=False))}" if span.get("attributes") else "",
                f"<b>输入</b> {escape(str(span.get('input_preview', '')))}" if span.get("input_preview") else "",
                f"<b>输出</b> {escape(str(span.get('output_preview', '')))}" if span.get("output_preview") else "",
                f"<b>错误</b> {escape(str(span.get('error_preview', '')))}" if span.get("error_preview") else "",
            ] if part
        )
        rows.append(
            "<tr class='" + ("error" if status not in {"ok", "success", "completed"} else "") + "'>"
            f"<td>{index}</td><td>{escape(str(span.get('kind', '?')))}</td>"
            f"<td>{escape(str(span.get('name', '?')))}</td><td>{escape(status)}</td>"
            f"<td>{_duration(span.get('duration_ms'))}</td><td>{escape(_usage_text(span))}</td>"
            f"<td>{detail}</td></tr>"
        )
    summary = escape(json.dumps(summarize(path), ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>mini-OpenClaw Trace</title>
<style>body{{font:14px system-ui,sans-serif;margin:32px;color:#182230}} table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #dbe2ea;padding:8px;text-align:left;vertical-align:top}}th{{background:#16294a;color:white}}tr.error{{background:#fff0f0}}pre{{background:#f4f6f8;padding:14px;border-radius:6px;overflow:auto}}td:last-child{{max-width:520px;word-break:break-word}}</style>
<h1>mini-OpenClaw Trace Replay</h1><p>只读渲染；不会重新执行工具。</p>
<table><thead><tr><th>#</th><th>类型</th><th>名称</th><th>状态</th><th>耗时</th><th>Token</th><th>详情</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Summary</h2><pre>{summary}</pre></html>"""


def write_html(path: str | Path, output: str | Path) -> Path:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(path), encoding="utf-8")
    return target
