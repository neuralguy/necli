from __future__ import annotations

import shutil
import sys
from io import StringIO

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
from config.i18n import (
    SUPPORTED_LANGS,
    LANG_DISPLAY,
    set_lang,
    t as _,
)
from config.themes import (
    BUILTIN_THEMES,
    list_themes,
    set_theme,
    get_active_theme_name,
    t as tc,
)
from ui.menu import select_menu, _panel_menu_direct
from agent.theme_preview import render_theme_preview

console = Console()


_PROVIDER_PRESETS = [
    ("openai", "OpenAI", "api.openai.com", "https://api.openai.com/v1", "openai_compatible", "openai"),
    ("anthropic", "Anthropic", "api.anthropic.com", "https://api.anthropic.com", "anthropic", "anthropic"),
    ("google", "Google Gemini", "generativelanguage.googleapis.com",
     "https://generativelanguage.googleapis.com", "google", "google"),
    ("openrouter", "OpenRouter", "openrouter.ai", "https://openrouter.ai/api/v1",
     "openai_compatible", "openai"),
    ("groq", "Groq", "api.groq.com", "https://api.groq.com/openai/v1",
     "openai_compatible", "openai"),
    ("xai", "xAI Grok", "api.x.ai", "https://api.x.ai/v1",
     "openai_compatible", "openai"),
    ("ollama", "Ollama 🏠", "localhost:11434", "http://localhost:11434/v1",
     "openai_compatible", "openai"),
]


def needs_onboarding() -> bool:
    return not bool(config.get("onboarded", False))


def mark_onboarded() -> None:
    config.set_value("onboarded", True)


def _clear_screen() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _hero_panel(step: int, total: int, title_key: str) -> Panel:
    accent = tc("accent")
    success = tc("success")
    muted = tc("muted")

    line1 = Text()
    line1.append("  ✦  ", style=f"bold {accent}")
    line1.append("necli-api", style=f"bold {success}")
    line1.append(f"   {_('welcome.tagline')}", style="dim")

    line2 = Text()
    line2.append("  ", style="")
    for i in range(1, total + 1):
        if i < step:
            line2.append("●", style=success)
        elif i == step:
            line2.append("●", style=f"bold {accent}")
        else:
            line2.append("○", style=muted)
        if i < total:
            line2.append(" ─ ", style=muted)
    line2.append(f"   {_('onboarding.step', n=step, total=total)}: {_(title_key)}", style="dim")

    body = Text("\n").join([line1, line2])
    return Panel(Align.left(body), border_style=accent, padding=(1, 2))


def _show_hero(step: int, total: int, title_key: str) -> None:
    _clear_screen()
    console.print(_hero_panel(step, total, title_key))
    console.print()


def _step_language(start: int = 0) -> tuple[bool, int]:
    _show_hero(1, 3, "onboarding.title_lang")

    current = config.get("language", "en")
    items = [
        {"label": LANG_DISPLAY.get(code, code), "hint": code, "active": code == current}
        for code in SUPPORTED_LANGS
    ]
    choice = select_menu(items, current=start, title=_("lang.subtitle"), allow_forward=True)
    if choice is None:
        return False, start
    if choice >= 0:
        set_lang(SUPPORTED_LANGS[choice])
    return False, max(choice, 0)


def _theme_list_panel(names: list[str], selected: int, current: str, width: int) -> str:
    bg_select = tc("bg_select")
    swatch_roles = ("accent", "success", "warning", "error", "info", "magenta", "purple")

    table = Table(
        show_header=False, border_style="dim", padding=(0, 1),
        show_edge=False, show_lines=False, expand=True,
    )
    table.add_column("Name", no_wrap=True, ratio=1)
    table.add_column("Palette", no_wrap=True, ratio=2)

    for i, name in enumerate(names):
        colors = BUILTIN_THEMES[name]
        is_sel = i == selected
        is_cur = name == current
        marker = "❯ " if is_sel else "  "
        if is_cur and is_sel:
            name_style = "bold green"
        elif is_cur:
            name_style = "green"
        elif is_sel:
            name_style = "bold white"
        else:
            name_style = ""
        name_cell = Text(marker + name + (" ●" if is_cur else ""), style=name_style)
        sw = Text()
        for r in swatch_roles:
            sw.append("██", style=colors[r])
            sw.append(" ", style="default")
        from rich.style import Style as RStyle
        row_bg = RStyle(bgcolor=bg_select) if is_sel else RStyle.null()
        table.add_row(name_cell, sw, style=row_bg)

    panel = Panel(
        table, title=_("themes.title"), title_align="left",
        subtitle=f"{selected + 1}/{len(names)}", subtitle_align="right",
        border_style="dim", padding=(0, 1), width=width,
    )
    buf = StringIO()
    Console(file=buf, highlight=False, force_terminal=True,
            width=width, color_system="truecolor").print(panel)
    return buf.getvalue()


def _step_theme(start: int = 0) -> tuple[bool, int]:
    _show_hero(2, 3, "onboarding.title_theme")

    names = list_themes()
    current = get_active_theme_name()

    # Раскладка как в /themes: панель списка сверху, превью под ней (вертикально).
    term_w = shutil.get_terminal_size((100, 24)).columns
    preview_w = min(76, term_w - 6)
    list_w = min(term_w, preview_w + 4)

    def render_fn(sel: int) -> str:
        list_panel = _theme_list_panel(names, sel, current, list_w)
        preview = render_theme_preview(BUILTIN_THEMES[names[sel]], width=preview_w)
        return list_panel + preview

    choice = _panel_menu_direct(
        render_fn, sys.stdout,
        _("themes.hint_apply") if _("themes.hint_apply") != "themes.hint_apply" else "↑↓ select · enter apply · esc skip",
        len(names), start,
        allow_back=True,
        allow_forward=True,
    )
    if choice is None:
        return False, start
    if choice >= 0:
        if names[choice] != current:
            set_theme(names[choice])
        return False, choice
    cursor = -(choice + 2)
    return True, cursor


def _step_provider(start: int = 0) -> tuple[bool, int]:
    from apis.config import add_api_config, list_api_configs
    from apis.registry import get_definition, reload_providers

    _show_hero(3, 3, "onboarding.title_provider")

    items = [
        {"label": name, "hint": host}
        for _pid, name, host, *_ in _PROVIDER_PRESETS
    ]
    items.append({"label": _("onboarding.skip_provider"), "hint": _("onboarding.skip_hint")})

    choice = select_menu(items, current=start, title=_("onboarding.pick_provider"), allow_back=True, allow_forward=True)
    if choice is None or choice == len(_PROVIDER_PRESETS):
        _ensure_default_provider()
        return False, start if choice is None else choice
    if choice <= -2:
        return True, -(choice + 2)

    pid, name, _host, base_url, ptype, api_format = _PROVIDER_PRESETS[choice]
    add_api_config(
        provider_id=pid, name=name, base_url=base_url,
        provider_type=ptype, api_format=api_format,
    )
    reload_providers()
    console.print(f"  [green]✓[/green] {_('api.added', name=name)}")

    if pid not in ("ollama",):
        _ask_api_key(pid, name)

    defn = get_definition(pid)
    if defn:
        model_id = defn.default_model or (defn.models[0].id if defn.models else "")
        if model_id:
            config.set_active_api(pid)
            config.set_active_api_model(model_id)

    if not list_api_configs():
        _ensure_default_provider()
    return False, choice


def _ask_api_key(pid: str, name: str) -> None:
    from apis.config import set_api_key as _set_key
    console.print()
    console.print(f"  [dim]{_('onboarding.key_prompt_hint')}[/dim]")
    try:
        key = console.input(
            f"  [bold]{_('api.field_api_key')}[/bold] [dim]({_('onboarding.optional')}):[/dim] "
        ).strip()
    except (KeyboardInterrupt, EOFError):
        key = ""
        console.print()
    if key:
        _set_key(pid, key)
        console.print(f"  [green]✓[/green] {_('api.key_set')}")
    else:
        console.print(f"  [yellow]⚠[/yellow] {_('onboarding.key_skipped', name=name)}")


def _ensure_default_provider() -> None:
    from apis.config import list_api_configs, add_api_config
    from apis.registry import get_definition, reload_providers

    if list_api_configs():
        if not config.get_active_api():
            cfgs = list_api_configs()
            first = cfgs[0]
            pid = first.get("id") if isinstance(first, dict) else None
            if pid:
                defn = get_definition(pid)
                if defn:
                    model_id = defn.default_model or (defn.models[0].id if defn.models else "")
                    if model_id:
                        config.set_active_api(pid)
                        config.set_active_api_model(model_id)
        return

    pid, name, _host, base_url, ptype, api_format = _PROVIDER_PRESETS[0]
    add_api_config(
        provider_id=pid, name=name, base_url=base_url,
        provider_type=ptype, api_format=api_format,
    )
    reload_providers()
    defn = get_definition(pid)
    if defn:
        model_id = defn.default_model or (defn.models[0].id if defn.models else "")
        if model_id:
            config.set_active_api(pid)
            config.set_active_api_model(model_id)


def run_onboarding() -> None:
    steps = [_step_language, _step_theme, _step_provider]
    cursors = [0] * len(steps)
    i = 0
    try:
        while i < len(steps):
            want_back, cursor = steps[i](cursors[i])
            cursors[i] = cursor
            if want_back and i > 0:
                i -= 1
            else:
                i += 1
    except (KeyboardInterrupt, EOFError):
        console.print()
        _ensure_default_provider()
    finally:
        mark_onboarded()

    _clear_screen()