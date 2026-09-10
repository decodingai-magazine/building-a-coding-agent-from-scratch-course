---
id: 165-kitaru-local-bootstrap-import-cohort
feature: evals-v2
status: pending
---

# Kitaru on the local OSS server: bootstrap script, `evals kitaru import`, `evals kitaru cohort`

Tags: `evals`, `kitaru`, `[HUMAN]`
Depends on: 155, 161
Blocks: 166

## Scope
ADR-0022 §10–§11: any Kitaru Server is one URL; registrations are one idempotent script; two thin commands join Opik to Kitaru by the decode session id. e2e against `kitaru login --local` (server image + `postgres:16-alpine` via docker compose on `localhost:8000`). The managed workspace is deactivated (`subscription_ended`) and becomes a URL switch later.

## Acceptance criteria
- [ ] `scripts/bootstrap_kitaru.py [--server URL] [--dry-run]` (targets `KITARU_API_URL`, 0.24+ idempotent registration): creates agent `decode` when absent; registers agent versions from the spec in today's `scripts/register_kitaru_agent.py` (its pure helpers `build_run_env`/`register_argv` move here; that script and its tests are deleted; `tests/unit/scripts/test_bootstrap_kitaru.py` covers the moved helpers + the orchestration with a fake `kitaru` subprocess); registers importer `opik` (`--script importers/opik_importer.py --entrypoint parse --provider opik`) and every `evaluators/*.py` (`--entrypoint evaluate`); prints a table `kind · name@version · id`; `--dry-run` prints the exact `kitaru …` argv it would run. Re-running changes nothing and prints the same table.
- [ ] `make kitaru-local` = `uv run kitaru login --local` + `uv run python scripts/bootstrap_kitaru.py --server http://localhost:8000`, then prints the two lines to export (`KITARU_API_URL=http://localhost:8000`, `KITARU_AGENT_ID=<uuid>`); `.env.example` documents both servers (`# local OSS server (make kitaru-local)` / `# managed workspace`) and repeats that `KITARU_API_URL` must be exported, not merely in `.env`.
- [ ] `python -m evals kitaru import <trace-id>... [--thread <session-id>]`: Opik SDK fetches each trace + spans, expands a trace id to its whole thread, writes the `opik@N` importer envelope `{schema_version, workspace, project, traces: [{trace, spans}]}` to `.decode/kitaru-imports/<thread>.json`, shells `kitaru session import <file> --importer opik@<v> --agent decode@<v> --media-type application/json --tag regression-case --wait`, prints `thread_id → kitaru session id (readiness)`; skips a thread whose Session already exists (looked up by `session_name` via `kitaru session list --output json`, client-side filter), saying so. Envelope builder is pure and unit-tested against the importer's own parser (round-trip on a fixture export).
- [ ] `python -m evals kitaru cohort from-experiment <experiment-name> [--cohort decode-benchmark-failures]`: reads the Opik experiment's items, keeps `reward == 0` (not `scoring_failed`), resolves each trial's Kitaru Session by `session_name` = the trial's decode `session_id` (from the trace metadata/summary; `kitaru_session_id` used as a shortcut when present), creates the cohort if absent then `kitaru cohort version create` with the session ids, prints the version reference and the session count; refuses with one line when the experiment's `experiment_config.kitaru_agent_id` is `None` or no Session resolves.
- [ ] Both commands: keyless-skip on missing `OPIK_API_KEY` / `KITARU_API_URL` (exit 0, one line); `--help` opik/kitaru-free; unit tests with a fake Opik client + fake `kitaru` subprocess (argv asserted).
- [ ] `running_the_code/06_evals_replays.md`: new §0 "Pick a server" (local via `make kitaru-local` vs managed, one URL), `scripts/bootstrap_kitaru.py` replaces the manual register commands, the two new commands in the flow; `07_evals_replays_deploy.md` notes the Modal Worker needs a server reachable from Modal (the managed one, when it resumes).
- [ ] [HUMAN] e2e on the local server: `make kitaru-local` → export the two vars → `make eval-benchmark ARGS='--task 001-find-and-replace --trials 2'` → `kitaru session list --agent decode` shows two Sessions named by the trials' session ids → `python -m evals kitaru cohort from-experiment <job>` (force one failure if both pass: `--task 018-git-bisect-revert --trials 1`) → `kitaru replay create <session> --agent decode@<v> --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' --evaluator decode-bad-request-400@1` settles on a laptop Worker. Ids logged here.

## Out of scope
- `evals kitaru sync-scores`; outcome re-verification on replays; ephemeral/Modal Workers against the local server; the managed-workspace control-plane key (task 153).

## Log
### [PA] 2026-09-10 — Grooming
The importer parser exists and is unit-tested; only the acquisition half was missing (its docstring says "see scripts/ once wired"). The SDK path avoids the `opik export` directory dance and produces the same `{trace, spans}` shape. kitaru 0.26 CLI verified: `login --local`, `experiment run start --baseline-evaluation-mode`, `worker start --claim`, `importer register/version`, `evaluator register`; `session list` carries no payloads since 0.24 (names still do). The adapter exposes no Session-id accessor, so `session_name` (= decode session id) is the join everywhere.
