"""Общие утилиты работы с текстом для инструментов."""


def truncate_middle(text: str, max_len: int = 50000) -> str:
    """Обрезает текст, сохраняя начало и конец.

    Формат меты единый для всех вызовов (commit-агент, project_check и т.д.),
    чтобы поведение обрезки было предсказуемым и покрывалось одними тестами.
    """
    if max_len < 160 or len(text) <= max_len:
        return text
    half = max_len // 2 - 80
    head = text[:half]
    tail = text[-half:]
    shown = len(head) + len(tail)
    return (
        head
        + f"\n... [{shown} of {len(text)} chars shown, {len(text) - shown} skipped] ...\n"
        + tail
    )
