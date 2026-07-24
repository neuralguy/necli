"""Сессия диалога: история, подсчёт токенов/стоимости — site-aware."""

import re
import shutil
import time
import uuid
from datetime import datetime

import config
import models as app_models
from logger import logger
from session._time import MSK, format_msk
from session.message import Message


class Session:
    def __init__(
        self,
        session_id: str | None = None,
        title: str | None = None,
        site: str | None = None,
        working_dir: str | None = None,
    ):
        self.id = session_id or self._generate_id()
        self.title = title or ""
        self.site: str = site or "api"
        self.working_dir = working_dir or ""
        self.created_at = time.time()
        self.updated_at = time.time()
        self.messages: list[Message] = []
        # Дерево альтернатив: для каждого parent_id — список Message-вариантов.
        # Активный путь хранится в self.messages (по одному ребёнку на родителя),
        # а в _branch_alternatives живут НЕ выбранные альтернативы.
        # Корень — синтетический parent_id=""; первые user-message получают parent_id="".
        self._branch_alternatives: dict[str, list[Message]] = {}
        # Цепочки сообщений альтернативных веток по id первого сообщения хвоста.
        self._branch_tails: dict[str, list[Message]] = {}
        self._cost_cache: dict | None = None
        self._compressed_stats: dict | None = None
        self.chat_url: str = ""
        self.dir = config.SESSIONS_DIR / self.id

    def ensure_dir(self):
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _generate_id() -> str:
        now = datetime.now(tz=MSK)
        date_part = now.strftime("%Y%m%d_%H%M%S")
        uid_part = uuid.uuid4().hex[:6]
        return f"{date_part}_{uid_part}"

    _SLUG_BAD_RE = re.compile(r"[^\w\-]+", re.UNICODE)

    @classmethod
    def _make_slug(cls, text: str, max_len: int = 20) -> str:
        s = text.strip().replace("\n", " ").replace("\t", " ")
        s = cls._SLUG_BAD_RE.sub("_", s)
        s = re.sub(r"_+", "_", s).strip("_")
        if len(s) > max_len:
            s = s[:max_len].rstrip("_")
        return s or "chat"

    def _rename_for_first_message(self, user_text: str) -> None:
        """Переименовать директорию сессии в slug_дата_время при первом user-сообщении."""
        # Только если ID ещё «технический» (YYYYMMDD_HHMMSS_uid) — не трогаем уже переименованные
        if not re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", self.id):
            return
        slug = self._make_slug(user_text, max_len=20)
        now = datetime.now(tz=MSK)
        dt_part = now.strftime("%Y%m%d_%H%M%S")
        new_id = f"{slug}_{dt_part}_{uuid.uuid4().hex[:4]}"
        new_dir = config.SESSIONS_DIR / new_id
        # Защита от редкой коллизии uuid-хвоста.
        while new_dir.exists() and new_dir != self.dir:
            new_id = f"{slug}_{dt_part}_{uuid.uuid4().hex[:4]}"
            new_dir = config.SESSIONS_DIR / new_id
        try:
            old_dir = self.dir
            if old_dir.exists():
                shutil.move(str(old_dir), str(new_dir))
            self.id = new_id
            self.dir = new_dir
            logger.info("session renamed: {} → {}", old_dir.name, new_id)
        except Exception as e:
            logger.error("session rename failed: {}", e)

    def _last_message_id(self) -> str:
        return self.messages[-1].id if self.messages else ""

    def add_user_message(self, content: str, model: str = "", attachments: list | None = None) -> Message:
        is_first_user = not any(m.role == "user" for m in self.messages)
        msg = Message(
            role="user", content=content, model=model,
            parent_id=self._last_message_id(),
            attachments=attachments,
        )
        self.messages.append(msg)
        self._cost_cache = None
        self.updated_at = time.time()
        self._auto_title(content)
        if is_first_user:
            self._rename_for_first_message(content)
        try:
            from ui.terminal_title import set_session_terminal_title
            set_session_terminal_title(self)
        except Exception:
            logger.debug("terminal title update failed", exc_info=True)
        return msg

    def add_assistant_message(
        self,
        content: str,
        model: str = "",
        duration: float = 0.0,
        usage: dict | None = None,
        thoughts: list | None = None,
    ) -> Message:
        msg = Message(
            role="assistant", content=content, model=model,
            duration=duration, usage=usage,
            parent_id=self._last_message_id(),
            thoughts=thoughts,
        )
        if usage:
            self._reconcile_input_tokens(int(usage.get("input") or 0))
        self.messages.append(msg)
        self._cost_cache = None
        self.updated_at = time.time()
        return msg

    def _reconcile_input_tokens(self, real_input: int) -> None:
        """Пересчитывает tokens предыдущих input-сообщений по реальному usage.input.

        Провайдер вернул prompt_tokens = real_input — это сумма всех input-сообщений
        (system/user/assistant/tool_result) на момент запроса. Мы пропорционально
        раскидываем эту реальную сумму обратно на heuristic-оценки сообщений,
        сохраняя относительные веса. После этого msg.tokens = реальные токены,
        а не cl100k_base × 1.2.

        Если usage.input не пришёл (real_input <= 0) — оставляем эвристику.
        """
        if real_input <= 0 or not self.messages:
            return
        heuristic_sum = sum(m.tokens for m in self.messages if m.role != "assistant")
        if heuristic_sum <= 0:
            return
        ratio = real_input / heuristic_sum
        if abs(ratio - 1.0) < 0.02:
            return
        for m in self.messages:
            if m.role != "assistant" and m.tokens > 0:
                m.tokens = max(1, round(m.tokens * ratio))

    def add_system_message(self, content: str, model: str = "") -> Message:
        msg = Message(role="system", content=content, model=model)
        self.messages.append(msg)
        self._cost_cache = None
        self.updated_at = time.time()
        return msg

    def add_tool_result(self, content: str, model: str = "") -> Message:
        msg = Message(
            role="tool_result", content=content, model=model,
            parent_id=self._last_message_id(),
        )
        self.messages.append(msg)
        self._cost_cache = None
        self.updated_at = time.time()
        return msg

    # ── Branches ──────────────────────────────────────────────────────────

    def _find_message_index(self, msg_id: str) -> int:
        """Возвращает индекс сообщения в active path по id или -1."""
        for i, m in enumerate(self.messages):
            if m.id == msg_id:
                return i
        return -1

    def list_all_variants(self, msg_id: str) -> list[Message]:
        """Все варианты сообщения (alts + active), отсортированы по timestamp."""
        idx = self._find_message_index(msg_id)
        if idx < 0:
            return []
        parent_id = self.messages[idx].parent_id or ""
        alts = list(self._branch_alternatives.get(parent_id, []))
        active = self.messages[idx]
        merged = [*alts, active]
        merged.sort(key=lambda m: getattr(m, "timestamp", 0.0) or 0.0)
        return merged

    def fork_at(self, msg_id: str) -> list[Message]:
        """Откалывает active-ветку начиная с msg_id (включительно) в alternatives.

        Возвращает откушенный хвост (для последующего сохранения / переключения).
        После fork_at messages[] обрезана ДО (не включая) msg_id; добавленные
        дальше сообщения станут новой веткой.
        """
        idx = self._find_message_index(msg_id)
        if idx < 0:
            return []
        tail = self.messages[idx:]
        self.messages = self.messages[:idx]
        # Записываем СТАРЫЙ active хвост в alternatives под parent_id первого
        # элемента хвоста (это родитель ветки).
        if tail:
            parent_id = tail[0].parent_id or ""
            self._branch_alternatives.setdefault(parent_id, []).append(tail[0])
            # Дочерние сообщения в этой alt-ветке тоже надо сохранить.
            # Храним всю цепочку в _branch_tails по id первого сообщения хвоста.
            self._branch_tails[tail[0].id] = tail
        self._cost_cache = None
        self.updated_at = time.time()
        return tail

    def switch_branch(self, msg_id: str, target_variant_index: int) -> bool:
        """Переключает active ветку на target_variant из всех вариантов сообщения msg_id.

        Возвращает True если переключение произошло (или target уже активный).
        """
        variants = self.list_all_variants(msg_id)
        if not variants or target_variant_index < 0 or target_variant_index >= len(variants):
            logger.warning(
                "switch_branch: bad target_index={} variants={} msg_id={}",
                target_variant_index, len(variants), msg_id[:8],
            )
            return False
        target = variants[target_variant_index]
        if target.id == msg_id:
            return True  # уже активный — no-op success

        # Текущий active хвост откалываем (попадает в _branch_tails)
        self.fork_at(msg_id)
        # Достаём цепочку для target из _branch_tails
        target_tail = self._branch_tails.get(target.id, [target])
        # Удаляем target из alternatives, потому что он становится active
        parent_id = target.parent_id or ""
        alts = self._branch_alternatives.get(parent_id, [])
        self._branch_alternatives[parent_id] = [m for m in alts if m.id != target.id]
        if not self._branch_alternatives[parent_id]:
            del self._branch_alternatives[parent_id]
        # Цепочка теперь становится active — убираем её из _branch_tails
        self._branch_tails.pop(target.id, None)
        # Подтягиваем хвост в active
        self.messages.extend(target_tail)
        self._cost_cache = None
        self.updated_at = time.time()
        logger.info(
            "switch_branch OK: target_id={} added {} messages",
            target.id[:8], len(target_tail),
        )
        return True

    _TOOL_BLOCK_RE = re.compile(
        r':::call[ \t]+\w+[^\n]*\n.*?(?:\n|^)call:::[ \t]*(?:\n|$)',
        re.DOTALL | re.MULTILINE,
    )

    def _round_boundaries(self) -> list[int]:
        """Индексы начала каждого раунда (user-сообщения) в self.messages."""
        return [i for i, m in enumerate(self.messages) if m.role == "user"]

    def tail_split_index(self, keep_recent_rounds: int) -> int:
        """Индекс, начиная с которого идут последние keep_recent_rounds раундов.

        Раунд = user-сообщение и всё до следующего user. Возвращает 0 если
        раундов меньше либо равно keep_recent_rounds (сжимать нечего).
        """
        bounds = self._round_boundaries()
        if len(bounds) <= keep_recent_rounds:
            return 0
        return bounds[len(bounds) - keep_recent_rounds]

    @staticmethod
    def _truncate_tool_block(m: re.Match) -> str:
        block = m.group(0)
        if len(block) <= 500:
            return block
        return block[:500] + "\n...(truncated)..."

    def build_compress_text(self, upto_index: int | None = None) -> str:
        parts: list[str] = []
        messages = self.messages if upto_index is None else self.messages[:upto_index]
        for msg in messages:
            if msg.role in ("system", "tool_result"):
                continue
            content = msg.content
            if msg.role == "assistant":
                content = self._TOOL_BLOCK_RE.sub(self._truncate_tool_block, content)
            label = "USER" if msg.role == "user" else "ASSISTANT"
            parts.append(f"{label}:\n{content}")
        text = "\n\n---\n\n".join(parts)
        if len(text) > 150_000:
            text = text[:75_000] + "\n\n[... middle truncated ...]\n\n" + text[-75_000:]
        return text

    def compress_reset(self, compressed_text: str, model: str = ""):
        snapshot = self.summary()
        logger.info(
            "session.compress_reset: id={} msgs={} cost=${:.4f} → {} chars compressed",
            self.id[:16], snapshot["messages"], snapshot["total_cost"], len(compressed_text),
        )
        self._compressed_stats = {
            "messages": snapshot["messages"],
            "total_cost": snapshot["total_cost"],
        }
        self.messages.clear()
        self._cost_cache = None
        meta = (
            f"[compressed] messages={snapshot['messages']}"
            f" cost=${snapshot['total_cost']:.4f}"
            f" models={', '.join(snapshot['models'])}"
        )
        self.add_system_message(meta, model=model)
        self.add_system_message(compressed_text, model=model)
        self.chat_url = ""

    def compress_reset_partial(
        self, compressed_text: str, tail_index: int, model: str = "",
    ) -> int:
        """Инкрементальная компрессия: сжать messages[:tail_index] в summary,
        сохранить messages[tail_index:] дословно.

        compressed_text — LLM-summary старой части. tail_index — индекс начала
        хвоста (см. tail_split_index). Возвращает число сжатых сообщений.
        Если tail_index <= 0 — fallback на полный compress_reset.
        """
        if tail_index <= 0:
            self.compress_reset(compressed_text, model=model)
            return 0
        head = self.messages[:tail_index]
        tail = self.messages[tail_index:]
        compressed_msgs = sum(1 for m in head if m.role == "user")
        # Стоимость считается по assistant-сообщениям головы: для каждого берём
        # его input (usage.input или сумму буфера input-сообщений до него) и
        # output по фактическим ценам модели — точнее, чем масштабирование по
        # доле числа сообщений.
        compressed_cost = self._cost_of_messages(head)
        logger.info(
            "session.compress_reset_partial: id={} head={} tail={} → {} chars",
            self.id[:16], len(head), len(tail), len(compressed_text),
        )
        prev = self._compressed_stats or {"messages": 0, "total_cost": 0.0}
        self._compressed_stats = {
            "messages": prev["messages"] + compressed_msgs,
            "total_cost": prev["total_cost"] + compressed_cost,
        }
        meta = f"[compressed {compressed_msgs} earlier round(s); recent rounds kept verbatim]"
        self.messages = [
            Message(role="system", content=meta, model=model),
            Message(role="system", content=compressed_text, model=model),
            *tail,
        ]
        self._cost_cache = None
        self.updated_at = time.time()
        return compressed_msgs

    def _auto_title(self, user_text: str):
        if not self.title:
            self.title = " ".join(user_text.strip().split())

    @property
    def models_used(self) -> list[str]:
        seen = set()
        result = []
        for m in self.messages:
            if m.model and m.model not in seen and m.model != "unknown":
                seen.add(m.model)
                result.append(m.model)
        return result

    @property
    def last_model(self) -> str:
        for m in reversed(self.messages):
            if m.model and m.model != "unknown":
                return m.model
        return ""

    @property
    def input_tokens(self) -> int:
        return sum(s["input_tokens"] for s in self._compute_cost().values())

    @property
    def output_tokens(self) -> int:
        return sum(m.tokens for m in self.messages if m.role == "assistant")

    @property
    def raw_input_tokens(self) -> int:
        return sum(m.tokens for m in self.messages if m.role in ("user", "system", "tool_result"))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def context_tokens(self) -> int:
        return self.raw_input_tokens + self.output_tokens

    def _compute_cost(self) -> dict:
        if self._cost_cache is not None:
            return self._cost_cache
        by_model: dict[str, dict] = {}
        input_buffer: list[tuple[int, str]] = []

        for msg in self.messages:
            model = msg.model or "unknown"
            if msg.role in ("user", "system", "tool_result"):
                input_buffer.append((msg.tokens, model))
            elif msg.role == "assistant":
                if model not in by_model:
                    by_model[model] = {
                        "input_tokens": 0, "output_tokens": 0,
                        "input_cost": 0.0, "output_cost": 0.0,
                        "total_cost": 0.0,
                    }
                price_in, price_out = app_models.get_pricing(model)

                # Приоритет: реальный usage от провайдера. Cached-токены
                # игнорируем — считаем весь input по обычной цене.
                usage = msg.usage if isinstance(msg.usage, dict) else None
                if usage and (usage.get("input") or usage.get("output")):
                    all_input = int(usage.get("input") or 0)
                    output_tokens = int(usage.get("output") or 0) or msg.tokens
                else:
                    all_input = sum(t for t, _ in input_buffer)
                    output_tokens = msg.tokens

                by_model[model]["input_tokens"] += all_input
                by_model[model]["output_tokens"] += output_tokens
                by_model[model]["input_cost"] += all_input * price_in / 1_000_000
                by_model[model]["output_cost"] += output_tokens * price_out / 1_000_000

        for model in by_model:
            s = by_model[model]
            s["total_cost"] = s["input_cost"] + s["output_cost"]

        self._cost_cache = by_model
        return by_model

    def _cost_of_messages(self, msgs: list) -> float:
        """Точная стоимость подсписка сообщений (input+output по ценам модели).

        Тот же алгоритм, что и в _compute_cost: input-сообщения копятся в буфер
        и приписываются к ближайшему следующему assistant; usage провайдера
        имеет приоритет над эвристикой токенов.
        """
        total = 0.0
        input_buffer: list[int] = []
        for msg in msgs:
            if msg.role in ("user", "system", "tool_result"):
                input_buffer.append(msg.tokens)
            elif msg.role == "assistant":
                model = msg.model or "unknown"
                price_in, price_out = app_models.get_pricing(model)
                usage = msg.usage if isinstance(msg.usage, dict) else None
                if usage and (usage.get("input") or usage.get("output")):
                    all_input = int(usage.get("input") or 0)
                    output_tokens = int(usage.get("output") or 0) or msg.tokens
                else:
                    all_input = sum(input_buffer)
                    output_tokens = msg.tokens
                total += all_input * price_in / 1_000_000
                total += output_tokens * price_out / 1_000_000
                input_buffer = []
        return total

    @property
    def total_cost(self) -> float:
        base = self._compressed_stats["total_cost"] if self._compressed_stats else 0.0
        return base + sum(s["total_cost"] for s in self._compute_cost().values())

    @property
    def total_duration(self) -> float:
        return sum(m.duration for m in self.messages if m.role == "assistant" and m.duration)

    @property
    def message_count(self) -> int:
        base = self._compressed_stats["messages"] if self._compressed_stats else 0
        return base + sum(1 for m in self.messages if m.role == "user")

    def summary(self) -> dict:
        cost_data = self._compute_cost()
        total_cost = self.total_cost
        return {
            "id": self.id,
            "title": self.title,
            "site": self.site,
            "working_dir": self.working_dir,
            "chat_url": self.chat_url,
            "created_at": self.created_at,
            "created": format_msk(self.created_at),
            "updated_at": self.updated_at,
            "updated": format_msk(self.updated_at),
            "models": self.models_used,
            "last_model": self.last_model,
            "messages": self.message_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "context_tokens": self.context_tokens,
            "total_cost": round(total_cost, 8),
            "total_duration": round(self.total_duration, 2),
            "cost_by_model": {
                k: {kk: round(vv, 8) if isinstance(vv, float) else vv for kk, vv in v.items()}
                for k, v in cost_data.items()
                if k not in ("unknown", "")
            },
            "compressed_stats": self._compressed_stats,
        }

