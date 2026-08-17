"""Choose per-block full rendering for compact and full views."""

from __future__ import annotations

from commands.menus._style import card_menu, facts_line
from config.display import get_full_blocks, set_full_blocks
from config.i18n import t as _
from tools.registry import list_tools

_THOUGHT_BLOCKS = ("think", "reasoning")


def _items(blocks: list[str], selected: set[str]) -> list[dict]:
    return [{"label": name, "active": name in selected} for name in blocks] + [
        {"label": _("common.done")}
    ]


async def _choose_mode(*, compact: bool) -> None:
    tools = [name for name in list_tools() if name not in _THOUGHT_BLOCKS]
    blocks = tools + list(_THOUGHT_BLOCKS)
    selected = get_full_blocks(compact=compact)
    if "*" in selected:
        selected = set(blocks)
    checked = {i for i, name in enumerate(blocks) if name in selected}
    while True:
        result = await card_menu(
            _items(blocks, selected),
            title=_("display.compact" if compact else "display.full"),
            facts=[facts_line(_("display.choose"), f"{len(selected)}/{len(blocks)}")],
            multi=True,
            checked=checked,
        )
        if result is None:
            return
        choice, checked = result
        if choice is None or choice == len(blocks):
            set_full_blocks(compact=compact, blocks={blocks[i] for i in checked if i < len(blocks)})
            return


async def display_interactive() -> None:
    while True:
        compact_blocks = get_full_blocks(compact=True)
        full_blocks = get_full_blocks(compact=False)
        compact = "all" if "*" in compact_blocks else str(len(compact_blocks))
        full = "all" if "*" in full_blocks else str(len(full_blocks))
        choice = await card_menu(
            [
                {"label": _("display.compact"), "hint": f"{len(get_full_blocks(compact=True))}"},
                {"label": _("display.full"), "hint": f"{len(get_full_blocks(compact=False))}"},
                {"label": _("common.back")},
            ],
            title=_("display.title"),
            facts=[facts_line(_("display.compact"), str(compact), _("display.full"), str(full))],
        )
        if choice is None or choice == 2:
            return
        await _choose_mode(compact=choice == 0)
