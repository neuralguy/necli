_OPEN = ":::call"
_CLOSE = "call:::"


# ── THINK ───────────────────────────────────────────────────────────
# Включается в системный промт, только когда think_enabled=True.
# Выключенное состояние = отсутствие блока (никаких "THINK disabled").
THINK_BLOCK = f"""# Think format

Think out loud before acting. This works on top of ANY mode (agent/planning)
and does not override its rules.

`think` is a regular tool: in native function-calling mode CALL IT as a function
(argument: thought); in fenced mode use the {_OPEN} think ... {_CLOSE} block. Either
way it does NOT execute code — it only displays your reasoning in the UI.

RULE: before ANY tool calls (including the `plan` tool) and before the final reply
you MUST emit EXACTLY ONE `think` (one function call, or one {_OPEN} think block) with detailed reasoning.

FORMAT — fenced, JSON in the body, field "thought":

    {_OPEN} think
    {{"thought": "The user asks for X. Y depends on Z. I'll grep call sites first, then choose between lru_cache and a manual dict."}}
    {_CLOSE}

STRICT RULES:
- EXACTLY ONE think block per response. Never two or more.
- One long "thought" string covering ALL reasoning: hypotheses, breakdown, options, conclusions.
- The think block comes BEFORE any regular text and tool calls.
- Do NOT put reasoning in regular text — only inside the single think block.
- After a tool result — write ONE new think block before next actions (if needed).
- The FINAL reply to the user — WITHOUT think blocks, only the result, in the user's language."""


# Native-вариант THINK: только function-calling, без :::call think.
THINK_BLOCK_NATIVE = """# Think format

Think out loud before acting. This works on top of ANY mode (agent/planning)
and does not override its rules.

`think` is a regular tool — CALL IT as a function with one argument "thought". It does NOT execute
code; it only displays your reasoning in the UI.

RULE: before ANY tool calls (including the `plan` tool) and before the final reply
you MUST emit EXACTLY ONE `think` call with detailed reasoning.

STRICT RULES:
- EXACTLY ONE think call per response. Never two or more.
- One long "thought" string covering ALL reasoning: hypotheses, breakdown, options, conclusions.
- The think call comes BEFORE any regular text and other tool calls.
- Do NOT put reasoning in regular text — only inside the single think call.
- After a tool result — make ONE new think call before next actions (if needed).
- The FINAL reply to the user — WITHOUT think, only the result, in the user's language."""


def think_block_for(native_tools: bool) -> str:
    return THINK_BLOCK_NATIVE if native_tools else THINK_BLOCK


# ── TOOL FORMAT (text mode) ─────────────────────────────────────────
# Включается в промт, только когда нативный function calling недоступен
# (tool_format=text). В native-режиме этот блок не нужен — S0 уже описывает
# fenced+native, и инструменты забиндены.
TOOL_FORMAT_TEXT_BLOCK = f"""# Tool call format: text mode

Native function calling is OFF. Call tools ONLY via {_OPEN} <tool> ... {_CLOSE} blocks.
Open line STARTS with three colons; close line is bare {_CLOSE}. Markers are asymmetric.

1) JSON tools — JSON body:

    {_OPEN} read_files
    {{"path": "main.py"}}
    {_CLOSE}

2) Content tools (create_file, create_docx) — path in header, raw body
   (create_file creates or fully overwrites):

    {_OPEN} create_file path="src/x.py"
    print("hi")
    {_CLOSE}

3) patch_file — FIND/REPLACE or INSERT sections:

    {_OPEN} patch_file path="a.py"
    --- FIND ---
    old
    --- REPLACE ---
    new
    {_CLOSE}

Use EXACTLY ONE FIND section and ONE REPLACE section per patch change — never repeat the REPLACE
marker after the replacement text. The body ends at {_CLOSE}; no terminator marker is needed.
Every block MUST close with {_CLOSE}. An unclosed block = the tool won't run."""


# ── One-shot сигналы в поток (только при переключении) ───────────────
# Компактные. Полное описание — в системном промте (блоки выше / mode-блоки).
THINK_SWITCH_ON = (
    "[SYSTEM: THINK format ON now — see the THINK FORMAT section in the system "
    "prompt. Start emitting a single think step before acting.]"
)
THINK_SWITCH_OFF = (
    "[SYSTEM: THINK format OFF now — stop emitting think steps, reply as usual.]"
)
MODE_SWITCH_TO_PLANNING = (
    "[SYSTEM: Switched to PLANNING mode — read-only tools only, write/execute blocked. "
    "See the PLANNING MODE section in the system prompt.]"
)
MODE_SWITCH_TO_AGENT = (
    "[SYSTEM: Switched to AGENT mode — all tools available again. "
    "See the AGENT MODE section in the system prompt.]"
)
MODE_SWITCH_TO_AUTONOMOUS = (
    "[SYSTEM: Switched to AUTONOMOUS mode — orchestrator-only long-running mode. "
    "Delegate implementation/debugging/testing/verification to subagents; see the AUTONOMOUS MODE section.]"
)
