"""LSP-клиент: запускает language-серверы по stdio, шлёт JSON-RPC.

Архитектура:
  - LSPServer: один запущенный процесс LSP + state (capabilities, open files).
  - LSPManager: singleton, держит все серверы, фоновый asyncio loop в отдельном
    потоке (по аналогии с MCPManager).

Поддерживаемые методы (агентные tools):
  - textDocument/definition
  - textDocument/references
  - textDocument/hover

Использование:
  init_lsp_from_config()  # при старте CLI
  shutdown_lsp()          # при выходе
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from logger import logger
from tools.models import ToolCall, ToolResult


# ── язык по расширению ─────────────────────────────────────────

_EXT_TO_LANG = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact",
    ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
}


def _lang_id_for_path(path: Path) -> str:
    return _EXT_TO_LANG.get(path.suffix.lower(), "plaintext")


def _uri_for_path(path: Path) -> str:
    return path.resolve().as_uri()


def _path_from_uri(uri: str) -> str:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse
        p = urlparse(uri)
        return unquote(p.path)
    return uri


# ── LSP server ─────────────────────────────────────────────────

@dataclass
class LSPServer:
    id: str
    config: dict
    status: str = "disconnected"   # disconnected | connected | error
    error: str = ""
    root_path: Optional[str] = None
    _proc: Any = None              # asyncio.subprocess.Process
    _reader_task: Any = None
    _next_id: int = 1
    _pending: dict = field(default_factory=dict)   # id → asyncio.Future
    _opened: dict = field(default_factory=dict)    # path → version
    _capabilities: dict = field(default_factory=dict)
    _diagnostics: dict = field(default_factory=dict)   # uri → list[dict]
    _diag_events: dict = field(default_factory=dict)   # uri → list[asyncio.Event] (по одному на ожидающий вызов)


def _detect_root(file_path: Path, markers: list[str]) -> Path:
    cur = file_path.resolve().parent
    for parent in (cur, *cur.parents):
        for m in markers or []:
            if (parent / m).exists():
                return parent
    return cur


# ── LSPManager ─────────────────────────────────────────────────

class LSPManager:
    _instance: Optional["LSPManager"] = None

    def __init__(self):
        self.servers: dict[str, LSPServer] = {}
        self._configs: list[dict] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._servers_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "LSPManager":
        if cls._instance is None:
            cls._instance = LSPManager()
        return cls._instance

    # ── фоновый loop ──

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def _run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                try:
                    loop.run_forever()
                except Exception as e:
                    logger.error("lsp loop crashed: {}", e)
                finally:
                    try:
                        loop.close()
                    except Exception:
                        logger.debug("lsp loop.close failed", exc_info=True)

            self._thread = threading.Thread(target=_run, name="lsp-loop", daemon=True)
            self._thread.start()
            ready.wait(timeout=5)
            if not self._loop:
                raise RuntimeError("LSP loop failed to start")
            return self._loop

    def _submit(self, coro, timeout: float = 30.0):
        loop = self._ensure_loop()
        fut: Future = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # ── публичная инициализация ──

    def init_from_configs(self, configs: list[dict]) -> None:
        """Сохраняет конфиги enabled-серверов. Запуск — ленивый по языку файла."""
        self._configs = []
        for cfg in configs:
            if not cfg.get("enabled", True):
                continue
            cmd = cfg.get("command", "")
            if not cmd or shutil.which(cmd) is None:
                logger.debug("lsp: skip '{}' (command '{}' not in PATH)", cfg.get("id"), cmd)
                continue
            self._configs.append(cfg)
        logger.info("lsp: {} server(s) configured (lazy start)", len(self._configs))

    def _find_config_for(self, file_path: Path) -> Optional[dict]:
        ext = file_path.suffix.lower()
        for cfg in self._configs:
            if ext in (cfg.get("extensions") or []):
                return cfg
        return None

    def _ensure_server(self, file_path: Path) -> Optional[LSPServer]:
        cfg = self._find_config_for(file_path)
        if not cfg:
            return None
        root = _detect_root(file_path, cfg.get("root_markers") or [])
        if root is None:
            logger.error("lsp: cannot detect root for {}", file_path)
            return None
        key = f"{cfg['id']}@{root}"
        # Защищаем проверку+старт+запись self.servers отдельным lock-ом, иначе два
        # параллельных вызова стартуют два LSP-процесса и один осиротеет.
        # Нельзя использовать self._lock — _submit→_ensure_loop берёт его же (deadlock).
        with self._servers_lock:
            existing = self.servers.get(key)
            if existing is not None and existing.status == "connected":
                return existing
            server = LSPServer(id=key, config=cfg, root_path=str(root))
            try:
                self._submit(self._start_async(server), timeout=30.0)
                server.status = "connected"
                self.servers[key] = server
                logger.info("lsp connected: {} (root={})", key, root)
                return server
            except Exception as e:
                server.status = "error"
                server.error = str(e)
                self.servers[key] = server
                logger.error("lsp start '{}' failed: {}", key, e)
                return None

    def _unavailable_reason(self, file_path: Path) -> str:
        """Точная причина, почему LSP недоступен + подсказка fallback на grep.

        Раньше всё валилось в одно «нет сервера, проверь конфиг» — но причины
        разные: нет конфига для расширения / не найден root-маркер / сервер упал
        при старте. В случае с root-маркером конфиг в порядке, и совет «проверь
        конфиг» сбивает с толку. Всегда подсказываем grep_files как замену."""
        suffix = file_path.suffix or "(no extension)"
        fallback = " Use grep_files to locate the symbol instead."
        cfg = self._find_config_for(file_path)
        if not cfg:
            return (f"No LSP server configured for {suffix} files "
                    f"(check .data/lsp_servers.json)." + fallback)
        root = _detect_root(file_path, cfg.get("root_markers") or [])
        if root is None:
            markers = ", ".join(cfg.get("root_markers") or []) or "a project root"
            return (f"LSP server '{cfg['id']}' is configured but no project root was found "
                    f"for {file_path} (looked for: {markers}). LSP needs a project root to "
                    f"start." + fallback)
        # config есть, root есть → сервер не стартовал/упал
        key = f"{cfg['id']}@{root}"
        srv = self.servers.get(key)
        err = (srv.error if srv and srv.error else "failed to start")
        return (f"LSP server '{cfg['id']}' could not start ({err})." + fallback)

    # ── async-имплементация ──

    async def _start_async(self, server: LSPServer) -> None:
        cmd = server.config["command"]
        args = server.config.get("args") or []
        env = {**os.environ, **(server.config.get("env") or {})}
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=server.root_path,
        )
        server._proc = proc
        server._reader_task = asyncio.create_task(self._reader_loop(server))

        # initialize
        if server.root_path is None:
            raise RuntimeError(f"lsp[{server.id}]: root_path is None")
        root_uri = Path(server.root_path).resolve().as_uri()
        params = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "rootPath": server.root_path,
            "capabilities": {
                "textDocument": {
                    "definition": {"linkSupport": False},
                    "references": {},
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "synchronization": {"didSave": True},
                },
                "workspace": {"workspaceFolders": True},
            },
            "workspaceFolders": [{"uri": root_uri, "name": Path(server.root_path).name}],
            "clientInfo": {"name": "necli-api", "version": "1.0"},
            "initializationOptions": server.config.get("initialization_options") or {},
        }
        resp = await self._request(server, "initialize", params, timeout=20.0)
        server._capabilities = (resp or {}).get("capabilities", {})
        await self._notify(server, "initialized", {})
        # Конфигурация (pyright: diagnosticMode, useLibraryCodeForTypes и т.п.).
        settings = server.config.get("settings")
        if settings:
            await self._notify(server, "workspace/didChangeConfiguration", {"settings": settings})

    async def _reader_loop(self, server: LSPServer) -> None:
        proc = server._proc
        try:
            while True:
                # Читаем заголовки до \r\n\r\n
                headers = {}
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        return  # EOF
                    line = line.decode("utf-8", "replace").rstrip("\r\n")
                    if line == "":
                        break
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                payload = await proc.stdout.readexactly(length)
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError as e:
                    logger.warning("lsp[{}] bad json: {}", server.id, e)
                    continue
                self._dispatch(server, msg)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("lsp[{}] reader crashed: {}", server.id, e)

    def _dispatch(self, server: LSPServer, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = server._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        # Server-originated request (has id, no result/error): must reply so the
        # server doesn't block on startup (e.g. workspace/configuration).
        if "id" in msg and method is not None:
            self._reply_to_server_request(server, msg)
            return
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params") or {}
            uri = params.get("uri", "")
            diags = params.get("diagnostics") or []
            server._diagnostics[uri] = diags
            # Будим ВСЕ ожидающие вызовы для этого uri (их может быть несколько
            # конкурентно), а не один — иначе параллельный вызов оставался бы
            # висеть до таймаута.
            for ev in server._diag_events.get(uri, []):
                if not ev.is_set():
                    ev.set()

    def _reply_to_server_request(self, server: LSPServer, msg: dict) -> None:
        """Отвечает на server→client запрос минимальным JSON-RPC ответом (null result),
        чтобы сервер не зависал на старте (workspace/configuration и т.п.)."""
        rid = msg.get("id")
        try:
            asyncio.create_task(
                self._send(server, {"jsonrpc": "2.0", "id": rid, "result": None})
            )
        except Exception as e:
            logger.debug("lsp[{}] reply to server request id={} failed: {}", server.id, rid, e)

    async def _send(self, server: LSPServer, msg: dict) -> None:
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        server._proc.stdin.write(header + body)
        await server._proc.stdin.drain()

    async def _request(self, server: LSPServer, method: str, params: dict, timeout: float = 10.0):
        rid = server._next_id
        server._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        server._pending[rid] = fut
        await self._send(server, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            server._pending.pop(rid, None)
            raise RuntimeError(f"LSP request '{method}' timed out after {timeout}s")

    async def _notify(self, server: LSPServer, method: str, params: dict) -> None:
        await self._send(server, {"jsonrpc": "2.0", "method": method, "params": params})

    async def _ensure_open(self, server: LSPServer, file_path: Path) -> None:
        path_str = str(file_path.resolve())
        if path_str in server._opened:
            return
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            raise RuntimeError(f"cannot read {file_path}: {e}")
        version = 1
        server._opened[path_str] = version
        await self._notify(server, "textDocument/didOpen", {
            "textDocument": {
                "uri": _uri_for_path(file_path),
                "languageId": _lang_id_for_path(file_path),
                "version": version,
                "text": text,
            },
        })

    # ── публичные действия ──

    def _run_action(self, file_path_str: str, line: int, character: int, action: str) -> ToolResult:
        path = Path(file_path_str)
        if not path.is_absolute():
            from tools._paths import resolve_path
            path = resolve_path(file_path_str)
        if not path.exists():
            return ToolResult(name=f"lsp_{action}", status="error",
                              output=f"Файл не найден: {file_path_str}",
                              exit_code=1, command=f"lsp_{action}")
        server = self._ensure_server(path)
        if server is None:
            return ToolResult(name=f"lsp_{action}", status="error",
                              output=self._unavailable_reason(path),
                              exit_code=1, command=f"lsp_{action}")
        try:
            text = self._submit(self._action_async(server, path, line, character, action), timeout=20.0)
            return ToolResult(name=f"lsp_{action}", status="ok",
                              output=text, exit_code=0,
                              command=f"{server.id} {action} {path}:{line}:{character}")
        except Exception as e:
            logger.error("lsp_{} failed: {}", action, e)
            return ToolResult(name=f"lsp_{action}", status="error",
                              output=f"LSP {action} failed: {type(e).__name__}: {e}",
                              exit_code=1, command=f"lsp_{action}")

    async def _action_async(self, server: LSPServer, path: Path, line: int, character: int, action: str) -> str:
        await self._ensure_open(server, path)
        # LSP использует 0-индексы для строк и колонок; пользователь даёт 1-based строку.
        pos = {"line": max(0, line - 1), "character": max(0, character)}
        params = {
            "textDocument": {"uri": _uri_for_path(path)},
            "position": pos,
        }
        if action == "definition":
            res = await self._request(server, "textDocument/definition", params, timeout=15.0)
            return _format_locations(res, "definition")
        if action == "references":
            params["context"] = {"includeDeclaration": True}
            res = await self._request(server, "textDocument/references", params, timeout=20.0)
            return _format_locations(res, "references")
        if action == "hover":
            res = await self._request(server, "textDocument/hover", params, timeout=15.0)
            return _format_hover(res)
        if action == "diagnostics":
            uri = _uri_for_path(path)
            # Если файл только что открыли — ждём publishDiagnostics.
            # Если уже был открыт — заново откроем чтобы pyright перепарсил актуальное содержимое с диска.
            #
            # Регистрируем СВОЙ event в списке ДО didClose/didOpen, чтобы не
            # пропустить publishDiagnostics, пришедший сразу после re-open.
            # Свой event на каждый вызов: конкурентные вызовы не затирают друг
            # друга (раньше единственный слот в dict орфанил чужое событие).
            ev = asyncio.Event()
            waiters = server._diag_events.setdefault(uri, [])
            waiters.append(ev)
            try:
                # Force re-open: closeDoc + didOpen с новой версией
                try:
                    if str(path.resolve()) in server._opened:
                        await self._notify(server, "textDocument/didClose", {
                            "textDocument": {"uri": uri},
                        })
                        server._opened.pop(str(path.resolve()), None)
                    await self._ensure_open(server, path)
                except Exception as e:
                    logger.debug("lsp diagnostics re-open failed: {}", e)
                try:
                    await asyncio.wait_for(ev.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    pass
            finally:
                # Удаляем только свой event; чужие ожидающие вызовы не трогаем.
                try:
                    waiters.remove(ev)
                except ValueError:
                    pass
                if not waiters:
                    server._diag_events.pop(uri, None)
            diags = server._diagnostics.get(uri) or []
            return _format_diagnostics(diags, path)
        raise ValueError(f"unknown action {action}")

    # ── shutdown ──

    def shutdown(self) -> None:
        for sid in list(self.servers.keys()):
            try:
                self._submit(self._shutdown_async(self.servers[sid]), timeout=5.0)
            except Exception as e:
                logger.debug("lsp shutdown '{}' error: {}", sid, e)
        self.servers.clear()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        # Дожидаемся завершения loop-потока, чтобы loop.close() произошёл здесь,
        # а не в гонке с GC уже после выхода из shutdown (как у MCPManager).
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("lsp loop thread did not stop within timeout")
        self._thread = None
        self._loop = None

    async def _shutdown_async(self, server: LSPServer) -> None:
        try:
            await self._request(server, "shutdown", {}, timeout=2.0)
        except Exception:
            pass
        try:
            await self._notify(server, "exit", {})
        except Exception:
            pass
        if server._reader_task:
            server._reader_task.cancel()
            try:
                await server._reader_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("lsp[{}] reader task await failed: {}", server.id, e)
        if server._proc:
            if server._proc.stdin is not None:
                try:
                    server._proc.stdin.close()
                except Exception as e:
                    logger.debug("lsp[{}] stdin close failed: {}", server.id, e)
            try:
                server._proc.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                await asyncio.wait_for(server._proc.wait(), timeout=2.0)
            except Exception:
                try:
                    server._proc.kill()
                except (OSError, ProcessLookupError) as e:
                    logger.warning("lsp proc kill failed pid=%s: %s",
                                   getattr(server._proc, "pid", None), e)
            # Явно закрываем subprocess-transport, ПОКА фоновый loop ещё жив.
            # Иначе BaseSubprocessTransport.__del__ дёрнет loop.call_soon уже
            # после loop.close() → "RuntimeError: Event loop is closed" при GC.
            transport = getattr(server._proc, "_transport", None)
            if transport is not None:
                try:
                    transport.close()
                except Exception as e:
                    logger.debug("lsp[{}] transport close failed: {}", server.id, e)
            server._proc = None

    def list_servers_info(self) -> list[dict]:
        out = []
        for s in self.servers.values():
            pid = None
            rss_kb = None
            if s._proc is not None:
                pid = getattr(s._proc, "pid", None)
                if pid:
                    rss_kb = _read_proc_rss(pid)
            out.append({
                "id": s.id, "status": s.status, "error": s.error,
                "root": s.root_path, "command": s.config.get("command"),
                "pid": pid, "rss_kb": rss_kb,
            })
        return out

    def disconnect_by_key(self, key: str) -> None:
        server = self.servers.pop(key, None)
        if not server:
            return
        try:
            self._submit(self._shutdown_async(server), timeout=5.0)
        except Exception as e:
            logger.debug("lsp disconnect_by_key '{}': {}", key, e)


# ── форматирование результатов ─────────────────────────────────

def _format_locations(res: Any, kind: str) -> str:
    if not res:
        return f"{kind}: ничего не найдено"
    if isinstance(res, dict):
        res = [res]
    lines = []
    for loc in res:
        if not isinstance(loc, dict):
            continue
        uri = loc.get("uri") or loc.get("targetUri") or ""
        rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange") or {}
        start = rng.get("start") or {}
        ln = start.get("line", 0) + 1
        ch = start.get("character", 0)
        path = _path_from_uri(uri)
        lines.append(f"{path}:{ln}:{ch}")
    if not lines:
        return f"{kind}: пустой результат"
    return "\n".join(lines)


def _read_proc_rss(pid: int) -> int | None:
    """Linux: читает RSS процесса в килобайтах из /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        return None
    return None


_DIAG_SEVERITY = {1: "ERROR", 2: "WARN", 3: "INFO", 4: "HINT"}


def _format_diagnostics(diags: list[dict], path: Path) -> str:
    if not diags:
        return "Всё корректно ✓"
    lines = [f"{path}: {len(diags)} диагностик"]
    for d in diags:
        sev = _DIAG_SEVERITY.get(d.get("severity"), "?")
        rng = d.get("range") or {}
        start = rng.get("start") or {}
        ln = start.get("line", 0) + 1
        ch = start.get("character", 0)
        msg = (d.get("message") or "").replace("\n", " ").strip()
        src = d.get("source") or ""
        code = d.get("code") or ""
        tag = f"[{src}{':' + str(code) if code else ''}]" if src or code else ""
        lines.append(f"  {sev} {ln}:{ch} {tag} {msg}".rstrip())
    return "\n".join(lines)


def _format_hover(res: Any) -> str:
    if not res or not isinstance(res, dict):
        return "hover: ничего не найдено"
    contents = res.get("contents")
    if contents is None:
        return "[lsp] hover: пустой контент"
    parts: list[str] = []

    def _add(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            val = v.get("value")
            if isinstance(val, str):
                parts.append(val)
        elif isinstance(v, list):
            for item in v:
                _add(item)

    _add(contents)
    text = "\n\n".join(p.strip() for p in parts if p and p.strip())
    return text or "hover: пустой контент"


# ── tools ──────────────────────────────────────────────────────

def _exec_action(call: ToolCall, action: str) -> ToolResult:
    args = call.args or {}
    path = args.get("path") or args.get("file") or ""
    if not path:
        return ToolResult(name=f"lsp_{action}", status="error",
                          output="Не указан path",
                          exit_code=1, command=call.command)
    try:
        line = int(args.get("line", 1))
        character = int(args.get("character", args.get("col", 0)))
    except (TypeError, ValueError):
        return ToolResult(name=f"lsp_{action}", status="error",
                          output="line/character должны быть числами",
                          exit_code=1, command=call.command)
    return LSPManager.instance()._run_action(path, line, character, action)


def execute_lsp_definition(call: ToolCall) -> ToolResult:
    return _exec_action(call, "definition")


def execute_lsp_references(call: ToolCall) -> ToolResult:
    return _exec_action(call, "references")


def execute_lsp_hover(call: ToolCall) -> ToolResult:
    return _exec_action(call, "hover")


def execute_lsp_diagnostics(call: ToolCall) -> ToolResult:
    return _exec_action(call, "diagnostics")


def get_diagnostics_for_path(path: str) -> str | None:
    """Внутренняя обёртка для авто-диагностики после write/patch.

    Возвращает строку с диагностикой если есть проблемы, иначе None.
    Не поднимает исключений — в случае любой ошибки тихо возвращает None.
    """
    try:
        from pathlib import Path as _P
        p = _P(path)
        if not p.is_absolute():
            from tools._paths import resolve_path
            p = resolve_path(path)
        if not p.exists():
            return None
        mgr = LSPManager.instance()
        if not mgr._configs:
            return None
        server = mgr._ensure_server(p)
        if server is None:
            return None
        text = mgr._submit(mgr._action_async(server, p, 1, 0, "diagnostics"), timeout=6.0)
        # Возвращаем только если есть реальные проблемы
        if text and "Всё корректно" not in text:
            return text
        return None
    except Exception as e:
        logger.debug("auto diagnostics failed: {}", e)
        return None


# ── публичный API ──────────────────────────────────────────────

def init_lsp_from_config() -> int:
    """Загружает конфиги enabled-серверов. Серверы стартуют лениво при первом
    запросе по соответствующему расширению. Возвращает кол-во конфигов."""
    from config.lsp import list_servers
    cfgs = list_servers()
    if not cfgs:
        return 0
    LSPManager.instance().init_from_configs(cfgs)
    return len([c for c in cfgs if c.get("enabled", True) and shutil.which(c.get("command", ""))])


def shutdown_lsp() -> None:
    if LSPManager._instance is not None:
        LSPManager._instance.shutdown()