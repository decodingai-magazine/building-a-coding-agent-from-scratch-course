---
id: 105
feature: evals
status: pending
---

# Benchmark task format, loader, oracle-sanity harness, dataset sync

Depends on: 103. Implements ADR-0017 §2,5.

## Scope

**Task folder contract** — `evals/benchmark/tasks/<NNN>-<slug>/`:

- `task.yaml`: `id`, `prompt` (what the agent sees — never mentions verify assets), `max_steps`,
  `difficulty` (`easy|medium|hard`), `tags` (list), optional `judges` (list of
  `{name, task_introduction, evaluation_criteria}` for G-Eval add-ons).
- `setup/` — files copied verbatim into the fresh Workspace before the run; optional
  `setup/setup.sh` executed IN the sandbox after seeding (builds git history, sqlite DBs, mixed
  encodings — things that can't live as committed files).
- `verify/` — the hidden oracle: `verify.sh` (exit 0 / prints PASS = success) + optional hidden
  test files. Injected into the Workspace ONLY at grade time; the agent never sees them and the
  prompt never names them.
- `solution/` — a committed gold solution (a set of files overlaying `setup/`), used ONLY by the
  oracle-sanity harness below; never enters an agent run.

**`evals/harness/task_loader.py`** — `BenchmarkTask` model (pydantic) + `load_benchmark_tasks()`
scanning the folder, validating the contract (missing verify.sh / empty prompt / bad difficulty
fail loudly).

**Oracle-sanity harness** — `tests/unit/evals/benchmark/test_oracle_sanity.py`, parametrized over
every authored task: in a temp dir, (a) seed `setup/` (+ run `setup.sh` host-side with bash),
overlay `solution/`, inject `verify/`, run `bash verify.sh` → MUST exit 0; (b) seed `setup/` only,
inject `verify/`, run → MUST exit non-zero. Keeps every oracle honest as tasks are authored
(108–110). verify.sh may only use bash + python + git + sqlite3 so it runs identically host-side
and in the sandbox image.

**Dataset sync** — `evals/harness/datasets.py`:
`opik.Opik().get_or_create_dataset("decode-benchmark-v1")` + `sync_benchmark_dataset()` inserting
one item per task (`task_id`, `difficulty`, `tags`; items auto-deduplicate). Exposed as
`python -m evals sync`. Dataset names are code constants, not settings.

**Tests**: loader validation (valid fixture task under `tests/unit/evals/fixtures/`, plus each
malformed shape rejected); dataset sync with a mocked `opik.Opik` client asserting item payloads.

## Acceptance Criteria

- [ ] Loader parses a valid task folder and rejects each contract violation with a clear error.
- [ ] Oracle-sanity harness runs both directions (solution→PASS, untouched setup→FAIL) against the
      test fixture task.
- [ ] `python -m evals sync --benchmark` upserts items (mock-verified offline).
- [ ] Contract documented in `evals/benchmark/tasks/README.md` (the folder IS the docs).
- [ ] `make ci` green.

## Out of scope

- The 20 real tasks (108–110). Running anything in docker (106).

## Log
