# necli — техническая документация

> Архитектура, внутренности и устройство кода. Здесь — как всё устроено внутри.

**Терминальный AI-агент. CLI + Telegram-мост, одно ядро.**

API-only клиент для LLM: прямые вызовы провайдеров через **httpx** (свои реализации, без LangChain), гибридный режим инструментов (fenced `:::call` блоки + native function calling), стриминг с инлайн-выполнением tool-блоков, агентный цикл до 500 итераций, до 100 задач на один вызов субагентов с **git worktree-изоляцией**, DAG-зависимостями, ролями/пресетами и фазовой оркестрацией (phases / items+stages в одном вызове), планировщик, скиллы, долговременная память между сессиями с автоизвлечением фактов, инсайты по всем сессиям (`/insights`), MCP, LSP, хуки (совместимые с claude-code), авто-pruning истории, автоочистка `.data/`, headless-режим для CI и зеркало в Telegram.

Python ≥ 3.10. Управление зависимостями — `uv`.

---

## Quick start

```bash
pip install uv
uv sync

# CLI (интерактивный prompt_toolkit shell)
uv run python src/main.py cli --api openai

# Headless (CI / pipe / cron)
echo "сосчитай строки .py" | uv run python src/main.py run --quiet --allow-all
```

Ключи API и определения провайдеров хранятся в `.data/apis.json` (`providers` / `keys`) и редактируются через меню `/api` внутри CLI.

---

## Содержание

1. [Точки входа](#точки-входа)
2. [Архитектура](#архитектура)
3. [Конфигурация и `.data/`](#конфигурация-и-data)
4. [API-провайдеры](#api-провайдеры)
5. [Агентный цикл](#агентный-цикл)
6. [Формат tool calls](#формат-tool-calls)
7. [Инструменты](#инструменты)
8. [Режимы (agent / planning / swarm)](#режимы-agent--planning--swarm)
9. [Планировщик](#планировщик)
10. [Сессии, токены, стоимость](#сессии-токены-стоимость)
11. [Slash-команды](#slash-команды)
12. [Субагенты](#субагенты)
13. [Скиллы](#скиллы)
14. [Память (memory)](#память-memory)
15. [Хуки (hooks)](#хуки-hooks)
16. [Сессионные заметки (session notes)](#сессионные-заметки-session-notes)
17. [MCP](#mcp)
18. [LSP](#lsp)
19. [Telegram-мост](#telegram-мост)
20. [Headless / CI](#headless--ci)
21. [Система разрешений](#система-разрешений)
22. [UI и темы (CLI)](#ui-и-темы-cli)
23. [Логирование](#логирование)
24. [Структура проекта](#структура-проекта)
25. [Инсайты (/insights)](#инсайты-insights)

---

## Точки входа

`src/main.py` — Click-группа с командами (запуск: `uv run python src/main.py <cmd>`; файл сам добавляет `src/` в `sys.path`):

| Команда | Назначение |
|---------|------------|
| `cli` | Основной TUI на постоянном prompt_toolkit Application (без `rich.live.Live` в пути отрисовки). |
| `run` | Headless: один проход агента, результат в stdout, exit code 0/1/2. |

Опции `cli`:

| Флаг | Назначение |
|------|------------|
| `--api, -A` | Активировать провайдера на этот запуск (`--api openai`). |
| `--model, -m` | Модель (id или display name). |
| `--workdir, -w` | Рабочая директория (по умолчанию — `cwd`). |
| `--resume, -r` | Восстановить сессию по id или префиксу. |

При старте `src/main.py` поднимает `RLIMIT_NOFILE` до 8192 (httpx-стримы + множество открытых файлов сессий быстро упираются в дефолт 1024 на Linux).

Опции `run` (см. `commands/headless.py`): `--model/-m`, `--workdir/-w`, `--api/-A`, `--json`, `--quiet/-q`, `--timeout`, `--allow-all`. `stdin` подхватывается, если не tty. Exit code 0/1/2.

---

## Архитектура

```
┌────────────┐  user input   ┌──────────────────┐
│ ui/prompt  │ ────────────► │ commands/        │
│ (PT)       │               │ interactive.py   │
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

LangChain **не используется**. Своя минимальная замена `langchain_core.messages` живёт в `apis/messages.py` (`SystemMessage` / `HumanMessage` / `AIMessage` / `ToolMessage` + `AIMessageChunk` с `__add__`). Общая логика стрима, retry/throttle, нативных tool calls и multimodal-вложений — в `apis/base.py:BaseProvider`. Провайдеры наследуются от него: `openai_provider`, `anthropic_provider`, `google_provider`, `custom_provider`.

---

## Конфигурация и `.data/`

Вся персистентность лежит в `.data/` рядом с проектом (`config/paths.py`; базовый каталог — `.data` рядом с кодом, либо `NECLI_HOME`; в frozen-режиме — `~/.necli`):

```
.data/
├── config.json                # основной конфиг (config/settings.py)
├── apis.json                  # провайдеры и ключи (providers / keys)
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
├── docx_sources/              # HTML-исходники + .template.docx для round-trip
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

- сессии старше **30 дней** — кандидаты на удаление, но последние **100** и все **pinned** сохраняются;
- пустые сессии-директории удаляются;
- `subagents/` старше **14 дней**;
- `clipboard_images/`, `docx_shots/`, `docx_sources/`, `uploads/` старше **7 дней**;
- корневой мусор `_clean_root_junk()`: `_git_stats.py`, `api_providers.json`, `diff_target.txt`, `docx_reference.docx`.

Конфиги, реестры, `agents/`, `skills/`, `memory/` не трогаются. `docx_reference.docx` больше **не** генерируется как служебный файл `create_docx` — он считается мусором и вычищается автоочисткой.

Ключевые поля `config.json` (см. `config/settings.py`):

| Поле | Назначение |
|------|------------|
| `active_api`, `active_api_model` | Текущий провайдер и модель. |
| `tool_permissions` | Постоянные разрешения инструментов. |
| `theme`, `theme_custom` | Активная тема и переопределение ролей. |
| `telegram_bot_token`, `telegram_chat_id`, `telegram_enabled` | Telegram-мост. |
| `think_enabled` | Глобальный THINK-режим (рассуждения вслух). |
| `temperature`, `max_tokens` | Generation params. |

Провайдеры и ключи живут в `.data/apis.json` (поля `providers` / `keys`), миграция из старых `api_providers` / `api_keys` в `config.json` автоматическая. Доступ — `config.get(key, default)` / `config.set_value(key, value)`. Словарь кэшируется, мутации идут только через `set_value`.

---

## API-провайдеры

Провайдер описывается `ApiProviderDefinition` (`apis/models.py`). Загрузчик `apis/registry.py`:

1. Читает встроенные шаблоны из `apis/definitions/*.json`.
2. Поверх накладывает пользовательские из `.data/apis.json["providers"]` (ключи — в `["keys"]`).
3. По полю `type` выбирает фабрику: `openai_provider`, `anthropic_provider`, `google_provider` или `custom_provider` (свой aiohttp/httpx-клиент для OpenAI-совместимых прокси с `reasoning_content`).

Инстансы кэшируются по `(provider_id, model_id, kwargs)` — kwargs включены в ключ, иначе каждый вызов с параметрами плодил бы новый инстанс со свежим `session_id` и сбивал prompt-cache. `reload_providers()` сбрасывает кэш — нужен после правок через `/api`.

Встроенные определения (`apis/definitions/`): anthropic, google, groq, lmstudio, ollama, openai, openrouter, xai (+ шаблон `_example.json`). Пользовательские OpenAI-совместимые прокси и свои шлюзы добавляются через `/api` или правкой `.data/apis.json`.

Поля определения: `default_headers` (произвольные HTTP-заголовки) и `extra` — шлюзовые настройки без хардкода в коде: `append_query`, `session_id_header`, `billing_header`, `inject_metadata`, `use_aiohttp`, `system_as_first_message`, `use_bearer_auth`, `prompt_cache`, `extra_body`, `reasoning_models`.

На провайдера можно повесить несколько ключей (список в `apis.json["keys"]`, каждому — имя) — при rate-limit запрос автоматически повторяется со следующим ключом (ротация).

Каждая модель: `id`, `display_name`, `context_window`, `input_price` / `output_price` (USD за 1M токенов). Цены используются в `session/session.py:_compute_cost`: при наличии реального `usage` от провайдера — он, иначе fallback на tiktoken-оценку.

---

## Агентный цикл

`agent/loop.py` содержит две реализации над одним и тем же `apis.agent_adapter.api_send_message`:

- **`run_agent_interactive`** — основной цикл с `LiveStream` (`agent/stream.py`). Стримит ответ, парсит `:::call <tool> ... call:::` блоки по мере поступления, выполняет инлайн через `agent/stream_tool_exec.py`. После каждой итерации скармливает `ToolResult`'ы модели как новое сообщение. Итерации не ограничены (`agent/loop.py`).
- **`run_agent`** — headless-вариант без Rich Live. Используется в `commands/headless.py`.

Ключевые детали:

- **`AgentContext`** (`agent/context.py`) хранит план, рабочую директорию, mode (`agent | planning | swarm`), event-handler, snapshot файлов из `agent/fs_watcher.py`, `step_tracker` (`agent/project_stats.py`), счётчик nudge и флаги прерывания.
- На каждый раунд `agent/messages.build_first_message` / `_build_result_message` дополняет сообщение блоком плана, сессионными заметками и трекингом изменений ФС.
- **Авто-продолжение**: подозрение на обрыв (`is_likely_truncated`) → `CONTINUE_MESSAGE` («продолжай»); ошибка прокси (`is_api_proxy_error`) → повтор.
- **Авто-компрессия истории** в `commands/interactive._maybe_auto_compress` срабатывает при `context_tokens / get_context_limit(model) ≥ 0.90`.
- **События** — через интерфейс `AgentEventHandler` (`agent/events.py`). Дефолт — `RichEventHandler`; при активном TG-мосте оборачивается `TelegramEventHandler`.
- **Прерывания**: `Ctrl+C` → `ctx.interrupted = True`, стрим закрывается, частичный ответ сохраняется как `[Прервано]`.
- **THINK-блоки** (`agent/think.py`) парсятся и рисуются отдельной панелью. В сессию сохраняются как `Message.thoughts` отдельно от основного `content`.

---

## Формат tool calls

`apis/agent_adapter.api_send_message` поддерживает два канала, одновременно:

1. **Fenced блоки в тексте** — асимметричные маркеры:
   ```
   :::call <tool> [attrs]
   ...body...
   call:::
   ```
   Парсятся `tools/call_parser.py`. Три формата:
   - **JSON-инструменты** — body это JSON.
   - **Контентные** (`create_file` / `create_docx`) — `path="..."` в шапке, body — сырой контент.
   - **`patch_file`** — секции `--- FIND --- / --- REPLACE --- / --- INSERT ---` или атрибут `delete_lines`.

2. **Native function calling.** Глобальный единый переключатель `tool_format_force_native` (дефолт `True`, `config/settings.py:27`; команда `/tool_format`): `True` → native function calling для всех провайдеров, `False` → fenced. В native-режиме `BaseProvider` биндит JSON-схемы из `apis/tool_schemas.py`; полученные `tool_calls` конвертируются в текстовые fenced-блоки и проходят через тот же парсер — UI одинаковый, но результаты возвращаются модели как структурные `ToolMessage` (не как текстовый транскрипт). Системный промпт полностью изолирует режимы: в native-варианте нет ни одного упоминания fenced-синтаксиса (промпты — `prompts/native.py` / `prompts/fenced.py`).

Особенности:

- При пустых `args` после стрима (баг ряда прокси) делается fallback non-stream запрос за корректным JSON.
- Прокси html-эскейпят кавычки/угловые скобки в SSE; декодирование — единый канонический модуль `tools/_html_unescape.py`, используется в `tools/call_parser.py`, `apis/agent_adapter.py`, `agent/display.py`.
- `usage` (`input`, `output`, `reasoning`) пробрасывается из ответа провайдера в `Message.usage`.
- Длина fence для контентных инструментов выбирается динамически.

---

## Инструменты

Единый реестр — `tools/registry.py:TOOL_REGISTRY` (строки 43-63). JSON-схемы для native — `apis/tool_schemas.py`. Одинаковые имена работают и в fenced, и в native режиме.

Полный список (`TOOL_REGISTRY`):

| Категория | Инструменты |
|-----------|-------------|
| Shell | `shell` |
| Чтение | `read`, `grep` |
| Запись / правка | `create_file`, `patch_file` |
| DOCX | `create_docx`, `docx_screenshot` (рендер страницы .docx/.pdf в PNG) |
| Сеть | `web_search`, `web_fetch`, `image_search` |
| Мета | `subagent`, `skill`, `poll`, `expand_tool_result` |
| Память | `memory_write`, `memory_list`, `memory_read` (долговременная память проекта, см. раздел Память) |
| LSP | `lsp_references`, `lsp_diagnostics` |
| MCP | `mcp__<server_id>__<tool_name>` (динамически после `init_mcp_from_config`) |
| Control-tools | `plan` (чеклист задачи, не исполняет код), `think` (рассуждение вслух — только при включённом THINK-режиме) |

`plan` и `think` — это control-tools: попадают в JSON-схемы (`apis/tool_schemas.py`), но кода не выполняют, а лишь ведут чеклист / рисуют мысль в UI. `think` подмешивается в схемы только при активном THINK-режиме.

Существенные детали:

- **`read`** — поддерживает `.docx`, `.pdf`, изображения, csv/tsv, Excel. Лимиты: `MAX_READ_FILES = 20`, `MAX_LINES = 1000` (`tools/file_ops/read.py`).
- **`grep`** (`tools/file_ops/grep.py`) — поиск по содержимому с regex, автоматически исключает зависимости/кэши/скрытые директории.
- **`patch_file`** — `find/replace` (одиночный или массив `patches`), `line + insert` для вставки, `delete_lines="10-15"`. Fuzzy-матч с warning'ом (`tools/file_ops/_fuzzy.py`).
- **`create_docx`** — HTML → Pandoc 3.x → DOCX. Inline-CSS (`color`, `font-family`, `font-size`, `background-color`, `text-align`) применяется пост-процессом python-docx. LaTeX `$...$` / `$$...$$` → нативные OMML-формулы. Round-trip read→edit→write через двухпроходный pandoc (html + markdown). Подробности — в `AGENTS.md` и скилле `docx-mastery`.
- **`docx_screenshot`** — рендерит страницу(ы) .docx или .pdf в PNG и прикрепляет к следующему ходу модели (multimodal), чтобы она «увидела» реальную вёрстку — шрифты, поля, таблицы, формулы, разрывы. Pipeline: .docx → .pdf через LibreOffice headless (`soffice --convert-to`), .pdf → PNG через PyMuPDF при 200 DPI. Аргументы: `page` (одна страница) или `pages` (`"2-5"`, `"1,3,7"`, `"2-4,8,10-11"`, список, или `"all"`).
- **`shell`** — `cd` и `&&` / `||` явно запрещены парсером (`tools/shell.py`). Тяжёлые/долгие команды можно запускать в фоне: `background=true` в аргументах (`tools/background.py`) — задача исполняется отдельно, результат приходит уведомлением по завершении.
- **`web_search`** — DuckDuckGo (через `ddgs`) и/или прямой fetch URL через `trafilatura`. Результаты кэшируются.
- **`web_fetch`** — отдельный fetch содержимого одного или нескольких URL (извлекает текст, при необходимости raw HTML).
- **`image_search`** — поиск картинок в сети и скачивание их в `assets/images`.
- **`poll`** — запрос к пользователю с до 4 вариантами. В headless автоматически отказывает.
- **`expand_tool_result`** — длинные output'ы усекаются с маркером `expand via :::call expand_tool_result {"id": "..."}`; модель просит полный текст по id. Кэш `agent/result_cache.py` (FIFO, в памяти процесса).
- **LSP-tools** — только `lsp_references` / `lsp_diagnostics` (см. раздел LSP; `lsp_definition` и `lsp_hover` удалены).
- **Валидация аргументов** — `tools/arg_validation.py` сверяет args со схемой до вызова handler'а: алиасы имён параметров, коэрция типов (`"42"`→`42`), точные диагностики обязательных полей и enum.

### Read-only / planning-mode

`tools/registry.py` экспортирует `PLANNING_TOOLS` / `SWARM_TOOLS` (канонический `READ_ONLY_TOOLS` — из `config/constants.py`, см. раздел Режимы). В `planning` mode разрешены только они + `plan`.

### Лимиты вызовов

- Итерации главного агента и субагентов **не ограничены** — цикл работает, пока задача не завершена или не прервана.
- До **100 задач** на один вызов `subagent` (`agent/subagent.py:80`), конкурентность внутри волны — `subagent.max_concurrency = 12` (`config/ui.py:224`).
- Каждый вызов выполняется отдельно `agent/executor._execute_single` с тиканием спиннера и трекингом изменений ФС.

---

## Режимы (agent / planning / swarm)

Переключение по **Tab** в prompt (`commands/interactive.py _toggle_mode`; в Telegram — `/menu` → mode, `agent/tg_menu.py`, `modes = [agent, planning, swarm]`).

| Режим | Иконка | Что разрешено |
|-------|--------|---------------|
| `agent` | 🚀 | Полный набор инструментов. Дефолт. |
| `planning` | 🧠 | Read-only + планирование: `read`, `grep`, `lsp_references`, `lsp_diagnostics`, `memory_list`, `memory_read`, `poll`, `skill`, `web_search`, `web_fetch` + control-tool `plan`. Любая попытка вызвать write/shell/etc. возвращает `build_blocked_result`. |
| `swarm` | 🔮 | `planning` + `shell` + `subagent` — режим для оркестрации параллельных субагентов. |

Составы — из кода (`src/config/constants.py:41`, `src/tools/registry.py:213-214`):

- `READ_ONLY_TOOLS = {read, grep, lsp_references, lsp_diagnostics, memory_list, memory_read}`.
- `PLANNING_TOOLS = READ_ONLY_TOOLS | {poll, skill, web_search, web_fetch}`.
- `SWARM_TOOLS = PLANNING_TOOLS | {shell, subagent}`.

При переключении режима в первое сообщение нового раунда инжектится notice о смене режима. Аналогично `/think` — notice включения/выключения THINK-режима.

---

## Планировщик

`planner.py` — пошаговые планы в духе Claude Code Plan Mode.

- `Plan` хранит `goal` и список `PlanStep(status=pending|in_progress|done|skipped, notes)`.
- Модель управляет планом через специальные блоки:

  ```
  :::call plan
  {"action": "create", "goal": "...", "steps": [{"title": "..."}, ...]}
  call:::
  ```

  Поддерживаемые действия: `create`, `update` (по `step` / `index` / `title`), `add_step`, `remove_step`. **Минимум 3 шага** (`planner.py:286`).
- После каждого ответа `agent/loop` применяет команды плана, обновляет `.plan.md` в директории сессии и рисует панель `render_plan_panel`.
- `LiveStream` обрабатывает блоки plan в стриме: показывает прогресс-бар (`▮▮▯▯ 2/4`) прямо во время ответа.
- Окно `prev/current/next` инжектится в контекст следующего сообщения через `Plan.render_for_context`.
- При завершении (`is_complete`) `.plan.md` удаляется, при загрузке (`load_plan_file`) восстанавливается из markdown.
- Если модель долго работает без tool-вызовов, но план не завершён — посылается nudge с напоминанием.

---

## Сессии, токены, стоимость

`session/session.py:Session`:

- `id` — изначально техническая метка `YYYYMMDD_HHMMSS_<uid>`. При первом user-сообщении переименовывается в `<slug>_YYYYMMDD_HHMMSS` (slug — первые 20 «слов»-символов). Директория двигается через `shutil.move`.
- `messages: list[Message]` с ролями `user | assistant | system | tool_result`.
- `_compressed_stats` — снапшот сообщений/стоимости после `/compress`.

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

`apis/_context_pruner.py` обрезает старые tool-результаты из истории перед запросом, экономя токены без потери актуального контекста. Работает в обоих форматах (text-mode блоки и native `ToolMessage`). Триггеры вытеснения: (A) файл перезаписан в более позднем раунде; (B) тот же путь прочитан позже (дедуп); (C) крупное чтение старше `_KEEP_RECENT_ROUNDS=4` раундов; (D) hard-cap — любое чтение/вывод старше `_HARD_EVICT_ROUNDS=10`. Свежий раунд не трогается. Вместо контента ставится маркер `[content evicted to save tokens — ...]` с подсказкой перечитать. Вытеснение выводов тяжёлых инструментов (`shell`, `web_search`, lsp_*) — по возрасту (C/D).

---

## Slash-команды

Диспетчер — `commands/slash.py:_handle_slash` → `SlashResult`. Состояние и побочные эффекты обрабатывает `commands/slash_handler.py`. Метаданные команд (имя, категория, help-строка) — единый реестр `commands/registry.py`; `/help` и автокомплит `ui/completer.py` берут список оттуда автоматически.

**Правило мест**: новая команда обязана быть в `commands/registry.py` (метаданные → help+completer подхватываются сами) и в `commands/slash.py` (диспетчер `_handle_slash`); если она меняет состояние / запускает async — ещё и в `commands/slash_handler.py`. См. `AGENTS.md`.

Полный актуальный список (`commands/registry.py`, категории из `CATEGORIES`):

| Команда | Категория | Что делает |
|---------|-----------|------------|
| `/new` | session | Новый чат: чистит сессию и `ApiSession`, сбрасывает session-level разрешения и активные скиллы. |
| `/branch` | session | Управление git-ветками рабочего репозитория. |
| `/commit` | session | Сгенерировать коммит через `agent/commit_agent.py` и закоммитить. |
| `/sessions` | session | Меню сохранённых сессий с cost/tokens preview. |
| `/history [N]` | session | Последние N действий агента (по умолчанию 10). |
| `/compress` | session | Сжать историю через активную модель, сохранить бэкап. |
| `/reflect` | session | Рефлексия: модель анализирует сессию и предлагает обновить `AGENTS.md` (`commands/slash.py:141`). |
| `/api` | model | Меню провайдеров: добавление/правка, ключи, активная модель. |
| `/models` | model | Picker моделей активного провайдера. |
| `/params` | model | Generation params (temperature, max_tokens). |
| `/autoprune` | model | Меню авто-pruning контекста (`commands/menus/autoprune.py`). |
| `/proxy [URL\|off]` | model | Установить/сбросить HTTP(S)/SOCKS-прокси для API-вызовов (`commands/menus/proxy.py`). |
| `/cd PATH` | tools | Сменить рабочую директорию (для tools и file-completer). |
| `/permissions` | tools | Allow/deny инструментов на уровне session / process / forever. |
| `/mcp` | tools | MCP-сервера: добавление, enable/disable, реконнект. |
| `/lsp` | tools | LSP-сервера: список / enable / диагностика. |
| `/skills` | tools | Меню скиллов: список / создание / добавление / удаление. |
| `/agents` | tools | CRUD заготовок-пресетов субагентов (`.data/agents/<name>/AGENT.md`). |
| `/themes` | display | Выбор темы и кастомизация ролей. |
| `/lang` | display | Язык интерфейса (en, ru, de, fr, zh). |
| `/think` | display | Toggle THINK-режима (рассуждения вслух); toggle `think_enabled`. |
| `/tool_format` | display | Toggle глобального native function calling (`tool_format_force_native`) — иначе fenced. |
| `/help` | misc | Справка (группировка по категориям). |
| `/stats [N]` | misc | Интерактивная статистика за N дней + общая, с вкладками (session / hands / models / tools / history). |
| `/insights` | misc | Анализ всех сессий → HTML-отчёт + извлечение фактов в память (см. раздел Инсайты). |
| `/copy [N]` | misc | Скопировать последние N ответов ассистента в буфер обмена (по умолчанию 1). |
| `/tg` | misc | Telegram-мост: токен / чат / тест / on-off. |

---

## Субагенты

`tools/subagent.py` + `agent/subagent.py` + `agent/subagent_api.py` + `agent/subagent_git.py` + `agent/subagent_render.py` + `agent/subagent_display.py`.

- До **100 задач** в одном вызове (`agent/subagent.py:80`). Каждая — отдельная `ApiSession`, изолированный контекст, свой stream. Конкурентность ограничена семафором (`subagent.max_concurrency` в `config/ui.py`, дефолт 12): сотни задач можно слать, они дренируются батчами без 429.
- Формы вызова (нормализуются в `tools/subagent_specs.py:build_subagent_task_specs`):
  - `prompt` — одиночный субагент;
  - `tasks: [...]` — fan-out: параллельные независимые задачи;
  - `phases: [{name, tasks}, ...]` — фазовая оркестрация: фазы исполняются по
    порядку (фаза N+1 стартует после завершения N), агенты внутри фазы — параллельно.
    Один вызов прогоняет весь конвейер, живая панель помечает завершённые фазы зелёным;
  - `items + stages` — pipeline: для каждого `item` стадии `stages` идут
    последовательно (шаблон `{item}`/`{index}`/`{stage}`), а сами items — параллельно.
- Поля задачи:
  - `prompt` (обязательно);
  - `phase` (обязательно для оркестрации) — группа/стадия в живой панели (`Scout`, `Implement`, `Verify`);
  - `label` (обязательно) — короткое 1-2 слова имя задачи в панели; без `phase`/`label` панель показывает безликое `Agents`/`Sub1`;
  - `model` (опционально — display name или id из любого включённого провайдера);
  - `role` — профиль из `agent/subagent_api._ROLE_PROFILES`: `coder`, `researcher`, `reviewer`, `planner`, `coordinator`. Роль меняет инструкции, но не ограничивает инструменты;
  - `preset` — готовая заготовка-роль из `.data/agents/<name>/AGENT.md` (`agent/agent_presets.py`): даёт инструкции/модель, передаёшь только `prompt`;
  - `depends_on` — список 1-based индексов задач, которые должны завершиться ДО этой. Их результаты инжектятся в промпт. Задачи без зависимостей идут параллельными волнами, зависимые ждут (`_resolve_dependencies` → топосортировка в волны). В `phases` зависимость от предыдущей фазы проставляется автоматически.
- **`isolate`** — по умолчанию `false`: субагенты пишут ПРЯМО в общую рабочую директорию, поэтому работу надо резать на независимые слайсы (каждый субагент владеет своими файлами). `isolate=true` — каждому отдельный git worktree (см. ниже).
- Все субагенты запускаются в agent-mode и получают одинаковый полный набор инструментов (кроме явно запрещённых внутри субагента `poll` и вложенного `subagent`).
- Дисплей: `SubagentTracker` / `SubagentBuffer` + `SwarmOverlay` — интерактивная панель в нижней зоне (навигация стрелками, Enter — детали задачи), плюс `agent/subagent_display.py`. Инкрементальный лог завершившихся — `progress.md` в run-директории.
- Итерации субагента не ограничены — цикл идёт до завершения задачи (или стопа по контексту/ошибке).
- Внутри субагента **запрещены** `poll` и вложенный `subagent`. `web_search` **разрешён** — субагент умеет искать в сети.
- Список доступных моделей и заготовок-пресетов подмешивается в системный промпт через `system_prompt._build_subagent_models_block` / `_build_agent_presets_block`.

### Координатор-паттерн (общие имена/контракты)

Когда несколько субагентов должны использовать одни и те же имена/сигнатуры — первая задача с `role="coordinator"` читает код и пишет контракты в общий scratchpad (`.data/subagents/<run-id>/shared.md`), а остальные задачи `depends_on: [1]` получают его spec в промпте. Контракт решается один раз, без merge-конфликтов потом.

### Git worktree-изоляция (mode=agent)

`agent/subagent_git.py` создаёт **отдельный git worktree** для каждого субагента под `.data/subagents/<run-id>/sub-<N>/` на ветке `subagent/<run-id>-<N>`:

- Файловые изменения **не текут** между субагентами и **не трогают** основной рабочий каталог до явного merge.
- Контекстная подмена workdir — через `ContextVar` в `tools/_paths.py` (`use_working_dir(path)`). `resolve_path` использует `os.path.normpath`, **не** `realpath` — чтобы симлинки `.venv` / `node_modules` внутри worktree разрешались в свои кэши, но запись не утекала по симлинкам.
- После завершения оркестратор:
  - авто-коммитит всё что сделал субагент (`git add -A -f` обязателен, иначе `.gitignore` отрежет легитимные правки в `.data/`),
  - удаляет worktree-директорию, ветка остаётся,
  - возвращает: branch, commit SHA, файлы, diff stat, готовые команды `git show <sha>` / `git log -p <branch>` с актуальным `base_sha` (никогда не хардкодит `main` / `master`).
- `cleanup_stale_branches()` при следующем запуске сабагентов удаляет все `subagent/*` ветки кроме текущего HEAD — мусор не копится.

Дальше пользователь решает руками: `git merge`, `git cherry-pick <sha>` или `git branch -D <branch>`. Merge-конфликты — на пользователе.

---

## Скиллы

`skills/manager.py` + `skills/registry.py` + `tools/skill_tool.py`. Скилл — директория `.data/skills/<name>/SKILL.md` с frontmatter:

```markdown
---
name: docx-mastery
description: Полное руководство по работе с .docx через create_docx
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

---

## Память (memory)

`memory/` — долговременная память агента (порт memory-системы Claude Code). Хранит факты, **не выводимые** из кода/git/`AGENTS.md`: предпочтения пользователя, обратную связь по стилю работы, контекст проекта, внешние референсы. Файлы — markdown с YAML-подобным frontmatter в `.data/memory/<slug>-<sha1[:10]>/` (изоляция по рабочей директории), а также **глобальная** кросс-проектная память `.data/memory/_global` (`config/paths.py global_memory_dir`).

Четыре типа памяти: `user`, `feedback`, `project`, `reference`.

Три механизма (`memory/memdir.py`, `memory/extract.py`, `memory/insights.py`):

- **Инжекция в промпт** — `format_memory_block()` собирает всю память проекта и глобальную в блок `<persistent_memory>` системного промпта следующих сессий (`system_prompt._build_memory_block`, лимит ~6000 символов).
- **Автоизвлечение** — `extract_memories(transcript, working_dir)` запускается фоново из интерактивного цикла каждые ~6 сообщений: лёгкий one-shot вызов активной модели (изолированный provider, без tools, история сессии не трогается — как `api_recap`) читает транскрипт + манифест уже сохранённого и решает, какие новые устойчивые факты сохранить (или какие обновить по тому же имени). Fire-and-forget: UI не блокируется, ошибки проглатываются.
- **Ручное редактирование моделью** — инструменты `memory_write` / `memory_list` / `memory_read` (`tools/memory_tool.py`): модель сама сохраняет факт, когда замечает что-то долговременное.

---

## Хуки (hooks)

`src/hooks/` (`runner.py`, `matcher.py`, `schema.py`) + конфиг `config/hooks.py` + файл `.data/hooks.json`.

Что это: внешние команды/HTTP-запросы, которые вызываются на ключевых событиях жизненного цикла агента и могут блокировать или дополнять его действия (совместимо с claude-code).

События (`HOOK_EVENTS`, `src/hooks/schema.py:36`):

| Событие | Когда |
|---------|-------|
| `PreToolUse` | Перед выполнением инструмента — может `approve` / `block`. |
| `PostToolUse` | После выполнения инструмента. |
| `UserPromptSubmit` | При отправке пользовательского сообщения. |
| `Stop` | Остановка хода агента. |
| `SessionStart` | Старт сессии. |
| `SessionEnd` | Завершение сессии. |

Конфиг `.data/hooks.json` — маппинг событий на матчеры/хуки. Кэш по mtime файла (`config/hooks.py`, `has_hooks()`), правки подхватываются без рестарта.

Контракт (совместим с claude-code):

- На **stdin** подаётся JSON payload: `{event, tool_name, tool_input, ...}`.
- Хук может вернуть **JSON в stdout** с полями: `decision` (`approve` | `block`), `reason`, `continue` (false → попросить остановиться), `systemMessage` (показать пользователю), `additionalContext` (подмешать в историю), `hookSpecificOutput.additionalContext`;
- либо просто **exit code**: `0` = ок, `2` = block (stderr → reason), иное = ошибка (не блок).

Типы хуков (`HookSpec`): `command` (shell-команда, таймаут по умолчанию 30 сек) и `http` (URL, опциональные `headers`). Поддерживаются matcher-обёртки и permission-style фильтр `if`.

Точки вызова: `tools/registry.py` (`_run_pre_tool_hooks` / `_run_post_tool_hooks`), `config/hooks.py has_hooks()`.

---

## Сессионные заметки (session notes)

`src/session/notes.py` — шаблон заметок о текущем состоянии и следующих шагах для длительной автономной работы. Файл: `<session.dir>/session_notes.md`, создаётся `ensure_session_notes()` при необходимости.

Блок с заметками инжектится в контекст через `format_session_notes_block()` (вызывается из `agent/messages.py:117`) — модель видит актуальное состояние сессии между ходами.

Шаблон (`_TEMPLATE`) содержит разделы: `# Session Title` (короткое описательное название сессии, 5-10 слов), `# Current State` (что активно делается, ближайшие шаги), `# Task specification` (что просил пользователь, ограничения и решения), `# Files and Functions` (важные файлы/функции и почему они важны), `# Workflow` (обычно запускаемые команды и как трактовать результат), `# Errors & Corrections` (ошибки, правки пользователя, неудачные подходы), `# Verification` (какие проверки запускались, вердикты), `# Worklog` (краткий пошаговый журнал работы).

Лимиты: `_MAX_NOTE_CHARS = 12000` (весь файл), `_MAX_MESSAGE_CHARS = 1200` (одно сообщение/раздел в промпте).

---

## MCP

`apis/mcp_client.py` + `config/mcp.py` + меню `commands/menus/mcp.py`. Клиент [Model Context Protocol](https://modelcontextprotocol.io/).

- Конфиг: `.data/mcp_servers.json` — `{servers: [{id, command, args, env, enabled, transport: "stdio"}]}`.
- Транспорт — только **stdio** (через `mcp.client.stdio.stdio_client`). SSE/HTTP — точка расширения в `_connect_async`.
- `MCPManager` — singleton с фоновым asyncio-loop в отдельном потоке (sync TOOL_REGISTRY вызовы → async SDK через `run_coroutine_threadsafe`).
- При старте interactive вызывается `init_mcp_from_config()`: подключает enabled-сервера и регистрирует их tools в `TOOL_REGISTRY` под именами `mcp__<server_id>__<tool_name>`. JSON-схемы попадают в `get_tool_schemas("agent")` через `get_mcp_tool_schemas()`. В `planning` режиме **не** подмешиваются.
- Меню `/mcp`: список со статусами (`●`/`○`/`✗`), добавление, enable/disable, удаление, реконнект.
- `shutdown_mcp()` в `finally` корректно закрывает `AsyncExitStack`'и и останавливает фоновый loop.
- `CallToolResult.content` нормализуется: text → как есть, image → плейсхолдер с MIME, resource → URI. `isError=True` → префикс `[MCP tool error]`.

---

## LSP

`apis/lsp_client.py` + `config/lsp.py` + меню `commands/menus/lsp.py`. Свой клиент LSP по stdio JSON-RPC.

- `LSPManager` — singleton с фоновым asyncio-loop в отдельном потоке (по аналогии с MCPManager).
- Конфиг — `.data/lsp_servers.json`. Если файла нет, используются `DEFAULT_SERVERS`: `pyright` (Python), `typescript-language-server` (TS/JS), `gopls` (Go), `rust-analyzer` (Rust). Сервер включается только если есть бинарь в PATH.
- Инструменты — **только** `lsp_references` и `lsp_diagnostics` (`execute_lsp_references` / `execute_lsp_diagnostics`, `apis/lsp_client.py:655-665`). `lsp_definition` и `lsp_hover` **удалены**. Оба инструмента read-only, доступны и в planning mode.
- Диагностики запускаются после write/patch/create (`auto_diagnostics`).
- `shutdown_lsp()` в `finally` корректно гасит дочерние процессы.

---

## Telegram-мост

`apis/telegram.py` + `agent/telegram_handler.py` + `agent/tg_menu.py` + `agent/tg_format.py`. Зеркалит события агента в Telegram-чат и принимает оттуда сообщения.

- Singleton `TelegramBridge`. Запускается из `commands/interactive.py`, если `telegram_enabled` и заданы `telegram_bot_token` + `telegram_chat_id`.
- Использует [aiogram 3](https://docs.aiogram.dev/). Реализует:
  - Очередь отправки с throttle (~30 msg/s) и автоматическим разбиением длинных сообщений (лимит 4000 символов).
  - Параллельное чтение `stdin` и `incoming_queue` — что придёт раньше, то и обрабатывается.
  - Typing-индикатор (`send_chat_action` каждые 4 сек) во время стрима.
  - Thinking-плейсхолдер «💭 thinking…», редактируется в финальный ответ.
  - Зеркалирование reasoning_content и финального текста.
  - Reply-клавиатуру и inline-меню (`/menu` → быстрые действия, переключение режима agent/planning/swarm, stop агента).
  - Slash-команды от бота маршрутизируются в основной агент.
- `TelegramEventHandler` оборачивает обычный `RichEventHandler` и дополнительно шлёт в TG старт/итог tool-вызовов, обновления плана, статусы субагентов.
- Меню `/tg`: токен / чат / тест соединения / on-off без рестарта CLI.

---

## Headless / CI

`commands/headless.py` — режим `python src/main.py run "..."` для CI/CD, pre-commit, cron, pipe.

- Никакого prompt_toolkit и Rich Live: финальный текст в **stdout**, прогресс в **stderr**, exit code 0/1/2.
- `stdin` подхватывается, если не tty: `git diff | python src/main.py run "коммит-сообщение"` приклеит diff в конец промпта.
- Опции: `--api/-A`, `--model/-m`, `--workdir/-w`, `--json` (структурированный вывод `{ok, text, model, workdir, elapsed_sec}`), `--quiet/-q`, `--timeout`, `--allow-all` (wildcard `*=allow,process`).
- Без `--allow-all` ставит `NECLI_HEADLESS=1` → инструменты в режиме `ask` авто-отказывают (а не зависают на TTY-меню), в stderr предупреждение.
- Использует тот же `agent/loop.run_agent` (без LiveStream), что и интерактив.

Примеры:

```bash
uv run python src/main.py run "посчитай строки в проекте" --quiet
git diff --staged | uv run python src/main.py run "напиши коммит" --json | jq -r .text
uv run python src/main.py run --api openai --allow-all --timeout 300 "прогон линтеров и фикс"
```

---

## Система разрешений

`config/permissions.py` — гранулярный контроль над выполнением инструментов.

| Scope | Хранение | Время жизни |
|-------|----------|-------------|
| `session` | в памяти | до `/new` |
| `process` | в памяти | до выхода из CLI |
| `forever` | `config.json["tool_permissions"]` | навсегда |

Три решения: `ask` (дефолт), `allow`, `deny`.

Приоритет: `session > process > forever > "ask"`. Wildcard `"*"` поддерживается на каждом уровне как fallback для всех инструментов без явного решения.

В цикле:

1. `agent/executor._execute_single` перед запуском tool проверяет `get_decision(tool_name)`.
2. При `deny` — сразу `ToolResult(status="error")` без выполнения.
3. При `ask` — компактный overlay (`ui/overlays.py`) с вариантами allow once / session / process / forever и deny once. Долгосрочные запреты меняются через `/permissions`.
4. В headless `NECLI_HEADLESS=1` заставляет `confirm_tool_call` отказывать без зависания.

Меню `/permissions` показывает все эффективные решения с указанием scope и позволяет менять/сбрасывать.

---

## UI и темы (CLI)

`ui/shell.py` — постоянный **prompt_toolkit Application**, который разделяет scrollback (статику) и динамическую зону; мост Rich→prompt_toolkit, в пути отрисовки **нет** `rich.live.Live`. `ui/prompt.py` добавляет вставку из буфера, изображения и эхо, а `ui/overlays.py` обслуживает интерактивные виджеты нижней зоны (`select_menu`, `panel_menu`, `ask_text`, `confirm`) — все меню оформлены как карточки/плоские списки с палитрой, колонками, прокруткой и подсказками.

Очередь ходов агента — `commands/agent_queue.py`: **строго последовательная**; поле ввода доступно **всегда**, даже во время ответа агента; ожидающие сообщения показываются строками над полем; стрелка вверх снимает отложенный батч.

Горячие клавиши и поведение:

- **Enter** — отправить. **Esc+Enter** или `\\` в конце строки — перенос.
- **Tab** — циклить mode: `agent ↔ planning ↔ swarm` (иконки 🚀 / 🧠 / 🔮).
- **Ctrl+V** — вставить текст из буфера (через `xclip` / `xsel` / `wl-paste` / `pbpaste`).
- **Ctrl+P** — вставить изображение из буфера: сохраняется в `.data/clipboard_images/`, в тексте — плейсхолдер `[imageN]`, передаётся в multimodal `HumanMessage`.
- **Ctrl+O** — toggle expanded/compact replay: перерисовывает весь вывод сессии из `agent/render_store.py` через `agent/render_replay.py` (полные превью без обрезки ↔ компактные).
- **Ctrl+C** обрабатывается как событие клавиши (прерывание хода/ввода), **Ctrl+D** — выход.
- История ввода — `.data/history` (FileHistory + ThreadedHistory).
- Автокомплит — `ui/completer.py`: slash-команды + файлы (`@`-prefix или после `/cd`).
- Интерактивная панель субагентов (навигация стрелками, Enter — детали).
- Интерактивный `/stats` с вкладками (session / hands / models / tools / history), спарклайны, бары.
- Статус-строка под полем: TTFB, токены, оценка cost и заполнение контекста. Живые кадры рисуются Shell без `rich.live.Live`.

`config/themes.py` — система тем по семантическим ролям (`accent`, `success`, `warning`, `error`, `info`, `magenta`, `purple`, `muted`, `dim_text`, `bar_filled`, `bg_code`, `bg_output`, `bg_select`).

Встроенные темы: `dracula` (дефолт), `monokai`, `catppuccin`, `nord`, `gruvbox`, `tokyo-night`, `solarized`, `one-dark`. Любую роль можно переопределить через `set_custom_color(role, color)`. Доступ из кода — `from config.themes import t; t("accent")`. Меню — `/themes`.

Языки интерфейса (`config/i18n.py`): `en`, `ru`, `de`, `fr`, `zh` (`SUPPORTED_LANGS`, дефолт `en`). Меню — `/lang`.

Лимиты и подсказки — `config/ui.py` (`limits`): `max_width = 100`, `streaming_max_lines = 40`, `max_result_length = 15000`, `subagent.max_concurrency = 12`.

---

## Логирование

`logger.py` использует **loguru**. Логи раскидываются по файлам с ротацией (в dev — `src/logs/`, в frozen-режиме — `~/.necli/logs`):

| Файл | Что пишется |
|------|-------------|
| `logs/general.log` | Всё подряд (INFO+), что не попало в другие слои (config, session, main). |
| `logs/agent.log` | Агентный цикл: итерации, nudge, авто-компрессия, субагенты, планировщик, system prompt, skills. |
| `logs/ai.log` | Стриминг ответа, парсинг tool calls, sanitizer, рендеринг. |
| `logs/api.log` | HTTP-уровень, retry, throttle, raw text preview, токены. |
| `logs/tools.log` | Tool calls с аргументами (без больших payload'ов) и result-сводки. |
| `logs/ui.log` | События UI / клавиатуры / меню / slash-команды. |
| `logs/errors.log` | ERROR+ со всех слоёв (дублирование), краткий формат: тип и сообщение исключения одной строкой, **без полного трейсбека**. |

Правила:

- Большие payload'ы (content, b64, find/replace/insert) **исключаются** из preview-логирования.
- `_LAYER_FILTERS` в `logger.py` использует точный префиксный матчинг: `name == p or name.startswith(p + ".")`. **`"agent.stream"` НЕ матчит `agent.stream_tool_exec`** — это подчёркивание, не точка. Новые `agent.stream_*` нужно явно перечислять в `_LAYER_FILTERS["ai"]`.
- Ротация 2 MB, retention 5 файлов, compression zip, `enqueue=True`, encoding utf-8.
- Стандартный `logging` перехватывается в loguru через `InterceptHandler` — большинство модулей используют `logging.getLogger(__name__)`, и без перехвата их записи теряются.
- Шумные сторонние библиотеки глушатся до WARNING.
- `request_id` пробрасывается через `ContextVar` (`request_id_var`) и печатается в каждую запись (трейсинг одной операции через слои).

Не читай логи целиком — только `tail -n` нужного файла либо `read` с `lines`.

---

## Структура проекта

```
src/  (точка входа: src/main.py)
├── main.py                       # Click CLI (cli / run); поднимает RLIMIT_NOFILE
├── system_prompt.py              # сборка финального промпта + subagent/MCP/memory блоки
├── planner.py                    # Plan, :::call plan, .plan.md
├── models.py                     # каталог моделей, pricing, context limits
├── logger.py                     # loguru конфигурация (слои, ротация, request_id)
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
│   ├── executor.py               # _execute_single + permission checks
│   ├── events.py                 # AgentEventHandler protocol + RichEventHandler
│   ├── context.py                # AgentContext (plan, mode, fs snapshot, ...)
│   ├── messages.py               # build_first_message / nudge / fs delta / session notes
│   ├── sanitizer.py              # очистка ответа модели
│   ├── think.py                  # THINK-блоки
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
│   └── commit_agent.py           # генерация коммита для /commit
│
├── apis/                         # API-провайдеры и интеграции (без LangChain)
│   ├── registry.py               # load_all, get_provider, resolve_api_model
│   ├── agent_adapter.py          # ApiSession, api_send_message, compress, api_recap
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
│   ├── definitions/*.json        # встроенные шаблоны провайдеров
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
│   └── menus/                    # agents, api, autoprune, help, history, insights,
│                                 # lang, lsp, mcp, params, permissions, proxy, skills,
│                                 # stats, telegram, themes, _editor, _style
│
├── config/                       # настройки и пути
│   ├── settings.py               # config.json get/set/cache (+ tool_format_force_native)
│   ├── paths.py                  # .data/, sessions/, skills/, memory/, ensure_dirs
│   ├── constants.py              # READ_ONLY_TOOLS, IGNORE_DIRS, ...
│   ├── data_cleanup.py           # автоочистка мусора из .data при старте (раз в сутки)
│   ├── themes.py                 # 8 встроенных тем + семантические роли
│   ├── permissions.py            # scopes session/process/forever
│   ├── mcp.py / lsp.py / hooks.py / pinned.py
│   ├── ui.py                     # лимиты и подсказки (limits.*)
│   └── i18n.py                   # переводы интерфейса (en/ru/de/fr/zh)
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
│   ├── extract.py                # фоновое автоизвлечение фактов (one-shot вызов модели)
│   └── insights.py               # /insights: метрики + анализ моделью → HTML-отчёт + память
│
├── tools/                        # все инструменты
│   ├── registry.py               # TOOL_REGISTRY + planning/swarm режимы + hooks
│   ├── parser.py / call_parser.py # парсер fenced :::call блоков
│   ├── shell.py / web_search.py / web_fetch.py / image_search.py
│   ├── subagent.py / subagent_specs.py / skill_tool.py / poll.py / expand_result.py
│   ├── memory_tool.py            # memory_write / memory_list / memory_read
│   ├── file_readers.py
│   ├── arg_validation.py         # алиасы/коэрция/диагностика аргументов tool-call
│   ├── background.py             # фоновый запуск тяжёлых команд (background=true)
│   ├── _paths.py / _html_unescape.py / json_repair.py / models.py
│   └── file_ops/                 # read.py, write.py, patch.py, grep.py,
│                                 # _fuzzy.py, docx_writer.py, docx_screenshot.py,
│                                 # _html_preprocess.py, _docx_reference.py,
│                                 # _docx_sources.py, _docx_whitespace.py,
│                                 # _pandoc.py, project_check.py
│
├── ui/                           # терминальный ввод/вывод (prompt_toolkit)
│   ├── shell.py                  # постоянный Application + static/dynamic зоны
│   ├── overlays.py               # интерактивные виджеты нижней зоны
│   ├── prompt.py                 # clipboard/images/echo поверх Shell
│   ├── completer.py              # slash + файловый автокомплит
│   ├── menu.py / poll.py / file_context.py
│   ├── clipboard.py / clipboard_copy.py / formatting.py
│   ├── _filters.py / _emoji_width.py / _keyreader.py
│   └── terminal_title.py
│
├── logs/                         # ротация loguru-логов
└── .data/                        # рантайм-состояние (см. раздел Конфигурация)
```

---

## Инсайты (/insights)

`memory/insights.py` + меню `commands/menus/insights.py`. Команда `/insights` строит
развёрнутый HTML-отчёт о том, как пользователь взаимодействует с агентом, по ВСЕМ
сохранённым сессиям.

Pipeline (`generate_insights`):

1. **Сбор** — `_load_all_sessions()` тянет все сессии из `session/storage`.
2. **Локальные метрики** — `collect_metrics()` без модели: сообщения, активные дни,
   топ-инструменты (по `:::call` в ответах), типы ошибок, часы активности, средняя
   длина сообщения/сессии, пересечения сессий (параллельная работа).
3. **Анализ моделью** — `build_transcript()` собирает транскрипт всех сессий, `api_insights`
   (чистый контекст активной модели, без tools, история не трогается — как `api_recap`)
   возвращает СТРОГИЙ JSON: at-a-glance, области работы, intents/session_types,
   достижения, категории трения с примерами, фичи к пробованию, паттерны, горизонт,
   правки для AGENTS.md и durable-факты для памяти. Текст — на языке интерфейса
   (`config.i18n.get_lang()`).
4. **Рендер** — `render_html()` собирает самостоятельный HTML (светлая Inter-тема,
   барные чарты, copy-кнопки, навигация), сохраняется в `.data/insights/report-<ts>.html`.
5. **Память** — `save_memories()` пишет durable-факты (`memory_write`), до `_MAX_MEMORY_ITEMS`.
   Из CLI-меню по умолчанию `persist_memory=False` — отчёт без записи в память.

`/insights` вызывается из уже работающего event loop, поэтому корутина исполняется в
отдельном потоке со своим циклом (`commands/menus/insights.py:_run_async`).
