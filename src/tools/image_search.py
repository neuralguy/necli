"""Поиск изображений в интернете.

Источники:
  - DuckDuckGo через ddgs (`DDGS().images()`) — по умолчанию, без ключей.
  - Unsplash / Pexels — опционально, если в settings.api_keys есть ключ
    "unsplash" / "pexels". Иначе тихо пропускаются.

Режимы:
  - Поиск: {"query": "..."} → список картинок (URL, размеры, источник).
  - Скачивание: download=true (+ download_indices / download_dir) — качает
    выбранные картинки, ВАЛИДИРУЕТ их через Pillow (битые/не-картинки
    отсеиваются) и кладёт пути в ToolResult.image_paths.

Лицензию НЕ фильтруем — показываем источник, выбор за пользователем.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from logger import logger

from tools._paths import clean_path, resolve_path
from tools.models import ToolCall, ToolResult

_MAX_RESULTS = 10
_DOWNLOAD_TIMEOUT = 20
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 МБ — отсекаем гигантские файлы
_DOWNLOAD_MAX_WORKERS = 6
_CACHE_TTL = 1800  # 30 минут
_CACHE_MAX_ENTRIES = 64
_DEFAULT_DOWNLOAD_DIR = "assets/images"

_VALID_SOURCES = ("auto", "ddg", "duckduckgo", "unsplash", "pexels")

# Расширения по content-type для именования скачанных файлов.
_EXT_BY_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tiff",
}

# query|source|max -> (timestamp, results)
_search_cache: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()


# --------------------------------------------------------------------------- #
# Кеш результатов поиска (LRU + TTL, как в web_search)                         #
# --------------------------------------------------------------------------- #
def _cache_get(key: str) -> list[dict] | None:
    item = _search_cache.get(key)
    if item is None:
        return None
    ts, results = item
    if time.time() - ts > _CACHE_TTL:
        _search_cache.pop(key, None)
        return None
    _search_cache.move_to_end(key)
    return results


def _cache_put(key: str, results: list[dict]) -> None:
    _search_cache[key] = (time.time(), results)
    _search_cache.move_to_end(key)
    while len(_search_cache) > _CACHE_MAX_ENTRIES:
        _search_cache.popitem(last=False)


# --------------------------------------------------------------------------- #
# Нормализованная форма результата                                            #
# --------------------------------------------------------------------------- #
def _norm(
    *,
    image: str,
    title: str = "",
    thumbnail: str = "",
    page: str = "",
    width=None,
    height=None,
    source: str = "",
    provider: str = "",
) -> dict:
    """Единый формат результата независимо от источника."""

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "image": (image or "").strip(),
        "title": (title or "").strip(),
        "thumbnail": (thumbnail or "").strip(),
        "page": (page or "").strip(),
        "width": _int(width),
        "height": _int(height),
        "source": (source or "").strip(),
        "provider": provider,
    }


# --------------------------------------------------------------------------- #
# Источники поиска                                                            #
# --------------------------------------------------------------------------- #
def _search_ddg(query: str, max_results: int, args: dict) -> list[dict]:
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from ddgs import DDGS
    except ImportError:
        raise RuntimeError("ddgs not installed. Run: uv add ddgs")

    # ddgs поддерживает фильтры size/type_image/color/layout — пробрасываем
    # только те, что заданы (и игнорируем, если версия ddgs их не знает).
    kwargs: dict = {"max_results": max_results}
    if args.get("size"):
        kwargs["size"] = args["size"]
    if args.get("type"):
        kwargs["type_image"] = args["type"]
    if args.get("color"):
        kwargs["color"] = args["color"]

    try:
        raw = DDGS().images(query, **kwargs)
    except TypeError:
        # Старая/новая сигнатура без части kwargs — повторяем по-минимуму.
        raw = DDGS().images(query, max_results=max_results)

    out = []
    for r in raw or []:
        out.append(
            _norm(
                image=r.get("image", ""),
                title=r.get("title", ""),
                thumbnail=r.get("thumbnail", ""),
                page=r.get("url", ""),
                width=r.get("width"),
                height=r.get("height"),
                source=r.get("source", ""),
                provider="ddg",
            )
        )
    return out


def _get_api_key(name: str) -> str | None:
    try:
        from config import settings

        keys = settings.get("api_keys", {}) or {}
        if isinstance(keys, dict):
            v = keys.get(name)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception as e:  # noqa: BLE001 — отсутствие ключа не ошибка
        logger.debug("image_search: api key lookup failed for {}: {}", name, e)
    return None


def _search_unsplash(query: str, max_results: int, args: dict) -> list[dict]:
    key = _get_api_key("unsplash")
    if not key:
        return []
    import httpx

    resp = httpx.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": min(max_results, 30)},
        headers={"Authorization": f"Client-ID {key}"},
        timeout=_DOWNLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("results", [])[:max_results]:
        urls = r.get("urls", {}) or {}
        out.append(
            _norm(
                image=urls.get("regular") or urls.get("full") or urls.get("raw", ""),
                title=r.get("description") or r.get("alt_description") or "",
                thumbnail=urls.get("thumb", ""),
                page=(r.get("links", {}) or {}).get("html", ""),
                width=r.get("width"),
                height=r.get("height"),
                source="unsplash.com",
                provider="unsplash",
            )
        )
    return out


def _search_pexels(query: str, max_results: int, args: dict) -> list[dict]:
    key = _get_api_key("pexels")
    if not key:
        return []
    import httpx

    resp = httpx.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": min(max_results, 80)},
        headers={"Authorization": key},
        timeout=_DOWNLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for r in data.get("photos", [])[:max_results]:
        src = r.get("src", {}) or {}
        out.append(
            _norm(
                image=src.get("large") or src.get("original", ""),
                title=r.get("alt", ""),
                thumbnail=src.get("tiny") or src.get("small", ""),
                page=r.get("url", ""),
                width=r.get("width"),
                height=r.get("height"),
                source="pexels.com",
                provider="pexels",
            )
        )
    return out


def _gather_results(query: str, source: str, max_results: int, args: dict) -> list[dict]:
    """Собирает результаты с учётом выбранного источника.

    auto: сначала ddg; стоки добавляются, только если их ключи заданы.
    """
    source = (source or "auto").lower()
    if source in ("ddg", "duckduckgo"):
        return _search_ddg(query, max_results, args)
    if source == "unsplash":
        return _search_unsplash(query, max_results, args)
    if source == "pexels":
        return _search_pexels(query, max_results, args)

    # auto
    results = _search_ddg(query, max_results, args)
    for fn in (_search_unsplash, _search_pexels):
        try:
            extra = fn(query, max_results, args)
            if extra:
                results.extend(extra)
        except Exception as e:  # noqa: BLE001 — сток упал, ddg уже есть
            logger.warning("image_search: stock source failed: {}", e)
    return results[: max(max_results, 1)]


# --------------------------------------------------------------------------- #
# Скачивание + валидация через Pillow                                         #
# --------------------------------------------------------------------------- #
def _safe_name(idx: int, url: str, content_type: str) -> str:
    base = f"image_{idx:02d}"
    ext = _EXT_BY_CT.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        # пробуем расширение из URL
        tail = url.split("?")[0].rsplit(".", 1)
        if len(tail) == 2 and 1 <= len(tail[1]) <= 5:
            ext = "." + tail[1].lower()
        else:
            ext = ".jpg"
    return base + ext


def _download_one(idx: int, url: str, dest_dir: Path) -> dict:
    """Качает одну картинку, валидирует Pillow. Возвращает dict со статусом."""
    import httpx

    out = {"index": idx, "url": url, "ok": False, "path": None, "error": None,
           "width": None, "height": None, "format": None}
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=_DOWNLOAD_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; necli-agent)"},
        ) as resp:
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            chunks = bytearray()
            for chunk in resp.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > _MAX_DOWNLOAD_BYTES:
                    out["error"] = f"too large (>{_MAX_DOWNLOAD_BYTES // (1024*1024)}MB)"
                    return out
            data = bytes(chunks)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"download failed: {e}"
        return out

    if not data:
        out["error"] = "empty response"
        return out

    # Валидация: реальная ли это картинка.
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            im.verify()  # ловит битые файлы
        # verify() инвалидирует объект — открываем заново для размеров.
        with Image.open(BytesIO(data)) as im2:
            out["width"], out["height"] = im2.size
            out["format"] = im2.format
    except Exception as e:  # noqa: BLE001 — не картинка / битый файл
        out["error"] = f"not a valid image: {e}"
        return out

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_name(idx, url, ct)
        path = dest_dir / name
        path.write_bytes(data)
        out["ok"] = True
        out["path"] = path
    except Exception as e:  # noqa: BLE001
        out["error"] = f"write failed: {e}"
    return out


def _download_many(items: list[tuple[int, str]], dest_dir: Path) -> list[dict]:
    if not items:
        return []
    if len(items) == 1:
        idx, url = items[0]
        return [_download_one(idx, url, dest_dir)]
    with ThreadPoolExecutor(max_workers=min(_DOWNLOAD_MAX_WORKERS, len(items))) as ex:
        return list(ex.map(lambda t: _download_one(t[0], t[1], dest_dir), items))


# --------------------------------------------------------------------------- #
# Точка входа                                                                 #
# --------------------------------------------------------------------------- #
def _err(msg: str, command: str = "image_search") -> ToolResult:
    return ToolResult(
        name="image_search", status="error", output=msg, exit_code=1, command=command,
    )


def execute_image_search(call: ToolCall) -> ToolResult:
    args = call.args or {}
    query = (args.get("query") or "").strip() or (call.command or "").strip()
    if not query:
        return _err('No query provided. Usage: {"query": "mountain sunset"}')

    source = (args.get("source") or "auto").lower()
    if source not in _VALID_SOURCES:
        return _err(
            f"Unknown source {source!r}. Valid: {', '.join(_VALID_SOURCES)}",
            command=query,
        )

    try:
        max_results = int(args.get("max_results") or _MAX_RESULTS)
    except (ValueError, TypeError):
        max_results = _MAX_RESULTS
    max_results = max(1, min(max_results, 50))

    cache_key = f"{source}|{max_results}|{args.get('size','')}|{args.get('type','')}|{args.get('color','')}|{query}"
    results = _cache_get(cache_key)
    if results is None:
        logger.info("image_search | query={!r} source={} max={}", query, source, max_results)
        try:
            results = _gather_results(query, source, max_results, args)
        except RuntimeError as e:  # ddgs не установлен и т.п.
            return _err(str(e), command=query)
        except Exception as e:  # noqa: BLE001
            logger.error("image_search failed | query={!r} error={}", query, e)
            return _err(f"Search failed: {e}", command=query)
        # отсеиваем результаты без прямого URL картинки
        results = [r for r in results if r.get("image")]
        _cache_put(cache_key, results)
    else:
        logger.debug("image_search cache hit | query={!r}", query)

    if not results:
        return ToolResult(
            name="image_search", status="ok",
            output="No images found.", exit_code=0, command=query,
        )

    # --- режим скачивания --------------------------------------------------
    do_download = bool(args.get("download"))
    downloaded: list[dict] = []
    image_paths: list[Path] = []
    if do_download:
        raw_dir = clean_path(args.get("download_dir") or _DEFAULT_DOWNLOAD_DIR)
        dest_dir = resolve_path(raw_dir)

        indices = args.get("download_indices")
        if isinstance(indices, int):
            indices = [indices]
        if indices:
            sel = [(i, results[i]["image"]) for i in indices if 0 <= i < len(results)]
        else:
            sel = [(i, r["image"]) for i, r in enumerate(results)]

        logger.info("image_search download | {} item(s) → {}", len(sel), dest_dir)
        downloaded = _download_many(sel, dest_dir)
        image_paths = [d["path"] for d in downloaded if d["ok"] and d["path"]]

    # --- формирование вывода ----------------------------------------------
    lines = []
    dl_by_index = {d["index"]: d for d in downloaded}
    for i, r in enumerate(results):
        dims = ""
        if r["width"] and r["height"]:
            dims = f" {r['width']}x{r['height']}"
        prov = f" via {r['provider']}" if r.get("provider") else ""
        lines.append(f"[{i}] {r['title'] or '(no title)'}{dims}")
        lines.append(f"    image:  {r['image']}")
        if r["thumbnail"]:
            lines.append(f"    thumb:  {r['thumbnail']}")
        if r["page"]:
            lines.append(f"    page:   {r['page']}")
        if r["source"] or prov:
            lines.append(f"    source: {r['source']}{prov}")

        d = dl_by_index.get(i)
        if d is not None:
            if d["ok"]:
                fmt = f" [{d['format']}]" if d.get("format") else ""
                lines.append(f"    ↓ saved: {d['path']}{fmt}")
            else:
                lines.append(f"    ↓ FAILED: {d['error']}")
        lines.append("")

    if do_download:
        ok_n = sum(1 for d in downloaded if d["ok"])
        fail_n = len(downloaded) - ok_n
        summary = f"Found {len(results)} image(s); downloaded {ok_n}"
        if fail_n:
            summary += f", {fail_n} failed/invalid"
        lines.insert(0, summary + ".\n")

    result = ToolResult(
        name="image_search",
        status="ok",
        output="\n".join(lines).strip(),
        exit_code=0,
        command=query,
    )
    if image_paths:
        result.image_paths = image_paths
        if len(image_paths) == 1:
            result.image_path = image_paths[0]
    return result
