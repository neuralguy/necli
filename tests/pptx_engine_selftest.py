"""Self-contained integration checks runnable with the standard Python interpreter."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pptx_engine import PptxDocument, apply_operations, build_render_slide, render_svg


def test_create_edit_save_reopen_and_render(root: Path) -> None:
    document = PptxDocument.create_blank()
    shape = document.add_shape(
        0,
        shape="roundRect",
        transform={"x": 914400, "y": 914400, "cx": 4_572_000, "cy": 1_143_000},
        fill={"type": "solid", "color": "#4472C4"},
        text="Heading",
    )
    text = document.add_text(
        0,
        text="Редактируемый текст",
        transform={"x": 914400, "y": 2_286_000, "cx": 4_572_000, "cy": 914400},
        style={"font_size": 24, "color": "#112233"},
    )
    table = document.add_table(
        0,
        [["A", "B"], ["1", "2"]],
        transform={"x": 5_715_000, "y": 914400, "cx": 4_572_000, "cy": 1_800_000},
    )
    apply_operations(
        document,
        [
            {"op": "set_text", "slide_index": 0, "element_id": shape.id, "text": "Updated heading"},
            {"op": "set_font", "slide_index": 0, "element_id": text.id, "patch": {"bold": True}},
            {
                "op": "edit_table_cell",
                "slide_index": 0,
                "element_id": table.id,
                "row": 1,
                "column": 1,
                "text": "42",
            },
        ],
    )
    output = root / "edited.pptx"
    document.save(str(output))
    assert output.read_bytes()[:2] == b"PK"
    reopened = PptxDocument.open_file(str(output))
    assert len(reopened.deck.slides) == 1
    assert len(reopened.deck.slides[0].elements) == 3
    tree = build_render_slide(reopened, 0, 800)
    assert tree["size"]["width"] == 800
    assert "<svg" in render_svg(reopened, 0, 800)


def test_structure_and_model(root: Path) -> None:
    document = PptxDocument.create_blank()
    one = document.add_text(
        0, text="one", transform={"x": 0, "y": 0, "cx": 1_000_000, "cy": 500_000}
    )
    two = document.add_text(
        0, text="two", transform={"x": 1_000_000, "y": 0, "cx": 1_000_000, "cy": 500_000}
    )
    group = document.group(0, [one.id, two.id])
    assert len(document.ungroup(0, group.id)) == 2
    document.duplicate_slide(0, clear_text=True)
    document.insert_blank_slide(1)
    document.move_slide(2, 1)
    output = root / "structure.pptx"
    document.save(str(output))
    assert len(PptxDocument.open_file(str(output)).deck.slides) == 3
    assert "one" in json.dumps(document.to_model(), ensure_ascii=False)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="pptx-agent-test-") as temp:
        folder = Path(temp)
        test_create_edit_save_reopen_and_render(folder)
        test_structure_and_model(folder)
    print("SELFTEST_OK")
