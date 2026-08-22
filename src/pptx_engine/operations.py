"""Declarative operation layer used by the CLI and agent JSONL protocol."""

from __future__ import annotations

from base64 import b64decode
from pathlib import Path
from typing import Any

from .engine import PptxDocument, PptxError


def _slide_index(op: dict[str, Any]) -> int:
    if "slide_index" not in op:
        raise PptxError(f"operation {op.get('op', '<unknown>')} requires slide_index")
    return int(op["slide_index"])


def apply_operation(document: PptxDocument, op: dict[str, Any]) -> dict[str, Any]:
    """Apply one documented JSON operation and return a compact result object."""
    name = op.get("op")
    if not isinstance(name, str):
        raise PptxError("operation requires string field 'op'")
    if name == "add_shape":
        element = document.add_shape(
            _slide_index(op),
            shape=op.get("shape", "rect"),
            transform=op.get("transform"),
            fill=op.get("fill"),
            stroke=op.get("stroke"),
            text=op.get("text"),
            name=op.get("name"),
        )
        return {"op": name, "element_id": element.id}
    if name == "add_text":
        element = document.add_text(
            _slide_index(op),
            text=str(op.get("text", "")),
            transform=op.get("transform"),
            style=op.get("style"),
            name=op.get("name"),
        )
        return {"op": name, "element_id": element.id}
    if name == "add_picture":
        if op.get("image_path"):
            data = Path(op["image_path"]).read_bytes()
            extension = Path(op["image_path"]).suffix.lstrip(".") or op.get(
                "extension", "png"
            )
        elif op.get("data_base64"):
            data = b64decode(op["data_base64"])
            extension = op.get("extension", "png")
        else:
            raise PptxError("add_picture requires image_path or data_base64")
        element = document.add_picture(
            _slide_index(op), data, extension, op.get("transform"), op.get("name")
        )
        return {"op": name, "element_id": element.id, "media_ref": element.media_ref}
    if name == "add_table":
        element = document.add_table(
            _slide_index(op),
            op.get("rows", []),
            op.get("transform"),
            op.get("col_widths"),
            op.get("row_heights"),
            op.get("name"),
        )
        return {"op": name, "element_id": element.id}
    if name == "set_text":
        element = document.set_text(
            _slide_index(op),
            str(op["element_id"]),
            str(op.get("text", "")),
            bool(op.get("replace_all", True)),
        )
        return {"op": name, "element_id": element.id}
    if name == "replace_all":
        count = document.replace_all(
            str(op["search"]),
            str(op.get("replacement", "")),
            case_sensitive=bool(op.get("case_sensitive", True)),
        )
        return {"op": name, "replacements": count}
    if name in {"transform", "set_transform"}:
        element = document.transform(
            _slide_index(op),
            str(op["element_id"]),
            op.get("patch", op.get("transform", {})),
        )
        return {"op": name, "element_id": element.id}
    if name == "set_fill":
        element = document.set_fill(
            _slide_index(op), str(op["element_id"]), dict(op["fill"])
        )
        return {"op": name, "element_id": element.id}
    if name == "set_stroke":
        element = document.set_stroke(
            _slide_index(op), str(op["element_id"]), op.get("stroke")
        )
        return {"op": name, "element_id": element.id}
    if name == "set_font":
        element = document.set_font(
            _slide_index(op), str(op["element_id"]), dict(op.get("patch", {}))
        )
        return {"op": name, "element_id": element.id}
    if name == "set_paragraph_format":
        element = document.set_paragraph_format(
            _slide_index(op),
            str(op["element_id"]),
            dict(op.get("patch", {})),
            op.get("paragraph_indices"),
        )
        return {"op": name, "element_id": element.id}
    if name == "edit_table_cell":
        element = document.edit_table_cell(
            _slide_index(op),
            str(op["element_id"]),
            int(op["row"]),
            int(op["column"]),
            str(op.get("text", "")),
        )
        return {"op": name, "element_id": element.id}
    if name == "delete_element":
        return {
            "op": name,
            "deleted": document.delete_element(_slide_index(op), str(op["element_id"])),
        }
    if name == "group":
        element = document.group(
            _slide_index(op), list(map(str, op["element_ids"])), op.get("name")
        )
        return {
            "op": name,
            "element_id": element.id,
            "children": [x.id for x in element.children],
        }
    if name == "ungroup":
        children = document.ungroup(_slide_index(op), str(op["element_id"]))
        return {"op": name, "element_ids": [child.id for child in children]}
    if name == "set_picture_crop":
        element = document.require_element(_slide_index(op), str(op["element_id"]))
        if element.type != "picture":
            raise PptxError("set_picture_crop applies only to pictures")
        element.src_rect = op.get("src_rect")
        element.dirty_src_rect = True
        return {"op": name, "element_id": element.id}
    if name == "set_picture_opacity":
        element = document.require_element(_slide_index(op), str(op["element_id"]))
        if element.type != "picture":
            raise PptxError("set_picture_opacity applies only to pictures")
        element.opacity = min(1.0, max(0.0, float(op["opacity"])))
        element.dirty = True
        return {"op": name, "element_id": element.id}
    if name == "set_slide_background":
        document.set_slide_background(_slide_index(op), str(op["color"]))
        return {"op": name, "slide_index": _slide_index(op)}
    if name == "set_slide_hidden":
        document.set_slide_hidden(_slide_index(op), bool(op.get("hidden", True)))
        return {"op": name, "slide_index": _slide_index(op)}
    if name == "duplicate_slide":
        slide = document.duplicate_slide(
            _slide_index(op), bool(op.get("clear_text", False))
        )
        return {
            "op": name,
            "slide_index": document.deck.slides.index(slide),
            "path": slide.path,
        }
    if name == "insert_blank_slide":
        slide = document.insert_blank_slide(_slide_index(op))
        return {
            "op": name,
            "slide_index": document.deck.slides.index(slide),
            "path": slide.path,
        }
    if name == "delete_slide":
        slide = document.delete_slide(_slide_index(op))
        return {"op": name, "path": slide.path}
    if name == "move_slide":
        document.move_slide(int(op["from_index"]), int(op["to_index"]))
        return {
            "op": name,
            "from_index": int(op["from_index"]),
            "to_index": int(op["to_index"]),
        }
    if name == "set_slide_size":
        document.set_slide_size(int(op["cx"]), int(op["cy"]))
        return {"op": name, "cx": int(op["cx"]), "cy": int(op["cy"])}
    raise PptxError(f"unsupported operation: {name}")


def apply_operations(
    document: PptxDocument, operations: list[dict[str, Any]], atomic: bool = True
) -> list[dict[str, Any]]:
    """Apply a batch. Atomic mode restores the pre-batch model on the first error."""
    if atomic:
        archive_bytes = document.archive.to_bytes()
        before = PptxDocument.open_bytes(archive_bytes)
    results: list[dict[str, Any]] = []
    try:
        for index, operation in enumerate(operations):
            try:
                result = apply_operation(document, operation)
                result["index"] = index
                results.append(result)
            except Exception as exc:
                raise PptxError(
                    f"operation {index} ({operation.get('op', '<unknown>')}) failed: {exc}"
                ) from exc
    except Exception:
        if atomic:
            document.archive, document.deck = before.archive, before.deck
        raise
    return results
