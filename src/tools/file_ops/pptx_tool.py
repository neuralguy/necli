"""Semantic PPTX agent tool backed exclusively by :mod:`pptx_engine`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logger import logger
from pptx_engine import (
    PptxDocument,
    PptxError,
    apply_operations,
    build_render_slide,
    render_png,
    render_svg,
)
from tools._paths import resolve_path
from tools.models import ToolCall, ToolResult

_DEFAULT_WIDTH = 12_192_000
_DEFAULT_HEIGHT = 6_858_000


def _element_text(element) -> str:
    if element.text is None:
        return ""
    return " / ".join(
        "".join(run.text for run in paragraph.runs)
        for paragraph in element.text.paragraphs
    )


def _one_line(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _walk_elements(elements, parent_id: str | None = None):
    for element in elements:
        yield element, parent_id
        yield from _walk_elements(element.children, element.id)


def read_pptx_compact(path: Path) -> str:
    """Return a low-token, line-addressable presentation overview."""
    document = PptxDocument.open_bytes(path.read_bytes())
    size = document.deck.size
    lines = [
        (
            f"[PPTX slides: {len(document.deck.slides)}; size={size.cx}x{size.cy} EMU; "
            'edit with pptx(action="edit") using slide_index and element_id]'
        )
    ]
    for slide_index, slide in enumerate(document.deck.slides):
        flat = list(_walk_elements(slide.elements))
        lines.append(f"slide {slide_index} | {len(flat)} elements")
        for element, parent_id in flat:
            rect = element.transform.offset
            parent = f" parent={parent_id}" if parent_id else ""
            text = _one_line(_element_text(element) or element.name or "")
            lines.append(
                f"  {element.id} {element.type}{parent} "
                f"@{rect.x},{rect.y} {rect.cx}x{rect.cy} | {text}"
            )
    return "\n".join(lines)


def _element_json(
    element, parent_id: str | None = None, include_xml: bool = False
) -> list[dict[str, Any]]:
    rect = element.transform.offset
    item: dict[str, Any] = {
        "id": element.id,
        "type": element.type,
        "name": element.name,
        "parentId": parent_id,
        "text": _element_text(element) or None,
        "transform": {
            "x": rect.x,
            "y": rect.y,
            "cx": rect.cx,
            "cy": rect.cy,
            "rot": element.transform.rot,
            "flipH": element.transform.flip_h,
            "flipV": element.transform.flip_v,
        },
        "fill": element.fill,
        "stroke": element.stroke,
        "mediaRef": element.media_ref,
    }
    if include_xml:
        item["originalXml"] = element.anchor.original_xml
    result = [item]
    for child in element.children:
        result.extend(_element_json(child, element.id, include_xml))
    return result


def _inspect(
    document: PptxDocument, slide_index: int | None, include_xml: bool
) -> dict[str, Any]:
    indexed_slides = (
        list(enumerate(document.deck.slides))
        if slide_index is None
        else [(slide_index, document.slide(slide_index))]
    )
    slides = []
    for index, slide in indexed_slides:
        elements = []
        for element in slide.elements:
            elements.extend(_element_json(element, include_xml=include_xml))
        slides.append(
            {
                "index": index,
                "path": slide.path,
                "background": slide.background,
                "elements": elements,
            }
        )
    return {
        "slideCount": len(document.deck.slides),
        "slideSizeEmu": {"cx": document.deck.size.cx, "cy": document.deck.size.cy},
        "slides": slides,
    }


def _help_text() -> str:
    return (
        "PPTX actions:\n"
        "create: {action,path,width?,height?,operations?}\n"
        "edit: {action,path,out?,operations:[...],atomic?}\n"
        "inspect: {action,path,slide?,includeXml?,fullModel?}\n"
        "render: {action,path,slide,format:'svg'|'png'|'json',out?,width?}\n"
        "validate: {action,path}\n"
        "Operations: add_shape, add_text, add_picture, add_table, set_text, replace_all, "
        "transform, set_fill, set_stroke, set_font, set_paragraph_format, edit_table_cell, "
        "delete_element, group, ungroup, set_picture_crop, set_picture_opacity, "
        "set_slide_background, set_slide_hidden, duplicate_slide, insert_blank_slide, "
        "delete_slide, move_slide, set_slide_size. Coordinates and sizes are EMU.\n"
        "Slide index rules: element edits, duplicate_slide, and delete_slide require an existing "
        "zero-based slide_index in 0..slide_count-1. insert_blank_slide uses a destination position "
        "in 0..slide_count inclusive; slide_index=slide_count appends. move_slide requires existing "
        "from_index and to_index values in 0..slide_count-1.\n"
        "Creation recipe for N slides: create starts with slide 0; first insert blank slides at "
        "slide_index 1 through N-1, then add content to slides 0 through N-1 in the same atomic batch."
    )


def _result(call: ToolCall, output: str) -> ToolResult:
    return ToolResult(
        name="pptx", status="ok", output=output, exit_code=0, command=call.command
    )


def _operation_batch(args: dict[str, Any], *, required: bool) -> list[dict[str, Any]]:
    operations = args.get("operations")
    if operations is None or operations == []:
        if required:
            raise PptxError("edit requires a non-empty operations array")
        return []
    if not isinstance(operations, list):
        raise PptxError("operations must be an array of objects")
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PptxError(f"operations[{index}] must be an object")
        if not isinstance(operation.get("op"), str) or not operation["op"].strip():
            keys = ", ".join(sorted(map(str, operation))) or "none"
            raise PptxError(
                f"operations[{index}] requires string field 'op' (received keys: {keys}); "
                'call pptx(action="help") for supported operations'
            )
    return operations


def pptx(call: ToolCall) -> ToolResult:
    args = call.args or {}
    action = str(args.get("action") or "").lower()
    if action == "help":
        return _result(call, _help_text())
    if action not in {"create", "edit", "inspect", "render", "validate"}:
        return ToolResult(
            name="pptx",
            status="error",
            output="PPTX error: action must be create, edit, inspect, render, validate, or help",
            exit_code=1,
            command=call.command,
        )
    if not args.get("path"):
        return ToolResult(
            name="pptx",
            status="error",
            output="PPTX error: path is required",
            exit_code=1,
            command=call.command,
        )

    try:
        path = resolve_path(str(args["path"]), extensions=(".pptx",))
        if path.suffix.lower() != ".pptx":
            path = path.with_suffix(".pptx")

        if action == "create":
            if path.exists() and args.get("overwrite", True) is False:
                raise PptxError(f"file already exists: {path}")
            document = PptxDocument.create_blank(
                int(args.get("width", _DEFAULT_WIDTH)),
                int(args.get("height", _DEFAULT_HEIGHT)),
            )
            operations = _operation_batch(args, required=False)
            results = (
                apply_operations(document, operations, atomic=True)
                if operations
                else []
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(document.to_bytes())
            return _result(
                call,
                f"Created {path} ({len(document.deck.slides)} slides, {len(results)} ops)",
            )

        document = PptxDocument.open_bytes(path.read_bytes())
        if action == "inspect":
            if args.get("fullModel"):
                payload = document.to_model()
            else:
                slide = args.get("slide")
                payload = _inspect(
                    document,
                    int(slide) if slide is not None else None,
                    bool(args.get("includeXml", False)),
                )
            return _result(
                call, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

        if action == "validate":
            required = ["[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"]
            missing = [
                part for part in required if part not in document.archive.entries
            ]
            payload = {
                "ok": not missing,
                "missingParts": missing,
                "slideCount": len(document.deck.slides),
            }
            return _result(
                call, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

        if action == "edit":
            operations = _operation_batch(args, required=True)
            results = apply_operations(
                document, operations, atomic=bool(args.get("atomic", True))
            )
            out = resolve_path(str(args.get("out") or path), extensions=(".pptx",))
            if out.suffix.lower() != ".pptx":
                out = out.with_suffix(".pptx")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(document.to_bytes())
            return _result(
                call,
                f"Edited {out} ({len(document.deck.slides)} slides, {len(results)} ops)",
            )

        slide = int(args.get("slide", 0))
        fmt = str(args.get("format") or "svg").lower()
        width = int(args.get("width", 1280))
        if fmt == "json":
            tree = build_render_slide(document, slide, width)
            if args.get("out"):
                out = resolve_path(str(args["out"]), extensions=(".json",))
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return _result(call, f"Rendered slide {slide} to {out}")
            return _result(
                call, json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
            )
        if fmt not in {"svg", "png"} or not args.get("out"):
            raise PptxError("render svg/png requires format and out")
        out = resolve_path(str(args["out"]), extensions=(f".{fmt}",))
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "svg":
            out.write_text(render_svg(document, slide, width), encoding="utf-8")
        else:
            render_png(document, slide, str(out), width)
        return _result(call, f"Rendered slide {slide} to {out}")
    except Exception as exc:
        logger.opt(exception=True).warning("pptx tool failed: {}", exc)
        return ToolResult(
            name="pptx",
            status="error",
            output=f"PPTX error: {exc}",
            exit_code=1,
            command=call.command,
        )


__all__ = ["pptx", "read_pptx_compact"]
