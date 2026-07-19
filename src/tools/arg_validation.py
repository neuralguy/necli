"""Валидация и нормализация аргументов tool-call до вызова handler'а.

Корень «модель путается с инструментами» — отсутствие слоя, который сверяет
присланные args со схемой ДО выполнения. Тогда `new_name` вместо `new_path`
или забытое required-поле вылезают внутри handler'а невнятным симптомом
(«File path not specified»), и модель гадает.

Этот модуль делает три вещи централизованно (в одной точке — execute_call):

1. Алиасы: распространённые синонимы (source→path, new_name→new_path, cmd→command…)
   приводятся к каноническому имени ПО РЕАЛЬНОЙ схеме инструмента. Тихо, с логом —
   максимизирует успех с первой попытки, поэтому описаниям больше не нужны
   костыли вида «NOT 'source'».
2. Коэрция типов: line="42" → 42, background="true" → True. Прокси/модель часто
   шлют числа и булевы строками.
3. Точная диагностика: если required-поле реально отсутствует или enum нарушен —
   возвращаем модели «параметр X обязателен, ты прислал Y», а не симптом.

Намеренно НЕ ошибаемся на лишних/неизвестных параметрах (их игнорируем) и не
валим коэрцию, которую не смогли выполнить (отдаём как есть — пусть решает
handler). Цель — чинить очевидное и точно объяснять неочевидное, а не быть
строгим jsonschema-валидатором.
"""

from __future__ import annotations

from logger import logger

# Синонимы → упорядоченные кандидаты канонических имён. Резолвятся против
# реальных properties инструмента: берётся первый кандидат, который есть в схеме
# и ещё не задан.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    # путь-источник
    "source": ("path",),
    "src": ("path",),
    "file": ("path",),
    "files": ("path",),
    "filepath": ("path",),
    "file_path": ("path",),
    "filename": ("path",),
    "fname": ("path",),
    "directory": ("path",),
    "dir": ("path",),
    "folder": ("path",),
    # путь-назначение
    "destination": ("new_path", "dest"),
    "dest": ("new_path",),
    "dst": ("new_path", "dest"),
    "new_name": ("new_path", "dest"),
    "newname": ("new_path", "dest"),
    "newpath": ("new_path", "dest"),
    "new_file": ("new_path", "dest"),
    "target": ("new_path", "dest"),
    "to": ("new_path", "dest"),
    # shell
    "cmd": ("command",),
    "bash": ("command",),
    "script": ("command",),
    # content
    "text": ("content",),
    "data": ("content",),
    "body": ("content",),
    # patch find/replace
    "old": ("find",),
    "old_str": ("find",),
    "old_string": ("find",),
    "search": ("find",),
    "new": ("replace",),
    "new_str": ("replace",),
    "new_string": ("replace",),
    # поиск
    "query": ("pattern",),
    "regex": ("pattern",),
    "glob": ("pattern",),
}

# Не трогаем эти ключи как «синонимы» (служебные/внутренние парсера).
_RESERVED = frozenset({"raw", "value", "patches", "diff"})


def _schema_index() -> dict[str, dict]:
    """Лениво строит {tool_name: parameters_schema} из TOOL_SCHEMAS (с кэшем)."""
    cached = getattr(_schema_index, "_cache", None)
    if cached is not None:
        return cached
    index: dict[str, dict] = {}
    try:
        from apis.tool_schemas import TOOL_SCHEMAS
    except Exception:  # pragma: no cover - схемы недоступны → валидация выключена
        logger.debug("arg_validation: TOOL_SCHEMAS unavailable, validation disabled", exc_info=True)
        _schema_index._cache = index  # type: ignore[attr-defined]
        return index
    for s in TOOL_SCHEMAS:
        fn = s.get("function", {})
        name = fn.get("name")
        params = fn.get("parameters")
        if name and isinstance(params, dict):
            index[name] = params
    _schema_index._cache = index  # type: ignore[attr-defined]
    return index


def _coerce(value, prop_schema: dict):
    """Коэрция значения к типу из схемы. При неуспехе возвращает как есть."""
    ptype = prop_schema.get("type")
    if ptype == "integer":
        if isinstance(value, bool):  # bool — подтип int, но это явно не число
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            s = value.strip()
            try:
                return int(s)
            except ValueError:
                try:
                    f = float(s)
                    if f.is_integer():
                        return int(f)
                except ValueError:
                    pass
        return value
    if ptype == "number":
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return value
        return value
    if ptype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lv = value.strip().lower()
            if lv in ("true", "1", "yes", "on"):
                return True
            if lv in ("false", "0", "no", "off"):
                return False
        if isinstance(value, int):
            return bool(value)
        return value
    return value


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _describe_params(params: dict) -> str:
    """Человекочитаемая сигнатура для диагностики."""
    props = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])
    parts = []
    for name, schema in props.items():
        t = schema.get("type", "any")
        flag = ", required" if name in required else ""
        parts.append(f"{name} ({t}{flag})")
    return ", ".join(parts)


def validate_and_normalize(
    tool_name: str, args: dict, command: str | None = None
) -> tuple[dict, str | None]:
    """Нормализует args по схеме инструмента.

    Возвращает (новые_args, error). error != None → вызов невалиден, в нём точная
    диагностика для модели. error == None → args исправлены (алиасы/типы) и готовы
    к передаче в handler.

    command — значение ToolCall.command: некоторые инструменты (shell) получают
    свою команду через это поле, а не через args. Учитываем его, чтобы не
    ругаться на «отсутствует required command», когда он на самом деле есть.

    Если у инструмента нет схемы (MCP/think/plan/неизвестный) — args без изменений.
    """
    params = _schema_index().get(tool_name)
    if params is None:
        return args, None

    props: dict = params.get("properties", {}) or {}
    required: list = list(params.get("required", []) or [])

    out = dict(args)

    # Команда из ToolCall.command (shell) удовлетворяет required-поле 'command'.
    if command and "command" in props and _is_empty(out.get("command")):
        out["command"] = command

    # 1) Алиасы: только для ключей, которых нет в схеме (валидные не трогаем).
    aliased: list[str] = []
    for key in list(out.keys()):
        if key in props or key in _RESERVED:
            continue
        for cand in _SYNONYMS.get(key, ()):  # упорядоченные кандидаты
            if cand in props and _is_empty(out.get(cand)):
                # переносим значение под каноническое имя, удаляя чужое
                out[cand] = out.pop(key)
                aliased.append(f"{key}→{cand}")
                break
    if aliased:
        logger.debug("arg_validation: {} aliased {}", tool_name, ", ".join(aliased))

    # 2) Коэрция типов по схеме.
    for key, val in list(out.items()):
        prop_schema = props.get(key)
        if isinstance(prop_schema, dict):
            out[key] = _coerce(val, prop_schema)

    # 3) enum-проверка.
    for key, val in out.items():
        prop_schema = props.get(key)
        if isinstance(prop_schema, dict) and "enum" in prop_schema and val is not None:
            allowed = prop_schema["enum"]
            if val not in allowed:
                return out, (
                    f"Invalid value for '{key}' in {tool_name}: {val!r}. "
                    f"Allowed values: {', '.join(map(str, allowed))}."
                )

    # 4) required-проверка (после алиасов/коэрции).
    missing = [r for r in required if _is_empty(out.get(r))]
    if missing:
        provided = ", ".join(sorted(out.keys())) or "(none)"
        # Подсказываем возможную путаницу: лишние ключи, не входящие в схему.
        extra = [k for k in out if k not in props and k not in _RESERVED]
        hint = ""
        if extra:
            hint = (
                f" You sent unexpected parameter(s): {', '.join(extra)} — "
                f"did you mean {' / '.join(missing)}?"
            )
        return out, (
            f"Invalid arguments for {tool_name}: missing required "
            f"parameter(s): {', '.join(missing)}. Provided: {provided}.{hint} "
            f"Expected parameters: {_describe_params(params)}."
        )

    return out, None
