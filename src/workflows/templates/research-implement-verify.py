meta = {"name": "research-implement-verify", "description": "Research, synthesize, implement, independently verify"}

try:
    args
except NameError:
    args = {}

async def run(ctx):
    request = args.get("request") or args.get("goal") or ""
    research_prompts = args.get("research_prompts") or [
        f"Research the codebase for this request. Report exact files, functions, risks, and likely implementation path. Do not modify files.\n\nREQUEST:\n{request}",
        f"Find existing tests/checks and real entrypoints relevant to this request. Report exact commands to verify. Do not modify files.\n\nREQUEST:\n{request}",
    ]

    ctx.phase("Research")
    research = await ctx.parallel([
        lambda prompt=prompt, i=i: ctx.agent(prompt, label=f"research-{i}", role="researcher")
        for i, prompt in enumerate(research_prompts, start=1)
    ])

    ctx.phase("Synthesis")
    spec = await ctx.agent(
        "Synthesize the research into an implementation spec. You are not implementing. "
        "Return exact files/lines, desired changes, acceptance criteria, and verification commands. "
        "Never say 'based on findings'; write the concrete spec.\n\n"
        f"REQUEST:\n{request}\n\nRESEARCH:\n{research}",
        label="synthesis",
        role="planner",
    )

    ctx.phase("Implementation")
    implementation = await ctx.agent(
        "Implement this exact spec. Make minimal, production-quality changes. "
        "Run targeted checks you can run. Report changed files and commands/results.\n\n"
        f"REQUEST:\n{request}\n\nSPEC:\n{spec}",
        label="implement",
        role="coder",
    )

    ctx.phase("Verification")
    verification = await ctx.verify(
        original_request=request,
        evidence={"spec": spec, "implementation": implementation},
        checks=args.get("checks") or [],
    )

    return {
        "request": request,
        "research": research,
        "spec": spec,
        "implementation": implementation,
        "verification": verification,
    }