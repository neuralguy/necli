meta = {"name": "audit-verify", "description": "Parallel audit with independent verification of findings"}

try:
    args
except NameError:
    args = {}

async def run(ctx):
    request = args.get("request") or args.get("goal") or "Audit the project"
    areas = args.get("areas") or ["agent", "tools", "workflow"]

    ctx.phase("Audit")
    findings = await ctx.parallel([
        lambda area=area: ctx.agent(
            f"Audit area '{area}' for issues relevant to this request. "
            "Do not modify files. Return concrete findings with file:line, severity, and evidence.\n\n"
            f"REQUEST:\n{request}",
            label=f"audit-{area}",
            role="reviewer",
        )
        for area in areas
    ])

    ctx.phase("Verify Findings")
    verification = await ctx.parallel([
        lambda finding=finding, i=i: ctx.verify(
            original_request=request,
            evidence=finding,
            label=f"verify-{i}",
        )
        for i, finding in enumerate(findings, start=1)
    ])

    return {"request": request, "findings": findings, "verification": verification}