from tools.models import ToolCall, ToolResult
from ui.poll import run_poll


def execute_poll(call: ToolCall) -> ToolResult:

    args = call.args
    steps = args.get("steps", [])

    if not steps:
        question = args.get("question", "")
        options = args.get("options", [])
        if question:
            steps = [{"question": question, "options": options}]

    if not steps:
        return ToolResult(
            name="poll",
            status="error",
            output="No questions provided",
            exit_code=1,
            command="poll",
        )

    results = run_poll(steps[:10])

    lines = []
    for r in results:
        answer = r["answer"]
        if isinstance(answer, list):
            answer = ", ".join(answer)
        lines.append(f"Q: {r['question']}")
        lines.append(f"A: {answer}")
        lines.append("")

    return ToolResult(
        name="poll",
        status="ok",
        output="\n".join(lines).strip(),
        exit_code=0,
        command="poll",
    )
