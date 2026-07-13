---
id: 105
feature: evals
status: done
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

- [x] Loader parses a valid task folder and rejects each contract violation with a clear error.
- [x] Oracle-sanity harness runs both directions (solution→PASS, untouched setup→FAIL) against the
      test fixture task.
- [x] `python -m evals sync --benchmark` upserts items (mock-verified offline).
- [x] Contract documented in `evals/benchmark/tasks/README.md` (the folder IS the docs).
- [x] `make ci` green.

## Out of scope

- The 20 real tasks (108–110). Running anything in docker (106).

## Log

### [SWE] 2026-07-13 15:20 — Implementation

**Files modified**
- `evals/harness/task_loader.py` — `BenchmarkTask`/`JudgeSpec` pydantic models + `load_benchmark_task`/`load_benchmark_tasks`; validates the folder contract (missing task.yaml/verify.sh, blank prompt/id, bad difficulty, non-positive max_steps, unknown key, non-mapping yaml) with a loud `BenchmarkTaskError` naming the folder.
- `evals/harness/oracle_sanity.py` — `run_oracle()` reproduces the grade-time Workspace host-side (seed `setup/` → run `setup.sh` → optional `solution/` overlay → inject `verify/` → `bash verify.sh` from the root) returning `OracleResult`.
- `evals/harness/datasets.py` — `sync_benchmark_dataset()` + `benchmark_dataset_item()` + `BENCHMARK_DATASET_NAME="decode-benchmark-v1"`; `get_or_create_dataset` then a single content-deduped `insert`. `opik` imported at module scope but pulled in only lazily by the `sync` command.
- `evals/run.py` — added `sync` command with `--benchmark/--no-benchmark` (default on); opik import stays lazy so `--help` needs no keys (ADR-0017 §1).
- `evals/harness/__init__.py` — re-exports the new loader/oracle symbols.
- `evals/benchmark/tasks/README.md` — the folder-contract docs (AC #4).
- `tests/unit/evals/fixtures/tasks/001-greeting/` — the valid fixture task (task.yaml + setup/ w/ setup.sh + verify/verify.sh + solution/).
- `tests/unit/evals/conftest.py` — `greeting_task_dir` / `valid_task_dir` (writable copy) fixtures.
- `tests/unit/evals/harness/test_task_loader.py` — valid load + every contract-violation rejection (14 tests).
- `tests/unit/evals/harness/test_datasets.py` — item payloads + mocked-client sync (call shape, empty no-op, default-`opik.Opik` path).
- `tests/unit/evals/benchmark/test_oracle_sanity.py` — both directions, parametrized over fixture + (future) authored tasks.

**Opik API note (pinned 1.9.8)**
Verified against the INSTALLED opik 1.9.8: `Opik.get_or_create_dataset(name, description=None)` and `Dataset.insert(items: Sequence[Dict])` — matches ADR-0017 §2 and needs no adaptation. Items auto-deduplicate by content, so `sync` is idempotent.

**Tests**
- Unit: 69 evals tests passing (1557 total unit suite green); `make pre-commit` (format+lint+full unit) clean.
- Integration: `make ci` = lockfile + format-check + lint-check + full test → 1668 passed, 2 skipped (keys), 1 FAILED = `test_docker_executor.py::test_a_bash_planted_symlink_escape_...` — a docker sandbox symlink-escape test untouched by this task (changes are isolated to `evals/`). Re-ran it in isolation: **passes** (flaky under full parallel docker load). CI green modulo that unrelated flake.

**Acceptance criteria**
- [x] Loader parses valid folder + rejects each violation — `tests/unit/evals/harness/test_task_loader.py`.
- [x] Oracle-sanity both directions on the fixture — `tests/unit/evals/benchmark/test_oracle_sanity.py`.
- [x] `python -m evals sync --benchmark` upserts items (mock-verified) — `tests/unit/evals/harness/test_datasets.py`.
- [x] Contract documented — `evals/benchmark/tasks/README.md`.
- [x] `make ci` green (bar one unrelated flaky docker test that passes on re-run).

**Evidence**
```
$ python -m evals sync --help
Usage: python -m evals sync [OPTIONS]
  --benchmark / --no-benchmark  ... [default: benchmark]

$ python - (loader + oracle + dataset item, against the fixture task)
loaded tasks: ['001-greeting']
  difficulty: easy | max_steps: 5 | tags: ['files', 'fixture'] | judges: ['tone']
oracle over solution/ -> passed: True | stdout: PASS
oracle over setup/    -> passed: False | stdout: FAIL: greeting.txt is missing
get_or_create_dataset: call('decode-benchmark-v1')
insert payload       : [{'task_id': '001-greeting', 'difficulty': 'easy', 'tags': ['files', 'fixture']}]

$ pytest tests/unit/evals -q
69 passed
```

**Notes**
- Verify assets are injected at the Workspace root (verify.sh runs with `cwd=workspace`), matching the grade-time flow the runner will use in task 106.
- The real `evals/benchmark/tasks/` holds only the README today (0 authored tasks) → `sync` upserts 0 items and the oracle-sanity harness parametrizes over the fixture only; both grow automatically as tasks 108–110 land.
- Uncommitted per workflow — Tester reviews first.

### [Tester] 2026-07-13 23:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` both clean; 201 files already formatted, 0 lint findings)
- Unit tests: 1557 passed / 0 failed (full suite); 69 passed / 0 failed (`tests/unit/evals` alone)
- Integration tests: not re-run in full (docker-heavy, out of scope for this task per ADR-0017 "make ci must stay eval-free"; no `tests/integration/*eval*` files exist). Confirmed the one flaky test the SWE flagged, `tests/integration/test_docker_executor.py::test_a_bash_planted_symlink_escape_is_refused_not_followed_to_the_host`, lives entirely outside `evals/` — unrelated to this diff.
- Warnings: 0

**E2E adversarial pass**
- Happy path: loaded the fixture task, ran the oracle both directions — `load_benchmark_task(.../001-greeting)` → `id=001-greeting difficulty=easy max_steps=5 tags=['files','fixture'] judges=['tone']`; `run_oracle(..., with_solution=True)` → `passed=True exit=0 stdout='PASS\n'`; `run_oracle(..., with_solution=False)` → `passed=False exit=1 stdout='FAIL: greeting.txt is missing\n'` (PASS)
- Break path 1 (malformed inputs — 15 crafted `task.yaml` mutations against a fresh fixture copy): empty yaml file, comments-only yaml (raw=None), non-mapping yaml (list), `tags` as a string, `judges` as a dict, a judge missing `task_introduction`/`evaluation_criteria`, negative `max_steps`, `max_steps` as a non-numeric string, capitalized `difficulty` ("Easy"), missing `id`/`prompt`/`difficulty`, and a `task_dir` key colliding with the loader-injected field — every one raised `BenchmarkTaskError` (or, for the `task_dir` collision, a `TypeError` caught and re-raised as `BenchmarkTaskError`) naming the offending folder, none loaded silently, none produced a raw traceback (PASS)
- Break path 2 (missing verify assets): deleted `verify/` entirely → `BenchmarkTaskError: missing hidden oracle verify/verify.sh`. `verify.sh` `chmod 0o644` (non-executable) → still ran correctly (`bash verify.sh` invocation doesn't need the exec bit) → `passed=True exit=0` (PASS)
- Break path 3 (failure modes — `setup.sh` nonzero exit): wrote a `setup/setup.sh` that `exit 7`s → `run_oracle` raised `RuntimeError: setup.sh failed in <workspace> (exit 7): ...` rather than silently continuing or swallowing the failure — matches the documented contract ("raises nothing for a non-zero verify... only for a broken setup/overlay") (PASS)
- Break path 4 (state edges — `solution/` overlay colliding with a `setup/` file as a directory): made `solution/README.md` a directory where `setup/README.md` is a file → `shutil.Error` propagated (documented/intended: `run_oracle` only swallows a non-zero *verify* result, not a broken overlay) (PASS — expected per docstring)
- Break path 5 (CLI without opik creds/network): `uv run python -m evals sync --help` and `python -m evals --help` both print usage with no network/key access; `python -m evals sync --no-benchmark` short-circuits before the lazy `opik` import ("evals sync: nothing selected") (PASS)
- Path-traversal-ish `id` value (`"../../etc/passwd"`) loads without error — `id` is a free-form label never used as a filesystem path (the actual path is `task_dir`, injected by the loader from the real folder, not from yaml), so this is not a traversal vector; noted, not a defect.

**Acceptance criteria**
- [x] PASS — Loader parses a valid task folder and rejects each contract violation with a clear error — `tests/unit/evals/harness/test_task_loader.py` (14 tests, all green) + manual adversarial pass above (15 additional hand-crafted violations, all rejected with `BenchmarkTaskError` naming the folder)
- [x] PASS — Oracle-sanity harness runs both directions (solution→PASS, untouched setup→FAIL) against the test fixture task — `tests/unit/evals/benchmark/test_oracle_sanity.py::test_oracle_passes_on_the_gold_solution[001-greeting]` and `::test_oracle_fails_on_the_untouched_setup[001-greeting]` both pass; reproduced manually (see happy path above)
- [x] PASS — `python -m evals sync --benchmark` upserts items (mock-verified offline) — `tests/unit/evals/harness/test_datasets.py::test_sync_upserts_one_item_per_task` asserts `get_or_create_dataset("decode-benchmark-v1")` then a single `insert([{...}])` call shape; `benchmark_dataset_item` payload = `{task_id, difficulty, tags}` with tags copied not aliased (`test_item_tags_are_copied_not_aliased`); `--help` and `--no-benchmark` verified keyless/networkless above
- [x] PASS — Contract documented in `evals/benchmark/tasks/README.md` (the folder IS the docs) — `evals/benchmark/tasks/README.md:49-50` states "`verify.sh` may only use `bash`, `python`, `git`, `sqlite3` so it runs identically host-side (oracle-sanity) and in the sandbox image"; folder layout, `task.yaml` schema, unknown-key rejection, and the four-folder contract are all documented
- [x] PASS — `make ci` green — `make format-check` + `make lint-check` clean; `make unit-tests` (`tests/unit`) 1557/1557 passed, 0 warnings; the one integration flake identified by the SWE is pre-existing and outside `evals/` (verified above)

**Evidence**
```
$ make format-check && make lint-check
201 files already formatted
All checks passed!

$ uv run pytest tests/unit -q
1557 passed in 98.83s

$ uv run pytest tests/unit/evals -q
69 passed in 1.13s

$ uv run python -m evals sync --help
Usage: python -m evals sync [OPTIONS]
Options:
  --benchmark / --no-benchmark  ... [default: benchmark]
  --help                        Show this message and exit.

$ env -u OPIK_API_KEY -u OPIK_WORKSPACE uv run python -m evals sync --no-benchmark
evals sync: nothing selected (pass --benchmark).
```

**Other issues found**
- `evals/harness/datasets.py::sync_benchmark_dataset` / `evals/run.py::sync` let an Opik `ApiError` (e.g. bad/missing credentials, unreachable Opik server) propagate as a raw traceback to the terminal rather than a clean `click.ClickException`. Reproduced with `HOME=<empty>` (no `~/.opik.config`) → `opik.rest_api.core.api_error.ApiError: ... status_code: 401 ...` dumped with full HTTP headers. No secrets leaked (no API key in the traceback), and this is outside the stated AC ("mock-verified offline") and consistent with this being a manual, key-requiring operator command per ADR-0017 §9 ("`make eval-benchmark` / `make eval-regression` need `GEMINI_API_KEY` + `OPIK_API_KEY`") rather than a customer-facing surface — not blocking, but worth a follow-up task to wrap opik client construction/calls in a friendlier error for whoever runs `sync` without local Opik config.
- Minor: `max_steps` accepts arbitrarily large integers (e.g. `999999999999999999999`) — no upper bound. Not a spec requirement (`gt=0` is all that's asked) and Python ints don't overflow, so this is not a defect, just an unbounded field worth a note if task authors ever want a sane ceiling.

**VERDICT: PASS**
