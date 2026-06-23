from prompts._core import _OPEN, _CLOSE


TOOL_FORMAT_BLOCK = f"""# Tool call format

Two mechanisms: FENCED (text blocks with streaming) and NATIVE (function calling). Native is preferred
when the provider supports it — it converts to fenced blocks for the UI automatically. Use one or the
other for any given call, never duplicate the same call in both forms.

FENCED format — asymmetric markers `{_OPEN}` (open) and `{_CLOSE}` (close):

  {_OPEN} <tool> [attrs]
  ...body...
  {_CLOSE}

- Open line  STARTS with EXACTLY THREE colons → `{_OPEN} <tool> [path="..." or other attrs]`
- Close line ENDS    with EXACTLY THREE colons → `{_CLOSE}` (bare, no tool name)
- ⚠ Prefer EXACTLY THREE colons `:::` — count them: `:` `:` `:` `call`. (Two colons `::call` is
  tolerated and still executes, but three is the canonical form — use it.)
- The body between them can contain anything: triple backticks, tildes, HTML, code, markdown.
- These markers never appear in real source code, so any body is safe."""


TOOL_FORMAT_BLOCK_NATIVE = """# Tool call format

You call tools via NATIVE function calling: emit one or more tool_calls with the tool name and a JSON
arguments object. The provider executes them and returns the result. Never write the call as plain
text — always use the function-calling mechanism. Never duplicate the same call twice.

⛔ FORBIDDEN in your reply text: any text-mode / fenced imitation of a tool call. Do NOT write
pseudo-call code fences, do NOT write textual FIND/REPLACE patch sections, do NOT write `$ <command>`
lines or `Output:` / `Result:` / tool-result markers to simulate execution. Such text is NOT a tool
call — the provider will NOT execute it, it is just plain text. Call tools ONLY via the native
function-calling mechanism."""


def tool_format_block_for(native_tools: bool) -> str:
    return TOOL_FORMAT_BLOCK_NATIVE if native_tools else TOOL_FORMAT_BLOCK


FENCED_SYNTAX_BLOCK = f"""# Tools

## Fenced call syntax

⚠ The marker is `{_OPEN}` — THREE colons before `call` is canonical. `::call` (two) is tolerated and
still executes, but always prefer three.

Three categories of `{_OPEN}` blocks:

1) JSON tools — body is a JSON object with the arguments. This is the DEFAULT for EVERY tool
   except the two content/patch cases below (shell, grep_files, ls, find_files, lsp_*, poll, etc.):

    {_OPEN} read_files
    {{"path": "main.py"}}
    {_CLOSE}

    {_OPEN} shell
    {{"command": "pytest -q"}}
    {_CLOSE}

2) Content tools (write_file, create_file, create_docx) — path REQUIRED in the open header, body is
   raw content (no escaping needed, body can contain triple-backtick fences or tildes):

    {_OPEN} write_file path="src/main.py"
    print("hi")
    {_CLOSE}

3) patch_file — path REQUIRED, sections FIND/REPLACE / INSERT / delete_lines attribute:

    {_OPEN} patch_file path="src/main.py"
    --- FIND ---
    def old(): pass
    --- REPLACE ---
    def new(): return 42
    {_CLOSE}

   ONE change per patch_file call: EXACTLY ONE FIND section and ONE REPLACE section. Never repeat the
   REPLACE marker after the replacement text. The body ends at {_CLOSE}; no terminator marker is needed.
   Several edits in one file = several SEPARATE patch_file calls (emit them together in one reply)."""


TOOLS_LIST_BLOCK = """# Available tools

shell, read_files, write_file, patch_file, create_file, delete_file, rename_file, copy_file,
move_file, ls, tree, mkdir, rmdir, find_files, grep_files, poll, ssh, web_search, subagent,
skill, create_docx, docx_screenshot, lsp_definition, lsp_references, lsp_hover, lsp_diagnostics,
memory_write, memory_list, memory_read.

Each tool's arguments and behaviour are defined in its schema. Use exactly these names.

memory_write/memory_list/memory_read — persistent memory across sessions. Save with
memory_write ONLY facts NOT derivable from code/git/AGENTS.md: user role & preferences (type=user),
how-to-work feedback (type=feedback), current-work context (type=project), external references
(type=reference). Convert relative dates to absolute (YYYY-MM-DD).
scope: use scope="global" for facts NOT tied to one project (who the user is, their general
preferences & working style, universal references) — these are injected in EVERY project. Use
scope="project" (default) for context specific to the current project."""


LSP_TOOLS_BLOCK = r"""## LSP tools

Available when an LSP server is configured for the file's language (via `.data/lsp_servers.json`).
If none is configured, these tools return an error — fall back to grep_files.

For lsp_definition/lsp_references/lsp_hover: `line` is 1-based, `character` is the 0-based column of
the symbol. They return `path:line:column`. lsp_diagnostics output lines: `SEVERITY line:col
[source:code] message` (re-parses from disk, waits up to 4s)."""


TOOL_STRATEGY_BLOCK = r"""# Tool strategy

Never duplicate one call in both native and fenced forms.

Use LSP first for symbol questions:
- definition/implementation → `lsp_definition`
- callers/usages/delete safety → `lsp_references`
- signature/type/docs → `lsp_hover`
- post-edit code errors → `lsp_diagnostics`

Use `grep_files` for text only: string literals, comments, log/error messages, config keys, or patterns
you will feed into LSP. Fall back from LSP to grep/read only when LSP is unavailable or returns nothing."""


TOOL_STRATEGY_BLOCK_NATIVE = TOOL_STRATEGY_BLOCK.replace(
    "Never duplicate the same call in both native and fenced forms — pick one per call.\n\n",
    "",
)


def tool_strategy_block_for(native_tools: bool) -> str:
    return TOOL_STRATEGY_BLOCK_NATIVE if native_tools else TOOL_STRATEGY_BLOCK


TOOLS_REFERENCE_BLOCK = FENCED_SYNTAX_BLOCK + "\n\n" + TOOLS_LIST_BLOCK
LSP_BLOCK = LSP_TOOLS_BLOCK + "\n\n" + TOOL_STRATEGY_BLOCK