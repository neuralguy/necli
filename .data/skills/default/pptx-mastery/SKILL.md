---
name: pptx-mastery
description: Native PowerPoint creation, editing, inspection, validation, and slide rendering through the pptx_engine-backed `pptx` tool. Use for any .pptx presentation task, including building decks, modifying existing slides or templates, adding text, shapes, pictures and tables, changing layouts and styles, or producing SVG/PNG previews.
---

# PPTX Mastery — native pptx_engine

Use this workflow as the authoritative path for `.pptx` files in necli.

## Non-negotiable rules

1. Use `read` for a compact deck overview and `pptx` for creation, editing, inspection, rendering, and validation.
2. Never use `create_file` or `patch_file` on a `.pptx`; it is a ZIP/OOXML package.
3. Never edit raw OOXML. Use semantic operations and stable element IDs from the current deck version.
4. Preserve slides and elements the user did not ask to change. Edit an existing template instead of rebuilding it.
5. Batch related changes into one `pptx(action="edit")` call. Edits are atomic by default.
6. Treat `slide_index` as zero-based. Treat coordinates and sizes as EMU (`914400` EMU = 1 inch; `9525` EMU = 1 px at 96 DPI).
7. Use `inspect` only for exact structure and formatting. Avoid `fullModel` and `includeXml` unless debugging requires them.
8. Render changed slides and inspect the visual result for layout-sensitive work. Successful package writing does not prove good slide composition.
9. Keep text readable: use strong contrast, a restrained palette, consistent typography, and sufficient margins.

## Mental model

A deck contains ordered slides. Each slide contains addressable elements such as:

- `text` or `shape`
- `picture`
- `table`
- `group`
- preserved charts, SmartArt, OLE, and unknown elements

Generic `read` returns a compact view:

```text
[PPTX slides: 3; size=12192000x6858000 EMU; edit with pptx(...) using slide_index and element_id]
slide 0 | 2 elements
  2 text @500000,400000 6000000x900000 | Quarterly Review
  3 picture @7000000,1200000 4000000x3000000 | Product image
```

Use these `element_id` values in edit operations. Re-read or inspect again after structural changes if another call depends on the new deck state.

## Efficient workflow

### Existing presentation

1. Start with `read`:

```json
{"path":"deck.pptx"}
```

2. Locate the relevant zero-based slide and element IDs.
3. Inspect only the target slide when exact geometry or styling matters:

```json
{"action":"inspect","path":"deck.pptx","slide":2}
```

4. Apply related changes atomically:

```json
{
  "action":"edit",
  "path":"deck.pptx",
  "operations":[
    {"op":"set_text","slide_index":2,"element_id":"4","text":"Updated title"},
    {"op":"set_font","slide_index":2,"element_id":"4","patch":{"font_size":28,"bold":true,"color":"#172B4D"}}
  ]
}
```

5. Render the changed slide and visually verify it.

### New presentation

`create` always starts with exactly one blank slide at index `0`. To create an N-slide deck in one atomic call, put all structural insertions first: insert blank slides at destination positions `1` through `N-1`, then add content to indices `0` through `N-1`. `insert_blank_slide.slide_index` is an insertion position in the inclusive range `0..current slide_count`; using the current `slide_count` appends. All other operations that take `slide_index` require an existing index in `0..slide_count-1`.

Create a blank 16:9 deck and populate it in one call:

```json
{
  "action":"create",
  "path":"report.pptx",
  "operations":[
    {
      "op":"add_text",
      "slide_index":0,
      "text":"Quarterly Review",
      "transform":{"x":700000,"y":500000,"cx":10800000,"cy":900000},
      "style":{"font_size":30,"bold":true,"color":"#172B4D"}
    }
  ]
}
```

Three-slide creation example (structure first, content second):

```json
{
  "action":"create",
  "path":"three-slides.pptx",
  "operations":[
    {"op":"insert_blank_slide","slide_index":1},
    {"op":"insert_blank_slide","slide_index":2},
    {"op":"add_text","slide_index":0,"text":"Slide 1","transform":{"x":700000,"y":500000,"cx":10800000,"cy":900000}},
    {"op":"add_text","slide_index":1,"text":"Slide 2","transform":{"x":700000,"y":500000,"cx":10800000,"cy":900000}},
    {"op":"add_text","slide_index":2,"text":"Slide 3","transform":{"x":700000,"y":500000,"cx":10800000,"cy":900000}}
  ]
}
```

Optional custom canvas size:

```json
{"action":"create","path":"custom.pptx","width":12192000,"height":6858000}
```

Set `overwrite:false` when accidental replacement must be prevented.

## Core operations

Every element operation that targets existing content needs `slide_index` and `element_id`.

### Add text

```json
{
  "op":"add_text",
  "slide_index":0,
  "text":"Key finding",
  "transform":{"x":800000,"y":1400000,"cx":5000000,"cy":700000},
  "style":{"font_size":24,"font_family":"Aptos","color":"#111827","bold":true},
  "name":"Finding title"
}
```

### Add shape

```json
{
  "op":"add_shape",
  "slide_index":0,
  "shape":"roundRect",
  "transform":{"x":800000,"y":2400000,"cx":3200000,"cy":1500000},
  "fill":{"type":"solid","color":"#E8F1FF"},
  "stroke":{"width":12700,"fill":{"type":"solid","color":"#4472C4"}},
  "text":"42% growth"
}
```

Common shapes include `rect`, `roundRect`, `ellipse`, `triangle`, `diamond`, and line presets.

### Add picture

Prefer `image_path` over model-visible base64:

```json
{
  "op":"add_picture",
  "slide_index":0,
  "image_path":"assets/chart.png",
  "transform":{"x":6500000,"y":1500000,"cx":4800000,"cy":3600000},
  "name":"Revenue chart"
}
```

Keep the source aspect ratio unless cropping is intentional. Use `set_picture_crop` or `set_picture_opacity` for targeted picture changes.

### Add table

```json
{
  "op":"add_table",
  "slide_index":0,
  "rows":[["Metric","Value"],["Revenue","$4.2M"],["Growth","18%"]],
  "transform":{"x":800000,"y":1800000,"cx":5200000,"cy":2500000},
  "col_widths":[3000000,2200000],
  "row_heights":[500000,500000,500000]
}
```

Edit one cell without recreating the table:

```json
{"op":"edit_table_cell","slide_index":0,"element_id":"7","row":1,"column":1,"text":"$4.5M"}
```

### Text and paragraph edits

```json
{"op":"set_text","slide_index":0,"element_id":"2","text":"New title"}
```

```json
{
  "op":"set_font",
  "slide_index":0,
  "element_id":"2",
  "patch":{"font_size":30,"font_family":"Aptos Display","bold":true,"italic":false,"color":"#172B4D"}
}
```

```json
{
  "op":"set_paragraph_format",
  "slide_index":0,
  "element_id":"2",
  "patch":{"align":"center","level":0,"space_after":8},
  "paragraph_indices":[0]
}
```

Use `replace_all` for a known repeated textual substitution across the deck:

```json
{"op":"replace_all","search":"2025","replacement":"2026","case_sensitive":true}
```

### Geometry, fill, and stroke

```json
{
  "op":"transform",
  "slide_index":0,
  "element_id":"5",
  "patch":{"x":900000,"y":1300000,"cx":5000000,"cy":1000000,"rot":0}
}
```

Transform patches may include `x`, `y`, `cx`, `cy`, `rot`, `flip_h`, and `flip_v`.

```json
{"op":"set_fill","slide_index":0,"element_id":"5","fill":{"type":"solid","color":"#FFFFFF"}}
```

```json
{"op":"set_stroke","slide_index":0,"element_id":"5","stroke":{"width":12700,"fill":{"type":"solid","color":"#94A3B8"}}}
```

### Structural operations

Index contracts are different by operation and must not be guessed:

- `duplicate_slide.slide_index` and `delete_slide.slide_index` target an existing slide: `0..slide_count-1`.
- `insert_blank_slide.slide_index` is a destination position: `0..slide_count` inclusive. Use `slide_count` to append; use `0` to prepend.
- `move_slide.from_index` and `move_slide.to_index` both target existing slides: `0..slide_count-1`.
- In an atomic batch, each structural operation changes the valid indices for every following operation. Prefer all insertions first, in ascending append order, then content operations.

```json
{"op":"delete_element","slide_index":0,"element_id":"8"}
{"op":"group","slide_index":0,"element_ids":["4","5"],"name":"KPI group"}
{"op":"ungroup","slide_index":0,"element_id":"9"}
```

```json
{"op":"duplicate_slide","slide_index":0,"clear_text":false}
{"op":"insert_blank_slide","slide_index":1}
{"op":"delete_slide","slide_index":3}
{"op":"move_slide","from_index":3,"to_index":1}
```

Deck-wide slide size:

```json
{"op":"set_slide_size","cx":12192000,"cy":6858000}
```

Slide properties:

```json
{"op":"set_slide_background","slide_index":0,"color":"#F8FAFC"}
{"op":"set_slide_hidden","slide_index":4,"hidden":true}
```

## Inspect, validate, and render

Inspect one slide whenever IDs or exact element properties are needed:

```json
{"action":"inspect","path":"deck.pptx","slide":0}
```

Omit `slide` only when an all-slide structural dump is genuinely useful. Set `fullModel:true` only for engine-level debugging. Set `includeXml:true` only to diagnose preservation problems; never copy that XML into an edit.

Validate package essentials:

```json
{"action":"validate","path":"deck.pptx"}
```

Render a slide to SVG for fast layout verification:

```json
{"action":"render","path":"deck.pptx","slide":0,"format":"svg","out":"preview.svg","width":1280}
```

PNG is also supported:

```json
{"action":"render","path":"deck.pptx","slide":0,"format":"png","out":"preview.png","width":1280}
```

Use `format:"json"` without `out` only when the render tree itself is needed. Open or read the rendered image when visual inspection is available; do not claim visual verification from a successful render command alone.

## Presentation quality

- Use one clear message per slide.
- Keep titles concise and consistently placed.
- Prefer a small typography system: title, section heading, body, caption.
- Avoid dense paragraphs; use short statements and meaningful grouping.
- Align elements to a consistent grid and maintain outer margins.
- Avoid placing critical content near slide edges.
- Use whitespace deliberately instead of filling every region.
- Keep color use semantic and restrained; verify contrast.
- Use tables only for genuinely tabular comparisons.
- Use images and charts when they communicate faster than prose.
- Preserve a supplied theme or template unless redesign is requested.

## Failure handling

If `pptx` returns an error:

1. Read the operation index and error literally; atomic edits leave the original file unchanged.
2. Re-read or inspect if an element ID or slide index is stale.
3. Call `pptx(action="help")` for the supported operation list instead of guessing names.
4. Check that image paths exist and use supported image formats.
5. Check transforms for positive `cx` and `cy` and sensible slide bounds.
6. Do not fall back to raw ZIP/XML surgery or a second PPTX library.

## Verification checklist

Before finishing a PPTX task:

- [ ] Used native `read` and `pptx` for presentation semantics.
- [ ] Preserved untouched slides and template content.
- [ ] Used current zero-based slide indices and element IDs.
- [ ] Batched related operations atomically.
- [ ] Kept pictures proportional unless intentional cropping was requested.
- [ ] Validated the final PPTX package.
- [ ] Rendered every materially changed slide when layout mattered.
- [ ] Visually checked available previews for clipping, overlap, contrast, and alignment.
- [ ] Confirmed the requested output path exists and the final tool operation succeeded.

Core principle: **read compactly, inspect narrowly, edit semantically, render visually, preserve everything else.**
