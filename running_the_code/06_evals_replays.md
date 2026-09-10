# 06 — Record and replay with Kitaru

Replay-based evals on your own traffic with [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs), all from your laptop. ~30 minutes; one provider key; Docker for the replay step.

Vocabulary: a run is recorded as a **Session**; humans judge Sessions in an **Investigation**; judged Sessions freeze into a **Cohort**; an **Evaluator** turns a criterion into a repeatable verdict; a **Replay** re-runs a Session from the top on a **Worker** you start, optionally with one change, so the same Evaluator scores before and after. The server executes nothing ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

Managed workspace, free for the course: `https://f5ee9622-kitaru.cloudinfra.zenml.io`.

## 1. Log in

`kitaru` came with `make install`.

```bash
uv run kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # device flow in the browser
uv run kitaru status                                              # first check whenever anything looks off
```

Optional: drive the workspace from Claude Code / Cursor. `.mcp.json` in this repo already starts `kitaru-mcp` in `standard` mode; skills: `npx skills add zenml-io/kitaru-skills`.

## 2. Record sessions

Two variables switch recording on. `KITARU_API_URL` must be **exported**: decode never reads it, the kitaru adapter does.

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
export KITARU_AGENT_ID=<uuid from `uv run kitaru agent get decode`>
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # node by node
```

> ✅ The session appears in the list, every model and tool call a node.

Since kitaru 0.24 `session list` returns no inputs/outputs — add `--include-payloads` when you want them (or read one session with `session get`).

REPL turns record too, grouped by decode session id. Remote runs on Modal record when the same keys ride the headless Secret ([04 §2](04_deploy.md#2-create-the-decode-headless-env-secret)). `KITARU_AGENT_ID` empty = no recording, no kitaru import. Unreachable workspace: a user-launched run continues on the bare agent with one `[kitaru] not recording this run` line and exits 0; a Worker-spawned run hard-fails instead.

Backfill from Opik with the custom importer (a Worker, §4, executes it; payloads split at 50 MiB):

```bash
uv run kitaru session import export.json --importer opik@1 --agent decode@1 \
  --media-type application/json --tag opik-backfill --wait
```

## 3. Judge, freeze, encode

```bash
# review worklist; verdicts (acceptable / problematic) happen at the links.review URL it prints
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <ID> --session-question '<ID>:observation=What do you notice? Did it match what should have happened?'

# freeze the reviewed sessions into an immutable cohort
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <ID> [...]

# encode the criterion as a versioned evaluator (deterministic Python)
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
uv run kitaru evaluator register my-check --script evaluators/my_check.py --entrypoint evaluate

# baseline sweep, no replay
uv run kitaru session evaluate --tag opik-backfill --evaluator 'my-check@1' --wait
```

Worked example in this repo: cohort + evaluator `decode-bad-request-400@1` ([`evaluators/decode_bad_request_400.py`](../evaluators/decode_bad_request_400.py)), flagging exactly the 2 crash sessions out of 38. Guided version: the `kitaru-investigation` skill.

## 4. Start a Worker on your laptop

A Worker claims replay / evaluator / importer tasks and spawns an **Agent Version**, the registered run spec: `decode run` with `SANDBOX_MODE=docker`, a fresh clone of this repo, Harness Home `~/.decode-kitaru-worker`. Register once per machine:

```bash
uv run python scripts/register_kitaru_agent.py             # adds agent version 2 to `decode`
uv run kitaru agent version list decode
```

A replay inherits the Worker's env, nothing is stored on the workspace: start it from a shell that carries your provider keys and the same `LLM_PROVIDER` / model the sessions were recorded with.

```bash
cd <repo root>                       # pwd first: the checkout nests two same-named dirs
set -a && . ./.env && set +a
unset KITARU_AGENT_ID                # a Worker Task's token cannot use agent routes
uv run kitaru worker start --concurrency 10
```

> ✅ From another terminal, `uv run kitaru worker list` shows yours with `live: True`. A silent worker is a healthy worker. Watch a replay: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

## 5. Replay, then compare

A **baseline replay** (no override) is the control: it proves the Session reproduces on the current Agent Version, so a later what-if is attributable to the change.

```bash
uv run kitaru replay create <SESSION_ID> --agent decode@2 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --baseline-evaluation-mode if-missing
uv run kitaru job watch <JOB_ID>          # from the create output
uv run kitaru replay get <REPLAY_ID>      # status + result_session_id
```

`--evaluator` is required. `history` answers tool calls from the recording; `on_miss: error_result` keeps a miss from executing live (`passthrough` runs real bash, never default to it). `--baseline-evaluation-mode` (kitaru 0.25, replacing `--evaluate-baselines`) scores the original too: `if-missing` (the default) scores it once, `force` re-scores, `none` skips. What-if: `--override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'`.

> ✅ Dashboard (URL from `kitaru status`) → Agents → decode → Sessions: the replay's page has a **Compare** link against its baseline. Any two sessions: tick both → Compare.

A whole cohort against one change:

```bash
uv run kitaru experiment create cheaper-model \
  --evaluator 'decode-bad-request-400@1' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'
uv run kitaru experiment run start cheaper-model \
  --cohort-version <ID> --agent decode@2 --baseline-evaluation-mode if-missing --wait   # non-zero on failure: a CI gate
```

Designing a what-if: the `kitaru-replay-experiment` skill.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `[kitaru] not recording this run: … is unavailable` | `uv run kitaru status`; re-auth with `kitaru login <url>`; check `KITARU_AGENT_ID` is an agent on that workspace. |
| Records nothing, says nothing | `KITARU_AGENT_ID` empty, or `KITARU_API_URL` in `.env` but not exported. |
| Replay stays queued | no live Worker (`kitaru worker list`), or wrong Agent Version: v2 = laptop Worker, v3 = Modal Worker ([07](07_evals_replays_deploy.md)). |
| `Decode: set GEMINI_API_KEY in your environment` in a replay | Worker shell had no provider key, or `.env` was sourced in the wrong directory. `pwd`, source, restart the Worker. |
| Replay fails before the agent starts | Docker down, or stale command path after `make install`: re-run `scripts/register_kitaru_agent.py`. |
| `403: Task credentials are not accepted on this route` | `unset KITARU_AGENT_ID` in the Worker shell. |
| `ModelHTTPError: 503` inside a replay | the pipe works; only the model was gone. `ModuleNotFoundError` / command not found = setup broken. |

---

**Next:** [07_evals_replays_deploy.md](07_evals_replays_deploy.md) — run the Worker on Modal so replays keep going with the laptop closed.
