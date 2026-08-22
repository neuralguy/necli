"""Render-tree construction and pure-Python SVG/PNG preview rendering."""

from __future__ import annotations

from base64 import b64encode
from html import escape
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .engine import PptxDocument
from .models import Element, TextBody
from .xmlutil import hex_to_rgba

EMU_PER_INCH = 914400
EMU_PER_PT = 12700
EMU_PER_PX_96 = 9525


def emu_to_px(value: float, scale: float = 1.0) -> float:
    return float(value) / EMU_PER_PX_96 * scale


def pt_to_px(value: float, scale: float = 1.0) -> float:
    return float(value) * 96.0 / 72.0 * scale


def rot_to_deg(value: float) -> float:
    return float(value) / 60000.0


def make_viewport(size: Any, fit_width_px: int = 1280) -> dict[str, float]:
    scale = fit_width_px / emu_to_px(size.cx)
    return {"scale": scale, "width": fit_width_px, "height": emu_to_px(size.cy, scale)}


def _fill(fill: dict[str, Any] | None) -> dict[str, Any]:
    if not fill or fill.get("type") == "none":
        return {"type": "none"}
    if fill.get("type") == "solid":
        return {"type": "solid", "color": fill.get("color", "#000000")}
    if fill.get("type") == "gradient":
        return {
            "type": "gradient",
            "stops": fill.get("stops", []),
            "angle": fill.get("angle", 0),
            "path": fill.get("path"),
        }
    if fill.get("type") == "pattern":
        return {
            "type": "pattern",
            "fg": fill.get("fg", "#000000"),
            "bg": fill.get("bg", "#FFFFFF"),
            "preset": fill.get("preset", "pct5"),
        }
    if fill.get("type") == "image":
        return {"type": "image", "media_ref": fill.get("media_ref")}
    return {"type": "none"}


def _stroke(stroke: dict[str, Any] | None, scale: float) -> dict[str, Any] | None:
    if not stroke or stroke.get("fill", {}).get("type") == "none":
        return None
    color = stroke.get("fill", {}).get("color", "#000000")
    return {
        "color": color,
        "width": max(0.5, emu_to_px(stroke.get("width", 12700), scale)),
        "dash": stroke.get("dash"),
        "cap": stroke.get("cap"),
        "head_end": stroke.get("head_end"),
        "tail_end": stroke.get("tail_end"),
    }


def _text_layout(
    body: TextBody | None, width: float, height: float, scale: float
) -> dict[str, Any] | None:
    if not body:
        return None
    inset = body.insets or {"l": 91440, "t": 45720, "r": 91440, "b": 45720}
    left, top = emu_to_px(inset.get("l", 0), scale), emu_to_px(inset.get("t", 0), scale)
    right, bottom = (
        emu_to_px(inset.get("r", 0), scale),
        emu_to_px(inset.get("b", 0), scale),
    )
    lines: list[dict[str, Any]] = []
    cursor = top
    available = max(1, width - left - right)
    for para_index, paragraph in enumerate(body.paragraphs):
        runs = []
        default_size = 18.0 * scale
        for run in paragraph.runs:
            size = pt_to_px(run.font_size or 18, scale)
            runs.append(
                {
                    "text": run.text,
                    "font_size": size,
                    "font_family": run.font_family or "Aptos, Calibri, sans-serif",
                    "color": run.color or "#000000",
                    "bold": bool(run.bold),
                    "italic": bool(run.italic),
                    "underline": bool(run.underline),
                    "letter_spacing": pt_to_px(run.letter_spacing or 0, scale),
                }
            )
            default_size = max(default_size, size)
        line_height = (
            paragraph.line_exact * 96 / 72 * scale
            if paragraph.line_exact
            else default_size * ((paragraph.line_height or 120) / 100)
        )
        text = "".join(r["text"] for r in runs)
        bullet = paragraph.bullet
        if bullet and bullet.get("type") != "none":
            marker = "•" if bullet.get("type") == "char" else "1."
            runs.insert(
                0,
                {
                    "text": marker + " ",
                    "font_size": default_size,
                    "font_family": "Arial, sans-serif",
                    "color": "#000000",
                    "bold": False,
                    "italic": False,
                    "underline": False,
                    "letter_spacing": 0,
                },
            )
        # Coarse wrapping occurs on the composed paragraph, then conservatively retains run style 0.
        approximate_chars = max(1, int(available / max(4, default_size * 0.52)))
        chunks = []
        for explicit_line in text.split("\n"):
            chunks.extend(
                explicit_line[i : i + approximate_chars]
                for i in range(0, len(explicit_line), approximate_chars)
            )
            if not explicit_line:
                chunks.append("")
        for chunk in chunks:
            use_runs = runs if len(chunks) == 1 else [{**runs[0], "text": chunk}]
            lines.append(
                {
                    "x": left + emu_to_px(paragraph.mar_l or 0, scale),
                    "y": cursor + default_size,
                    "height": line_height,
                    "runs": use_runs,
                    "align": paragraph.align or "left",
                    "paragraph_index": para_index,
                }
            )
            cursor += line_height
        cursor += pt_to_px(paragraph.space_after or 0, scale)
    content_height = cursor - top
    if body.anchor == "middle":
        dy = max(0, (height - top - bottom - content_height) / 2)
        for line in lines:
            line["y"] += dy
    elif body.anchor == "bottom":
        dy = max(0, height - top - bottom - content_height)
        for line in lines:
            line["y"] += dy
    return {
        "insets": {"l": left, "t": top, "r": right, "b": bottom},
        "lines": lines,
        "height": content_height,
        "wrap": body.wrap is not False,
    }


def _node(
    element: Element, viewport: dict[str, float], document: PptxDocument
) -> dict[str, Any]:
    scale = viewport["scale"]
    rect = element.transform.offset
    box = {
        "x": emu_to_px(rect.x, scale),
        "y": emu_to_px(rect.y, scale),
        "width": emu_to_px(rect.cx, scale),
        "height": emu_to_px(rect.cy, scale),
    }
    node: dict[str, Any] = {
        "id": element.id,
        "type": element.type,
        "name": element.name,
        "box": box,
        "rotation": rot_to_deg(element.transform.rot),
        "flip_h": element.transform.flip_h,
        "flip_v": element.transform.flip_v,
        "fill": _fill(element.fill),
        "stroke": _stroke(element.stroke, scale),
        "opacity": element.opacity if element.opacity is not None else 1.0,
    }
    if element.type in {"shape", "text"}:
        node.update(
            {
                "geometry": element.preset_geometry or "rect",
                "adjust": element.adjust,
                "text": _text_layout(element.text, box["width"], box["height"], scale),
            }
        )
    elif element.type == "picture":
        node.update(
            {
                "media_ref": element.media_ref,
                "data_uri": _data_uri(document, element.media_ref),
                "src_rect": element.src_rect,
            }
        )
    elif element.type == "group":
        node["children"] = [
            _node(child, viewport, document) for child in element.children
        ]
    elif element.type == "table":
        node["columns"] = [emu_to_px(v, scale) for v in element.col_widths]
        node["rows"] = [emu_to_px(v, scale) for v in element.row_heights]
        cells = []
        for row_index, row in enumerate(element.rows):
            rendered_row = []
            for column_index, cell in enumerate(row):
                width = (
                    node["columns"][column_index]
                    if column_index < len(node["columns"])
                    else 0
                )
                height = node["rows"][row_index] if row_index < len(node["rows"]) else 0
                rendered_row.append(
                    {
                        "text": _text_layout(cell.get("text"), width, height, scale)
                        if isinstance(cell, dict)
                        else None,
                        "fill": _fill(cell.get("fill"))
                        if isinstance(cell, dict)
                        else {"type": "none"},
                        "merged": bool(cell.get("merged"))
                        if isinstance(cell, dict)
                        else False,
                        "grid_span": cell.get("grid_span", 1)
                        if isinstance(cell, dict)
                        else 1,
                        "row_span": cell.get("row_span", 1)
                        if isinstance(cell, dict)
                        else 1,
                    }
                )
            cells.append(rendered_row)
        node["cells"] = cells
    elif element.type == "chart":
        node["chart"] = element.chart or {}
    else:
        node["kind"] = element.kind or "unknown"
    return node


def build_render_slide(
    document: PptxDocument, slide_index: int, fit_width_px: int = 1280
) -> dict[str, Any]:
    slide = document.slide(slide_index)
    viewport = make_viewport(document.deck.size, fit_width_px)
    return {
        "slide_index": slide_index,
        "size": viewport,
        "background": _fill(slide.background or {"type": "solid", "color": "#FFFFFF"}),
        "nodes": [_node(e, viewport, document) for e in slide.decorations]
        + [_node(e, viewport, document) for e in slide.elements],
    }


def _data_uri(document: PptxDocument, media_ref: str | None) -> str | None:
    if not media_ref:
        return None
    data = document.archive.read_bytes(media_ref)
    if not data:
        return None
    ext = media_ref.rsplit(".", 1)[-1].lower()
    mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{b64encode(data).decode('ascii')}"


def _svg_style(
    fill: dict[str, Any], stroke: dict[str, Any] | None, opacity: float
) -> str:
    if fill.get("type") == "solid":
        style = f'fill="{escape(fill.get("color", "#000000"))}"'
    elif fill.get("type") == "none":
        style = 'fill="none"'
    else:
        style = f'fill="{escape(fill.get("bg", "#FFFFFF"))}"'
    if stroke:
        style += (
            f' stroke="{escape(stroke["color"])}" stroke-width="{stroke["width"]:.2f}"'
        )
        if stroke.get("dash"):
            dash = {"dash": "6 3", "dot": "2 3", "dashDot": "6 3 2 3"}.get(
                stroke["dash"], ""
            )
            if dash:
                style += f' stroke-dasharray="{dash}"'
    else:
        style += ' stroke="none"'
    if opacity < 0.999:
        style += f' opacity="{max(0, min(1, opacity)):.3f}"'
    return style


def _shape_svg(node: dict[str, Any], definitions: list[str]) -> str:
    box, fill, stroke, opacity = (
        node["box"],
        node["fill"],
        node.get("stroke"),
        node.get("opacity", 1),
    )
    x, y, w, h = box["x"], box["y"], max(0, box["width"]), max(0, box["height"])
    transform = ""
    if node.get("rotation") or node.get("flip_h") or node.get("flip_v"):
        extras = []
        if node.get("rotation"):
            extras.append(
                f"rotate({node['rotation']:.3f} {x + w / 2:.3f} {y + h / 2:.3f})"
            )
        if node.get("flip_h"):
            extras.append(f"translate({2 * x + w:.3f} 0) scale(-1 1)")
        if node.get("flip_v"):
            extras.append(f"translate(0 {2 * y + h:.3f}) scale(1 -1)")
        transform = ' transform="' + " ".join(extras) + '"'
    style = _svg_style(fill, stroke, opacity)
    geometry = node.get("geometry", "rect")
    if fill.get("type") == "gradient" and fill.get("stops"):
        gradient_id = f"g-{node['id']}"
        stops = "".join(
            f'<stop offset="{float(s.get("pos", 0)) * 100:.1f}%" stop-color="{escape(s.get("color", "#000000"))}"/>'
            for s in fill["stops"]
        )
        definitions.append(
            f'<linearGradient id="{gradient_id}" gradientTransform="rotate({float(fill.get("angle", 0)):.2f})">{stops}</linearGradient>'
        )
        style = _svg_style(
            {"type": "solid", "color": f"url(#{gradient_id})"}, stroke, opacity
        )
    if geometry in {"ellipse", "arc", "pie", "chord"}:
        element = f'<ellipse cx="{x + w / 2:.3f}" cy="{y + h / 2:.3f}" rx="{w / 2:.3f}" ry="{h / 2:.3f}" {style}/>'
    elif geometry in {
        "roundRect",
        "round1Rect",
        "round2SameRect",
        "snip1Rect",
        "snip2SameRect",
    }:
        radius = min(w, h) * 0.12
        element = f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" rx="{radius:.3f}" {style}/>'
    elif geometry in {"triangle", "rtTriangle"}:
        element = f'<path d="M {x + w / 2:.3f} {y:.3f} L {x + w:.3f} {y + h:.3f} L {x:.3f} {y + h:.3f} Z" {style}/>'
    elif geometry in {"line", "straightConnector1"}:
        element = f'<line x1="{x:.3f}" y1="{y:.3f}" x2="{x + w:.3f}" y2="{y + h:.3f}" {style}/>'
    elif geometry in {"diamond"}:
        element = f'<path d="M {x + w / 2:.3f} {y:.3f} L {x + w:.3f} {y + h / 2:.3f} L {x + w / 2:.3f} {y + h:.3f} L {x:.3f} {y + h / 2:.3f} Z" {style}/>'
    else:
        element = (
            f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" {style}/>'
        )
    texts = _text_svg(node.get("text"), x, y, w)
    return f"<g{transform}>{element}{texts}</g>"


def _text_svg(layout: dict[str, Any] | None, x: float, y: float, width: float) -> str:
    if not layout:
        return ""
    output = []
    for line in layout.get("lines", []):
        dx = line["x"]
        if line.get("align") == "center":
            dx = x + width / 2
        elif line.get("align") == "right":
            dx = x + width - layout["insets"]["r"]
        anchor = {"center": "middle", "right": "end"}.get(line.get("align"), "start")
        run_xml = ""
        for run in line["runs"]:
            attrs = f'font-family="{escape(run["font_family"])}" font-size="{run["font_size"]:.2f}" fill="{escape(run["color"])}"'
            if run.get("bold"):
                attrs += ' font-weight="700"'
            if run.get("italic"):
                attrs += ' font-style="italic"'
            if run.get("letter_spacing"):
                attrs += f' letter-spacing="{run["letter_spacing"]:.2f}"'
            deco = ' text-decoration="underline"' if run.get("underline") else ""
            run_xml += f"<tspan {attrs}{deco}>{escape(run['text'])}</tspan>"
        output.append(
            f'<text x="{dx:.3f}" y="{line["y"] + y:.3f}" text-anchor="{anchor}">{run_xml}</text>'
        )
    return "".join(output)


def _picture_svg(node: dict[str, Any]) -> str:
    box = node["box"]
    uri = node.get("data_uri")
    if not uri:
        return f'<rect x="{box["x"]}" y="{box["y"]}" width="{box["width"]}" height="{box["height"]}" fill="#E5E7EB" stroke="#9CA3AF"/><text x="{box["x"] + 8}" y="{box["y"] + 20}" font-size="14" fill="#374151">Image unavailable</text>'
    opacity = node.get("opacity", 1)
    return f'<image x="{box["x"]:.3f}" y="{box["y"]:.3f}" width="{box["width"]:.3f}" height="{box["height"]:.3f}" href="{uri}" preserveAspectRatio="xMidYMid slice" opacity="{opacity:.3f}"/>'


def _table_svg(node: dict[str, Any]) -> str:
    box = node["box"]
    cols, heights, cells = (
        node.get("columns", []),
        node.get("rows", []),
        node.get("cells", []),
    )
    out = []
    y = box["y"]
    for r, height in enumerate(heights):
        x = box["x"]
        for c, width in enumerate(cols):
            cell = cells[r][c] if r < len(cells) and c < len(cells[r]) else {}
            if not cell.get("merged"):
                fill = cell.get("fill", {"type": "solid", "color": "#FFFFFF"})
                colour = (
                    fill.get("color", "#FFFFFF")
                    if fill.get("type") == "solid"
                    else "#FFFFFF"
                )
                out.append(
                    f'<rect x="{x:.3f}" y="{y:.3f}" width="{width:.3f}" height="{height:.3f}" fill="{escape(colour)}" stroke="#6B7280" stroke-width="0.8"/>'
                )
                out.append(_text_svg(cell.get("text"), x, y, width))
            x += width
        y += height
    return "".join(out)


def _chart_svg(node: dict[str, Any]) -> str:
    box = node["box"]
    chart = node.get("chart", {})
    series = chart.get("series", [])
    out = [
        f'<rect x="{box["x"]:.3f}" y="{box["y"]:.3f}" width="{box["width"]:.3f}" height="{box["height"]:.3f}" fill="#FFFFFF" stroke="#A0A0A0"/>'
    ]
    values = [
        v for s in series for v in s.get("values", []) if isinstance(v, (int, float))
    ]
    max_value = max(values, default=1) or 1
    if chart.get("title"):
        out.append(
            f'<text x="{box["x"] + box["width"] / 2:.3f}" y="{box["y"] + 20:.3f}" text-anchor="middle" font-size="14" font-weight="700">{escape(str(chart["title"]))}</text>'
        )
    count = max(1, len(values))
    gap = box["width"] * 0.1 / count
    bar_width = max(2, (box["width"] * 0.8 / count) - gap)
    for i, value in enumerate(values):
        height = (float(value) / max_value) * (box["height"] * 0.72)
        x = box["x"] + box["width"] * 0.1 + i * (bar_width + gap)
        y = box["y"] + box["height"] * 0.88 - height
        out.append(
            f'<rect x="{x:.3f}" y="{y:.3f}" width="{bar_width:.3f}" height="{height:.3f}" fill="#4472C4"/>'
        )
    return "".join(out)


def render_svg(
    document: PptxDocument, slide_index: int, fit_width_px: int = 1280
) -> str:
    tree = build_render_slide(document, slide_index, fit_width_px)
    defs: list[str] = []
    background = (
        tree["background"].get("color", "#FFFFFF")
        if tree["background"].get("type") == "solid"
        else "#FFFFFF"
    )
    content: list[str] = [
        f'<rect width="100%" height="100%" fill="{escape(background)}"/>'
    ]
    for node in tree["nodes"]:
        if node["type"] in {"shape", "text"}:
            content.append(_shape_svg(node, defs))
        elif node["type"] == "picture":
            content.append(_picture_svg(node))
        elif node["type"] == "group":
            # Render child nodes recursively while preserving their already slide-relative coordinates.
            for child in node.get("children", []):
                content.append(
                    _shape_svg(child, defs)
                    if child["type"] in {"shape", "text"}
                    else _picture_svg(child)
                    if child["type"] == "picture"
                    else ""
                )
        elif node["type"] == "table":
            content.append(_table_svg(node))
        elif node["type"] == "chart":
            content.append(_chart_svg(node))
        else:
            b = node["box"]
            content.append(
                f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["width"]}" height="{b["height"]}" fill="#F3F4F6" stroke="#9CA3AF"/><text x="{b["x"] + 4}" y="{b["y"] + 16}" font-size="12" fill="#4B5563">{escape(node.get("kind", "unsupported"))}</text>'
            )
    defs_xml = f"<defs>{''.join(defs)}</defs>" if defs else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{tree["size"]["width"]:.0f}" height="{tree["size"]["height"]:.0f}" viewBox="0 0 {tree["size"]["width"]:.3f} {tree["size"]["height"]:.3f}">{defs_xml}{"".join(content)}</svg>'


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=max(1, size))
        except OSError:
            pass
    return ImageFont.load_default()


def render_png(
    document: PptxDocument, slide_index: int, output_path: str, fit_width_px: int = 1280
) -> None:
    tree = build_render_slide(document, slide_index, fit_width_px)
    background = (
        tree["background"].get("color", "#FFFFFF")
        if tree["background"].get("type") == "solid"
        else "#FFFFFF"
    )
    canvas = Image.new(
        "RGBA",
        (round(tree["size"]["width"]), round(tree["size"]["height"])),
        hex_to_rgba(background),
    )
    draw = ImageDraw.Draw(canvas, "RGBA")
    for node in tree["nodes"]:
        _draw_node(draw, canvas, node)
    canvas.convert("RGB").save(output_path, "PNG")


def _draw_node(
    draw: ImageDraw.ImageDraw, canvas: Image.Image, node: dict[str, Any]
) -> None:
    box = node["box"]
    xy = (
        round(box["x"]),
        round(box["y"]),
        round(box["x"] + box["width"]),
        round(box["y"] + box["height"]),
    )
    if node["type"] in {"shape", "text"}:
        fill = node.get("fill", {})
        color = (
            fill.get("color", "#FFFFFF") if fill.get("type") == "solid" else "#FFFFFF"
        )
        outline = node.get("stroke", {}).get("color") if node.get("stroke") else None
        width = (
            max(1, round(node.get("stroke", {}).get("width", 1)))
            if node.get("stroke")
            else 1
        )
        if node.get("geometry") == "ellipse":
            draw.ellipse(
                xy,
                fill=hex_to_rgba(color, node.get("opacity", 1)),
                outline=outline,
                width=width,
            )
        elif node.get("geometry") == "triangle":
            draw.polygon(
                [(xy[0] + (xy[2] - xy[0]) // 2, xy[1]), (xy[2], xy[3]), (xy[0], xy[3])],
                fill=hex_to_rgba(color),
                outline=outline,
            )
        else:
            draw.rounded_rectangle(
                xy,
                radius=round(min(box["width"], box["height"]) * 0.12)
                if node.get("geometry") == "roundRect"
                else 0,
                fill=hex_to_rgba(color, node.get("opacity", 1)),
                outline=outline,
                width=width,
            )
        _draw_text(draw, node.get("text"), box["x"], box["y"])
    elif node["type"] == "picture":
        node.get("media_ref")
        # node does not own document; PNG path is deliberately safe fallback if image bytes aren't opened by caller.
        draw.rectangle(xy, fill=(229, 231, 235, 255), outline=(156, 163, 175, 255))
        draw.text(
            (xy[0] + 6, xy[1] + 6), "Image", fill=(55, 65, 81, 255), font=_font(14)
        )
    elif node["type"] == "table":
        cols, rows, cells = (
            node.get("columns", []),
            node.get("rows", []),
            node.get("cells", []),
        )
        y = box["y"]
        for ri, height in enumerate(rows):
            x = box["x"]
            for ci, width in enumerate(cols):
                cell = cells[ri][ci] if ri < len(cells) and ci < len(cells[ri]) else {}
                draw.rectangle(
                    (round(x), round(y), round(x + width), round(y + height)),
                    fill=hex_to_rgba(cell.get("fill", {}).get("color", "#FFFFFF")),
                    outline=(107, 114, 128, 255),
                )
                _draw_text(draw, cell.get("text"), x, y)
                x += width
            y += height
    elif node["type"] == "chart":
        draw.rectangle(xy, fill=(255, 255, 255, 255), outline=(160, 160, 160, 255))
    elif node["type"] == "group":
        for child in node.get("children", []):
            _draw_node(draw, canvas, child)


def _draw_text(
    draw: ImageDraw.ImageDraw,
    layout: dict[str, Any] | None,
    origin_x: float,
    origin_y: float,
) -> None:
    if not layout:
        return
    for line in layout.get("lines", []):
        x, y = line["x"] + origin_x, line["y"] + origin_y
        for run in line["runs"]:
            font = _font(round(run["font_size"]), run.get("bold", False))
            text = run["text"]
            draw.text(
                (round(x), round(y - run["font_size"])),
                text,
                fill=hex_to_rgba(run.get("color")),
                font=font,
            )
            x += draw.textlength(text, font=font)
