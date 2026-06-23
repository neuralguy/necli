WEB_SEARCH_BLOCK = r"""# Web search

You HAVE internet via `web_search` — never refuse a real-time question citing "no access" or "training
cutoff". Use it for anything newer than your cutoff or not derivable from the working dir: current
prices/rates, today's news/dates/weather, recent library versions/changelogs, exact API/SDK docs, any
"today/current/latest" question.

- Search: `{"query": "USD to RUB rate today", "max_results": 5}`
- Fetch:  `{"url": "https://example.com/article"}` — extracts page text.
Pipeline: search first; if snippets aren't enough, fetch the top URL(s) for full text."""


DOCX_BLOCK = """# DOCX files

For ANY .docx work (read/create/edit) you MUST FIRST load the `docx-mastery` skill (call the skill tool
with {"name": "docx-mastery"}) — it has the full guide (create_docx usage, styles, screenshot check,
pitfalls). Do not touch a .docx without loading it."""


def docx_block_for(native_tools: bool) -> str:
    return DOCX_BLOCK