"""Command-line entry point for read-only Trace summaries and renderings."""
from __future__ import annotations

import argparse
from pathlib import Path

from .trace_report import cost_report, diagnose, render_html, render_markdown, render_terminal, simulate, summarize, write_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mini-openclaw-trace")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("summary", "cost", "simulate", "diagnose", "replay", "render"):
        command = sub.add_parser(name)
        command.add_argument("trace")
        if name in {"summary", "cost", "diagnose", "replay", "render"}:
            command.add_argument("--no-children", action="store_true", help="do not include matching subagent traces")
        if name == "replay":
            command.add_argument("--details", action="store_true")
        if name == "render":
            command.add_argument("--format", choices=("html", "markdown", "terminal"), default="html")
            command.add_argument("--output")
    args = parser.parse_args(argv)
    if args.command in {"summary", "cost", "simulate", "diagnose"}:
        import json
        reports = {
            "summary": summarize,
            "cost": cost_report,
            "simulate": simulate,
            "diagnose": diagnose,
        }
        if args.command in {"summary", "cost", "diagnose"}:
            report = reports[args.command](args.trace, include_children=not args.no_children)
        else:
            report = reports[args.command](args.trace)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "replay":
        print(render_terminal(args.trace, details=args.details, include_children=not args.no_children))
    elif args.format == "html":
        output = Path(args.output) if args.output else Path(args.trace).with_suffix(".html")
        print(write_html(args.trace, output, include_children=not args.no_children))
    elif args.format == "markdown":
        rendered = render_markdown(args.trace, include_children=not args.no_children)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
            print(args.output)
        else:
            print(rendered)
    else:
        print(render_terminal(args.trace, details=True, include_children=not args.no_children))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
