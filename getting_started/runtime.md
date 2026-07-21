# Headless Runtime — `decode run`, durability & replay

The REPL needs you at the keyboard. `decode run` doesn't: it runs one task to completion unattended and prints the answer on stdout (pipe-clean). Same agent, different driver — a [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) **durable flow** checkpoints every model and tool call, so an expensive run survives a crash and resumes instead of re-paying for finished work ([ADR-0008](../docs/adr/0008-kitaru-durable-runtime.md)).

Prerequisite: the core setup from [install_and_usage.md](install_and_usage.md). Nothing else — the local Kitaru stack runs offline, no server needed.

## Run a task

```bash
decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs with no approval prompt. `decode run --hitl` instead pauses the whole execution on a durable Kitaru wait for `write`/`edit`/`bash`/`ask_user`; resolve from another terminal with `kitaru executions input <exec_id> --wait <name> --value 'true'`.
- **Offline local stack** — no Kitaru server or `kitaru init` needed. Inspect runs with `kitaru executions list` / `get <id>` / `logs <id>`; `kitaru login` starts the optional local web dashboard at `http://127.0.0.1:8383` (`kitaru logout` falls back to the server-less local database if the daemon hangs).
- **Guards** — the same provider-key guard as the REPL; `RUNTIME_ENABLED=false` disables the subcommand with a friendly line.

## Replay & what-if

Every `decode run` records a checkpoint per model call and per tool call, so you can re-run any recorded execution from any anchor with the **model swapped** and see what would have happened ([ADR-0010](../docs/adr/0010-runtime-replay.md)):

```bash
decode run "…"                                              # stderr prints exec_id + a replay hint
kitaru executions get <ID>                                  # list the checkpoint anchors
decode replay <ID> --from decode_runtime_model_request --model gemini-2.5-pro
```

Upstream of `--from` serves from the original run's cache; the anchor and downstream re-execute for real. The new fork's `exec_id` prints on stderr — compare fork vs original with `kitaru executions get`. `--from` is required; a trustworthy what-if does a **baseline rerun** first (no `--model`) and diffs the fork against that. `decode replay` is bypass-only (HITL replays re-ask every wait — use `kitaru executions replay`).

## Troubleshooting

### macOS: the local Kitaru server crashes mid-run

A run starts fine, then floods with `RemoteDisconnected` followed by `Connection refused` on `127.0.0.1:8383`. The server *daemon* died — its log (`~/Library/Application Support/kitaru/zen_server/daemon/service.log`) ends with `objc[…]: +[NSCharacterSet initialize] may have been in progress in another thread when fork() was called … Crashing instead.` That is Apple's ObjC fork-safety abort: the daemon forks while the Apple runtime is initializing on another thread, and macOS kills the child rather than inherit a half-built runtime. Fix it either way:

```bash
uv run kitaru logout                                          # simplest: no daemon, no crash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run kitaru login   # or keep the dashboard
```

Prefer `logout` unless you actually want the web dashboard — `decode run`, `kitaru executions`, and `kitaru secrets` all work against the server-less local database. Confirm with `kitaru info`: `Local server: registered but unavailable` means a stale registration is still pointing at the dead daemon.

## Go further

- Run headless **inside a sandbox** and on any repo: [sandboxing.md](sandboxing.md) (`SANDBOX_MODE=docker decode run --repo <url> "<task>"`).
- Run headless **in the cloud** — the whole agent on Modal, checkpoints on a self-hosted server: [infra.md](infra.md).
- Hydrate the run's secrets from an Environment Bucket instead of `.env`: [credentials.md](credentials.md).
