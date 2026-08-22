from __future__ import annotations

from loguru import logger

from tools.models import ToolCall, ToolResult

_MAX_RESULTS = 5
_MAX_RESULTS_LIMIT = 20
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


def _search_with_retry(query: str, max_results: int) -> list:
    """Сетевой поиск с ретраями: один timeout не должен ронять вызов модели."""
    import time
    import warnings

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                from ddgs import DDGS
            return DDGS().text(query, max_results=max_results)
        except Exception as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * (attempt + 1))
    raise last_error


def execute_web_search(call: ToolCall) -> ToolResult:
    args = call.args or {}

    queries = args.get("queries", None)
    if not queries or not isinstance(queries, list):
        return ToolResult(
            name="web_search",
            status="error",
            output=(
                "No queries provided. "
                'Usage: {"queries": ["what is python?", "rust vs go"], "max_results": 5}'
            ),
            exit_code=1,
            command="web_search",
        )

    queries = [str(q).strip() for q in queries if q and str(q).strip()]
    if not queries:
        return ToolResult(
            name="web_search",
            status="error",
            output="No non-empty queries provided.",
            exit_code=1,
            command="web_search",
        )

    if len(queries) > 5:
        queries = queries[:5]
        logger.warning(
            "web_search: truncated queries to 5 (got {})", len(args.get("queries", []))
        )

    try:
        max_results = int(args.get("max_results") or _MAX_RESULTS)
    except (ValueError, TypeError):
        logger.warning(
            "web_search: invalid max_results={!r}, using default {}",
            args.get("max_results"),
            _MAX_RESULTS,
        )
        max_results = _MAX_RESULTS

    max_results = max(1, min(max_results, _MAX_RESULTS_LIMIT))

    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            import ddgs  # noqa: F401 — проверка доступности пакета
    except ImportError:
        return ToolResult(
            name="web_search",
            status="error",
            output="ddgs not installed. Run: uv add ddgs",
            exit_code=1,
            command="web_search",
        )

    all_lines: list[str] = []
    for qidx, query in enumerate(queries):
        try:
            results = _search_with_retry(query, max_results)
        except Exception as e:
            logger.error("web_search failed | query={!r} error={}", query, e)
            all_lines.append(f"[Query {qidx + 1}: {query}]")
            all_lines.append(f"  Search failed: {e}")
            all_lines.append("")
            continue

        all_lines.append(f"[Query {qidx + 1}: {query}]")
        if not results:
            all_lines.append("  No results found.")
        else:
            for i, r in enumerate(results):
                title = r.get("title", "")
                result_url = r.get("href", r.get("link", ""))
                snippet = r.get("body", "")
                all_lines.append(f"  [{i}] {title}")
                all_lines.append(f"      {result_url}")
                all_lines.append(f"      {snippet}")
        all_lines.append("")

    return ToolResult(
        name="web_search",
        status="ok",
        output="\n".join(all_lines).strip(),
        exit_code=0,
        command=f"web_search [{', '.join(queries)}]",
    )
