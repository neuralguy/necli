"""Fuzzy find/replace для patch_file: нормализация пробелов и отступов."""


# Ширина таба при нормализации строк (стандарт для Python-кода).
_TAB_WIDTH = 4

def _normalize_line(line: str) -> str:
    """Нормализует строку: табы → пробелы, схлопывает множественные пробелы."""
    return ' '.join(line.expandtabs(_TAB_WIDTH).split())


def _fuzzy_find_replace(text: str, find: str, replace: str) -> tuple[str, bool]:
    """Ищет find в text с нормализацией пробелов/отступов.

    Стратегии (в порядке приоритета):
    1. Нормализация пробелов построчно с сохранением оригинального отступа.
    2. Построчный поиск блока без учёта ведущих отступов.

    Возвращает (новый_текст, найдено).
    """
    find_lines = find.splitlines()
    text_lines = text.splitlines(keepends=True)

    if not find_lines:
        return text, False

    norm_find = [_normalize_line(ln) for ln in find_lines]
    norm_text = [_normalize_line(ln.rstrip('\n')) for ln in text_lines]

    for i in range(len(text_lines) - len(find_lines) + 1):
        if norm_text[i:i + len(find_lines)] == norm_find:
            orig_indent = len(text_lines[i]) - len(text_lines[i].lstrip())
            replace_lines = replace.splitlines()
            if replace_lines:
                find_indent = len(find_lines[0]) - len(find_lines[0].lstrip())
                indent_diff = orig_indent - find_indent
                if indent_diff > 0:
                    pad = ' ' * indent_diff
                    replace_lines = [pad + ln if ln.strip() else ln for ln in replace_lines]
                elif indent_diff < 0:
                    cut = -indent_diff
                    # Срез считаем по ОРИГИНАЛЬНОЙ строке. Если в зоне среза
                    # есть не-пробельные или табы (mixed tabs/spaces) — отказ,
                    # чтобы не отрезать значимые символы.
                    if any(
                        ln.strip() and (ln[:cut].strip() != '' or '\t' in ln[:cut])
                        for ln in replace_lines
                    ):
                        return text, False
                    replace_lines = [ln[cut:] if ln.strip() else ln for ln in replace_lines]
            new_replace = '\n'.join(replace_lines)
            last_line = text_lines[i + len(find_lines) - 1]
            if last_line.endswith('\n') and not new_replace.endswith('\n'):
                new_replace += '\n'
            result_lines = text_lines[:i] + [new_replace] + text_lines[i + len(find_lines):]
            return ''.join(result_lines), True

    strip_find = [ln.strip() for ln in find_lines if ln.strip()]
    if strip_find:
        for i in range(len(text_lines) - len(strip_find) + 1):
            window = [ln.rstrip('\n').strip() for ln in text_lines[i:i + len(strip_find)]]
            if window == strip_find:
                orig_indent = len(text_lines[i]) - len(text_lines[i].lstrip())
                replace_lines = replace.splitlines()
                if replace_lines:
                    # Strip-стратегия (fallback): найдено совпадение без учёта
                    # отступов. Здесь все строки replace выравниваются по отступу
                    # первой найденной строки — вложенность replace теряется. Это
                    # сознательный компромисс последней попытки: точную relative-
                    # вложенность сохраняет основная стратегия выше (indent_diff),
                    # сюда попадаем только когда та не нашла совпадения.
                    pad = ' ' * orig_indent
                    replace_lines = [pad + ln.lstrip() if ln.strip() else ln for ln in replace_lines]
                new_replace = '\n'.join(replace_lines)
                last_line = text_lines[i + len(strip_find) - 1]
                if last_line.endswith('\n') and not new_replace.endswith('\n'):
                    new_replace += '\n'
                result_lines = text_lines[:i] + [new_replace] + text_lines[i + len(strip_find):]
                return ''.join(result_lines), True

    return text, False