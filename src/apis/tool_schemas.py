"""OpenAI tools JSON schemas для всех necli инструментов.

Используется в API-режиме для нативной передачи инструментов через
параметр `tools` в OpenAI-совместимый API.

Схемы соответствуют тому, что парсер из tools/parser.py принимает
как args, чтобы execute_call мог их выполнить без изменений.
"""

from __future__ import annotations

from typing import Any


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command (git, pip, make, tests, etc.). "
                "Do NOT use for file operations (cat/echo/tee/heredoc/sed for writes) — "
                "use write_file/create_file/patch_file. "
                "`cd` is allowed and may be chained to enter any directory "
                "(e.g. `cd /any/path && cmd`); it applies only within this single call. "
                "Prefer separate calls for unrelated commands. "
                "For heavy/long commands (builds, full test suites, long downloads) set "
                "background=true: the command runs detached, you get a job-id immediately "
                "and can keep working; a notification with its output is delivered "
                "automatically once it finishes. Do NOT call poll just to wait for a "
                "background job; wait for the automatic completion notification. "
                "Foreground commands time out at 60s."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "background": {
                        "type": "boolean",
                        "description": (
                            "Run the command in the background for heavy/long tasks. "
                            "Returns a job-id at once; output arrives as a notification "
                            "when it finishes. Do NOT call poll just to wait for it; "
                            "wait for the automatic completion notification. "
                            "Foreground (default) times out at 60s."
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
            "name": "read_files",
            "description": (
                "Read up to 20 files at once. Files are read fully. "
                "Use 'lines' ONLY to verify changes after patch_file. "
                "Supports images, .docx, .csv/.tsv, Excel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Single file path, or multiple file paths.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "lines": {"type": "string"},
                                        "encoding": {"type": "string"},
                                    },
                                    "required": ["path"],
                                },
                            ]
                        },
                        "description": "Multiple file paths, optionally with per-file line ranges.",
                    },
                    "lines": {"type": "string", "description": "Line range like '10-50' or '5'."},
                    "encoding": {"type": "string", "description": "Text encoding, default utf-8."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file entirely. "
                "Use only for new files or full rewrites of small files (<30 lines). "
                "For editing existing files use patch_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Targeted file edit — ONE change per call. Use find/replace, "
                "line/insert, or delete_lines. For several edits in one file, "
                "make several separate patch_file calls (one change each)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string", "description": "Text to find (one change per call)."},
                    "replace": {"type": "string", "description": "Replacement text."},
                    "line": {"type": "integer", "description": "Line number for insert."},
                    "insert": {"type": "string", "description": "Text to insert after 'line'."},
                    "delete_lines": {"type": "string", "description": "Range like '10-15' to delete."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file. Errors if file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_docx",
            "description": (
                "Render an HTML document to a .docx file via Pandoc. "
                "Use this for any rich-formatted document: headings, tables, "
                "lists, images, and math. LaTeX math inside $...$ or $$...$$ "
                "is converted to native editable Word formulas (OMML). "
                "Images: either base64 data URIs (data:image/png;base64,...) "
                "or local file paths in <img src>. Requires pandoc in PATH. "
                "EDITING an existing .docx: do NOT rewrite the whole HTML. "
                "read_files on the .docx returns its exact HTML source (marked "
                "'editable via patch_file/create_docx'); edit it IN PLACE with "
                "patch_file (small find/replace on that HTML), then call create_docx "
                "once with the FULL updated HTML to regenerate the .docx for viewing. "
                "The HTML source is persisted, so patch_file targets survive between turns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Output .docx path."},
                    "content": {"type": "string", "description": "HTML markup (LaTeX math supported)."},
                    "reference_doc": {
                        "type": "string",
                        "description": "Optional .docx template for styles (fonts, headings).",
                    },
                    "overwrite": {"type": "boolean", "description": "Default true."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docx_screenshot",
            "description": (
                "Render a page of a .docx (or .pdf) to a PNG image and attach it "
                "to your next turn so you can SEE the actual laid-out page: fonts, "
                "margins, tables, formulas, page breaks — things invisible in the "
                "HTML source. Uses LibreOffice (docx→pdf) + PyMuPDF (pdf→png). "
                "Use after create_docx to visually verify formatting, or to inspect "
                "any page(s) of an existing document. Render a single page via 'page', "
                "or multiple pages at once via 'pages' (range '2-5', set '1,3,7', "
                "mixed '2-4,8,10-11', list [1,4,9], or 'all' for the whole document). "
                "All rendered pages are attached in order to your next turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": ".docx or .pdf path."},
                    "page": {"type": "integer", "description": "1-based single page number, default 1."},
                    "pages": {
                        "type": "string",
                        "description": (
                            "Multiple pages: range '2-5', set '1,3,7', mixed '2-4,8,10-11', "
                            "or 'all'. Overrides 'page' when provided."
                        ),
                    },
                    "dpi": {
                        "type": "integer",
                        "description": (
                            "Render resolution in DPI (clamped to 50-600). Higher = sharper "
                            "but larger images; raise it to read small text/formulas. Default 200."
                        ),
                        "minimum": 50,
                        "maximum": 600,
                        "default": 200,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file",
            "description": "Rename or move a file. Arguments: 'path' (source) and 'new_path' (destination). NOT 'source'/'destination', NOT 'new_name'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "new_path": {"type": "string"},
                },
                "required": ["path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file or directory. Arguments: 'path' (source) and 'dest' (destination). NOT 'source'/'destination'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "dest": {"type": "string"},
                },
                "required": ["path", "dest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move a file or directory. Arguments: 'path' (source) and 'dest' (destination). NOT 'source'/'destination'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "dest": {"type": "string"},
                },
                "required": ["path", "dest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List directory contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "all": {"type": "boolean", "description": "Include hidden files."},
                    "long": {"type": "boolean", "description": "Detailed format."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tree",
            "description": "Display directory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer", "description": "Max depth, default 3."},
                    "all": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create a directory (including parents).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rmdir",
            "description": "Remove a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "force": {"type": "boolean", "description": "Recursive delete."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files by name or glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "description": "Glob pattern like '*.py'."},
                    "name": {"type": "string", "description": "Exact filename."},
                    "type": {"type": "string", "enum": ["file", "dir", "any"]},
                    "depth": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "Search text in files with regex or literal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "ignore_case": {"type": "boolean"},
                    "literal": {"type": "boolean"},
                    "context": {"type": "integer"},
                    "include_ignored": {
                        "type": "boolean",
                        "description": "If true, scan node_modules/.venv/dist/build/.git etc. Default false."
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poll",
            "description": (
                "Ask user a question with options. Use INSTEAD of plain text questions "
                "when uncertain. Single step: {question, options}. "
                "Multi-step: {steps: [{question, options, multiple}, ...]}. "
                "Max 10 steps. Each step is single-select by default; set "
                "multiple=true (or multi_select=true/type='multi') for multi-select."
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
                    "multiple": {"type": "boolean"},
                    "multi_select": {"type": "boolean"},
                    "type": {"type": "string", "enum": ["single", "multi", "multiple", "multi-select"]},
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
                                "multi_select": {"type": "boolean"},
                                "type": {"type": "string", "enum": ["single", "multi", "multiple", "multi-select"]},
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
            "name": "ssh",
            "description": (
                "Run command on remote server via SSH (ControlMaster pooling). "
                "Use ONLY host aliases configured via /ssh, not IPs. "
                "Dangerous commands require user confirmation. "
                "Supports upload/download."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "command": {"type": "string"},
                    "upload": {"type": "string", "description": "Local file to upload."},
                    "download": {"type": "string", "description": "Remote file to download."},
                    "dest": {"type": "string"},
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web via DuckDuckGo OR fetch URL(s) directly. "
                "Search mode: {\"query\": \"...\"}, optional fetch=true / fetch_indices. "
                "Direct fetch mode: {\"url\": \"https://...\"} or {\"urls\": [...]} — "
                "extracts page text via trafilatura, results cached for 1 hour. "
                "Add raw=true (or html=true) to get the page's raw HTML markup "
                "instead of extracted text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "url": {"type": "string", "description": "Direct fetch — single URL"},
                    "urls": {"type": "array", "items": {"type": "string"}, "description": "Direct fetch — multiple URLs"},
                    "max_results": {"type": "integer"},
                    "fetch": {"type": "boolean"},
                    "fetch_indices": {"type": "array", "items": {"type": "integer"}},
                    "raw": {"type": "boolean", "description": "Return raw HTML markup instead of extracted text (fetch mode)."},
                    "html": {"type": "boolean", "description": "Alias for raw."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_search",
            "description": (
                "Search for images on the web. Useful for finding pictures for "
                "websites, mockups, docs, etc. Default source is DuckDuckGo (no key "
                "needed); Unsplash/Pexels are also used if their API keys are set in "
                "config api_keys. Returns a numbered list of image URLs with "
                "dimensions, page link and source. "
                "Set download=true to download images into the project (validated "
                "with Pillow — broken/non-image files are skipped); use "
                "download_indices to pick specific results and download_dir to "
                "choose the folder (default assets/images). License is NOT filtered — "
                "the source is shown so you can check usage rights yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for, e.g. 'mountain sunset'."},
                    "max_results": {"type": "integer", "description": "Max images to return (1-50, default 10)."},
                    "source": {
                        "type": "string",
                        "enum": ["auto", "ddg", "unsplash", "pexels"],
                        "description": "Image source. 'auto' (default) = DuckDuckGo + any configured stock sources.",
                    },
                    "size": {"type": "string", "description": "ddg size filter: Small|Medium|Large|Wallpaper."},
                    "type": {"type": "string", "description": "ddg type filter: photo|clipart|gif|transparent|line."},
                    "color": {"type": "string", "description": "ddg color filter, e.g. Red, Blue, Monochrome."},
                    "download": {"type": "boolean", "description": "Download images to disk and validate them."},
                    "download_indices": {"type": "array", "items": {"type": "integer"}, "description": "Indices of results to download (default: all)."},
                    "download_dir": {"type": "string", "description": "Target folder for downloads (default assets/images)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_diff",
            "description": (
                "Apply a unified diff to the working tree via 'git apply' "
                "(fallback to 'patch -p1'). Use for multi-hunk/multi-file refactors "
                "where patch_file would need many separate calls. "
                "Diff format: '--- a/path' / '+++ b/path' headers, '@@ ... @@' hunks. "
                "Dry-run is performed before apply; on failure nothing is changed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "diff": {
                        "type": "string",
                        "description": "Unified diff body ('--- a/path' / '+++ b/path' headers, '@@ ... @@' hunks).",
                    },
                },
                "required": ["diff"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_tool_result",
            "description": (
                "Возвращает ПОЛНЫЙ текст ранее обрезанного tool output. "
                "Используй, когда в результате видишь маркер "
                "'expand via call expand_tool_result {\"id\": \"...\"}' "
                "и нужен полный текст. id живёт только в текущем процессе CLI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Идентификатор из маркера в обрезанном output"},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": (
                "Сохранить долговременный факт в персистентную память "
                "(переживает сессии, автоматически подмешивается в системный промпт). "
                "Сохраняй ТОЛЬКО то, что НЕ выводимо из кода/git/AGENTS.md: "
                "предпочтения и роль пользователя (type=user), обратную связь о том, "
                "как вести работу (type=feedback), контекст текущих задач/целей/инцидентов "
                "(type=project), внешние референсы/значения (type=reference). "
                "scope='global' — факт НЕ привязан к одному проекту (кто пользователь, "
                "его общие предпочтения/стиль работы, универсальные референсы); такой "
                "факт виден во ВСЕХ проектах. scope='project' (по умолчанию) — только "
                "контекст текущего проекта. Относительные даты переводи в абсолютные "
                "(YYYY-MM-DD). Если файл с таким name уже есть в этом scope — он обновляется."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Короткое имя файла памяти, напр. 'user-profile'."},
                    "body": {"type": "string", "description": "Содержимое: сам факт. Для feedback добавляй 'Why:' и 'How to apply:'."},
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "Тип памяти.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["project", "global"],
                        "description": (
                            "project (default) — память текущего проекта. "
                            "global — кросс-проектный факт (про пользователя/общие "
                            "предпочтения), виден во всех проектах."
                        ),
                    },
                },
                "required": ["name", "body", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list",
            "description": "Список сохранённых memory-файлов проекта с кратким содержанием.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Прочитать содержимое конкретного memory-файла целиком.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Имя memory-файла (с .md или без)."}},
                "required": ["name"],
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
                    "name": {"type": "string", "description": "Run label shown in the final result."},
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
                    "model": {"type": "string", "description": "Model override (display_name or model_id)."},
                    "role": {
                        "type": "string",
                        "enum": ["coder", "researcher", "reviewer", "planner", "coordinator"],
                    },
                    "preset": {"type": "string", "description": "Preset role name from .data/agents/."},
                    "label": {"type": "string", "description": "Required 1-2 word name of WHAT this subagent does (e.g. 'Auth API', 'Landing'), shown in the live panel."},
                    "phase": {"type": "string", "description": "Display phase name."},
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
                                    "enum": ["coder", "researcher", "reviewer", "planner", "coordinator"],
                                },
                                "preset": {"type": "string"},
                                "label": {"type": "string", "description": "1-2 word name of WHAT this task does (e.g. 'Auth API'), shown in the live panel."},
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
                                    "enum": ["coder", "researcher", "reviewer", "planner", "coordinator"],
                                },
                                "preset": {"type": "string"},
                                "label": {"type": "string", "description": "1-2 word name of WHAT this stage does, shown in the live panel."},
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
            "description": "Load a skill from .data/skills by name.",
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
            "name": "lsp_definition",
            "description": (
                "Go to definition via LSP. Use when you need to find where a "
                "symbol (function/class/variable) is defined. Returns list of "
                "'path:line:column'. Args: path (file with symbol), line (1-based), "
                "character (0-based column of symbol)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "line": {"type": "integer", "description": "1-based line number where the symbol is."},
                    "character": {"type": "integer", "description": "0-based column position of the symbol."},
                },
                "required": ["path", "line", "character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_references",
            "description": (
                "Find all references to a symbol via LSP. Use to locate every "
                "usage of a function/class/variable across the project. Returns "
                "list of 'path:line:column'. Args same as lsp_definition."
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
                "Get LSP diagnostics (errors, warnings, type problems) for a file. "
                "Re-parses the file from disk and waits up to 4s for the language "
                "server to report issues. Use after editing to catch type errors, "
                "unused imports, undefined names, etc. Output lines: "
                "'SEVERITY line:col [source:code] message'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to diagnose."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_hover",
            "description": (
                "Get hover info (type, signature, docstring) for a symbol via "
                "LSP. Use to inspect what a function/class is without reading "
                "its source file. Args same as lsp_definition."
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
            "name": "plan",
            "description": (
                "Task checklist (3+ steps), UI only — runs no code. action: create "
                "(goal + steps[], once, min 3), update (by index or title, status: "
                "pending|in_progress|done|skipped), add_step. Mark in_progress when "
                "starting a step, done when finished; all done/skipped before final reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "add_step"]},
                    "goal": {"type": "string", "description": "Одна строка — цель всей задачи (для create)."},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "status": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                        "description": "Полный список шагов (для create). Минимум 3.",
                    },
                    "index": {"type": "integer", "description": "1-based индекс шага (для update)."},
                    "title": {"type": "string", "description": "Заголовок шага: для add_step или поиска шага в update."},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "skipped"],
                        "description": "Новый статус шага (для update/add_step).",
                    },
                    "notes": {"type": "string", "description": "Краткая заметка к шагу (опционально)."},
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
                "Размышление вслух перед действиями. НЕ выполняет код — отображает "
                "мысль в UI. Используется когда включён think-режим: РОВНО ОДИН вызов "
                "think перед любыми другими инструментами и перед финальным ответом, "
                "с одной длинной мыслью, покрывающей все рассуждения."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Текст рассуждения."},
                },
                "required": ["thought"],
            },
        },
    },
]


from config import READ_ONLY_TOOLS as _READ_ONLY_TOOL_NAMES  # noqa: E402

_PLANNING_TOOL_NAMES = _READ_ONLY_TOOL_NAMES | {"poll", "skill", "web_search"}
_AUTONOMOUS_TOOL_NAMES = _PLANNING_TOOL_NAMES | {"shell", "subagent"}

# Кэш для get_tool_schemas. Ключ — (mode, mcp_signature), где mcp_signature —
# кортеж имён MCP-инструментов. Инвалидируется при любом изменении набора MCP.
_SCHEMAS_CACHE: dict[tuple, list[dict[str, Any]]] = {}


def _mcp_signature() -> tuple:
    try:
        from apis.mcp_client import get_mcp_tool_schemas
        names = tuple(sorted(
            s.get("function", {}).get("name", "") for s in get_mcp_tool_schemas()
        ))
        return names
    except Exception:
        return ()


def _resolve_think_for_schemas() -> bool:
    try:
        from config.settings import get as _get
        return bool(_get("think_enabled", False))
    except Exception:
        return False


def _skill_gated_filter(active_skills) -> frozenset:
    """Набор гейтящихся инструментов, которые сейчас НЕ должны быть в схемах.

    Возвращает frozenset скрытых имён (для хешируемого cache_key).
    """
    try:
        from skills.registry import GATED_TOOLS, visible_gated_tools

        visible = visible_gated_tools(set(active_skills or ()))
        return frozenset(GATED_TOOLS - visible)
    except Exception:
        return frozenset()


def get_tool_schemas(mode: str = "agent", active_skills=None) -> list[dict[str, Any]]:
    """Возвращает JSON-схемы инструментов для нужного режима.

    plan/planning → read-only инструменты + plan (+ think если включён).
    autonomous    → planning-инструменты + shell + subagent (+ think если включён).
    agent         → все базовые + MCP + plan (+ think если включён).

    think попадает в схемы ТОЛЬКО при активном think-режиме — иначе модель
    звала бы его без надобности. plan доступен всегда (структурирование задач).

    active_skills — множество загруженных скиллов. Инструменты, гейтящиеся
    скиллом (web_search/image_search→web, ssh→ssh, subagent→subagents),
    исключаются из схем, пока соответствующий скилл не активен. Так модель не
    видит инструмент, пока не загрузит его скилл.
    """
    think_on = _resolve_think_for_schemas()
    restricted_mode = mode in ("plan", "planning", "autonomous", "auto")
    mcp_sig = () if restricted_mode else _mcp_signature()
    hidden = _skill_gated_filter(active_skills)
    cache_key = (mode, mcp_sig, think_on, hidden)
    cached = _SCHEMAS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    if mode in ("plan", "planning", "autonomous", "auto"):
        allowed = (_AUTONOMOUS_TOOL_NAMES if mode in ("autonomous", "auto") else _PLANNING_TOOL_NAMES) | {"plan"}
        if think_on:
            allowed = allowed | {"think"}
        base = [
            s for s in TOOL_SCHEMAS
            if s["function"]["name"] in allowed
        ]
    else:
        base = [
            s for s in TOOL_SCHEMAS
            if s["function"]["name"] != "think" or think_on
        ]
        try:
            from apis.mcp_client import get_mcp_tool_schemas
            base.extend(get_mcp_tool_schemas())
        except Exception:
            pass
    if hidden:
        base = [s for s in base if s["function"]["name"] not in hidden]
    _SCHEMAS_CACHE[cache_key] = base
    return list(base)


def tool_requires_args(name: str) -> bool:
    """True, если у инструмента есть обязательные параметры.

    Нужен для восстановления после прокси-бага: при стриминге native tool_calls
    некоторые провайдеры отдают args пустым `{}`. Если у инструмента есть
    required-поля, пустой `{}` — почти наверняка потерянные аргументы, и нужен
    фолбэк-перезапрос. Для безаргументных инструментов (memory_list и т.п.)
    пустой `{}` валиден — фолбэк не нужен.
    """
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {})
        if fn.get("name") == name:
            params = fn.get("parameters", {}) or {}
            return bool(params.get("required"))
    # Неизвестный/MCP-инструмент: безопаснее считать, что аргументы нужны
    # (лишний фолбэк дешевле потерянных args). Но если это вообще не наш тул —
    # пустой dict не критичен; возвращаем False, чтобы не зацикливать.
    return False


def invalidate_schemas_cache() -> None:
    """Сбрасывает кэш get_tool_schemas. Вызывается при изменении MCP servers."""
    _SCHEMAS_CACHE.clear()



