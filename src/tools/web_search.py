from __future__ import annotations

from loguru import logger

from tools.models import ToolCall, ToolResult

_MAX_RESULTS = 5


def execute_web_search(call: ToolCall) -> ToolResult:
    args = call.args or {}

    queries = args.get("queries", None)
    if not queries or not isinstance(queries, list):
        return ToolResult(
            name="web_search",
            status="error",
            output=(
                'No queries provided. '
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
        logger.warning("web_search: truncated queries to 5 (got {})", len(args.get("queries", [])))

    try:
        max_results = int(args.get("max_results") or _MAX_RESULTS)
    except (ValueError, TypeError):
        logger.warning(
            "web_search: invalid max_results={!r}, using default {}",
            args.get("max_results"), _MAX_RESULTS,
        )
        max_results = _MAX_RESULTS

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

    all_lines: list[str] = []
    for qidx, query in enumerate(queries):
        try:
            results = DDGS().text(query, max_results=max_results)
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
        command=" web_search ".join(queries),
    )
