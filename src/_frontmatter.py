"""Общий парсер YAML-подобного frontmatter из markdown-файлов."""

import re

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Разбирает frontmatter вида ---\\nkey: val\\n---\\nbody.

    Возвращает (meta, body), где meta — словарь ключ→значение.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_yaml = m.group(1)
    body = text[m.end():]
    meta: dict[str, str] = {}
    for line in raw_yaml.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    return meta, body