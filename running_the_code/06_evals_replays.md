# 06. Record and replay your evals with Kitaru

Replay-based evals on your own traffic with [Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs), all from your laptop. ~30 minutes; one provider key; Docker for the replay step.

Vocabulary: a run is recorded as a **Session**; humans judge Sessions in an **Investigation**; judged Sessions freeze into a **Cohort**; an **Evaluator** turns a criterion into a repeatable verdict; a **Replay** re-runs a Session from the top on a **Worker** you start, optionally with one change, so the same Evaluator scores before and after.

## 1. Set up

The `kitaru` Python package and CLI already come preconfigured within the `uv` virtual environment, when running `make install`.

But we still need to set up some skills, the server and the MCP server. Prerequisites:

- **Docker** — runs the local server (`make kitaru-local`) and the Worker (§2).
- **Node.js** — for `npx skills add` below.
- **One provider** in `.env`: a key (e.g. `GEMINI_API_KEY`, same as [01](01_install_and_usage.md)) or your own Modal endpoint (`LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL`, see [02](02_modal_endpoints.md)).
- **`OPIK_API_KEY`** + some existing decode traces — only for the Opik import in §3.

For all the details, you can check the full [Kitaru installation guide](https://docs.zenml.io/kitaru/getting-started/installation?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs) in their official docs.

### Skills

The Kitaru skills your coding agent uses below (`kitaru-investigation`, `kitaru-replay-experiment`, …) are not shipped in this repo — install the latest version from the official source:

```bash
npx skills add zenml-io/kitaru-skills
```

Or as a Claude Code plugin:

```bash
/plugin marketplace add zenml-io/kitaru-skills
/plugin install kitaru@kitaru
```

Already installed the plugin a while ago? `/plugin marketplace update kitaru` pulls the current skills.

These are the 3 skills we will need:

![](../assets/kitaru_skills.png)

### Pick a server

Choose between the local open-source server and the managed workspace. For educational purposes, running it locally is perfectly fine.

|                   | Local OSS server                                         | Managed workspace                                                                               |
| ----------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Account           | Not required                                             | Create a free account [here](https://cloud.zenml.io/signup?product=kitaru). 14 days free trial. |
| URL / dashboard   | `http://localhost:8000`                                  | Copy the URL from the managed workspace.                                                        |
| `KITARU_API_URL`  | `http://localhost:8000`                                  | `<managed_workspace_url>`                                                                       |
| Start/Connect     | `make kitaru-local` (runs docker compose under the hood) | `uv run kitaru login <managed_workspace_url>` (device flow in the browser)                      |
| Stop it           | `uv run kitaru logout`                                   | —                                                                                               |
| Where Workers run | your laptop                                              | your laptop — the workspace only stores the results                                             |

Both need `KITARU_API_URL` within your `.env` file.

Then register everything decode needs on that server:

```bash
make kitaru-local                                  # local: login --local + bootstrap + the two .env lines
make kitaru-bootstrap                              # server = KITARU_API_URL from your env / .env, else local
make kitaru-bootstrap KITARU_API_URL=<url>         # or name it explicitly; ARGS=--dry-run prints the argv
```

> ✅ It prints one row per resource (`kind · name@version · id`) and the `KITARU_AGENT_ID=<uuid>` to
> use in §4. A second run prints the same table and registers nothing.

### MCP Server

Already configured under `.mcp.json` — Claude Code picks it up from the repo root (approve it once when prompted). Other coding agents: copy the same entry into their MCP config.

Change the local URL from `"--server", "http://localhost:8000"` to other hosted version of Kitaru if required.

### Final check

A first check is running the status command to see more details about the current connection:

```bash
uv run kitaru status
```

Then run Kitaru's doctor command to see if everything is set up correctly:

```bash
uv run kitaru doctor
```

![kitaru doctor output](../assets/klitaru_doctor.png)

## 2. Start a Worker on your laptop

The server executes nothing ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)): every import (§3), evaluator run (§5) and replay (§6) is a job that waits until a **Worker** claims it. Start one now and leave it running in its own terminal.

The Worker spawns the **Agent Version** that §1's bootstrap registered: `decode run` with `SANDBOX_MODE=docker`, a fresh clone of this repo, Harness Home `~/.decode-kitaru-worker`. Check which version that is on your server:

```bash
uv run kitaru agent version list decode      # pick the one whose env says SANDBOX_MODE=docker (decode@1 on a fresh server)
```

Workers run only on your machine ([ADR-0023](../docs/adr/0023-kitaru-workers-run-locally.md)); a managed workspace stores the Sessions, it executes nothing. A moved venv or an edited evaluator registers exactly one new version on the next `make kitaru-bootstrap`; everything else is left alone.

The spawned `decode run` starts in `~/.decode-kitaru-worker`, where there is no `.env` — it inherits the **Worker's shell** instead. So start the Worker from a shell that carries your provider keys and the same `LLM_PROVIDER` / model the sessions were recorded with:

```bash
cd <repo root>                       # pwd first: the checkout nests two same-named dirs
set -a && . ./.env && set +a         # provider keys + KITARU_API_URL into this shell
unset KITARU_AGENT_ID                # a Worker Task's token cannot use agent routes
uv run kitaru worker start --concurrency 10
```

> ✅ From another terminal, `uv run kitaru worker list` shows yours with `live: True`. A silent worker is a healthy worker. Watch a job: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

## 3. Import traces from Opik into Kitaru

Populate Kitaru with Sessions from the decode runs you already traced in Opik. Needs `OPIK_API_KEY` in `.env` and the live Worker from §2.

### Find the ids

Kitaru imports a whole **thread** — one decode session — so you can hand it either id:

- **A decode session id** (= the Opik thread id) — every run you made writes
  `.decode/sessions/<timestamp>_<SESSION_ID>.jsonl`; the part after `_` is the id:

  ```bash
  ls -t .decode/sessions/ | head -5
  # 20260914T182638Z_089e454c-cb64-47b9-9cf3-f6cba35663d8.jsonl  →  089e454c-cb64-47b9-9cf3-f6cba35663d8
  ```

- **A trace id** — let the miner search your live Opik project and print the trace ids worth
  importing, grouped by failure signature:

  ```bash
  uv run python -m evals mine --preset all --limit 20     # errors | long | denied | all; --since 2026-09-01T00:00:00Z
  ```

- Or in the Opik UI: your project (`decode-<DECODE_ENV>`, e.g. `decode-local`) → **Traces** → the
  `ID` column (or **Threads** for the session id).

### Import

```bash
uv run python -m evals kitaru import <TRACE_ID> [<TRACE_ID>...]
uv run python -m evals kitaru import --thread <SESSION_ID>        # repeatable
```

It expands each trace to its whole thread, runs the `opik` importer registered in §1, and prints `thread → session id (readiness)`. A thread the server already has is skipped. With no live Worker it waits, then times out; `--no-wait` files the job and returns.

## 4. Record sessions in real time

Instead of importing after the fact, record every run as it happens. Two variables switch it on:

- `KITARU_API_URL` — which server (§1).
- `KITARU_AGENT_ID` — the UUID of the `decode` agent **on that server** (every server has its own).
  Empty = no recording.

Both are read from `.env` (an exported value wins); decode hands the URL to the kitaru adapter itself.

Get the id from the `make kitaru-local` / bootstrap output, or from `uv run kitaru agent get decode`
(the `ID` row). Add both to `.env`:

```text
KITARU_API_URL=http://localhost:8000
KITARU_AGENT_ID=<uuid from the bootstrap output>
```

Then record one run and look at it:

```bash
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # node by node
```

> ✅ The session appears in the list, every model and tool call a node.

## 5. Judge, freeze, encode

```bash
# review worklist; verdicts (acceptable / problematic) happen at the links.review URL it prints
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <ID> --session-question '<ID>:observation=What do you notice? Did it match what should have happened?'

# freeze the reviewed sessions into an immutable cohort
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <ID> [...]

# encode the criterion as a versioned evaluator (deterministic Python)
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
make kitaru-bootstrap                         # registers every evaluators/*.py

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

## 6. Replay, then compare

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

## 7. Troubleshooting

| Symptom                                                      | Fix                                                                                                                                                        |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[kitaru] not recording this run: … is unavailable`          | `uv run kitaru status`; re-auth with `kitaru login <url>`; check `KITARU_AGENT_ID` is an agent on that workspace.                                          |
| Records nothing, says nothing                                | `KITARU_AGENT_ID` or `KITARU_API_URL` missing from `.env` (and not exported).                                                                              |
| Replay stays queued                                          | no live Worker (`kitaru worker list`), or the wrong Agent Version (`kitaru agent version list decode`: pick the one whose env says `SANDBOX_MODE=docker`). |
| `evals kitaru import` waits, then times out                  | no live Worker — an import is a job, and the server executes nothing. Start one (§2) and re-run.                                                           |
| `evals kitaru cohort`: "recorded no Kitaru Sessions"         | the benchmark ran without `KITARU_AGENT_ID`; set both variables in `.env` and re-run it.                                                                   |
| `Decode: set GEMINI_API_KEY in your environment` in a replay | Worker shell had no provider key, or `.env` was sourced in the wrong directory. `pwd`, source, restart the Worker.                                         |
| Replay fails before the agent starts                         | Docker down, or stale command path after `make install`: re-run `make kitaru-bootstrap`.                                                                   |
| `403: Task credentials are not accepted on this route`       | `unset KITARU_AGENT_ID` in the Worker shell.                                                                                                               |
| `ModelHTTPError: 503` inside a replay                        | the pipe works; only the model was gone. `ModuleNotFoundError` / command not found = setup broken.                                                         |

---

**Done.** You have the full stack: a coding agent on your own model, sandboxed, deployed, traced, benchmarked, recorded, and replayed. Back to the [course README](../README.md) for the lessons.
