# 05 — Evals with Opik

Tracing shows what happened; it cannot say whether a change made the agent better or worse. The eval suite in [`evals/`](../evals/) ([ADR-0017](../docs/adr/0017-decode-eval-suite.md), rebuilt by [ADR-0022](../docs/adr/0022-evals-v2-own-harbor-on-opik.md)) does, on one shared [Opik](https://www.comet.com/site/?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) harness.

Needs `OPIK_API_KEY` ([01 §2](01_install_and_usage.md#2-add-a-key)) plus your provider key; Docker for the benchmark. Runs cost money and are never in `make ci`. Missing a key: one line, exit 0. Benchmark and regression runs log under `EVAL_PROJECT_NAME` (`decode-evals`), apart from the live project; the online track deliberately grades the live one.

| Track | Answers | Graded by | Run |
|---|---|---|---|
| Demo Skills | does it impress? | you | `/demo-N-...` in the REPL |
| Benchmark | does it work? | Terminal-Bench-style tasks run through `decode run`, graded host-side by `tests/test.sh` → `reward.txt` | `make eval-benchmark` |
| Regression Cases | does it work the way we designed? | code metrics + threshold gate (and the same cases judged as English assertions in an Opik Test Suite) | `make eval-regression` · `python -m evals suite` |
| Online eval | is live traffic still good? | an LLM judge over emitted traces — an always-on Online Rule plus a scripted thread pass | `python -m evals online-rule create` · `python -m evals online` |

Every command lives under one CLI:

```
$ uv run python -m evals --help
Usage: python -m evals [OPTIONS] COMMAND [ARGS]...

  decode eval suite — benchmark + regression harness (ADR-0017).

Options:
  --help  Show this message and exit.

Commands:
  benchmark    Run the outcome benchmark as one Opik Experiment (ADR-0022...
  kitaru       Join decode's Opik traces to Kitaru Sessions (ADR-0022 §11).
  mine         Mine the LIVE project for regressions worth turning into...
  online       Score decode's LIVE REPL threads with one...
  online-rule  Manage the Opik ONLINE RULE that scores live traces as...
  regression   Run the behavior Regression Cases host-native as an Opik...
  suite        Run the Opik Test Suite regression surface —...
  sync         Upsert the eval tracks' Opik surfaces (ADR-0022 §6,8).
```

## 1. Demo skills

Six skills under `.decode/skills/demo-N-*/`, run as in [01 §4](01_install_and_usage.md#4-try-a-skill), no Opik needed:

| Skill | Shows off |
|---|---|
| `/demo-1-terminal-arcade` | a playable `curses` Snake game in one file |
| `/demo-2-bug-hunt` | find and fix two seeded bugs until the suite is green |
| `/demo-3-repo-pulse` | live GitHub API data → single-file dashboard |
| `/demo-4-review-swarm` | three parallel Explore subagents → one verdict |
| `/demo-5-sandbox-feature-pr` | decode improves decode: sandbox + hand-back → draft PR |
| `/demo-6-article-kg` | web articles → interactive knowledge graph |

## 2. Benchmark

Nineteen tasks (7 easy / 6 medium / 6 hard) in the Terminal-Bench layout — `task.toml`, `instruction.md`, an `environment/` seed, a hidden `tests/test.sh`, a `solution/solve.sh` oracle. Format and audit table: [`evals/benchmark/tasks/README.md`](../evals/benchmark/tasks/README.md).

One **Trial** is the shipped product, not a test harness (ADR-0022 §1): the seed becomes a fresh git repo, a `decode run "<instruction>" --repo <seed> --local --max-requests <max_steps> --summary-json …` subprocess does the work in a real sandbox from a throwaway Harness Home, Hand-back pushes its `decode/<short-id>` branch back into the seed — and only then, **host-side**, the Verifier runs on a pristine clone of that branch with `tests/` copied in last and writes one float to `reward.txt`. The agent never sees its grader; a run that changed nothing is graded on the base commit and scores 0.

```bash
make eval-benchmark                                       # whole suite, docker sandbox, 1 trial
make eval-benchmark ARGS='--difficulty easy'              # easy | medium | hard
make eval-benchmark ARGS='--task 001-find-and-replace'
make eval-benchmark ARGS='--trials 3'                     # 3 trials/task → pass@3, pass^3, flakiness
make eval-benchmark ARGS='--sandbox modal --threads 4'    # remote rung, 4 trials at a time
make eval-benchmark ARGS='--job-name nightly-3 --model gemini-2.5-pro'
```

`--trials` is Opik's `trial_count`, `--threads` its `task_threads` (default 1 on docker, 4 on modal — a docker trial warms its own container). `--job-name` names **both** the Opik experiment and the Trial Dir parent (default `bench-<UTC stamp>`). `--model` overrides the model every trial runs on, never the provider.

> ✅ A Rich table — one row per task (`n`, `pass@1`, at `k > 1` also `pass@k` / `pass^k` / `flaky`, `~$/trial`, `infra`), a section per tier, a total row — then one line naming the experiment and the trial dirs. In Opik: one experiment row over the `decode-benchmark-v2` dataset, tagged with model, provider, git sha, sandbox and `kitaru_agent_id`.

### Running the agent on the Modal endpoint

Two different knobs both say "modal": `--sandbox modal` is where the agent's **tools** run (the remote sandbox rung, [03](03_sandboxing.md)); `LLM_PROVIDER=modal` is which **model** the agent talks to (your own endpoint, [02](02_modal_endpoints.md)). They combine freely. For the model side:

```bash
LLM_PROVIDER=modal make eval-benchmark ARGS='--threads 1'          # agent on the endpoint, docker sandbox
LLM_PROVIDER=modal EVAL_JUDGE_PROVIDER=modal make eval-regression  # agent and judge on the endpoint
```

- **Keep the endpoint at min 1 container** while a run is in flight (Modal dashboard → the endpoint → scaling). At min 0 the first trial waits out a cold start, and a container that dies mid-run answers `503` to every trial after it.
- **`--threads 1` on a single-container endpoint.** Two concurrent trials against one container produced a request that never returned; decode's client has no per-request timeout, so that trial ran to its full agent timeout (600 s), graded on the base commit as `agent_fail`, and left its sandbox container behind (`docker ps`, `docker rm -f <id>`). Rerun alone it passed in 61 s.
- The regression cases run host-native and one at a time, so no thread knob applies there.

Which Opik project a run writes to never depends on the provider: benchmark and regression experiments always land under `decode-evals` (their datasets live there); only the online track ([§4](#4-online-eval-and-the-mining-loop)) reads the **live** project, `decode-<DECODE_ENV>`.

### The Trial Dir — evidence on disk, always

Written in a `finally`, so even a trial that blew up leaves its evidence, under the job name:

```
.decode/evals/runs/<job>/<task>__<short-id>/
├── seed/                 # the Seed Repo the agent cloned (with its decode/<short-id> branch)
├── home/                 # the throwaway Harness Home: sessions, logs, .decode/sandbox
├── pristine/             # the clone the Verifier graded, tests/ overlaid last
├── agent/
│   ├── stdout.txt        # the answer decode printed
│   ├── stderr.txt        # guards, notices, the hand-back line
│   └── summary.json      # {session_id, exit_reason, requests, tokens, cost_usd, handback…}
├── verifier/
│   ├── test-stdout.txt · test-stderr.txt
│   └── reward.txt        # the one float that graded the trial
└── result.json           # reward, status, reason, per-phase timings, git sha, model
```

A reward is reproducible from here with a bare `python3` — no Opik, no docker. Start any post-mortem at `result.json`, then `agent/stderr.txt`.

### Failure taxonomy — what counts and what is thrown out

| `result.json` status | When | Effect on the score |
|---|---|---|
| `agent_ok` | reward 1 | numerator + denominator |
| `agent_fail` | wrong answer, the `--max-requests` ceiling, the agent **timeout**, an `error` exit **that still wrote a summary** | denominator only — a real loss, with a named reason |
| `infra_error` | seed/clone failed, the sandbox never started, `decode run` died **before** its summary, the Verifier crashed/timed out, an unreadable reward | excluded from **both** — Opik `ScoreResult(scoring_failed=True)`, counted in the `infra` column |

A timeout is never an infra error: the process group gets SIGINT, decode's `finally` hands the partial branch back, and it is graded like any other (so a timed-out run with no summary is still `agent_fail`, reason `the agent timed out after 900s; it never wrote a summary`). A missing or non-numeric `reward.txt` is an error, never a 0.

### Calibration — what the numbers mean

Tier ceilings (easy 15 steps / 600 s · medium 25 / 900 s · hard 40 / 1500 s) are calibrated to the open model the course serves on Modal (`Qwen/Qwen3.6-35B-A3B-FP8`, [02](02_modal_endpoints.md)): an easy task should finish reliably, a hard one sometimes. On the out-of-box `gemini` route the same tasks read higher — compare runs on the same provider, never across. **The step cap is never the sensitivity knob**; trials are. `--trials 3` gives you pass@3 (at least one of three passed — capability), pass^3 (all three passed — reliability) and the gap between them, which is flakiness. One trial tells you nothing about spread.

Keyless, in every `make ci`: the oracle gate proves each task's Verifier can tell `solution/solve.sh` (reward 1) from an untouched seed (reward 0).

## 3. Regression Cases

A case checks design intent: the right tool, a minimal diff, the permission gate respected, compaction survived. Host-native (`SANDBOX_MODE=none`, a fresh temp workspace, no docker), fast enough for a pre-merge ritual. Twenty-one invented cases tiered 5 easy / 8 medium / 8 hard, plus the mined ones that carry the trace they came from ([`evals/regression/README.md`](../evals/regression/README.md)).

```bash
make eval-regression                            # sync + run + the threshold gate
make eval-regression ARGS='--difficulty hard'   # one tier, synced and gated together
python -m evals regression --case smoke-read-tool
python -m evals suite --difficulty hard         # the same cases, judged on their assertions
```

> ✅ The gate (`evals/regression/test_thresholds.py`) prints one table **per tier** and gates **globally**: tool discipline ≥ 0.8, judges ≥ 0.7. A drop against the previous experiment WARNs. The experiment is `decode-regression-gate`, or `decode-regression-gate-<tier>` for a filtered run, so a tier's baseline stays its own.

**Editing a case re-syncs it.** A case's prompt, metrics, tier, symptom, assertion or description change its checksum. Both `make eval-regression` and `python -m evals regression` sync their selection before grading, so the fresh item is what gets graded; the stale item stays in Opik (it never deletes) and is ignored, and the Test Suite is minted under a new name. A `RegressionSelectionError` naming a case means Opik has not reflected the insert yet — rerun.

**Two surfaces, on purpose.** One case definition registers twice: as a `decode-regression-v2` dataset item that deterministic metrics score (`python -m evals regression`), and as an item in the `decode-regression-suite-<8 hex>` Test Suite (named after the synced cases' content, so an edit mints a fresh suite rather than a second, twice-judged item) whose English `assertion` an LLM judge checks against the answer (`python -m evals suite`, gated on `pass_rate` ≥ 0.8). Numbers catch exact regressions cheaply; assertions catch "the answer got worse in a way no single number captures". The contrast is the lesson — neither replaces the other.

## 4. Online eval, and the mining loop

The online track grades the traces decode **already emitted** from real sessions, in place, in the live project. Two halves:

```bash
python -m evals online-rule create              # once: the always-on response_quality judge
python -m evals online-rule create --dry-run    # rehearse: prints the payload, writes nothing
python -m evals online                          # on demand: a conversation judge over recent threads
python -m evals online --filter 'start_time > "2026-07-01T00:00:00Z"'
```

`online-rule create` is idempotent (a second run prints `already exists: <id>`) and needs only `OPIK_API_KEY` — the judge runs on **Opik's** provider, so spell the model as your workspace does (`--model gemini-2.5-flash`, not `gemini/gemini-2.5-flash`). Prompt and the UI fallback: [`evals/README.md`](../evals/README.md).

That rule is what makes the loop close — **live traffic → mined trace → Regression Case → gate**:

```bash
python -m evals mine --preset errors --since 2026-09-04T00:00:00Z   # errors | low-quality | long | denied | all
python -m evals mine --preset all --limit 100 --json                # every id, for case authoring
```

`mine` is read-only: it searches the live project, clusters hits by signature — preset, error, last tool, model — and prints trace ids. You pick one, read the run in Opik (its `thread_id` is the decode session id), and write the case that fails on exactly that behaviour into `evals/regression/cases/mined_<slug>.py` with its `source_trace_id`. From then on the gate in §3 owns it. Worked example, picks and deliberate skips: [`evals/regression/mining/NOTES.md`](../evals/regression/mining/NOTES.md).

### Choosing the judge's provider

The judge has its own provider knob: `EVAL_JUDGE_PROVIDER` (`gemini` | `openrouter` | `modal`; empty
follows the agent's `LLM_PROVIDER`, which is what every run before this knob did), with
`EVAL_JUDGE_MODEL` picking the model *on* that route (a LiteLLM string). So a modal-served agent can
be graded by a cheap gemini judge — or by your own endpoint, at no per-token cost:

```bash
EVAL_JUDGE_PROVIDER=modal make eval-regression   # judge on the Modal endpoint
```

When agent and judge providers differ, the preflight asks for **both** keys — one guard serves
`eval-benchmark` (agent only) and `eval-regression` (agent *and* judge).

⚠️ A `modal` judge gives two things up, both forced by the endpoint (SGLang + DFLASH speculative
decoding): it drops `logprobs`/`top_logprobs`, which the server refuses, so G-Eval parses the score
out of the returned JSON instead of weighting it by token probabilities; and it switches Qwen's
thinking off (`chat_template_kwargs.enable_thinking=false`) under a 300 s timeout, because a
thinking judge blows opik's 60 s default restating the rubric. **Scores from a logprob judge and a
non-logprob judge are not directly comparable** — compare a modal judge only against itself.

---

**Next:** [06_evals_replays.md](06_evals_replays.md) — record real sessions and replay them with one change, with Kitaru.
