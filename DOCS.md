# Техническая документация necli

> Эта документация описывает внутреннее устройство проекта. Для установки, первого запуска и краткой справки используйте [README.md](README.md).

necli — терминальный AI-агент с интерактивным CLI, headless-режимом и Telegram-мостом. Все фронтенды используют общее ядро агента, API-провайдеров, инструментов и хранения сессий.

Клиент для LLM с прямыми вызовами провайдеров через httpx и входом в ChatGPT через браузерный OAuth: собственные реализации API, гибридный режим инструментов (fenced `:::call` блоки + native function calling), стриминг с инлайн-выполнением tool-блоков, агентный цикл без лимита итераций, до 100 задач на один вызов субагентов с git worktree-изоляцией, DAG-зависимостями, ролями/пресетами и фазовой оркестрацией (phases / items+stages в одном вызове), планировщик, скиллы, нативные движки `.docx` / `.pptx` (создание, правка, инспекция, рендер), долговременная память между сессиями с ручным сохранением фактов основным агентом, инсайты по всем сессиям (`/insights`), MCP, LSP, хуки (совместимые с claude-code), авто-pruning истории, автоочистка `.data/`, headless-режим для CI и зеркало в Telegram.

Python ≥ 3.10. Управление зависимостями — `uv`.

## Quick start

```bash
# Устанавливает зависимости приложения из uv.lock.
uv sync

# Для тестов и статического анализа добавьте инструменты разработки.
uv sync --extra dev

# CLI (интерактивный prompt_toolkit shell)
uv run necli cli --api openai

# Headless (CI / pipe / cron)
printf "сосчитай строки .py" | uv run necli run --quiet --allow-all
```

Ключи API, определения провайдеров и модельные роутеры хранятся в `.data/apis.json` (`providers` / `keys` / `routers`). OAuth-токены ChatGPT вынесены в `.data/chatgpt_auth.json`. Вход, выход и остальные настройки доступны через `/api` и `/models` внутри CLI.

## Содержание

- [Структура проекта](#структура-проекта)
- [Точки входа](#точки-входа)
- [Разработка и проверки](#разработка-и-проверки)
- [Архитектура](#архитектура)
- [Конфигурация и `.data/`](#конфигурация-и-data)
- [API-провайдеры](#api-провайдеры)
- [Агентный цикл](#агентный-цикл)
- [Формат tool calls](#формат-tool-calls)
- [Инструменты](#инструменты)
- [Режимы (agent / planning / swarm)](#режимы-agent--planning--swarm)
- [Планировщик](#планировщик)
- [Сессии, токены, стоимость](#сессии-токены-стоимость)
- [Slash-команды](#slash-команды)
- [Субагенты](#субагенты)
- [Скиллы](#скиллы)
- [Память (memory)](#память-memory)
- [Хуки (hooks)](#хуки-hooks)
- [Сессионные заметки (session notes)](#сессионные-заметки-session-notes)
- [MCP](#mcp)
- [LSP](#lsp)
- [Telegram-мост](#telegram-мост)
- [Headless / CI](#headless--ci)
- [Система разрешений](#система-разрешений)
- [UI и темы (CLI)](#ui-и-темы-cli)
- [Логирование](#логирование)
- [Инсайты (/insights)](#инсайты-insights)

## Структура проекта

```
src/  (точка входа: src/main.py)
├── main.py                       # Click CLI (cli / run); поднимает RLIMIT_NOFILE
├── system_prompt.py              # сборка финального промпта + subagent/MCP/memory блоки
├── planner.py                    # Plan, :::call plan, .plan.md
├── models.py                     # каталог моделей, pricing, context limits
├── logger.py                     # loguru конфигурация (слои, ротация, request_id)
├── _frontmatter.py               # общий парсер YAML-подобного frontmatter
│
├── hooks/                        # хуки (совместимы с claude-code)
│   ├── runner.py                 # исполнение hooks (subprocess/HTTP) + сведение результатов
│   ├── matcher.py                # matcher-обёртки и permission-style фильтр if
│   └── schema.py                 # HOOK_EVENTS, HookSpec, HookOutcome
│
├── prompts/                      # системные промпты по формату tool calls
│   ├── fenced.py                 # fenced-вариант (:::call блоки)
│   └── native.py                 # native function calling вариант
│
├── agent/                        # агентный цикл
│   ├── loop.py                   # run_agent / run_agent_interactive (неограниченные итерации)
│   ├── stream.py                 # LiveStream c инлайн-выполнением tool блоков
│   ├── stream_parser.py          # поиск partial/complete :::call блоков
│   ├── stream_tool_exec.py       # выполнение блоков по мере появления
│   ├── stream_render.py          # composition стрима
│   ├── native_tool_stream.py     # инкрементальный парсер native tool calls из SSE
│   ├── executor.py               # _execute_single + permission checks
│   ├── events.py                 # AgentEventHandler protocol + RichEventHandler
│   ├── context.py                # AgentContext (plan, mode, fs snapshot, ...)
│   ├── messages.py               # build_first_message / nudge / fs delta / session notes
│   ├── sanitizer.py              # очистка ответа модели
│   ├── think.py                  # THINK-блоки
│   ├── working.py                # WorkingRound — живой блок работы (токены, время, анимация)
│   ├── background_render.py      # рендер фоновых задач (BackgroundTaskView/Overlay)
│   ├── subagent.py               # buffer / multiplexer (до 100 задач)
│   ├── subagent_api.py           # запуск sub-сессии (ApiSession per task) + роли/DAG
│   ├── subagent_git.py           # git worktree-изоляция per task
│   ├── subagent_render.py        # рендер прогресса субагентов
│   ├── subagent_display.py       # дисплей-инструменты для панелей субагентов
│   ├── agent_presets.py          # заготовки-роли из .data/agents/<name>/AGENT.md
│   ├── telegram_handler.py / tg_menu.py / tg_format.py
│   ├── fs_watcher.py             # snapshot изменений рабочей директории
│   ├── project_stats.py          # StepTracker для трекинга изменений
│   ├── display.py / diff_render.py / syntax.py / theme_preview.py
│   ├── block_stream.py           # BlockStreamer — поблочный markdown-стрим
│   ├── markdown.py               # markdown-утилиты для рендера
│   ├── render_store.py / render_replay.py  # буфер вывода + Ctrl+O replay
│   ├── result_cache.py           # кэш длинных tool результатов (expand_tool_result)
│   ├── commit_agent.py           # генерация коммита для /commit
│   └── telemetry.py              # turn/round tracking для structured logging
│
├── apis/                         # API-провайдеры и интеграции
│   ├── registry.py               # load_all, get_provider, resolve_api_model
│   ├── agent_adapter.py          # ApiSession, api_send_message, compress, api_recap
│   ├── helper_models.py          # маршруты helper/image моделей
│   ├── router_provider.py        # упорядоченный fallback между provider/model
│   ├── base.py                   # BaseProvider — httpx SSE + native tool calls
│   ├── messages.py               # SystemMessage / HumanMessage / AIMessage / ToolMessage
│   ├── _retry.py                 # throttle/retry поверх non-stream и стрима
│   ├── _context_pruner.py        # pruning старых read/tool-результатов из истории
│   ├── models.py                 # ApiProviderDefinition / ApiModelInfo
│   ├── tool_schemas.py           # OpenAI-style schemas + agent/planning фильтр
│   ├── config.py                 # apis.json / config.json
│   ├── model_discovery.py        # автообнаружение моделей у провайдера
│   ├── mcp_client.py             # MCPManager + регистрация в TOOL_REGISTRY
│   ├── lsp_client.py             # LSPManager + lsp_references/lsp_diagnostics
│   ├── telegram.py               # TelegramBridge (aiogram)
│   └── providers/                # openai_provider / anthropic_provider /
│                                 # google_provider / custom_provider
│
├── commands/                     # точки входа и slash-команды
│   ├── interactive.py            # main loop CLI (включая _maybe_auto_compress)
│   ├── headless.py               # `run` команда (CI)
│   ├── slash.py / slash_handler.py
│   ├── registry.py               # единый реестр slash-команд (метаданные → help/completer)
│   ├── agent_queue.py            # строго последовательная очередь ходов агента
│   ├── interactive_state.py / interactive_status.py
│   ├── permission_prompt.py
│   ├── helpers.py
│   ├── onboarding.py
│   └── menus/                    # agents, api, autoprune, help, helpers, history, insights,
│                                 # lang, lsp, mcp, params, permissions, proxy, settings,
│                                 # skills, stats, telegram, themes, tools, _editor, _style
│
├── config/                       # настройки и пути
│   ├── settings.py               # config.json get/set/cache (+ tool_format_force_native)
│   ├── display.py                # per-block full rendering selection for compact/full views
│   ├── paths.py                  # .data/, sessions/, skills/, memory/, ensure_dirs
│   ├── constants.py              # READ_ONLY_TOOLS, IGNORE_DIRS, Limits
│   ├── data_cleanup.py           # автоочистка мусора из .data при старте (раз в сутки)
│   ├── themes.py                 # 8 встроенных тем + семантические роли
│   ├── permissions.py            # scopes session/process/forever
│   ├── mcp.py / lsp.py / hooks.py / pinned.py
│   ├── ui.py                     # лимиты и подсказки (limits.*)
│   ├── i18n.py                   # переводы интерфейса (en/ru/de/fr/zh)
│   ├── _atomic.py                # atomic_write_text / atomic_write_json
│   ├── _sync.py                  # synchronized decorator, set_enabled helper
│   └── locales/                  # de.py / en.py / fr.py / ru.py / zh.py
│
├── session/                      # сессии и persistence
│   ├── session.py                # Session, _compute_cost, compress_reset
│   ├── storage.py                # save/load/list_sessions/get_statistics
│   ├── message.py / tokens.py / _time.py
│   └── notes.py                  # сессионные заметки (session_notes.md)
│
├── skills/                       # обнаружение и управление скиллами
│   ├── manager.py
│   └── registry.py
│
├── memory/                       # долговременная память агента (см. раздел Память)
│   ├── memdir.py                 # CRUD memory-файлов + format_memory_block/manifest
│   ├── extract.py                # валидация решений модельного аудита памяти
│   ├── cleanup.py                # модельная дедупликация/чистка раз в 3 дня
│   ├── insights.py               # /insights: метрики + анализ моделью → HTML-отчёт + память
│   └── _time.py                  # форматирование timestamps
│
├── docx_engine/                  # нативный движок .docx (OOXML, локальный пакет)
│   ├── parse.py / scan.py        # чтение: блочная модель + byte-exact сканер <w:body>
│   ├── patch.py / generate.py / text_patch.py  # хирургическая сборка итогового .docx
│   ├── blank.py                  # пустой шаблон документа
│   ├── mathml.py                 # LaTeX ⇄ MathML ⇄ OMML-формулы
│   ├── chart.py                  # чарты (+ встроенный xlsx)
│   ├── notes.py / sources.py     # footnotes/endnotes, библиография
│   ├── section.py / theme.py / watermark.py / protection.py
│   ├── ink.py / symbol_fonts.py / xml_utils.py / models.py
│   └── cli.py                    # JSONL-мост для агента
│
├── pptx_engine/                  # нативный движок .pptx (Pure-Python, локальный пакет)
│   ├── __init__.py               # публичный API
│   ├── engine.py                 # редактирование: элементы, слайды, трансформации
│   ├── operations.py             # декларативные операции (JSONL-протокол)
│   ├── parser.py / models.py     # парсинг slide XML → модель
│   ├── render.py                 # render-tree + SVG/PNG-превью
│   ├── archive.py / xmlutil.py   # ZIP-пакет, XML-хелперы
│   └── cli.py                    # агентный CLI (line-delimited JSON)
│
├── tools/                        # все инструменты
│   ├── registry.py               # TOOL_REGISTRY + planning/swarm режимы + hooks
│   ├── parser.py / call_parser.py # парсер fenced :::call блоков
│   ├── shell.py / web_search.py / web_fetch.py / image_search.py
│   ├── subagent.py / subagent_specs.py / skill_tool.py / poll.py / expand_result.py
│   ├── memory_tool.py            # memory: write / list / read / delete
│   ├── file_readers.py
│   ├── arg_validation.py         # алиасы/коэрция/диагностика аргументов tool-call
│   ├── background.py             # фоновый запуск тяжёлых команд (background=true)
│   ├── cancellation.py           # CancellationScope для прерывания инструментов
│   ├── text_utils.py             # truncate_middle — общая обрезка длинного текста
│   ├── _paths.py / _html_unescape.py / json_repair.py / models.py
│   └── file_ops/                 # read.py, write.py, patch.py, grep.py,
│                                 # _fuzzy.py, docx_tool.py (нативный docx),
│                                 # pptx_tool.py (нативный pptx), project_check.py
│
├── ui/                           # терминальный ввод/вывод (prompt_toolkit)
│   ├── shell.py                  # постоянный Application + static/dynamic зоны
│   ├── shell_keys.py             # keyboard-биндинги (ShellKeyBindingMixin)
│   ├── shell_layout.py           # layout, оверлеи, бюджеты (ShellLayoutMixin)
│   ├── shell_output.py           # вывод, динамика, статика, статус (ShellOutputMixin)
│   ├── overlay.py                # базовый контракт Overlay
│   ├── buffer_editing.py         # readline-подобные операции редактирования
│   ├── submissions.py            # типы сообщений из UI
│   ├── text_layout.py            # word-wrap, clip, WordWrapProcessor
│   ├── terminal.py               # определение глубины цвета, term_size
│   ├── rows.py                   # RowGroup для строк под рамкой
│   ├── rendering.py              # RichBridge, ansi_rows
│   ├── overlays.py               # интерактивные виджеты нижней зоны
│   ├── prompt.py                 # clipboard/images/echo поверх Shell
│   ├── completer.py              # slash + файловый автокомплит
│   ├── menu.py / poll.py / file_context.py
│   ├── clipboard.py / clipboard_copy.py / formatting.py
│   ├── _filters.py / _emoji_width.py / _keyreader.py
│   ├── focus.py / notifications.py
│   └── terminal_title.py
│
└── .data/                        # рантайм-состояние (см. раздел Конфигурация)
```

## Точки входа

`src/main.py` — Click-группа с командами. Основной способ запуска после `uv sync` — `uv run necli <cmd>`; для запуска из исходного дерева также поддерживается `uv run python src/main.py <cmd>`:

| Команда | Назначение |
|---|---|
| `cli` | Основной TUI на постоянном prompt_toolkit Application (без `rich.live.Live` в пути отрисовки). |
| `run` | Headless: один проход агента, результат в stdout, exit code 0/1/2. |

Опции `cli`:

| Флаг | Назначение |
|---|---|
| `--api, -A` | Активировать провайдера на этот запуск (`--api openai`). |
| `--model, -m` | Модель (id или display name). |
| `--workdir, -w` | Рабочая директория (по умолчанию — `cwd`). |
| `--resume, -r` | Восстановить сессию по id или префиксу. |

При старте `src/main.py` поднимает `RLIMIT_NOFILE` до 8192 (httpx-стримы + множество открытых файлов сессий быстро упираются в дефолт 1024 на Linux).

Опции `run` (см. `commands/headless.py`): `--model/-m`, `--workdir/-w`, `--api/-A`, `--json`, `--full-json`, `--quiet/-q`, `--timeout`, `--allow-all`. `stdin` подхватывается, если не tty. Exit code 0/1/2. По завершении в сокращённом режиме на stderr печатается сводка `✓ Worked <время> ⎿ N⟳ · M🛠 · ↑in ↓out`; `--full-json` выводит на stdout полный отчёт (все ответы модели и tool-вызовы с аргументами и выводом, каждое событие один раз — без дублирования истории) и взаимно исключён с `--json`.

## Разработка и проверки

Зависимости и инструменты разработки описаны в `pyproject.toml`, а точные версии фиксирует `uv.lock`. Полный набор проверок должен выполняться в синхронизированном окружении:

```bash
uv sync --extra dev
uv run ruff format --check src tests
uv run ruff check src tests --no-cache
uv run pytest -q
```

Форматер и линтер применяются ко всем модулям и тестам. Тестовая конфигурация автоматически добавляет `src/` в путь импорта и включает поддержку `asyncio`; вручную задавать `PYTHONPATH` и устанавливать плагины для тестов не требуется.

## Архитектура

```
┌────────────┐  user input   ┌──────────────────┐
│ ui/shell   │ ────────────► │ commands/        │
│ (PT App)   │               │ interactive.py   │
└────────────┘               └──┬───────────────┘
        ▲                       │ /slash → commands/slash_handler.py
        │ stream chunks         ▼
┌──────────────────┐    ┌──────────────────────┐
│ agent/stream.py  │ ◄──┤ agent/loop.py        │
│ LiveStream       │    │ run_agent_interactive│
│ inline tool exec │    └──┬───────────────────┘
└──┬───────────────┘       │ api_send_message()
   │ tool calls            ▼
   ▼                ┌──────────────────────┐
┌──────────────────┐│ apis/agent_adapter   │
│ tools/registry   ││ ApiSession + msgs    │
│ TOOL_REGISTRY    │└──┬───────────────────┘
│ + MCP / LSP      │   │ BaseProvider (httpx SSE)
└──────────────────┘   ▼
                ┌──────────────────────┐
                │ apis/providers/...   │
                │ openai/anthropic/    │
                │ google/custom_http   │
                └──────────────────────┘
```

Два фронтенда поверх одного ядра (`agent/` + `apis/` + `tools/` + `session/`):

- `commands/interactive.py` — TUI с постоянным prompt_toolkit Application (`ui/shell.py`), последовательная очередь ходов агента (`commands/agent_queue.py`).
- `apis/telegram.py` + `agent/telegram_handler.py` — Telegram-мост.

Собственные классы сообщений (`SystemMessage` / `HumanMessage` / `AIMessage` / `ToolMessage` и `AIMessageChunk` с `__add__`) определены в `apis/messages.py`. Общая логика стрима, retry/throttle, нативных tool calls и multimodal-вложений — в `apis/base.py:BaseProvider`. Провайдеры наследуются от него: `openai_provider`, `anthropic_provider`, `google_provider`, `custom_provider`.

## Конфигурация и `.data/`

Вся персистентность лежит в `.data/` рядом с проектом (`config/paths.py`; базовый каталог — `.data` рядом с кодом, либо `NECLI_HOME`; в frozen-режиме — `~/.necli`):

```
.data/
├── config.json                # основной конфиг (config/settings.py)
├── apis.json                  # провайдеры, ключи и роутеры (providers / keys / routers)
├── chatgpt_auth.json          # OAuth-токены ChatGPT (0600)
├── ui.json                    # override эмодзи/лейблов/цветов инструментов + лимиты
├── hooks.json                 # хуки (см. раздел Хуки)
├── mcp_servers.json           # MCP-сервера
├── lsp_servers.json           # LSP-сервера (опционально, есть дефолты)
├── pinned_sessions.json       # закреплённые сессии (не удаляются автоочисткой)
├── .last_cleanup              # маркер последней автоочистки .data (раз в сутки)
├── history                    # история ввода prompt_toolkit
├── clipboard_images/          # вставленные через Ctrl+P изображения
├── uploads/                   # кэш загруженных картинок (напр. из Telegram)
├── subagents/<run-id>/        # run-директории субагентов (shared.md, worktrees)
├── agents/<name>/AGENT.md     # заготовки-пресеты субагентов
├── memory/                    # долговременная память проекта + _global (см. раздел)
├── insights/report-*.html     # HTML-отчёты команды /insights
├── skills/<name>/SKILL.md     # скиллы (см. раздел)
└── sessions/<id>/
    ├── history.json           # полные сообщения сессии
    ├── summary.json           # агрегаты cost/tokens
    ├── .plan.md               # активный план (если есть)
    └── session_notes.md       # сессионные заметки (см. раздел)
```

**Автоочистка `.data/`** (`config/data_cleanup.py`) запускается тихо в фоне при старте, не чаще раза в сутки (маркер `.last_cleanup`), с безопасной retention-политикой:

- сессии старше 30 дней — кандидаты на удаление, но последние 100 и все pinned сохраняются;
- пустые сессии-директории удаляются;
- `subagents/` старше 14 дней;
- `clipboard_images/`, `uploads/` старше 7 дней;
- корневой мусор `_clean_root_junk()`: `_git_stats.py`, `api_providers.json`, `diff_target.txt`, `docx_reference.docx`.

Конфиги, реестры, `agents/`, `skills/`, `memory/` не трогаются.

Ключевые поля `config.json` (см. `config/settings.py`):

| Поле | Назначение |
|---|---|
| `active_api`, `active_api_model` | Текущий провайдер и модель. |
| `tool_permissions` | Постоянные разрешения инструментов. |
| `theme`, `theme_custom` | Активная тема и переопределение ролей. |
| `telegram_bot_token`, `telegram_chat_id`, `telegram_enabled` | Telegram-мост. |
| `think_enabled` | Глобальный THINK-режим (рассуждения вслух). |
| `notifications_enabled` | Desktop-уведомления о завершении долгого хода (дефолт `True`). |
| `temperature`, `max_tokens` | Generation params. |
| `tool_format_force_native` | Глобальный переключатель native/fenced (дефолт `True`). |
| `disabled_tools` | Список отключённых пользователем инструментов. |
| `emoji_width` | 0 — Rich считает emoji как 2 cell (дефолт), 1 — как 1 cell. |

Провайдеры и ключи живут в `.data/apis.json` (поля `providers` / `keys`), миграция из старых `api_providers` / `api_keys` в `config.json` автоматическая. OAuth-токены ChatGPT хранятся отдельно в `.data/chatgpt_auth.json`, записываются атомарно с правами `0600` и не попадают в ротацию API-ключей. Доступ к обычным настройкам — `config.get(key, default)` / `config.set_value(key, value)`. Словарь кэшируется, мутации идут только через `set_value`.

## API-провайдеры

Провайдер описывается `ApiProviderDefinition` (`apis/models.py`). Загрузчик `apis/registry.py`:

1. Читает встроенные шаблоны из `apis/definitions/*.json`, если каталог присутствует.
2. Поверх накладывает пользовательские из `.data/apis.json["providers"]` (ключи — в `["keys"]`); пресеты для онбординга — `commands/onboarding.py:_PROVIDER_PRESETS`.
3. По полю `type` выбирает фабрику: `chatgpt_provider`, `openai_provider`, `anthropic_provider`, `google_provider` или `custom_provider`. Обычные OpenAI-совместимые провайдеры используют Chat Completions через `BaseProvider`; ChatGPT использует Responses endpoint и OAuth bearer.

`apis/chatgpt_auth.py` реализует OAuth 2.0 Authorization Code + PKCE. Вход расположен отдельной первой строкой главного меню `/api`, а не внутри добавления пользовательского провайдера. ChatGPT исключается из общего списка: после входа эта же верхняя строка становится `ChatGPT OAuth` и открывает сокращённое меню «использовать / модели / выйти». При каждой новой попытке прежний callback этого процесса принудительно останавливается до повторного bind на `127.0.0.1:1455`. Браузер открывает `auth.openai.com`, callback проверяет `state`, после чего access/refresh token сохраняются отдельно. Access token обновляется заранее и повторно после первого HTTP 401. `apis/providers/chatgpt_provider.py` преобразует внутренние сообщения и native function tools в Responses input items, передаёт `ChatGPT-Account-Id` и преобразует Responses SSE обратно в `AIMessageChunk`. Встроенные Sol, Terra и Luna имеют официальный контекст 1,05 млн токенов и стандартные short-context API-цены за 1 млн токенов; это справочная оценка, поскольку OAuth-запросы расходуют лимит подписки, а не оплачиваются как API-вызовы. `apis/chatgpt_usage.py` читает subscription rate limits из `backend-api/wham/usage`, определяет недельное окно по его длительности и кеширует оставшийся процент. Обновление выполняется в фоне при запуске и после открытия `/api` или `/models`, не задерживая первый рендер меню, а также после каждого завершённого ответа. Один кеш используют верхняя OAuth-строка `/api`, секция ChatGPT в `/models` и строка статуса над вводом; подписка UI на обновления перерисовывает статус после завершения фонового запроса.

Инстансы кэшируются по `(provider_id, model_id, kwargs)` — kwargs включены в ключ, иначе каждый вызов с параметрами плодил бы новый инстанс со свежим `session_id` и сбивал prompt-cache. `reload_providers()` сбрасывает кэш — нужен после правок через `/api`.

`/helpers` хранит независимые пары `provider/model` для helper- и image-запросов. Helper-модель обслуживает изолированные one-shot операции (`compress`, recap, извлечение памяти, insights), не меняя основную `ApiSession`. Если image-модель задана, `api_send_message` сначала отправляет ей все изображения (включая результаты tools), получает подробное описание сцены, текста, персонажей и интерфейса, а основной модели передаёт только блок `<image_descriptions>`. Описание сохраняется рядом с attachment и восстанавливается как текст после resume. Пустая или ставшая невалидной настройка использует основную модель.

Пресеты онбординга (`commands/onboarding.py`): chatgpt (OAuth), openai, anthropic, google, openrouter, groq, xai, ollama. Любой другой OpenAI-совместимый эндпоинт или свой шлюз добавляется через `/api` или правкой `.data/apis.json`.

Поля определения: `default_headers` (произвольные HTTP-заголовки) и `extra` — шлюзовые настройки без хардкода в коде: `append_query`, `session_id_header`, `billing_header`, `inject_metadata`, `use_aiohttp`, `system_as_first_message`, `use_bearer_auth`, `prompt_cache`, `extra_body`, `reasoning_models`.

На провайдера можно повесить несколько ключей (список в `apis.json["keys"]`, каждому — имя, баланс, RPM-лимит) — при rate-limit запрос автоматически повторяется со следующим ключом (ротация). Баланс ключа уменьшается после каждого запроса (`spend_usage`).

### Модельные fallback-роутеры

Роутер — именованный упорядоченный список пар `provider_id` / `model_id`, хранящийся в `.data/apis.json["routers"]`. CRUD и нормализация записей находятся в `apis/config.py` (`list_routers`, `save_router`, `move_router_route`, `remove_router`). UI в `commands/menus/routers.py` доступен через пункт управления роутерами в `/models` и позволяет:

- создать роутер из двух или более моделей включённых провайдеров, включая модели разных провайдеров;
- менять состав, порядок fallback-маршрутов и удалять неактивный роутер;
- фильтровать модели по имени, id или провайдеру. В multi-select фокус списка и поиска разделён: `Tab` переключает фокус, `Space` отмечает модель в списке и вводит пробел только в поле поиска.

`apis/registry.py:_load_routers_definition` публикует включённые роутеры как модели виртуального провайдера `routers`. Контекстное окно роутера равно минимуму среди его доступных моделей; цены для каталога берутся у первой доступной модели.

`apis/router_provider.py:RouterProvider` начинает **каждый** API-запрос с первого маршрута. После исчерпания внутренних retry модели любая ошибка **до первого stream-chunk** переключает запрос на следующий маршрут. Если часть ответа уже отдана, fallback не выполняется: ошибка пробрасывается, чтобы не склеивать ответы двух моделей. Для non-stream `ainvoke` переключение идёт при любом исключении. Отмена `asyncio.CancelledError` никогда не триггерит fallback.

Баланс роутера — сумма балансов уникальных участвующих провайдеров. `RouterProvider` запоминает фактически сработавший provider, поэтому `spend_usage` списывает стоимость только с реально использованного ключа.

Каждая модель: `id`, `display_name`, `context_window`, `input_price` / `output_price` (USD за 1M токенов). Цены используются в `session/session.py:_compute_cost`: при наличии реального `usage` от провайдера — он, иначе fallback на tiktoken-оценку.

## Агентный цикл

`agent/loop.py` содержит две реализации над одним и тем же `apis.agent_adapter.api_send_message`:

- **`run_agent_interactive`** — основной цикл с `LiveStream` (`agent/stream.py`). Стримит ответ, парсит `:::call <tool> ... call:::` блоки по мере поступления, выполняет инлайн через `agent/stream_tool_exec.py`. После каждой итерации скармливает `ToolResult`'ы модели как новое сообщение. Итерации не ограничены.

- **`run_agent`** — headless-вариант без Rich Live. Используется в `commands/headless.py`.

### WorkingRound (живой блок работы)

`agent/working.py` — единый живой блок, охватывающий весь пользовательский ход: все ответы модели и любое количество вызванных ею инструментов. Внутренние API-запросы только обновляют живой кадр; в scrollback он фиксируется один раз при завершении хода.

Показывает:
- Анимированный заголовок «Working» с shimmer-эффектом и таймером.
- Число запросов к модели (`⟳`), вызовов инструментов (`🛠`), токены (`↑input ↓output`).
- Долгий выполняемый инструмент — отдельная живая строка `🛠 shell · <команда> · 12s` в динамической зоне над Working (`agent/executor.py`, ключ `"tool"`): поднимается через 1.5 с исполнения и гасится перед печатью итогового статичного блока. Выше неё живёт свёрнутая панель плана (`agent/plan_panel.py`, ключ `"plan"`; см. раздел Планировщик).
- Если ход агента завершён, но фоновая работа (background-задачи/субагенты) ещё идёт — заголовок переключается на статичный `⏳ Waiting` вместо вечного shimmer; раунд продолжает жить и просыпается при продолжении по фоновому результату.
- При завершении — итоговая строка `✓ Worked 12s` (или `■ Interrupted` / `■ Stopped`).
- Сводка персистируется в сессию как сообщение с ролью `"worked"` и сохраняется в `RenderStore` для Ctrl+O replay.

### Background-задачи

`tools/background.py` + `agent/background_render.py` — фоновое выполнение shell-команд:

- `background=true` в аргументах `shell` запускает команду в daemon-потоке.
- Агент сразу получает job-id и продолжает работу.
- Завершённые задачи доставляются модели как уведомления через `drain_finished_results()`.
- `BackgroundTaskView` рисует живую строку под рамкой, `BackgroundTaskOverlay` — полноэкранный просмотр с автоскроллом.

### Native tool stream

`agent/native_tool_stream.py` — инкрементальный парсер native tool calls из SSE-стрима:

- Состояния: `COLLECTING → SEALED → EXECUTED`.
- Вызов запечатывается (SEALED) сразу при получении валидного JSON-объекта аргументов, не дожидаясь следующего индекса или `finish_reason`.
- Поддерживает как delta-фрагменты, так и кумулятивные (некоторые прокси шлют весь JSON целиком на каждом чанке).
- Запечатанные вызовы исполняются немедленно через `_schedule_native_call`, не дожидаясь конца стрима модели.

### Ключевые детали

- `AgentContext` (`agent/context.py`) хранит план, рабочую директорию, mode (`agent | planning | swarm`), event-handler, snapshot файлов из `agent/fs_watcher.py`, `step_tracker` (`agent/project_stats.py`), счётчик nudge и флаги прерывания.
- На каждый раунд `agent/messages.build_first_message` / `_build_result_message` дополняет сообщение блоком плана, сессионными заметками и трекингом изменений ФС.
- Авто-продолжение: подозрение на обрыв (`is_likely_truncated`) → `CONTINUE_MESSAGE` («продолжай»); ошибка прокси (`is_api_proxy_error`) → повтор.
- Авто-компрессия истории в `commands/interactive._maybe_auto_compress` срабатывает при `context_tokens / get_context_limit(model) ≥ 0.90`.
- События — через интерфейс `AgentEventHandler` (`agent/events.py`). Дефолт — `RichEventHandler`; при активном TG-мосте оборачивается `TelegramEventHandler`.
- Соседние `read`/`grep` одного батча сливаются в один компактный блок `show_scan_combined` (`agent/display.py`); fenced- и native-стримы накапливают такую скан-фазу до следующего другого инструмента или конца ответа. Свёрнуто — строка-счётчик «N чтений/поисков», Ctrl+O разворачивает в список путей со сводками; порог — от двух скан-вызовов, `single_line_tools` отключает слияние.
- Токены везде рисуются со стрелкой вверх первой: `↑input ↓output` (Working, статус-строка, replay, headless-сводка).
- Прерывания: `Ctrl+C` → `ctx.interrupted = True`, стрим закрывается, частичный ответ сохраняется как `[Прервано]`.
- THINK-блоки (`agent/think.py`) парсятся и рисуются отдельной панелью. В сессию сохраняются как `Message.thoughts` отдельно от основного `content`.

## Формат tool calls

`apis/agent_adapter.api_send_message` поддерживает два канала, одновременно:

**Fenced блоки в тексте** — асимметричные маркеры:

```
:::call <tool> [attrs]
...body...
call:::
```

Парсятся `tools/call_parser.py`. Три формата:

1. **JSON-инструменты** — body это JSON.
2. **Контентные** (`create_file`) — `path="..."` в шапке, body — сырой контент.
3. **`patch_file`** — секции `--- FIND --- / --- REPLACE --- / --- INSERT ---` или атрибут `delete_lines`.

**Native function calling.** Глобальный единый переключатель `tool_format_force_native` (дефолт `True`, `config/settings.py`; команда `/tool_format`): `True` → native function calling для всех провайдеров, `False` → fenced `:::call`. В native-режиме `BaseProvider` биндит JSON-схемы из `apis/tool_schemas.py`; полученные `tool_calls` конвертируются в текстовые fenced-блоки и проходят через тот же парсер — UI одинаковый, но результаты возвращаются модели как структурные `ToolMessage` (не как текстовый транскрипт). Системный промпт полностью изолирует режимы: в native-варианте нет ни одного упоминания fenced-синтаксиса (промпты — `prompts/native.py` / `prompts/fenced.py`).

Особенности:

- При пустых `args` после стрима (баг ряда прокси) делается fallback non-stream запрос за корректным JSON.
- Прокси html-эскейпят кавычки/угловые скобки в SSE; декодирование — единый канонический модуль `tools/_html_unescape.py`, используется в `tools/call_parser.py`, `apis/agent_adapter.py`, `agent/display.py`.
- `usage` (`input`, `output`, `reasoning`) пробрасывается из ответа провайдера в `Message.usage`.
- Длина fence для контентных инструментов выбирается динамически.

## Инструменты

Единый реестр — `tools/registry.py:TOOL_REGISTRY` (строки 43-63). JSON-схемы для native — `apis/tool_schemas.py`. Одинаковые имена работают и в fenced, и в native режиме.

Полный список (`TOOL_REGISTRY`):

| Категория | Инструменты |
|---|---|
| Shell | `shell` |
| Чтение | `read`, `grep` |
| Запись / правка | `create_file`, `patch_file` |
| Офис | `docx` (create / edit / inspect — нативный движок, формулы и таблицы), `pptx` (create / edit / inspect / render / validate — слайды, фигуры, таблицы, изображения) |
| Сеть | `web_search`, `web_fetch`, `image_search` |
| Мета | `subagent`, `skill`, `poll`, `expand_tool_result` |
| Память | `memory` с `action=write/list/read/delete` (долговременная память проекта, см. раздел Память) |
| LSP | `lsp_references`, `lsp_diagnostics` |
| MCP | `mcp__<server_id>__<tool_name>` (динамически после `init_mcp_from_config`) |
| Control-tools | `plan` (чеклист задачи, не исполняет код), `think` (рассуждение вслух — только при включённом THINK-режиме) |

`plan` и `think` — это control-tools: попадают в JSON-схемы (`apis/tool_schemas.py`), но кода не выполняют, а лишь ведут чеклист / рисуют мысль в UI. `think` подмешивается в схемы только при активном THINK-режиме.

### Существенные детали

- **`read`** — поддерживает `.docx`, `.pptx`, `.pdf`, изображения, csv/tsv, Excel. Лимит: `MAX_LINES = 1000` (`tools/file_ops/read.py`). Кэш прочитанных диапазонов с инвалидацией при изменении файла.
- **`grep`** (`tools/file_ops/grep.py`) — поиск по содержимому с regex, автоматически исключает зависимости/кэши/скрытые директории.
- **`patch_file`** — `find/replace` (одиночный или массив `patches`), `line + insert` для вставки, `delete_lines="10-15"`. Fuzzy-матч с warning'ом (`tools/file_ops/_fuzzy.py`).
- **`docx`** (`tools/file_ops/docx_tool.py`) — нативный движок `docx_engine` (локальный пакет проекта). Действия: `create` / `edit` / `inspect` / `help` (темы `blocks` / `runs` / `edit` / `options`). Конвертер формул `mathml.py` обрабатывает базовый LaTeX, включая дроби, корни, индексы, матрицы, `array`/`aligned`/`gathered`, акценты, математические алфавиты (`\\mathbb`, `\\mathcal`, `\\mathfrak`, `\\mathbf`, `\\mathit`, `\\mathsf`, `\\mathtt`), пределы и частые операторы. Пользовательские макросы и пакетные команды LaTeX не поддерживаются.
- **`pptx`** (`tools/file_ops/pptx_tool.py`) — нативный движок `pptx_engine` (локальный пакет проекта). Действия: `create` / `edit` / `inspect` / `render` (SVG/PNG-превью) / `validate` / `help`.
- **`shell`** — `cd` и `&&` / `||` явно запрещены парсером (`tools/shell.py`). Тяжёлые/долгие команды можно запускать в фоне: `background=true` в аргументах (`tools/background.py`) — задача исполняется отдельно, результат приходит уведомлением по завершении.
- **`web_search`** — DuckDuckGo (через `ddgs`) и/или прямой fetch URL через `trafilatura`. Результаты кэшируются.
- **`web_fetch`** — отдельный fetch содержимого одного или нескольких URL (извлекает текст, при необходимости raw HTML).
- **`image_search`** — поиск картинок в сети и скачивание их в `assets/images`.
- **`poll`** — запрос к пользователю с до 4 вариантами. В headless автоматически отказывает.
- **`expand_tool_result`** — длинные output'ы усекаются с маркером `expand via :::call expand_tool_result {"id": "..."}`; модель просит полный текст по id. Кэш `agent/result_cache.py` (FIFO, в памяти процесса).
- LSP-tools — только `lsp_references` / `lsp_diagnostics` (см. раздел LSP).

**Валидация аргументов** — `tools/arg_validation.py` сверяет args со схемой до вызова handler'а: алиасы имён параметров (`source→path`, `new_name→new_path`, `cmd→command`), коэрция типов (`"42"`→`42`, `"true"`→`True`), точные диагностики обязательных полей и enum.

**Отмена** — `tools/cancellation.py` предоставляет `CancellationScope` для прерывания синхронных инструментов (shell, background-задачи) из субагентов.

### Read-only / planning-mode

`tools/registry.py` экспортирует `PLANNING_TOOLS` / `SWARM_TOOLS` (канонический `READ_ONLY_TOOLS` — из `config/constants.py`, см. раздел Режимы). В `planning` mode разрешены только они + `plan`.

### Лимиты вызовов

- Итерации главного агента не ограничены — цикл работает, пока задача не завершена или не прервана. Стержневые ограничители субагентов: до 100 итераций (`MAX_SUBAGENT_ITERATIONS`), активный контекст 1M токенов (`MAX_SUBAGENT_CONTEXT_TOKENS`) и общий wall-clock бюджет 2 ч (`MAX_SUBAGENT_WALL_SEC`) — висящий провайдер с ретраями больше не тянет волну бесконечно. Все — в `config/constants.py`.
- До 100 задач на один вызов `subagent` (`agent/subagent.py:80`), конкурентность внутри волны — `subagent.max_concurrency = 12` (`config/ui.py:224`).
- Каждый вызов выполняется отдельно `agent/executor._execute_single` с тиканием спиннера и трекингом изменений ФС.

## Режимы (agent / planning / swarm)

Переключение по Tab в prompt (`commands/interactive.py _toggle_mode`; в Telegram — `/menu` → mode, `agent/tg_menu.py`, `modes = [agent, planning, swarm]`).

| Режим | Иконка | Что разрешено |
|---|---|---|
| `agent` | 🚀 | Полный набор инструментов. Дефолт. |
| `planning` | 🧠 | Read-only + планирование: `read`, `grep`, `lsp_references`, `lsp_diagnostics`, `memory` (только list/read), `poll`, `skill`, `web_search`, `web_fetch` + control-tool `plan`. Любая попытка вызвать write/shell/etc. возвращает `build_blocked_result`. |
| `swarm` | 🔮 | `planning` + `shell` + `subagent` — режим для оркестрации параллельных субагентов. |

Составы — из кода (`src/config/constants.py:41`, `src/tools/registry.py:213-214`):

- `READ_ONLY_TOOLS = {read, grep, lsp_references, lsp_diagnostics, memory}`; для `memory` в planning разрешены только `action=list/read`.
- `PLANNING_TOOLS = READ_ONLY_TOOLS | {poll, skill, web_search, web_fetch}`.
- `SWARM_TOOLS = PLANNING_TOOLS | {shell, subagent}`.

При переключении режима в первое сообщение нового раунда инжектится notice о смене режима. Аналогично `/think` — notice включения/выключения THINK-режима.

## Планировщик

`planner.py` — пошаговые планы в духе Claude Code Plan Mode.

`Plan` хранит `goal` и список `PlanStep(status=pending|in_progress|done|skipped, notes)`.

Модель управляет планом через специальные блоки:

```
:::call plan
{"action": "create", "goal": "...", "steps": [{"title": "..."}, ...]}
call:::
```

Поддерживаемые действия: `create`, `update` (по `step` / `index` / `title`), `add_step`, `remove_step`. **Минимум 3 шага** (`planner.py:286`).

После каждого ответа `agent/loop` применяет команды плана и обновляет `.plan.md` в директории сессии. В TUI план живёт в динамической зоне Shell (`agent/plan_panel.py`, ключ `"plan"`): свёрнутая панель показывает заголовок, текущий и следующий шаг над блоком Working, `/plan` разворачивает её до полного списка и сворачивает обратно. По завершении плана один раз печатается финальная выполненная панель `render_plan_panel` в статичный scrollback, зона гаснет; при `/new` / `/branch` / смене сессии панель сбрасывается. Вне TUI (headless/TG) событие уходит в прежний статичный `show_plan_update`.

`LiveStream` обрабатывает блоки plan в стриме: показывает прогресс-бар (`▮▮▯▯ 2/4`) прямо во время ответа.

Окно `prev/current/next` инжектится в контекст следующего сообщения через `Plan.render_for_context`.

При завершении (`is_complete`) `.plan.md` удаляется, при загрузке (`load_plan_file`) восстанавливается из markdown.

Если модель долго работает без tool-вызовов, но план не завершён — посылается nudge с напоминанием.

## Сессии, токены, стоимость

`session/session.py:Session`:

- `id` — изначально техническая метка `YYYYMMDD_HHMMSS_<uid>`. При первом user-сообщении переименовывается в `<slug>_YYYYMMDD_HHMMSS` (slug — первые 20 «слов»-символов). Директория двигается через `shutil.move`.
- `messages: list[Message]` с ролями `user | assistant | system | tool_result | worked | tool_call`.
- `_compressed_stats` — накопленные сообщения, input/output-токены и стоимость до `/compress`; они сохраняются в шапке после сжатия, тогда как прогресс контекста показывает только текущую историю.

### Стоимость

`_compute_cost` приоритетно использует реальный `usage` от провайдера (`input`, `output`, `reasoning`). Если usage нет — fallback на tiktoken через буфер input-сообщений. Стоимость считается напрямую: `input * input_price + output * output_price`, без учёта prompt-кэша.

### Авто-компрессия

`commands/interactive._maybe_auto_compress`:

- порог `0.90` от `get_context_limit(model)`;
- каскад: сначала пробуется инкрементальная компрессия (`_handle_compress_incremental` — сжать старое, последние раунды оставить дословно); если раундов мало — полный `compress_reset`;
- `compress_reset` сериализует историю, отправляет в активную модель через `api_compress_history`, кладёт сжатый текст обратно как `system` сообщения;
- защита от повторного срабатывания через `_auto_compress_last_msg`;
- при активном Telegram-мосте шлёт уведомление об автокомпрессии.

### Persistence

`session/storage.py`:

- `save()` пишет `history.json` (полное содержимое) и `summary.json` (агрегаты).
- `list_sessions()` собирает превью из `summary.json`, пересчитывает `total_cost` по актуальным ценам — старые сессии не «протухают» при изменении прайс-листа.
- `get_global_statistics()` / `get_period_statistics(days)` — данные для `/stats`.
- `load(prefix)` поддерживает префикс/подстроку id для `--resume`.

### Pruning контекста перед отправкой

`apis/_context_pruner.py` обрезает старые tool-результаты из истории перед запросом, экономя токены без потери актуального контекста. Работает в обоих форматах (text-mode блоки и native `ToolMessage`). Триггеры вытеснения:

- **(A)** файл перезаписан в более позднем раунде;
- **(B)** тот же путь прочитан позже (дедуп);
- **(C)** крупное чтение старше `_KEEP_RECENT_ROUNDS=4` раундов;
- **(D)** hard-cap — любое чтение/вывод старше `_HARD_EVICT_ROUNDS=10`;
- **(E)** range-dedup — уже прочитанный диапазон строк не отправляется повторно, если файл не менялся.

Свежий раунд не трогается. Вместо контента ставится маркер `[content evicted to save tokens — ...]` с подсказкой перечитать.

Дополнительно:

- **Tool folding** — выводы тяжёлых инструментов (`shell`, `web_search`, `web_fetch`, lsp_*) старше `tool_fold_rounds` раундов сворачиваются в компактную заметку.
- **Skill eviction** — текст SKILL.md вытесняется после `_SKILL_EVICT_ROUNDS=5` раундов (синхронно с окном активности скилла).
- **Runtime tool results** — блоки `<runtime_tool_results>` старше 3 последних сжимаются в summary.

## Slash-команды

Диспетчер — `commands/slash.py:_handle_slash` → `SlashResult`. Состояние и побочные эффекты обрабатывает `commands/slash_handler.py`. Метаданные команд (имя, категория, help-строка) — единый реестр `commands/registry.py`; `/help` и автокомплит `ui/completer.py` берут список оттуда автоматически.

**Правило мест:** новая команда обязана быть в `commands/registry.py` (метаданные → help+completer подхватываются сами) и в `commands/slash.py` (диспетчер `_handle_slash`); если она меняет состояние / запускает async — ещё и в `commands/slash_handler.py`. См. `AGENTS.md`.

Полный актуальный список (`commands/registry.py`, категории из `CATEGORIES`):

| Команда | Категория | Что делает |
|---|---|---|
| `/new` | session | Новый чат: чистит сессию и `ApiSession`, сбрасывает session-level разрешения и активные скиллы. |
| `/branch` | session | Ответвить текущий чат в новую сессию (копия истории). |
| `/commit` | session | Сгенерировать коммит через `agent/commit_agent.py` и закоммитить. |
| `/sessions` | session | Меню сохранённых сессий с cost/tokens preview. |
| `/compress` | session | Сжать историю через helper-модель (или основную как fallback), сохранить бэкап. |
| `/reflect` | session | Рефлексия: модель анализирует сессию и предлагает обновить `AGENTS.md`. |
| `/api` | model | Меню провайдеров: добавление/правка, ключи, активная модель. Курсор сразу на строке активного провайдера — и при входе, и при возврате из деталей. |
| `/models` | model | Поисковый picker моделей всех включённых провайдеров; создание и настройка fallback-роутеров. |
| `/autoprune` | model | Меню авто-pruning контекста (`commands/menus/autoprune.py`). |
| `/proxy [URL\|off]` | model | Установить/сбросить HTTP(S)/SOCKS-прокси для API-вызовов. |
| `/permissions` | tools | Allow/deny инструментов на уровне session / process / forever. |
| `/tools` | tools | Список инструментов с описаниями и переключателями. |
| `/skills` | tools | Меню скиллов: список / создание / добавление / удаление. |
| `/agents` | tools | CRUD заготовок-пресетов субагентов (`.data/agents/<name>/AGENT.md`). |
| `/themes` | display | Выбор темы и кастомизация ролей. |
| `/plan` | display | Развернуть/свернуть живую панель плана (свёрнутая — текущий и следующий шаг; финальный «выполнен» план печатается один раз в статик). |
| `/think` | display | Toggle THINK-режима (рассуждения вслух); toggle `think_enabled`. |
| `/tool_format` | display | Toggle глобального native function calling (`tool_format_force_native`) — иначе fenced. |
| `/help` | misc | Справка (группировка по категориям). |
| `/settings` | misc | Группированные настройки: helper/image-модель и params, MCP, LSP, язык. |
| `/stats [N]` | misc | Интерактивная статистика за N дней + общая, с вкладками (session / hands / models / tools / history). |
| `/insights` | misc | Анализ всех сессий → HTML-отчёт + извлечение фактов в память (см. раздел Инсайты). |
| `/copy [N]` | misc | Скопировать последние N ответов ассистента в буфер обмена (по умолчанию 1). |
| `/tg` | misc | Telegram-мост: токен / чат / тест / on-off. |

## Субагенты

`tools/subagent.py` + `agent/subagent.py` + `agent/subagent_api.py` + `agent/subagent_git.py` + `agent/subagent_render.py` + `agent/subagent_display.py`.

До 100 задач в одном вызове (`agent/subagent.py:80`). Каждая — отдельная `ApiSession`, изолированный контекст, свой stream. Конкурентность ограничена семафором (`subagent.max_concurrency` в `config/ui.py`, дефолт 12): сотни задач можно слать, они дренируются батчами без 429.

Формы вызова (нормализуются в `tools/subagent_specs.py:build_subagent_task_specs`):

- `prompt` — одиночный субагент;
- `tasks: [...]` — fan-out: параллельные независимые задачи;
- `phases: [{name, tasks}, ...]` — фазовая оркестрация: фазы исполняются по порядку (фаза N+1 стартует после завершения N), агенты внутри фазы — параллельно. Один вызов прогоняет весь конвейер, живая панель помечает завершённые фазы зелёным;
- `items + stages` — pipeline: для каждого `item` стадии `stages` идут последовательно (шаблон `{item}`/`{index}`/`{stage}`), а сами items — параллельно.

Поля задачи:

- `prompt` (обязательно);
- `phase` (обязательно для оркестрации) — группа/стадия в живой панели (`Scout`, `Implement`, `Verify`);
- `label` (обязательно) — короткое 1-2 слова имя задачи в панели; без `phase`/`label` панель показывает безликое `Agents`/`Sub1`;
- `model` (опционально — display name или id из любого включённого провайдера);
- `role` — профиль из `agent/subagent_api._ROLE_PROFILES`: `coder`, `researcher`, `reviewer`, `planner`, `coordinator`. Роль меняет инструкции, но не ограничивает инструменты;
- `preset` — готовая заготовка-роль из `.data/agents/<name>/AGENT.md` (`agent/agent_presets.py`): даёт инструкции/модель, передаёшь только `prompt`;
- `depends_on` — список 1-based индексов задач, которые должны завершиться ДО этой. Их результаты инжектятся в промпт. Задачи без зависимостей идут параллельными волнами, зависимые ждут (`_resolve_dependencies` → топосортировка в волны). В `phases` зависимость от предыдущей фазы проставляется автоматически.
- `isolate` — по умолчанию `false`: субагенты пишут ПРЯМО в общую рабочую директорию, поэтому работу надо резать на независимые слайсы (каждый субагент владеет своими файлами; no two touch the same path). `isolate=true` — каждому отдельный git worktree (см. ниже).

Промпты (fenced/native `MODE_SWARM` + секция Subagents) требуют весь пайплайн делегирования одним вызовом: параллельные задачи — `tasks[]`, staged-пайплайн — `phases[]` того же вызова; последовательные вызовы `subagent` по одной фазе считаются ошибкой оркестратора (сериализуют ран и прячут DAG от планировщика).

Все субагенты запускаются в agent-mode и получают одинаковый полный набор инструментов (кроме явно запрещённых внутри субагента `poll` и вложенного `subagent`).

Дисплей: `SubagentTracker` / `SubagentBuffer` + `SwarmOverlay` — интерактивная панель в нижней зоне (навигация стрелками, Enter — детали задачи), плюс `agent/subagent_display.py`. Инкрементальный лог завершившихся — `progress.md` в run-директории.

Итерации субагента ограничены 100 (`MAX_SUBAGENT_ITERATIONS`, `config/constants.py`), активный контекст — 1M токенов, общий wall-clock бюджет — 2 ч (`MAX_SUBAGENT_WALL_SEC`); по исчерпании любого лимита задача завершается с ошибкой, а сделанная работа сохраняется в финальном тексте.

Внутри субагента запрещены `poll` и вложенный `subagent`. `web_search` разрешён — субагент умеет искать в сети.

Список доступных моделей и заготовок-пресетов подмешивается в системный промпт через `system_prompt._build_subagent_models_block` / `_build_agent_presets_block`.

### Координатор-паттерн (общие имена/контракты)

Когда несколько субагентов должны использовать одни и те же имена/сигнатуры — первая задача с `role="coordinator"` читает код и пишет контракты в общий scratchpad (`.data/subagents/<run-id>/shared.md`), а остальные задачи `depends_on: [1]` получают его spec в промпте. Контракт решается один раз, без merge-конфликтов потом.

### Git worktree-изоляция (mode=agent)

`agent/subagent_git.py` создаёт отдельный git worktree для каждого субагента под `.data/subagents/<run-id>/sub-<N>/` на ветке `subagent/<run-id>-<N>`:

- Файловые изменения не текут между субагентами и не трогают основной рабочий каталог до явного merge.
- Контекстная подмена workdir — через `ContextVar` в `tools/_paths.py` (`use_working_dir(path)`). `resolve_path` использует `os.path.normpath`, **не `realpath`** — чтобы симлинки `.venv` / `node_modules` внутри worktree разрешались в свои кэши, но запись не утекала по симлинкам.

После завершения оркестратор:

- авто-коммитит всё что сделал субагент (`git add -A -f` обязателен, иначе `.gitignore` отрежет легитимные правки в `.data/`),
- удаляет worktree-директорию, ветка остаётся,
- возвращает: branch, commit SHA, файлы, diff stat, готовые команды `git show <sha>` / `git log -p <branch>` с актуальным `base_sha` (никогда не хардкодит `main` / `master`).

`cleanup_stale_branches()` при следующем запуске субагентов удаляет все `subagent/*` ветки кроме текущего HEAD — мусор не копится.

Дальше пользователь решает руками: `git merge`, `git cherry-pick <sha>` или `git branch -D <branch>`. Merge-конфликты — на пользователе.

## Скиллы

`skills/manager.py` + `skills/registry.py` + `tools/skill_tool.py`. Скилл — директория `.data/skills/<name>/SKILL.md` с frontmatter:

```markdown
---
name: docx-mastery
description: Полное руководство по работе с .docx через инструмент docx
disable-model-invocation: false
---
...тело скилла...
```

Поведение:

- Каталоги: `skills/default/` (в git, встроенные) и `skills/user/` (в gitignore, пользовательские) — создаются в `config/paths.py ensure_dirs`.
- Скиллы обнаруживаются `discover_skills()` и подмешиваются в системный промпт через `build_skills_prompt` — модель видит каталог с описаниями.
- Чтобы активировать, модель вызывает `skill` с `{"name": "..."}`. Тело инжектится как user-message с маркером `━━━ СКИЛЛ АКТИВИРОВАН ━━━`.
- `disable-model-invocation: true` в frontmatter скрывает скилл из автокаталога (доступен только по явному вызову через `/skills`).
- Меню `/skills`: список / создание / добавление из директории / удаление.
- `reset_active_skills()` зовётся при `/new`.

## Память (memory)

`memory/` — долговременная память агента (порт memory-системы Claude Code). Хранит факты, не выводимые из кода/git/`AGENTS.md`: предпочтения пользователя, обратную связь по стилю работы, контекст проекта, внешние референсы. Файлы — markdown с YAML-подобным frontmatter в `.data/memory/<slug>-<sha1[:10]>/` (изоляция по рабочей директории), а также глобальная кросс-проектная память `.data/memory/_global` (`config/paths.py global_memory_dir`).

Четыре типа памяти: `user`, `feedback`, `project`, `reference`.

Три механизма (`memory/memdir.py`, `memory/cleanup.py`, `memory/insights.py`):

1. **Инжекция в промпт** — `format_memory_block()` всегда добавляет закреплённые/приоритетные записи и до 8 записей, лексически релевантных текущему пользовательскому запросу, в блок `<persistent_memory>` (`system_prompt._build_memory_block`, лимит ~6000 символов). Порядок файлов больше не определяет, какая память попадёт в контекст.

2. **Периодическая чистка** — при запуске CLI в фоне выполняется модельный аудит глобальной и текущей проектной памяти, но не чаще раза в 3 дня (маркер `.data/memory/.last_model_cleanup`). Дубли объединяются, однозначно устаревшие записи обновляются или удаляются; при сомнении модель выбирает `ignore`. Маркер обновляется только после успешного ответа модели.

3. **Ручное редактирование моделью** — единый инструмент `memory` с действиями write / list / read / delete (`tools/memory_tool.py`): только основной агент по ходу текущего диалога решает, когда сохранить или удалить долговременный факт.

## Хуки (hooks)

`src/hooks/` (`runner.py`, `matcher.py`, `schema.py`) + конфиг `config/hooks.py` + файл `.data/hooks.json`.

Что это: внешние команды/HTTP-запросы, которые вызываются на ключевых событиях жизненного цикла агента и могут блокировать или дополнять его действия (совместимо с claude-code).

События (`HOOK_EVENTS`, `src/hooks/schema.py:36`):

| Событие | Когда |
|---|---|
| `PreToolUse` | Перед выполнением инструмента — может `approve` / `block`. |
| `PostToolUse` | После выполнения инструмента. |
| `UserPromptSubmit` | После приёма пользовательского сообщения, перед первым LLM-запросом раунда. |
| `Stop` | После последнего LLM-ответа, когда агент готов завершить раунд. Может отменить завершение. |
| `SessionStart` | Старт сессии. |
| `SessionEnd` | Завершение сессии. |

Конфиг `.data/hooks.json` — маппинг событий на матчеры/хуки. Кэш по mtime файла (`config/hooks.py`, `has_hooks()`), правки подхватываются без рестарта.

Контракт (совместим с claude-code):

- На stdin подаётся JSON payload: `{event, tool_name, tool_input, ...}`.
- Хук может вернуть **JSON в stdout** с полями: `decision` (`approve` | `block`), `reason`, `continue` (false → попросить остановиться), `systemMessage` (показать пользователю), `additionalContext` (подмешать в историю), `hookSpecificOutput.additionalContext`;
- либо просто exit code: `0` = ок, `2` = block (stderr → reason), иное = ошибка (не блок).

В payload `UserPromptSubmit` входят `prompt`, `provider` и `model`; в payload `Stop` — `final_response`, `provider`, `model`, `stop_hook_active` и `stop_hook_count`. Если синхронный `Stop`-хук возвращает `decision: "block"` или exit code `2`, завершение отменяется, а `reason` отправляется агенту как запрос продолжить текущую задачу. После реального tool call счётчик сбрасывается; без прогресса допускается не более трёх продолжений, чтобы ошибочный hook не создал бесконечный цикл. `continue: false` имеет приоритет и разрешает завершение.

Типы хуков (`HookSpec`): `command` (shell-команда, таймаут по умолчанию 30 сек) и `http` (URL, опциональные `headers`). Поддерживаются matcher-обёртки и permission-style фильтр `if`.

Точки вызова: `tools/registry.py` (`_run_pre_tool_hooks` / `_run_post_tool_hooks`), `config/hooks.py has_hooks()`.

## Сессионные заметки (session notes)

`src/session/notes.py` — шаблон заметок о текущем состоянии и следующих шагах для длительной автономной работы. Файл: `<session.dir>/session_notes.md`, создаётся `ensure_session_notes()` при необходимости.

Блок с заметками инжектится в контекст через `format_session_notes_block()` (вызывается из `agent/messages.py:117`) — модель видит актуальное состояние сессии между ходами.

Шаблон (`_TEMPLATE`) содержит разделы: `# Session Title` (короткое описательное название сессии, 5-10 слов), `# Current State` (что активно делается, ближайшие шаги), `# Task specification` (что просил пользователь, ограничения и решения), `# Files and Functions` (важные файлы/функции и почему они важны), `# Workflow` (обычно запускаемые команды и как трактовать результат), `# Errors & Corrections` (ошибки, правки пользователя, неудачные подходы), `# Verification` (какие проверки запускались, вердикты), `# Worklog` (краткий пошаговый журнал работы).

Лимиты: `_MAX_NOTE_CHARS = 12000` (весь файл), `_MAX_MESSAGE_CHARS = 1200` (одно сообщение/раздел в промпте).

## MCP

`apis/mcp_client.py` + `config/mcp.py` + меню `commands/menus/mcp.py`. Клиент [Model Context Protocol](https://modelcontextprotocol.io/).

Конфиг: `.data/mcp_servers.json` — `{servers: [{id, command, args, env, enabled, transport: "stdio"}]}`.

Транспорт — только stdio (через `mcp.client.stdio.stdio_client`). SSE/HTTP — точка расширения в `_connect_async`.

`MCPManager` — singleton с фоновым asyncio-loop в отдельном потоке (sync TOOL_REGISTRY вызовы → async SDK через `run_coroutine_threadsafe`).

При старте interactive вызывается `init_mcp_from_config()`: подключает enabled-сервера и регистрирует их tools в `TOOL_REGISTRY` под именами `mcp__<server_id>__<tool_name>`. JSON-схемы попадают в `get_tool_schemas("agent")` через `get_mcp_tool_schemas()`. В `planning` режиме не подмешиваются.

Меню `/mcp`: список со статусами (`●`/`○`/`✗`), добавление, enable/disable, удаление, реконнект.

`shutdown_mcp()` в `finally` корректно закрывает `AsyncExitStack`'и и останавливает фоновый loop.

`CallToolResult.content` нормализуется: text → как есть, image → плейсхолдер с MIME, resource → URI. `isError=True` → префикс `[MCP tool error]`.

## LSP

`apis/lsp_client.py` + `config/lsp.py` + меню `commands/menus/lsp.py`. Свой клиент LSP по stdio JSON-RPC.

`LSPManager` — singleton с фоновым asyncio-loop в отдельном потоке (по аналогии с MCPManager).

Конфиг — `.data/lsp_servers.json`. Если файла нет, используются `DEFAULT_SERVERS`: `pyright` (Python), `typescript-language-server` (TS/JS), `gopls` (Go), `rust-analyzer` (Rust). Сервер включается только если есть бинарь в PATH.

Инструменты — **только `lsp_references`** и **`lsp_diagnostics`** (`execute_lsp_references` / `execute_lsp_diagnostics`, `apis/lsp_client.py:655-665`). `lsp_definition` и `lsp_hover` удалены. Оба инструмента read-only, доступны и в planning mode.

Диагностики запускаются после write/patch/create (`auto_diagnostics`).

`shutdown_lsp()` в `finally` корректно гасит дочерние процессы.

## Telegram-мост

`apis/telegram.py` + `agent/telegram_handler.py` + `agent/tg_menu.py` + `agent/tg_format.py`. Зеркалит события агента в Telegram-чат и принимает оттуда сообщения.

Singleton `TelegramBridge`. Запускается из `commands/interactive.py`, если `telegram_enabled` и заданы `telegram_bot_token` + `telegram_chat_id`.

Использует [aiogram 3](https://docs.aiogram.dev/). Реализует:

- Очередь отправки с throttle (~30 msg/s) и автоматическим разбиением длинных сообщений (лимит 4000 символов).
- Параллельное чтение `stdin` и `incoming_queue` — что придёт раньше, то и обрабатывается.
- Typing-индикатор (`send_chat_action` каждые 4 сек) во время стрима.
- Thinking-плейсхолдер «💭 thinking…», редактируется в финальный ответ.
- Зеркалирование reasoning_content и финального текста.
- Reply-клавиатуру и inline-меню (`/menu` → быстрые действия, переключение режима agent/planning/swarm, stop агента).
- Slash-команды от бота маршрутизируются в основной агент.

`TelegramEventHandler` оборачивает обычный `RichEventHandler` и дополнительно шлёт в TG старт/итог tool-вызовов, обновления плана, статусы субагентов.

Меню `/tg`: токен / чат / тест соединения / on-off без рестарта CLI.

## Headless / CI

`commands/headless.py` — реализация команды `uv run necli run "..."` для CI/CD, pre-commit, cron и pipe.

Никакого prompt_toolkit и Rich Live: финальный текст в stdout, прогресс в stderr, exit code 0/1/2.

`stdin` подхватывается, если не tty: `git diff | uv run necli run "коммит-сообщение"` приклеит diff в конец промпта.

Опции: `--api/-A`, `--model/-m`, `--workdir/-w`, `--json` (структурированный вывод `{ok, text, model, workdir}`), `--quiet/-q`, `--timeout`, `--allow-all` (wildcard `*=allow,process`). Прогресс инструментов в обычном режиме идёт в stderr по одной строке; `--quiet` скрывает прогресс и итоговый ответ успешного запуска.

Без `--allow-all` ставит `NECLI_HEADLESS=1` → инструменты в режиме `ask` авто-отказывают (а не зависают на TTY-меню), в stderr предупреждение.

Использует тот же `agent/loop.run_agent` (без LiveStream), что и интерактив.

Примеры:

```bash
uv run necli run "посчитай строки в проекте" --quiet
git diff --staged | uv run necli run "напиши коммит" --json | jq -r .text
uv run necli run --api openai --allow-all --timeout 300 "прогон линтеров и фикс"
```

## Система разрешений

`config/permissions.py` — гранулярный контроль над выполнением инструментов.

| Scope | Хранение | Время жизни |
|---|---|---|
| `session` | в памяти | до `/new` |
| `process` | в памяти | до выхода из CLI |
| `forever` | `config.json["tool_permissions"]` | навсегда |

Три решения: `ask` (дефолт), `allow`, `deny`.

Приоритет: `session > process > forever > "ask"`. Wildcard `"*"` поддерживается на каждом уровне как fallback для всех инструментов без явного решения.

В цикле:

- `agent/executor._execute_single` перед запуском tool проверяет `get_decision(tool_name)`.
- При `deny` — сразу `ToolResult(status="error")` без выполнения.
- При `ask` — компактный overlay (`ui/overlays.py`) с вариантами allow once / session / process / forever и deny once. Долгосрочные запреты меняются через `/permissions`.
- В headless `NECLI_HEADLESS=1` заставляет `confirm_tool_call` отказывать без зависания.

Меню `/permissions` показывает все эффективные решения с указанием scope и позволяет менять/сбрасывать.

## UI и темы (CLI)

### Shell (постоянный Application)

`ui/shell.py` — постоянный prompt_toolkit Application, который разделяет scrollback (статику) и динамическую зону; мост Rich→prompt_toolkit, в пути отрисовки **нет `rich.live.Live`**.

Shell разбит на три mixin'а:

- `ui/shell_keys.py` (`ShellKeyBindingMixin`) — все keyboard-биндинги: Enter, Tab (mode), Ctrl+C, Ctrl+D, Ctrl+O, стрелки, навигация по строкам субагентов.
- `ui/shell_layout.py` (`ShellLayoutMixin`) — построение layout, оверлеи, расчёт бюджетов, фокус.
- `ui/shell_output.py` (`ShellOutputMixin`) — динамическая зона, статика в scrollback, статус-строка, queued messages, строки под рамкой.

Вспомогательные модули:

- `ui/overlay.py` — базовый контракт `Overlay` для временных виджетов нижней зоны.
- `ui/buffer_editing.py` — readline-подобные операции редактирования буфера (backspace, delete, word-delete, Ctrl+W/U/K, Home/End, стрелки).
- `ui/submissions.py` — нормализованные типы сообщений из UI (`SUBMIT_USER`, `SUBMIT_SLASH`, `SUBMIT_EOF`, `SUBMIT_INTERRUPT`, `SUBMIT_BG_RESUME`, `SUBMIT_TG`).
- `ui/text_layout.py` — word-wrap, clip по видимой ширине, `WordWrapProcessor` для prompt_toolkit.
- `ui/terminal.py` — определение глубины цвета терминала, `term_size()`.
- `ui/rows.py` — `RowGroup` для интерактивных строк под рамкой (субагенты, фоновые задачи).
- `ui/rendering.py` — `RichBridge` (рендер Rich-объектов в ANSI-строки), `ansi_rows()`.

`ui/prompt.py` добавляет вставку из буфера, изображения и эхо, а `ui/overlays.py` обслуживает интерактивные виджеты нижней зоны (`select_menu`, `panel_menu`, `ask_text`, `confirm`) — все меню оформлены как карточки/плоские списки с палитрой, колонками, прокруткой и подсказками. Общий `CardMenu` фильтрует пункты как `/models`: запрос разбивается на токены, каждый токен должен входить подстрокой в label/hint/badge/columns/search-поля.

### Очередь ходов агента

`commands/agent_queue.py` — строго последовательная; поле ввода доступно всегда, даже во время ответа агента; ожидающие сообщения показываются строками над полем; стрелка вверх снимает отложенный батч.

### Горячие клавиши и поведение

- `Enter` — отправить. `Esc+Enter` или `\\` в конце строки — перенос.
- `Tab` — циклить mode: `agent ↔ planning ↔ swarm` (иконки 🚀 / 🧠 / 🔮).
- `Ctrl+V` — вставить текст из буфера (через `xclip` / `xsel` / `wl-paste` / `pbpaste`).
- `Ctrl+P` — вставить изображение из буфера: сохраняется в `.data/clipboard_images/`, в тексте — плейсхолдер `[imageN]`. При настроенной image-модели она превращает изображение в подробное текстовое описание для основной модели; иначе используется прежний multimodal `HumanMessage`.
- `Ctrl+O` — toggle expanded/compact replay: перерисовывает весь вывод сессии из `agent/render_store.py` через `agent/render_replay.py` (полные превью без обрезки ↔ компактные).
- `Ctrl+C` обрабатывается как событие клавиши (прерывание хода/ввода), `Ctrl+D` — выход.

История ввода — `.data/history` (FileHistory + ThreadedHistory): slash-команды не сохраняются, одинаковые соседние записи не дублируются. Автокомплит — `ui/completer.py`: slash-команды + файлы (`@`-prefix).

### Темы

`config/themes.py` — система тем по семантическим ролям (`accent`, `success`, `warning`, `error`, `info`, `magenta`, `purple`, `muted`, `dim_text`, `bar_filled`, `bg_code`, `bg_output`, `bg_select`).

Встроенные темы: `dracula` (дефолт), `monokai`, `catppuccin`, `nord`, `gruvbox`, `tokyo-night`, `solarized`, `one-dark`. Любую роль можно переопределить через `set_custom_color(role, color)`. Доступ из кода — `from config.themes import t; t("accent")`. Меню — `/themes`.

### Языки

Языки интерфейса (`config/i18n.py`): `en`, `ru`, `de`, `fr`, `zh` (`SUPPORTED_LANGS`, дефолт `en`). Меню — `/settings` → раздел Interface.

### Лимиты

Лимиты и подсказки — `config/ui.py` (`limits`): `max_width = 100`, `streaming_max_lines = 40`, `max_result_length = 15000`, `subagent.max_concurrency = 12`.

### Emoji width

`ui/_emoji_width.py` — патч `rich.cells.get_character_cell_size` для терминалов, где emoji рендерятся как 1 cell вместо 2. Включается через `config.json: "emoji_width": 1` или `NECLI_EMOJI_WIDTH=1`.

### Desktop-уведомления

`ui/focus.py` — отслеживание фокуса терминала через focus reporting (`CSI ?1004`): при старте Shell пишет `\x1b[?1004h`, обёртка над `Vt100Input` вырезает `\x1b[I` / `\x1b[O]` из потока ввода до vt100-парсера (prompt_toolkit их не знает) и превращает в булево состояние (`is_terminal_focused()`, `None` = терминал не отчитывается). При выходе режим выключается (`\x1b[?1004l`).

`ui/notifications.py` — `notify_turn_finished(elapsed, cancelled=, poll=)`: системный тост о завершении хода (или poll-ожидания), если включено `notifications_enabled`, ход длился ≥ 60 сек (`MIN_TURN_SECONDS`), терминал не в фокусе (`None` трактуется как «не в фокусе») и с прошлого уведомления прошло ≥ 5 сек. Доставка — фоновым потоком-демоном: Linux `notify-send -a necli`, macOS `osascript`, Windows PowerShell toast через Windows Runtime API. Вызовы: `commands/interactive.py` после `_run_with_interrupt` (обычный ход и авто-резюм фоновой задачи), `agent/executor.py` после poll-инструмента. Переключатель — `/settings` → Interface (при включении кидает пробный тост).

## Логирование

`logger.py` использует loguru. В dev оба активных файла находятся только в `logs/` в корне проекта; в frozen-режиме — в `$NECLI_HOME/logs` или `~/.necli/logs`. Каталог можно явно переопределить через `NECLI_LOG_DIR`.

| Файл | Что пишется |
|---|---|
| `logs/necli.log` | Читаемый человек журнал `INFO+`: дата, уровень, модуль, функция, строка и сообщение. |
| `logs/necli-debug.jsonl` | Полный структурированный журнал `DEBUG+`, включая source location, correlation IDs и performance spans. |

Правила:

- Большие payload'ы скрыты по умолчанию; их preview включается только через `NECLI_LOG_PAYLOADS=1`. Секреты не логируются и при включённом preview.
- `necli.log` ротируется по 20 MB и хранит 10 ротаций; `necli-debug.jsonl` — по 50 MB и хранит 5.
- Стандартный `logging` перехватывается в loguru через `InterceptHandler`, поэтому модули с `logging.getLogger(__name__)` попадают в те же два файла с корректным source location.
- Шумные сторонние библиотеки глушатся до WARNING.
- Контекст `session`, `turn`, `round`, `request`, `subagent` и `tool_call` пробрасывается через `ContextVar` и сохраняется в структурированных событиях.
- Не читай логи целиком — только `tail -n` нужного файла либо `read` с `lines`.

## Инсайты (/insights)

`memory/insights.py` + меню `commands/menus/insights.py`. Команда `/insights` строит развёрнутый HTML-отчёт о том, как пользователь взаимодействует с агентом, по ВСЕМ сохранённым сессиям.

Pipeline (`generate_insights`):

1. **Сбор** — `_load_all_sessions()` тянет все сессии из `session/storage`.
2. **Локальные метрики** — `collect_metrics()` без модели: сообщения, активные дни, топ-инструменты (по `:::call` в ответах), типы ошибок, часы активности, средняя длина сообщения/сессии, пересечения сессий (параллельная работа).
3. **Анализ моделью** — `build_transcript()` собирает транскрипт всех сессий, `api_insights` (чистый контекст активной модели, без tools, история не трогается — как `api_recap`) возвращает СТРОГИЙ JSON: at-a-glance, области работы, intents/session_types, достижения, категории трения с примерами, фичи к пробованию, паттерны, горизонт, правки для AGENTS.md и durable-факты для памяти. Текст — на языке интерфейса (`config.i18n.get_lang()`).
4. **Рендер** — `render_html()` собирает самостоятельный HTML (светлая Inter-тема, барные чарты, copy-кнопки, навигация), сохраняется в `.data/insights/report-<ts>.html`.
5. **Память** — `save_memories()` пишет durable-факты (`memory action=write`), до `_MAX_MEMORY_ITEMS`.

Из CLI-меню по умолчанию `persist_memory=False` — отчёт без записи в память.

`/insights` запускает генерацию фоновой задачей на основном event loop и сразу возвращает управление терминалу. Готовый путь к отчёту или ошибка печатаются в scrollback; при завершении интерактивной сессии незаконченная задача отменяется.
