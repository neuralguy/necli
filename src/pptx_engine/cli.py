"""Agent-ready command line interface.

Successful invocations write exactly one JSON object to stdout. Diagnostics and
tracebacks never contaminate stdout, which makes the tool straightforward to
call from autonomous agents and orchestration systems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import PptxDocument, PptxError
from .operations import apply_operations
from .render import build_render_slide, render_png, render_svg

VERSION = "0.1.0"


def _emit(payload: dict[str, Any], pretty: bool = False) -> None:
    print(
        json.dumps(
            payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty
        ),
        flush=True,
    )


def _operations_from(value: str) -> list[dict[str, Any]]:
    candidate = Path(value)
    source = candidate.read_text(encoding="utf-8") if candidate.exists() else value
    decoded = json.loads(source)
    if isinstance(decoded, dict):
        decoded = decoded.get("operations", [decoded])
    if not isinstance(decoded, list) or not all(isinstance(x, dict) for x in decoded):
        raise PptxError(
            "operations must be a JSON array or {'operations': [...]} object"
        )
    return decoded


def _inspect(
    document: PptxDocument, slide_index: int | None, include_xml: bool = False
) -> dict[str, Any]:
    slides = (
        document.deck.slides if slide_index is None else [document.slide(slide_index)]
    )
    summary = []
    for slide in slides:
        elements = []

        def walk(
            items: list[Any], parent_id: str | None = None, _elements=elements
        ) -> None:
            for element in items:
                item = {
                    "id": element.id,
                    "type": element.type,
                    "name": element.name,
                    "placeholder": element.placeholder,
                    "transform": {
                        "x": element.transform.offset.x,
                        "y": element.transform.offset.y,
                        "cx": element.transform.offset.cx,
                        "cy": element.transform.offset.cy,
                        "rot": element.transform.rot,
                    },
                    "text": "\n".join(
                        "".join(r.text for r in p.runs) for p in element.text.paragraphs
                    )
                    if element.text
                    else None,
                    "media_ref": element.media_ref,
                    "parent_id": parent_id,
                }
                if include_xml:
                    item["original_xml"] = element.anchor.original_xml
                _elements.append(item)
                walk(element.children, element.id)

        walk(slide.elements)
        summary.append(
            {
                "index": document.deck.slides.index(slide),
                "path": slide.path,
                "element_count": len(elements),
                "background": slide.background,
                "elements": elements,
            }
        )
    return {
        "ok": True,
        "version": VERSION,
        "slide_size_emu": {"cx": document.deck.size.cx, "cy": document.deck.size.cy},
        "slide_count": len(document.deck.slides),
        "slides": summary,
    }


def command_create(args: argparse.Namespace) -> dict[str, Any]:
    document = PptxDocument.create_blank(args.width, args.height)
    document.save(args.output)
    return {
        "ok": True,
        "action": "create",
        "output": str(Path(args.output).resolve()),
        "slide_count": 1,
        "slide_size_emu": {"cx": args.width, "cy": args.height},
    }


def command_inspect(args: argparse.Namespace) -> dict[str, Any]:
    return _inspect(PptxDocument.open_file(args.input), args.slide, args.include_xml)


def command_export_model(args: argparse.Namespace) -> dict[str, Any]:
    document = PptxDocument.open_file(args.input)
    model = document.to_model()
    if args.output:
        Path(args.output).write_text(
            json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "ok": True,
            "action": "export-model",
            "output": str(Path(args.output).resolve()),
            "slide_count": len(document.deck.slides),
        }
    return {"ok": True, "model": model}


def command_apply(args: argparse.Namespace) -> dict[str, Any]:
    document = PptxDocument.open_file(args.input)
    operations = _operations_from(args.operations)
    results = apply_operations(document, operations, atomic=not args.no_atomic)
    document.save(args.output)
    payload: dict[str, Any] = {
        "ok": True,
        "action": "apply",
        "input": str(Path(args.input).resolve()),
        "output": str(Path(args.output).resolve()),
        "operation_count": len(results),
        "operations": results,
        "slide_count": len(document.deck.slides),
    }
    if args.model_output:
        Path(args.model_output).write_text(
            json.dumps(document.to_model(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload["model_output"] = str(Path(args.model_output).resolve())
    return payload


def command_render(args: argparse.Namespace) -> dict[str, Any]:
    document = PptxDocument.open_file(args.input)
    if args.format == "json":
        tree = build_render_slide(document, args.slide, args.width)
        if args.output:
            Path(args.output).write_text(
                json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {
                "ok": True,
                "action": "render",
                "format": "json",
                "output": str(Path(args.output).resolve()),
                "slide_index": args.slide,
            }
        return {"ok": True, "action": "render", "format": "json", "slide": tree}
    if not args.output:
        raise PptxError("--output is required for svg and png rendering")
    if args.format == "svg":
        Path(args.output).write_text(
            render_svg(document, args.slide, args.width), encoding="utf-8"
        )
    else:
        render_png(document, args.slide, args.output, args.width)
    return {
        "ok": True,
        "action": "render",
        "format": args.format,
        "output": str(Path(args.output).resolve()),
        "slide_index": args.slide,
        "width": args.width,
    }


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    document = PptxDocument.open_file(args.input)
    package = document.archive
    required = ["[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"]
    missing = [item for item in required if item not in package.entries]
    slide_paths = [slide.path for slide in document.deck.slides]
    return {
        "ok": not missing,
        "action": "validate",
        "input": str(Path(args.input).resolve()),
        "missing_parts": missing,
        "slide_count": len(slide_paths),
        "slide_paths": slide_paths,
        "warnings": [] if not missing else ["PPTX package misses required parts"],
    }


def _agent_request(request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    if action == "create":
        output = request["output"]
        document = PptxDocument.create_blank(
            int(request.get("width", 12_192_000)), int(request.get("height", 6_858_000))
        )
        document.save(output)
        return {
            "ok": True,
            "request_id": request.get("request_id"),
            "action": action,
            "output": str(Path(output).resolve()),
        }
    if action in {"inspect", "render", "apply", "validate", "export-model"}:
        input_file = request.get("input")
        if not input_file:
            raise PptxError(f"agent action {action} requires input")
        document = PptxDocument.open_file(input_file)
        if action == "inspect":
            result = _inspect(
                document, request.get("slide"), bool(request.get("include_xml", False))
            )
        elif action == "validate":
            required = ["[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"]
            missing = [
                item for item in required if item not in document.archive.entries
            ]
            result = {
                "ok": not missing,
                "missing_parts": missing,
                "slide_count": len(document.deck.slides),
            }
        elif action == "export-model":
            if request.get("output"):
                Path(request["output"]).write_text(
                    json.dumps(document.to_model(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result = {"ok": True, "output": str(Path(request["output"]).resolve())}
            else:
                result = {"ok": True, "model": document.to_model()}
        elif action == "apply":
            results = apply_operations(
                document,
                request.get("operations", []),
                atomic=bool(request.get("atomic", True)),
            )
            output = request["output"]
            document.save(output)
            result = {
                "ok": True,
                "output": str(Path(output).resolve()),
                "operations": results,
                "slide_count": len(document.deck.slides),
            }
        else:
            fmt, output, slide, width = (
                request.get("format", "svg"),
                request.get("output"),
                int(request.get("slide", 0)),
                int(request.get("width", 1280)),
            )
            if fmt == "json":
                tree = build_render_slide(document, slide, width)
                if output:
                    Path(output).write_text(
                        json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    result = {
                        "ok": True,
                        "output": str(Path(output).resolve()),
                        "format": fmt,
                    }
                else:
                    result = {"ok": True, "slide": tree, "format": fmt}
            elif fmt == "svg":
                if not output:
                    raise PptxError("render SVG requires output")
                Path(output).write_text(
                    render_svg(document, slide, width), encoding="utf-8"
                )
                result = {
                    "ok": True,
                    "output": str(Path(output).resolve()),
                    "format": fmt,
                }
            elif fmt == "png":
                if not output:
                    raise PptxError("render PNG requires output")
                render_png(document, slide, output, width)
                result = {
                    "ok": True,
                    "output": str(Path(output).resolve()),
                    "format": fmt,
                }
            else:
                raise PptxError("format must be json, svg, or png")
        result["request_id"] = request.get("request_id")
        result["action"] = action
        return result
    raise PptxError(
        "agent request action must be create, inspect, export-model, apply, render, or validate"
    )


def command_agent(_: argparse.Namespace) -> int:
    """Serve newline-delimited JSON requests until stdin closes."""
    exit_code = 0
    for number, raw in enumerate(sys.stdin, 1):
        if not raw.strip():
            continue
        request_id = None
        try:
            request = json.loads(raw)
            request_id = (
                request.get("request_id") if isinstance(request, dict) else None
            )
            answer = _agent_request(request)
        except Exception as exc:
            exit_code = 1
            answer = {
                "ok": False,
                "code": "PPTX_AGENT_ERROR",
                "line": number,
                "request_id": request_id,
                "error": str(exc),
            }
        _emit(answer)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pptx-agent", description="Pure-Python PPTX engine and agent-ready CLI"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument(
        "--pretty", action="store_true", help="pretty-print the JSON response"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a blank valid PPTX")
    create.add_argument("--output", required=True)
    create.add_argument("--width", type=int, default=12_192_000)
    create.add_argument("--height", type=int, default=6_858_000)
    create.set_defaults(handler=command_create)
    inspect = sub.add_parser("inspect", help="inspect slides and elements")
    inspect.add_argument("--input", required=True)
    inspect.add_argument("--slide", type=int)
    inspect.add_argument("--include-xml", action="store_true")
    inspect.set_defaults(handler=command_inspect)
    model = sub.add_parser("export-model", help="export full JSON model")
    model.add_argument("--input", required=True)
    model.add_argument("--output")
    model.set_defaults(handler=command_export_model)
    apply = sub.add_parser("apply", help="apply a JSON operation batch atomically")
    apply.add_argument("--input", required=True)
    apply.add_argument(
        "--operations", required=True, help="JSON file path or inline JSON"
    )
    apply.add_argument("--output", required=True)
    apply.add_argument("--model-output")
    apply.add_argument("--no-atomic", action="store_true")
    apply.set_defaults(handler=command_apply)
    render = sub.add_parser(
        "render", help="render one slide to SVG, PNG, or render-tree JSON"
    )
    render.add_argument("--input", required=True)
    render.add_argument("--slide", required=True, type=int)
    render.add_argument("--format", required=True, choices=("svg", "png", "json"))
    render.add_argument("--output")
    render.add_argument("--width", type=int, default=1280)
    render.set_defaults(handler=command_render)
    validate = sub.add_parser("validate", help="validate PPTX package essentials")
    validate.add_argument("--input", required=True)
    validate.set_defaults(handler=command_validate)
    agent = sub.add_parser(
        "agent", help="serve newline-delimited JSON agent requests on stdin"
    )
    agent.set_defaults(handler=command_agent)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "agent":
            return args.handler(args)
        _emit(args.handler(args), args.pretty)
        return 0
    except Exception as exc:
        _emit({"ok": False, "code": "PPTX_AGENT_ERROR", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
