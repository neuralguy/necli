import shutil
import sys
from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

import config
from config.i18n import t as _
from config.themes import t
from logger import logger
from ui.menu import _panel_menu_direct, select_menu

console = Console()


_KNOWN_PROVIDERS = {
    "openai": ("OpenAI", "https://api.openai.com/v1", "openai_compatible", "openai"),
    "anthropic": ("Anthropic", "https://api.anthropic.com", "anthropic", "anthropic"),
    "google": ("Google Gemini", "https://generativelanguage.googleapis.com", "google", "google"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai_compatible", "openai"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "openai_compatible", "openai"),
    "xai": ("xAI Grok", "https://api.x.ai/v1", "openai_compatible", "openai"),
}

_LOCAL_PROVIDERS = {
    "ollama":    ("Ollama (local)",            "http://localhost:11434/v1"),
    "lmstudio":  ("LM Studio (local)",         "http://localhost:1234/v1"),
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
        url_cell = Text(_shorten_url(p.get("base_url") or ""), style="bold white" if is_sel else "dim")
        models_cell = Text(str(models_count) if models_count else "—",
                           style="bold white" if is_sel else ("cyan" if models_count else "dim"))
        if is_active:
            status_text = Text("● " + _("common.active"), style="bold white" if is_sel else "green")
        elif has_key and models_count:
            status_text = Text(_("common.ready"), style="bold white" if is_sel else "dim")
        elif not has_key:
            status_text = Text(_("api.status_no_key"), style="bold white" if is_sel else "dim red")
        else:
            status_text = Text(_("api.status_no_models"), style="bold white" if is_sel else "dim yellow")

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

        def render_fn(
            sel: int,
            providers=providers,
            active_api=active_api,
            width=width,
        ) -> str:
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
    from apis.config import add_api_config
    from apis.registry import get_definition, reload_providers

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
            default_headers=dict(getattr(defn, "default_headers", None) or {}),
            requires_auth=getattr(defn, "requires_auth", True),
            auth_header=getattr(defn, "auth_header", "Authorization"),
            auth_prefix=getattr(defn, "auth_prefix", "Bearer"),
            max_retries=getattr(defn, "max_retries", 3),
            timeout=getattr(defn, "timeout", 120),
            proxy=getattr(defn, "proxy", ""),
            extra=dict(getattr(defn, "extra", None) or {}),
        )
        reload_providers()
        console.print(f"  [green]\u2713[/green] {_('api.provider_updated')}")
    except (KeyboardInterrupt, EOFError):
        console.print()


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 10:
        return "•" * len(api_key)
    return f"{api_key[:6]}…{api_key[-4:]}"


def _refresh_active_api_session(pid: str, active_api: str) -> None:
    if pid != active_api:
        return
    from apis.agent_adapter import create_api_session, get_api_session

    existing = get_api_session()
    if existing:
        create_api_session(pid, existing.model_id)


def _prompt_cache_enabled(defn) -> bool:
    extra = getattr(defn, "extra", None) or {}
    mode = str(extra.get("prompt_cache", extra.get("prompt_caching", "auto"))).lower()
    if mode in {"off", "false", "none", "disabled"}:
        return False
    if mode in {"anthropic", "anthropic_cache_control", "cache_control", "on", "true"}:
        return True
    model_ids = [getattr(m, "id", "") for m in getattr(defn, "models", [])]
    return any("claude" in mid.lower() or "anthropic/" in mid.lower() for mid in model_ids)


def _api_keys_menu(provider_id: str, active_api: str) -> None:
    from apis.config import (
        add_api_credential,
        get_api_credentials,
        remove_api_credential,
        set_api_credential_name,
        set_main_api_credential,
        update_api_credential_proxy,
    )
    from apis.registry import reload_providers

    while True:
        credentials = get_api_credentials(provider_id)
        items = [
            {
                "label": f"{'★ ' if item.get('main') else ''}{item['name'] + ' — ' if item.get('name') else ''}{_mask_api_key(item['key'])}",
                "hint": item["proxy"] or "без proxy",
            }
            for item in credentials
        ]
        items.append({"label": "Добавить ключ", "hint": "ключ и optional proxy"})
        items.append({"label": _("common.back")})

        choice = select_menu(items, title=f"Управление ключами: {provider_id}")
        if choice is None or choice == len(credentials) + 1:
            return

        if choice == len(credentials):
            try:
                console.print()
                api_key = console.input("  [bold]API key:[/bold] ").strip()
                if not api_key:
                    continue
                name = console.input("  [bold]Имя[/bold] [dim](optional):[/dim] ").strip()
                proxy = console.input("  [bold]Proxy[/bold] [dim](optional):[/dim] ").strip()
                add_api_credential(provider_id, api_key, proxy, name)
                reload_providers()
                _refresh_active_api_session(provider_id, active_api)
                console.print("  [green]✓[/green] Ключ добавлен")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        current = credentials[choice]
        actions = [
            {"label": "Переименовать", "hint": current["name"] or "без имени"},
            {"label": "Изменить proxy", "hint": current["proxy"] or "сейчас без proxy"},
            {"label": "Сделать главным", "hint": "запросы будут начинаться с него"},
            {"label": "Показать ключ полностью", "hint": "вывести ключ без маскировки"},
            {"label": "Удалить ключ", "hint": "убрать только этот ключ"},
            {"label": _("common.back")},
        ]
        action = select_menu(actions, title=f"Ключ: {_mask_api_key(current['key'])}")
        if action is None or action == 5:
            continue

        if action == 0:
            try:
                console.print()
                console.print("  [dim]Enter — оставить как есть, '-' — убрать имя[/dim]")
                name = console.input(
                    f"  [bold]Имя[/bold] [dim]({current['name'] or 'без имени'}):[/dim] "
                ).strip()
                if not name:
                    continue
                set_api_credential_name(provider_id, choice, "" if name == "-" else name)
                reload_providers()
                console.print("  [green]✓[/green] Имя обновлено")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if action == 1:
            try:
                console.print()
                console.print("  [dim]Enter — оставить как есть, '-' — убрать proxy[/dim]")
                proxy = console.input(
                    f"  [bold]Proxy[/bold] [dim]({current['proxy'] or 'без proxy'}):[/dim] "
                ).strip()
                if not proxy:
                    continue
                update_api_credential_proxy(provider_id, choice, "" if proxy == "-" else proxy)
                reload_providers()
                _refresh_active_api_session(provider_id, active_api)
                console.print("  [green]✓[/green] Proxy обновлён")
            except (KeyboardInterrupt, EOFError):
                console.print()
            continue

        if action == 2:
            set_main_api_credential(provider_id, choice)
            reload_providers()
            _refresh_active_api_session(provider_id, active_api)
            console.print("  [green]✓[/green] Главный ключ обновлён")
            continue

        if action == 3:
            console.print()
            console.print(f"  [bold]API key:[/bold] {current['key']}")
            console.print()
            console.input("  [dim]Нажмите Enter для продолжения...[/dim]")
            # Стереть 4 строки (пустая + ключ + пустая + prompt) и перерисовать меню
            sys.stdout.write("\033[4A\033[J")
            sys.stdout.flush()
            continue

        if action == 4:
            confirm = [{"label": _("common.yes_delete")}, {"label": _("common.cancel")}]
            confirmed = select_menu(confirm, title=f"Удалить ключ {_mask_api_key(current['key'])}?")
            if confirmed == 0:
                remove_api_credential(provider_id, choice)
                reload_providers()
                _refresh_active_api_session(provider_id, active_api)
                console.print("  [green]✓[/green] Ключ удалён")


def _api_provider_detail(provider: dict, active_api: str, active_model: str):
    """Меню детали провайдера. Возвращает SlashResult или None."""
    from apis.config import get_api_credentials, remove_api_config
    from apis.registry import get_definition, reload_providers
    from commands.slash import SlashResult

    r = SlashResult()
    pid = provider["id"]

    while True:
        defn = get_definition(pid)
        if not defn:
            return None

        is_active = pid == active_api
        credentials = get_api_credentials(pid)
        has_key = bool(credentials)
        status = f"[green]{_('common.active')}[/green]" if is_active else f"[dim]{_('common.inactive')}[/dim]"
        key_status = f"[green]{len(credentials)}[/green]" if has_key else f"[red]{_('common.not_set')}[/red]"
        cache_enabled = _prompt_cache_enabled(defn)
        cache_status = _("api.prompt_cache_on") if cache_enabled else _("api.prompt_cache_off")

        sys.stdout.write("\x1b7")
        sys.stdout.flush()

        console.print()
        console.print(f"  [bold yellow]{defn.name}[/bold yellow]  {status}")
        console.print(f"  [dim]ID: {pid} · Type: {defn.type} · URL: {defn.base_url}[/dim]")
        console.print(f"  [dim]API key: {key_status} · {_('api.prompt_cache')}: {cache_status}[/dim]")
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
        actions.append({"label": "Управление ключами", "hint": f"{len(credentials)} key(s)"})
        actions.append({"label": _("api.edit_provider"), "hint": _("api.edit_provider_hint")})
        actions.append({"label": _("api.manage_models")})
        actions.append({
            "label": _("api.prompt_cache"),
            "hint": _("api.prompt_cache_on") if cache_enabled else _("api.prompt_cache_off"),
        })
        actions.append({"label": _("api.refresh_models"), "hint": _("api.refresh_hint")})
        actions.append({"label": _("api.delete"), "hint": _("api.delete_permanent")})
        actions.append({"label": _("common.back")})

        choice = select_menu(actions)

        sys.stdout.write("\x1b8")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

        if choice is None or choice == 7:
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
            _api_keys_menu(pid, active_api)
            reload_providers()
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
            from apis.config import set_provider_prompt_cache

            if set_provider_prompt_cache(pid, not cache_enabled):
                reload_providers()
                _refresh_active_api_session(pid, active_api)
                state = _("api.prompt_cache_on") if not cache_enabled else _("api.prompt_cache_off")
                console.print(f"  [green]\u2713[/green] {_('api.prompt_cache')}: {state}")
            continue

        if choice == 5:
            _api_sync_models(pid)
            reload_providers()
            continue

        if choice == 6:
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
        {"label": "Groq", "hint": "api.groq.com"},
        {"label": "xAI Grok", "hint": "api.x.ai"},
        {"label": "Ollama 🏠", "hint": f"localhost:11434 · {no_key}"},
        {"label": "LM Studio 🏠", "hint": f"localhost:1234 · {no_key}"},
        {"label": _("api.custom"), "hint": _("api.any_url")},
    ]

    cloud_ids = ["openai", "anthropic", "google", "openrouter", "groq", "xai"]
    local_ids = ["ollama", "lmstudio"]

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
    from apis.config import remove_model_from_provider
    from apis.registry import get_definition, reload_providers

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
