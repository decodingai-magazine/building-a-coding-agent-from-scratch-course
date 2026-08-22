# Headless Runtime — `decode run`, durability & replay

The REPL needs you at the keyboard. `decode run` doesn't: it runs one task to completion unattended and prints the answer on stdout (pipe-clean). Same agent, different driver — a [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) **durable flow** checkpoints every model and tool call, so an expensive run survives a crash and resumes instead of re-paying for finished work ([ADR-0008](../docs/adr/0008-kitaru-durable-runtime.md)).

Prerequisite: the core setup from [01_install_and_usage.md](01_install_and_usage.md). Nothing else — the local Kitaru stack runs offline, no server needed.

## Run a task

```bash
decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs with no approval prompt. `decode run --hitl` instead pauses the whole execution on a durable Kitaru wait for `write`/`edit`/`bash`/`ask_user`; resolve from another terminal with `kitaru executions input <exec_id> --wait <name> --value 'true'`.
- **Offline local stack** — no Kitaru server or `kitaru init` needed. Inspect runs with `kitaru executions list` / `get <id>` / `logs <id>`; `kitaru login` starts the optional local web dashboard at `http://127.0.0.1:8383` (`kitaru logout` falls back to the server-less local database if the daemon hangs).
- **Guards** — the same provider-key guard as the REPL; `RUNTIME_ENABLED=false` disables the subcommand with a friendly line.

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

## Troubleshooting

### macOS: the local Kitaru server crashes mid-run

A run starts fine, then floods with `RemoteDisconnected` / `Connection refused` on `127.0.0.1:8383`: the server daemon died to Apple's ObjC fork-safety abort — its log (`~/Library/Application Support/kitaru/zen_server/daemon/service.log`) ends with `objc[…]: … fork() was called … Crashing instead.` Fix either way:

```bash
uv run kitaru logout                                          # simplest: no daemon, no crash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run kitaru login   # or keep the dashboard
```

Prefer `logout` unless you actually want the web dashboard — `decode run`, `kitaru executions`, and `kitaru secrets` all work against the server-less local database. Confirm with `kitaru info`: `Local server: registered but unavailable` means a stale registration is still pointing at the dead daemon.

## Go further

- Run headless **inside a sandbox** and on any repo: [04_sandboxing.md](04_sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`).
- Run headless **in the cloud** — the whole agent on Modal, checkpoints on a self-hosted server: [07_infra.md](07_infra.md).
- Hydrate the run's secrets from an Environment Bucket instead of `.env`: [06_credentials.md](06_credentials.md).
