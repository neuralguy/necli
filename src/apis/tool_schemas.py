"""OpenAI tools JSON schemas for all necli tools.

Used in API mode to pass native tools via the `tools` parameter
to an OpenAI-compatible API.

Schemas match what the parser in tools/parser.py accepts as args,
so execute_call can execute them unchanged.
"""

from __future__ import annotations

import copy
import threading
from typing import Any


def _pptx_operation_schema() -> dict[str, Any]:
    """Describe PPTX edits deeply enough for provider-side tool generation.

    Some OpenAI-compatible providers discard keys that are not declared in an
    object schema.  An unstructured ``items: {type: object}`` consequently
    turns every generated operation into ``{}`` before necli receives it.
    """
    transform = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "cx": {"type": "integer"},
            "cy": {"type": "integer"},
            "rot": {"type": "integer"},
            "flip_h": {"type": "boolean"},
            "flip_v": {"type": "boolean"},
        },
    }
    fill = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["none", "solid", "gradient", "pattern"]},
            "color": {"type": "string"},
            "angle": {"type": "number"},
            "path": {"type": "string"},
            "preset": {"type": "string"},
            "fg": {"type": "string"},
            "bg": {"type": "string"},
            "stops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pos": {"type": "number"},
                        "color": {"type": "string"},
                    },
                    "required": ["pos", "color"],
                },
            },
        },
    }
    stroke = {
        "type": "object",
        "properties": {
            "width": {"type": "integer"},
            "fill": fill,
            "cap": {"type": "string"},
            "dash": {"type": "string"},
            "head_end": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "w": {"type": "string"},
                    "len": {"type": "string"},
                },
            },
            "tail_end": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "w": {"type": "string"},
                    "len": {"type": "string"},
                },
            },
        },
    }
    font_patch = {
        "type": "object",
        "properties": {
            "font_size": {"type": "number"},
            "font_family": {"type": "string"},
            "color": {"type": "string"},
            "bold": {"type": "boolean"},
            "italic": {"type": "boolean"},
            "underline": {"type": "boolean"},
            "strike": {"type": "boolean"},
            "align": {"type": "string", "enum": ["left", "center", "right", "justify"]},
        },
    }
    paragraph_patch = {
        "type": "object",
        "properties": {
            "align": {"type": "string", "enum": ["left", "center", "right", "justify"]},
            "level": {"type": "integer"},
            "line_height": {"type": "number"},
            "line_exact": {"type": "number"},
            "space_before": {"type": "number"},
            "space_after": {"type": "number"},
            "mar_l": {"type": "integer"},
            "indent": {"type": "integer"},
        },
    }
    patch = {
        "type": "object",
        "properties": {
            **transform["properties"],
            **font_patch["properties"],
            **paragraph_patch["properties"],
        },
        "description": "Transform, font, or paragraph fields appropriate for op.",
    }
    return {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": [
                    "add_shape",
                    "add_text",
                    "add_picture",
                    "add_table",
                    "set_text",
                    "replace_all",
                    "transform",
                    "set_transform",
                    "set_fill",
                    "set_stroke",
                    "set_font",
                    "set_paragraph_format",
                    "edit_table_cell",
                    "delete_element",
                    "group",
                    "ungroup",
                    "set_picture_crop",
                    "set_picture_opacity",
                    "set_slide_background",
                    "set_slide_hidden",
                    "duplicate_slide",
                    "insert_blank_slide",
                    "delete_slide",
                    "move_slide",
                    "set_slide_size",
                ],
                "description": (
                    "Operation name. Required fields: add_shape/add_text/add_picture/add_table: slide_index plus content; "
                    "set_text/transform/set_fill/set_stroke/set_font/set_paragraph_format/delete_element: slide_index+element_id; "
                    "edit_table_cell: slide_index+element_id+row+column; group: slide_index+element_ids; ungroup: slide_index+element_id; "
                    "set_picture_crop/set_picture_opacity: slide_index+element_id+src_rect/opacity; "
                    "set_slide_background/set_slide_hidden: slide_index+color/hidden; duplicate_slide/delete_slide: existing slide_index; "
                    "insert_blank_slide: insertion slide_index in 0..slide_count, where slide_count appends; "
                    "move_slide: from_index+to_index; set_slide_size: cx+cy; replace_all: search+replacement."
                ),
            },
            "slide_index": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Zero-based existing slide for element/slide operations. For insert_blank_slide only, this is the "
                    "destination position in 0..current slide_count inclusive; use current slide_count to append."
                ),
            },
            "element_id": {
                "type": "string",
                "description": "Stable id from read/inspect."
            },
            "element_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "At least two top-level ids."
            },
            "text": {"type": "string"},
            "name": {"type": "string"},
            "shape": {"type": "string"},
            "transform": transform,
            "patch": patch,
            "style": font_patch,
            "fill": fill,
            "stroke": stroke,
            "image_path": {"type": "string"},
            "data_base64": {"type": "string"},
            "extension": {"type": "string"},
            "rows": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"oneOf": [{"type": "string"}, {"type": "number"}]},
                },
            },
            "col_widths": {"type": "array", "items": {"type": "integer"}},
            "row_heights": {"type": "array", "items": {"type": "integer"}},
            "row": {"type": "integer", "minimum": 0},
            "column": {"type": "integer", "minimum": 0},
            "search": {"type": "string"},
            "replacement": {"type": "string"},
            "case_sensitive": {"type": "boolean"},
            "replace_all": {"type": "boolean"},
            "paragraph_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
            "src_rect": {
                "type": "object",
                "properties": {
                    "l": {"type": "number"},
                    "t": {"type": "number"},
                    "r": {"type": "number"},
                    "b": {"type": "number"},
                },
            },
            "opacity": {"type": "number", "minimum": 0, "maximum": 1},
            "color": {"type": "string"},
            "hidden": {"type": "boolean"},
            "clear_text": {"type": "boolean"},
            "from_index": {
                "type": "integer",
                "minimum": 0,
                "description": "Source slide index for move_slide."
            },
            "to_index": {
                "type": "integer",
                "minimum": 0,
                "description": "Destination slide index for move_slide."
            },
            "cx": {"type": "integer"},
            "cy": {"type": "integer"},
        },
        "required": ["op"],
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command"},
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Run the command in the background for heavy/long tasks"
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read a single file or directory. "
                "Supports images, .docx, .pptx, .csv/.tsv"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute path to the file or directory",
                    },
                    "limit": {"type": "number", "description": "Max lines to read (default 1000)"},
                    "offset": {"type": "number", "description": "Starting line number (1-indexed)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents with a regular expression or list files by include globs. "
                "Path may be a file or directory. Directories are searched recursively while automatically excluding hidden directories, dependencies, caches, "
                "build output, and other project junk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression to find in file contents",
                    },
                    "path": {"type": "string", "description": "File or directory to search"},
                    "include": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "File glob(s), e.g. '*.py' or 'src/**/*.ts'. Here you can specify a file path",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case exactly, default false",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": "Maximum results, default 100",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Atomic targeted file edits. "
                "Each patch is applied sequentially and the whole call rolls back if any match fails"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "patches": {
                        "type": "array",
                        "minItems": 1,
                        "description": "FIND/REPLACE list",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string"},
                                "replace": {"type": "string"},
                            },
                            "required": ["find", "replace"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["path", "patches"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a new file or fully overwrite an existing one"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx",
            "description": (
                "Native DOCX tool. Use help for uncommon syntax"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "edit", "inspect", "help"]},
                    "path": {"type": "string"},
                    "topic": {
                        "type": "string",
                        "enum": ["blocks", "runs", "edit", "options"],
                        "description": "help only."
                    },
                    "out": {
                        "type": "string",
                        "description": "Output path"
                    },
                    "blocks": {
                        "type": "array",
                        "items": {"anyOf": [{"type": "object"}, {"type": "string"}]},
                        "description": "Strings are paragraphs"
                    },
                    "ops": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Targets use current bN ids."
                    },
                    "target": {"description": "Block id/index or array; omit for metadata"},
                    "options": {
                        "type": "object",
                        "description": "Document options"
                    },
                    "includeRaw": {
                        "type": "boolean",
                        "description": "Expensive raw OOXML inspection"
                    },
                    "includeMedia": {
                        "type": "boolean",
                        "description": "Expensive media inspection"
                    },
                    "includeMeta": {
                        "type": "boolean",
                        "description": "Include metadata"
                    },
                    "overwrite": {"type": "boolean"},
                    "eastAsiaFont": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pptx",
            "description": (
                "Native PPTX tool. Use read/inspect for stable element ids"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "edit", "inspect", "render", "validate", "help"],
                    },
                    "path": {"type": "string"},
                    "out": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "items": _pptx_operation_schema(),
                        "description": "Each operation requires op"
                    },
                    "slide": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Zero-based slide index",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["svg", "png", "json"],
                        "description": "Render format",
                    },
                    "width": {
                        "type": "integer",
                        "description": "EMU for create, pixels for render"
                    },
                    "height": {"type": "integer", "description": "Create height in EMU"},
                    "atomic": {
                        "type": "boolean",
                        "description": "Atomic by default"
                    },
                    "includeXml": {
                        "type": "boolean",
                        "description": "Expensive raw OOXML inspection"
                    },
                    "fullModel": {
                        "type": "boolean",
                        "description": "Expensive complete model inspection"
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Overwrite by default"
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poll",
            "description": (
                "Ask the user a question with options. Use steps for one or more questions; "
                "set multiple=true on a step to allow multiple selections. Max 10 steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                                        "steps": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "options": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 10,
                                },
                        "multiple": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("Search the web"),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 5,
                        "description": "Search queries",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Max results per query, default 5",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": ("Fetch content from URLs"),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs to fetch",
                    },
                    "raw": {
                        "type": "boolean",
                        "description": "Return raw HTML markup instead of extracted text",
                    },
                },
                "required": ["urls"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_search",
            "description": (
                "Search for images on the web. Searches and downloads images to assets/images"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 5,
                        "description": "Search queries",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max images to return per query, default 10",
                    },
                    "size": {
                        "type": "string",
                        "description": "ddg size filter: Small|Medium|Large|Wallpaper",
                    },
                    "type": {
                        "type": "string",
                        "description": "ddg type filter: photo|clipart|gif|transparent|line",
                    },
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_tool_result",
            "description": (
                "Returns the full text of a previously truncated tool output. "
                "Use when you see the marker "
                '\'expand via call expand_tool_result {"id": "..."}\' '
                "in a result and need the full text"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Id from the marker",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": (
                "Manage persistent memory"
                "For write, name,body,type are required. For read/delete, name is required"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["write", "list", "read", "delete"],
                    },
                    "name": {
                        "type": "string",
                        "description": "Short memory file name",
                    },
                    "body": {
                        "type": "string",
                        "description": "The fact content",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "Memory type",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["project", "global"],
                        "description": (
                            "project (default) — memory of the current project. "
                            "global — cross-project fact (about the user/general preferences), visible in all projects"
                        ),
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subagent",
            "description": (
                "Run one subagent, a parallel fan-out, or a phased/pipeline orchestration "
                "over many isolated subagents (git worktree each). For a simple task pass "
                "`prompt`. For fan-out pass `tasks`. For phased/pipeline orchestration pass "
                "`items`+`stages` or `phases`. ⚠ When the work has SEVERAL sequential phases "
                "(e.g. Scout → Synthesis → Implement → Verify), pass them ALL AT ONCE in a "
                "single call via `phases`: [{name, tasks}, ...]. Do NOT call subagent once per "
                "phase and wait — one call runs every phase in order automatically (phase N+1 "
                "starts only after phase N finishes, agents inside a phase run in parallel) and "
                "the live panel shows the whole pipeline, ticking each finished phase green. "
                "Each task/stage can set role, preset, model, "
                "label, phase, and depends_on. ALWAYS give each task BOTH: a `phase` (the "
                "stage/group it belongs to, e.g. 'Scout', 'Implement', 'Verify') AND a `label` "
                "(1-2 word name of WHAT it does, e.g. 'Auth API', 'Landing'). The panel groups "
                "agents by phase and shows each by its label — without them it shows a bland "
                "'Agents'/'Sub1'. Think in levels: phases → agents → per-agent config. "
                "Subagents always run in agent mode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Run label shown in the final result.",
                    },
                    "goal": {"type": "string", "description": "Alias for name / high-level goal."},
                    "isolate": {
                        "type": "boolean",
                        "description": (
                            "Environment isolation for ALL subagents in this run. Default false: "
                            "subagents write DIRECTLY into the shared working directory — so you MUST "
                            "split the work into INDEPENDENT slices (each subagent owns distinct "
                            "files; no two touch the same path). Set true to give each subagent an "
                            "isolated git worktree on its own branch (you merge results manually) — "
                            "use this when tasks would otherwise conflict on the same files."
                        ),
                    },
                    "prompt": {"type": "string", "description": "Single-subagent task prompt."},
                    "model": {
                        "type": "string",
                        "description": "Model override (display_name or model_id).",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["coder", "researcher", "reviewer", "planner", "coordinator"],
                    },
                    "preset": {
                        "type": "string",
                        "description": "Preset role name from .data/agents/",
                    },
                    "label": {
                        "type": "string",
                        "description": "Required 1-2 word name of WHAT this subagent does (e.g. 'Auth API', 'Landing')",
                    },
                    "phase": {"type": "string", "description": "Display phase name"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-based task indices that must finish before this task.",
                    },
                    "tasks": {
                        "type": "array",
                        "description": "Parallel subagent tasks. Each task needs prompt.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"},
                                "model": {"type": "string"},
                                "role": {
                                    "type": "string",
                                    "enum": [
                                        "coder",
                                        "researcher",
                                        "reviewer",
                                        "planner",
                                        "coordinator",
                                    ],
                                },
                                "preset": {"type": "string"},
                                "label": {
                                    "type": "string",
                                    "description": "1-2 word name of WHAT this task does (e.g. 'Auth API'), shown in the live panel.",
                                },
                                "phase": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "integer"}},
                            },
                            "required": ["prompt"],
                        },
                    },
                    "items": {
                        "type": "array",
                        "description": "Pipeline items. Each item is passed through every stage.",
                        "items": {},
                    },
                    "stages": {
                        "type": "array",
                        "description": (
                            "Pipeline stages. Use prompt/template with placeholders: "
                            "{item}, {item_json}, {index}, {item_index}, {stage}, {phase}."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "title": {"type": "string"},
                                "prompt": {"type": "string"},
                                "template": {"type": "string"},
                                "model": {"type": "string"},
                                "role": {
                                    "type": "string",
                                    "enum": [
                                        "coder",
                                        "researcher",
                                        "reviewer",
                                        "planner",
                                        "coordinator",
                                    ],
                                },
                                "preset": {"type": "string"},
                                "label": {
                                    "type": "string",
                                    "description": "1-2 word name of WHAT this stage does, shown in the live panel.",
                                },
                                "phase": {"type": "string"},
                            },
                        },
                    },
                    "phases": {
                        "type": "array",
                        "description": (
                            "Dependency-ordered phases run sequentially in ONE call — pass the "
                            "whole pipeline here at once, never one phase per call. Each phase "
                            "can have tasks[] (parallel agents inside it) and/or items[]+stages[]. "
                            "By default each phase depends on the previous one (phase N+1 waits "
                            "for phase N to finish), so order them as the execution order."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "title": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "integer"}},
                                "tasks": {"type": "array", "items": {"type": "object"}},
                                "items": {"type": "array", "items": {}},
                                "stages": {"type": "array", "items": {"type": "object"}},
                            },
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": "Load a skill",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_references",
            "description": (
                "Find all references to a symbol via LSP"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "character": {"type": "integer"},
                },
                "required": ["path", "line", "character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_diagnostics",
            "description": (
                "Get LSP diagnostics (errors, warnings, type problems) for a file"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to diagnose"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": (
                "Use only when the task is genuinely multi-step. For unfamiliar code, inspect first "
                "and base the plan on evidence. Do not create a plan for a simple task. Each step "
                "should state what will change and how it will be done, including exact paths when "
                "known. Do not force a fixed number of steps; use only as many as the task needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "add_step", "remove_step"]},
                    "goal": {
                        "type": "string",
                        "description": "Single line — the goal of the entire task (for create)",
                    },
                    "steps": {
                        "type": "array",
                        "minItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Concrete step",
                                },
                                "status": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                        "description": "Full list of concrete outcome-level steps for create",
                    },
                    "index": {
                        "oneOf": [
                            {"type": "integer"},
                            {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                        ],
                        "description": "1-based step index/list for update. Single index also selects remove_step position",
                    },
                    "title": {
                        "type": "string",
                        "description": "Step title: for add_step or finding a step in update",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "skipped"],
                        "description": "New step status (for update/add_step)",
                    },
                                        "updates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer", "description": "1-based step index"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "done", "skipped"],
                                    "description": "New status",
                                },
                            },
                            "required": ["index"],
                        },
                        "description": "List of changes for batch update: each object has an index and optionally a new status",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Think tool"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Reasoning text"},
                },
                "required": ["thought"],
            },
        },
    },
]


from tools.registry import PLANNING_TOOLS as _PLANNING_TOOL_NAMES, SWARM_TOOLS as _SWARM_TOOL_NAMES  # noqa: E402, I001

# Cache for get_tool_schemas. Key — (mode, mcp_signature), where mcp_signature is
# a tuple of MCP tool names. Invalidated whenever the MCP set changes.
_SCHEMAS_CACHE: dict[tuple, list[dict[str, Any]]] = {}
_SCHEMAS_LOCK = threading.RLock()
_SCHEMAS_GENERATION = 0


def _mcp_signature() -> tuple:
    try:
        from apis.mcp_client import get_mcp_tool_schemas

        return tuple(sorted(s.get("function", {}).get("name", "") for s in get_mcp_tool_schemas()))
    except Exception:
        return ()


def _resolve_think_for_schemas() -> bool:
    try:
        from config.settings import get as _get

        return bool(_get("think_enabled", False))
    except Exception:
        return False


def _memory_enabled() -> bool:
    try:
        from config.settings import get

        return bool(get("memory_enabled", True))
    except Exception:
        return True


def get_tool_schemas(mode: str = "agent", active_skills=None) -> list[dict[str, Any]]:
    """Returns JSON schemas for tools matching the given mode.

    plan/planning → read-only tools + plan (+ think if enabled).
    swarm         → planning tools + shell + subagent (+ think if enabled).
    agent         → all base tools + MCP + plan (+ think if enabled).

    think is included ONLY when think-mode is active — otherwise the model
    would call it unnecessarily. plan is always available (task structure).

    active_skills kept for call compatibility and does not affect the result:
    skills add instructions but do not restrict tools.
    """
    think_on = _resolve_think_for_schemas()
    restricted_mode = mode in ("plan", "planning", "swarm", "auto")
    mcp_sig = () if restricted_mode else _mcp_signature()
    from tools.registry import get_disabled_tools

    disabled = frozenset(get_disabled_tools())
    memory_on = _memory_enabled()
    cache_key = (mode, mcp_sig, think_on, disabled, memory_on)
    with _SCHEMAS_LOCK:
        cached = _SCHEMAS_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)
        generation = _SCHEMAS_GENERATION

    if restricted_mode:
        allowed = (_SWARM_TOOL_NAMES if mode in ("swarm", "auto") else _PLANNING_TOOL_NAMES) | {
            "plan"
        }
        if think_on:
            allowed = allowed | {"think"}
        base = []
        for schema in TOOL_SCHEMAS:
            if (
                schema["function"]["name"] not in allowed
                or schema["function"]["name"] in disabled
                or (schema["function"]["name"] == "memory" and not memory_on)
            ):
                continue
            if schema["function"]["name"] == "memory":
                schema = copy.deepcopy(schema)
                action = schema["function"]["parameters"]["properties"]["action"]
                action["enum"] = ["list", "read"]
            base.append(schema)
    else:
        base = [
            s
            for s in TOOL_SCHEMAS
            if (s["function"]["name"] != "think" or think_on)
            and s["function"]["name"] not in disabled
            and (s["function"]["name"] != "memory" or memory_on)
        ]
        try:
            from apis.mcp_client import get_mcp_tool_schemas

            base.extend(
                schema
                for schema in get_mcp_tool_schemas()
                if schema.get("function", {}).get("name") not in disabled
            )
        except Exception:
            pass
    with _SCHEMAS_LOCK:
        # If an invalidation happened while the schemas were being built, do
        # not put a stale MCP snapshot back into the freshly cleared cache.
        if generation != _SCHEMAS_GENERATION:
            return list(base)
        # Another thread may have populated the same key while we were
        # building it. Reuse that snapshot instead of replacing it.
        cached = _SCHEMAS_CACHE.setdefault(cache_key, base)
        return list(cached)


def invalidate_schemas_cache() -> None:
    """Resets the get_tool_schemas cache. Called when MCP servers change."""
    global _SCHEMAS_GENERATION
    with _SCHEMAS_LOCK:
        _SCHEMAS_GENERATION += 1
        _SCHEMAS_CACHE.clear()
