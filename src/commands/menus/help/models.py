"""Структуры данных справки: блоки, страницы, разделы + шорткаты создания."""

# ruff: noqa: N802  # H/T/C/TIP/WARN/TABLE/KEYS/LIST/SEP/DEMO — намеренные шорткаты

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HBlock:
    """Один блок контента на странице справки."""
    kind: str          # h | text | code | tip | warn | table | keys | list | sep | demo
    content: str = ""
    items: list = field(default_factory=list)   # для table / keys / list
    role: str = ""     # цвет (accent, success, warning, error, info, …)


@dataclass
class HPage:
    """Одна страница: заголовок + левая и правая колонки."""
    title: str
    left: list[HBlock] = field(default_factory=list)
    right: list[HBlock] = field(default_factory=list)
    paired: bool = False  # поблочное выравнивание колонок (примеры под командами)


@dataclass
class HSection:
    """Раздел справки: имя, иконка, описание, список страниц."""
    name: str
    icon: str
    desc: str
    pages: list[HPage] = field(default_factory=list)


# ── шорткаты для создания блоков ──────────────────────────────────────────
def H(t: str, role: str = "accent") -> HBlock:
    return HBlock("h", t, role=role)

def T(t: str) -> HBlock:
    return HBlock("text", t)

def C(t: str) -> HBlock:
    return HBlock("code", t)

def TIP(t: str) -> HBlock:
    return HBlock("tip", t)

def WARN(t: str) -> HBlock:
    return HBlock("warn", t)

def TABLE(rows: list[tuple]) -> HBlock:
    return HBlock("table", items=rows)

def KEYS(rows: list[tuple]) -> HBlock:
    return HBlock("keys", items=rows)

def LIST(items: list[str]) -> HBlock:
    return HBlock("list", items=items)

def SEP() -> HBlock:
    return HBlock("sep")

def DEMO(t: str) -> HBlock:
    return HBlock("demo", t)
