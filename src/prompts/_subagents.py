SUBAGENTS_BLOCK = r"""# Subagents

`subagent` runs parallel workers with separate context. Use it only for 2+ independent sizable branches,
not for a single linear task or one-off 1–3 call work. Model dependencies with `depends_on`; never use
sleep/poll to wait for sibling agents.

Default workers share the working tree, so assign distinct files/paths. Use `isolate=true` only when
shared edits are unavoidable: isolation prevents agents OVERWRITING each other, but same-region edits
still create merge conflicts, so prefer DISTINCT files even under isolation.

A subagent sees only its prompt. Include goal, why, known facts, exact scope, out-of-scope items,
deliverable format, and verification commands. Do not delegate vague "fix whatever you find" work.
For sizable fan-out, finish with an independent verifier returning VERDICT, EVIDENCE, FINDINGS, NEXT_FIX.

Before spawning subagents, load the `subagents` skill for the full guide."""