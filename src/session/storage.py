"""Сохранение, загрузка и листинг сессий — site-aware."""

import json
import time
from typing import Optional

import config
import models as app_models
from logger import logger
from session.message import Message
from session.session import Session
from session._time import format_msk, format_msk_short


def _recalc_model_cost(model: str, mdata: dict) -> float:
    """Пересчитывает total_cost для модели по сохранённым токенам и
    текущим ценам из MODEL_PRICING / API pricing.

    Это нужно потому что summary.json хранит зафиксированные cost на момент
    сохранения, а pricing в каталоге мог измениться (или появиться позже).
    """
    price_in, price_out = app_models.get_pricing(model)
    inp = mdata.get("input_tokens", 0)
    out = mdata.get("output_tokens", 0)
    return inp * price_in / 1_000_000 + out * price_out / 1_000_000


def _compressed_total_cost(data: dict) -> float:
    stats = data.get("compressed_stats") or {}
    if not isinstance(stats, dict):
        return 0.0
    try:
        return float(stats.get("total_cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def save(session: Session):
    session.ensure_dir()
    try:
        _save_history(session)
        _save_summary(session)
        try:
            from session.notes import save_session_notes
            save_session_notes(session)
        except Exception:
            logger.debug("session notes save failed", exc_info=True)
        logger.debug(
            "session.save: {} messages={} cost=${:.4f}",
            session.id[:16], len(session.messages), session.total_cost,
        )
    except Exception as e:
        logger.opt(exception=True).error("session.save failed for {}: {}", session.id, e)
        raise


def _save_history(session: Session):
    data = {
        "id": session.id,
        "title": session.title,
        "site": session.site,
        "chat_url": session.chat_url,
        "created_at": session.created_at,
        "created": format_msk(session.created_at),
        "updated_at": session.updated_at,
        "updated": format_msk(session.updated_at),
        "messages": [m.to_dict() for m in session.messages],
        "compressed_stats": session._compressed_stats,
    }
    # Branches: _branch_alternatives хранит первые сообщения альтернативных
    # веток, _branch_tails хранит цепочки сообщений каждой ветки.
    alts = getattr(session, "_branch_alternatives", {}) or {}
    tails = getattr(session, "_branch_tails", {}) or {}
    if alts:
        data["branch_alternatives"] = {
            parent_id: [m.to_dict() for m in msgs]
            for parent_id, msgs in alts.items()
        }
    if tails:
        data["branch_tails"] = {
            head_id: [m.to_dict() for m in chain]
            for head_id, chain in tails.items()
        }
    pre = getattr(session, "_pre_compress_messages", None)
    if pre:
        data["pre_compress_messages"] = pre
        data["pre_compress_at"] = getattr(session, "_pre_compress_at", None)
    path = session.dir / "history.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_summary(session: Session):
    path = session.dir / "summary.json"
    path.write_text(
        json.dumps(session.summary(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load(session_id: str) -> Optional[Session]:
    session_dir = config.SESSIONS_DIR / session_id
    if session_dir.exists():
        return _load_from_dir(session_dir)

    matches = []
    for d in config.SESSIONS_DIR.iterdir():
        if d.is_dir() and d.name.startswith(session_id):
            matches.append(d)
    if len(matches) == 1:
        return _load_from_dir(matches[0])

    if not matches:
        for d in config.SESSIONS_DIR.iterdir():
            if d.is_dir() and session_id in d.name:
                matches.append(d)
        if len(matches) == 1:
            return _load_from_dir(matches[0])

    return None


def _load_from_dir(session_dir) -> Optional[Session]:
    history_path = session_dir / "history.json"
    if not history_path.exists():
        logger.warning("session.load: no history.json in {}", session_dir)
        return None
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("session.load: parse error in {}: {}", history_path, e)
        return None

    session = Session(
        session_id=data.get("id", session_dir.name),
        site=data.get("site", ""),
    )
    session.title = data.get("title", "")
    session.created_at = data.get("created_at", time.time())
    session.updated_at = data.get("updated_at", session.created_at)
    session.chat_url = data.get("chat_url", "")
    session.messages = [Message.from_dict(m) for m in data.get("messages", [])]
    session._compressed_stats = data.get("compressed_stats")

    # Branches
    alts_raw = data.get("branch_alternatives") or {}
    if isinstance(alts_raw, dict):
        session._branch_alternatives = {
            parent_id: [Message.from_dict(m) for m in msgs]
            for parent_id, msgs in alts_raw.items()
            if isinstance(msgs, list)
        }
    tails_raw = data.get("branch_tails") or {}
    if isinstance(tails_raw, dict):
        session._branch_tails = {
            head_id: [Message.from_dict(m) for m in chain]
            for head_id, chain in tails_raw.items()
            if isinstance(chain, list)
        }

    pre = data.get("pre_compress_messages")
    if pre:
        session._pre_compress_messages = pre
        session._pre_compress_at = data.get("pre_compress_at")
    return session


def list_sessions(limit: int = 20) -> list[dict]:
    sessions = []
    if not config.SESSIONS_DIR.exists():
        return sessions
    for session_dir in config.SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        info = _read_summary(session_dir)
        if not info:
            info = _read_summary_from_history(session_dir)
        if info:
            sessions.append(info)
    sessions.sort(key=lambda s: s.get("updated_at", s.get("created_at", 0)), reverse=True)
    if limit > 0:
        return sessions[:limit]
    return sessions


def _get_context_tokens(summary_data: dict, session_dir) -> int:
    ctx = summary_data.get("context_tokens")
    if ctx is not None:
        return ctx
    history_path = session_dir / "history.json"
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            return sum(m.get("tokens", 0) for m in data.get("messages", []))
        except Exception:
            logger.debug("_get_context_tokens: read {} failed", history_path, exc_info=True)
    return summary_data.get("total_tokens", 0)


def _preview_title(content: str) -> str:
    content = " ".join(str(content).split())
    if not content:
        return ""
    return content[:237] + "..." if len(content) > 240 else content


def _first_user_message_title(msgs: list, fallback: str) -> str:
    for msg in msgs:
        if msg.get("role") == "user":
            content = _preview_title(msg.get("content", ""))
            if content:
                return content
    return fallback


def _read_summary(session_dir) -> Optional[dict]:
    summary_path = session_dir / "summary.json"
    if not summary_path.exists():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        created = data.get("created_at", 0)
        updated = data.get("updated_at", created)
        title = data.get("title", "")

        return {
            "id": data.get("id", session_dir.name),
            "title": title,
            "site": data.get("site", ""),
            "created_at": created,
            "created": data.get("created", format_msk_short(created)),
            "updated_at": updated,
            "updated": data.get("updated", format_msk_short(updated)),
            "messages": data.get("messages", 0),
            "tokens": _get_context_tokens(data, session_dir),
            "cost": _recalc_summary_total_cost(data),
            "models": data.get("models", []),
            "last_model": data.get("last_model", ""),
        }
    except Exception:
        logger.debug("_read_summary: parse {} failed", summary_path, exc_info=True)
        return None


def _recalc_summary_total_cost(data: dict) -> float:
    cbm = data.get("cost_by_model") or {}
    if not cbm:
        return float(data.get("total_cost", 0.0) or 0.0)
    total = _compressed_total_cost(data)
    for model, mdata in cbm.items():
        if model in ("unknown", ""):
            continue
        total += _recalc_model_cost(model, mdata)
    return total


def _read_summary_from_history(session_dir) -> Optional[dict]:
    history_path = session_dir / "history.json"
    if not history_path.exists():
        return None
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        msgs = data.get("messages", [])
        msg_count = sum(1 for m in msgs if m.get("role") == "user")
        total_tokens = sum(m.get("tokens", 0) for m in msgs)

        models = []
        seen = set()
        for m in msgs:
            mdl = m.get("model", "")
            if mdl and mdl not in seen and mdl != "unknown":
                seen.add(mdl)
                models.append(mdl)

        created = data.get("created_at", 0)
        updated = data.get("updated_at", created)

        return {
            "id": data.get("id", session_dir.name),
            "title": _first_user_message_title(msgs, data.get("title", "")),
            "site": data.get("site", ""),
            "created_at": created,
            "created": format_msk_short(created) if created else "—",
            "updated_at": updated,
            "updated": format_msk_short(updated) if updated else "—",
            "messages": msg_count,
            "tokens": total_tokens,
            "cost": 0.0,
            "models": models,
            "last_model": models[-1] if models else "",
        }
    except Exception:
        logger.debug("_read_summary_from_history: parse {} failed", history_path, exc_info=True)
        return None


def _empty_statistics(days: int | None = None) -> dict:
    stats: dict = {
        "total_sessions": 0, "total_messages": 0,
        "total_input_tokens": 0, "total_output_tokens": 0,
        "total_cost": 0.0,
        "by_model": {},
    }
    if days is not None:
        stats["days"] = days
    return stats


def _ensure_model_stats(stats: dict, model: str) -> dict:
    if model not in stats["by_model"]:
        stats["by_model"][model] = {
            "sessions": 0, "messages": 0, "input_tokens": 0,
            "output_tokens": 0, "cost": 0.0,
        }
    return stats["by_model"][model]


def _get_period_statistics_from_history(days: int) -> dict:
    stats = _empty_statistics(days)
    cutoff = time.time() - days * 86400
    if not config.SESSIONS_DIR.exists():
        return stats

    input_roles = {"user", "system", "tool_result"}
    for session_dir in config.SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        # summary.updated_at — авторитетный признак последней активности сессии:
        # если он раньше cutoff, сессия не относится к периоду (это поведение
        # зафиксировано тестами — updated_at пишется при каждом save).
        summary_path = session_dir / "summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                if "updated_at" in summary:
                    updated_at = float(summary.get("updated_at") or 0)
                    if updated_at < cutoff:
                        continue
            except Exception:
                pass
        history_path = session_dir / "history.json"
        if not history_path.exists():
            continue
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        session_models = set()
        period_messages = 0
        period_activity = False
        last_period_model = ""
        input_buffer: list[int] = []

        for msg in data.get("messages", []):
            role = msg.get("role", "")
            timestamp = float(msg.get("timestamp") or 0)
            tokens = int(msg.get("tokens") or 0)
            if role in input_roles:
                input_buffer.append(tokens)
                if role == "user" and timestamp >= cutoff:
                    period_messages += 1
                    period_activity = True
                continue

            if role != "assistant" or timestamp < cutoff:
                continue

            period_activity = True
            model = msg.get("model", "")
            if model in ("unknown", ""):
                continue

            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if usage and (usage.get("input") or usage.get("output")):
                input_tokens = int(usage.get("input") or 0)
                output_tokens = int(usage.get("output") or 0) or tokens
            else:
                input_tokens = sum(input_buffer)
                output_tokens = tokens

            price_in, price_out = app_models.get_pricing(model)
            cost = (
                input_tokens * price_in / 1_000_000
                + output_tokens * price_out / 1_000_000
            )

            model_stats = _ensure_model_stats(stats, model)
            model_stats["input_tokens"] += input_tokens
            model_stats["output_tokens"] += output_tokens
            model_stats["cost"] += cost

            stats["total_input_tokens"] += input_tokens
            stats["total_output_tokens"] += output_tokens
            stats["total_cost"] += cost
            session_models.add(model)
            last_period_model = model

        if not period_activity:
            continue

        stats["total_sessions"] += 1
        stats["total_messages"] += period_messages
        for model in session_models:
            stats["by_model"][model]["sessions"] += 1

        if len(session_models) == 1:
            model = next(iter(session_models))
            stats["by_model"][model]["messages"] += period_messages
        elif last_period_model in session_models:
            stats["by_model"][last_period_model]["messages"] += period_messages
        elif session_models:
            per_model = period_messages // len(session_models)
            for model in session_models:
                stats["by_model"][model]["messages"] += per_model

    return stats


def get_statistics(days: int | None = None) -> dict:
    if days is not None:
        return _get_period_statistics_from_history(days)

    stats = _empty_statistics()
    if not config.SESSIONS_DIR.exists():
        return stats

    for session_dir in config.SESSIONS_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        summary_path = session_dir / "summary.json"
        if not summary_path.exists():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        stats["total_sessions"] += 1
        stats["total_messages"] += data.get("messages", 0)

        session_models = set()
        session_input = 0
        session_output = 0
        session_total_cost = _compressed_total_cost(data)
        for model, mdata in data.get("cost_by_model", {}).items():
            if model in ("unknown", ""):
                continue
            session_models.add(model)
            m_input = mdata.get("input_tokens", 0)
            m_output = mdata.get("output_tokens", 0)
            session_input += m_input
            session_output += m_output
            m_cost = _recalc_model_cost(model, mdata)
            session_total_cost += m_cost
            model_stats = _ensure_model_stats(stats, model)
            model_stats["input_tokens"] += m_input
            model_stats["output_tokens"] += m_output
            model_stats["cost"] += m_cost

        stats["total_cost"] += session_total_cost

        if session_input == 0 and session_output == 0:
            session_input = data.get("input_tokens", 0)
            session_output = data.get("output_tokens", 0)
        stats["total_input_tokens"] += session_input
        stats["total_output_tokens"] += session_output

        for model in session_models:
            stats["by_model"][model]["sessions"] += 1

        session_msg_count = data.get("messages", 0)
        if len(session_models) == 1:
            m = next(iter(session_models))
            stats["by_model"][m]["messages"] += session_msg_count
        elif session_models:
            last = data.get("last_model", "")
            if last in session_models:
                stats["by_model"][last]["messages"] += session_msg_count
            else:
                per_model = session_msg_count // len(session_models)
                for m in session_models:
                    stats["by_model"][m]["messages"] += per_model

    return stats


def get_global_statistics() -> dict:
    return get_statistics(days=None)

