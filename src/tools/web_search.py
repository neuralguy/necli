from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from tools.models import ToolCall, ToolResult

_MAX_RESULTS = 5
_MAX_CONTENT_LENGTH = 15000
_MAX_RAW_HTML_LENGTH = 80000
_FETCH_TIMEOUT = 10
_CACHE_TTL = 3600  # 1 час
_CACHE_MAX_ENTRIES = 100
_FETCH_MAX_WORKERS = 8


def _fetch_pages(urls: list[str], raw: bool = False) -> dict[str, str | None]:
    """Параллельно качает страницы. Возвращает url -> content (порядок не важен, ключуемся по url)."""
    if not urls:
        return {}
    fetcher = _fetch_raw_html if raw else _fetch_page
    if len(urls) == 1:
        return {urls[0]: fetcher(urls[0])}
    with ThreadPoolExecutor(max_workers=min(_FETCH_MAX_WORKERS, len(urls))) as ex:
        return dict(zip(urls, ex.map(fetcher, urls), strict=False))

# url -> (timestamp, text). OrderedDict для O(1) eviction старейших.
# Кэш мутируется из воркеров ThreadPoolExecutor, поэтому защищён локом.
_fetch_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(url: str) -> str | None:
    with _cache_lock:
        item = _fetch_cache.get(url)
        if item is None:
            return None
        ts, text = item
        if time.time() - ts > _CACHE_TTL:
            _fetch_cache.pop(url, None)
            return None
        _fetch_cache.move_to_end(url)
        return text


def _cache_put(url: str, text: str) -> None:
    with _cache_lock:
        _fetch_cache[url] = (time.time(), text)
        _fetch_cache.move_to_end(url)
        while len(_fetch_cache) > _CACHE_MAX_ENTRIES:
            _fetch_cache.popitem(last=False)

def _coerce_indices(raw: object) -> set[int]:
    """Приводит fetch_indices к множеству int. Принимает list/tuple/set/одиночное
    значение, int и строки вида '0'. Невалидные значения молча игнорируются."""
    if raw is None:
        return set()
    if isinstance(raw, (str, bytes)):
        items: list[object] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    out: set[int] = set()
    for item in items:
        if isinstance(item, bool):
            continue
        try:
            out.add(int(item))
        except (ValueError, TypeError):
            logger.warning("web_search: ignoring invalid fetch_index {!r}", item)
    return out


def _fetch_page(url: str) -> str | None:
    cached = _cache_get(url)
    if cached is not None:
        logger.debug("web_fetch cache hit | url={!r}", url)
        return cached

    try:
        import trafilatura
    except ImportError:
        return "[trafilatura not installed, skipping page fetch]"

    try:
        try:
            downloaded = trafilatura.fetch_url(url, timeout=_FETCH_TIMEOUT)
        except TypeError:
            downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded, include_links=False, include_tables=True)
        if text:
            _cache_put(url, text)
        return text
    except Exception as e:
        logger.warning("web_search fetch failed | url={!r} error={}", url, e)
        return f"[fetch error: {e}]"


def _fetch_raw_html(url: str) -> str | None:
    cache_key = "raw::" + url
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("web_fetch raw cache hit | url={!r}", url)
        return cached

    try:
        import trafilatura
        downloaded = None
        try:
            downloaded = trafilatura.fetch_url(url, timeout=_FETCH_TIMEOUT)
        except TypeError:
            downloaded = trafilatura.fetch_url(url)
    except ImportError:
        downloaded = None

    if not downloaded:
        try:
            import httpx
            resp = httpx.get(
                url,
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; necli-agent)"},
            )
            resp.raise_for_status()
            downloaded = resp.text
        except Exception as e:
            logger.warning("web_search raw fetch failed | url={!r} error={}", url, e)
            return f"[raw fetch error: {e}]"

    if downloaded:
        _cache_put(cache_key, downloaded)
    return downloaded


def _do_url_fetch(call: ToolCall, urls: list[str], raw: bool = False) -> ToolResult:
    if not urls:
        return ToolResult(
            name="web_search",
            status="error",
            output='Не указаны url(s) для fetch.',
            exit_code=1,
            command="web_fetch",
        )

    limit = _MAX_RAW_HTML_LENGTH if raw else _MAX_CONTENT_LENGTH
    fetched = _fetch_pages(urls, raw=raw)
    lines = []
    for url in urls:
        content = fetched.get(url)
        lines.append(f"=== {url} ===")
        if not content:
            lines.append("[empty or fetch failed]")
        else:
            lines.append(content[:limit])
            if len(content) > limit:
                lines.append(f"... (truncated, {len(content)} chars total)")
        lines.append("")

    return ToolResult(
        name="web_search",
        status="ok",
        output="\n".join(lines).strip(),
        exit_code=0,
        command=f"web_fetch{' raw' if raw else ''} [{len(urls)} url(s)]",
    )


def execute_web_search(call: ToolCall) -> ToolResult:
    args = call.args or {}

    # Режим прямого fetch'а по URL — без поиска
    url = (args.get("url") or "").strip()
    urls = args.get("urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split(",") if u.strip()]
    if url:
        urls = [url, *list(urls)]
    urls = [u for u in urls if u]
    raw = bool(args.get("raw") or args.get("html"))
    if urls:
        logger.info("web_fetch | urls={} raw={}", urls, raw)
        return _do_url_fetch(call, urls, raw=raw)

    query = args.get("query", "").strip() or call.command.strip()

    if not query:
        return ToolResult(
            name="web_search",
            status="error",
            output=(
                'No query/url provided. '
                'Usage: {"query": "..."} для поиска или '
                '{"url": "https://..."} для fetch.'
            ),
            exit_code=1,
            command="web_search",
        )

    try:
        max_results = int(args.get("max_results") or _MAX_RESULTS)
    except (ValueError, TypeError):
        logger.warning(
            "web_search: invalid max_results={!r}, using default {}",
            args.get("max_results"), _MAX_RESULTS,
        )
        max_results = _MAX_RESULTS
    fetch_content = args.get("fetch", False)
    fetch_indices = args.get("fetch_indices", [])

    logger.info("web_search | query={!r} max_results={} fetch={}", query, max_results, fetch_content)

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from ddgs import DDGS
    except ImportError:
        return ToolResult(
            name="web_search",
            status="error",
            output="ddgs not installed. Run: uv add ddgs",
            exit_code=1,
            command="web_search",
        )

    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception as e:
        logger.error("web_search failed | query={!r} error={}", query, e)
        return ToolResult(
            name="web_search",
            status="error",
            output=f"Search failed: {e}",
            exit_code=1,
            command=query,
        )

    if not results:
        return ToolResult(
            name="web_search",
            status="ok",
            output="No results found.",
            exit_code=0,
            command=query,
        )

    # Определяем какие страницы нужно загрузить
    indices_to_fetch: set[int] = set()
    if fetch_content:
        indices_to_fetch = set(range(len(results)))
    elif fetch_indices:
        indices_to_fetch = {idx for idx in _coerce_indices(fetch_indices) if 0 <= idx < len(results)}

    urls_by_index = {
        i: r.get("href", r.get("link", ""))
        for i, r in enumerate(results)
        if i in indices_to_fetch
    }
    fetched = _fetch_pages([u for u in urls_by_index.values() if u])

    lines = []
    for i, r in enumerate(results):
        title = r.get("title", "")
        result_url = r.get("href", r.get("link", ""))
        snippet = r.get("body", "")

        lines.append(f"[{i}] {title}")
        lines.append(f"    {result_url}")
        lines.append(f"    {snippet}")

        if i in indices_to_fetch:
            content = fetched.get(result_url)
            if content:
                lines.append("    --- Page content ---")
                lines.append(f"    {content[:_MAX_CONTENT_LENGTH]}")
                if len(content) > _MAX_CONTENT_LENGTH:
                    lines.append(f"    ... (truncated, {len(content)} chars total)")

        lines.append("")

    return ToolResult(
        name="web_search",
        status="ok",
        output="\n".join(lines).strip(),
        exit_code=0,
        command=query,
    )
