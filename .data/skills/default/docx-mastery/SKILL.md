---
name: docx-mastery
description: "Native DOCX creation and editing through the docx_engine-backed docx tool: compact block reads, token-efficient targeted edits, rich runs, tables, extended LaTeX-to-OMML formulas, images, charts, sections, headers/footers, comments, notes, tracked changes, and lossless preservation of untouched OOXML. Use for any .docx task."
---

# DOCX Mastery — native docx_engine

This skill is the authoritative workflow for `.docx` files in necli.

## 0. Non-negotiable rules

1. **Use `read` to understand a DOCX, and `docx` to create/edit/inspect it.**
2. **Never use `create_file` or `patch_file` on a `.docx`.** A DOCX is a ZIP/OOXML package
3. **Never manipulate raw OOXML** Public creation intentionally rejects raw OOXML blocks. Prefer semantic blocks/runs/options
4. **Minimize tokens** Start with compact `read`. Inspect only the exact blocks or metadata you need. Batch edits into one `docx(action="edit")` call
5. **Preserve what the user did not ask to change.** Editing keeps untouched original blocks/parts. Do not reconstruct the whole document just to change a few places
6. **Block IDs (`bN`) belong to the current document version.** Structural edits can shift IDs. Re-read before a later call that needs fresh IDs
7. **Prefer explicit failure over silent formatting loss.** If a text block has mixed styles/links/math/notes, do not force `set(text)`. Use `runs` or `replace`
8. **For a supplied template/reference DOCX, preserve it instead of imitating it from memory.** Copy the file to the requested output path when needed, then edit the copy with `docx`.
9. **Use black as the default text color everywhere unless another color is explicitly requested.**

## 1. Mental model

Think of a DOCX as an ordered semantic block list:

- paragraph
- heading
- list item
- table
- display formula
- image
- chart
- page break
- TOC/caption and other generated objects

`read` gives a compact, line-addressable representation. Example:

```text
[DOCX blocks: 8; edit with docx(action="edit") using current bN ids; ...]
b0 h1 | Report
b1 p {bold,link} | Revenue increased by 12%.
b2 li:bullet:0 | First point
b3 table:3x2 | Name | Value ; A | 10 ; B | 20
b4 math | x^2+1
b5 image | 640x360px align=center wrap=inline
b6 chart | Quarterly trend
b7 p | Conclusion
meta | header, footer, comments=2
```

The `bN` ID is the preferred edit target. It is deliberately compact and temporary.

## 2. Token-efficient workflow

### Reading a document

Start with generic `read`:

```json
{"path":"report.docx"}
```

For long documents, use the normal `read` pagination arguments (`offset`, `limit`) instead of requesting the entire document.
Do **not** inspect every block after reading. The compact read is usually enough to locate the target.

### Inspecting exact details

Use targeted inspect only when exact formatting, runs, table structure, links, comments, math, etc. matter:

```json
{"action":"inspect","path":"report.docx","target":"b17"}
```

Several blocks may be inspected together:

```json
{"action":"inspect","path":"report.docx","target":["b17","b18","b22"]}
```

Without `target`, inspect returns document metadata only:

```json
{"action":"inspect","path":"report.docx"}
```

Use `includeRaw:true` only when debugging preservation/OOXML. It is expensive. Use `includeMedia:true` only when image bytes are genuinely required. It can be extremely expensive.

### Editing

Batch independent changes in one call:

```json
{
  "action":"edit",
  "path":"report.docx",
  "ops":[
    {"op":"set","target":"b3","text":"Updated introduction"},
    {"op":"delete","target":"b9"},
    {"op":"insert","where":"after","target":"b12","blocks":[{"type":"p","text":"New paragraph"}]}
  ]
}
```

One batch is preferable to many tiny tool calls when the targets are all from the same current read version.

After a structural `insert`, `replace`, or `delete`, old `bN` IDs may no longer correspond to the next version. If another edit call is needed, re-read first.

## 3. Creating a document

Minimal creation:

```json
{
  "action":"create",
  "path":"report.docx",
  "blocks":[
    {"type":"h1","text":"Report"},
    {"type":"p","text":"Introduction."}
  ]
}
```

Plain strings are paragraph shorthand:

```json
{"action":"create","path":"notes.docx","blocks":["First paragraph","Second paragraph"]}
```

By default creation overwrites an existing path. Set `overwrite:false` when accidental overwrite must be prevented.

## 4. Block DSL

### Paragraph

```json
{"type":"p","text":"Plain paragraph"}
```

or rich text:

```json
{
  "type":"p",
  "runs":[
    {"text":"Normal "},
    {"text":"bold","bold":true},
    {"text":" and link","link":"https://example.com"}
  ],
  "format":{"align":"justify"}
}
```

### Headings

Preferred compact syntax:

```json
{"type":"h1","text":"Main title"}
{"type":"h2","text":"Section"}
```

`h1` through `h9` are supported. Equivalent explicit form:

```json
{"type":"heading","level":2,"text":"Section"}
```

Use `styleId` only when matching a specific existing Word style is necessary.

### Lists

Bullet:

```json
{"type":"li","list":"bullet","text":"First point"}
```

Ordered:

```json
{"type":"li","list":"ordered","text":"First step"}
```

Nested level:

```json
{"type":"li","list":"bullet","level":1,"text":"Nested point"}
```

Advanced list form can specify an existing numbering definition:

```json
{"type":"li","list":{"kind":"ordered","numId":"7","ilvl":2},"text":"Item"}
```

For normal creation, let the tool allocate numbering automatically.

### Tables

Simple table:

```json
{
  "type":"table",
  "header":true,
  "rows":[
    ["Name","Value"],
    ["A","10"],
    ["B","20"]
  ]
}
```

`header:true` makes the first row bold and gives it a light fill.

A cell may be an object rather than a string. `text` is shorthand for a one-paragraph cell:

```json
{
  "type":"table",
  "rows":[
    [{"text":"A","bold":true},{"paras":["line 1","line 2"]}]
  ]
}
```

Optional column sizing:

```json
{"colWidthsPct":[30,70]}
```

or:

```json
{"colWidthsTwips":[3000,6500]}
```

Do not over-specify widths unless required. Wide tables are inherently fragile in Word. Prefer fewer columns or landscape orientation when appropriate.

### Display mathematics

```json
{"type":"math","latex":"\\frac{a}{b}=c"}
```

Optional alignment:

```json
{"type":"math","latex":"E=mc^2","align":"center"}
```

Use LaTeX source. The engine converts it to native editable Word OMML.

### Supported LaTeX subset

Use the supported semantic subset rather than assuming full TeX or package compatibility. The engine supports ordinary grouping, fractions, roots, scripts, Greek letters, common relations, delimiters, matrices and the following extensions:

| Category | Supported commands and environments |
|---|---|
| Mathematical alphabets | `\mathbb`, `\mathcal`, `\mathfrak`, `\mathbf`, `\mathit`, `\boldsymbol`, `\mathsf`, `\mathtt`, `\mathnormal`, `\mathrm` |
| Operators and limits | `\sum`, `\prod`, `\coprod`, common integrals, `\bigcup`, `\bigcap`, `\bigsqcup`, `\bigvee`, `\bigwedge`, `\bigodot`, `\bigotimes`, `\bigoplus`, `\lim`, `\limsup`, `\liminf`, `\argmax`, `\argmin` |
| Accents and annotations | `\hat`, `\widehat`, `\bar`, `\vec`, `\overrightarrow`, `\overleftarrow`, `\dot`, `\ddot`, `\tilde`, `\widetilde`, `\check`, `\breve`, `\overset`, `\underset`, `\stackrel`, `\not` |
| Multi-line structures | `matrix`, `pmatrix`, `bmatrix`, `Bmatrix`, `vmatrix`, `Vmatrix`, `cases`, `array`, `aligned`, `align`, `gather`, `gathered`, `split`, `smallmatrix`, `\substack` |
| Layout and spacing | `\displaystyle`, `\textstyle`, `\scriptstyle`, `\scriptscriptstyle`, `\,`, `\;`, `\quad`, `\qquad` and related supported spacing commands |

Use `\mathbb{R}`, `\mathbb{N}`, `\mathbb{Z}` and `\mathbb{Q}` directly; they become native double-struck Unicode symbols in OMML. Restrict mathematical-alphabet arguments to Latin letters and, where appropriate, digits. Keep custom macros, `\newcommand`, package imports, TikZ, `\require`, and arbitrary package-specific commands out of DOCX formulas.

### Inline mathematics

Inside a paragraph run:

```json
{
  "type":"p",
  "runs":[
    {"text":"For "},
    {"latex":"x^2+1"},
    {"text":" we obtain..."}
  ]
}
```

Do not type LaTeX commands into an ordinary `text` run and expect conversion. Use the `latex` run field.

### Images

The model should pass a filesystem path, not base64:

```json
{"type":"image","path":"charts/result.png","align":"center"}
```

Optional:

```json
{
  "type":"image",
  "path":"diagram.png",
  "widthPx":600,
  "heightPx":350,
  "align":"center",
  "wrap":"inline"
}
```

The tool reads and encodes the media itself. For common raster formats it derives dimensions automatically and scales overly wide images down to a reasonable width. If dimensions cannot be detected, provide both `widthPx` and `heightPx`.

Never put image base64 in the model-visible tool.

### Charts

Use native chart blocks when the data itself belongs in the DOCX:

```json
{
  "type":"chart",
  "kind":"area",
  "title":"Quarterly trend",
  "categories":["Q1","Q2","Q3"],
  "series":[
    {"name":"Revenue","values":[10,15,22]},
    {"name":"Cost","values":[7,9,13]}
  ]
}
```

The engine creates both the Word chart and its embedded workbook. Do not create fake cell references manually.

For custom scientific visualizations that are better expressed by matplotlib, generate a PNG separately and insert it with an `image` block. Use native charts for ordinary editable business charts.

### Page break

```json
{"type":"pageBreak"}
```

### Table of contents

```json
{"type":"toc","entries":[...]}
```

Use only when you have the required TOC entry structure. For uncommon TOC syntax, query `docx(action="help", topic="blocks")` rather than guessing.

### Caption

```json
{"type":"caption","label":"Figure","number":1,"text":"Architecture"}
```

Use a consistent label/number sequence throughout the document.

## 5. Run fields

Common run fields:

```json
{
  "text":"example",
  "bold":true,
  "italic":true,
  "underline":true,
  "strike":false,
  "color":"C00000",
  "font":"Arial",
  "size":14,
  "highlight":"yellow",
  "vertAlign":"superscript",
  "link":"https://example.com"
}
```

`size` is in points and is converted to Word half-points.

Aliases:

```json
{"text":"2","superscript":true}
{"text":"n","subscript":true}
```

`link` may be a URL string. On inspect it may appear as a richer link object.

Inline math uses:

```json
{"latex":"\\alpha+\\beta"}
```

Advanced engine fields are supported when preserving or explicitly manipulating an existing document:

- `styleId`
- `charSpacingTwips`
- `charScalePct`
- `em`
- `noteRef`
- `commentIds`
- `ins`
- `del`
- `rPrChange`
- `ruby`

Do not emit advanced fields unless the task actually requires them or they came from `inspect` and must be preserved.

## 6. Paragraph format fields

Use camelCase in the agent protocol:

```json
{
  "format":{
    "align":"justify",
    "lineSpacing":360,
    "lineRule":"auto",
    "indentLeft":0,
    "indentRight":0,
    "indentFirstLine":709,
    "spaceBefore":0,
    "spaceAfter":120,
    "pageBreakBefore":false,
    "keepNext":true,
    "keepLines":true,
    "widowControl":true,
    "contextualSpacing":false,
    "shadingFill":"FFFF00",
    "borders":{},
    "tabStops":[],
    "dropCap":{},
    "bidi":false
  }
}
```

Important: explicit zero is meaningful. Use `0` when you mean “remove inherited spacing/indent”, rather than omitting the field.

Do not spray format fields onto every paragraph. Word styles should carry repeated formatting; set direct formatting only where it is semantically needed or where matching a reference requires it.

## 7. Edit operations

### `set` — smallest safe textual/style change

Text only:

```json
{"op":"set","target":"b12","text":"Replacement text"}
```

This is ideal for a uniform paragraph because the engine can surgically preserve its original OOXML formatting.

Do **not** use text-only `set` on a paragraph with mixed formatting, hyperlinks, math, or notes. The tool intentionally rejects ambiguous cases.

Use explicit runs instead:

```json
{
  "op":"set",
  "target":"b12",
  "runs":[
    {"text":"Result: "},
    {"text":"42","bold":true}
  ]
}
```

Change paragraph formatting without replacing content:

```json
{"op":"set","target":"b12","format":{"align":"center","spaceAfter":0}}
```

A block can be `set` only once in one `docx` call. Combine all desired changes for that target into one `set` op.

### `replace` — replace one block with one or more blocks

```json
{
  "op":"replace",
  "target":"b8",
  "blocks":[
    {"type":"h2","text":"New section"},
    {"type":"p","text":"Replacement body."}
  ]
}
```

Use `replace` for tables, images, charts, formulas, or any structural change to a block.

### `insert`

At end:

```json
{"op":"insert","where":"end","blocks":[{"type":"p","text":"Appendix"}]}
```

At start:

```json
{"op":"insert","where":"start","blocks":[{"type":"h1","text":"Cover"}]}
```

Relative to a block:

```json
{"op":"insert","where":"after","target":"b4","blocks":[{"type":"p","text":"Added text"}]}
```

Valid `where`: `start`, `end`, `before`, `after`.

### `delete`

Single block:

```json
{"op":"delete","target":"b7"}
```

Several blocks:

```json
{"op":"delete","target":["b7","b8","b9"]}
```

## 8. Edit strategy for existing documents

### Small change

1. `read` the relevant region.
2. Locate `bN`.
3. If plain/uniform text: one `set(text)`.
4. If rich/structural: targeted `inspect`, then `set(runs)` or `replace`.
5. Do not re-read unless another call needs fresh IDs.

### Many independent changes

1. One paginated read pass to collect all current target IDs.
2. Inspect only the rich blocks whose exact structure matters.
3. Send one `edit` with many `ops`.
4. Re-read once after the batch only if continuing with ID-based edits.

### Large rewrite

Do not resend untouched blocks. Replace the range block-by-block or delete a target range and insert the new section

### Reference/template document

If the user supplied a DOCX whose formatting must be retained:

1. Work on a copy if the user wants a new output file.
2. Read the copy compactly.
3. Inspect representative headings/body/table blocks plus metadata only as needed.
4. Edit the copy in place with semantic operations.
5. Do not rebuild the template from a blank DOCX unless explicitly asked.

This preserves styles, numbering, relationships, headers/footers, and arbitrary OOXML that the semantic model does not need to understand.

## 9. Document-level options

Options can be passed on `create` or `edit`.

Common examples:

```json
{
  "options":{
    "header":{"text":"Confidential report"},
    "footer":{"text":"Page ","pageNumber":true},
    "watermark":"DRAFT",
    "section":{"orientation":"landscape"}
  }
}
```

The engine supports native document options including:

- `header`: text or paragraph-based header content
- `footer`: text or paragraph-based footer content; optional page number
- `watermark`
- `section`: orientation, page size, margins and other section properties
- `comments`
- `footnotes`
- `endnotes`
- `sources`
- `themeFonts`
- `themeColors`
- `protection`
- `numbering`
- `savedAt`

Inspect document metadata before changing global properties of an existing file:

```json
{"action":"inspect","path":"report.docx"}
```

For uncommon option syntax, `docx(action="help", topic="options")` is an on-demand schema reminder. Do not guess field shapes.

### Comments

Typical document option:

```json
{
  "comments":[
    {"id":"1","author":"Reviewer","initials":"RV","text":"Check this statement"}
  ]
}
```

Comment anchors/ranges belong to runs/blocks. Inspect an existing commented block before editing it.

### Footnotes/endnotes

Typical option data:

```json
{"footnotes":[{"id":"1","text":"Source note"}]}
```

Runs may reference notes through `noteRef`. For edits around existing notes, inspect the target first and preserve the note reference explicitly.

### Tracked changes

Ins/del revision metadata can be represented on runs. If the task is to preserve existing tracked changes, inspect the rich block and do not flatten it to plain text. If the user explicitly asks to create tracked changes, use the advanced `ins`/`del` run fields and verify by re-inspecting the edited block.

## 10. Metadata inspection

`docx(action="inspect", path=...)` without a target is the cheap way to check document-wide state. Metadata can include:

- header/footer text
- whether footer contains page numbering
- watermark
- title-page flag
- even/odd headers flag
- comments
- footnotes/endnotes
- bibliography sources
- protection
- theme fonts/colors
- section settings
- ink count

Use this instead of inspecting every block when the question is “what are the page margins?”, “is this landscape?”, “does it have a watermark?”, etc.

## 11. Quality rules for document authoring

The engine can represent rich DOCX features, but content/layout decisions are still the agent's responsibility.

### Preserve user intent

- If the user supplied a reference or existing document, preserve its structure and style unless asked to redesign it.
- Do not shorten, merge, or rewrite sections that were not requested.
- Do not convert tables into prose or vice versa unless asked.
- Keep numbering and labels consistent.

### Headings and hierarchy

- Use real heading blocks rather than fake bold paragraphs.
- Keep heading levels hierarchical: h1 → h2 → h3; avoid arbitrary jumps unless the source document already uses them.
- Do not create two headings in a row without content when it makes the document read awkwardly.

### Tables

- Use tables for genuinely tabular data.
- Keep column counts reasonable.
- For very wide data, either split it or use a landscape section.
- Keep numeric precision consistent within a column.

### Images

- Prefer vector-independent, print-readable raster exports when inserting generated plots.
- Keep aspect ratio unless distortion is intentional.
- Generate readable chart labels before insertion; the DOCX engine cannot repair unreadable text baked into an image.
- Place captions consistently.

### Formulas

- Use native LaTeX→OMML, not screenshots of equations.
- Use inline `latex` runs for inline formulas and `math` blocks for display formulas.
- If a LaTeX expression is rejected, first reduce it to the supported subset: remove package macros, expand local shorthand, and use the documented native commands. Preserve mathematical meaning; use an equation image only when the user explicitly requests it or the required construction is genuinely outside the supported subset.

### Code

For code-heavy documents, use paragraphs/runs with a monospace font (for example `Consolas` or `Courier New`) and preserve line structure with separate paragraphs when necessary. If exact terminal/program output layout is visually critical and extremely long, a generated image can be inserted, but do not default to screenshots for ordinary code.

## 12. Scientific/academic documents

When the user asks for a course paper, report, thesis section, RGR, etc.:

- Follow the supplied institutional/template formatting exactly when provided.
- Build title page, contents, introduction, sections, conclusion and references according to the user's requirements—not a guessed universal standard.
- Prefer multiple substantive paragraphs over filler.
- Use native tables and native equations.
- Keep table/figure captions and numbering consistent.
- For a supplied university template, edit a copy of that template instead of recreating it from blank.

Do not invent page numbers for a manually written contents list unless they are known. Without a page-layout/rendering tool, guessed page numbers are worse than an updatable Word TOC field.

## 13. Analytical reports and factual data

DOCX generation does not relax factual standards.

- Verify current prices, benchmarks, dates, model specifications, regulations, etc. using the appropriate research tools before putting them into the document.
- Do not invent plausible-looking numbers.
- Calculate aggregates/rankings programmatically when nontrivial.
- Record sources in the document when required.
- For editable ordinary bar/line/area-style business visualizations, prefer native DOCX charts.
- For specialized plots (radar, scientific scatter variants, heatmaps, etc.), generate a high-quality image and insert it.

## 14. Verification after edits

Do not blindly re-read the whole file after every successful write. The tool re-parses the exact bytes it wrote before reporting success.

Use verification proportional to the change:

- **Simple uniform text edit:** successful `docx edit` is normally sufficient; optionally read the local region if the exact user-visible text matters.
- **Rich formatting/link/math edit:** inspect the edited block.
- **Structural insert/delete/replace:** re-read the relevant page of compact blocks to obtain fresh IDs and confirm ordering.
- **Document-wide metadata change:** metadata inspect.
- **Complex table/chart/note/comment change:** targeted inspect plus compact read of the surrounding blocks.

There is no `docx_screenshot` tool. Do not claim visual page-layout verification if you did not actually perform it through some separately available renderer.

## 15. Failure handling

If `docx` returns an error:

1. Read the error literally; the tool validates ambiguous operations on purpose.
2. Do not fall back to HTML/Pandoc or raw ZIP surgery.
3. If syntax is uncertain, call one of:

```json
{"action":"help","topic":"blocks"}
{"action":"help","topic":"runs"}
{"action":"help","topic":"edit"}
{"action":"help","topic":"options"}
```

4. If an edit target is missing, re-read the document and use current IDs.
5. If `set(text)` says mixed formatting/math/notes are ambiguous, inspect the block and use `runs` or `replace`.
6. If an image's dimensions cannot be detected, provide `widthPx` and `heightPx`.
7. If a formula is unsupported, reduce it to the documented subset or ask the user for an acceptable semantic fallback. Do not corrupt the document with raw OOXML, package macros, or a silently degraded text run.

## 16. High-efficiency patterns

### Replace one sentence in a normal paragraph

```text
read → identify b23 → docx edit(set text) → done
```

### Fix bold text and a link in one paragraph

```text
read → inspect b23 → docx edit(set runs) → inspect b23 if needed
```

### Add a section with heading, prose, table and chart

One call:

```json
{
  "action":"edit",
  "path":"report.docx",
  "ops":[{
    "op":"insert",
    "where":"end",
    "blocks":[
      {"type":"h1","text":"Results"},
      {"type":"p","text":"Summary of results."},
      {"type":"table","header":true,"rows":[["Metric","Value"],["Accuracy","94.2%"]]},
      {"type":"chart","kind":"area","categories":["A","B","C"],"series":[{"name":"Score","values":[70,82,94.2]}]}
    ]
  }]
}
```

### Edit a 100-page document

Do **not** inspect or resend 100 pages.

1. `read` with pagination/searchable chunks.
2. Collect only the relevant `bN` IDs.
3. Inspect only complicated target blocks.
4. Batch edits.
5. Re-read only the changed neighborhood if another edit follows.

## 17. Final checklist

Before finishing a DOCX task:

- [ ] Only native `read` + `docx` were used for DOCX semantics.
- [ ] Existing untouched content was preserved rather than regenerated.
- [ ] Current `bN` IDs were used.
- [ ] Large reads were paginated.
- [ ] `inspect` was targeted instead of dumping the whole document.
- [ ] Rich paragraphs were edited with `runs`/`replace`, not unsafe plain `set(text)`.
- [ ] Images were passed by path, not model-generated base64.
- [ ] Equations use native LaTeX→OMML and only supported commands; mathematical alphabets and multi-line environments follow the documented subset.
- [ ] Tables/charts use semantic blocks.
- [ ] Global section/header/footer/watermark changes used `options`.
- [ ] Facts/numbers were verified when the task required current factual accuracy.
- [ ] The final tool operation succeeded and any high-risk changed structure was re-read/inspected appropriately.

The core principle: **read compactly, inspect narrowly, edit semantically, batch changes, preserve everything else.**