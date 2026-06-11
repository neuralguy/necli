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
                "automatically once it finishes. Foreground commands time out at 60s."
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
                            "when it finishes. Foreground (default) times out at 60s."
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
                    "path": {"type": "string", "description": "Single file path."},
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple file paths.",
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
                "Multi-step: {steps: [{question, options}, ...]}. Max 4 options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "options": {"type": "array", "items": {"type": "string"}},
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
                "Сохранить долговременный факт в персистентную память проекта "
                "(переживает сессии, автоматически подмешивается в системный промпт). "
                "Сохраняй ТОЛЬКО то, что НЕ выводимо из кода/git/AGENTS.md: "
                "предпочтения и роль пользователя (type=user), обратную связь о том, "
                "как вести работу (type=feedback), контекст текущих задач/целей/инцидентов "
                "(type=project), внешние референсы/значения (type=reference). "
                "Относительные даты переводи в абсолютные (YYYY-MM-DD). "
                "Если файл с таким name уже есть — он обновляется."
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
            "name": "workflow",
            "description": (
                "Run a Python workflow over subagents with real phases. "
                "Use `phases` for inline workflows, or `script`/`path`/`name` for a Python "
                "workflow file defining `async def run(ctx)`. Python DSL: ctx.phase(title), "
                "ctx.log(text), ctx.agent(prompt, opts), await ctx.parallel([...]), "
                "await ctx.pipeline(items, ...). Subagents always run in agent mode."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Workflow name or saved workflow name."},
                    "goal": {"type": "string", "description": "Alias for workflow name."},
                    "description": {"type": "string"},
                    "isolate": {
                        "type": "boolean",
                        "description": "Default true: run workflow subagents in isolated git worktrees.",
                    },
                    "cache": {
                        "type": "boolean",
                        "description": "Default true. Reuse matching agent results when resume_from_run_id is set.",
                    },
                    "resume_from_run_id": {
                        "type": "string",
                        "description": "Previous workflow run id to reuse cached successful agent results from.",
                    },
                    "fail_fast": {
                        "type": "boolean",
                        "description": "If true, abort workflow on the first failed agent. Default false.",
                    },
                    "args": {
                        "type": "object",
                        "description": "User arguments exposed to Python workflow scripts as global `args`.",
                    },
                    "script": {
                        "type": "string",
                        "description": "Inline Python workflow script with meta dict and async def run(ctx).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to a Python workflow file, or name under .data/workflows/.",
                    },
                    "meta": {"type": "object"},
                    "phases": {
                        "type": "array",
                        "description": "Inline real phases. Each phase has title/name and tasks[] or agents[].",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "title": {"type": "string"},
                                "detail": {"type": "string"},
                                "tasks": {"type": "array", "items": {"type": "object"}},
                                "agents": {"type": "array", "items": {"type": "object"}},
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
            "name": "subagent",
            "description": (
                "Run one subagent, a parallel fan-out, or a phased/pipeline orchestration "
                "over many isolated subagents (git worktree each). For a simple task pass "
                "`prompt`. For fan-out pass `tasks`. For workflow-style orchestration pass "
                "`items`+`stages` or `phases`. Each task/stage can set role, preset, model, "
                "label, phase, and depends_on. Subagents always run in agent mode."
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
                    "label": {"type": "string", "description": "Short display label for this subagent."},
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
                                "label": {"type": "string"},
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
                                "label": {"type": "string"},
                                "phase": {"type": "string"},
                            },
                        },
                    },
                    "phases": {
                        "type": "array",
                        "description": (
                            "Dependency-ordered phases. Each phase can have tasks[] and/or "
                            "items[]+stages[]. By default each phase depends on the previous one."
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


from config import READ_ONLY_TOOLS as _PLANNING_TOOL_NAMES  # noqa: E402

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


def get_tool_schemas(mode: str = "agent") -> list[dict[str, Any]]:
    """Возвращает JSON-схемы инструментов для нужного режима.

    plan  → read-only инструменты + plan (+ think если включён).
    agent → все базовые + MCP + plan (+ think если включён).

    think попадает в схемы ТОЛЬКО при активном think-режиме — иначе модель
    звала бы его без надобности. plan доступен всегда (структурирование задач).
    """
    think_on = _resolve_think_for_schemas()
    mcp_sig = () if mode == "plan" else _mcp_signature()
    cache_key = (mode, mcp_sig, think_on)
    cached = _SCHEMAS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    if mode == "plan":
        allowed = _PLANNING_TOOL_NAMES | {"plan"}
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
    _SCHEMAS_CACHE[cache_key] = base
    return list(base)


def invalidate_schemas_cache() -> None:
    """Сбрасывает кэш get_tool_schemas. Вызывается при изменении MCP servers."""
    _SCHEMAS_CACHE.clear()



