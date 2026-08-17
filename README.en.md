# necli

[Русский](README.md) | **English**

**necli** is a terminal AI agent with an interactive TUI and a headless mode for CI workflows. It connects directly to LLM providers over HTTP and supports streaming responses, tool calls, persistent sessions, MCP/LSP, background tasks, subagents, and native `.docx` and `.pptx` engines.

<img src="docs/img/1.png" width="800"/>
<img src="docs/img/2.png" width="800"/>
<img src="docs/img/3.png" width="800"/>
<img src="docs/img/4.png" width="800"/>

The project does not store API keys in its source code. Runtime state, sessions, provider configuration, and uploaded files are stored in `.data/` by default, which is excluded from Git. Set the `NECLI_HOME` environment variable to keep user data in a different location.

## Requirements

| Component | Requirement |
|---|---|
| Python | **3.10+** |
| Environment manager | [uv](https://docs.astral.sh/uv/) |
| API key | At least one compatible LLM provider |
| Operating system | Linux, macOS, or Windows; the file descriptor limit is raised only on Unix |

## Installation

Install `uv`, then create a synchronized environment from the committed `uv.lock`:

```bash
pip install uv
uv sync
```

Install the development extras to run the linter and test suite:

```bash
uv sync --extra dev
```

After synchronization, the CLI is available as `uv run necli`. Running the source file directly remains supported for compatibility: `uv run python src/main.py`.

## First Run

Start the interactive client and select a provider. API keys, models, and routers are configured through the built-in `/api` and `/models` menus, so secrets do not need to be passed as command-line arguments.

```bash
uv run necli cli
```

Main options for the interactive command:

| Option | Purpose |
|---|---|
| `--api`, `-A` | Temporarily select a provider for this run. |
| `--model`, `-m` | Specify a model ID or display name. |
| `--workdir`, `-w` | Set the agent's working directory. |
| `--resume`, `-r` | Resume a session by its ID or ID prefix. |

## Headless Mode and CI

The `run` command reads a prompt from stdin, writes the result to stdout, and returns an exit code. This makes it suitable for pipes, cron jobs, and CI.

```bash
printf 'Count the lines in the project Python files' \
  | uv run necli run --quiet --allow-all
```

When the turn finishes, the short mode prints a `✓ Worked 2m ⎿ 5⟳ · 12🛠 · ↑12K ↓4K` summary to stderr (per-tool progress lines go there too), keeping stdout clean for the answer. Use `--json` for machine-readable output, or `--full-json` for a full report (every model response and tool call with args and output, each event once). Interactive actions that require user input are unavailable in headless mode.

## Data and Security

> **Do not commit `.data/` to your repository.** It may contain API keys, conversation history, attachments, MCP configuration, and other user data.

| Path | Contents |
|---|---|
| `.data/config.json` | Main UI and agent-mode settings. |
| `.data/apis.json` | Providers, keys, and fallback routers. |
| `.data/sessions/` | Session history and statistics. |
| `.data/memory/` | Project-scoped and global long-term memory. |
| `.data/skills/` | User and built-in skills. |

See [DOCS.md](DOCS.md) for the complete description of the architecture, tool-call formats, modes, sessions, MCP, LSP, the Telegram bridge, and office document engines.

## Development

The project includes a locked dependency set, test configuration, and static-analysis rules. Run these checks before submitting changes:

```bash
uv sync --extra dev
uv run ruff format --check src tests
uv run ruff check src tests --no-cache
uv run pytest -q
```

To apply automatic formatting and safe linter fixes:

```bash
uv run ruff format src tests
uv run ruff check src tests --fix
```

## Project Structure

```text
src/
├── agent/          # agent loop, streaming, and subagents
├── apis/           # providers, adapters, MCP, and LSP
├── commands/       # CLI commands, slash commands, and menus
├── config/         # settings, paths, localization, and themes
├── docx_engine/    # DOCX creation, editing, and inspection
├── pptx_engine/    # PPTX creation, editing, inspection, and rendering
├── session/        # session storage and statistics
├── tools/          # file, shell, network, and office document tools
└── ui/              # prompt_toolkit interface

tests/              # regression and unit tests
```

## Developer Documentation

See [DOCS.md](DOCS.md) for the architecture, agent loop, tool-call format, subagent and memory internals, and codebase structure.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
