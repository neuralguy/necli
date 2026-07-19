"""Анализ всего общения пользователя с агентом → подробный HTML-отчёт + память.

Команда /insights собирает ВСЕ сохранённые сессии (storage), считает локальные
метрики (сообщения, активные дни, инструменты, ошибки, часы активности, типы
сессий), строит транскрипт и просит модель (api_insights, чистый контекст)
вернуть развёрнутый JSON-разбор взаимодействия в стиле usage-аналитики:

  - at-a-glance (что работает / что мешает / быстрые победы / амбициозное);
  - чем пользователь занимается (project areas);
  - впечатляющие достижения, категории трения с примерами;
  - фичи necli, которые стоит попробовать, и новые паттерны использования;
  - что на горизонте; правки для AGENTS.md/памяти; забавный финал;
  - durable-факты для персистентной памяти (name/type/scope/body).

Отчёт рендерится в самостоятельный HTML (светлая Inter-тема, барные чарты,
copy-кнопки, навигация) и сохраняется в .data/insights/report-<ts>.html.
Текст генерируется на языке из настроек (config.i18n.get_lang()).

Модуль не роняет основной поток: ошибки пробрасываются в вызывающий UI.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
import time
from pathlib import Path

import session.storage as storage
from config.i18n import get_lang
from config.paths import BASE_DIR
from logger import logger

from .memdir import MEMORY_TYPES, format_manifest, write_memory

_REPORT_DIR = BASE_DIR / "insights"
_MAX_TRANSCRIPT_CHARS = 800_000
_MAX_MEMORY_ITEMS = 8
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_CALL_RE = re.compile(r":::call[ \t]+(\w+)", re.MULTILINE)
_DAY_SECONDS = 86_400

_LANG_NAMES = {
    "en": "English",
    "ru": "Russian (русский)",
    "de": "German (Deutsch)",
    "fr": "French (français)",
    "zh": "Chinese (中文)",
}

def _today() -> str:
    return _dt.date.today().isoformat()

# ── Сбор данных ──────────────────────────────────────────────────────────────

def _load_all_sessions() -> list[dict]:
    """Все сессии с загруженными сообщениями, новые первыми."""
    metas = storage.list_sessions(limit=0)
    out: list[dict] = []
    for meta in metas:
        sess = storage.load(meta["id"])
        if sess is None:
            continue
        out.append({"meta": meta, "session": sess})
    return out

def collect_metrics(loaded: list[dict]) -> dict:
    """Локальные (без модели) агрегаты по всем сессиям."""
    total_user = 0
    total_assistant = 0
    total_tool_results = 0
    tool_counts: dict[str, int] = {}
    error_kinds: dict[str, int] = {}
    error_hits = 0
    active_days: set[str] = set()
    first_ts: float | None = None
    last_ts: float | None = None
    user_msg_lengths: list[int] = []
    hour_counts: dict[int, int] = {}
    session_spans: list[tuple[float, float]] = []
    session_sizes: list[int] = []

    for item in loaded:
        sess = item["session"]
        s_first: float | None = None
        s_last: float | None = None
        s_user = 0
        for m in sess.messages:
            ts = float(m.timestamp or 0)
            if ts:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
                s_first = ts if s_first is None else min(s_first, ts)
                s_last = ts if s_last is None else max(s_last, ts)
                dt = _dt.datetime.fromtimestamp(ts)
                active_days.add(dt.date().isoformat())
            if m.role == "user":
                total_user += 1
                s_user += 1
                user_msg_lengths.append(len(m.content or ""))
                if ts:
                    h = _dt.datetime.fromtimestamp(ts).hour
                    hour_counts[h] = hour_counts.get(h, 0) + 1
            elif m.role == "assistant":
                total_assistant += 1
                for tool in _CALL_RE.findall(m.content or ""):
                    tool_counts[tool] = tool_counts.get(tool, 0) + 1
            elif m.role == "tool_result":
                total_tool_results += 1
                body = (m.content or "")
                low = body.lower()
                if "traceback" in low:
                    error_kinds["Traceback"] = error_kinds.get("Traceback", 0) + 1
                    error_hits += 1
                elif "command not found" in low or "no such file" in low:
                    error_kinds["Command/File not found"] = error_kinds.get("Command/File not found", 0) + 1
                    error_hits += 1
                elif "permission denied" in low:
                    error_kinds["Permission denied"] = error_kinds.get("Permission denied", 0) + 1
                    error_hits += 1
                elif "fragment not found" in low or "not found" in low:
                    error_kinds["Not found"] = error_kinds.get("Not found", 0) + 1
                    error_hits += 1
                elif "error" in low or "✗" in body:
                    error_kinds["Other error"] = error_kinds.get("Other error", 0) + 1
                    error_hits += 1
        if s_user:
            session_sizes.append(s_user)
        if s_first and s_last:
            session_spans.append((s_first, s_last))

    span_days = 0
    if first_ts and last_ts:
        span_days = int((last_ts - first_ts) // _DAY_SECONDS) + 1

    # Multi-сессии: пересечения по времени (параллельная работа).
    session_spans.sort()
    overlap_events = 0
    for i in range(1, len(session_spans)):
        if session_spans[i][0] <= session_spans[i - 1][1]:
            overlap_events += 1

    top_tools = sorted(tool_counts.items(), key=lambda kv: kv[1], reverse=True)
    top_errors = sorted(error_kinds.items(), key=lambda kv: kv[1], reverse=True)
    avg_user_len = round(sum(user_msg_lengths) / len(user_msg_lengths)) if user_msg_lengths else 0
    avg_session_size = round(sum(session_sizes) / len(session_sizes), 1) if session_sizes else 0
    msgs_per_day = round(total_user / len(active_days), 1) if active_days else 0

    periods = [("Morning (6-12)", range(6, 12)), ("Afternoon (12-18)", range(12, 18)),
               ("Evening (18-24)", range(18, 24)), ("Night (0-6)", range(6))]
    time_of_day = [(label, sum(hour_counts.get(h, 0) for h in rng)) for label, rng in periods]

    return {
        "total_sessions": len(loaded),
        "total_user": total_user,
        "total_assistant": total_assistant,
        "total_tool_results": total_tool_results,
        "total_tool_calls": sum(tool_counts.values()),
        "tool_counts": dict(top_tools),
        "top_tools": top_tools[:10],
        "error_kinds": top_errors[:8],
        "error_hits": error_hits,
        "active_days": len(active_days),
        "span_days": span_days,
        "avg_user_len": avg_user_len,
        "avg_session_size": avg_session_size,
        "msgs_per_day": msgs_per_day,
        "overlap_events": overlap_events,
        "time_of_day": time_of_day,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }

def build_transcript(loaded: list[dict]) -> str:
    """Транскрипт всех сессий для модели (свежие ближе к инструкции при усечении)."""
    blocks: list[str] = []
    for item in loaded:
        meta = item["meta"]
        sess = item["session"]
        header = f"### SESSION {meta.get('id', '')[:12]} — {meta.get('title', '') or '(no title)'}"
        lines = [header]
        for m in sess.messages:
            if m.role == "user":
                lines.append("USER: " + " ".join((m.content or "").split())[:1200])
            elif m.role == "assistant":
                txt = _CALL_RE.sub(r"[tool:\1]", m.content or "")
                txt = " ".join(txt.split())[:800]
                if txt:
                    lines.append("AGENT: " + txt)
            elif m.role == "tool_result":
                first = (m.content or "").strip().split("\n", 1)[0][:200]
                lines.append("RESULT: " + first)
        blocks.append("\n".join(lines))
    full = "\n\n".join(reversed(blocks))
    if len(full) > _MAX_TRANSCRIPT_CHARS:
        full = "…(old sessions truncated)…\n" + full[-_MAX_TRANSCRIPT_CHARS:]
    return full

# ── Промпт и парсинг ─────────────────────────────────────────────────────────

def build_prompt(transcript: str, metrics: dict, manifest: str, lang: str) -> str:
    types = ", ".join(MEMORY_TYPES)
    existing = manifest.strip() or "(no memories saved yet)"
    lang_name = _LANG_NAMES.get(lang, "English")
    metrics_json = json.dumps(
        {k: v for k, v in metrics.items() if k not in ("first_ts", "last_ts")},
        ensure_ascii=False,
    )
    return (
        "You are an analyst producing a rich, in-depth 'Interaction Insights' report "
        "about how a specific user collaborates with an AI coding agent called necli. "
        "You are given precomputed METRICS and a TRANSCRIPT of all their sessions. "
        "Produce a thorough, evidence-grounded analysis that helps make future "
        "interactions dramatically better, plus durable memory facts to persist.\n\n"
        f"WRITE EVERY HUMAN-READABLE STRING IN THIS LANGUAGE: {lang_name}. "
        "(Keep code snippets, tool names, file paths and the JSON keys themselves "
        "in their original form — only translate the prose values.)\n\n"
        "Return STRICT JSON (and NOTHING else) with EXACTLY this shape:\n"
        "{\n"
        '  "headline": "one vivid sentence characterizing the user as a collaborator",\n'
        '  "glance": {\n'
        '    "working": "2-3 sentences: what is working well overall",\n'
        '    "hindering": "2-3 sentences: what is hindering them (both sides)",\n'
        '    "quick_wins": "2-3 sentences: concrete quick wins to try",\n'
        '    "ambitious": "2-3 sentences: ambitious workflows now within reach"\n'
        "  },\n"
        '  "user_profile": "3-5 sentences: role, skill, communication style, language, pace",\n'
        '  "project_areas": [{"name": "area", "sessions": "~N sessions", "desc": "2-3 sentences"}],\n'
        '  "intents": [{"label": "Bug Fixing", "count": 12}],\n'
        '  "session_types": [{"label": "Single Task", "count": 9}],\n'
        '  "big_wins": [{"title": "short", "desc": "2-3 sentences of genuine achievement"}],\n'
        '  "friction": [{"title": "short", "desc": "1-2 sentences + how to reduce it", '
        '"examples": ["concrete example from transcript", ...]}],\n'
        '  "satisfaction": [{"label": "Likely Satisfied", "count": 30}],\n'
        '  "features_to_try": [{"title": "feature name", "oneliner": "what it does", '
        '"why": "why it fits THIS user", "example": "copy-paste prompt/snippet"}],\n'
        '  "usage_patterns": [{"title": "short", "summary": "1 sentence", '
        '"detail": "2-3 sentences grounded in metrics", "prompt": "copy-paste prompt"}],\n'
        '  "horizon": [{"title": "short", "possible": "2-3 sentences of what becomes possible", '
        '"tip": "how to get started", "prompt": "copy-paste prompt"}],\n'
        '  "agents_md": [{"text": "rule to add to AGENTS.md/system prompt", '
        '"why": "evidence from transcript"}],\n'
        '  "fun_ending": {"headline": "a funny/memorable quote-like highlight", '
        '"detail": "1-2 sentences of context"},\n'
        '  "memories": [{"name": "short-kebab-name", "type": "<type>", '
        '"scope": "global|project", "body": "one concise durable fact, optionally '
        'with **Why:** / **How to apply:**"}]\n'
        "}\n\n"
        "RULES:\n"
        "- Ground EVERYTHING in the actual transcript and metrics — cite concrete "
        "examples, real numbers, real quotes. No generic filler.\n"
        "- project_areas: 3-6 items. big_wins: 3 items. friction: 2-4 items with "
        "1-2 real examples each. features_to_try: 2-4. usage_patterns: 2-3. "
        "horizon: 2-3. agents_md: 3-6. intents/session_types/satisfaction: 3-6 "
        "rows each with integer counts inferred from the transcript.\n"
        f"- memory.type one of: {types}. memory.scope 'global' for who-the-user-is "
        "and general working-style facts, 'project' for project-specific context. "
        f"At most {_MAX_MEMORY_ITEMS} memory items, high-signal only.\n"
        "- Do NOT duplicate facts already in EXISTING MEMORIES; reuse exact 'name' "
        "to overwrite a changed one.\n"
        "- Output valid JSON only. No markdown fences, no commentary.\n\n"
        "EXISTING MEMORIES:\n" + existing + "\n\n"
        "METRICS:\n" + metrics_json + "\n\n"
        "--- TRANSCRIPT ---\n" + transcript + "\n--- END ---"
    )

def parse_analysis(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty model response")
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            raise ValueError("no JSON object in model response")  # noqa: B904
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("model response is not a JSON object")
    return data

# ── Запись памяти ────────────────────────────────────────────────────────────

def save_memories(analysis: dict, working_dir: str | None = None) -> int:
    items = analysis.get("memories") or []
    if not isinstance(items, list):
        return 0
    today = _today()
    saved = 0
    for item in items[:_MAX_MEMORY_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        body = str(item.get("body", "")).strip()
        mtype = str(item.get("type", "project")).strip() or "project"
        scope = str(item.get("scope", "project")).strip() or "project"
        if mtype not in MEMORY_TYPES:
            mtype = "project"
        if scope not in ("project", "global"):
            scope = "project"
        if not name or not body:
            continue
        try:
            write_memory(name, body, mtype=mtype, today=today,
                         working_dir=working_dir, scope=scope)
            saved += 1
        except Exception as e:
            logger.debug("insights: write memory '%s' failed: %s", name, e, exc_info=True)
    logger.info("insights: saved %d/%d memory fact(s)", saved, len(items))
    return saved

# ── HTML-рендер ──────────────────────────────────────────────────────────────

def _esc(v) -> str:
    return html.escape(str(v))

def _bars(pairs, color: str) -> str:
    """Барный чарт из [(label, count)]."""
    pairs = [(str(lbl), int(c)) for lbl, c in pairs if str(lbl).strip()]
    if not pairs:
        return '<p class="empty">—</p>'
    mx = max(c for _, c in pairs) or 1
    rows = []
    for lbl, c in pairs:
        w = c / mx * 100
        rows.append(
            f'<div class="bar-row"><div class="bar-label">{_esc(lbl)}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%;background:{color}"></div></div>'
            f'<div class="bar-value">{c}</div></div>'
        )
    return "".join(rows)

def render_html(analysis: dict, metrics: dict, saved_memories: int, lang: str) -> str:
    a = analysis
    generated = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    def span_str() -> str:
        f, last = metrics.get("first_ts"), metrics.get("last_ts")
        if not f or not last:
            return ""
        return (_dt.datetime.fromtimestamp(f).strftime("%Y-%m-%d") + " → "
                + _dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d"))

    glance = a.get("glance") or {}
    glance_html = ""
    for key, label in (("working", "What's working"), ("hindering", "What's hindering you"),
                       ("quick_wins", "Quick wins to try"), ("ambitious", "Ambitious workflows")):
        if glance.get(key):
            glance_html += (f'<div class="glance-section"><strong>{label}:</strong> '
                            f'{_esc(glance[key])}</div>')

    areas_html = "".join(
        f'<div class="project-area"><div class="area-header">'
        f'<span class="area-name">{_esc(x.get("name", ""))}</span>'
        f'<span class="area-count">{_esc(x.get("sessions", ""))}</span></div>'
        f'<div class="area-desc">{_esc(x.get("desc", ""))}</div></div>'
        for x in (a.get("project_areas") or []) if isinstance(x, dict)
    ) or '<p class="empty">—</p>'

    wins_html = "".join(
        f'<div class="big-win"><div class="big-win-title">{_esc(x.get("title", ""))}</div>'
        f'<div class="big-win-desc">{_esc(x.get("desc", ""))}</div></div>'
        for x in (a.get("big_wins") or []) if isinstance(x, dict)
    ) or '<p class="empty">—</p>'

    friction_html = ""
    for x in (a.get("friction") or []):
        if not isinstance(x, dict):
            continue
        ex = "".join(f"<li>{_esc(e)}</li>" for e in (x.get("examples") or []) if str(e).strip())
        ex_block = f'<ul class="friction-examples">{ex}</ul>' if ex else ""
        friction_html += (
            f'<div class="friction-category"><div class="friction-title">{_esc(x.get("title", ""))}</div>'
            f'<div class="friction-desc">{_esc(x.get("desc", ""))}</div>{ex_block}</div>'
        )
    friction_html = friction_html or '<p class="empty">—</p>'

    features_html = ""
    for x in (a.get("features_to_try") or []):
        if not isinstance(x, dict):
            continue
        ex = x.get("example", "")
        ex_block = (f'<div class="feature-code"><code>{_esc(ex)}</code>'
                    f'<button class="copy-btn" onclick="copyText(this)">Copy</button></div>') if ex else ""
        features_html += (
            f'<div class="feature-card"><div class="feature-title">{_esc(x.get("title", ""))}</div>'
            f'<div class="feature-oneliner">{_esc(x.get("oneliner", ""))}</div>'
            f'<div class="feature-why"><strong>Why for you:</strong> {_esc(x.get("why", ""))}</div>'
            f'{ex_block}</div>'
        )
    features_html = features_html or '<p class="empty">—</p>'

    patterns_html = ""
    for x in (a.get("usage_patterns") or []):
        if not isinstance(x, dict):
            continue
        p = x.get("prompt", "")
        p_block = (f'<div class="pattern-prompt"><div class="prompt-label">Paste into necli:</div>'
                   f'<code>{_esc(p)}</code>'
                   f'<button class="copy-btn" onclick="copyText(this)">Copy</button></div>') if p else ""
        patterns_html += (
            f'<div class="pattern-card"><div class="pattern-title">{_esc(x.get("title", ""))}</div>'
            f'<div class="pattern-summary">{_esc(x.get("summary", ""))}</div>'
            f'<div class="pattern-detail">{_esc(x.get("detail", ""))}</div>{p_block}</div>'
        )
    patterns_html = patterns_html or '<p class="empty">—</p>'

    horizon_html = ""
    for x in (a.get("horizon") or []):
        if not isinstance(x, dict):
            continue
        p = x.get("prompt", "")
        p_block = (f'<div class="pattern-prompt"><div class="prompt-label">Paste into necli:</div>'
                   f'<code>{_esc(p)}</code>'
                   f'<button class="copy-btn" onclick="copyText(this)">Copy</button></div>') if p else ""
        tip = f'<div class="horizon-tip"><strong>Getting started:</strong> {_esc(x.get("tip", ""))}</div>' if x.get("tip") else ""
        horizon_html += (
            f'<div class="horizon-card"><div class="horizon-title">{_esc(x.get("title", ""))}</div>'
            f'<div class="horizon-possible">{_esc(x.get("possible", ""))}</div>{tip}{p_block}</div>'
        )
    horizon_html = horizon_html or '<p class="empty">—</p>'

    agents_items = ""
    for i, x in enumerate(a.get("agents_md") or []):
        if not isinstance(x, dict):
            continue
        txt = x.get("text", "")
        why = f'<div class="cmd-why">{_esc(x.get("why", ""))}</div>' if x.get("why") else ""
        agents_items += (
            f'<div class="claude-md-item">'
            f'<input type="checkbox" id="cmd-{i}" class="cmd-checkbox" checked data-text="{_esc(txt)}">'
            f'<label for="cmd-{i}"><code class="cmd-code">{_esc(txt)}</code>'
            f'<button class="copy-btn" onclick="copyCmdItem({i})">Copy</button></label>{why}</div>'
        )
    agents_items = agents_items or '<p class="empty">—</p>'

    fun = a.get("fun_ending") or {}
    fun_html = ""
    if fun.get("headline"):
        fun_html = (f'<div class="fun-ending"><div class="fun-headline">"{_esc(fun.get("headline"))}"</div>'
                    f'<div class="fun-detail">{_esc(fun.get("detail", ""))}</div></div>')

    intents_bars = _bars([(x.get("label"), x.get("count")) for x in (a.get("intents") or []) if isinstance(x, dict)], "#2563eb")
    sess_bars = _bars([(x.get("label"), x.get("count")) for x in (a.get("session_types") or []) if isinstance(x, dict)], "#8b5cf6")
    sat_bars = _bars([(x.get("label"), x.get("count")) for x in (a.get("satisfaction") or []) if isinstance(x, dict)], "#eab308")
    tools_bars = _bars(metrics.get("top_tools", []), "#0891b2")
    err_bars = _bars(metrics.get("error_kinds", []), "#dc2626")
    tod_bars = _bars(metrics.get("time_of_day", []), "#8b5cf6")

    overlap = metrics.get("overlap_events", 0)

    return f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>necli · Interaction Insights</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; background:#f8fafc; color:#334155; line-height:1.65; padding:48px 24px; }}
  .container {{ max-width:820px; margin:0 auto; }}
  h1 {{ font-size:32px; font-weight:700; color:#0f172a; margin-bottom:8px; }}
  h2 {{ font-size:20px; font-weight:600; color:#0f172a; margin-top:48px; margin-bottom:16px; }}
  .subtitle {{ color:#64748b; font-size:15px; margin-bottom:24px; }}
  .nav-toc {{ display:flex; flex-wrap:wrap; gap:8px; margin:24px 0 32px; padding:16px; background:#fff; border-radius:8px; border:1px solid #e2e8f0; }}
  .nav-toc a {{ font-size:12px; color:#64748b; text-decoration:none; padding:6px 12px; border-radius:6px; background:#f1f5f9; transition:all .15s; }}
  .nav-toc a:hover {{ background:#e2e8f0; color:#334155; }}
  .stats-row {{ display:flex; gap:24px; margin-bottom:40px; padding:20px 0; border-top:1px solid #e2e8f0; border-bottom:1px solid #e2e8f0; flex-wrap:wrap; }}
  .stat {{ text-align:center; }}
  .stat-value {{ font-size:24px; font-weight:700; color:#0f172a; }}
  .stat-label {{ font-size:11px; color:#64748b; text-transform:uppercase; }}
  .at-a-glance {{ background:linear-gradient(135deg,#fef3c7,#fde68a); border:1px solid #f59e0b; border-radius:12px; padding:20px 24px; margin-bottom:32px; }}
  .glance-title {{ font-size:16px; font-weight:700; color:#92400e; margin-bottom:16px; }}
  .glance-sections {{ display:flex; flex-direction:column; gap:12px; }}
  .glance-section {{ font-size:14px; color:#78350f; line-height:1.6; }}
  .glance-section strong {{ color:#92400e; }}
  .project-areas {{ display:flex; flex-direction:column; gap:12px; margin-bottom:32px; }}
  .project-area {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:16px; }}
  .area-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
  .area-name {{ font-weight:600; font-size:15px; color:#0f172a; }}
  .area-count {{ font-size:12px; color:#64748b; background:#f1f5f9; padding:2px 8px; border-radius:4px; }}
  .area-desc {{ font-size:14px; color:#475569; line-height:1.5; }}
  .section-intro {{ font-size:14px; color:#64748b; margin-bottom:16px; }}
  .big-wins {{ display:flex; flex-direction:column; gap:12px; margin-bottom:24px; }}
  .big-win {{ background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:16px; }}
  .big-win-title {{ font-weight:600; font-size:15px; color:#166534; margin-bottom:8px; }}
  .big-win-desc {{ font-size:14px; color:#15803d; line-height:1.5; }}
  .friction-categories {{ display:flex; flex-direction:column; gap:16px; margin-bottom:24px; }}
  .friction-category {{ background:#fef2f2; border:1px solid #fca5a5; border-radius:8px; padding:16px; }}
  .friction-title {{ font-weight:600; font-size:15px; color:#991b1b; margin-bottom:6px; }}
  .friction-desc {{ font-size:13px; color:#7f1d1d; margin-bottom:10px; }}
  .friction-examples {{ margin:0 0 0 20px; font-size:13px; color:#334155; }}
  .friction-examples li {{ margin-bottom:4px; }}
  .claude-md-section {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:16px; margin-bottom:20px; }}
  .claude-md-section h3 {{ font-size:14px; font-weight:600; color:#1e40af; margin:0 0 12px; }}
  .claude-md-actions {{ margin-bottom:12px; padding-bottom:12px; border-bottom:1px solid #dbeafe; }}
  .copy-all-btn {{ background:#2563eb; color:#fff; border:none; border-radius:4px; padding:6px 12px; font-size:12px; cursor:pointer; font-weight:500; }}
  .copy-all-btn:hover {{ background:#1d4ed8; }}
  .copy-all-btn.copied {{ background:#16a34a; }}
  .claude-md-item {{ display:flex; flex-wrap:wrap; align-items:flex-start; gap:8px; padding:10px 0; border-bottom:1px solid #dbeafe; }}
  .claude-md-item:last-child {{ border-bottom:none; }}
  .cmd-checkbox {{ margin-top:4px; }}
  .cmd-code {{ background:#fff; padding:8px 12px; border-radius:4px; font-size:12px; color:#1e40af; border:1px solid #bfdbfe; font-family:monospace; display:inline-block; white-space:pre-wrap; word-break:break-word; }}
  .cmd-why {{ font-size:12px; color:#64748b; width:100%; padding-left:24px; margin-top:4px; }}
  .features-section, .patterns-section {{ display:flex; flex-direction:column; gap:12px; margin:16px 0; }}
  .feature-card {{ background:#f0fdf4; border:1px solid #86efac; border-radius:8px; padding:16px; }}
  .pattern-card {{ background:#f0f9ff; border:1px solid #7dd3fc; border-radius:8px; padding:16px; }}
  .feature-title, .pattern-title {{ font-weight:600; font-size:15px; color:#0f172a; margin-bottom:6px; }}
  .feature-oneliner, .pattern-summary {{ font-size:14px; color:#475569; margin-bottom:8px; }}
  .feature-why, .pattern-detail {{ font-size:13px; color:#334155; line-height:1.5; }}
  .feature-code, .pattern-prompt {{ background:#f8fafc; padding:12px; border-radius:6px; margin-top:12px; border:1px solid #e2e8f0; display:flex; align-items:flex-start; gap:8px; flex-wrap:wrap; }}
  .feature-code code, .pattern-prompt code {{ flex:1; font-family:monospace; font-size:12px; color:#334155; white-space:pre-wrap; word-break:break-word; }}
  .prompt-label {{ font-size:11px; font-weight:600; text-transform:uppercase; color:#64748b; margin-bottom:6px; width:100%; }}
  .copy-btn {{ background:#e2e8f0; border:none; border-radius:4px; padding:4px 8px; font-size:11px; cursor:pointer; color:#475569; flex-shrink:0; }}
  .copy-btn:hover {{ background:#cbd5e1; }}
  .charts-row {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; margin:24px 0; }}
  .chart-card {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:16px; }}
  .chart-title {{ font-size:12px; font-weight:600; color:#64748b; text-transform:uppercase; margin-bottom:12px; }}
  .bar-row {{ display:flex; align-items:center; margin-bottom:6px; }}
  .bar-label {{ width:120px; font-size:11px; color:#475569; flex-shrink:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .bar-track {{ flex:1; height:6px; background:#f1f5f9; border-radius:3px; margin:0 8px; }}
  .bar-fill {{ height:100%; border-radius:3px; }}
  .bar-value {{ width:40px; font-size:11px; font-weight:500; color:#64748b; text-align:right; }}
  .empty {{ color:#94a3b8; font-size:13px; }}
  .horizon-section {{ display:flex; flex-direction:column; gap:16px; }}
  .horizon-card {{ background:linear-gradient(135deg,#faf5ff,#f5f3ff); border:1px solid #c4b5fd; border-radius:8px; padding:16px; }}
  .horizon-title {{ font-weight:600; font-size:15px; color:#5b21b6; margin-bottom:8px; }}
  .horizon-possible {{ font-size:14px; color:#334155; margin-bottom:10px; line-height:1.5; }}
  .horizon-tip {{ font-size:13px; color:#6b21a8; background:rgba(255,255,255,.6); padding:8px 12px; border-radius:4px; margin-bottom:10px; }}
  .fun-ending {{ background:linear-gradient(135deg,#fef3c7,#fde68a); border:1px solid #fbbf24; border-radius:12px; padding:24px; margin-top:40px; text-align:center; }}
  .fun-headline {{ font-size:18px; font-weight:600; color:#78350f; margin-bottom:8px; }}
  .fun-detail {{ font-size:14px; color:#92400e; }}
  .narrative {{ background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:20px; margin-bottom:24px; font-size:14px; color:#475569; }}
  @media (max-width:640px) {{ .charts-row {{ grid-template-columns:1fr; }} .stats-row {{ justify-content:center; }} }}
</style>
</head>
<body>
  <div class="container">
    <h1>necli · Interaction Insights</h1>
    <p class="subtitle">{metrics.get('total_user', 0)} user messages across {metrics.get('total_sessions', 0)} sessions · {_esc(span_str())} · generated {generated}</p>

    <div class="at-a-glance">
      <div class="glance-title">At a Glance</div>
      <div class="glance-sections">{glance_html or '<p class="empty">—</p>'}</div>
    </div>

    <nav class="nav-toc">
      <a href="#section-work">What You Work On</a>
      <a href="#section-usage">How You Use necli</a>
      <a href="#section-wins">Impressive Things</a>
      <a href="#section-friction">Where Things Go Wrong</a>
      <a href="#section-features">Features to Try</a>
      <a href="#section-patterns">New Usage Patterns</a>
      <a href="#section-horizon">On the Horizon</a>
      <a href="#section-agents">AGENTS.md Additions</a>
    </nav>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{metrics.get('total_user', 0)}</div><div class="stat-label">Messages</div></div>
      <div class="stat"><div class="stat-value">{metrics.get('total_sessions', 0)}</div><div class="stat-label">Sessions</div></div>
      <div class="stat"><div class="stat-value">{metrics.get('total_tool_calls', 0)}</div><div class="stat-label">Tool Calls</div></div>
      <div class="stat"><div class="stat-value">{metrics.get('active_days', 0)}</div><div class="stat-label">Active Days</div></div>
      <div class="stat"><div class="stat-value">{metrics.get('msgs_per_day', 0)}</div><div class="stat-label">Msgs/Day</div></div>
      <div class="stat"><div class="stat-value">{metrics.get('avg_session_size', 0)}</div><div class="stat-label">Msgs/Session</div></div>
    </div>

    <h2 id="section-work">What You Work On</h2>
    <div class="project-areas">{areas_html}</div>

    <h2 id="section-usage">How You Use necli</h2>
    <div class="charts-row">
      <div class="chart-card"><div class="chart-title">What You Wanted</div>{intents_bars}</div>
      <div class="chart-card"><div class="chart-title">Top Tools Used</div>{tools_bars}</div>
    </div>
    <div class="charts-row">
      <div class="chart-card"><div class="chart-title">Session Types</div>{sess_bars}</div>
      <div class="chart-card"><div class="chart-title">User Messages by Time of Day</div>{tod_bars}</div>
    </div>
    <div class="charts-row">
      <div class="chart-card"><div class="chart-title">Tool Errors Encountered</div>{err_bars}</div>
      <div class="chart-card"><div class="chart-title">Inferred Satisfaction (model-estimated)</div>{sat_bars}</div>
    </div>
    <div class="chart-card" style="margin:24px 0;">
      <div class="chart-title">Multi-tasking (Overlapping Sessions)</div>
      <p style="font-size:13px; color:#475569;">{overlap} time-overlap event(s) detected across sessions — periods where you ran necli on more than one task in the same window.</p>
    </div>

    <h2 id="section-wins">Impressive Things You Did</h2>
    <div class="big-wins">{wins_html}</div>

    <h2 id="section-friction">Where Things Go Wrong</h2>
    <div class="friction-categories">{friction_html}</div>

    <h2 id="section-features">necli Features to Try</h2>
    <div class="features-section">{features_html}</div>

    <h2 id="section-patterns">New Ways to Use necli</h2>
    <div class="patterns-section">{patterns_html}</div>

    <h2 id="section-horizon">On the Horizon</h2>
    <div class="horizon-section">{horizon_html}</div>

    <h2 id="section-agents">Suggested AGENTS.md / Memory Additions</h2>
    <div class="claude-md-section">
      <h3>Copy these into your AGENTS.md or memory</h3>
      <div class="claude-md-actions"><button class="copy-all-btn" onclick="copyAllChecked()">Copy All Checked</button></div>
      {agents_items}
    </div>
    <p class="subtitle" style="margin-top:12px;">{saved_memories} memory fact(s) written to persistent memory.</p>

    {fun_html}
  </div>
  <script>
    function copyText(btn) {{
      const code = btn.parentElement.querySelector('code');
      navigator.clipboard.writeText(code.textContent).then(() => {{
        btn.textContent = 'Copied!'; setTimeout(() => {{ btn.textContent = 'Copy'; }}, 2000);
      }});
    }}
    function copyCmdItem(idx) {{
      const cb = document.getElementById('cmd-' + idx);
      if (!cb) return;
      navigator.clipboard.writeText(cb.dataset.text || '').then(() => {{
        const btn = cb.parentElement.querySelector('.copy-btn');
        if (btn) {{ btn.textContent = 'Copied!'; setTimeout(() => {{ btn.textContent = 'Copy'; }}, 2000); }}
      }});
    }}
    function copyAllChecked() {{
      const boxes = document.querySelectorAll('.cmd-checkbox:checked');
      const texts = [];
      boxes.forEach(cb => {{ if (cb.dataset.text) texts.push(cb.dataset.text); }});
      const btn = document.querySelector('.copy-all-btn');
      navigator.clipboard.writeText(texts.join('\\n')).then(() => {{
        if (btn) {{ btn.textContent = 'Copied ' + texts.length + ' items!'; btn.classList.add('copied');
          setTimeout(() => {{ btn.textContent = 'Copy All Checked'; btn.classList.remove('copied'); }}, 2000); }}
      }});
    }}
  </script>
</body>
</html>
"""

def write_report(html_text: str) -> Path:
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = _REPORT_DIR / f"report-{ts}.html"
    path.write_text(html_text, encoding="utf-8")
    return path

# ── Оркестрация ──────────────────────────────────────────────────────────────

async def generate_insights(working_dir: str | None = None, *, persist_memory: bool = True) -> dict:
    """Полный цикл: собрать → проанализировать моделью → отрендерить → записать.

    Возвращает dict: {report_path, metrics, analysis, saved_memories}.
    Бросает наружу при отсутствии данных или ошибке вызова модели — UI покажет.
    """
    from apis.agent_adapter import api_insights

    t0 = time.monotonic()
    loaded = _load_all_sessions()
    if not loaded:
        raise RuntimeError("no sessions to analyze")

    lang = get_lang()
    metrics = collect_metrics(loaded)
    transcript = build_transcript(loaded)
    manifest = format_manifest(working_dir)
    prompt = build_prompt(transcript, metrics, manifest, lang)

    logger.info(
        "insights: %d sessions, transcript=%d chars, lang=%s",
        metrics["total_sessions"], len(transcript), lang,
    )
    raw = await api_insights(prompt)
    analysis = parse_analysis(raw)

    saved = save_memories(analysis, working_dir) if persist_memory else 0
    html_text = render_html(analysis, metrics, saved, lang)
    path = write_report(html_text)

    logger.info(
        "insights: report=%s saved_memories=%d in %.1fs",
        path, saved, time.monotonic() - t0,
    )
    return {
        "report_path": path,
        "metrics": metrics,
        "analysis": analysis,
        "saved_memories": saved,
    }
