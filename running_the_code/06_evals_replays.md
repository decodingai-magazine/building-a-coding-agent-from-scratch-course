# 06. Record and replay your evals with Kitaru

Record decode runs as Kitaru Sessions, judge them, and replay them with one change, all from your laptop. This page is only the commands; the concepts are in the [Kitaru docs](https://docs.zenml.io/kitaru/core-concepts/concepts?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

## 1. Set up

Prerequisites:

- `make install` done ([01](01_install_and_usage.md)); it includes the `kitaru` CLI.
- **Docker** — runs the local server and the Worker.
- **Node.js** — for `npx skills add`.
- **One provider** in `.env`: a key (e.g. `GEMINI_API_KEY`, [01](01_install_and_usage.md)) or your Modal endpoint (`LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL`, [02](02_modal_endpoints.md)).
- **`OPIK_API_KEY`** + some traced decode runs ([05](05_evals.md)) — only for the import in §4.

Full install reference: [Kitaru installation guide](https://docs.zenml.io/kitaru/getting-started/installation?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

### Skills

Not shipped in this repo; install the latest:

```bash
npx skills add zenml-io/kitaru-skills
```

Or as a Claude Code plugin (`/plugin marketplace update kitaru` refreshes an old install):

```bash
/plugin marketplace add zenml-io/kitaru-skills
/plugin install kitaru@kitaru
```

The 3 skills we use:

![](../assets/kitaru_skills.png)

### Server

Pick one. For the course, the local server is enough.

|                  | Local OSS server                                | Managed workspace                                                              |
| ---------------- | ----------------------------------------------- | ------------------------------------------------------------------------------ |
| Account          | none                                            | [free trial](https://cloud.zenml.io/signup?product=kitaru), 14 days            |
| `KITARU_API_URL` | `http://localhost:8000`                         | `<managed_workspace_url>`, copied from the workspace                           |
| Connect          | `make kitaru-local` (docker compose + register) | `uv run kitaru login <url>`, then `make kitaru-bootstrap KITARU_API_URL=<url>` |
| Stop             | `uv run kitaru logout`                          | —                                                                              |
| Workers          | your laptop                                     | your laptop; the workspace only stores results                                 |

Both register the `decode` agent, the `opik` importer and `evaluators/*.py` on that server (idempotent; `ARGS=--dry-run` prints instead), then print the two lines for `.env`:

```text
KITARU_API_URL=http://localhost:8000
KITARU_AGENT_ID=<uuid from the output>
```

`KITARU_AGENT_ID` is per server. Re-run `make kitaru-bootstrap` when you switch servers, after `make install`, or after editing `evaluators/`.

### MCP server

`.mcp.json` at the repo root already points the `kitaru` MCP server at `http://localhost:8000`; Claude Code picks it up (approve it once). Managed workspace: change the `--server` value. Other coding agents: copy the entry into their MCP config ([Kitaru setup](https://docs.zenml.io/kitaru/getting-started/setup?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs)).

### Check

```bash
uv run kitaru status
uv run kitaru doctor
```

![kitaru doctor output](../assets/klitaru_doctor.png)

## 2. Record a session

With both `.env` lines from §1 set, every REPL turn and every `decode run` is recorded:

```bash
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # node by node
```

Nothing in the list = one of the two `.env` lines is missing (§8).

§5 needs 15-30 sessions with a mix of verdicts. Record them in one go (~10 minutes, 3 runs at a time):

```bash
make kitaru-seed                        # 30 runs: 14 good, 8 cut off (--max-requests 1), 8 crashed (bogus model / broken provider)
make kitaru-seed ARGS=--dry-run         # print the 30 commands instead
```

Prints one line per run with its decode session id. Costs model calls (the crashes are free); refuses to start without the `.env` lines.

## 3. Start a Worker

Imports (§4), replays (§5 to §7) and evaluator runs (§9) are jobs that wait for a Worker; the server executes nothing. Start one in its own terminal and leave it running.

The Worker spawns `decode run` under `SANDBOX_MODE=docker` over a fresh clone of this repo, from `~/.decode-kitaru-worker`, where there is no `.env`. So its shell must carry your provider keys:

```bash
cd <repo root>
set -a && . ./.env && set +a
unset KITARU_AGENT_ID                # a Worker Task's token cannot use agent routes
uv run kitaru worker start --concurrency 10
```

Check from another terminal: `uv run kitaru worker list` shows it with `live: True`. Watch a job: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

## 4. Import traces from Opik

Backfill Sessions from decode runs you already traced in Opik. Needs `OPIK_API_KEY` in `.env` and the Worker from §3.

Find the ids. An import takes a whole thread (one decode session), so either id works:

- **Decode session id** (= Opik thread id) — the part after `_` in the session log filename:

  ```bash
  ls -t .decode/sessions/ | head -5
  # 20260914T182638Z_089e454c-cb64-47b9-9cf3-f6cba35663d8.jsonl  →  089e454c-cb64-47b9-9cf3-f6cba35663d8
  ```

- **Trace id** — the miner prints the ones worth importing, grouped by failure:

  ```bash
  uv run python -m evals mine --preset all --limit 20     # errors | long | denied | all; --since 2026-09-01T00:00:00Z
  ```

- Opik UI: project `decode-<DECODE_ENV>` (e.g. `decode-local`) → **Traces** (`ID` column) or **Threads**.

Import:

```bash
uv run python -m evals kitaru import <TRACE_ID> [<TRACE_ID>...]
uv run python -m evals kitaru import --thread <SESSION_ID>        # repeatable
```

Prints `thread → session id`. Already-imported threads are skipped. `--no-wait` files the job and returns.

## 5. Harvest failures into a regression suite

Sample sessions (`make kitaru-seed` from §2 if you have none) into an investigation, give each a verdict, freeze the `problematic` ones into a cohort, then gate on it. Reference: [regression suite](https://docs.zenml.io/kitaru/guides/regression-suite?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

```bash
# 1. investigation over 15-30 sessions (one --session per id, a keyed question per session)
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <SESSION_ID> --session-question '<SESSION_ID>:observation=What do you notice? Did it match what should have happened?'

# 2. verdict per session: acceptable | problematic | uncertain (CLI, or the links.review URL it prints)
uv run kitaru investigation session verdict <INVESTIGATION_ID> <SESSION_ID> problematic
uv run kitaru investigation session list <INVESTIGATION_ID>

# 3. freeze the problematic sessions into an immutable cohort version
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <SESSION_ID> [--session ...]
uv run kitaru cohort version get decode-bad-request-400@1        # its id is <COHORT_VERSION_ID> below
```

`cohort create` also takes `--tag regression-case` (everything §4 imported) instead of `--session`. A benchmark job's failures ([05](05_evals.md)) freeze themselves:

```bash
uv run python -m evals kitaru cohort from-experiment <JOB_NAME>   # --cohort decode-benchmark-failures
```

It refuses with one line when the benchmark ran without `KITARU_AGENT_ID` or when no Session resolves on this server.

The gate: replay the cohort on the current Agent Version (`decode@1`, see §6) and score it. `--wait` exits non-zero on failure, so the same command is the CI step.

```bash
uv run kitaru experiment create decode-regression --agent decode \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"fail"}}'
uv run kitaru experiment run start decode-regression \
  --cohort-version <COHORT_VERSION_ID> --agent decode@1 --wait --timeout 1800
```

`decode-bad-request-400@1` is the evaluator shipped in this repo ([`evaluators/decode_bad_request_400.py`](../evaluators/decode_bad_request_400.py)), registered by the bootstrap. Your own: §9. Guided: the `kitaru-investigation` skill.

## 6. Replay safely: tool policies

A replay re-runs `decode run` from the top; the tool policy decides what each tool call (bash, file writes) gets instead of running again. Reference: [tool policies](https://docs.zenml.io/kitaru/guides/tool-policies?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

| Key       | Values                                       | Meaning                                                                                            |
| --------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `type`    | `history` / `static` / `passthrough` / `llm` | answer from the recording / a fixed table / run the tool for real / let a model invent the result  |
| `on_miss` | `fail` / `error_result` / `passthrough`      | no recorded answer: stop the replay / hand the model an error and continue / run the tool for real |
| `scope`   | `baseline` / `cohort_version` / `agent`      | which recordings `history` may answer from                                                         |

Default to `history` + `on_miss: fail`: a call the recording cannot answer stops the replay instead of inventing the rest. `passthrough` runs real bash (inside the Worker's docker Workspace, but live); never make it the default. Per tool: `"tools": {"web_fetch": {"type": "passthrough"}}` next to `"default"`.

One baseline replay (no override) is the control. Pick the Agent Version whose env says `SANDBOX_MODE=docker` (`decode@1` on a fresh server):

```bash
uv run kitaru agent version list decode
uv run kitaru replay create <SESSION_ID> --agent decode@1 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"fail"}}' \
  --baseline-evaluation-mode if-missing
uv run kitaru job watch <JOB_ID>          # from the create output
uv run kitaru replay get <REPLAY_ID>      # status + result_session_id
```

Compare: dashboard (URL from `kitaru status`) → Agents → decode → Sessions → the replay's **Compare** link, or tick any two sessions → Compare.

## 7. Model migration

Same cohort, one change: `--override` maps each recorded model to its replacement (other keys: `system_prompt`, `prompt`, `model_params`). Reference: [replay and overrides](https://docs.zenml.io/kitaru/guides/replay-and-overrides?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

```bash
uv run kitaru experiment create cheaper-model --agent decode \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"fail"}}' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'
uv run kitaru experiment run start cheaper-model \
  --cohort-version <COHORT_VERSION_ID> --agent decode@1 --wait --timeout 1800
uv run kitaru experiment run get <RUN_ID>          # pass / fail counts
uv run kitaru session get <RESULT_SESSION_ID>      # cost + tokens of one replay; the baseline's for the delta
```

Every session carries cost + tokens, so the answer is a pass-rate delta and a cost delta. Guided: the `kitaru-replay-experiment` skill.

## 8. Debug a replay

```bash
uv run kitaru job watch <JOB_ID>
uv run kitaru replay get <REPLAY_ID>                       # status, error, result_session_id
uv run kitaru session get <RESULT_SESSION_ID>              # the failed node
tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log    # the spawned decode run
docker ps                                                  # its Workspace container
```

| Symptom                                                      | Fix                                                                                                                                               |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[kitaru] not recording this run: … is unavailable`          | `uv run kitaru status`; re-auth with `uv run kitaru login <url>`; check `KITARU_AGENT_ID` is the `decode` agent on **that** server.               |
| Records nothing, says nothing                                | `KITARU_AGENT_ID` or `KITARU_API_URL` missing from `.env`. An exported `KITARU_API_URL` wins over `.env`: `unset` it if it names another server.  |
| Replay stays queued                                          | no live Worker (`uv run kitaru worker list`), or the wrong Agent Version (`uv run kitaru agent version list decode`, pick `SANDBOX_MODE=docker`). |
| `evals kitaru import` waits, then times out                  | no live Worker. Start one (§3) and re-run.                                                                                                        |
| `evals kitaru cohort`: "recorded no Kitaru Sessions"         | the benchmark ran without `KITARU_AGENT_ID`; set both `.env` lines and re-run it.                                                                 |
| Replay stops on a tool call                                  | `on_miss: fail` did its job: the run diverged from the recording before that call. Read the result session up to it.                              |
| `Decode: set GEMINI_API_KEY in your environment` in a replay | the Worker shell had no provider key, or `.env` was sourced from the wrong directory. `pwd`, source, restart the Worker.                          |
| Replay fails before the agent starts                         | Docker down, or a stale command path after `make install`: re-run `make kitaru-bootstrap`.                                                        |
| `403: Task credentials are not accepted on this route`       | `unset KITARU_AGENT_ID` in the Worker shell.                                                                                                      |
| `ModelHTTPError: 503` inside a replay                        | the pipe works; only the model was gone. `ModuleNotFoundError` / command not found = setup broken.                                                |

More: [Kitaru troubleshooting](https://docs.zenml.io/kitaru/get-help/troubleshooting?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

## 9. Write your own evaluator

Deterministic Python over a session's nodes; one file under `evaluators/`, registered by the bootstrap. Reference: [write an evaluator](https://docs.zenml.io/kitaru/guides/write-an-evaluator?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

```bash
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
make kitaru-bootstrap                                                 # registers evaluators/*.py as my-check@1
uv run kitaru session evaluate --tag regression-case --evaluator 'my-check@1' --wait   # baseline sweep, no replay
```

Then swap it into §5's gate (`--evaluator 'my-check@1'`; repeat the flag for several).

---

**Done.** You have the full stack: a coding agent on your own model, sandboxed, deployed, traced, benchmarked, recorded, and replayed. Back to the [course README](../README.md) for the lessons.
