---
id: 165-kitaru-local-bootstrap-import-cohort
feature: evals-v2
status: done
---

# Kitaru on the local OSS server: bootstrap script, `evals kitaru import`, `evals kitaru cohort`

Tags: `evals`, `kitaru`, `[HUMAN]`
Depends on: 155, 161
Blocks: 166

## Scope
ADR-0022 §10–§11: any Kitaru Server is one URL; registrations are one idempotent script; two thin commands join Opik to Kitaru by the decode session id. e2e against `kitaru login --local` (server image + `postgres:16-alpine` via docker compose on `localhost:8000`). The managed workspace is deactivated (`subscription_ended`) and becomes a URL switch later.

## Acceptance criteria
- [x] `scripts/bootstrap_kitaru.py [--server URL] [--dry-run]` (targets `KITARU_API_URL`, 0.24+ idempotent registration): creates agent `decode` when absent; registers agent versions from the spec in today's `scripts/register_kitaru_agent.py` (its pure helpers `build_run_env`/`register_argv` move here; that script and its tests are deleted; `tests/unit/scripts/test_bootstrap_kitaru.py` covers the moved helpers + the orchestration with a fake `kitaru` subprocess); registers importer `opik` (`--script importers/opik_importer.py --entrypoint parse --provider opik`) and every `evaluators/*.py` (`--entrypoint evaluate`); prints a table `kind · name@version · id`; `--dry-run` prints the exact `kitaru …` argv it would run. Re-running changes nothing and prints the same table.
- [x] `make kitaru-local` = `uv run kitaru login --local` + `uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000`, then prints the two lines to export (`KITARU_API_URL=http://localhost:8000`, `KITARU_AGENT_ID=<uuid>`); `.env.example` documents both servers (`# local OSS server (make kitaru-local)` / `# managed workspace`) and repeats that `KITARU_API_URL` must be exported, not merely in `.env`.
- [x] `python -m evals kitaru import <trace-id>... [--thread <session-id>]`: Opik SDK fetches each trace + spans, expands a trace id to its whole thread, writes the `opik@N` importer envelope `{schema_version, workspace, project, traces: [{trace, spans}]}` to `.decode/kitaru-imports/<thread>.json`, shells `kitaru session import <file> --importer opik@<v> --agent decode@<v> --media-type application/json --tag regression-case --wait`, prints `thread_id → kitaru session id (readiness)`; skips a thread whose Session already exists (looked up by `session_name` via `kitaru session list --output json`, client-side filter), saying so. Envelope builder is pure and unit-tested against the importer's own parser (round-trip on a fixture export).
- [x] `python -m evals kitaru cohort from-experiment <experiment-name> [--cohort decode-benchmark-failures]`: reads the Opik experiment's items, keeps `reward == 0` (not `scoring_failed`), resolves each trial's Kitaru Session by `session_name` = the trial's decode `session_id` (from the trace metadata/summary; `kitaru_session_id` used as a shortcut when present), creates the cohort if absent then `kitaru cohort version create` with the session ids, prints the version reference and the session count; refuses with one line when the experiment's `experiment_config.kitaru_agent_id` is `None` or no Session resolves.
- [x] Both commands: keyless-skip on missing `OPIK_API_KEY` / `KITARU_API_URL` (exit 0, one line); `--help` opik/kitaru-free; unit tests with a fake Opik client + fake `kitaru` subprocess (argv asserted).
- [x] `running_the_code/06_evals_replays.md`: new §0 "Pick a server" (local via `make kitaru-local` vs managed, one URL), `scripts/bootstrap_kitaru.py` replaces the manual register commands, the two new commands in the flow; `07_evals_replays_deploy.md` notes the Modal Worker needs a server reachable from Modal (the managed one, when it resumes).
- [ ] [HUMAN] e2e on the local server: `make kitaru-local` → export the two vars → `make eval-benchmark ARGS='--task 001-find-and-replace --trials 2'` → `kitaru session list --agent decode` shows two Sessions named by the trials' session ids → `python -m evals kitaru cohort from-experiment <job>` (force one failure if both pass: `--task 018-git-bisect-revert --trials 1`) → `kitaru replay create <session> --agent decode@<v> --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' --evaluator decode-bad-request-400@1` settles on a laptop Worker. Ids logged here.

## Out of scope
- `evals kitaru sync-scores`; outcome re-verification on replays; ephemeral/Modal Workers against the local server; the managed-workspace control-plane key (task 153).

## Log
### [Tester] 2026-09-11 12:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 350 files; `ruff check` all passed; `make pre-commit` 2943 passed / 1 skipped, 0 failed — matches SWE's report)
- Unit tests: 2943 passed / 0 failed
- Integration tests: N/A (no infra code changed, per SWE — confirmed by diff shape)
- Warnings: 0

**E2E adversarial pass**
- Happy path: `KITARU_API_URL=http://localhost:8000 uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000 --dry-run` → prints the 5-row `exists` table, `--dry-run: nothing was registered.`; confirmed via `kitaru agent/importer/evaluator get` before/after that `latest_version` is unchanged (2/1/1). Real re-run (no `--dry-run`) prints the identical table + `KITARU_AGENT_ID=...`; `kitaru agent version list decode` still has exactly 2 versions. PASS.
- Break path 1 (state edge: worker cwd ≠ repo root — the importer-portability smell called out in the SWE's own Notes): killed all stray local workers, started a **fresh** `kitaru worker start` with cwd `/tmp/qa-kitaru-cwd-test` (`uv run --project <repo> kitaru worker start --server http://localhost:8000`), then `uv run python -m evals kitaru import 01a0861b-799c-7833-b78f-e8595294bc36` (a trace not previously touched) → `... failed: Job ... settled as failed.`, worker log: `Task ... failed: Importer process exited with code 1.` Reproduced directly: `python3 importers/opik_importer.py` run with cwd `/tmp` raises `ModuleNotFoundError: No module named 'importers'` at `from importers.opik_spans import is_tool_span, tool_span_name` (line 56). **FAIL** — confirms the smell the SWE flagged but left unfixed. Fix (per task spec): make `importers/opik_importer.py` self-contained (inline the span reader), or have both `importers/opik_importer.py` and `evals/harness/mine.py` import from one shared place that does not depend on the Worker's cwd containing the repo root on `sys.path`.
- Break path 2 (dangling references, QA instruction §6): `grep -rn register_kitaru_agent` across live (non-task, non-ADR) source turns up 4 remaining hits the SWE's "mechanical doc fixes" pass missed: `scripts/modal_kitaru_worker.py:35` (references a `--sandbox-mode none --skip-bin-check` invocation of the deleted script — `bootstrap_kitaru.py` has neither flag), `src/decode/remote/image.py:10` (`--skip-bin-check`, same problem), `src/decode/remote/app.py:103`, `src/decode/remote/headless.py:151`. These are live docstrings/comments, not historical task logs or ADRs (which are correctly left alone). **FAIL** — a future reader following these pointers hits a deleted file and flags that no longer exist on the replacement.
- Break path 3 (keyless skip + `.env` fallback boundary): `env -u OPIK_API_KEY` alone did NOT trigger the skip (repo's `.env` still supplies the key — expected per ADR-0021's `process env > .env` chain, not a bug); `OPIK_API_KEY="" KITARU_API_URL=http://localhost:8000 uv run python -m evals kitaru import trace-x` → `evals kitaru import: skipped — set OPIK_API_KEY to backfill sessions.` exit 0. `env -u KITARU_API_URL` → `evals kitaru import: skipped — set KITARU_API_URL to backfill sessions.` exit 0; same for `cohort from-experiment`. PASS.
- Break path 4 (malformed input: unknown experiment name) → `evals kitaru cohort from-experiment does-not-exist` → one-line refusal, non-zero exit, no traceback (regression-tested fix for the SDK's raising `get_experiments_by_name` confirmed live). PASS.

**Acceptance criteria**
- [x] PASS — AC1 bootstrap idempotent register/dry-run — evidence above; server state (`latest_version`) byte-identical before/after `--dry-run`.
- [x] PASS — AC2 `make kitaru-local` + `.env.example` — `git diff .env.example` shows both servers documented as prose with the export reminder; `Makefile kitaru-local` target read and matches spec (not re-run live to avoid a second docker-compose stack — `make kitaru-local`'s `bootstrap_kitaru.py` call already verified standalone above).
- [x] PASS (with a live break path, see above) — AC3 `evals kitaru import` — one more real `decode-prod` trace (`01a08614-aaf4-752a-857a-209f2ca411c9`, thread `69b1d5ef-b0b7-4788-9488-3d1870241ae3`) imported from a repo-root worker: `... → 01a08fb3-4dd7-7f72-b401-05885aa8b7e7 (ready)`; `kitaru session list --tag regression-case` shows it tagged; re-run (`--thread ...`) → `0 of 1 thread(s) imported` (skipped); envelope at `.decode/kitaru-imports/69b1d5ef-b0b7-4788-9488-3d1870241ae3.json` round-trips through `importers.opik_importer.parse` → one `ImportedSession`, name `decode_run`, matching `external_id`. The literal AC text is met; the cwd-portability break path above is a separate FAIL.
- [x] PASS — AC4 `evals kitaru cohort from-experiment` — re-run of `bench-task165-demo` → `already holds every failing Session (1); nothing added.` (no new version); `from-experiment bench-qa-161` (a real pre-existing job with no `kitaru_agent_id`, task 161) → one-line refusal: `this experiment recorded no Kitaru Sessions (experiment_config.kitaru_agent_id is None) — export KITARU_AGENT_ID (and KITARU_API_URL) and re-run the benchmark.`
- [x] PASS — AC5 keyless-skip + `--help` isolation — see break paths 3 above; `--help` verified via `sys.modules` check after `runpy.run_module`: `'opik' in sys.modules` and `'kitaru' in sys.modules` both `False`.
- [x] PASS (with break path 2 above) — AC6 runbooks 06/07 — read, content matches the described §0/notes; the *separate* dangling-reference issue is in code files the AC doesn't name, not in the runbooks themselves.
- [ ] Awaiting human verification — AC7 `[HUMAN]` e2e (benchmark → cohort → replay). Not run: the `kitaru replay create` leg calls a live model (cents) even under a `history` tool policy, and the task explicitly leaves this open for a human. Live infra confirmed available for the human to pick this up: local server up (`kitaru-local-server-1`/`kitaru-local-db-1`), a repo-root Worker left running (started fresh by the Tester after killing stray processes), agent `decode` v1(docker)/v2(none), `opik@1` importer, `decode-bad-request-400@1` evaluator, cohort `decode-benchmark-failures@1`.

**Evidence**
```
$ KITARU_API_URL=http://localhost:8000 uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000 --dry-run
kitaru bootstrap → http://localhost:8000
KIND                   · NAME@VERSION             · ID                                   · ACTION
agent                  · decode                   · 01a08fa4-2060-7442-8ec7-56af85ffc6c1 · exists
agent version (docker) · decode@1                 · 01a08fa4-2072-79f3-a99a-0da53a6bbd52 · exists
agent version (none)   · decode@2                 · 01a08fa4-2226-7e62-b90b-1f925af7f00e · exists
importer               · opik@1                   · 01a08fa4-267d-70f0-9570-b74838584654 · exists
evaluator              · decode-bad-request-400@1 · 01a08fa4-29f0-7353-9b8f-858ddf923ee5 · exists
--dry-run: nothing was registered.

$ cd /tmp/qa-kitaru-cwd-test && uv run --project <repo> kitaru worker start --server http://localhost:8000   # fresh worker, cwd = /tmp
$ uv run python -m evals kitaru import 01a0861b-799c-7833-b78f-e8595294bc36
f43f2cf7-5b69-4e01-8dd5-2b88c623cbf0 → `kitaru session import ... --server http://localhost:8000` failed: Job ... settled as failed. (failed)
evals kitaru import: 0 of 1 thread(s) imported from decode-prod into http://localhost:8000.
# worker log: "Task ... failed: Importer process exited with code 1."
$ .venv/bin/python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('opik_importer', 'importers/opik_importer.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)"   # run with cwd=/tmp
FAILED: ModuleNotFoundError No module named 'importers'

$ grep -rn register_kitaru_agent scripts/modal_kitaru_worker.py src/decode/remote/*.py
src/decode/remote/image.py:10:...``scripts/register_kitaru_agent.py --skip-bin-check``...
src/decode/remote/app.py:103:...(``scripts/register_kitaru_agent.py``)...
scripts/modal_kitaru_worker.py:35:...``scripts/register_kitaru_agent.py --sandbox-mode none --skip-bin-check``...
src/decode/remote/headless.py:151:...(``scripts/register_kitaru_agent.py``)...
```

**Other issues found**
- None beyond the two FAILs above; code quality otherwise strong (pure builders, digest-based idempotency, `resolve_ref`, paged session listing, the two regression-tested live-bug fixes for stderr envelopes and `get_experiments_by_name` are all sound and re-verified live).
- `git status --short src/` empty; no stray commits (HEAD still `210f94a`, task 164); no `decode/*` refs on `origin` (`git ls-remote --heads origin` checked); AGENTS.md + manual-e2e-qa skill edits are minimal and accurate (both now point at `bootstrap_kitaru.py`).

**VERDICT: FAIL**

Two concrete, reproduced issues to fix:
1. **Importer portability (live-reproduced):** `importers/opik_importer.py` imports `importers.opik_spans`, which only resolves when the Worker's process has the repo root on `sys.path` (i.e. cwd = repo root). A Worker started elsewhere fails every import with `ModuleNotFoundError: No module named 'importers'`. Fix: inline the span-reading helpers into `importers/opik_importer.py` (self-contained, single-`--script` upload), or restructure so both `importers/opik_importer.py` and `evals/harness/mine.py` import from one place that does not depend on the Worker's launch cwd.
2. **Dangling `register_kitaru_agent.py` references in live code** (not ADRs/task logs, which are fine as historical record): `scripts/modal_kitaru_worker.py:35`, `src/decode/remote/image.py:10`, `src/decode/remote/app.py:103`, `src/decode/remote/headless.py:151`. Two of these also name flags (`--sandbox-mode`, `--skip-bin-check`) that do not exist on `bootstrap_kitaru.py`. Update these to point at `scripts/bootstrap_kitaru.py` with accurate usage.

### [PA] 2026-09-10 — Grooming
The importer parser exists and is unit-tested; only the acquisition half was missing (its docstring says "see scripts/ once wired"). The SDK path avoids the `opik export` directory dance and produces the same `{trace, spans}` shape. kitaru 0.26 CLI verified: `login --local`, `experiment run start --baseline-evaluation-mode`, `worker start --claim`, `importer register/version`, `evaluator register`; `session list` carries no payloads since 0.24 (names still do). The adapter exposes no Session-id accessor, so `session_name` (= decode session id) is the join everywhere.

### [SWE] 2026-09-11 12:05 — Implementation

**Files modified**
- `scripts/bootstrap_kitaru.py` — NEW: one idempotent registration for ONE Kitaru Server (agent + its two Agent Versions, importer `opik`, every `evaluators/*.py`); the deleted script's pure builders `build_run_env`/`register_argv` moved here verbatim.
- `scripts/register_kitaru_agent.py`, `tests/unit/scripts/test_register_kitaru_agent.py` — DELETED (35 tests; the helper-level ones moved, the ones for flags that no longer exist dropped).
- `evals/harness/kitaru_cli.py` — NEW: the ONE `kitaru` CLI seam (argv + envelope + paging + `KITARU_API_URL` read + `resolve_ref`).
- `evals/harness/kitaru_import.py` — NEW: Opik traces → thread → the `opik` envelope → `kitaru session import`.
- `evals/harness/kitaru_cohort.py` — NEW: an Opik experiment's failing trials → a Kitaru Cohort version.
- `evals/run.py` — NEW `kitaru` group: `import` + `cohort from-experiment` (lazy imports, keyless skip).
- `evals/harness/benchmark.py` — `trial_payload` / `_infra_payload` now carry `session_id` + `kitaru_session_id` (see Notes: AC3/AC4's join did not exist without this).
- `Makefile` — `kitaru-local` (+ `.PHONY`, `KITARU_LOCAL_URL`).
- `.env.example` — both servers, as PROSE (a `KEY=` line would break the settings-drift guard; `KITARU_API_URL` is not a `Settings` field).
- `running_the_code/06_evals_replays.md` — new §0 "Pick a server" (table + bootstrap), bootstrap replaces the manual registers, both new commands in the flow, 3 new troubleshooting rows.
- `running_the_code/07_evals_replays_deploy.md` — note: the Modal Worker needs a server reachable FROM Modal, and version numbers are per-server (read them off `agent version list`).
- `AGENTS.md` (Agent Version bullet) + `.agents/skills/manual-e2e-qa/SKILL.md` — the two live pointers at the deleted script (mechanical; flagged for PA).
- Tests: `tests/unit/scripts/test_bootstrap_kitaru.py` (51), `tests/unit/evals/harness/test_kitaru_cli.py` (19), `test_kitaru_import.py` (32), `test_kitaru_cohort.py` (29), `tests/unit/evals/test_run.py` (+8), `tests/unit/evals/harness/test_benchmark.py` (+3).

**Tests**
- Unit: 2943 passing, 0 failing (`make pre-commit`; 1 pre-existing `fastapi` skip). New: 142; deleted: 35.
- Integration: N/A — no infra code changed (the live evidence below is the real thing, not a test).

**Acceptance criteria**
- [x] AC1 bootstrap — live below; idempotent by read-then-register (NOT by "0.24+ idempotent registration": a duplicate register is a 409, see Notes).
- [x] AC2 `make kitaru-local` + `.env.example` — live below.
- [x] AC3 `evals kitaru import` — live below; skip matches on the importer's `external_id`, not the Session Name (see Notes).
- [x] AC4 `evals kitaru cohort from-experiment` — live below, including both refusals.
- [x] AC5 keyless skip + opik/kitaru-free `--help` + fake-client tests — `test_run.py::test_kitaru_import_skips_friendly_without_the_two_keys`, `::test_the_kitaru_bridge_help_imports_neither_opik_nor_kitaru`.
- [x] AC6 runbooks 06 + 07.
- [ ] [HUMAN] AC7 e2e — done non-interactively as far as it goes (bootstrap, benchmark trial, cohort, import); the `kitaru replay create` leg is NOT run (it spawns a real docker replay on a laptop Worker). Ids below.

**Evidence**
```
$ make kitaru-local
KIND                   · NAME@VERSION             · ID                                   · ACTION
agent                  · decode                   · 01a08fa4-2060-7442-8ec7-56af85ffc6c1 · created
agent version (docker) · decode@1                 · 01a08fa4-2072-79f3-a99a-0da53a6bbd52 · registered
agent version (none)   · decode@2                 · 01a08fa4-2226-7e62-b90b-1f925af7f00e · registered
importer               · opik@1                   · 01a08fa4-267d-70f0-9570-b74838584654 · registered
evaluator              · decode-bad-request-400@1 · 01a08fa4-29f0-7353-9b8f-858ddf923ee5 · registered
KITARU_AGENT_ID=01a08fa4-2060-7442-8ec7-56af85ffc6c1
export KITARU_API_URL=http://localhost:8000
export KITARU_AGENT_ID=01a08fa4-2060-7442-8ec7-56af85ffc6c1

$ uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000    # re-run
... the same five rows, every ACTION now `exists`, same ids; `kitaru agent version list decode` still has exactly 2.

$ uv run python -m evals kitaru import 01a08d79-4563-7281-9601-4036f0443810   # a real decode-prod trace
1e1851f5-c7ed-480b-84a4-8b6b876167f2 → 01a08fa5-1078-7660-90c9-6b34b8de0c1d (ready)
evals kitaru import: 1 of 1 thread(s) imported from decode-prod into http://localhost:8000.
$ uv run python -m evals kitaru import --thread 1e1851f5-...                  # re-run
1e1851f5-c7ed-480b-84a4-8b6b876167f2 → 01a08fa5-1078-7660-90c9-6b34b8de0c1d (ready)
evals kitaru import: 0 of 1 thread(s) imported ...
$ uv run kitaru session get 01a08fa5-1078-7660-90c9-6b34b8de0c1d
{'name': 'decode_run', 'origin': 'imported', 'external_id': 'default/decode-prod/1e1851f5-...', 'imported_from': 'opik',
 'llm_call_count': 1, 'tool_call_count': 1, 'cost': '0.00620535', 'tokens': {...}, 'inputs': {'input': 'list every file ...'},
 'metadata': {'replay_readiness': {'level': 'ready', 'replayable_tool_call_count': 1}, 'opik_thread_id': '1e1851f5-...'}}
$ uv run kitaru session list --tag regression-case        # AC3's tag really landed on the created Session
1 [('01a08fa5-1078-7660-90c9-6b34b8de0c1d', 'decode_run')]

$ make eval-benchmark ARGS='--task 018-git-bisect-revert --trials 1 --job-name bench-task165-demo'
018-git-bisect-revert | n=1 | pass@1 0.00 | experiment bench-task165-demo logged under decode-evals
$ uv run kitaru session list --agent decode --origin recorded
01a08fa5-bf8e-7952-ab42-e022b33b2c2b  f0b5b1a6-3d4b-44e5-bdc5-7e5125a976a3  failed   # named by the trial's session id
$ uv run python -m evals kitaru cohort from-experiment bench-task165-demo
evals kitaru cohort: decode-benchmark-failures@1 — 1 session(s), 1 added from bench-task165-demo (cohort created).
$ uv run python -m evals kitaru cohort from-experiment bench-task165-demo    # re-run
evals kitaru cohort: decode-benchmark-failures@1 already holds every failing Session (1); nothing added.
$ uv run python -m evals kitaru cohort from-experiment does-not-exist
Error: evals kitaru cohort: no Opik experiment named 'does-not-exist' in decode-evals — check the job name `evals benchmark` printed.
$ env -u KITARU_API_URL uv run python -m evals kitaru import trace-x
evals kitaru import: skipped — set KITARU_API_URL to backfill sessions.        # exit 0
```

**Notes**
- **Server state left for the Tester:** `kitaru-local-server-1` + `kitaru-local-db-1` up on `http://localhost:8000` (fresh DB — the probe volume was deleted before the demo). **Start your own Worker before `evals kitaru import`** (`uv run kitaru worker start --server http://localhost:8000` in another shell) — mine lived in this session's process and is gone; with no Worker claiming, an import waits and times out. Beware: the server still lists my three dead Workers as `live: True` (liveness is a heartbeat TTL, they were last seen at 09:02 UTC), so `kitaru worker list` is NOT proof a Worker is claiming — trust your own running process. Agent `decode` = `01a08fa4-2060-7442-8ec7-56af85ffc6c1` with v1 docker / v2 none, `opik@1`, `decode-bad-request-400@1`, cohort `decode-benchmark-failures@1`, 1 imported + 1 recorded Session. `uv run kitaru logout` stops it all. NOTE: `KITARU_API_URL` in this checkout's `.env` still points at the DEACTIVATED managed workspace, so every command must be run with it exported to the local URL.
- **Deviation (AC1 premise):** registrations are NOT idempotent on kitaru 0.26.0 — a duplicate `agent|importer|evaluator register` is a 409 `conflict` and `… version register` always creates the next version. Idempotency is therefore read-then-register: an Agent Version is matched on its whole run spec, an importer/evaluator version on the SHA-256 digest of the uploaded script carried in `--display-version`. Both "reuse" and "error" are handled (a read that fails reads as absent; a write that 409s surfaces as one line).
- **Deviation (AC3 join):** an IMPORTED Session is named by the importer after the first trace (`ImportedSession.name = first.get("name") or key` → live: `decode_run`), never by the thread id, so a `session_name` lookup would never match and every re-run would re-import. The skip check matches the importer's own `external_id` (`enc(workspace)/enc(project)/enc(thread)`), with `metadata.opik_thread_id` as a second spelling — still a client-side filter over `kitaru session list --output json`. The Session NAME join is exactly right for RECORDED sessions and is what AC4 uses.
- **Addition outside the ACs (load-bearing):** `trial_payload` gained `session_id` + `kitaru_session_id`. Without it AC4 has no join at all: an experiment item's `trace_id` is `evaluate()`'s wrapper trace, not the `decode run` subprocess trace, and nothing else on the row names the recorded run. Both values were already in the trial's `--summary-json`; `_infra_payload` gained the same keys as `None` so the payload shape stays uniform.
- **Two live bugs found and fixed regression-test-first:** (1) a FAILING `kitaru` sub-command prints its envelope to STDERR, so parsing stdout alone turned every `not_found` into "no json envelope" and lost the `kind` the cohort command branches on (`test_an_error_envelope_on_stderr_is_read_like_one_on_stdout`, `test_a_failing_kitaru_call_is_read_off_stderr`); (2) `opik.Opik.get_experiments_by_name` RAISES `ExperimentNotFound` instead of returning `[]`, so a mistyped job name dumped a traceback (`test_an_unknown_job_name_is_one_refusal_not_an_opik_traceback`). It is also a case-insensitive SUBSTRING search — the command now takes the newest EXACT match.
- **An import needs a live Worker.** `kitaru session import` files a job; the server executes nothing (ADR-0019), so with no Worker claiming, `--wait` times out (verified: `timeout` after 30s, "Start a worker with `kitaru worker start`"). Documented in the runbook + the command's help. Related: the uploaded `importers/opik_importer.py` imports `importers.opik_spans`, which resolves only because the Worker spawns the task with the repo root as cwd — a Worker started elsewhere would fail the import. Not fixed here (out of scope, single-`--script` upload); flagged for the PA.
- Mechanical doc fixes for the deleted script's two live pointers (`AGENTS.md` Agent Version bullet, the `manual-e2e-qa` skill's Worker-replay row). ADRs 0020/0021 also name the old script — left untouched, they are a historical record. `tasks/153` names it too (stale, left alone).
- `kitaru session get --output json` carries no `nodes` array (it reports `llm_call_count` / `tool_call_count` / `replay_readiness` instead) — the imported Session is NOT empty, the json envelope simply does not include the node list.
- Trade-off: `evals kitaru import` pages `session list` scoped to the import's agent on every run (one request per 200 sessions) rather than caching — fine at demo scale; if a workspace grows past a few thousand sessions, filter server-side by `external_id` when kitaru grows that filter.

### [SWE] 2026-09-11 12:26 — Fixes (Tester round 1)

Both FAILs fixed. No production behaviour changed: FAIL 1 is a pure move of seven functions into the
file that has to travel alone, FAIL 2 is comments and docstrings.

**Files modified**
- `importers/opik_importer.py` — the tool-span reader is now INLINE (the ONE definition): `is_tool_span`,
  `tool_span_name`, `tool_span_arguments`, `tool_span_result`, `tool_span_deferred` + `_mapping` /
  `_payload` and their four constants, moved verbatim out of `importers/opik_spans.py`. The file is
  self-contained: its only non-stdlib import is `kitaru.task.importer`, which every Worker has.
  Two stale pointers in the same file fixed while in there: the module docstring's "see ``scripts/``
  once wired" (the producer exists now — `evals/harness/kitaru_import.py`), and the new comment's
  mention of the sibling, which now says "the now-deleted ``importers/opik_spans.py``".
- `importers/opik_spans.py` — DELETED. It only existed to be shared, and the importer cannot import a
  sibling (a Kitaru importer is uploaded as ONE script, run from the Worker's own cwd).
- `evals/harness/mine.py` — imports the four readers it uses FROM `importers.opik_importer` (one
  definition, no copy to drift), with a comment naming the cost: `evals mine` now pulls
  `kitaru.task.importer` (~0.3 s, already a main dep). `evals --help` never pays it — `run.py` imports
  `mine` inside the command body, and `test_the_kitaru_bridge_help_imports_neither_opik_nor_kitaru`
  still passes.
- `evals/harness/kitaru_import.py` — its docstring justified re-implementing `thread_key` /
  `session_external_id` by "exactly as `importers/opik_spans.py` keeps `evals mine` kitaru-free"; that
  reason died with the module, so it now names the real one (importing the importer would cost
  `evals kitaru import --help` a kitaru import, ADR-0017 §1).
- `tests/unit/importers/test_opik_spans.py` → `tests/unit/importers/test_opik_importer.py` (`git mv`) —
  same nine reader assertions against the new home, + 2 new pins (below);
  `test_the_importer_reads_tool_semantics_through_this_module` renamed to
  `test_the_parser_reads_tool_semantics_through_the_same_reader` (its contract changed from "two
  callers, one module" to "the parser and `evals mine` read the one definition").
- `scripts/modal_kitaru_worker.py`, `src/decode/remote/image.py`, `src/decode/remote/app.py`,
  `src/decode/remote/headless.py` — the four dangling `scripts/register_kitaru_agent.py` pointers now
  name `scripts/bootstrap_kitaru.py` with accurate wording (below).
- `scripts/bootstrap_kitaru.py` — two provenance mentions of the old script now say DELETED, so no
  reader mistakes them for a live pointer.

**Fix 1 — the importer travels alone (BLOCKER)**
Root cause, not symptom: the importer is uploaded as a single `--script` and executed by the Worker
from whatever cwd the Worker was started in, so ANY sibling import is a cwd bet. Inlining is the
smallest fix that removes the bet, and the Tester's first named option. `evals/harness/mine.py` is the
one other caller and now imports from the importer, so there is still exactly ONE definition.

Proven a pure move, not a rewrite:
- the import block differs from HEAD by exactly one deleted line (`diff` below);
- `ast.unparse` of all seven moved functions is byte-identical to HEAD's `opik_spans.py`, except
  `tool_span_deferred`, whose BODY is identical and whose docstring absorbed the module docstring's
  denial paragraph (the reason `evals mine --preset denied` reads it);
- `kitaru importer test` still reports `sessions:2, failures:1, items:3` — the same numbers tasks 155
  and 163 recorded.

`tool_span_result` moved with the rest even though only tests call it today (it is half of the
"both instrumentation spellings" pair `tool_span_arguments` belongs to) — a pure move, deliberately
not trimmed. NOT taken, still out of scope: task 163's flagged follow-up that `_build_node` unwraps
tool payloads with the OLD keys only (a behaviour change).

**Regression tests (red before the fix, green after)**
- `test_the_importer_loads_as_a_single_file_from_a_foreign_cwd` — loads the file via
  `importlib.util.spec_from_file_location` in a SUBPROCESS with `cwd=tmp_path` and `PYTHONPATH`
  scrubbed, asserting `parse` / `parser` / `is_tool_span` are callable. A subprocess, not the
  in-process load the Tester sketched, precisely because the repo IS importable inside a pytest
  session: an in-process load resolves a sibling out of `sys.modules` and passes while the real Worker
  fails.
- `test_the_importer_imports_nothing_but_the_stdlib_and_kitarus_task_contract` — walks the importer's
  AST and asserts its non-stdlib import roots are `{kitaru}`. This is the contract behind the symptom:
  it names the offending module the next time someone adds a sibling import.
- Red-proof against HEAD's importer (evidence below): foreign-cwd load exits 1 with
  `ModuleNotFoundError: No module named 'importers'`; AST roots are `['importers', 'kitaru']`.

**Fix 2 — dangling `register_kitaru_agent.py` pointers**
Each now points at `scripts/bootstrap_kitaru.py` and drops the two flags that never existed on it:
- `scripts/modal_kitaru_worker.py` — the worker spawns "the `none`-mode Agent Version"; the bootstrap
  registers it on EVERY server it bootstraps, alongside the laptop's `docker` one, and there is
  nothing to pass and no check to skip: `desired_versions()` reads `DECODE_BIN` / `HARNESS_HOME` from
  `decode.remote.image` and never stats them (only the laptop's own `decode` entrypoint is checked, in
  `main()`). The `--agent decode@3` pin is now "read the NUMBER off `kitaru agent version list decode`,
  it is per server" — a bootstrapped server has docker=1 / none=2, the managed workspace's `none` spec
  is 3 (4 is the QA-accident duplicate). Same reason the adjacent "the laptop's v2 / the v3 in-image
  paths" parenthetical now names the SPEC instead of a number.
- `src/decode/remote/image.py` — "registered from a laptop that cannot stat them" now explains WHY that
  is safe (the bootstrap registers the two values as-is) instead of citing `--skip-bin-check`.
- `src/decode/remote/app.py` / `src/decode/remote/headless.py` — the drift-guard comment and the
  timeout comment name `scripts/bootstrap_kitaru.py::desired_versions` / `scripts/bootstrap_kitaru.py`.

Every LIVE hit is gone. The Tester's literal gate ("only `tasks/done/`") is not reachable and should
not be: the remaining hits are `docs/adr/0020` + `docs/adr/0021` (SWE is read-only on ADRs, and they
are a historical record of decisions taken when the script existed), `tasks/165` + `tasks/done/*` (task
logs), `scripts/bootstrap_kitaru.py` ×2 and `tests/unit/scripts/test_bootstrap_kitaru.py` ×1 (provenance
inside the REPLACEMENT, now all three spelled "deleted"). One left for the PA, not mine to rewrite:
**`tasks/153-mint-control-plane-key-close-pending-gate.md:69` is a still-pending task whose spec tells an
operator to run `scripts/register_kitaru_agent.py --sandbox-mode none --skip-bin-check`** — a live
instruction that will not work; it needs re-grooming against `scripts/bootstrap_kitaru.py`.

**Tests**
- Unit: 2945 passing, 0 failing (`make pre-commit`; 1 pre-existing `fastapi` skip). +2 vs the Tester's
  2943 — the two new portability pins.
- Integration: 114 passing (`make integration-tests`, 8 min) — run because `src/` changed (docstrings
  and one comment only).
- Format / lint: `make format-fix && make lint-fix && make format-check && make lint-check` clean
  (349 files formatted, all checks passed).

**Acceptance criteria** — unchanged from the Tester's read: AC1-AC6 pass, AC7 `[HUMAN]` still open.
AC3's evidence is now stronger (see the foreign-cwd import below).

**Evidence**
```
$ diff <(git show HEAD:importers/opik_importer.py | sed -n '/^from __future__/,/^MAX_PAYLOAD_BYTES/p') \
       <(sed -n '/^from __future__/,/^MAX_PAYLOAD_BYTES/p' importers/opik_importer.py)
22,23d21
< from importers.opik_spans import is_tool_span, tool_span_name
<                                     # the whole import-block change: one deleted line

$ .venv/bin/python <compare.py>   # ast.unparse per moved function: HEAD's opik_spans.py vs the new home
_mapping: identical=True body_identical=True
is_tool_span: identical=True body_identical=True
tool_span_name: identical=True body_identical=True
_payload: identical=True body_identical=True
tool_span_arguments: identical=True body_identical=True
tool_span_result: identical=True body_identical=True
tool_span_deferred: identical=False body_identical=True    # docstring only

$ .venv/bin/python <red_proof.py>   # the two new pins, run against `git show HEAD:...` = RED
foreign-cwd load returncode: 1
ModuleNotFoundError: No module named 'importers'
third-party roots: ['importers', 'kitaru']
$ uv run pytest tests/unit/importers/test_opik_importer.py -q
11 passed in 1.97s                                          # = GREEN after the fix

$ uv run kitaru importer test importers/opik_importer.py --entrypoint parse \
      --payload importers/fixtures/opik-sample.json
{"ok":true,...,"sessions":2,"failures":1,"items":3}         # same numbers as tasks 155 and 163

# --- live, on the local OSS server -------------------------------------------------------------
$ lsof -a -p <tester's worker> -d cwd     # the worker left running would have FALSE-GREENED this
python3.1 97863 ... cwd DIR ... /Users/.../building-a-coding-agent-from-scratch-course
$ kill 97863                              # killed first, on purpose

$ KITARU_API_URL=http://localhost:8000 uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000
agent                  · decode                   · 01a08fa4-2060-7442-8ec7-56af85ffc6c1 · exists
agent version (docker) · decode@1                 · 01a08fa4-2072-79f3-a99a-0da53a6bbd52 · exists
agent version (none)   · decode@2                 · 01a08fa4-2226-7e62-b90b-1f925af7f00e · exists
importer               · opik@2                   · 01a08fc0-b3fe-7373-aa78-5ec1c464e210 · registered
evaluator              · decode-bad-request-400@1 · 01a08fa4-29f0-7353-9b8f-858ddf923ee5 · exists
# digest-based idempotency did register opik@2 by itself, as the Tester predicted; an immediate
# re-run printed `opik@2 · exists` and left `importer version list opik` at two rows.
# Then the two stale docstring pointers above moved the digest once more, so the NEXT bootstrap
# registered `opik@3 · 01a08fce-50cd-7691-b355-bc044807b538` — the same mechanism, proven twice, and
# the server's newest blob is byte-identical to the file in the tree.

$ cd /private/tmp/qa-165-foreign-cwd && uv run --project <repo> kitaru worker start --server http://localhost:8000 &
$ lsof -a -p 962 -d cwd
python3.1 962 ... cwd DIR ... /private/tmp/qa-165-foreign-cwd          # NOT the repo root

$ KITARU_API_URL=http://localhost:8000 uv run python -m evals kitaru import 01a0861b-799c-7833-b78f-e8595294bc36
f43f2cf7-5b69-4e01-8dd5-2b88c623cbf0 → 01a08fc1-3a62-7a91-9ab9-93d1edd61c38 (ready)
evals kitaru import: 1 of 1 thread(s) imported from decode-prod into http://localhost:8000.
# ^ the EXACT trace + thread the Tester's foreign-cwd worker failed on, never imported before.
$ uv run kitaru session get 01a08fc1-3a62-7a91-9ab9-93d1edd61c38
{'name': 'decode_run', 'origin': 'imported', 'external_id': 'default/decode-prod/f43f2cf7-...',
 'imported_from': 'opik', 'status': 'completed', 'llm_call_count': 8, 'tool_call_count': 7}
readiness: ready                     # real content, not an empty shell
$ python -c "resolve_ref(...)"  ->  importer opik@2 · agent decode@2      # the NEW blob ran
$ uv run python -m evals kitaru import --thread f43f2cf7-...              # re-run
f43f2cf7-... → 01a08fc1-3a62-7a91-9ab9-93d1edd61c38 (ready)
evals kitaru import: 0 of 1 thread(s) imported ...                        # skip still works

$ KITARU_API_URL=http://localhost:8000 uv run python -m evals kitaru import 01a08f1a-9aae-70c7-b6f0-5e7a00c50d9b
ebfc7abe-cdcb-4c71-b32c-fa7f2fd10b8a → 01a08fce-8480-74b0-ba8b-079f3132e7c7 (partial)
evals kitaru import: 1 of 1 thread(s) imported from decode-prod into http://localhost:8000.
# ^ a SECOND never-imported thread, through the foreign-cwd Worker, on the `opik@3` blob. `partial`
# is correct, not a defect: this is the gate-denied probe trace, whose deferred tool call has no
# result by construction (task 163).

$ KITARU_API_URL=http://localhost:8000 uv run python -m evals mine --preset denied --limit 20
denied | - | read | gemini-3.5-flash
│ 01a08f1a-9aae-70c7-b6f0-5e7a00c50d9b │ ebfc7abe-... │ 2026-09-11 06:14:40Z │ 5c46f38b5f56 │ gemini-3.5-flash │
evals mine: 1 trace(s) in 1 signature(s) from decode-prod (preset denied).
# ^ the one live path this fix moved: `denied` is the preset that routes through `tool_span_deferred`
# + `tool_span_arguments`, so this proves the readers resolve and run from their new home. It finds
# exactly the probe trace task 163 recorded.

$ grep -rn register_kitaru_agent --include='*.py' --include='*.md' . | wc -l
79        # every one accounted for: 55 in `tasks/done/` · 16 in this task file · 1 in `tasks/153`
          # (the PA item above) · 4 in `docs/adr/0020`+`0021` (SWE is read-only there) · 3 provenance
          # lines inside the replacement, all three now spelled "deleted":
$ grep -rn register_kitaru_agent --include='*.py' --include='*.md' . | grep -v tasks/ | grep -v docs/adr/
scripts/bootstrap_kitaru.py:12:  ... this file replaces the DELETED ``scripts/register_kitaru_agent.py``, ...
scripts/bootstrap_kitaru.py:125:# --- the pure builders (moved verbatim from the deleted scripts/register_kitaru_agent.py) ---
tests/unit/scripts/test_bootstrap_kitaru.py:5:* the PURE builders moved out of the deleted ``scripts/register_kitaru_agent.py`` ...
# no live pointer left; the three above name it as deleted, inside its own replacement.
```

**Notes**
- **Server state for the re-QA / the human:** local server still up on `http://localhost:8000`; the
  Tester's repo-root Worker was KILLED (it would false-green this fix) and a fresh one runs with cwd
  `/private/tmp/qa-165-foreign-cwd`, log at `/private/tmp/qa-165-foreign-cwd/worker.log` — a worker
  that proves portability on every task it claims. Agent `decode` `01a08fa4-2060-7442-8ec7-56af85ffc6c1`
  (v1 docker / v2 none), importer now `opik@3` (the version matching the file in the tree), evaluator
  `decode-bad-request-400@1`, cohort `decode-benchmark-failures@1`, sessions: 3 imported + 1 recorded. `KITARU_API_URL` in this checkout's
  `.env` still points at the deactivated managed workspace, so export the local URL for every command.
- **Trade-off carried forward, unchanged:** `evals mine` now costs one `kitaru.task.importer` import
  (~0.3 s) to keep ONE definition. The alternative — a re-export shim in a third module — would import
  kitaru too, and a second copy would drift the next time pydantic-ai renames an attribute (it already
  did once, which is why the two spellings exist).
- No commit made; branch `feat/evals-v2` still at `210f94a` (`git rev-parse --short HEAD`, checked now) with the work uncommitted, per the pipeline.

### [Tester] 2026-09-11 12:45 — QA (round 2)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 349 files; `ruff check` all passed; `make pre-commit` 2945 passed / 1 skipped, 0 failed — matches SWE's round-2 report exactly)
- Unit tests: 2945 passed / 0 failed
- Integration tests: not re-run (only comment/docstring changes in `src/` since round 1's green integration run); citing SWE's `make integration-tests` 114 passed
- Warnings: 0

**E2E adversarial pass**
- Happy path: re-verified round 1's still-passing items were untouched — see AC evidence below.
- Break path 1 (regression-test validity: does the new pin actually catch the round-1 bug?): reintroduced a sibling import in `importers/opik_importer.py` (`from importers.opik_spans import is_tool_span as _x`, with a stub `importers/opik_spans.py` recreated so the module resolves in-process) → `uv run pytest tests/unit/importers/test_opik_importer.py -q -k "foreign_cwd or stdlib_and_kitarus"` → both new pins go RED: `test_the_importer_loads_as_a_single_file_from_a_foreign_cwd` fails with `ModuleNotFoundError: No module named 'importers'` inside the subprocess; `test_the_importer_imports_nothing_but_the_stdlib_and_kitarus_task_contract` fails with `AssertionError: {'importers', 'kitaru'}`. Reverted (`cp` from backup + `rm` the stub) → `uv run pytest tests/unit/importers/test_opik_importer.py -q` → 11 passed; `sha256(importers/opik_importer.py)` after revert = `612b73107825e3...`, matching the digest below byte-for-byte. PASS — the pins are load-bearing, not decorative, and the revert was byte-exact.
- Break path 2 (live foreign-cwd import, independent of the SWE's worker): started my OWN Worker from `/private/tmp/qa-165-tester-foreign-cwd` (`nohup env KITARU_API_URL=http://localhost:8000 uv run --project <repo> kitaru worker start --server http://localhost:8000`, pid 6624), confirmed via `lsof -a -p 6624 -d cwd` → cwd is the foreign dir, not the repo. Picked a fresh, never-before-imported trace from `evals/regression/mining/2026-09-11-errors-unwindowed.json` (thread `cd4672a5-4918-4383-bae1-a0278aa9081e`, cross-checked absent from `kitaru session list --tag regression-case`) → `uv run python -m evals kitaru import 01a08d78-c569-7bab-91e1-2e1ca5d4c02b` → `cd4672a5-... → 01a08fd2-4c66-7930-b33d-d28ebcba6893 (ready)`. `kitaru session get` on it shows real content: `status: failed`, real `error` text (`ModelHTTPError: status_code: 503 ...`), `replay_readiness.level: ready`, `llm_call_count: 1`. A blob still carrying the sibling import would have exited 1 on this exact path (proven in break path 1 and in round 1). PASS.
- Break path 3 (do the four fixed comments name real symbols, not another invented flag — this is exactly the class of bug round 1 flagged): read `scripts/bootstrap_kitaru.py` directly rather than trusting the diff. `desired_versions(*, repo, decode_bin, harness_home)` exists (line 344), reads `DECODE_BIN`/`HARNESS_HOME` from `decode.remote.image` with no `.exists()`/`.is_file()` call on either; `main()` stats only the LAPTOP entrypoint (`entrypoint.is_file()`, line 673) via `--decode-bin` / the default `<repo>/.venv/bin/decode`. All three sites (`scripts/modal_kitaru_worker.py`, `src/decode/remote/image.py`, `src/decode/remote/app.py`) that now name `desired_versions` / "reads without stat" / "checks only the laptop's own entrypoint" describe this code accurately. PASS.

**Acceptance criteria** — unchanged from round 1's read; all six non-`[HUMAN]` criteria remain PASS with the round-2 fixes now verified:
- [x] PASS — AC1 bootstrap idempotent register/dry-run — unaffected by round-2 changes (comments/docstrings only in `scripts/bootstrap_kitaru.py`); re-verified `desired_versions` unchanged in behavior.
- [x] PASS — AC2 `make kitaru-local` + `.env.example` — unaffected by round-2 changes.
- [x] PASS — AC3 `evals kitaru import` — round-1's importer-portability FAIL is now fixed and re-proven live above (foreign-cwd Worker, fresh trace, `ready`).
- [x] PASS — AC4 `evals kitaru cohort from-experiment` — unaffected by round-2 changes.
- [x] PASS — AC5 keyless-skip + `--help` isolation — re-verified: `runpy.run_module('evals', run_name='__main__')` with `--help` → `'opik' in sys.modules` / `'kitaru' in sys.modules` both `False`; `uv run python -m evals mine --preset denied --limit 5` still finds the 1 probe trace through the moved readers.
- [x] PASS — AC6 runbooks 06/07 — unaffected by round-2 changes; the round-1 dangling-reference FAIL (separate from AC6's own text) is now fixed and re-verified by reading the referent, not just the diff (break path 3 above).
- [ ] Awaiting human verification — AC7 `[HUMAN]` e2e (benchmark → cohort → replay) — unchanged, still open for a human, per round 1.

**Evidence**
```
$ uv run pytest tests/unit/importers/test_opik_importer.py -q -k "foreign_cwd or stdlib_and_kitarus"
2 passed, 9 deselected in 0.92s                              # against the fix

# --- break path 1: red-proof the pins against a reintroduced sibling import ---
$ cat >> importers/opik_importer.py's import block: "from importers.opik_spans import is_tool_span as _x"
$ cat > importers/opik_spans.py <<'EOF'
def is_tool_span(span): return False
EOF
$ uv run pytest tests/unit/importers/test_opik_importer.py -q -k "foreign_cwd or stdlib_and_kitarus"
FAILED test_the_importer_loads_as_a_single_file_from_a_foreign_cwd
  ModuleNotFoundError: No module named 'importers'
FAILED test_the_importer_imports_nothing_but_the_stdlib_and_kitarus_task_contract
  AssertionError: {'importers', 'kitaru'}
2 failed, 9 deselected in 0.54s
$ rm importers/opik_spans.py && cp <backup> importers/opik_importer.py   # revert
$ uv run pytest tests/unit/importers/test_opik_importer.py -q
11 passed in 1.32s
$ python3 -c "hashlib.sha256(open('importers/opik_importer.py','rb').read()).hexdigest()"
612b73107825e32fa378118d900380957af56695df64569ab0821758a7812fc3   # byte-exact revert

$ grep -rn opik_spans --include='*.py' --include='*.md' .
# only tasks/165 (this file), tasks/done/163 (historical), and importers/opik_importer.py:62 +
# tests/unit/importers/test_opik_importer.py:10 (both say "now-deleted" — provenance, not live pointers)

$ python -c "runpy.run_module('evals', run_name='__main__')" (argv=['evals','--help'])
Usage: evals [OPTIONS] COMMAND [ARGS]...
False False                                                  # opik in sys.modules, kitaru in sys.modules

$ uv run python -m evals mine --preset denied --limit 5
denied | - | read | gemini-3.5-flash
01a08f1a-9aae-70c7-b6f0-5e7a00c50d9b │ ebfc7abe-... │ 2026-09-11 06:14:40Z │ gemini-3.5-flash
evals mine: 1 trace(s) in 1 signature(s) from decode-prod (preset denied).

# --- break path 2: independent foreign-cwd worker + fresh trace ---
$ mkdir -p /private/tmp/qa-165-tester-foreign-cwd
$ cd /private/tmp/qa-165-tester-foreign-cwd && nohup env KITARU_API_URL=http://localhost:8000 \
    uv run --project <repo> kitaru worker start --server http://localhost:8000 &   # pid 6624
$ lsof -a -p 6624 -d cwd
python3.1 6624 ... cwd DIR ... /private/tmp/qa-165-tester-foreign-cwd
$ KITARU_API_URL=http://localhost:8000 uv run python -m evals kitaru import 01a08d78-c569-7bab-91e1-2e1ca5d4c02b
cd4672a5-4918-4383-bae1-a0278aa9081e → 01a08fd2-4c66-7930-b33d-d28ebcba6893 (ready)
evals kitaru import: 1 of 1 thread(s) imported from decode-prod into http://localhost:8000.
$ uv run kitaru session get 01a08fd2-4c66-7930-b33d-d28ebcba6893
{'status': 'failed', 'error': 'pydantic_ai.exceptions.ModelHTTPError: status_code: 503 ...',
 'replay_readiness': {'level': 'ready', ...}, 'llm_call_count': 1, 'imported_from': 'opik'}

$ uv run kitaru importer get opik --output json
{'latest_version': 3}
$ uv run kitaru importer version get opik@3 --output json
{'display_version': 'sha-612b73107825', 'source': {'type': ..., 'blob_id': ..., 'entrypoint': ...}}
# matches sha256(importers/opik_importer.py)[:12] computed above — the registered blob is the tree
# file; the live import through pid 6624 is the real proof it has no sibling import (a blob carrying
# one would have exited 1 on this exact path, per break path 1 / round 1).

# --- break path 3: read the referent, not just the diff ---
$ grep -n "def desired_versions\|is_file()" scripts/bootstrap_kitaru.py
344:def desired_versions(*, repo: Path, decode_bin: Path, harness_home: Path) -> list[VersionSpec]:
554:    if not script.is_file():
673:    if not entrypoint.is_file():
# desired_versions (line 344) reads DECODE_BIN/HARNESS_HOME from decode.remote.image (line 352) with
# no stat on either; main() (line 673) stats only the LAPTOP `decode` entrypoint. The four fixed
# comments describe this accurately.

$ make pre-commit
2945 passed, 1 skipped in 69.92s   # ruff format-check 349 files, ruff check clean

$ git status --short
# identical scope to pre-testing snapshot; no leftover break-test artifacts (importers/opik_spans.py
# absent, importers/opik_importer.py diff line count unchanged at 87 insertions/7 deletions vs HEAD)
```

**Other issues found**
- Carried forward from round 1, not the SWE's to fix: `tasks/153-mint-control-plane-key-close-pending-gate.md:69` is a still-pending task whose spec instructs an operator to run the deleted `scripts/register_kitaru_agent.py --sandbox-mode none --skip-bin-check` — flagged for the PA to re-groom against `scripts/bootstrap_kitaru.py`.
- Worker hygiene: my own foreign-cwd Worker (pid 6624) was killed after use; the SWE's pre-existing foreign-cwd Worker (pid 962, `/private/tmp/qa-165-foreign-cwd`) was left running, confirmed genuinely live via `lsof` + a 7-second-old heartbeat (not the stale "live: true" the SWE warned about) — left alone for the human's AC7 pass.

**VERDICT: PASS**

Both round-1 FAILs are fixed and independently re-proven: (1) the importer is now self-contained (only non-stdlib import is `kitaru.task.importer`), the two new regression pins are load-bearing (red-proofed live against a reintroduced sibling import, reverted byte-exact), and a live import through a Worker I started myself from a foreign cwd succeeded (`ready`) on a fresh trace; (2) all four dangling-reference fixes now name real, verified symbols (`desired_versions`, its no-stat behavior, the laptop-only entrypoint check) rather than another invented flag. Full suite green (2945/0, 1 pre-existing skip), format/lint clean, `--help` still opik/kitaru-free, no scope creep in the diff. AC7 remains `[HUMAN]` and open, as scoped.
