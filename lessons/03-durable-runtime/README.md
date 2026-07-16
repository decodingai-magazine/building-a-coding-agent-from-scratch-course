# Lesson 3 — Durable execution, HITL & replay

`kill -9` a headless run and watch it resume from checkpoints. Durable
human-in-the-loop waits, and what-if replay with the model swapped.

## Run it

```bash
./lessons/03-durable-runtime/run.sh
```

One durable `decode run` (a checkpoint per model call and per tool call), then
its execution record via `kitaru executions get`, then the exact replay command
to fork it with the model swapped.

## Playbook (interactive)

1. **Crash survival.** Start an expensive run, kill it mid-flight, re-run:

   ```bash
   uv run decode run "read every ADR in docs/adr and produce a decision timeline" &
   sleep 20 && kill -9 %1
   uv run decode run "read every ADR in docs/adr and produce a decision timeline"
   ```

   The second run resumes from checkpoints instead of re-paying for finished
   model and tool calls.

2. **Durable HITL wait.** `uv run decode run --hitl "delete loop_demo.txt"` —
   the execution *pauses* on a durable Kitaru wait for the gated tool. Resolve
   it from another terminal:

   ```bash
   uv run kitaru executions input <exec_id> --wait <name> --value 'true'
   ```

3. **What-if replay.** Take any recorded exec_id and fork it from an anchor with
   a different model — upstream serves from the original run's cache:

   ```bash
   uv run decode replay <ID> --from decode_runtime_model_request --model gemini-2.5-pro
   ```

   A trustworthy what-if does a **baseline rerun first** (no `--model`) and
   diffs the fork against that, not against the original.

## Deep dives

- `src/decode/runtime/`
- [ADR-0008 — Kitaru durable runtime](../../docs/adr/0008-kitaru-durable-runtime.md)
- [ADR-0010 — runtime replay](../../docs/adr/0010-runtime-replay.md)
- [getting_started/runtime.md](../../getting_started/runtime.md)

## Background reading

| Article | Why read it here |
|---|---|
| [Building Reliable AI Agents with Durable Workflows](https://www.decodingai.com/p/building-reliable-ai-agents-with) | Checkpoint-based durable execution for agents (save-point recovery from crashes and rate limits) — the exact problem Kitaru solves here. |
