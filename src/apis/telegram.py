"""Telegram-мост: aiogram-бот для зеркалирования событий агента и приёма сообщений.

Архитектура:
- Singleton TelegramBridge живёт на протяжении сессии CLI.
- При старте `interactive` команда вызывает `bridge.start()` если токен/chat_id заданы.
- Отправка сообщений — через `bridge.send(text)`, ставится в фоновую очередь,
  отправляется батчем (склейка), чтобы не превысить лимит Telegram (~30 msg/s).
- Приём: aiogram polling в фоновой задаче пишет входящие в `incoming_queue`,
  основной prompt loop читает её параллельно с stdin.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


TG_MAX_LEN = 4000  # запас от лимита 4096
SEND_INTERVAL = 0.3  # сек между сообщениями (батч)


def _escape_html(text: str) -> str:
    return html.escape(text, quote=False)


# Простые форматирующие теги Telegram, которые нужно закрыть на границе чанка
# и переоткрыть в следующем, иначе оба куска становятся невалидным HTML и
# Telegram отвечает 400 (сообщение теряется).
_TG_TAG_RE = re.compile(r"<(/?)(b|i|u|s|code|pre)(?:\s[^>]*)?>", re.IGNORECASE)


def _open_tags_at(text: str) -> list[str]:
    """Возвращает стек незакрытых форматирующих тегов в конце text."""
    stack: list[str] = []
    for m in _TG_TAG_RE.finditer(text):
        closing = m.group(1) == "/"
        tag = m.group(2).lower()
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag:
                    del stack[i]
                    break
        else:
            stack.append(tag)
    return stack


def _strip_html(text: str) -> str:
    """Грубо снимает HTML-разметку для plain-text fallback при parse-ошибке."""
    no_tags = re.sub(r"<[^>]+>", "", text)
    return html.unescape(no_tags)


def _safe_cut(remaining: str, limit: int) -> int:
    """Находит позицию реза ≤ limit, не попадающую внутрь HTML-тега '<...>'."""
    cut = remaining.rfind("\n", 0, limit)
    if cut >= limit // 2:
        return cut
    cut = limit
    # Если рез попал между '<' и его '>' — сдвигаем на символ перед '<'.
    lt = remaining.rfind("<", 0, cut)
    gt = remaining.rfind(">", 0, cut)
    if lt > gt:
        safe = remaining.rfind(">", 0, lt)
        cut = safe + 1 if safe != -1 else lt
    # Гарантируем прогресс: рез всегда положительный.
    if cut <= 0:
        cut = limit
    return cut


def _split_long(text: str, limit: int = TG_MAX_LEN) -> list[str]:
    """Делит текст на куски ≤ limit по \\n/границе тега, сохраняя валидность HTML.

    На границе чанка незакрытые форматирующие теги (b/i/u/s/code/pre)
    закрываются, а в следующем чанке переоткрываются — иначе Telegram
    отвергает оба куска с 400 Bad Request.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    carry: list[str] = []  # теги, которые надо переоткрыть в начале следующего чанка
    while True:
        prefix = "".join(f"<{t}>" for t in carry)
        # Резервируем место не только под открывающий prefix, но и под
        # закрывающие теги, которые могут быть дописаны в конец чанка.
        # Худший случай закрытия — все теги carry плюс возможные теги,
        # открытые внутри head; оцениваем сверху как 2x длину prefix
        # (открывающий <tag> ≈ закрывающий </tag> + слеш).
        close_reserve = len(prefix) + len(carry) + 2
        budget = limit - len(prefix) - close_reserve
        if budget < 64:  # деградация: limit слишком мал относительно carry
            budget = max(64, limit - len(prefix))
        if len(prefix) + len(remaining) <= limit:
            # Остаток (с переоткрытыми тегами) помещается целиком.
            parts.append(prefix + remaining)
            break
        cut = _safe_cut(remaining, budget)
        head = remaining[:cut]
        chunk = prefix + head
        open_now = _open_tags_at(chunk)
        if open_now:
            chunk += "".join(f"</{t}>" for t in reversed(open_now))
        # Подстраховка: если оценка не сработала и чанк всё равно длиннее
        # лимита, ужимаем head до тех пор, пока не уложимся.
        while len(chunk) > limit and cut > 1:
            cut = max(1, cut - (len(chunk) - limit) - 1)
            head = remaining[:cut]
            chunk = prefix + head
            open_now = _open_tags_at(chunk)
            if open_now:
                chunk += "".join(f"</{t}>" for t in reversed(open_now))
        parts.append(chunk)
        carry = open_now
        remaining = remaining[cut:].lstrip("\n")
        if not remaining:
            break
    return parts


def _is_html_parse_error(exc: Exception) -> bool:
    """True если ошибка Telegram связана с невалидным HTML (можно ретраить как plain)."""
    msg = str(exc).lower()
    return (
        "can't parse entities" in msg
        or "can't find end" in msg
        or "unsupported start tag" in msg
        or "unclosed" in msg
        or ("tag" in msg and "parse" in msg)
    )


@dataclass
class IncomingMessage:
    text: str
    chat_id: int
    user_id: int
    username: str


class TelegramBridge:
    _instance: Optional["TelegramBridge"] = None

    def __init__(self):
        self._bot = None  # aiogram.Bot
        self._dp = None   # aiogram.Dispatcher
        self._polling_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._send_queue: Optional[asyncio.Queue] = None
        self.incoming_queue: Optional[asyncio.Queue] = None
        self._chat_id: Optional[int] = None
        self._running = False
        self._lock = asyncio.Lock()
        self._typing_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._command_handlers: dict = {}
        self._callback_handler = None
        self._reply_keyboard = None
        self._button_aliases: dict[str, str] = {}
        self.agent_busy: bool = False
        # Inline-approve tool-вызовов: approval_id → (concurrent.futures.Future, message_id)
        self._pending_approvals: dict = {}
        self._approval_seq: int = 0

    @classmethod
    def instance(cls) -> "TelegramBridge":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def chat_id(self) -> Optional[int]:
        return self._chat_id

    async def start(self, token: str, chat_id: int) -> tuple[bool, str]:
        """Запускает бота. Возвращает (success, message)."""
        async with self._lock:
            if self._running:
                return True, "уже запущен"
            try:
                from aiogram import Bot, Dispatcher, F
                from aiogram.client.default import DefaultBotProperties
                from aiogram.enums import ParseMode
            except ImportError as e:
                logger.error("aiogram import failed: %s", e)
                return False, f"aiogram не установлен: {e}"

            try:
                self._bot = Bot(
                    token=token,
                    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
                )
                me = await self._bot.get_me()
                logger.info("telegram bot connected: @%s id=%s", me.username, me.id)
            except Exception as e:
                logger.error("telegram bot init failed: %s", e, exc_info=True)
                self._bot = None
                return False, f"ошибка подключения: {e}"

            self._chat_id = int(chat_id)
            self._dp = Dispatcher()
            self._send_queue = asyncio.Queue(maxsize=1000)
            self.incoming_queue = asyncio.Queue(maxsize=1000)

            target_chat = self._chat_id

            @self._dp.message(F.chat.id == target_chat)
            async def _on_msg(message):  # type: ignore[no-redef]
                text = (message.text or message.caption or "").strip()
                if not text:
                    return
                # Кнопки reply-клавиатуры с эмодзи-лейблами → маппинг в slash-команду
                alias = self._button_aliases.get(text)
                if alias is not None:
                    text = alias
                # Slash-команды → отдельные хендлеры (не уходят в основной prompt)
                if text.startswith("/"):
                    parts = text.split(maxsplit=1)
                    cmd = parts[0].split("@")[0].lower()
                    arg = parts[1] if len(parts) > 1 else ""
                    handler = self._command_handlers.get(cmd)
                    if handler is not None:
                        try:
                            await handler(arg, message)
                        except Exception as e:
                            logger.error("tg cmd %s failed: %s", cmd, e, exc_info=True)
                            self.send(f"❌ <i>cmd {_escape_html(cmd)}: {_escape_html(str(e))}</i>")
                        return
                await self.incoming_queue.put(IncomingMessage(
                    text=text,
                    chat_id=message.chat.id,
                    user_id=message.from_user.id if message.from_user else 0,
                    username=(message.from_user.username if message.from_user else "") or "",
                ))
                logger.info("telegram in: %s", text[:80])

            @self._dp.callback_query(F.message.chat.id == target_chat)
            async def _on_cb(cb):  # type: ignore[no-redef]
                data = cb.data or ""
                # Approve tool-вызова имеет приоритет над меню-handler'ом.
                if data.startswith("approve:") or data.startswith("deny:"):
                    await self._resolve_approval(data, cb)
                    return
                handler = self._callback_handler
                if handler is not None:
                    try:
                        await handler(data, cb)
                    except Exception as e:
                        logger.error("tg cb %s failed: %s", data, e, exc_info=True)
                        try:
                            await cb.answer(f"Ошибка: {e}", show_alert=True)
                        except Exception:
                            pass
                else:
                    try:
                        await cb.answer()
                    except Exception:
                        pass

            self._running = True
            self._loop = asyncio.get_running_loop()
            self._polling_task = asyncio.create_task(
                self._run_polling(), name="tg-polling",
            )
            self._sender_task = asyncio.create_task(
                self._run_sender(), name="tg-sender",
            )
            return True, f"@{me.username}"

    async def _run_polling(self):
        try:
            await self._dp.start_polling(self._bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("telegram polling crashed: %s", e, exc_info=True)
            self._running = False

    async def _run_sender(self):
        try:
            while self._running:
                queue = self._send_queue
                if queue is None:
                    break
                item = await queue.get()
                if item is None:
                    break
                if not self._running or self._bot is None:
                    break
                text, reply_markup = item if isinstance(item, tuple) else (item, None)
                chunks = _split_long(text)
                for i, chunk in enumerate(chunks):
                    bot = self._bot
                    if not self._running or bot is None:
                        return
                    # reply_markup только на последний чанк
                    markup = reply_markup if i == len(chunks) - 1 else None
                    try:
                        await bot.send_message(
                            self._chat_id,
                            chunk,
                            disable_web_page_preview=True,
                            reply_markup=markup,
                        )
                    except Exception as e:
                        if _is_html_parse_error(e):
                            try:
                                await bot.send_message(
                                    self._chat_id,
                                    _strip_html(chunk),
                                    disable_web_page_preview=True,
                                    reply_markup=markup,
                                    parse_mode=None,
                                )
                            except Exception as e2:
                                logger.warning("tg send failed (plain fallback): %s", e2)
                        else:
                            logger.warning("tg send failed: %s", e)
                    await asyncio.sleep(SEND_INTERVAL)
        except asyncio.CancelledError:
            raise

    async def stop(self):
        async with self._lock:
            if not self._running:
                return
            self._running = False
            try:
                if self._dp is not None:
                    await self._dp.stop_polling()
            except Exception:
                logger.debug("dp.stop_polling failed", exc_info=True)
            for task in (self._polling_task, self._sender_task, self._typing_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.debug("tg task stop raised", exc_info=True)
            self._typing_task = None
            if self._bot is not None:
                try:
                    await self._bot.session.close()
                except Exception:
                    logger.debug("bot.session.close failed", exc_info=True)
            self._polling_task = None
            self._sender_task = None
            self._bot = None
            self._dp = None
            self._send_queue = None
            self.incoming_queue = None
            # Сбрасываем хендлеры с замыканиями на старый state — иначе при
            # повторном start() через тот же singleton останутся мёртвые ссылки.
            self._command_handlers = {}
            self._callback_handler = None
            self._button_aliases = {}
            self._reply_keyboard = None
            for _aid, (fut, _mid) in list(self._pending_approvals.items()):
                if not fut.done():
                    try:
                        fut.set_result(None)
                    except Exception:
                        logger.debug("pending approval cancel failed", exc_info=True)
            self._pending_approvals = {}
            self._loop = None
            logger.info("telegram bridge stopped")

    def send(self, text: str, reply_markup=None) -> None:
        """Безопасно ставит сообщение в очередь отправки. Можно вызывать из sync-кода."""
        if not self._running or self._send_queue is None:
            return
        if not text or not text.strip():
            return
        try:
            self._send_queue.put_nowait((text, reply_markup))
        except asyncio.QueueFull:
            logger.warning("tg send queue full, dropping")

    def register_command(self, cmd: str, handler) -> None:
        """Регистрирует обработчик slash-команды от бота. handler: async (arg: str, message) -> None."""
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        self._command_handlers[cmd.lower()] = handler

    def register_button_aliases(self, aliases: dict) -> None:
        """Регистрирует маппинг текста reply-кнопок → slash-команды."""
        self._button_aliases.update({k: (v if v.startswith("/") else "/" + v) for k, v in aliases.items()})

    def register_callback_handler(self, handler) -> None:
        """Регистрирует обработчик callback_query. handler: async (data: str, callback_query) -> None."""
        self._callback_handler = handler

    async def set_bot_commands(self, commands: list) -> None:
        """Регистрирует команды в меню Telegram. commands: [(cmd, description), ...]."""
        if not self._running or self._bot is None:
            return
        try:
            from aiogram.types import BotCommand
            await self._bot.set_my_commands([
                BotCommand(command=c.lstrip("/"), description=d) for c, d in commands
            ])
        except Exception as e:
            logger.debug("set_bot_commands failed: %s", e)

    def set_bot_commands_sync(self, commands: list) -> None:
        if not self._running or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.set_bot_commands(commands), self._loop,
            )
        except Exception as e:
            logger.debug("set_bot_commands_sync failed: %s", e)

    async def answer_callback(self, callback_query, text: str = "", show_alert: bool = False) -> None:
        try:
            await callback_query.answer(text or None, show_alert=show_alert)
        except Exception as e:
            logger.debug("answer_callback failed: %s", e)

    async def _resolve_approval(self, data: str, cb) -> None:
        """Обрабатывает нажатие inline-кнопки approve:/deny: и будит ждущий поток."""
        try:
            verb, _, approval_id = data.partition(":")
        except Exception:
            verb, approval_id = "", ""
        allowed = verb == "approve"
        entry = self._pending_approvals.pop(approval_id, None)
        try:
            await cb.answer("✅ allowed" if allowed else "✗ denied", show_alert=False)
        except Exception:
            logger.debug("approval answer_callback failed", exc_info=True)
        if entry is None:
            return
        fut, message_id = entry
        if not fut.done():
            try:
                fut.set_result(allowed)
            except Exception:
                logger.debug("approval future set_result failed", exc_info=True)
        if message_id is not None:
            try:
                mark = "✅ <b>Allowed</b>" if allowed else "✗ <b>Denied</b>"
                await self._bot.edit_message_reply_markup(
                    chat_id=self._chat_id, message_id=message_id, reply_markup=None,
                )
                await self._bot.edit_message_text(
                    chat_id=self._chat_id, message_id=message_id, text=mark,
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.debug("approval message edit failed", exc_info=True)

    async def _send_approval_message(self, text: str, approval_id: str):
        """Отправляет сообщение с inline-кнопками allow/deny. Возвращает message_id."""
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Allow", callback_data=f"approve:{approval_id}"),
            InlineKeyboardButton(text="✗ Deny", callback_data=f"deny:{approval_id}"),
        ]])
        chunk = _split_long(text)[0]
        try:
            m = await self._bot.send_message(
                self._chat_id, chunk, disable_web_page_preview=True, reply_markup=markup,
            )
            return m.message_id
        except Exception as e:
            if _is_html_parse_error(e):
                try:
                    m = await self._bot.send_message(
                        self._chat_id, _strip_html(chunk), disable_web_page_preview=True,
                        reply_markup=markup, parse_mode=None,
                    )
                    return m.message_id
                except Exception as e2:
                    logger.warning("tg approval send failed (plain): %s", e2)
                    return None
            logger.warning("tg approval send failed: %s", e)
            return None

    def request_approval(self, text: str, timeout: float = 300.0) -> Optional[bool]:
        """Sync: шлёт запрос с inline-кнопками allow/deny и ждёт нажатия.

        Возвращает True (allow), False (deny) или None (таймаут / бридж не активен).
        Безопасно вызывать из рабочего потока executor'а.
        """
        if not self._running or self._loop is None or self._bot is None:
            return None
        import concurrent.futures
        self._approval_seq += 1
        approval_id = str(self._approval_seq)
        fut: concurrent.futures.Future = concurrent.futures.Future()

        async def _setup() -> None:
            message_id = await self._send_approval_message(text, approval_id)
            self._pending_approvals[approval_id] = (fut, message_id)

        try:
            asyncio.run_coroutine_threadsafe(_setup(), self._loop).result(timeout=10.0)
        except Exception as e:
            logger.warning("tg approval setup failed: %s", e)
            return None
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            self._pending_approvals.pop(approval_id, None)
            logger.info("tg approval timed out: id=%s", approval_id)
            return None
        except Exception as e:
            logger.debug("tg approval wait failed: %s", e)
            return None

    async def edit_inline_message(self, callback_query, text: str, reply_markup=None) -> None:
        try:
            chunk = _split_long(text)[0]
            await callback_query.message.edit_text(
                chunk, disable_web_page_preview=True, reply_markup=reply_markup,
            )
        except Exception as e:
            if _is_html_parse_error(e):
                try:
                    await callback_query.message.edit_text(
                        _strip_html(_split_long(text)[0]),
                        disable_web_page_preview=True,
                        reply_markup=reply_markup, parse_mode=None,
                    )
                    return
                except Exception as e2:
                    logger.debug("edit_inline_message failed (plain fallback): %s", e2)
                    return
            logger.debug("edit_inline_message failed: %s", e)

    async def send_placeholder(self, text: str) -> Optional[int]:
        """Отправляет сообщение сразу (минуя очередь) и возвращает message_id.

        Используется для thinking-плейсхолдера, который потом редактируется.
        """
        if not self._running or self._bot is None:
            return None
        try:
            chunk = _split_long(text)[0]
            m = await self._bot.send_message(
                self._chat_id, chunk, disable_web_page_preview=True,
            )
            return m.message_id
        except Exception as e:
            if _is_html_parse_error(e):
                try:
                    m = await self._bot.send_message(
                        self._chat_id, _strip_html(_split_long(text)[0]),
                        disable_web_page_preview=True, parse_mode=None,
                    )
                    return m.message_id
                except Exception as e2:
                    logger.warning("tg placeholder failed (plain fallback): %s", e2)
                    return None
            logger.warning("tg placeholder failed: %s", e)
            return None

    async def edit_message(self, message_id: int, text: str) -> bool:
        """Редактирует сообщение по id. Если текст длиннее лимита — заменяет только первый чанк, остальное отправляет следом."""
        if not self._running or self._bot is None or message_id is None:
            return False
        chunks = _split_long(text)
        first = chunks[0]
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=message_id,
                text=first,
                disable_web_page_preview=True,
            )
        except Exception as e:
            if _is_html_parse_error(e):
                try:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=message_id,
                        text=_strip_html(first),
                        disable_web_page_preview=True,
                        parse_mode=None,
                    )
                except Exception as e2:
                    logger.warning("tg edit failed (plain fallback): %s", e2)
                    return False
            else:
                logger.warning("tg edit failed: %s", e)
                return False
        for ch in chunks[1:]:
            self.send(ch)
        return True

    def edit_message_threadsafe(self, message_id: int, text: str) -> None:
        """Sync-safe вариант edit_message: планирует корутину в loop'е бота."""
        if not self._running or self._loop is None or message_id is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.edit_message(message_id, text), self._loop,
            )
        except Exception as e:
            logger.debug("edit_message_threadsafe failed: %s", e)

    def send_placeholder_threadsafe(self, text: str) -> Optional[int]:
        """Sync-safe send_placeholder: блокирующе ждёт результат (≤3 сек)."""
        if not self._running or self._loop is None:
            return None
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self.send_placeholder(text), self._loop,
            )
            return fut.result(timeout=3.0)
        except Exception as e:
            logger.debug("send_placeholder_threadsafe failed: %s", e)
            return None

    async def _run_typing(self):
        """Шлёт chat action 'typing' каждые 4 секунды (action длится ~5с в TG)."""
        try:
            while self._running:
                try:
                    await self._bot.send_chat_action(self._chat_id, "typing")
                except Exception as e:
                    logger.debug("tg typing failed: %s", e)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise

    def start_typing(self) -> None:
        """Запускает фоновый typing-индикатор. Идемпотентно."""
        if not self._running or self._loop is None:
            return
        if self._typing_task and not self._typing_task.done():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._spawn_typing(), self._loop,
            )
        except Exception as e:
            logger.debug("start_typing schedule failed: %s", e)

    async def _spawn_typing(self) -> None:
        self._typing_task = asyncio.create_task(self._run_typing(), name="tg-typing")

    def stop_typing(self) -> None:
        """Останавливает typing-индикатор."""
        task = self._typing_task
        if task is None:
            return
        self._typing_task = None
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(task.cancel)
        except Exception as e:
            logger.debug("stop_typing failed: %s", e)

    async def test_send(self, token: str, chat_id: int, text: str) -> tuple[bool, str]:
        """Отправляет тестовое сообщение без запуска поллинга."""
        try:
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode
        except ImportError as e:
            return False, f"aiogram не установлен: {e}"
        bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            await bot.send_message(int(chat_id), text, disable_web_page_preview=True)
            return True, "ок"
        except Exception as e:
            return False, str(e)
        finally:
            try:
                await bot.session.close()
            except Exception:
                logger.debug("test_send bot.session.close failed", exc_info=True)


def get_bridge() -> TelegramBridge:
    return TelegramBridge.instance()