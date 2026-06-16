meta = {"name": "loop-until-pass", "description": "Implement and independently verify until PASS or max_rounds"}

try:
    args
except NameError:
    args = {}

async def run(ctx):
    request = args.get("request") or args.get("goal") or ""
    spec = args.get("spec") or request
    max_rounds = int(args.get("max_rounds") or 3)

    ctx.phase("Implement / Verify Loop")

    async def implement(findings, round_index):
        prompt = (
            f"Round {round_index}: implement or fix the request below. "
            "Use the verifier findings if present. Run targeted checks and report changed files.\n\n"
            f"REQUEST:\n{request}\n\nSPEC:\n{spec}\n\nVERIFIER FINDINGS FROM PREVIOUS ROUND:\n{findings or '(none)'}"
        )
        return await ctx.agent(prompt, label=f"implement-{round_index}", role="coder")

    async def verify(implementation, round_index):
        return await ctx.verify(
            original_request=request,
            evidence={"spec": spec, "implementation": implementation, "round": round_index},
            checks=args.get("checks") or [],
            label=f"verify-{round_index}",
        )

    return await ctx.loop_until_pass(implement, verify, max_rounds=max_rounds)