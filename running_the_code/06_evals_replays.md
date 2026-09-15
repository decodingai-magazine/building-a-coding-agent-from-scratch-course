# 06. Record and replay your evals with Kitaru

Replay-based evals on your own traffic with [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs), all from your laptop. ~30 minutes; one provider key; Docker for the replay step.

Vocabulary: a run is recorded as a **Session**; humans judge Sessions in an **Investigation**; judged Sessions freeze into a **Cohort**; an **Evaluator** turns a criterion into a repeatable verdict; a **Replay** re-runs a Session from the top on a **Worker** you start, optionally with one change, so the same Evaluator scores before and after. The server executes nothing ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

## 0. Pick a server

A Kitaru Server is **one URL** ([ADR-0022](../docs/adr/0022-evals-v2-own-harbor-on-opik.md) §11) and
everything below is identical on either of them: a **local OSS deployment** on your laptop, or the **managed workspace**.

|                      | Local OSS server                                                              | Managed workspace                                        |
| -------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------- |
| URL                  | `http://localhost:8000`                                                       | `https://f5ee9622-kitaru.cloudinfra.zenml.io`            |
| Start it             | `make kitaru-local` (docker compose: the server image + `postgres:16-alpine`) | `uv run kitaru login <url>` (device flow in the browser) |
| Stop it              | `uv run kitaru logout`                                                        | —                                                        |
| Where Workers run    | your laptop                                                                   | your laptop — the workspace only stores the results      |

Everything decode needs on a server — the `decode` agent + its Agent Version, the `opik`
importer, every evaluator in `evaluators/` — is ONE idempotent script; `make kitaru-local` runs it
for you, and re-running it changes nothing:

```bash
make kitaru-local                                           # login --local + bootstrap + the two export lines
uv run python scripts/bootstrap_kitaru.py --server <url>    # any other server (--dry-run prints the argv)
```

> ✅ It prints one row per resource (`kind · name@version · id`) and the `KITARU_AGENT_ID=<uuid>` to
> export. A second run prints the same table and registers nothing.

## 1. Log in

`kitaru` came with `make install`. The server is resolved as `--server` > `KITARU_API_URL` > your
`kitaru login` store, so an exported `KITARU_API_URL` wins over whatever you logged into last.

```bash
uv run kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # or: make kitaru-local
uv run kitaru status                                              # first check whenever anything looks off
```

Optional: drive the workspace from Claude Code / Cursor. `.mcp.json` in this repo already starts `kitaru-mcp` in `standard` mode; skills: `npx skills add zenml-io/kitaru-skills`.

## 2. Record sessions

Two variables switch recording on. `KITARU_API_URL` must be **exported**: decode never reads it, the kitaru adapter does.

```bash
export KITARU_API_URL=http://localhost:8000          # or the managed workspace URL
export KITARU_AGENT_ID=<uuid from `make kitaru-local` / `uv run kitaru agent get decode`>
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # node by node
```

> ✅ The session appears in the list, every model and tool call a node.

Since kitaru 0.24 `session list` returns no inputs/outputs — add `--include-payloads` when you want them (or read one session with `session get`).

REPL turns record too, grouped by decode session id. Remote runs on Modal record when the same keys ride the headless Secret ([04 §2](04_deploy.md#2-create-the-decode-headless-env-secret)). `KITARU_AGENT_ID` empty = no recording, no kitaru import. Unreachable workspace: a user-launched run continues on the bare agent with one `[kitaru] not recording this run` line and exits 0; a Worker-spawned run hard-fails instead.

Backfill sessions decode never recorded, straight from Opik traces — one command, no export dance
(the ids come from `uv run python -m evals mine`):

```bash
uv run python -m evals kitaru import <TRACE_ID> [<TRACE_ID>...]   # or: --thread <SESSION_ID>
```

It expands each trace to its whole thread (one decode session), writes the `opik` importer's own
envelope to `.decode/kitaru-imports/<thread>.json`, runs `kitaru session import … --wait`, and prints
`thread → session id (readiness)`. A thread the server already has is skipped. **A Worker (§4) must
be running** — the server executes nothing, so with no Worker claiming, the import waits and times
out. `--project` reads another Opik project; `--no-wait` files the job and returns.

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
uv run python scripts/bootstrap_kitaru.py --server $KITARU_API_URL   # registers every evaluators/*.py

# baseline sweep, no replay
uv run kitaru session evaluate --tag regression-case --evaluator 'my-check@1' --wait
```

A benchmark job's failures freeze themselves — the trials that scored 0, resolved to their recorded
Sessions by session id, added to the cohort:

```bash
uv run python -m evals kitaru cohort from-experiment <JOB NAME>   # --cohort decode-benchmark-failures
```

It refuses with one line when the experiment recorded nothing (`experiment_config.kitaru_agent_id`
is `None` — you ran the benchmark without `KITARU_AGENT_ID`) or when no Session resolves on this
server, and adds only what the cohort lacks (a cohort version is immutable).

Worked example in this repo: cohort + evaluator `decode-bad-request-400@1` ([`evaluators/decode_bad_request_400.py`](../evaluators/decode_bad_request_400.py)), flagging exactly the 2 crash sessions out of 38. Guided version: the `kitaru-investigation` skill.

## 4. Start a Worker on your laptop

A Worker claims replay / evaluator / importer tasks and spawns an **Agent Version**, the registered run spec: `decode run` with `SANDBOX_MODE=docker`, a fresh clone of this repo, Harness Home `~/.decode-kitaru-worker`. Register once per machine:

```bash
uv run python scripts/bootstrap_kitaru.py --server $KITARU_API_URL   # registers what is missing
uv run kitaru agent version list decode
```

One version is registered on a fresh server: `decode@1` = the laptop Worker (`SANDBOX_MODE=docker`).
Workers run only on your machine ([ADR-0023](../docs/adr/0023-kitaru-workers-run-locally.md)); a managed workspace stores the Sessions, it executes nothing.
A moved venv or an edited evaluator registers exactly one new version; everything else is left alone.

A replay inherits the Worker's env, nothing is stored on the workspace: start it from a shell that carries your provider keys and the same `LLM_PROVIDER` / model the sessions were recorded with.

```bash
cd <repo root>                       # pwd first: the checkout nests two same-named dirs
set -a && . ./.env && set +a
unset KITARU_AGENT_ID                # a Worker Task's token cannot use agent routes
uv run kitaru worker start --concurrency 10
```

> ✅ From another terminal, `uv run kitaru worker list` shows yours with `live: True`. A silent worker is a healthy worker. Watch a replay: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

## 5. Replay, then compare

A **baseline replay** (no override) is the control: it proves the Session reproduces on the current Agent Version, so a later what-if is attributable to the change. `decode@1` below is the **docker** version on a freshly bootstrapped server — the number is per-server, so check yours with `uv run kitaru agent version list decode` and pick the one whose env says `SANDBOX_MODE=docker`.

```bash
uv run kitaru replay create <SESSION_ID> --agent decode@1 \
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
  --cohort-version <ID> --agent decode@1 --baseline-evaluation-mode if-missing --wait   # non-zero on failure: a CI gate
```

Designing a what-if: the `kitaru-replay-experiment` skill.

## 6. Troubleshooting

| Symptom                                                      | Fix                                                                                                                                                                                                                |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `[kitaru] not recording this run: … is unavailable`          | `uv run kitaru status`; re-auth with `kitaru login <url>`; check `KITARU_AGENT_ID` is an agent on that workspace.                                                                                                  |
| Records nothing, says nothing                                | `KITARU_AGENT_ID` empty, or `KITARU_API_URL` in `.env` but not exported.                                                                                                                                           |
| Replay stays queued                                          | no live Worker (`kitaru worker list`), or the wrong Agent Version (`kitaru agent version list decode`: pick the one whose env says `SANDBOX_MODE=docker`).                                                        |
| `evals kitaru import` waits, then times out                  | no live Worker — an import is a job, and the server executes nothing. Start one (§4) and re-run.                                                                                                                   |
| `evals kitaru cohort`: "recorded no Kitaru Sessions"         | the benchmark ran without `KITARU_AGENT_ID` exported; re-run it with both variables exported.                                                                                                                      |
| `Decode: set GEMINI_API_KEY in your environment` in a replay | Worker shell had no provider key, or `.env` was sourced in the wrong directory. `pwd`, source, restart the Worker.                                                                                                 |
| Replay fails before the agent starts                         | Docker down, or stale command path after `make install`: re-run `scripts/bootstrap_kitaru.py`.                                                                                                                     |
| `403: Task credentials are not accepted on this route`       | `unset KITARU_AGENT_ID` in the Worker shell.                                                                                                                                                                       |
| `ModelHTTPError: 503` inside a replay                        | the pipe works; only the model was gone. `ModuleNotFoundError` / command not found = setup broken.                                                                                                                 |

---

**Done.** You have the full stack: a coding agent on your own model, sandboxed, deployed, traced, benchmarked, recorded, and replayed. Back to the [course README](../README.md) for the lessons.
