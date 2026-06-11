# necli — техническая документация

> Архитектура, внутренности и устройство кода. Здесь — как всё устроено внутри.

**Терминальный AI-агент. CLI + Telegram-мост, одно ядро.**

API-only клиент для LLM: прямые вызовы провайдеров через **httpx** (свои реализации, без LangChain), гибридный режим инструментов (fenced `:::call` блоки + native function calling), стриминг с инлайн-выполнением tool-блоков, агентный цикл до 500 итераций, до 100 параллельных субагентов с **git worktree-изоляцией**, DAG-зависимостями и ролями/пресетами, Python workflow-оркестратор с настоящими фазами поверх субагентов, планировщик, скиллы, долговременная память между сессиями с автоизвлечением фактов, MCP, LSP, SSH-пул, git-based undo/redo, авто-pruning истории, автоочистка `.data/`, голосовой ввод, headless-режим для CI и зеркало в Telegram.

Python ≥ 3.10. Управление зависимостями — `uv`.

---

## Quick start

```bash
pip install uv
uv sync

# CLI (Rich Live в терминале)
uv run python src/main.py interactive --api onlysq

# Headless (CI / pipe / cron)
echo "сосчитай строки .py" | uv run python src/main.py run --quiet --allow-all
```

Ключи API хранятся в `.data/config.json` (`api_keys`) и редактируются через меню `/api` внутри CLI.

---

## Содержание

1. [Точки входа](#точки-входа)
2. [Архитектура](#архитектура)
3. [Конфигурация и `.data/`](#конфигурация-и-data)
4. [API-провайдеры](#api-провайдеры)
5. [Агентный цикл](#агентный-цикл)
6. [Формат tool calls](#формат-tool-calls)
7. [Инструменты](#инструменты)
8. [Режимы (agent / planning)](#режимы-agent--planning)
9. [Планировщик](#планировщик)
10. [Сессии, токены, стоимость](#сессии-токены-стоимость)
11. [Slash-команды](#slash-команды)
12. [Субагенты](#субагенты)
13. [Workflows](#workflows)
14. [Скиллы](#скиллы)
15. [Память (memory)](#память-memory)
16. [SSH](#ssh)
17. [MCP](#mcp)
18. [LSP](#lsp)
19. [Telegram-мост](#telegram-мост)
20. [Headless / CI](#headless--ci)
21. [Система разрешений](#система-разрешений)
22. [UI и темы (CLI)](#ui-и-темы-cli)
23. [Тесты](#тесты)
24. [Логирование](#логирование)
25. [Структура проекта](#структура-проекта)

---

## Точки входа

`src/main.py` — Click-группа с командами (запуск: `uv run python src/main.py <cmd>`; файл сам добавляет `src/` в `sys.path`):

| Команда | Назначение |
|---------|------------|
| `interactive` | Основной TUI на Rich Live + prompt_toolkit. |
| `run` | Headless: один проход агента, результат в stdout, exit code 0/1/2. |

Опции `interactive`:

| Флаг | Назначение |
|------|------------|
| `--api, -A` | Активировать провайдера на этот запуск (`--api onlysq`). |
| `--model, -m` | Модель (id или display name). |
| `--workdir, -w` | Рабочая директория (по умолчанию — `cwd`). |
| `--resume, -r` | Восстановить сессию по id или префиксу. |

При старте `src/main.py` поднимает `RLIMIT_NOFILE` до 8192 (httpx-стримы + множество открытых файлов сессий быстро упираются в дефолт 1024 на Linux).

---

## Архитектура

```
┌────────────┐  user input   ┌──────────────────┐
│ ui/prompt  │ ────────────► │ commands/        │
│ (PT)       │               │ interactive.py   │
└────────────┘               └──┬───────────────┘
        ▲                       │ /slash → commands/slash.py
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

- `commands/interactive.py` — TUI с Rich Live.
- `apis/telegram.py` + `agent/telegram_handler.py` — Telegram-мост.

LangChain **не используется**. Своя минимальная замена `langchain_core.messages` живёт в `apis/messages.py` (`SystemMessage` / `HumanMessage` / `AIMessage` / `ToolMessage` + `AIMessageChunk` с `__add__`). Общая логика стрима, retry/throttle, нативных tool calls и multimodal-вложений — в `apis/base.py:BaseProvider`. Провайдеры наследуются от него: `openai_provider`, `anthropic_provider`, `google_provider`, `custom_provider`.

---

## Конфигурация и `.data/`

Вся персистентность лежит в `.data/` рядом с проектом (`config/paths.py`):

```
.data/
├── config.json                # основной конфиг (config/settings.py)
├── apis.json                  # альтернативный источник API-конфигов
├── mcp_servers.json           # MCP-сервера
├── lsp_servers.json           # LSP-сервера (опционально, есть дефолты)
├── ssh_hosts.json             # SSH-хосты
├── history                    # история ввода prompt_toolkit
├── docx_reference.docx        # генерится один раз для create_docx
├── pinned_sessions.json       # закреплённые сессии (не удаляются автоочисткой)
├── .last_cleanup              # маркер последней автоочистки .data (раз в сутки)
├── clipboard_images/          # вставленные через Ctrl+P изображения
├── uploads/                   # кэш загруженных картинок (напр. из Telegram)
├── subagents/<run-id>/sub-N/  # git worktrees субагентов
├── undo/<key>/git/            # git-стор undo/redo (отдельный от проектного .git)
├── docx_sources/              # HTML-исходники + .template.docx для round-trip
├── agents/<name>/AGENT.md     # заготовки-пресеты субагентов
├── memory/<project>/*.md      # долговременная память проекта (см. раздел)
├── workflows/*.py             # сохранённые Python workflow-скрипты
├── workflow_runs/<run-id>/    # state/result/artifacts запусков workflow
├── ui.json                    # override эмодзи/лейблов/цветов инструментов
├── skills/<name>/SKILL.md     # скиллы (см. раздел)
└── sessions/<id>/
    ├── history.json           # полные сообщения сессии
    ├── summary.json           # агрегаты cost/tokens
    └── .plan.md               # активный план (если есть)
```

**Автоочистка `.data/`** (`config/data_cleanup.py`) запускается тихо в фоне при старте, не чаще раза в сутки (маркер `.last_cleanup`), с безопасной retention-политикой: сессии старше 30 дней (но последние 100 и все pinned сохраняются), `subagents/`/`workflow_runs/` старше 14 дней, временные `clipboard_images/`/`docx_*`/`uploads/` старше 7 дней, мёртвые ssh-сокеты, undo-репы старше 60 дней (кроме текущей рабочей директории). Конфиги, реестры, `agents/`, `skills/`, `memory/` не трогаются.

Ключевые поля `config.json` (см. `config/settings.py`):

| Поле | Назначение |
|------|------------|
| `active_api`, `active_api_model` | Текущий провайдер и модель. |
| `api_providers`, `api_keys` | Пользовательские провайдеры и их ключи. |
| `tool_permissions` | Постоянные разрешения инструментов. |
| `theme`, `theme_custom` | Активная тема и переопределение ролей. |
| `telegram_bot_token`, `telegram_chat_id`, `telegram_enabled` | Telegram-мост. |
| `think_enabled` | Глобальный THINK-режим (рассуждения вслух). |
| `temperature`, `max_tokens` | Generation params. |

Доступ — `config.get(key, default)` / `config.set_value(key, value)`. Словарь кэшируется, мутации идут только через `set_value`.

---

## API-провайдеры

Провайдер описывается `ApiProviderDefinition` (`apis/models.py`). Загрузчик `apis/registry.py`:

1. Читает встроенные шаблоны из `apis/definitions/*.json`.
2. Поверх накладывает пользовательские из `config.json["api_providers"]`.
3. По полю `type` выбирает фабрику: `openai_provider`, `anthropic_provider`, `google_provider` или `custom_provider` (свой aiohttp/httpx-клиент для OpenAI-совместимых прокси с `reasoning_content`).

Инстансы кэшируются по `(provider_id, model_id)`. `reload_providers()` сбрасывает кэш — нужен после правок через `/api`.

Встроенные определения (`apis/definitions/`): anthropic, cerebras, cohere, completitions, deepseek, fireworks, google, groq, hyperbolic, mistral, openai, openrouter, perplexity, sambanova, together, xai. Пользовательский OnlySQ и любые другие OpenAI-совместимые прокси добавляются через `/api`.

Каждая модель: `id`, `display_name`, `context_window`, `input_price` / `output_price` (USD за 1M токенов). Цены используются в `session/session.py:_compute_cost`: при наличии реального `usage` от провайдера — он, иначе fallback на tiktoken-оценку.

---

## Агентный цикл

`agent/loop.py` содержит две реализации над одним и тем же `apis.agent_adapter.api_send_message`:

- **`run_agent_interactive`** — основной цикл с `LiveStream` (`agent/stream.py`). Стримит ответ, парсит `:::call <tool> ... call:::` блоки по мере поступления, выполняет инлайн через `agent/stream_tool_exec.py`. После каждой итерации скармливает `ToolResult`'ы модели как новое сообщение. До `MAX_ITERATIONS = 500`.
- **`run_agent`** — headless-вариант без Rich Live. Используется в `commands/headless.py`.

Ключевые детали:

- **`AgentContext`** (`agent/context.py`) хранит план, рабочую директорию, mode (`agent | planning`), event-handler, snapshot файлов из `agent/fs_watcher.py`, `step_tracker` (`agent/project_stats.py`), счётчик nudge и флаги прерывания.
- На каждый раунд `agent/messages.build_first_message` / `_build_result_message` дополняет сообщение блоком плана и трекингом изменений ФС.
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
   Парсятся `tools/call_parser.py`. Лимит `MAX_TOOL_CALLS_PER_MESSAGE = 50`. Три формата:
   - **JSON-инструменты** — body это JSON.
   - **Контентные** (`write_file` / `create_file` / `create_docx`) — `path="..."` в шапке, body — сырой контент.
   - **`patch_file`** — секции `--- FIND --- / --- REPLACE --- / --- INSERT ---` или атрибут `delete_lines`.

2. **Native function calling.** Глобальный единый переключатель `tool_format_force_native` (команда `/tool_format`, читается в `system_prompt._resolve_native_tools`): `True` → native function calling для всех провайдеров, `False` (дефолт) → fenced. В native-режиме `BaseProvider` биндит JSON-схемы из `apis/tool_schemas.py`; полученные `tool_calls` конвертируются в текстовые fenced-блоки (`_tool_calls_to_text_blocks`) и проходят через тот же парсер — UI одинаковый, но результаты возвращаются модели как структурные `ToolMessage` (не как текстовый транскрипт). Системный промпт полностью изолирует режимы: в native-варианте нет ни одного упоминания fenced-синтаксиса.

Особенности:

- При пустых `args` после стрима (баг ряда прокси) делается fallback non-stream запрос за корректным JSON.
- Прокси (OnlySQ и др.) html-эскейпят кавычки/угловые скобки в SSE; декодирование — единый канонический модуль `tools/_html_unescape.py`, используется в `tools/call_parser.py`, `apis/agent_adapter.py`, `agent/display.py`.
- `usage` (`input`, `output`, `reasoning`) пробрасывается из ответа провайдера в `Message.usage`.
- Длина fence для контентных инструментов выбирается динамически.

---

## Инструменты

Единый реестр — `tools/registry.py:TOOL_REGISTRY`. JSON-схемы для native — `apis/tool_schemas.py`. Одинаковые имена работают и в fenced, и в native режиме.

| Категория | Инструменты |
|-----------|-------------|
| Shell | `shell` |
| Чтение | `read_files` (alias `read_file`), `grep_files`, `find_files`, `ls`, `tree` |
| Запись / правка | `write_file`, `create_file`, `patch_file`, `apply_diff`, `create_docx` |
| ФС-управление | `delete_file`, `rename_file`, `copy_file`, `move_file`, `mkdir`, `rmdir` |
| DOCX | `create_docx`, `docx_screenshot` (рендер страницы .docx/.pdf в PNG через LibreOffice + PyMuPDF) |
| Сеть | `web_search`, `ssh` |
| Мета | `subagent`, `workflow`, `skill`, `poll`, `expand_tool_result` |
| Память | `memory_write`, `memory_list`, `memory_read` (долговременная память проекта, см. раздел Память) |
| LSP | `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_diagnostics` |
| MCP | `mcp__<server_id>__<tool_name>` (динамически после `init_mcp_from_config`) |
| Control-tools | `plan` (чеклист задачи, не исполняет код), `think` (рассуждение вслух — только при включённом THINK-режиме) |

`plan` и `think` — это control-tools: попадают в JSON-схемы (`apis/tool_schemas.py`), но кода не выполняют, а лишь ведут чеклист / рисуют мысль в UI. `think` подмешивается в схемы только при активном THINK-режиме.

Существенные детали:

- **`read_files`** — кэш по `abs_path → {mtime, size, ranges, binary}`. Повторное чтение без изменений → короткий маркер `NOT CHANGED`. Поддерживает `.docx`, `.pdf`, изображения, csv/tsv, Excel. Лимиты: `MAX_READ_FILES = 20`, `MAX_LINES = 1000`. Бинарные файлы кэшируются флагом `binary=True`: повторный read картинки не возвращает `image_path` — модель её уже видела.
- **`patch_file`** — `find/replace` (одиночный или массив `patches`), `line + insert` для вставки, `delete_lines="10-15"`. Fuzzy-матч с warning'ом (`tools/file_ops/_fuzzy.py`).
- **`apply_diff`** — `git apply` с fallback на `patch -p1`, dry-run перед применением. Multi-hunk и multi-file в одном вызове.
- **`create_docx`** — HTML → Pandoc 3.x → DOCX. Inline-CSS (`color`, `font-family`, `font-size`, `background-color`, `text-align`) применяется пост-процессом python-docx. LaTeX `$...$` / `$$...$$` → нативные OMML-формулы. Round-trip read→edit→write через двухпроходный pandoc (html + markdown). Подробности — в `AGENTS.md` и скилле `docx-mastery`.
- **`docx_screenshot`** — рендерит страницу(ы) .docx или .pdf в PNG и прикрепляет к следующему ходу модели (multimodal), чтобы она «увидела» реальную вёрстку — шрифты, поля, таблицы, формулы, разрывы. Pipeline: .docx → .pdf через LibreOffice headless (`soffice --convert-to`), .pdf → PNG через PyMuPDF при 200 DPI. Аргументы: `page` (одна страница) или `pages` (`"2-5"`, `"1,3,7"`, `"2-4,8,10-11"`, список, или `"all"`).
- **`shell`** — `cd` и `&&` / `||` явно запрещены парсером (`tools/shell.py`).
- **`web_search`** — DuckDuckGo (через `ddgs`) и/или прямой fetch URL через `trafilatura`. Результаты кэшируются.
- **`ssh`** — обёртка над OpenSSH с ControlMaster-пулом (см. раздел SSH).
- **`poll`** — запрос к пользователю с до 4 вариантами. В headless автоматически отказывает.
- **`apply_diff`** — применяет unified diff к рабочему дереву через `git apply` (fallback на `patch -p1`), dry-run перед применением. Multi-hunk / multi-file в одном вызове. После применения гоняет ruff по затронутым `.py`.
- **`expand_tool_result`** — длинные output'ы усекаются с маркером `expand via :::call expand_tool_result {"id": "..."}`; модель просит полный текст по id. Кэш `agent/result_cache.py` (FIFO, в памяти процесса).
- **LSP-tools** — `lsp_definition` / `lsp_references` / `lsp_hover` / `lsp_diagnostics`. Используют `apis/lsp_client.py:LSPManager`. По умолчанию `pyright`, `typescript-language-server`, `gopls`, `rust-analyzer` — если бинарь есть в PATH.

### Read-only / planning-mode

`tools/registry.py` экспортирует `READ_ONLY_TOOLS` (он же `PLANNING_TOOLS` из `config.constants`) — `{read_files, grep_files, tree, ls, find_files}`. В `planning` mode разрешены только они + `plan` + read-only LSP-инспекторы + `poll`/`web_search`/`skill`.

### Лимиты вызовов

- `MAX_TOOL_CALLS_PER_MESSAGE = 50` — лишние блоки в одном сообщении отбрасываются.
- Каждый вызов выполняется отдельно `agent/executor._execute_single` с тиканием спиннера и трекингом изменений ФС.

---

## Режимы (agent / planning)

Переключение по `Tab` в prompt или через `/mode`. `AgentContext.toggle_mode` циклит `agent ↔ planning`.

| Режим | Иконка | Что разрешено |
|-------|--------|---------------|
| `agent` | 🚀 | Полный набор инструментов. Дефолт. |
| `planning` | 🧠 | Только read-only: `read_files`, `grep_files`, `tree`, `ls`, `find_files` + LSP-read + `plan` / `poll` / `web_search` / `skill`. Любая попытка вызвать write/shell/etc. возвращает `build_blocked_result`. |

При переключении `agent/loop` инжектит `MODE_SWITCH_AGENT` / `MODE_SWITCH_PLANNING` из `prompts/_notices.py` в первое сообщение нового раунда. Аналогично `/think` — `THINK_ON_NOTICE` / `THINK_OFF_NOTICE`.

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

  Поддерживаемые действия: `create`, `update` (по `step` / `index` / `title`), `add_step`, `remove_step`. Лимит — 25 шагов, минимум 3.
- После каждого ответа `agent/loop._process_plan_commands` применяет команды, обновляет `.plan.md` в директории сессии и рисует панель `render_plan_panel`.
- `LiveStream` обрабатывает блоки plan в стриме: показывает прогресс-бар (`▮▮▯▯ 2/4`) прямо во время ответа.
- Окно `prev/current/next` инжектится в контекст следующего сообщения через `Plan.render_for_context`.
- При завершении (`is_complete`) `.plan.md` удаляется, при загрузке (`load_plan_file`) восстанавливается из markdown.
- `/plan-clear` сбрасывает план и удаляет файл.
- Если модель долго работает без tool-вызовов, но план не завершён — посылается nudge с напоминанием.

---

## Сессии, токены, стоимость

`session/session.py:Session`:

- `id` — изначально техническая метка `YYYYMMDD_HHMMSS_<uid>`. При первом user-сообщении переименовывается в `<slug>_YYYYMMDD_HHMMSS` (slug — первые 20 «слов»-символов). Директория двигается через `shutil.move`.
- `messages: list[Message]` с ролями `user | assistant | system | tool_result`.
- `_compressed_stats` — снапшот сообщений/стоимости после `/compress`.
- `_pre_compress_messages` / `_pre_compress_at` — бэкап ДО compress, переживает рестарт CLI, используется `/decompress`. Повторный compress не перезаписывает первоначальный бэкап.

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

`apis/_context_pruner.py` обрезает старые tool-результаты из истории перед запросом, экономя токены без потери актуального контекста. Работает в обоих форматах (text-mode блоки `$ read_files ...` и native `ToolMessage`). Триггеры вытеснения: (A) файл перезаписан в более позднем раунде; (B) тот же путь прочитан позже (дедуп); (C) крупное чтение старше `_KEEP_RECENT_ROUNDS=4` раундов; (D) hard-cap — любое чтение/вывод старше `_HARD_EVICT_ROUNDS=10`. Свежий раунд не трогается. Вместо контента ставится маркер `[content evicted to save tokens — ...]` с подсказкой перечитать. Вытеснение выводов тяжёлых инструментов (`shell`, `grep_files`, `tree`, `ls`, `web_search`, lsp_*) — по возрасту (C/D).

### Undo / redo файловых изменений

`agent/undo_store.py` — git-based timeline правок, **отдельный** от проектного `.git`. GIT_DIR лежит в `.data/undo/<sha1(workdir)>/git` (проектный репозиторий не трогается, `.gitignore`-паттерны в его `info/exclude`). Перед каждым раундом `snapshot_round` коммитит состояние файлов; `refs/undo/tip` отмечает верхушку timeline (новый снапшот после `/undo` отрезает старое «будущее»). Команда `/undo [N]` двигает рабочее дерево на N раундов назад (`reset --hard` + `clean -fd`), `N<0` — redo вперёд.

---

## Slash-команды

Диспетчер — `commands/slash.py:_handle_slash` → `SlashResult`. Состояние и побочные эффекты обрабатывает `commands/slash_handler.py`. Метаданные команд (имя, категория, help-строка) — единый реестр `commands/registry.py`; `/help` и автокомплит `ui/completer.py` берут список оттуда автоматически.

**Правило мест**: новая команда обязана быть в `commands/registry.py` (метаданные → help+completer подхватываются сами) и в `commands/slash.py` (диспетчер `_handle_slash`); если она меняет состояние / запускает async — ещё и в `commands/slash_handler.py`. См. `AGENTS.md`.

| Команда | Что делает |
|---------|------------|
| `/help`, `/?` | Справка. |
| `/api`, `/apis` | Меню провайдеров: добавление/правка, ключи, активная модель, tool format. |
| `/models` | Picker моделей активного провайдера. |
| `/model [<name>]` | Без аргумента — показать текущую модель и цену; с именем — прямое переключение. |
| `/params` | Generation params (temperature, max_tokens). |
| `/copy [N]` | Скопировать последние N ответов ассистента в буфер обмена (по умолчанию 1). |
| `/new` | Новый чат: чистит сессию и `ApiSession`, сбрасывает session-level разрешения. |
| `/sessions` | Меню сохранённых сессий с cost/tokens preview. |
| `/session <id>` | Переключение по id/префиксу. |
| `/compress` | Сжать историю через активную модель, сохранить бэкап. |
| `/decompress` | Восстановить оригинальные сообщения из бэкапа. |
| `/stats [N]` | Статистика за N дней (по умолчанию 7) + общая. |
| `/history [N]` | Последние N действий агента (по умолчанию 10). |
| `/cd PATH` | Сменить рабочую директорию (для tools и file-completer). |
| `/permissions`, `/perm` | Allow/deny инструментов на уровне session / process / forever. |
| `/skills`, `/skill` | Меню скиллов. |
| `/agents` | CRUD заготовок-пресетов субагентов (`.data/agents/<name>/AGENT.md`). |
| `/workflows [RUN_ID]` | Список запусков workflow или просмотр фаз/агентов конкретного запуска. |
| `/ssh` | Управление SSH-хостами. |
| `/mcp`, `/mcps` | MCP-сервера: добавление, enable/disable, реконнект. |
| `/lsp`, `/lsps` | LSP-сервера: список / enable / диагностика. |
| `/tg` | Telegram-мост. |
| `/themes`, `/theme` | Выбор темы и кастомизация ролей. |
| `/lang` | Язык интерфейса. |
| `/think` | Toggle THINK-режима (рассуждения вслух). |
| `/tool_format` | Toggle глобального native function calling (`tool_format_force_native`) — иначе fenced. |
| `/plan` | Показать текущий план. |
| `/reflect` | Рефлексия: модель анализирует сессию и предлагает обновить `AGENTS.md`. |
| `/undo [N]` | Откатить/вернуть файловые изменения на N раундов через отдельный git-стор (`agent/undo_store.py`). N<0 — redo. |
| `/branch` | Управление git-ветками рабочего репозитория. |
| `/commit` | Сгенерировать коммит через `agent/commit_agent.py` и закоммитить. |

---

## Субагенты

`tools/subagent.py` + `agent/subagent.py` + `agent/subagent_api.py` + `agent/subagent_git.py` + `agent/subagent_render.py`.

- До **100 параллельных задач** в одном вызове. Каждая — отдельная `ApiSession`, изолированный контекст, свой stream. Конкурентность ограничена семафором (`subagent.max_concurrency` в `config/ui.py`, дефолт 12): сотни задач можно слать, они дренируются батчами без 429.
- Поля задачи (`SubagentTask`, парсятся в `tools/subagent.py` и `agent/loop._execute_subagent_call`):
  - `prompt` (обязательно);
  - `model` (опционально — display name или id из любого включённого провайдера);
  - `role` — профиль из `agent/subagent_api._ROLE_PROFILES`: `coder`, `researcher`, `reviewer`, `planner`, `coordinator`. Роль меняет инструкции, но не ограничивает инструменты;
  - `preset` — готовая заготовка-роль из `.data/agents/<name>/AGENT.md` (`agent/agent_presets.py`): даёт инструкции/модель, передаёшь только `prompt`;
  - `depends_on` — список 1-based индексов задач, которые должны завершиться ДО этой. Их результаты инжектятся в промпт. Задачи без зависимостей идут параллельными волнами, зависимые ждут (`_resolve_dependencies` → топосортировка в волны).
- Все субагенты запускаются в agent-mode и получают одинаковый полный набор инструментов (кроме явно запрещённых внутри субагента `poll` и вложенного `subagent`).
- Дисплей: `SubagentTracker` / `SubagentBuffer` рисуют несколько строк прогресса параллельно; в финале выводят свод по задачам. Инкрементальный лог завершившихся — `progress.md` в run-директории.
- Лимит итераций субагента — `MAX_SUBAGENT_ITERATIONS = 120`.
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

## Workflows

`tools/workflow.py` + `workflows/runner.py` — Python-first orchestration layer поверх существующих субагентов. `subagent` остаётся низкоуровневым fan-out/DAG инструментом, а `workflow` добавляет настоящие фазы, фазовые артефакты, cache/resume и сохранённый state.

Inline-вариант:

```json
{
  "name": "research-impl-verify",
  "isolate": true,
  "phases": [
    {
      "title": "Research",
      "tasks": [
        {"label": "api", "role": "researcher", "prompt": "Research API layer"},
        {"label": "ui", "role": "researcher", "prompt": "Research UI layer"}
      ]
    },
    {
      "title": "Verify",
      "tasks": [
        {"label": "verify", "role": "reviewer", "prompt": "Run tests and verify integration"}
      ]
    }
  ]
}
```

Python script-вариант (`.data/workflows/<name>.py` или inline `script`):

```python
meta = {"name": "research-impl-verify"}

async def run(ctx):
    ctx.phase("Research")
    research = await ctx.parallel([
        lambda: ctx.agent("Research API layer", label="api", role="researcher"),
        lambda: ctx.agent("Research UI layer", label="ui", role="researcher"),
    ])

    ctx.phase("Verify")
    verify = await ctx.agent("Run tests and summarize risks", label="verify", role="reviewer")
    return {"research": research, "verify": verify}
```

DSL:

- `ctx.phase(title, detail="")` — открывает настоящую фазу в state/UI.
- `ctx.log(text)` — пишет narrative log текущей фазы.
- `ctx.agent(prompt, **opts)` — один субагент (`role`, `preset`, `model`, `label`).
- `await ctx.parallel([...])` — барьерная параллельная фаза.
- `await ctx.pipeline(items, ...)` — последовательные стадии на item, параллельно по items.

Лимит: **до 25 агентов на одну фазу** (`MAX_WORKFLOW_AGENTS_PER_PHASE`, накопительно по всем `parallel()`/`pipeline()` фазы) — превышение даёт явную ошибку, а не тихий срез. Прогресс рисует живая панель (`workflows/render.py`): единый фрейм с колонкой фаз слева (активная помечена `›`) и агентами активной фазы справа (модель · текущий инструмент · токены/инструменты/время), сверху — суммарная стоимость прогона.

State и артефакты пишутся в `.data/workflow_runs/<run-id>/`:

- `state.json` — фазы, агенты, статусы, cache keys.
- `result.json` — возвращаемое значение workflow.
- `agents/agent-N/prompt.txt`, `result.json`, `result.md` — per-agent артефакты.

Опции:

- `isolate` — по умолчанию `true`, каждый агент получает git worktree.
- `cache` — по умолчанию `true`.
- `resume_from_run_id` — reuse успешных agent-вызовов с совпавшим cache key.
- `fail_fast` — если `true`, первый failed agent прерывает workflow.
- `args` — dict, доступный Python workflow-скрипту как global `args`.

Python workflow исполняется с ограниченными builtins и allowlist импортов (`json`, `math`, `re`, `datetime`, `pathlib`, `itertools`, `functools`). Для произвольных действий используйте субагентов через `ctx.agent`, а не прямой доступ из workflow-скрипта.

Просмотр запусков: `/workflows` и `/workflows RUN_ID`.

---

## Скиллы

`skills/manager.py` + `tools/skill_tool.py`. Скилл — директория `.data/skills/<name>/SKILL.md` с frontmatter:

```markdown
---
name: docx-mastery
description: Полное руководство по работе с .docx через create_docx
---

...тело скилла...
```

Поведение:

- Скиллы обнаруживаются `discover_skills()` и подмешиваются в системный промпт через `build_skills_prompt` — модель видит каталог с описаниями.
- Чтобы активировать, модель вызывает `skill` с `{"name": "..."}`. Тело инжектится как user-message с маркером `━━━ СКИЛЛ АКТИВИРОВАН ━━━`.
- `disable-model-invocation: true` в frontmatter скрывает скилл из автокаталога (доступен только по явному вызову через `/skills`).
- Меню `/skills`: список / создание / добавление из директории / удаление.
- `reset_active_skills()` зовётся при `/new`.

---

## Память (memory)

`memory/` — долговременная память агента (порт memory-системы Claude Code). Хранит факты, **не выводимые** из кода/git/`AGENTS.md`: предпочтения пользователя, обратную связь по стилю работы, контекст проекта, внешние референсы. Файлы — markdown с YAML-подобным frontmatter в `.data/memory/<project>/` (изоляция по рабочей директории: `slug-<sha1[:10]>`).

Четыре типа памяти: `user`, `feedback`, `project`, `reference`.

Три механизма (`memory/memdir.py`, `memory/extract.py`):

- **Инжекция в промпт** — `format_memory_block()` собирает всю память проекта в блок `<persistent_memory>` системного промпта следующих сессий (`system_prompt._build_memory_block`, лимит ~6000 символов).
- **Автоизвлечение** — `extract_memories(transcript, working_dir)` запускается фоново из интерактивного цикла каждые 6 сообщений: лёгкий one-shot вызов активной модели (изолированный provider, без tools, история сессии не трогается — как `api_recap`) читает транскрипт + манифест уже сохранённого и решает, какие новые устойчивые факты сохранить (или какие обновить по тому же имени). Fire-and-forget: UI не блокируется, ошибки проглатываются.
- **Ручное редактирование моделью** — инструменты `memory_write` / `memory_list` / `memory_read` (`tools/memory_tool.py`): модель сама сохраняет факт, когда замечает что-то долговременное.

---

## SSH

`tools/ssh.py` + `config/ssh.py` + меню `commands/menus/ssh.py`.

- Хосты: `.data/ssh_hosts.json` — `{alias, host, user, port, identity_file, password?}`.
- В инструменте `ssh` используются **только алиасы** — IP/host напрямую запрещены.
- Под капотом — OpenSSH с **ControlMaster пулом**: одно соединение на алиас живёт между вызовами.
- `close_all_connections()` в `finally` interactive-цикла корректно закрывает мультиплексеры.
- Поддержка `upload` / `download` (через `scp`).
- Опасные команды (`rm -rf /`, форк-бомбы) детектируются эвристикой и просят `confirm_tool_call`.
- Меню `/ssh`: список с статусами, добавление, удаление, тест соединения.

---

## MCP

`apis/mcp_client.py` + `config/mcp.py` + меню `commands/menus/mcp.py`. Клиент [Model Context Protocol](https://modelcontextprotocol.io/).

- Конфиг: `.data/mcp_servers.json` — `{servers: [{id, command, args, env, enabled, transport: "stdio"}]}`.
- Транспорт — только **stdio** (через `mcp.client.stdio.stdio_client`). SSE/HTTP — точка расширения в `_connect_async`.
- `MCPManager` — singleton с фоновым asyncio-loop в отдельном потоке (sync TOOL_REGISTRY вызовы → async SDK через `run_coroutine_threadsafe`).
- При старте interactive вызывается `init_mcp_from_config()`: подключает enabled-сервера и регистрирует их tools в `TOOL_REGISTRY` под именами `mcp__<server_id>__<tool_name>`. JSON-схемы попадают в `get_tool_schemas("agent")` через `get_mcp_tool_schemas()`. В `planning` режиме НЕ подмешиваются.
- Меню `/mcp`: список со статусами (`●`/`○`/`✗`), добавление, enable/disable, удаление, реконнект.
- `shutdown_mcp()` в `finally` корректно закрывает `AsyncExitStack`'и и останавливает фоновый loop.
- `CallToolResult.content` нормализуется: text → как есть, image → плейсхолдер с MIME, resource → URI. `isError=True` → префикс `[MCP tool error]`.

---

## LSP

`apis/lsp_client.py` + `config/lsp.py` + меню `commands/menus/lsp.py`. Свой клиент LSP по stdio JSON-RPC.

- `LSPManager` — singleton с фоновым asyncio-loop в отдельном потоке (по аналогии с MCPManager).
- Конфиг — `.data/lsp_servers.json`. Если файла нет, используются `DEFAULT_SERVERS`: `pyright` (Python), `typescript-language-server` (TS/JS), `gopls` (Go), `rust-analyzer` (Rust). Сервер включается только если есть бинарь в PATH.
- Поддерживаемые методы: `textDocument/definition`, `textDocument/references`, `textDocument/hover`, диагностики после write/patch/create (если `auto_diagnostics=True`).
- Инструменты `lsp_definition` / `lsp_references` / `lsp_hover` / `lsp_diagnostics` — read-only, доступны и в planning mode.
- `shutdown_lsp()` в `finally` корректно гасит дочерние процессы.

---

## Telegram-мост

`apis/telegram.py` + `agent/telegram_handler.py` + `agent/tg_menu.py`. Зеркалит события агента в Telegram-чат и принимает оттуда сообщения.

- Singleton `TelegramBridge`. Запускается из `commands/interactive.py`, если `telegram_enabled` и заданы `telegram_bot_token` + `telegram_chat_id`.
- Использует [aiogram 3](https://docs.aiogram.dev/). Реализует:
  - Очередь отправки с throttle (~30 msg/s) и автоматическим разбиением длинных сообщений (лимит 4000 символов).
  - Параллельное чтение `stdin` и `incoming_queue` в `_read_user_with_tg` — что придёт раньше, то и обрабатывается.
  - Typing-индикатор (`send_chat_action` каждые 4 сек) во время стрима.
  - Thinking-плейсхолдер «💭 thinking…», редактируется в финальный ответ.
  - Зеркалирование reasoning_content и финального текста.
  - Reply-клавиатуру и inline-меню (`/menu` → быстрые `/new`, `/compress`).
  - Slash-команды от бота маршрутизируются в основной агент через `_apply_tg_action`.
- `TelegramEventHandler` оборачивает обычный `RichEventHandler` и дополнительно шлёт в TG старт/итог tool-вызовов, обновления плана, статусы субагентов.
- Меню `/tg`: токен / чат / тест соединения / on-off без рестарта CLI.

---

## Headless / CI

`commands/headless.py` — режим `python src/main.py run "..."` для CI/CD, pre-commit, cron, pipe.

- Никакого prompt_toolkit и Rich Live: финальный текст в **stdout**, прогресс в **stderr**, exit code 0/1/2.
- `stdin` подхватывается, если не tty: `git diff | python src/main.py run "коммит-сообщение"` приклеит diff в конец промпта.
- Опции: `--api`, `--model`, `--workdir`, `--json` (структурированный вывод `{ok, text, model, workdir, elapsed_sec}`), `--quiet`, `--timeout`, `--allow-all` (wildcard `*=allow,process`).
- Без `--allow-all` ставит `NECLI_HEADLESS=1` → инструменты в режиме `ask` авто-отказывают (а не зависают на TTY-меню), в stderr предупреждение.
- Использует тот же `agent/loop.run_agent` (без LiveStream), что и интерактив.

Примеры:

```bash
uv run python src/main.py run "посчитай строки в проекте" --quiet
git diff --staged | uv run python src/main.py run "напиши коммит" --json | jq -r .text
uv run python src/main.py run --api onlysq --allow-all --timeout 300 "прогон линтеров и фикс"
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
3. При `ask` — `commands/permission_prompt.confirm_tool_call` (интерактивный prompt с вариантами allow once / allow session / allow forever / deny / deny forever).
4. В headless `NECLI_HEADLESS=1` заставляет `confirm_tool_call` отказывать без зависания.

Меню `/permissions` показывает все эффективные решения с указанием scope и позволяет менять/сбрасывать.

---

## UI и темы (CLI)

`ui/prompt.py` — обёртка над **prompt_toolkit**:

- **Enter** — отправить. **Esc+Enter** или `\\` в конце строки — перенос.
- **Tab** — циклить mode (`agent ↔ planning`).
- **Ctrl+V** — вставить текст из буфера (через `xclip` / `xsel` / `wl-paste` / `pbpaste`).
- **Ctrl+P** — вставить изображение из буфера: сохраняется в `.data/clipboard/`, в тексте — плейсхолдер `[imageN]`, передаётся в multimodal `HumanMessage`.
- **Ctrl+O** — toggle expanded/compact replay: перерисовывает весь вывод сессии из `agent/render_store.py` через `agent/render_replay.py` (полные превью без обрезки ↔ компактные).
- **Ctrl+C** во время ввода — отмена строки; **Ctrl+D** — выход.
- История ввода — `.data/history` (FileHistory + ThreadedHistory).
- Автокомплит — `ui/completer.make_combined_completer`: slash-команды + файлы (`@`-prefix или после `/cd`).
- Stream stats в нижней строке Live: TTFB, токены `↓ since_last / total`, оценка cost, `ctx N/limit (X%)`.

`config/themes.py` — система тем по семантическим ролям (`accent`, `success`, `warning`, `error`, `info`, `magenta`, `purple`, `muted`, `dim_text`, `bar_filled`, `bg_code`, `bg_output`, `bg_select`).

Встроенные темы: `dracula` (дефолт), `monokai`, `catppuccin`, `nord`, `gruvbox`, `tokyo-night`, `solarized`, `one-dark`. Любую роль можно переопределить через `set_custom_color(role, color)`. Доступ из кода — `from config.themes import t; t("accent")`. Меню — `/themes`.

---

## Тесты

Pytest-набор в `tests/`. Покрывает ядро: парсеры, file ops, session, agent helpers, apis, config, ui, skills, planner.

### Запуск

```bash
uv run pytest                        # все тесты
uv run pytest tests/unit/tools/      # один подмодуль
uv run pytest tests/unit/agent/test_sanitizer.py -v
uv run pytest -k "fuzzy or patch"    # по имени
uv run pytest -m "not slow"          # без медленных
```

### Маркеры (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: slow tests (>1s)",
    "requires_pandoc: requires pandoc binary",
    "requires_git: requires git binary",
    "requires_node: requires node binary",
    "requires_network: requires network access",
    "integration: integration tests",
    "e2e: end-to-end tests",
]
addopts = "-ra --strict-markers"
asyncio_mode = "auto"
```

### Ключевые фикстуры (`tests/conftest.py`)

| Фикстура | Назначение |
|----------|------------|
| `tmp_workdir` | Подменяет рабочую директорию через `tools._paths.set_working_dir` на `tmp_path`. |
| `isolated_data` | Изолирует `.data/` (config.json, apis.json, sessions/, skills/) во временной папке + сбрасывает кэши `config.settings._config_cache` и `apis.config._apis_cache`. |
| `clear_read_cache_between_tests` (autouse) | Чистит `tools/file_ops/read._READ_CACHE` до и после каждого теста. |
| `make_tool_call` | Фабрика `ToolCall(name, args)`. |

### Что покрывается / что нет

**Покрыто:** все парсеры (call-блоки fenced/native, JSON-repair, HTML-unescape, plan-команды), file ops (включая fuzzy и read-cache), Session lifecycle и compress с бэкапом, токенайзеры (tiktoken / Gemini heuristic / multiplier-based), agent helpers (sanitizer, stream-parser, result_cache), APIs (AIMessageChunk merge, throttle-retry, реестр провайдеров, валидация JSON-дефиниций), Config (settings, permissions с тремя уровнями + wildcard, темы, MCP/LSP/SSH).

**Намеренно слабо покрыто** (низкий ROI): `agent/loop.py`, `agent/stream.py` — heavy async pipeline; UI rendering (`agent/display.py`, `subagent_render.py`, `tg_menu.py`); `commands/interactive.py` — event loop; `apis/lsp_client.py`, `apis/mcp_client.py` — требуют живые subprocess-сервера.

### Чеклист нового теста

1. Изолируй сайд-эффекты: `tmp_workdir` / `isolated_data` / `monkeypatch`, а не запись в реальный `.data/` или cwd.
2. Async-тесты помечай `@pytest.mark.asyncio` (auto-mode уже включён, но явный маркер — хороший тон).
3. Требуется внешний бинарь / сеть — `@pytest.mark.requires_*` и `shutil.which()` / `pytest.importorskip`.
4. `pytest -s` / `-v` для отладки, не `print`.

---

## Логирование

`logger.py` использует **loguru**. Логи раскидываются по файлам в `logs/` с ротацией:

| Файл | Что пишется |
|------|-------------|
| `logs/general.log` | Всё подряд (INFO+). |
| `logs/agent.log` | События цикла: итерации, nudge, авто-компрессия. |
| `logs/ai.log` | Превью запросов/ответов модели, usage, reasoning. |
| `logs/api.log` | HTTP-уровень, retry, throttle, raw text preview. |
| `logs/tools.log` | Tool calls с аргументами (без больших payload'ов) и result-сводки. |
| `logs/ui.log` | События UI / клавиатуры / меню. |
| `logs/errors.log` | ERROR+ с traceback'ами. |

Правила:

- Большие payload'ы (content, b64, find/replace/insert) **исключаются** из preview-логирования.
- HTML-сущности и подозрительные эскейпы логируются отдельным warning'ом.
- `_LAYER_FILTERS` в `logger.py` использует точное префиксное матчинг: `name == p or name.startswith(p + ".")`. **`"agent.stream"` НЕ матчит `agent.stream_tool_exec`** — это подчёркивание, не точка. Новые `agent.stream_*` нужно явно перечислять в `_LAYER_FILTERS["ai"]`.

Не читай логи целиком — только `tail -n` нужного файла либо `read_files` с `lines`.

---

## Структура проекта

```
necli-api/  (дерево ниже — содержимое src/, точка входа: src/main.py)
├── main.py                       # Click CLI (interactive / run); поднимает RLIMIT_NOFILE
├── system_prompt.py              # сборка финального промпта + subagent/MCP/memory блоки
├── planner.py                    # Plan, :::call plan, .plan.md
├── models.py                     # каталог моделей, pricing, context limits
├── logger.py                     # loguru конфигурация
├── pyproject.toml                # uv-managed зависимости + pytest config
├── AGENTS.md                     # детальные инструкции для AI-агента
│
├── prompts/                      # системные промпты по секциям
│   ├── _base.py                  # общие блоки (tool format, efficiency, ...)
│   ├── _agent.py                 # agent-mode дополнение
│   ├── _planning.py              # planning-mode дополнение
│   └── _notices.py               # NUDGE / COMPRESS / MODE_SWITCH / THINK
│
├── agent/                        # агентный цикл
│   ├── loop.py                   # run_agent / run_agent_interactive
│   ├── stream.py                 # LiveStream c инлайн-выполнением tool блоков
│   ├── stream_parser.py          # поиск partial/complete :::call блоков
│   ├── stream_tool_exec.py       # выполнение блоков по мере появления
│   ├── stream_render.py          # Rich Live composition
│   ├── executor.py               # _execute_single + permission checks
│   ├── events.py                 # AgentEventHandler protocol + RichEventHandler
│   ├── context.py                # AgentContext (plan, mode, fs snapshot, ...)
│   ├── messages.py               # build_first_message / nudge / fs delta
│   ├── sanitizer.py              # очистка ответа модели
│   ├── think.py                  # THINK-блоки
│   ├── subagent.py               # buffer / multiplexer
│   ├── subagent_api.py           # запуск sub-сессии (ApiSession per task) + роли/DAG
│   ├── subagent_git.py           # git worktree-изоляция per task
│   ├── subagent_render.py        # рендер прогресса субагентов
│   ├── agent_presets.py          # заготовки-роли из .data/agents/<name>/AGENT.md
│   ├── telegram_handler.py / tg_menu.py
│   ├── fs_watcher.py             # snapshot изменений рабочей директории
│   ├── project_stats.py          # StepTracker для трекинга изменений
│   ├── display.py / diff_render.py / syntax.py / theme_preview.py
│   ├── block_stream.py           # BlockStreamer — поблочный markdown-стрим
│   ├── render_store.py / render_replay.py  # буфер вывода + Ctrl+O replay
│   ├── commit_agent.py           # генерация коммита для /commit
│   ├── undo_store.py             # git-стор undo/redo (/undo), отдельный GIT_DIR
│   └── result_cache.py           # кэш длинных tool результатов (expand_tool_result)
│
├── apis/                         # API-провайдеры и интеграции (без LangChain)
│   ├── registry.py               # load_all, get_provider, resolve_api_model
│   ├── agent_adapter.py          # ApiSession, api_send_message, compress, restore
│   ├── base.py                   # BaseProvider — httpx SSE + native tool calls
│   ├── messages.py               # SystemMessage / HumanMessage / AIMessage / ToolMessage
│   ├── _retry.py                 # throttle/retry поверх non-stream и стрима
│   ├── _context_pruner.py        # pruning старых read/tool-результатов из истории
│   ├── models.py                 # ApiProviderDefinition / ApiModelInfo
│   ├── tool_schemas.py           # OpenAI-style schemas + agent/planning фильтр
│   ├── config.py                 # apis.json / config.json["api_providers"]
│   ├── model_discovery.py        # автообнаружение моделей у провайдера
│   ├── mcp_client.py             # MCPManager + регистрация в TOOL_REGISTRY
│   ├── lsp_client.py             # LSPManager + lsp_* tools
│   ├── telegram.py               # TelegramBridge (aiogram)
│   ├── definitions/*.json        # встроенные шаблоны провайдеров
│   └── providers/                # openai_provider / anthropic_provider /
│                                 # google_provider / custom_provider
│
├── commands/                     # точки входа и slash-команды
│   ├── interactive.py            # main loop CLI
│   ├── headless.py               # `run` команда (CI)
│   ├── slash.py / slash_handler.py
│   ├── registry.py               # единый реестр slash-команд (метаданные → help/completer)
│   ├── interactive_state.py / interactive_status.py
│   ├── permission_prompt.py
│   ├── helpers.py
│   └── menus/                    # api, ssh, mcp, lsp, telegram, permissions,
│                                 # themes, skills, params, history, lang, agents
│
├── config/                       # настройки и пути
│   ├── settings.py               # config.json get/set/cache
│   ├── paths.py                  # .data/, .data/sessions/, .data/skills/, ...
│   ├── constants.py              # READ_ONLY_TOOLS, IGNORE_DIRS, MAX_WORKFLOW_AGENTS_PER_PHASE, ...
│   ├── data_cleanup.py           # автоочистка мусора из .data при старте (раз в сутки)
│   ├── themes.py
│   ├── permissions.py
│   ├── mcp.py / lsp.py / ssh.py
│   └── i18n.py                   # переводы интерфейса
│
├── session/                      # сессии и persistence
│   ├── session.py                # Session, _compute_cost, compress_reset
│   ├── storage.py                # save/load/list_sessions/get_statistics
│   ├── message.py / tokens.py / _time.py
│
├── skills/                       # обнаружение и управление скиллами
│   └── manager.py
│
├── memory/                       # долговременная память агента (см. раздел Память)
│   ├── memdir.py                 # CRUD memory-файлов + format_memory_block/manifest
│   └── extract.py                # фоновое автоизвлечение фактов (one-shot вызов модели)
│
├── workflows/                    # Python workflow-оркестратор поверх субагентов
│   ├── runner.py                 # WorkflowRunner/Context: phase/parallel/pipeline, лимит агентов
│   ├── render.py                 # живая single-table панель прогресса (фазы + агенты)
│   └── specs.py                  # state-модели запусков (.data/workflow_runs)
│
├── tools/                        # все инструменты
│   ├── registry.py               # TOOL_REGISTRY + planning/read-only режим
│   ├── parser.py / call_parser.py # парсер fenced :::call блоков
│   ├── shell.py / ssh.py / web_search.py
│   ├── subagent.py / skill_tool.py / poll.py / expand_result.py
│   ├── memory_tool.py            # memory_write / memory_list / memory_read
│   ├── workflow.py               # запуск Python workflow (Workflow-инструмент)
│   ├── dir_ops.py / file_readers.py / file_checks.py
│   ├── _paths.py / _html_unescape.py / json_repair.py / models.py
│   └── file_ops/                 # read.py, write.py, patch.py, manage.py,
│                                 # _fuzzy.py, docx_writer.py, docx_screenshot.py,
│                                 # _html_preprocess.py, _docx_reference.py,
│                                 # _pandoc.py, diff_apply.py, project_check.py
│
├── ui/                           # терминальный ввод/вывод
│   ├── prompt.py                 # InputPrompt + PT bindings
│   ├── completer.py              # slash + файловый автокомплит
│   ├── menu.py / poll.py / file_context.py
│   ├── clipboard.py / clipboard_copy.py / formatting.py
│   ├── _filters.py / _emoji_width.py / _keyreader.py
│
├── tests/                        # pytest-набор (см. раздел Тесты)
├── logs/                         # ротация loguru-логов
└── .data/                        # рантайм-состояние (см. раздел Конфигурация)
```
