---
name: subagents
description: Запуск параллельных субагентов (инструмент subagent) — для 2+ независимых крупных веток работы одновременно. Загружай ПЕРЕД тем как спавнить субагентов, чтобы узнать роли, пресеты, DAG-зависимости, изоляцию git-worktree и список доступных моделей.
---

# SUBAGENTS — параллельное выполнение

Up to 100 parallel subagents via the `subagent` tool, each with its own context and git worktree.

🔴 PURPOSE = PARALLELISM: 2+ INDEPENDENT, SIZABLE branches (≥5 tool calls each) running AT ONCE —
tests for different modules, refactoring separate parts, analysing several large files. Spawn ONLY
when there's more than one independent branch.
DON'T use for: a single/linear task (do it inline), spawning just ONE subagent, anything doable in
1-3 calls (worktree overhead ~10s), tasks that depend on each other's output, or anything needing `poll`.

A subagent does NOT see the conversation — its `prompt` must be self-contained (exact files, paths,
requirements, deliverable format). Modes: `mode=plan` (text-only, no tools) / `mode=agent` (full tools,
up to 50 iterations).

## WRITING THE PROMPT (this decides how good the subagent is)

A subagent is only as smart as its brief. Terse command-style prompts ("fix the bug", "do the tests")
produce shallow, generic work. Brief it like a smart colleague who just walked into the room — it has
NOT seen this conversation, doesn't know what you tried or why this matters.

- **Give the goal AND the why.** What you're accomplishing and how this piece fits — so it can make
  judgment calls, not just follow a narrow instruction.
- **Share what you already learned/ruled out.** Don't make it re-discover what you know.
- **Be concrete about scope:** exact files, paths, line numbers, what's IN and what's OUT, what another
  subagent is handling. Prove you understood the task — vague briefs get vague results.
- **Lookups vs investigations:** for a lookup, hand over the exact command/target. For an investigation,
  hand over the QUESTION, not prescribed steps — fixed steps become dead weight when the premise is wrong.
- **NEVER delegate understanding.** Don't write "based on your research, implement the fix" or "fix
  whatever you find" — that pushes the thinking onto the subagent instead of you doing it. Decide the
  what, delegate the how.
- **State the deliverable:** what it must return and in what form ("report under 150 words: what changed,
  files touched, how to verify"). The main agent reads only its final text + the git diff.
- **ALWAYS set BOTH `phase` and `label`.** Think in levels: phases → agents → per-agent config.
  `phase` = the stage/group this agent belongs to ("Scout", "Implement", "Verify") — the panel groups
  agents under it. `label` = a 1-2 word name of WHAT this agent does ("Auth API", "Landing"). Without
  them the panel shows a bland "Agents"/"Sub1". Even for a single flat fan-out give every task a phase
  (e.g. all "Scout") and a distinct label, so the panel reads clearly at a glance.
- **Shared brief for similar tasks — DON'T repeat yourself.** If several subagents get a largely
  IDENTICAL task (same context, rules, deliverable format, differing only in their slice), do NOT paste
  the same long prompt into each. Write the common part ONCE to a shared file (e.g. via write_file to
  `.data/scratch/<task>-brief.md`) and give each subagent a short prompt: "Read `<path>` for the full
  brief, then do YOUR part: <the specifics for this agent>". This keeps prompts small, guarantees every
  agent works from the same instructions, and lets you fix the brief in one place.

## COOPERATION

🔴 ONE CALL FOR THE WHOLE PIPELINE — NOT WAVE-BY-WAVE. The single biggest mistake is calling `subagent`
multiple times in a row (Scout call → wait → Implement call → wait → Verify call). DON'T. A multi-phase
run (research→implement→verify, or any chain of stages) is ONE `subagent` call with the `phases[]`
array — the orchestrator runs phases in order, each phase's tasks in parallel, and injects every
phase's output into the next via auto-`depends_on`. You write the entire plan up front in a single call
and let it drain; you do NOT manually gate one wave on the previous one's result.
    {"phases": [
      {"name": "Scout",     "tasks": [ {...}, {...} ]},
      {"name": "Implement", "tasks": [ {...}, {...} ]},   // auto depends on Scout
      {"name": "Verify",    "tasks": [ {...} ]}           // auto depends on Implement
    ]}
Spawn a SECOND `subagent` call ONLY when you genuinely cannot know the next phase's tasks until you've
read the previous one's output (e.g. Scout reveals which files exist, and you must DECIDE the
implementation specs yourself before delegating). If the phases are already plannable — and a standard
research→implement→verify shape almost always is — they go in ONE `phases[]` call.

- `depends_on`: list of 1-based indices that must finish first; their output is injected into this
  task's prompt. No-dep tasks run in parallel waves; dependents wait. Use to chain research→implement→review.
- 🔴 If task B needs a file/contract/output that task A produces, B MUST declare `depends_on:[A]` — then
  A's result is injected into B's prompt automatically. NEVER make a subagent wait with `sleep`, retry
  loops, or "cat the file later" to synchronize with a sibling: subagents in the SAME wave run in
  parallel and cannot see each other's files mid-flight. A `sleep N` before reading a sibling's output
  is always a bug — model the dependency with `depends_on` instead. Tasks in one wave must be truly
  independent (own files, no shared expectation of timing).
- `role`: coder | researcher | reviewer | planner | coordinator (focuses the subagent, narrows tools).
- `preset`: a saved role from .data/agents/<name>/AGENT.md (supplies instructions/tools/model; you pass
  only `prompt`). Create one by writing the AGENT.md via write_file.
- Coordinator pattern: when several subagents must share names/signatures, make task #1 role="coordinator"
  (writes the contract to a shared scratchpad) and the rest `depends_on:[1]` — decides the contract once
  instead of resolving conflicts later.
- Concurrency cap = subagent.max_concurrency (default 12); request hundreds, they drain in batches.

## GIT WORKTREE ISOLATION (mode=agent)

Each subagent works on branch `subagent/<run-id>-<N>`; changes don't touch your tree until you merge.
The orchestrator auto-commits and reports branch/SHA/diff and EXACT `git show`/`git log -p` commands
based on the real base — USE THOSE, never hardcode main/master.
Merge (`git merge --no-ff <branch>`), cherry-pick a SHA, or discard (`git branch -D <branch>`) yourself.
All `subagent/*` branches are auto-deleted at the next run — don't store anything long-term. Conflicts
are yours: resolve via patch_file/write_file, then `git add -A && git commit`; merge smaller changes first.

## MODEL & PRESETS

In the subagent tool you can specify a "model" field for each task (display_name or model_id). If not
specified — the main agent's model is used. Reuse a preset via {"preset": "<name>", "prompt": "<task>"}.
The live list of available models and presets is appended below when this skill is loaded.