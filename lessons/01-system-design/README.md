# Lesson 1 — What we're building + system design

Why the harness — not the model — decides how good your agent is. Anatomy of
harness vs loop vs model, and the milestone map for the whole build.

## Run it

```bash
./lessons/01-system-design/run.sh
```

One headless run through the whole harness: CLI → runtime driver → agent loop →
gated tools → provider, answer on stdout. The prompt asks the agent to map its
own source tree — the output *is* the system-design diagram of this lesson.

## Playbook (interactive)

```bash
uv run decode
```

1. Look at the banner: provider, model, sandbox mode — the three swappable seams.
2. Ask: `what is this repo and how is it structured?` — watch the loop stream,
   call read-only tools without asking, and render the answer.
3. Quit (`Ctrl-D`), then look at `.decode/`: `sessions/*.jsonl` (the replayable
   transcript), `MEMORY.md`, `logs/decode.log`. Every harness artifact, one dir.

## Deep dives

- [ADR index](../../docs/adr/) — every non-obvious decision, with the rejected alternatives.
- [Course outline](../../README.md#-course-outline) — the milestone map.

## Background reading

| Article | Why read it here |
|---|---|
| [We Killed RAG, MCP, and Agentic Loops. Here's What Happened.](https://www.decodingai.com/p/building-vertical-ai-agents-case-study-1) | Production case for choosing the simplest harness that works — one-shot calls + big-context CAG beating loops, MCP, and RAG. |
| [Agentic AI Engineering Guide: 6 Critical Mistakes](https://www.decodingai.com/p/agentic-ai-engineering-guide-6-mistakes) | Six silent system-design failures — models aren't the problem, the surrounding system is. |
| [Agentic Harness Engineering](https://www.decodingai.com/p/agentic-harness-engineering) | The lesson thesis verbatim: the harness (tools, memory, orchestration, sandboxes), not the model, decides agent quality. |
| [Build, Configure, or Use As-Is: The Agentic Harness](https://www.decodingai.com/p/agentic-harness-system-design) | Component-by-component harness teardown with a build/configure/use-as-is decision framework. |
