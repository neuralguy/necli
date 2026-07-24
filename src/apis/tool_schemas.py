"""OpenAI tools JSON schemas for all necli tools.

Used in API mode to pass native tools via the `tools` parameter
to an OpenAI-compatible API.

Schemas match what the parser in tools/parser.py accepts as args,
so execute_call can execute them unchanged.
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
                "Do NOT use for file operations (cat/echo/tee/heredoc/sed for writes) — use create_file/patch_file. "
                "cd /any/path && cmd applies only within this single call. "
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
                "Read up to 20 files at once, or list a directory."
                "Supports images, .docx, .csv/.tsv, Excel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "lines": {"type": "string", "description": "Line range like '10-50' or '5'."},
                                    },
                                    "required": ["path"],
                                },
                            ]
                        },
                        "description": "One or more file paths. Each item is a path string, or an object with path + optional lines.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents with a regular expression or list files by include globs. "
                "Path may be a file or directory; directories are searched recursively while automatically excluding hidden directories, dependencies, caches, "
                "build output, and other project junk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to find in file contents"},
                    "path": {"type": "string", "description": "File or directory to search"},
                    "include": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "File glob(s), e.g. '*.py' or 'src/**/*.ts'. Here you can specify a file path",
                    },
                    "case_sensitive": {"type": "boolean", "description": "Match case exactly; default false"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Maximum results, default 100"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": (
                "Targeted file edit. `replace` replaces the exact text in `find`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string", "description": "Text to find"},
                    "replace": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "find"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": (
                "Create a new file or fully overwrite an existing one. "
                "For editing existing files use patch_file"
            ),
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
                "read_files on the .docx returns its exact HTML source, edit it IN PLACE with "
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
                "Render a page of a .docx (or .pdf) to a PNG image. Check fonts, "
                "margins, tables, formulas, page breaks — things invisible in the HTML source. "
                "Use after create_docx to visually verify formatting of an existing document"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": ".docx or .pdf path."},
                    "pages": {
                        "type": "string",
                        "description": (
                            "Multiple pages: range '2-5', set '1,3,7', mixed '2-4,8,10-11', "
                            "or 'all' for all pages. Omit for single page (default 1)."
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
            "name": "poll",
            "description": (
                "Ask user a question with options. Use instead of plain text questions "
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
            "name": "web_search",
            "description": (
                "Search the web"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 5,
                        "description": "Search queries (1-5). Each query is searched separately",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per query (default 5)",
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
            "description": (
                "Fetch content from one or more URLs"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more URLs to fetch",
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
                "Search for images on the web. Useful for finding pictures for "
                "websites, mockups, docs, etc. Searches and downloads images to assets/images"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 5,
                        "description": "Search queries (1-5). Each query is searched separately",
                    },
                    "max_results": {"type": "integer", "description": "Max images to return per query (default 10)"},
                    "size": {"type": "string", "description": "ddg size filter: Small|Medium|Large|Wallpaper"},
                    "type": {"type": "string", "description": "ddg type filter: photo|clipart|gif|transparent|line"},
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
                "Returns the FULL text of a previously truncated tool output. "
                "Use when you see the marker "
                "'expand via call expand_tool_result {\"id\": \"...\"}' "
                "in a result and need the full text"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Identifier from the marker in the truncated output"},
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
                "Save a long-term fact to persistent memory. "
                "Only store what is NOT derivable from code/git/AGENTS.md: "
                "user preferences and role (type=user), feedback on how to work "
                "(type=feedback), context of current tasks/goals/incidents "
                "(type=project), external references/values (type=reference). "
                "Convert relative dates to absolute (YYYY-MM-DD). If a file with "
                "that name already exists in this scope, it is updated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short memory file name, e.g. 'user-profile'."},
                    "body": {"type": "string", "description": "The fact content. For feedback, add 'Why:' and 'How to apply:'"},
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
                            "global — cross-project fact (about the user/general "
                            "preferences), visible in all projects."
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
            "description": "List of saved memory files for the project with brief contents",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read the full contents of a specific memory file",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Memory file name (with or without .md)"}},
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
                    "preset": {"type": "string", "description": "Preset role name from .data/agents/"},
                    "label": {"type": "string", "description": "Required 1-2 word name of WHAT this subagent does (e.g. 'Auth API', 'Landing')"},
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
            "description": "Load a skill from .data/skills by name",
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
                "Find all references to a symbol via LSP. Use to locate every "
                "usage of a function/class/variable across the project"
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
                "Use after editing to catch type errors, undefined names, etc."
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
                "Task checklist (3+ steps). action: create (goal + steps[], once, min 3), update (by index or title, status: "
                "pending|in_progress|done|skipped), add_step. Mark in_progress when starting a step, done when finished; all done/skipped before final reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "add_step"]},
                    "goal": {"type": "string", "description": "Single line — the goal of the entire task (for create)."},
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
                        "description": "Full list of steps for create. Minimum 3.",
                    },
                    "index": {
                        "oneOf": [
                            {"type": "integer"},
                            {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                        ],
                        "description": "1-based step index or list of indices (for update).",
                    },
                    "title": {"type": "string", "description": "Step title: for add_step or finding a step in update."},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "skipped"],
                        "description": "New step status (for update/add_step).",
                    },
                    "notes": {"type": "string", "description": "Brief note on the step (optional)."},
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
                                "notes": {"type": "string", "description": "Note (optional)"},
                            },
                            "required": ["index"],
                        },
                        "description": "List of changes for batch update: each object has an index and optionally status/notes.",
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
                "Think out loud before acting. Does NOT execute code — displays "
                "the thought in the UI. Used when think-mode is enabled: EXACTLY ONE call "
                "to think before any other tools and before the final answer, "
                "with one long thought covering all reasoning."
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


from config import READ_ONLY_TOOLS as _READ_ONLY_TOOL_NAMES  # noqa: E402

_PLANNING_TOOL_NAMES = _READ_ONLY_TOOL_NAMES | {"poll", "skill", "web_search", "web_fetch"}
_AUTONOMOUS_TOOL_NAMES = _PLANNING_TOOL_NAMES | {"shell", "subagent"}

# Cache for get_tool_schemas. Key — (mode, mcp_signature), where mcp_signature is
# a tuple of MCP tool names. Invalidated whenever the MCP set changes.
_SCHEMAS_CACHE: dict[tuple, list[dict[str, Any]]] = {}


def _mcp_signature() -> tuple:
    try:
        from apis.mcp_client import get_mcp_tool_schemas
        return tuple(sorted(
            s.get("function", {}).get("name", "") for s in get_mcp_tool_schemas()
        ))
    except Exception:
        return ()


def _resolve_think_for_schemas() -> bool:
    try:
        from config.settings import get as _get
        return bool(_get("think_enabled", False))
    except Exception:
        return False




def get_tool_schemas(mode: str = "agent", active_skills=None) -> list[dict[str, Any]]:
    """Returns JSON schemas for tools matching the given mode.

    plan/planning → read-only tools + plan (+ think if enabled).
    autonomous    → planning tools + shell + subagent (+ think if enabled).
    agent         → all base tools + MCP + plan (+ think if enabled).

    think is included ONLY when think-mode is active — otherwise the model
    would call it unnecessarily. plan is always available (task structure).

    active_skills kept for call compatibility and does not affect the result:
    skills add instructions but do not restrict tools.
    """
    think_on = _resolve_think_for_schemas()
    restricted_mode = mode in ("plan", "planning", "autonomous", "auto")
    mcp_sig = () if restricted_mode else _mcp_signature()
    cache_key = (mode, mcp_sig, think_on)
    cached = _SCHEMAS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    if restricted_mode:
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
    _SCHEMAS_CACHE[cache_key] = base
    return list(base)


def tool_requires_args(name: str) -> bool:
    """True if the tool has required parameters.

    Needed for recovery from a proxy bug: when streaming native tool_calls,
    some providers return empty `{}` args. If a tool has required fields,
    empty `{}` almost certainly means lost arguments, so a fallback re-request
    is needed. For argument-less tools (memory_list etc.), empty `{}` is valid
    and no fallback is needed.
    """
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {})
        if fn.get("name") == name:
            params = fn.get("parameters", {}) or {}
            return bool(params.get("required"))
    # Unknown/MCP tool: safer to assume args are needed
    # (extra fallback is cheaper than lost args). But if it's not our tool at all —
    # empty dict is harmless; return False to avoid loops.
    return False


def invalidate_schemas_cache() -> None:
    """Resets the get_tool_schemas cache. Called when MCP servers change."""
    _SCHEMAS_CACHE.clear()



