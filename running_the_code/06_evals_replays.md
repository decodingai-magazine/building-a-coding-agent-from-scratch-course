# 06. Record and replay your evals with Kitaru

Record decode runs as [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) Sessions, judge them, and replay them with one change, all from your laptop. This page contains only the commands, the concepts are in the [Kitaru docs](https://docs.zenml.io/kitaru/core-concepts/concepts?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

## 1. Set up

Prerequisites:

- running `make install` already includes the `kitaru` CLI within the `uv` virtual environment.
- **Docker** — runs the local server and the Worker.
- **Node.js** — for `npx skills add`.
- **`jq`** (`brew install jq`) — the commands below capture every id into a shell variable, so run each section in one terminal.
- **One provider** in `.env`: a key (e.g. `GEMINI_API_KEY`, more in [01_install_and_usage](01_install_and_usage.md)) or your Modal endpoint (`LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL`, more in [02_modal_endpoints](02_modal_endpoints.md)).
- **`OPIK_API_KEY`** optional step for importing traces from Opik. Full setup in [05_evals](05_evals.md)

See [01_install_and_usage](01_install_and_usage.md) and [02_modal_endpoints](02_modal_endpoints.md) for the full setup of the coding agent, here we will focus only on the setup of the Kitaru eval harness.

Full install reference: [Kitaru installation guide](https://docs.zenml.io/kitaru/getting-started/installation?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

### Install the Skills

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

### Pick a server

Pick one. For the course, the local server is enough.

|                                | Local OSS server                               | Managed workspace                                                                                                                                               |
| ------------------------------ | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Account                        | none                                           | [free trial](https://cloud.zenml.io/signup?product=kitaru&utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs), 14 days |
| `KITARU_API_URL` (from `.env`) | `http://localhost:8000`                        | `<managed_workspace_url>`, copied from the workspace                                                                                                            |
| Dashboard                      | `http://localhost:8000`                        | [Kitaru dashboard](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand)            |
| Connect                        | run `make kitaru-local` (using docker compose) | run `uv run kitaru login <url>`, then `make kitaru-bootstrap KITARU_API_URL=<url>`                                                                              |
| Stop                           | run `uv run kitaru logout`                     | —                                                                                                                                                               |
| Workers                        | your laptop                                    | your laptop; the workspace only stores results                                                                                                                  |

Both commands from `Connect` register the `decode` agent, the `opik` importer and `evaluators/*.py` on that Kitaru server.

After running `make kitaru-local` or `make kitaru-bootstrap KITARU_API_URL=<url>`, at the bottom you will see the value of the `KITARU_API_URL` and `KITARU_AGENT_ID` env vars.

Take them and add them to your `.env` file:

```text
KITARU_API_URL=http://localhost:8000
KITARU_AGENT_ID=<uuid from the output>
```

`KITARU_AGENT_ID` is per server. Re-run `make kitaru-bootstrap` when you switch servers, after `make install`, after editing `evaluators/` or after doing any change to your code in general.

> [!NOTE]
> As Kitaru is also [open-source](https://github.com/zenml-io/kitaru), there is also a 3rd option, of hosting the server yourself.

### MCP server

We already have setup at the repo root a `.mcp.json` file that points at the local Kitaru MCP server at `http://localhost:8000`.

- Claude Code picks it up automatically (approve it once).
- Managed workspace: change the `--server` value from `.mcp.json`.
- Other coding agents: copy the entry into their MCP config ([see full Kitaru setup](https://docs.zenml.io/kitaru/getting-started/setup?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs)).

### Check

Details about the Kitaru setup.

```bash
uv run kitaru status
```

Detailed doctor output:

```
uv run kitaru doctor
```

![kitaru doctor output](../assets/klitaru_doctor.png)

## 2. Record a session

With both `.env` lines from §1 set, every REPL turn and every `decode run` is recorded:

```bash
uv run decode run "say hi in exactly three words"
```

The run is now a Session:

```bash
uv run kitaru session list --agent decode --origin recorded --size 3
```

Keep the newest id and open it node by node:

```bash
export SESSION_ID=$(uv run kitaru session list --agent decode --origin recorded --size 1 -o json | jq -r '.items[0].id')
uv run kitaru session get "$SESSION_ID"
```

Nothing in the list = one of the two `.env` lines is missing from the setup steps.

You can also visualize the sessions in the Kitaru dashboard, locally at [http://localhost:8000](http://localhost:8000) or in the managed version at [https://app.kitaru.ai](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand).

## 3. Seed multiple sessions

> [!NOTE]
> A session is equal to an Opik thread.

To illustrate a real example we need a mix of 15-30 sessions. You can either run `decode run "<goal>"` yourself on ~20-30 examples or use `make kitaru-seed` to generate 30 examples (takes ~10 minutes):

```bash
make kitaru-seed
```

It generates 30 runs: 14 good, 8 cut off (--max-requests 1), 8 crashed (bogus model / broken provider).

![](../assets/kitaru_sessions.png)

Or see what it would run first:

```bash
make kitaru-seed ARGS=--dry-run
```

## 4. Start a Worker

Imports, replays and evaluator runs are jobs that wait for a Worker to execute them. The Kitaru server executes nothing. Start one in your own terminal and leave it running.

> [!NOTE]
> When deploying Kitaru, you need to deploy the Worker separately on your infrastructure, such as Modal.

The Worker spawns `decode run` under `SANDBOX_MODE=docker` over a fresh clone of this repo, from `~/.decode-kitaru-worker`, where there is no `.env`. So its shell must carry your provider keys:

```bash
cd <repo root>          # the building-a-coding-agent-from-scratch-course root directory
set -a && . ./.env && set +a
unset KITARU_AGENT_ID   # a Worker doesn't need to be aware about the Kitaru agent it points to
```

Start it and leave this terminal running:

```bash
uv run kitaru worker start --concurrency 1 --timeout 28800
```

`--timeout` (8 h here) is the Worker's lifetime, and its token lives that long plus 5 minutes. Without it the token dies after 1 hour and the Worker stops claiming work (see [Troubleshooting](#troubleshooting)).

`--concurrency 1` runs one replay at a time. Every replay of the `decode` Agent Version shares ONE Workspace (`~/.decode-kitaru-worker/.decode/sandbox`), so parallel replays race on its `git clone` and fail before the agent starts. It also means a replay reuses the Workspace the previous one left behind, which is harmless for read-only tasks.

Check from another terminal: `uv run kitaru worker list` shows it with `Status: live`. Watch a job: `docker ps`, `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.

## 5. Import traces from Opik

Optional step to backfill Sessions from decode runs you already traced in Opik. Needs `OPIK_API_KEY` in `.env` and the Worker from step §3.

As you already seeded your Kitaru instance with decode runs at step 3, you can pass this step, but importing more traces from Opik would add more variety to your dataset.

Pull the newest threads (one thread = one decode session = one Kitaru Session) from the Opik project `decode-<DECODE_ENV>` (e.g. `decode-local`):

```bash
uv run python -m evals kitaru import --limit 20
```

Or every thread in the project:

```bash
uv run python -m evals kitaru import
```

Prints `thread → session id`. Already-imported threads are skipped, so re-running is cheap. `--project <NAME>` reads another project; `--no-wait` files the jobs and returns.

## 6. Harvest failures into a regression suite

Now that we have our sessions prepared (via seed, Opik or both), we can sample them into an investigation, give each a verdict, freeze the `problematic` ones into a cohort, and create an evaluator per error type. Reference: [regression suite](https://docs.zenml.io/kitaru/guides/regression-suite?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

Sample 20 of the newest 30 sessions, replays left out:

```bash
SESSION_IDS=($(uv run kitaru session list --agent decode --size 60 -o json \
  | jq -r '[.items[] | select(.origin != "replay")][:30][].id' | sort -R | head -20))
```

Number of sessions:

```bash
echo "${#SESSION_IDS[@]}"
```

Every session needs its own question, so build the flags:

```bash
ARGS=(); for id in "${SESSION_IDS[@]}"; do
  ARGS+=(--session "$id" --session-question "$id:observation=What do you notice? Did it match what should have happened?")
done
```

Create the investigation and keep its id:

```bash
INVESTIGATION=$(uv run kitaru investigation create my-discovery-1 --agent decode "${ARGS[@]}" -o json)
export INVESTIGATION_ID=$(jq -r '.item.id' <<<"$INVESTIGATION")
```

Judge each session `acceptable` / `problematic` / `uncertain` in the review UI. Paste the command below to see the link towards the investigation review UI, open it, go over the sessions and assign a label and an optional critique. After return to the terminal, and continue this tutorial.

```bash
jq -r '.links.review' <<<"$INVESTIGATION"
```

This is how the investigation review UI looks like:

![](../assets/kitaru_investigation.png)

After finishing the review in the UI list all the labels/verdicts per sessions of the investigation:

```bash
uv run kitaru investigation session list "$INVESTIGATION_ID" --size 100 -o json | jq -r '.items[] | "\(.session_id)  \(.verdict)"'
```

![](../assets/kitaru_list_investigations.png)

Also, you will get to copy-paste a prompt like this:

```text
Investigation complete: my-discovery-1. 20 of 20 sessions reviewed, agent: decode.
Reviewed 23 September 2026.

Verdicts: 6 acceptable, 14 problematic, 0 uncertain.

Choose what to do next:
1. Build a cohort from a behavior found in these reviewed sessions.
2. Investigate the 14 problematic sessions in more detail.
```

Which you need to paste into your agent open within this project that has access to the skills that explain in detail to the agent how to operate Kitaru and MCP server that allows the agent to communicate with the Kitaru platform. I used Opus 5.5 for my tests.

![](../assets/kitaru_investigating.png)

If running based on our seed, it will find two cohort candidates by clustering the sessions per error type.

1. **Candidate 1:** a run that hits the request limit gives the user nothing. A decode run stopped by the request limit ends with a raw UsageLimitExceeded, no output, and no partial answer or explanation.
2. **Candidate 2:** connection error (I suggest rejecting it as agent behavior)

Next, you need to pass the following prompt to create a cohort per each candidate, that will help us understand the behavior in more depth and create an evaluator that can detect similar issues in the future:

```text
Create a cohort for each candidate!
```

Which will create two cohorts within Kitaru, one for each candidate:

![](../assets/kitaru_cohorts.png)

> [!WARNING]
> These are the results based on the synthetic seed. What you are importing from Opik or different tests might alter the results.

Now, we need to either apply an existing evaluator or create a new one per cohort. As we already have the evaluators for these two, we prompt:

```text
Now, apply the two evaluators existing evaluators per cohort:
  - cohort decode-connection-error -> evaluator: decode_connection_error.py
  - cohort decode-request-limit -> evaluator: decode_request_limit.py
```

If you would have a cohort with a new failure class, you would need to create a new evaluator, which is easy to do as you have all the logs and erros within the cohort.

![](../assets/kitaru_session_error.png.png)

After running the prompt above, the evaluator will be ran on each session in the cohort and the results will be displayed as follows:

![](../assets/kitaru_cohort_evaluator_result.png)

It's normal for all of them to fail, because each session still contains the error that caused the failure.

So what we have to do now, is to fix the code that caused the failure and re-run the cohort as an experiment. So, let's assume that we fixed our connection error. As it was artificially injected we know reruning will work, but in a real-world scenario you would need to fix the code first. So we prompt the agent within the same session:

```text
For cohort `decode-connection-error` we fixed the connection error. Start an experiment based on the seesions from the cohort, rerun the evaluator and see if the failure class has been resolved.
```

Behind the scenes, Kitaru will take all the sessions from the cohort and leverage it's replays features to rerun each session on decode on the worker we started at step 4. It will run an isolated instance of decode based on each session, while having all the tool outputs cached to replay the same scenario.

Here are the experiments attached to the `decode-connection-error` cohort:

![](../assets/cohort_experiments_list.png)

And within the second experiment, after fixing the error, we can see that the failure class has been resolved on all the sessions:

![](../assets/kitaru_experiment_result.png)

And that's it. Now we have an evaluator that can always detect these type of errors.

The next steps are to repeat the same process on the other cohorts and expand your evaluator to cover all the failure classes.

## 7. More about replays

A replay re-runs `decode run` from the top. The tool policy decides if a tool call (bash, file writes) is using it's cache, fails if it doesn't exist or reruns. Reference: [tool policies](https://docs.zenml.io/kitaru/guides/tool-policies?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

These policies are extremely important when running an experiment that contains replays.

Here are all the options:

| Key       | Values                                       | Meaning                                                                                            |
| --------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `type`    | `history` / `static` / `passthrough` / `llm` | answer from the recording / a fixed table / run the tool for real / let a model invent the result  |
| `on_miss` | `fail` / `error_result` / `passthrough`      | no recorded answer: stop the replay / hand the model an error and continue / run the tool for real |
| `scope`   | `baseline` / `cohort_version` / `agent`      | which recordings `history` may answer from                                                         |

Pick `on_miss` by how complete the recordings are:

- **Baselines that ran to completion**: `on_miss: fail`. A call the recording cannot answer stops the replay instead of inventing the rest.
- **Baselines that were cut off or crashed** (the §5 cohorts): `on_miss: passthrough`. Their recording ends after a call or two, so `fail` stops every replay with `ToolPolicyMissError: No history result for tool '<name>'` before the evaluator ever runs. `passthrough` runs the tool for real, inside the Worker's docker Workspace but live.

Per tool: `"tools": {"web_fetch": {"type": "passthrough"}}` next to `"default"`.

You can start a replay only at the session level:

Get the session Id of the most recent recorded session:

```bash
export SESSION_ID=$(uv run kitaru session list --agent decode --origin recorded --size 1 -o json | jq -r '.items[0].id')
```

The start the replay:

```bash
REPLAY=$(uv run kitaru replay create "$SESSION_ID" --agent decode@1 \
  --evaluator 'decode-request-limit@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"fail"}}' \
  --baseline-evaluation-mode if-missing -o json)
export REPLAY_ID=$(jq -r '.item.id' <<<"$REPLAY") JOB_ID=$(jq -r '.item.job_id' <<<"$REPLAY")
```

Wait for the Worker to execute it, then read the session it produced:

```bash
export RESULT_SESSION_ID=$(uv run kitaru replay get "$REPLAY_ID" -o json | jq -r '.item.result_session_id')
```

You can see the replay within the sessions tab:

![](../assets/kitaru_replay_sessions.png)

Or you can also compare from the dashboard the replay with the baseline session (by selecting the two sessions and clicking **Compare**):

![](../assets/kitaru_compare_baseline_replay.png)

## 8. Model migration

Now what if we want to do a change to our code and we want to see how it affects the harnsess's performance? Let's take changing the model from `Qwen/Qwen3.6-35B-A3B-FP8` to `gemini-3.8-flash` as an example.

First we will create an experiment with the new model (this doesn't run the experiment yet!):

```bash
uv run kitaru experiment create change-to-new-model --agent decode \
  --evaluator 'decode-connection-error@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"passthrough"}}' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.8-flash"}}'
```

The `--override` parameter maps each recorded model to its replacement (other keys: `system_prompt`, `prompt`, `model_params`). Reference: [replay and overrides](https://docs.zenml.io/kitaru/guides/replay-and-overrides?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

![](../assets/kitaru_change_model_experiment.png)

First, let's take the `COHORT_VERSION_ID` from our baseline run:

```bash
export COHORT_VERSION_ID=$(uv run kitaru cohort version get decode-connection-error@1 -o json | jq -r '.item.id')
echo "$COHORT_VERSION_ID"
```

Then replay the same cohort on the new model (running on the worker):

```bash
uv run kitaru experiment run start change-to-new-model \
  --cohort-version "$COHORT_VERSION_ID" --agent decode@1 --wait --timeout 1800
```

It starts to run:

![](../assets/kitaru_change_model_experiment_running.png)

And then we can see the results, more exactly that the evaluator still passes all the sessions on the new model:

![](../assets/kitaru_change_model_experiment_done.png)

Also check the pass / fail counts for that experiment run:

```bash
export RUN_ID=$(uv run kitaru experiment run list --size 1 --sort created:desc -o json | jq -r '.items[0].id')
uv run kitaru experiment run get "$RUN_ID"
```

## 9. Write your own evaluator

You can easily scaffold a new deterministic Python evaluator under `evaluators/` by running:

```bash
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
```

Edit the file, then check that it loads:

```bash
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
```

Register it as `my-check@1`:

```bash
make kitaru-bootstrap
```

Score sessions with it, no replay involved:

```bash
uv run kitaru session evaluate --tag regression-case --evaluator 'my-check@1' --wait
```

For more details, check out [Kitaru's docs](https://docs.zenml.io/kitaru/guides/write-an-evaluator?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).

## Troubleshooting

### Debug a replay

```bash
uv run kitaru job watch "$JOB_ID"                          # ids from §6
uv run kitaru replay get "$REPLAY_ID"                      # status, error, result_session_id
uv run kitaru session get "$RESULT_SESSION_ID"             # the failed node
tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log    # the spawned decode run
docker ps                                                  # its Workspace container
```

| Symptom                                                                                                          | Fix                                                                                                                                                                                                                        |
| ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[kitaru] not recording this run: … is unavailable`                                                              | `uv run kitaru status`; re-auth with `uv run kitaru login <url>`; check `KITARU_AGENT_ID` is the `decode` agent on **that** server.                                                                                        |
| Records nothing, says nothing                                                                                    | `KITARU_AGENT_ID` or `KITARU_API_URL` missing from `.env`. An exported `KITARU_API_URL` wins over `.env`: `unset` it if it names another server.                                                                           |
| Replay / import stays queued, or `evals kitaru import` times out                                                 | no live Worker: start one (§4) and re-run. For replays, also check the Agent Version (`uv run kitaru agent version list decode`, pick `SANDBOX_MODE=docker`).                                                              |
| `ToolPolicyMissError: No history result for tool '…'`                                                            | `on_miss: fail` did its job: the replay went past what the recording holds. Expected on cut-off or crashed baselines — use `on_miss: passthrough` (§6).                                                                    |
| `Experiment run … settled as failed`                                                                             | a replay job crashed, which is not an evaluator verdict: `uv run kitaru experiment run jobs "$RUN_ID"` shows the real error.                                                                                               |
| `could not clone … into the Workspace` … `File exists` / `No such file or directory`                             | parallel replays raced on the shared Workspace. Restart the Worker with `--concurrency 1` (§4) and start a new run.                                                                                                        |
| `Decode: set <PROVIDER>_API_KEY in your environment` (or a connection error) in a replay                         | the Worker shell has no provider key / endpoint, or `.env` was sourced from the wrong directory. `pwd`, source, restart the Worker.                                                                                        |
| Replay fails before the agent starts                                                                             | Docker down, or a stale command path after `make install`: re-run `make kitaru-bootstrap`.                                                                                                                                 |
| `403: A worker credential is required on this route` (or `403: Task credentials are not accepted on this route`) | `unset KITARU_AGENT_ID` in the Worker shell (`set -a && . ./.env` sets it), then restart it. Starts ~1 h after launch? See [below](#worker-log-fills-with-403-a-worker-credential-is-required-on-this-route-after-1-hour). |

### Worker log fills with `403: A worker credential is required on this route` after ~1 hour

```text
httpx INFO HTTP Request: POST http://localhost:8000/api/v1/tasks/claim "HTTP/1.1 200 OK"
httpx INFO HTTP Request: POST http://localhost:8000/api/v1/tasks/claim "HTTP/1.1 403 Forbidden"
kitaru.worker.worker WARNING Failed to claim tasks: 403: A worker credential is required on this route.
```

The Worker claimed fine, then every claim fails, retried at a growing interval (2 s … 60 s) forever. Jobs you queue stay `pending`.

**Cause** (kitaru 0.27.0): a Worker started without `--timeout` gets a token valid for 1 hour (`WORKER_TOKEN_LIFETIME_SECONDS = 3600`). The local server runs with no auth, so it answers an expired Worker token by treating the request as the default account, not with `401`. The claim route accepts Workers only, so it returns `403`. The Worker renews its token only on `401`, so it never recovers.

**Fix:** Ctrl-C the Worker and start it again, this time with a lifetime. The token then lasts `--timeout` + 5 minutes:

```bash
uv run kitaru worker start --concurrency 1 --timeout 28800
```

If the `403` shows up right at startup, the cause is different: `KITARU_AGENT_ID` is set in the Worker shell (see the table above).

More: [Kitaru troubleshooting](https://docs.zenml.io/kitaru/get-help/troubleshooting?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs).
