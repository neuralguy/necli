from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from tools.models import ToolCall, ToolResult

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
        logger.warning("web_fetch fetch failed | url={!r} error={}", url, e)
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
            logger.warning("web_fetch raw fetch failed | url={!r} error={}", url, e)
            return f"[raw fetch error: {e}]"

    if downloaded:
        _cache_put(cache_key, downloaded)
    return downloaded


def execute_web_fetch(call: ToolCall) -> ToolResult:
    args = call.args or {}

    urls = args.get("urls") or []
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split(",") if u.strip()]
    urls = [u for u in urls if u]

    if not urls:
        return ToolResult(
            name="web_fetch",
            status="error",
            output='No urls provided. Usage: {"urls": ["https://..."], "raw": false}',
            exit_code=1,
            command="web_fetch",
        )

    raw = bool(args.get("raw", False))
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
        name="web_fetch",
        status="ok",
        output="\n".join(lines).strip(),
        exit_code=0,
        command=f"web_fetch{' raw' if raw else ''} [{len(urls)} url(s)]",
    )
