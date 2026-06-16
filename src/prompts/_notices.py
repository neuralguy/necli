CONTINUE_MESSAGE = (
    "Your previous reply was cut off by the token limit. Continue from where you stopped. "
    "Up to 50 tool calls per message. NEVER invent tool output — the system will send real "
    "results next. Keep text short. Reply in the user's language."
)

# Маркер, дописываемый в текст ответа ассистента, когда пользователь остановил
# выполнение (Ctrl+C — мягко или жёстко). Модель видит его в истории на
# следующем ходу и понимает, что её прервали, а не она сама завершила работу.
INTERRUPTED_NOTICE = "[Execution stopped by the user.]"

REPROMPT_SUFFIX = """IMPORTANT: You lost focus. Here is the plan you created earlier and have NOT finished:
{plan_context}
Continue with the remaining steps RIGHT NOW. Call the tools you need. Do NOT ask questions. Do NOT stop. Reply in the user's language."""

# Маркеры блока истории диалога, инжектируемого в первое сообщение / при
# продолжении (agent/messages.py, agent/loop.py).
CONVERSATION_CONTEXT_HEADER = "--- CONVERSATION CONTEXT ---"
CONVERSATION_CONTEXT_FOOTER = "--- END CONTEXT ---"

# Блок незавершённого плана из прошлой сессии (agent/messages.py).
# {plan} — текущий render_for_context() плана.
ACTIVE_PLAN_NOTICE = (
    "--- ACTIVE PLAN (from previous session) ---\n{plan}\n\n"
    "This plan was created in a previous session and is NOT finished. "
    "Continue executing the remaining steps. Update the plan as you go.\n"
    "--- END ACTIVE PLAN ---"
)

COMPRESS_PROMPT = """Compress the following dialog history between a user and an AI assistant (coding agent).

Compression rules:
- Preserve ALL essential information: user tasks, decisions made, architecture, names of modified files, key code fragments, unfinished tasks
- Preserve the current project state: what's done, what's in progress, what problems occurred and how they were solved
- Remove: repetitions, intermediate reasoning, tool calls (but keep their essence), boilerplate
- Format: structured summary, NOT a chat. Group by semantic blocks
- If there was an active plan — preserve its current state with step statuses
- Preserve the original language
- Be as compact as possible without losing important information

DIALOG HISTORY:"""

REFLECT_PROMPT = """TASK: Reflection on the current session.

Analyze all the work we have done in this session. The full history is already in the context — just re-read it.

Extract ONLY important lessons, rules, and project knowledge that will be useful when working with this project IN THE FUTURE. For example:
- Important architectural decisions and project patterns
- Code style and formatting rules
- Non-obvious mechanics, pitfalls, bugs
- Dependencies between components
- Naming conventions, file structure

DO NOT RECORD:
- Specific changes that were made (those are in git)
- Obvious things that are clear from the code
- Information already present in AGENTS.md

If there was nothing important in the session for future work — just say so and do NOT modify AGENTS.md. Do not add for the sake of adding.

If there is something to add — first read the current AGENTS.md via read_files, then append the new knowledge AT THE END of the file in a separate section. If a suitable section already exists — extend it. Use patch_file for appending, do not overwrite the whole file.

WRITING STYLE — NO FLUFF:
- Write as compactly as possible. One item = one point. No intros, preambles, "it's important to note", "worth considering"
- Don't recount the history "how it broke before" — write only the rule/fact/consequence
- Don't duplicate what's already in AGENTS.md phrased differently. First check existing sections — if the topic is already covered, extend the existing item, do not create a new one
- Combine related pitfalls into one section instead of separate small ones
- Drop the obvious: if the rule follows from a function/class name — do not record it
- Code examples — only if the rule is incomprehensible without them. Minimal, no captain-obvious comments
- Lists of files and APIs — only names + short purpose, no long descriptions

Write the AGENTS.md additions in English (the file is in English). The reflection message back to the user goes in the user's language."""

# Mode/THINK переключения теперь описываются в системном промте
# (prompts/_settings.py + system_prompt.build_system_prompt). В поток при
# смене на лету идут только короткие one-shot сигналы THINK_SWITCH_* /
# MODE_SWITCH_TO_* оттуда же. Полные нотисы здесь удалены, чтобы не было
# дублирующего источника правды.