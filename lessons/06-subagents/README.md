# Lesson 6 — Agents catalog, subagents & parallel fan-out

Build/Plan/Code-Reviewer agents and the read-only Explore subagent — parallel
fan-out with budgets, report contracts, and no silent failures.

## Run it

```bash
./lessons/06-subagents/run.sh
```

The main loop spawns read-only Explore subagents in parallel (up to 4); each is
the same agentic loop re-entered with restricted tools, and each hands back a
compressed report instead of flooding the parent's context.

## Playbook (interactive)

```bash
uv run decode
/demo-4-review-swarm
```

The showcase: three parallel Explore subagents fan out over the codebase and
merge into one verdict. Watch the parent context stay small while the children
do the reading.

Also try the catalog: launch with a different primary agent and note what
changes is the *tool surface and prompt*, not the loop.

## Deep dives

- `src/decode/agents/`
- [ADR-0013 — Explore subagents](../../docs/adr/0013-explore-subagents.md)
- [ADR-0017 — resilient parallel subagent fan-out](../../docs/adr/0017-resilient-parallel-subagent-fanout.md)

## Background reading

| Article | Why read it here |
|---|---|
| [Stop Building AI Agents](https://www.decodingai.com/p/stop-building-ai-agents) | When simpler workflow patterns beat agents — and why agents shine specifically in human-in-the-loop settings. |
| [Getting Agent Architecture Right](https://www.decodingai.com/p/getting-agent-architecture-right) | Choosing the simplest structure (workflow vs agent vs hybrid) per task ambiguity — orchestration judgment. |
| [Stop Building AI Agents. Use These 5 Patterns Instead.](https://www.decodingai.com/p/stop-building-ai-agents-use-these) | Five workflow patterns incl. parallelization and orchestrator-worker — the vocabulary behind subagent fan-out. |
| [Scaling to 120+ AI Agents Without Losing Control](https://www.decodingai.com/p/scaling-120-ai-agents-two-tier-orchestration) | Conductor + specialist-subagent two-tier orchestration that stays debuggable at scale. |
| [From 12 Agents to 1: AI Agent Architecture Decision Guide](https://www.decodingai.com/p/from-12-agents-to-1-ai-agent-architecture-decision-guide) | Decision framework for single vs multi-agent — the anti-overengineering counterweight to an agents catalog. |
| [Stop Orchestrating AI Agents. Use Ralph Loops Instead.](https://www.decodingai.com/p/ralph-loops) | Single-agent self-review loops as an alternative to multi-agent orchestration — directly about coding-agent loops. |
| [From Vibe Coding to a Real Engineering Team](https://www.decodingai.com/p/squid-my-agentic-coding-setup-may-2026) | A live Build/Plan/Review agents catalog with human gates — the author's own six-agent coding team. |
