import shutil
import sys
from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

import config
from config.themes import t
from config.i18n import t as _
from logger import logger
from ui.menu import select_menu, _panel_menu_direct

console = Console()


_KNOWN_PROVIDERS = {
    "openai": ("OpenAI", "https://api.openai.com/v1", "openai_compatible", "openai"),
    "anthropic": ("Anthropic", "https://api.anthropic.com", "anthropic", "anthropic"),
    "google": ("Google Gemini", "https://generativelanguage.googleapis.com", "google", "google"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai_compatible", "openai"),
    "together": ("Together AI", "https://api.together.xyz/v1", "openai_compatible", "openai"),
    "fireworks": ("Fireworks AI", "https://api.fireworks.ai/inference/v1", "openai_compatible", "openai"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "openai_compatible", "openai"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "openai_compatible", "openai"),
    "mistral": ("Mistral AI", "https://api.mistral.ai/v1", "openai_compatible", "openai"),
}

_LOCAL_PROVIDERS = {
    "ollama":    ("Ollama (local)",            "http://localhost:11434/v1"),
    "lmstudio":  ("LM Studio (local)",         "http://localhost:1234/v1"),
    "llamacpp":  ("llama.cpp server (local)",  "http://localhost:8080/v1"),
    "vllm":      ("vLLM (local)",              "http://localhost:8000/v1"),
}


def _shorten_url(url: str, max_len: int = 36) -> str:
    if not url:
        return "—"
    u = url.replace("https://", "").replace("http://", "")
    if len(u) <= max_len:
        return u
    return u[: max_len - 1] + "…"


def _render_api_table(providers: list, active_api: str, selected: int, width: int) -> str:
    """Рендерит таблицу провайдеров с подсветкой выбранной строки."""
    table = Table(
        show_header=True,
        header_style="bold dim",
        border_style="dim",
        padding=(0, 1),
        show_edge=False,
        show_lines=False,
        expand=True,
    )
    table.add_column(_("api.col_name"), min_width=18, no_wrap=True, ratio=3)
    table.add_column(_("api.col_url"), min_width=24, no_wrap=True, ratio=4)
    table.add_column(_("api.col_models"), justify="right", min_width=8, no_wrap=True, ratio=1)
    table.add_column(_("api.col_status"), justify="right", min_width=10, no_wrap=True, ratio=1)

    bg_select = t("bg_select")

    # Provider rows
    for i, p in enumerate(providers):
        is_sel = selected == i
        is_active = p["id"] == active_api
        has_key = p["has_key"]
        models_count = len(p["models"])

        marker = "❯ " if is_sel else "  "
        if is_active and is_sel:
            name_style = "bold green"
        elif is_active:
            name_style = "green"
        elif is_sel:
            name_style = "bold white"
        else:
            name_style = ""

        row_bg = Style(bgcolor=bg_select) if is_sel else Style.null()

        name_cell = Text(marker + p["name"], style=name_style)
        url_cell = Text(_shorten_url(p.get("base_url") or ""), style="dim")
        models_cell = Text(str(models_count) if models_count else "—",
                           style="cyan" if models_count else "dim")
        if is_active:
            status_text = Text("● " + _("common.active"), style="green")
        elif has_key and models_count:
            status_text = Text(_("common.ready"), style="dim")
        elif not has_key:
            status_text = Text(_("api.status_no_key"), style="dim red")
        else:
            status_text = Text(_("api.status_no_models"), style="dim yellow")

        table.add_row(name_cell, url_cell, models_cell, status_text, style=row_bg)

    # Last row: + Add
    add_idx = len(providers)
    is_sel = selected == add_idx
    marker = "❯ " if is_sel else "  "
    style = "bold white" if is_sel else ""
    row_bg = Style(bgcolor=bg_select) if is_sel else Style.null()
    table.add_row(
        Text(marker + _("api.add_provider"), style=style),
        Text(_("api.add_hint"), style="dim"),
        Text(""), Text(""),
        style=row_bg,
    )

    total = len(providers) + 1
    panel = Panel(
        table,
        title=_("api.title"),
        title_align="left",
        subtitle=f"{selected + 1}/{total}",
        subtitle_align="right",
        border_style="dim",
        padding=(0, 1),
    )

    buf = StringIO()
    render_console = Console(file=buf, highlight=False, force_terminal=True,
                             width=width, color_system="truecolor")
    render_console.print(panel)
    return buf.getvalue()


def api_interactive():
    """Интерактивное меню управления API-провайдерами. Возвращает SlashResult."""
    from apis.registry import list_providers, reload_providers
    from commands.slash import SlashResult

    r = SlashResult()

    while True:
        providers = list_providers()
        active_api = config.get("active_api", "")
        active_model = config.get("active_api_model", "")

        total = len(providers) + 1  # providers + add

        initial = 0
        if active_api:
            for i, p in enumerate(providers):
                if p["id"] == active_api:
                    initial = i
                    break

        term_w = shutil.get_terminal_size((100, 24)).columns
        width = min(term_w, 110)

        def render_fn(sel: int) -> str:
            return _render_api_table(providers, active_api, sel, width)

        choice = _panel_menu_direct(
            render_fn,
            sys.stdout,
            _("menu.hint_nav"),
            total,
            initial,
        )

        if choice is None:
            return r

        if choice == len(providers):
            _api_add_menu()
            reload_providers()
            continue

        provider = providers[choice]
        result = _api_provider_detail(provider, active_api, active_model)
        if result is not None:
            return result
        continue


def _api_model_add(provider_id: str):
    """Добавление новой text-модели."""
    from apis.config import add_model_to_provider

    try:
        console.print()
        console.print(f"  [bold]💬 {_('api.col_model')}[/bold]")
        model_id = console.input(f"  [bold]{_('api.field_model_id')}:[/bold] ").strip()
        if not model_id:
            return
        display_name = console.input(f"  [bold]{_('api.field_display_name')}:[/bold] ").strip() or model_id
        ctx_str = console.input(f"  [bold]{_('api.field_context_window')}[/bold] [dim](128000):[/dim] ").strip()
        context_window = int(ctx_str) if ctx_str else 128_000
        in_str = console.input(f"  [bold]{_('api.field_input_price')}[/bold] [dim](0):[/dim] ").strip()
        input_price = float(in_str) if in_str else 0.0
        out_str = console.input(f"  [bold]{_('api.field_output_price')}[/bold] [dim](0):[/dim] ").strip()
        output_price = float(out_str) if out_str else 0.0
        add_model_to_provider(
            provider_id, model_id, display_name,
            context_window, input_price, output_price,
        )
        console.print(f"  [green]\u2713[/green] {_('api.model_added', name=display_name)}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _api_model_edit(provider_id: str, model):
    """Редактирование параметров существующей модели."""
    from apis.config import add_model_to_provider

    console.print()
    console.print(f"  [bold]{_('api.editing')}:[/bold] {model.display_name} ({model.id})")
    console.print(f"  [dim]{_('common.enter_keep')}[/dim]")
    console.print()
    try:
        display_name = console.input(f"  [bold]{_('api.field_display_name')}[/bold] [dim]({model.display_name}):[/dim] ").strip()
        ctx_str = console.input(f"  [bold]{_('api.field_context_window')}[/bold] [dim]({model.context_window}):[/dim] ").strip()
        in_str = console.input(f"  [bold]{_('api.field_input_price')}[/bold] [dim]({model.input_price}):[/dim] ").strip()
        out_str = console.input(f"  [bold]{_('api.field_output_price')}[/bold] [dim]({model.output_price}):[/dim] ").strip()
        add_model_to_provider(
            provider_id,
            model.id,
            display_name or model.display_name,
            int(ctx_str) if ctx_str else model.context_window,
            float(in_str) if in_str else model.input_price,
            float(out_str) if out_str else model.output_price,
        )
        console.print(f"  [green]\u2713[/green] {_('api.model_updated')}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _api_provider_edit(provider_id: str):
    """Редактирование параметров провайдера."""
    from apis.registry import get_definition, reload_providers
    from apis.config import add_api_config

    defn = get_definition(provider_id)
    if not defn:
        return

    console.print()
    console.print(f"  [bold]{_('api.editing')}:[/bold] {defn.name} ({provider_id})")
    console.print(f"  [dim]{_('common.enter_keep')}[/dim]")
    console.print()
    try:
        name = console.input(f"  [bold]{_('api.field_name')}[/bold] [dim]({defn.name}):[/dim] ").strip()
        base_url = console.input(f"  [bold]{_('api.field_base_url')}[/bold] [dim]({defn.base_url}):[/dim] ").strip()
        ptype = console.input(f"  [bold]{_('api.field_type')}[/bold] [dim]({defn.type}):[/dim] ").strip()
        add_api_config(
            provider_id=provider_id,
            name=name or defn.name,
            base_url=base_url or defn.base_url,
            provider_type=ptype or defn.type,
            api_format=getattr(defn, 'api_format', None) or "openai",
            models=[{
                "id": m.id,
                "display_name": m.display_name,
                "context_window": m.context_window,
                "input_price": m.input_price,
                "output_price": m.output_price,
            } for m in defn.models],
            default_model=defn.default_model or "",
        )
        reload_providers()
        console.print(f"  [green]\u2713[/green] {_('api.provider_updated')}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _api_provider_detail(provider: dict, active_api: str, active_model: str):
    """Меню детали провайдера. Возвращает SlashResult или None."""
    from apis.registry import get_definition, reload_providers
    from apis.config import remove_api_config, set_api_key, get_api_key
    from commands.slash import SlashResult

    r = SlashResult()
    pid = provider["id"]

    while True:
        defn = get_definition(pid)
        if not defn:
            return None

        is_active = pid == active_api
        has_key = bool(get_api_key(pid))
        status = f"[green]{_('common.active')}[/green]" if is_active else f"[dim]{_('common.inactive')}[/dim]"
        key_status = f"[green]{_('common.set')}[/green]" if has_key else f"[red]{_('common.not_set')}[/red]"

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        console.print()
        console.print(f"  [bold yellow]{defn.name}[/bold yellow]  {status}")
        console.print(f"  [dim]ID: {pid} · Type: {defn.type} · URL: {defn.base_url}[/dim]")
        console.print(f"  [dim]API key: {key_status}[/dim]")
        if defn.models:
            model_names = [m.display_name for m in defn.models]
            console.print(f"  [dim]{_('api.col_models')}: {', '.join(model_names)}[/dim]")
        else:
            console.print(f"  [dim]{_('api.col_models')}: {_('common.none')}[/dim]")
        console.print()

        actions = []
        default_model = defn.default_model or (defn.models[0].id if defn.models else "")
        hint = f"{_('api.col_model').lower()}: {default_model}" if default_model else _("api.status_no_models")
        use_label = _("api.switch_model") if is_active else _("api.use")
        actions.append({"label": use_label, "hint": hint})
        actions.append({"label": _("api.set_key")})
        actions.append({"label": _("api.edit_provider"), "hint": _("api.edit_provider_hint")})
        actions.append({"label": _("api.manage_models")})
        actions.append({"label": _("api.refresh_models"), "hint": _("api.refresh_hint")})
        actions.append({"label": _("api.delete"), "hint": _("api.delete_permanent")})
        actions.append({"label": _("common.back")})

        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 6:
            return None

        if choice == 0:
            if not has_key and defn.requires_auth:
                console.print(f"  [red]{_('api.set_key_first')}[/red]")
                continue
            model_id = defn.default_model or (defn.models[0].id if defn.models else "")
            if not model_id:
                console.print(f"  [red]{_('api.add_one_model_first')}[/red]")
                continue

            config.set_active_api(pid)
            config.set_active_api_model(model_id)
            r.switch_api = pid
            r.switch_api_model = model_id
            display = defn.get_model_info(model_id)
            display_name = display.display_name if display else model_id
            console.print(f"  [green]\u2713[/green] API: [bold]{defn.name}[/bold] / [yellow]{display_name}[/yellow]")
            return r

        if choice == 1:
            try:
                console.print()
                key = console.input(f"  [bold]{_('api.field_api_key')}:[/bold] ").strip()
                if key:
                    set_api_key(pid, key)
                    reload_providers()
                    if pid == active_api:
                        from apis.agent_adapter import get_api_session, create_api_session
                        existing = get_api_session()
                        if existing:
                            create_api_session(pid, existing.model_id)
                    console.print(f"  [green]\u2713[/green] {_('api.key_set')}")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if choice == 2:
            _api_provider_edit(pid)
            reload_providers()
            continue

        if choice == 3:
            _api_models_menu(pid)
            reload_providers()
            continue

        if choice == 4:
            _api_sync_models(pid)
            reload_providers()
            continue

        if choice == 5:
            confirm = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            c = select_menu(confirm, title=_("api.delete_provider_q", name=pid))
            if c == 0:
                if is_active:
                    config.set_active_api("")
                    config.set_active_api_model("")
                    r.switch_api = ""
                remove_api_config(pid)
                reload_providers()
                console.print(f"  [green]\u2713[/green] {_('api.provider_removed', name=pid)}")
                if r.switch_api is not None:
                    return r
            return None


def _api_add_menu():
    """Menu for adding a new API provider."""
    from apis.config import add_api_config

    no_key = _("api.no_key_needed")
    items = [
        {"label": "OpenAI", "hint": "api.openai.com"},
        {"label": "Anthropic", "hint": "api.anthropic.com"},
        {"label": "Google Gemini", "hint": "generativelanguage.googleapis.com"},
        {"label": "OpenRouter", "hint": "openrouter.ai"},
        {"label": "DeepSeek", "hint": "api.deepseek.com"},
        {"label": "Groq", "hint": "api.groq.com"},
        {"label": "Together AI", "hint": "api.together.xyz"},
        {"label": "Fireworks AI", "hint": "api.fireworks.ai"},
        {"label": "Mistral AI", "hint": "api.mistral.ai"},
        {"label": "Ollama 🏠", "hint": f"localhost:11434 · {no_key}"},
        {"label": "LM Studio 🏠", "hint": f"localhost:1234 · {no_key}"},
        {"label": "llama.cpp server 🏠", "hint": f"localhost:8080 · {no_key}"},
        {"label": "vLLM 🏠", "hint": f"localhost:8000 · {no_key}"},
        {"label": _("api.custom"), "hint": _("api.any_url")},
    ]

    cloud_ids = ["openai", "anthropic", "google", "openrouter", "deepseek", "groq", "together", "fireworks", "mistral"]
    local_ids = ["ollama", "lmstudio", "llamacpp", "vllm"]

    choice = select_menu(items, title=_("api.add_title"))
    if choice is None:
        return

    if choice < len(cloud_ids):
        pid = cloud_ids[choice]
        name, base_url, ptype, api_format = _KNOWN_PROVIDERS[pid]
        add_api_config(
            provider_id=pid, name=name, base_url=base_url,
            provider_type=ptype, api_format=api_format,
        )
        console.print(f"  [green]\u2713[/green] {_('api.added', name=name)}")
        console.print(f"  [dim]{_('api.next_set_key')}[/dim]")
        return

    local_start = len(cloud_ids)
    local_end = local_start + len(local_ids)
    if local_start <= choice < local_end:
        pid = local_ids[choice - local_start]
        name, base_url = _LOCAL_PROVIDERS[pid]
        add_api_config(
            provider_id=pid, name=name, base_url=base_url,
            provider_type="openai_compatible", api_format="openai",
        )
        console.print(f"  [green]\u2713[/green] {_('api.added', name=name)} [dim]({base_url})[/dim]")
        console.print(f"  [dim]{_('api.start_server_hint')}[/dim]")
        return

    try:
        console.print()
        pid = console.input(f"  [bold]{_('api.field_provider_id')}:[/bold] ").strip()
        if not pid:
            return
        name = console.input(f"  [bold]{_('api.field_name')}:[/bold] ").strip() or pid
        base_url = console.input(f"  [bold]{_('api.field_base_url')}:[/bold] ").strip()
        if not base_url:
            console.print(f"  [red]{_('api.url_required')}[/red]")
            return
        add_api_config(
            provider_id=pid, name=name, base_url=base_url,
            provider_type="openai_compatible", api_format="openai",
        )
        console.print(f"  [green]\u2713[/green] {_('api.added', name=name)}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _api_sync_models(provider_id: str):
    """Auto-discovery of models via {base_url}/models."""
    from apis.model_discovery import sync_models

    console.print()
    console.print(f"  [dim]{_('api.fetching', provider=provider_id)}[/dim]")
    try:
        result = sync_models(provider_id, replace=False)
    except ValueError as e:
        console.print(f"  [red]✗[/red] {e}")
        return
    except (OSError, RuntimeError) as e:
        console.print(f"  [red]✗ {_('api.network_error')}:[/red] {e}")
        return
    except Exception as e:
        logger.debug("sync_models failed: {}", e)
        console.print(f"  [red]✗ {type(e).__name__}:[/red] {e}")
        return

    added = result.get("added", [])
    kept = result.get("kept", [])
    total = result.get("total", 0)
    console.print(f"  [green]✓[/green] {_('api.fetched', total=total)}")
    console.print(f"  [dim]{_('api.fetched_detail', kept=len(kept), added=len(added))}[/dim]")
    if added:
        preview = ", ".join(added[:8])
        more = f" +{len(added) - 8}" if len(added) > 8 else ""
        console.print(f"  [dim]{preview}{more}[/dim]")


def _api_models_menu(provider_id: str):
    """Меню управления моделями провайдера."""
    from apis.registry import get_definition, reload_providers
    from apis.config import remove_model_from_provider

    while True:
        reload_providers()
        defn = get_definition(provider_id)
        if not defn:
            return

        items = []
        for m in defn.models:
            price = f"${m.input_price:.2f}/${m.output_price:.2f}"
            ctx = f"{m.context_window // 1000}K" if m.context_window < 1_000_000 else f"{m.context_window // 1_000_000}M"
            hint = f"{m.id} · {price} · {ctx}"
            items.append({"label": m.display_name, "hint": hint})
        items.append({"label": _("api.add_model"), "hint": _("api.add_model_hint")})

        choice = select_menu(items, title=_("api.models_title", name=defn.name))
        if choice is None:
            return

        if choice == len(defn.models):
            _api_model_add(provider_id)
            continue

        model = defn.models[choice]
        actions = [
            {"label": _("api.edit"), "hint": _("api.edit_hint")},
            {"label": _("api.delete"), "hint": _("api.delete_remove")},
            {"label": _("common.back")},
        ]
        a = select_menu(actions, title=f"{model.display_name} ({model.id})")
        if a == 0:
            _api_model_edit(provider_id, model)
        elif a == 1:
            confirm = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            c = select_menu(confirm, title=_("api.delete_model_q", name=model.display_name))
            if c == 0:
                remove_model_from_provider(provider_id, model.id)
                console.print(f"  [green]\u2713[/green] {_('api.model_removed')}")
        continue
