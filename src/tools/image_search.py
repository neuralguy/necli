"""Поиск изображений в интернете через DuckDuckGo.

Режимы:
  - Поиск + скачивание: {"queries": ["cats", "dogs"]} → ищет картинки,
    валидирует их через Pillow (битые/не-картинки отсеиваются) и сохраняет
    в assets/images. Результаты возвращаются с путями к скачанным файлам.

Лицензию НЕ фильтруем — выбор за пользователем.
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

# query|max|size|type -> (timestamp, results)
_search_cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()


# Кеш результатов поиска (LRU + TTL)
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


# Нормализованная форма результата
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


# Поиск через DuckDuckGo
def _search_ddg(query: str, max_results: int, args: dict) -> list[dict]:
    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from ddgs import DDGS
    except ImportError:
        raise RuntimeError("ddgs not installed. Run: uv add ddgs")  # noqa: B904

    kwargs: dict = {"max_results": max_results}
    if args.get("size"):
        kwargs["size"] = args["size"]
    if args.get("type"):
        kwargs["type_image"] = args["type"]

    try:
        raw = DDGS().images(query, **kwargs)
    except TypeError:
        # Старая/новая сигнатура без части kwargs — повторяем по-минимуму.
        raw = DDGS().images(query, max_results=max_results)

    out = []
    for r in raw or []:
        out.append(  # noqa: PERF401
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


# Скачивание + валидация через Pillow
def _safe_name(idx: int, url: str, content_type: str) -> str:
    base = f"image_{idx:02d}"
    ext = _EXT_BY_CT.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        # пробуем расширение из URL
        tail = url.split("?")[0].rsplit(".", 1)
        ext = "." + tail[1].lower() if len(tail) == 2 and 1 <= len(tail[1]) <= 5 else ".jpg"
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
    except Exception as e:
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
    except Exception as e:
        out["error"] = f"not a valid image: {e}"
        return out

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_name(idx, url, ct)
        path = dest_dir / name
        path.write_bytes(data)
        out["ok"] = True
        out["path"] = path
    except Exception as e:
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


# Вспомогательная: поиск + скачивание для одного запроса
def _search_and_download(
    query: str, max_results: int, args: dict, dest_dir: Path
) -> tuple[list[dict], list[Path], list[dict]]:
    """Ищет картинки по query, скачивает все результаты, возвращает (results, image_paths, downloaded)."""
    size = args.get("size", "")
    type_ = args.get("type", "")
    cache_key = f"{max_results}|{size}|{type_}|{query}"

    results = _cache_get(cache_key)
    if results is None:
        logger.info("image_search | query={!r} max={}", query, max_results)
        try:
            results = _search_ddg(query, max_results, args)
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("image_search failed | query={!r} error={}", query, e)
            raise RuntimeError(f"Search failed: {e}") from e
        results = [r for r in results if r.get("image")]
        _cache_put(cache_key, results)
    else:
        logger.debug("image_search cache hit | query={!r}", query)

    if not results:
        return results, [], []

    # Всегда скачиваем все результаты.
    sel = [(i, r["image"]) for i, r in enumerate(results)]
    downloaded = _download_many(sel, dest_dir)
    image_paths = [d["path"] for d in downloaded if d["ok"] and d["path"]]
    return results, image_paths, downloaded


# Точка входа
def _err(msg: str, command: str = "image_search") -> ToolResult:
    return ToolResult(
        name="image_search", status="error", output=msg, exit_code=1, command=command,
    )


def execute_image_search(call: ToolCall) -> ToolResult:
    args = call.args or {}

    queries = args.get("queries", None)
    if not queries or not isinstance(queries, list):
        return _err(
            'No queries provided. '
            'Usage: {"queries": ["mountain sunset", "beach"], "max_results": 10}'
        )

    queries = [str(q).strip() for q in queries if q and str(q).strip()]
    if not queries:
        return _err("No non-empty queries provided.")

    if len(queries) > 5:
        queries = queries[:5]
        logger.warning("image_search: truncated queries to 5 (got {})", len(args.get("queries", [])))

    try:
        max_results = int(args.get("max_results") or _MAX_RESULTS)
    except (ValueError, TypeError):
        max_results = _MAX_RESULTS
    max_results = max(1, min(max_results, 50))

    raw_dir = clean_path(_DEFAULT_DOWNLOAD_DIR)
    dest_dir = resolve_path(raw_dir, extensions=())

    all_lines: list[str] = []
    all_image_paths: list[Path] = []

    for qidx, query in enumerate(queries):
        try:
            results, image_paths, downloaded = _search_and_download(query, max_results, args, dest_dir)
        except RuntimeError as e:
            all_lines.append(f"[Query {qidx + 1}: {query}]")
            all_lines.append(f"  Error: {e}")
            all_lines.append("")
            continue

        all_image_paths.extend(image_paths)

        ok_n = sum(1 for d in downloaded if d["ok"])
        fail_n = len(downloaded) - ok_n
        summary = f"Found {len(results)} image(s); downloaded {ok_n}"
        if fail_n:
            summary += f", {fail_n} failed/invalid"
        all_lines.append(f"[Query {qidx + 1}: {query}] — {summary}")
        all_lines.append("")

        dl_by_index = {d["index"]: d for d in downloaded}
        for i, r in enumerate(results):
            dims = ""
            if r["width"] and r["height"]:
                dims = f" {r['width']}x{r['height']}"
            prov = f" via {r['provider']}" if r.get("provider") else ""
            all_lines.append(f"  [{i}] {r['title'] or '(no title)'}{dims}")
            all_lines.append(f"      image:  {r['image']}")
            if r["thumbnail"]:
                all_lines.append(f"      thumb:  {r['thumbnail']}")
            if r["page"]:
                all_lines.append(f"      page:   {r['page']}")
            if r["source"] or prov:
                all_lines.append(f"      source: {r['source']}{prov}")

            d = dl_by_index.get(i)
            if d is not None:
                if d["ok"]:
                    fmt = f" [{d['format']}]" if d.get("format") else ""
                    all_lines.append(f"      ↓ saved: {d['path']}{fmt}")
                else:
                    all_lines.append(f"      ↓ FAILED: {d['error']}")
            all_lines.append("")

    if not all_lines:
        return ToolResult(
            name="image_search", status="ok",
            output="No images found.", exit_code=0,
        )

    result = ToolResult(
        name="image_search",
        status="ok",
        output="\n".join(all_lines).strip(),
        exit_code=0,
    )
    if all_image_paths:
        result.image_paths = all_image_paths
        if len(all_image_paths) == 1:
            result.image_path = all_image_paths[0]
    return result
