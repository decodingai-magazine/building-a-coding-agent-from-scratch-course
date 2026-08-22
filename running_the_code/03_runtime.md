# Headless Runtime — `decode run`, recording & replay

The REPL needs you at the keyboard. `decode run` doesn't: it runs one task to completion unattended and prints the answer on stdout (pipe-clean). Same agent, different driver — a plain `asyncio.run` around the very `build_agent()` the REPL uses, with no durability layer at all: a crash is a re-run ([ADR-0019 §1](../docs/adr/0019-kitaru-replay-runtime.md)).

What you *can* keep is the **record**. With one opt-in, every run (REPL turns included) is filed on the [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) workspace as a **Kitaru Session** — every LLM and tool call, node by node — and a recorded Session is the thing a **Replay** re-executes later, on a Worker, with or without a change.

Prerequisite: the core setup from [01_install_and_usage.md](01_install_and_usage.md). Nothing else — recording is off by default and costs nothing when it is.

## Run a task

```bash
decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs inline with no approval prompt, and `ask_user` is a no-op. There is no pause and no wait: durable HITL died with the durable runtime, because upstream removed the primitive ([ADR-0019 §1](../docs/adr/0019-kitaru-replay-runtime.md)).
- **stdout is the answer, alone.** Notices (a hand-back line, a recording warning) go to stderr; the detail is in `.decode/logs/decode.log`. So `decode run … | pbcopy` is safe.
- **Same everything else as the REPL** — the provider-key guard, `--model` (Model Override), `--repo`/`--local` with a sandbox mode, Hand-back on completion, and Opik tracing. `RUNTIME_ENABLED=false` disables the subcommand with one friendly line.
- **`TASK` is optional** — because a Kitaru Worker passes the prompt in the environment, not on the command line (see the replay section). With no task anywhere you get one line naming both ways to supply one.

## Record runs as Kitaru Sessions (opt-in)

Recording is presence-based and lives in exactly one function, the **Recording Seam** (`src/decode/runtime/recording.py`). Two variables switch it on:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io   # adapter-owned (or just `kitaru login`)
export KITARU_AGENT_ID=<uuid of the workspace's `decode` agent>     # decode's ONE recording knob
uv run decode run "explain what this repo does"
uv run kitaru session get <SESSION_ID>          # the run, node by node
```

- **Both surfaces record.** The REPL wraps the same way, with `session_name` = the decode session id, so a conversation's turns group together on the workspace.
- **`KITARU_API_URL` must be *exported*, not merely written in `.env`.** decode never reads it: the adapter's own client resolves the connection (env, else your `kitaru login` store). `set -a && . .env && set +a` is the shortcut.
- **Off is byte-identical.** With `KITARU_AGENT_ID` empty, no kitaru module is even imported.
- **Unreachable workspace degrades, it never blocks.** A user-launched run drops to the bare agent, prints ONE stderr line (`[kitaru] not recording this run: … continuing on the bare agent`), and still exits 0 — recording is an observer, never an availability dependency. A run spawned by a Kitaru **Worker** hard-fails instead: an unrecorded replay is a lying experiment.

## Replay a recorded session on a Kitaru Worker

Replays run **from the top** on a Worker you start yourself — the Kitaru server schedules, your machine executes ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)). Three steps, once per machine:

```bash
# 1. Register the Agent Version the Worker spawns (adds a version; never a second agent).
uv run python scripts/register_kitaru_agent.py --dry-run   # prints the exact kitaru command
uv run python scripts/register_kitaru_agent.py

# 2. Start a Worker from a shell that HAS your provider credentials. Kitaru layers a task's env
#    on top of the Worker's own, so the keys reach `decode run` without ever leaving this host —
#    which is why the registered version attaches no secret.
set -a && . .env && set +a && kitaru worker start

# 3. Replay one recorded session (baseline: no --override). --tool-policy history replays the
#    recorded tool results instead of calling live tools; without it the server default MAY execute
#    them for real.
kitaru replay create <SESSION_ID> --agent decode@<VERSION> --evaluator <NAME>@<VERSION> \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}'
kitaru replay get <REPLAY_ID>        # status + result_session_id
kitaru session get <RESULT_SESSION>  # the replayed run, node by node
```

The registered version re-creates decode's context rather than simulating it: `decode run` with no inline prompt (the task arrives in `KITARU_TASK_INPUTS`), `SANDBOX_MODE=docker`, and a Workspace that is a fresh clone of this repo. Its working dir is a **Harness Home outside the repo** (`~/.decode-kitaru-worker`), so a replay's sessions, logs and Workspace never land in your working tree — watch it work with `docker ps` and `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

Which model a replay uses is the Worker shell's `LLM_PROVIDER` / model config, so a baseline replay reproduces the recorded run only if you start the Worker with the same provider it recorded against.

A **Baseline Replay** (no `--override`) is the control: it proves the Session still reproduces on the current Agent Version, which is what makes a later what-if — a model swap, a system-prompt change — attributable to the change and not to drift. Overrides, evaluators, cohorts and experiments are the operator surface documented in the `kitaru-investigation` and `kitaru-replay-experiment` skills.

## Troubleshooting

| Symptom | What it means |
|---|---|
| `[kitaru] not recording this run: … is unavailable` | The seam degraded: the workspace could not be reached (or `KITARU_AGENT_ID` is not an agent on it). The run itself is fine. Check `uv run kitaru status` — it prints the resolved `server_url` and whether the stored credential is still valid; re-auth with `kitaru login <url>`. |
| The run records nothing and says nothing | `KITARU_AGENT_ID` is empty, or `KITARU_API_URL` was set in `.env` but never exported — decode does not read that variable, the adapter's client does. |
| A replay stays queued | No Worker is claiming it: `kitaru worker list` should show one `live`. A Worker only runs while its shell does. |
| A replay fails at the first model request | The Worker's shell had no provider credential (the run spec attaches none, by design), or that provider is down. Restart it with `set -a && . .env && set +a && kitaru worker start`. |
| A replay fails before the agent starts | Usually the docker daemon (the Agent Version pins `SANDBOX_MODE=docker`) or a stale `--command` path after a fresh `make install`. Re-register: `uv run python scripts/register_kitaru_agent.py`. |

## Go further

- Run headless **inside a sandbox** and on any repo: [04_sandboxing.md](04_sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`).
- Hydrate the run's secrets from an Environment Bucket instead of `.env`: [06_credentials.md](06_credentials.md).
- Where the workspace, the Worker and the retired self-hosted stack sit: [07_infra.md](07_infra.md).
