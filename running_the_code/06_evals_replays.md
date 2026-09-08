# Kitaru Evals & Replays — run it locally, from zero to a compared experiment

[05_evals.md](05_evals.md) grades the agent with benchmarks and probes you *drive*. This page is
the other half: **replay-based evals** on your *own* traffic, with
**[Kitaru](https://docs.zenml.io/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=docs)**
run entirely from your laptop — setting it up from scratch and walking the full evidence loop:
**record → investigate → cohort → evaluator → replay → compare**. Everything below was executed for
real against the managed workspace; the pitfalls at the end are the ones actually hit.

Budget: ~30 minutes, one provider key, Docker Desktop for the replay step. Moving the replay Worker
off your laptop is the next page, [07_evals_replays_deploy.md](07_evals_replays_deploy.md).

## 0. What you're building

Kitaru (0.22.x) is a replay-based eval framework: agent runs are recorded as **Sessions**
(every LLM + tool call as nodes), humans judge them in **Investigations**, judged sessions
freeze into **Cohorts**, **Evaluators** turn criteria into repeatable verdicts, and **Replays**
re-execute a session from the top on a **Kitaru Worker** in *your* environment — optionally with
one change (model, prompt, params) — so you can compare before/after with the same evaluator on
both sides. Nothing executes on the server ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

The **managed workspace** is `https://f5ee9622-kitaru.cloudinfra.zenml.io` — someone else's uptime,
free for the course, nothing to host. It holds the recorded Sessions, Cohorts, Replays and registered
Agent Versions; it executes nothing.

## 1. Set up the CLI and log in

The `kitaru` CLI ships with decode's deps (`kitaru[cli,mcp,worker]`), so `make install` already
gave it to you:

```bash
uv run kitaru login https://f5ee9622-kitaru.cloudinfra.zenml.io   # device flow in the browser
uv run kitaru status                                              # authenticated, compatible, dashboard URL
```

`kitaru status` is your first stop whenever anything looks off — it names the selected server,
credential state, and the dashboard URL.

CI / non-interactive machines use an API key instead of a login. On a **managed** workspace it must
be a control plane key (`ZENPROKEY_…`, minting walked through in
[07_evals_replays_deploy.md §2b](07_evals_replays_deploy.md#2b-mint-the-container-credential-a-zenprokey_)) —
a workspace-local `KITKEY_…` is rejected under control-plane authentication; that prefix works only
on self-hosted servers:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io
export KITARU_API_KEY=ZENPROKEY_...
```

## 2. Optional: wire your coding agent into Kitaru

The workspace is fully drivable from a coding agent (Claude Code, Cursor) via MCP + skills —
this is how the whole investigation flow in this repo was run:

```json
// .mcp.json (already in this repo)
{
  "mcpServers": {
    "kitaru": {
      "command": "uv",
      "args": ["run", "kitaru-mcp", "--server", "https://f5ee9622-kitaru.cloudinfra.zenml.io", "--mode", "standard"]
    }
  }
}
```

Modes: `read-only` → `standard` (create cohorts, investigations, runs) → `destructive`
(cancel/delete). The matching skills (`kitaru-investigation`, `kitaru-replay-experiment`,
`kitaru-importer-builder`) live under `.claude/skills/` — installed via
`npx skills add zenml-io/kitaru-skills`.

## 3. Get sessions in — record new, import old

**Record new runs** (the adapter path — full replay fidelity). Recording is presence-based and lives
in exactly one function, the **Recording Seam** (`src/decode/runtime/recording.py`). Two variables
switch it on:

```bash
export KITARU_API_URL=https://f5ee9622-kitaru.cloudinfra.zenml.io   # adapter-owned (or just `kitaru login`)
export KITARU_AGENT_ID=<uuid printed by `kitaru agent get decode`>  # decode's ONE recording knob
uv run decode run "say hi in exactly three words"
uv run kitaru session list --agent decode --origin recorded --size 3
uv run kitaru session get <SESSION_ID>                              # the run, node by node
```

- **Both surfaces record.** The REPL wraps the same way, with `session_name` = the decode session id,
  so a conversation's turns group together on the workspace. A remote run on Modal records too, when
  the same three keys ride its Secret ([04_deploy.md §2b](04_deploy.md#2b-the-decode-headless-secret)).
- **`KITARU_API_URL` must be *exported*, not merely written in `.env`.** decode never reads it: the
  adapter's own client resolves the connection (env, else your `kitaru login` store).
  `set -a && . .env && set +a` is the shortcut.
- **Off is byte-identical.** With `KITARU_AGENT_ID` empty, no kitaru module is even imported.
- **Unreachable workspace degrades, it never blocks.** A user-launched run drops to the bare agent,
  prints ONE stderr line (`[kitaru] not recording this run: … continuing on the bare agent`), and
  still exits 0 — recording is an observer, never an availability dependency. A run spawned by a
  Kitaru **Worker** hard-fails instead: an unrecorded replay is a lying experiment
  ([ADR-0019 §3](../docs/adr/0019-kitaru-replay-runtime.md)).
- **A Worker's hard-fail is ONE line too, wherever it happens.** The workspace can also refuse the
  Session the adapter creates lazily *inside* the run (a 403 on the agents route, a 422 for an
  unknown task, a typo'd `KITARU_TASK_ID`); `decode run` turns that into the same
  `Decode: [kitaru] recording is unavailable for this Kitaru Worker Task: …` line and a non-zero
  exit, with the traceback in `.decode/logs/decode.log` only. A 403 adds the `KITARU_AGENT_ID`
  diagnosis (§7.3). A failure the **agent** raised (a provider 503) is never reworded as a recording
  failure — the Worker log stays honest about which half broke.

**Import history** (the importer path — backfill from Opik). This repo registered a custom
importer `opik@1` (`importers/opik_importer.py`) that converts an Opik REST export
(`{workspace, project, traces: [{trace, spans}]}`) into joined multi-turn sessions:

```bash
uv run kitaru importer list                       # opik@1 among the kitaru/* built-ins
uv run kitaru session import export.json \
  --importer opik@1 --agent decode@1 \
  --media-type application/json \
  --tag opik-backfill --wait
```

Payloads cap at 50 MiB — split by *thread group* (a conversation must never straddle two files).
Imports are deduplicated on `provider + external_id`, so re-running the same export skips
instead of duplicating. An import is a job executed by a **worker** (§5) — start one first.

## 4. Judge, freeze, and encode a behavior

The evidence loop, driven from your coding agent (the `kitaru-investigation` skill walks it) or
by hand:

```bash
# pick a review worklist and open it in the browser UI
uv run kitaru investigation create my-discovery-1 --agent decode \
  --session <ID> --session-question '<ID>:observation=What do you notice? Did it match what should have happened?' \
  ...                                              # repeat per session; worklists are FIXED at creation
```

The create result carries a `links.review` URL — human verdicts (`acceptable` / `problematic`)
and free-text observations happen there, not in the terminal. When a behavior is accepted:

```bash
# freeze the reviewed positives into an immutable population
uv run kitaru cohort create decode-bad-request-400 --agent decode --session <ID> [...]

# encode the criterion as a versioned evaluator (deterministic Python, SessionView → EvaluationResult)
uv run kitaru evaluator scaffold my-check --path evaluators/my_check.py
uv run kitaru evaluator test evaluators/my_check.py --entrypoint evaluate
uv run kitaru evaluator register my-check --script evaluators/my_check.py --entrypoint evaluate

# baseline sweep: score every backfilled session, no replay involved
uv run kitaru session evaluate --tag opik-backfill --evaluator 'my-check@1' --wait
```

This repo's worked example: cohort `decode-bad-request-400@1` (a reviewed malformed-request
crash) + evaluator `decode-bad-request-400@1` (`evaluators/decode_bad_request_400.py`) — it
flagged exactly the 2 crash sessions out of 38, zero false positives.

## 5. Start a Worker on your laptop (the thing that executes replays)

Nothing runs on the server. A Worker is a process *you* run that claims replay / evaluator /
importer tasks and spawns them with your credentials. Replays run **from the top**: the Kitaru server
schedules, the Worker executes ([ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md)).

**What a Worker spawns is an Agent Version** — the immutable run spec registered on the workspace.
Register the laptop one once per machine (it adds a *version* to the existing `decode` agent, never a
second agent):

```bash
uv run python scripts/register_kitaru_agent.py --dry-run   # prints the exact `kitaru agent version register` argv
uv run python scripts/register_kitaru_agent.py             # registers agent version 2
uv run kitaru agent version list decode
```

**Agent version 2** re-creates decode's context rather than simulating it: `decode run` with no
inline prompt (the task arrives in `KITARU_TASK_INPUTS`), `SANDBOX_MODE=docker`, and a Workspace
that is a fresh clone of this repo. Its working dir is a **Harness Home outside the repo**
(`~/.decode-kitaru-worker`), so a replay's sessions, logs and Workspace never land in your working
tree — watch it work with `docker ps` and `tail -f ~/.decode-kitaru-worker/.decode/logs/decode.log`.
Docker Desktop must be up.

Then start the Worker from a shell that **has your provider credentials**:

```bash
cd <repo root>                       # careful: the checkout nests two same-named dirs
set -a && source .env && set +a      # provider keys must be IN the worker's env
unset KITARU_AGENT_ID                # see pitfall #3 below
uv run kitaru worker start --concurrency 10
```

The worker prints one `starting: {...}` line and then sits silent — that's it polling (2s
interval); output appears when it claims a task. Verify from another terminal:

```bash
uv run kitaru worker list            # yours, live: True
```

**A replay's secrets are the Worker's env, not the Environment Bucket.** Kitaru can attach secrets
to a registered Agent Version (`--secret-id …`); decode's versions deliberately attach **none**.
A Worker layers a task's env on top of its own, so the Worker shell's sourced `.env` is what a
replayed `decode run` sees, and no live key is ever copied onto the workspace
([ADR-0019 Amendments §2](../docs/adr/0019-kitaru-replay-runtime.md)). The Bucket
([01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional))
is *how `Settings` is filled* at a remote `DECODE_ENV`; neither feeds the other. Which model a
replay uses is therefore the Worker shell's `LLM_PROVIDER` / model config — a baseline replay
reproduces the recorded run only if you start the Worker with the same provider it recorded against.

**Or run the Worker on Modal instead of your laptop** — same Worker, a container instead of your
shell, replays keep going with the laptop closed: [07_evals_replays_deploy.md](07_evals_replays_deploy.md).
It spawns **agent version 3**, not 2, and the two Workers can only run their **own** version.

## 6. Replay, then compare

A **Baseline Replay** (no `--override`) is the control: it proves the Session still reproduces on
the current Agent Version, which is what makes a later what-if — a model swap, a system-prompt
change — attributable to the change and not to drift.

```bash
uv run kitaru replay create <BASELINE_SESSION_ID> \
  --agent decode@2 \
  --evaluator 'decode-bad-request-400@1' \
  --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' \
  --evaluate-baselines
uv run kitaru job watch <JOB_ID>     # from the create output; settles in seconds-to-minutes
uv run kitaru replay get <REPLAY_ID> # status + result_session_id
uv run kitaru session get <RESULT_SESSION_ID>   # the replayed run, node by node
```

- `--evaluator` is **required** — a replay without a metric is just a rerun.
- `--tool-policy history` answers tool calls from the recording; `on_miss: error_result` keeps a
  cache miss from executing live (`passthrough` would run real bash — never default to it).
- `--evaluate-baselines` scores the original too, giving the paired verdict.
- A what-if fork is one flag more: `--override '{"model": {"Qwen/...": "gemini-3.5-flash"}}'`.

**Compare in the UI** (dashboard from `kitaru status` → workspace → Agents → decode →
Sessions):

1. A replay session's page carries a `from <baseline>` **Compare** link — the canonical
   baseline-vs-replay diff (inputs, tool policy, outputs, per-step timeline).
2. Any two sessions: tick two checkboxes in the Sessions list → **Compare** in the bottom bar.
3. Direct URL: `<dashboard>/sessions/compare?left=<id>&right=<id>`.

Comparing two *replays* only means something when they share a baseline (baseline replay vs
fork). Different baselines = "two different sessions", and the page says so.

**At scale** — a whole cohort against one change, paired columns per evaluator:

```bash
uv run kitaru experiment create cheaper-model \
  --evaluator 'decode-bad-request-400@1' \
  --override '{"model": {"Qwen/Qwen3.6-35B-A3B-FP8": "gemini-3.5-flash"}}'
uv run kitaru experiment run start cheaper-model \
  --cohort-version <ID> --agent decode@2 --evaluate-baselines --wait
```

`--wait` exits non-zero on failure, so the same command is a CI gate. Designing and interpreting a
what-if is the `kitaru-replay-experiment` skill.

## 7. Field notes — the pitfalls we actually hit

1. **`.env` sourced in the wrong directory.** The checkout nests two directories with the same
   name; `set -a && . .env` in the outer one fails silently-ish and the worker starts keyless.
   Symptom: replay fails with `Decode: set GEMINI_API_KEY in your environment`. `pwd` first.
2. **Worker env ≠ your shell env.** Provider keys reach the replayed decode only through the
   worker's own environment (agent v2 ships `secret_ids: []` —
   [ADR-0019 Amendments §2](../docs/adr/0019-kitaru-replay-runtime.md)). Export keys *before*
   `kitaru worker start`, or attach version secrets for shared workers.
3. **`KITARU_AGENT_ID` must NOT be in the worker's env.** The worker injects a task-scoped
   token; with the agent id set, the Recording Seam probes an agents route that task tokens
   can't use → `403: Task credentials are not accepted on this route` → the run hard-fails
   (correctly), in ONE `Decode: [kitaru] …` line that now names this very trap. `unset
   KITARU_AGENT_ID` in the worker shell; the adapter infers the agent from the task. Same rule on
   Modal, enforced twice ([07_evals_replays_deploy.md §2c](07_evals_replays_deploy.md#2c-the-decode-kitaru-worker-secret)).
4. **`Invalid arguments: --evaluator requires an argument`** on `replay create` means the flag
   is *missing*, not empty — it's required.
5. **`replay create` has no `--wait`** — that's `session import` / `experiment run start`. Use
   `kitaru job watch <job-id>` instead.
6. **Import payload over 50 MiB** → split by thread group, one conversation per file, never
   across two.
7. **A silent worker is a healthy worker.** It polls quietly; check `kitaru worker list`, not
   the terminal.
8. **A failed replay with an *agent-level* error is still a working pipe.** `ModelHTTPError:
   503` from a downed provider means the worker claimed, spawned, and ran decode — only the
   model was gone. A spawn/import error (`ModuleNotFoundError`, command-not-found) is the one
   that means your setup is broken.

## 8. Troubleshooting

| Symptom | What it means |
|---|---|
| `[kitaru] not recording this run: … is unavailable` | The seam degraded: the workspace could not be reached (or `KITARU_AGENT_ID` is not an agent on it). The run itself is fine. Check `uv run kitaru status` — it prints the resolved `server_url` and whether the stored credential is still valid; re-auth with `kitaru login <url>`. |
| The run records nothing and says nothing | `KITARU_AGENT_ID` is empty, or `KITARU_API_URL` was set in `.env` but never exported — decode does not read that variable, the adapter's client does. |
| A replay stays queued | No Worker is claiming it: `kitaru worker list` should show one `live`. A laptop Worker only runs while its shell does. Also check the Agent Version: a v2 replay needs the laptop Worker, a v3 replay the Modal one ([07_evals_replays_deploy.md](07_evals_replays_deploy.md)). |
| A replay fails at the first model request | The Worker's shell had no provider credential (the run spec attaches none, by design), or that provider is down. Restart it with `set -a && . .env && set +a && kitaru worker start`. |
| A replay fails before the agent starts | Usually the docker daemon (agent v2 pins `SANDBOX_MODE=docker`) or a stale `--command` path after a fresh `make install`. Re-register: `uv run python scripts/register_kitaru_agent.py`. |
| `403: Task credentials are not accepted on this route` | `KITARU_AGENT_ID` in the Worker's env — `unset` it in the Worker shell (§7.3). |

## Go further

- [07_evals_replays_deploy.md](07_evals_replays_deploy.md) — the same Worker on Modal, so replays
  run with your laptop closed.
- [04_deploy.md](04_deploy.md) — the headless harness on Modal; its `decode-headless` Secret can
  carry the recording keys so remote runs land here as Sessions too.
- [01_install_and_usage.md §6](01_install_and_usage.md#6-environments--decode_env-and-the-environment-bucket-optional) — the Environment Bucket (vs a replay's secrets, §5).
- [ADR-0019](../docs/adr/0019-kitaru-replay-runtime.md) — why decode is shaped this way.
- Kitaru docs: https://docs.zenml.io/kitaru — concepts (sessions/replays/cohorts/experiments),
  tool policies, importers, workers in production.
