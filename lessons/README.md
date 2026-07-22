# Lessons — runnable entrypoints

Eight lessons mirroring the [course outline](../README.md#-course-outline).
Each dir pairs a **`run.sh` entrypoint** (the scriptable slice of that lesson's
decode surface) with a **README playbook** (the interactive parts you type into
the REPL yourself) and links the newsletter articles that motivated it.

Prerequisite: the 5-minute core setup in
[running_the_code/install_and_usage.md](../running_the_code/install_and_usage.md)
(`make install` + `GEMINI_API_KEY` in `.env`). Extra requirements are per-row.

| Lesson | Scope | Entrypoint | Extra requirements |
|--------|-------|------------|--------------------|
| [1 — System design](01-system-design/) | harness vs loop vs model, the whole anatomy in one run | `01-system-design/run.sh` | — |
| [2 — Agent loop](02-agent-loop/) | ReAct turn end-to-end · steer / follow-up / abort · the y/n gate · provider seam | `02-agent-loop/run.sh` | — |
| [3 — Durable runtime](03-durable-runtime/) | checkpoints, kill -9 resume, durable HITL waits, what-if replay | `03-durable-runtime/run.sh` | — |
| [4 — Context engineering](04-context-engineering/) | memory injection, compaction, skills, LSP, the footer gauge | `04-context-engineering/run.sh` | — |
| [5 — Permissions & sandbox](05-permissions-and-sandbox/) | allow/ask/deny · isolated Docker/Modal Workspace · `--repo` · git hand-back | `05-permissions-and-sandbox/run.sh` | Docker daemon |
| [6 — Subagents](06-subagents/) | agents catalog · parallel Explore fan-out · compressed reports | `06-subagents/run.sh` | — |
| [7 — Evals](07-evals/) | outcome benchmark, regression probes, LLM-as-judge, online evals | `07-evals/run.sh` | `OPIK_API_KEY` 💰 |
| [8 — Ship](08-ship/) | environment-scoped secrets, no-backfill invariant, cloud pipeline | `08-ship/run.sh` | Kitaru local stack (💰 only infra.md) |

Each lesson's **Background reading** section maps the
[Decoding AI](https://www.decodingai.com) articles that motivated it — 43
articles across the eight lessons, concentrated in the AI Agents Foundations
series (lessons 1–2), the AI Evals series (lesson 7), and the 2026
harness-engineering pieces (lesson 1).

Run any entrypoint from the repo root:

```bash
./lessons/03-durable-runtime/run.sh
```

Every script is self-locating, guards its own prerequisites with a friendly
line, and leaves your checkout untouched (lesson 5 works on a *clone* inside
the Workspace; its output comes back as a `decode/<session-id>` branch).
