---
id: 065-runtime-test-store-isolation
feature: kitaru-runtime
status: done
---

# Harden runtime test isolation: never touch a developer's real ZenML store

Tags: `runtime`, `test`, `infra`
Depends on: #061, #064
Blocks: —

Test-infra only — **no production code change.** The runtime tests' Kitaru-store isolation
(`tests/unit/decode/runtime/conftest.py`) is **order-dependent**: if a non-isolated ZenML-touching
test (e.g. `tests/unit/decode/test_cli.py`) runs *between* a secret-creating runtime test and a later
`create_secret`/`get_secret`, the isolation breaks and the call hits the **developer's real ZenML
store**, polluting it with `decode-llm-creds` (and failing the secret-store / 061-proxy tests). The
deterministic as-run suite (`make ci`) is clean — runtime/ sorts before `test_cli.py`, and
`pytest-randomly` is not installed — but it is a latent footgun: any reorder (a future `pytest-randomly`,
running a subset, CI sharding) can leak into and corrupt real infra. The Tester (task 064 round 2)
reproduced it and recommended the fix.

## Scope

- **Make the isolation per-test and robust, not set-once.** The fixture that pins ZenML to a
  `tmp_path` store (`isolated_kitaru_store` or equivalent) must (re)assert the isolated store for
  **every** runtime test that touches secrets/executions — so a non-isolated test mutating ZenML's
  global config/active stack *before* it cannot let a `get_secret`/`create_secret` fall through to the
  real store. Prefer an **autouse** fixture in the runtime conftest that re-pins the store per test
  (e.g. set + restore `ZENML_CONFIG_PATH`/`HOME` and reset the relevant ZenML singleton per test),
  rather than relying on a session/module-scoped one that a sibling test can disturb.
- **Defense in depth on secret names + teardown.** Tests create secrets under a **unique per-test
  name** (or delete the secret in teardown), so even a hypothetical fall-through cannot collide with
  or leave behind a real-store `decode-llm-creds`. The production default `runtime_secret_name`
  (`decode-llm-creds`) is unchanged; only the tests' chosen names change.
- Apply the same hardening to the **pre-existing 061** proxy test that shares the hazard.
- Keep `filterwarnings=["error"]`, 0 warnings; do not weaken any existing assertion.

## Acceptance criteria

- [x] **Adverse-order proof:** a test (or a documented forced-order run) that interleaves a
      non-isolated ZenML-touching test (`test_cli.py`) BETWEEN the secret-creating runtime tests and
      `test_run_command.py`/`test_secret_store_config.py` passes, and the developer's real ZenML store
      is **never** written (no `decode-llm-creds` leaks). Reproduce the Tester's original failing order
      and show it green. — `tests/integration/test_runtime_store_isolation.py` (subprocess, sandboxed)
      + manual run of the exact trio under the developer's REAL live server (90 passed, real store
      secrets unchanged — see Log Evidence).
- [x] Runtime secret-touching tests use a unique-per-test secret name and/or delete created secrets in
      teardown — verified no leftover artifact in any store after the suite. — `runtime_secret_name`
      fixture (`decode-test-creds-<uuid>` + best-effort teardown delete).
- [x] The isolation is re-asserted per test (autouse), so it holds regardless of what ran before;
      `pytest tests/unit/decode/runtime/ -p no:randomly` and a forced adverse order both pass. —
      fixtures registered at the rootdir conftest (always in scope) + per-test store-isolation assert.
- [x] No production code changed (diff is `tests/` + this task file); existing runtime/061/064 tests
      stay green. — `git diff --name-only -- src/` empty; `git diff --name-only -- docs/` empty.
- [x] `make ci` green, 0 warnings, `uv lock --check` clean. — 1032 passed; lock clean.

## User stories

### Story: A contributor runs tests in any order without risk
1. A contributor runs a subset, or adds `pytest-randomly`, reordering the suite.
2. The runtime tests still isolate their Kitaru store per test.
3. No test ever reads or writes the contributor's real ZenML store.

## Out of scope
- Any production behavior change (this is purely test isolation).
- Changing the `runtime_secret_name` default or the secret-store/proxy features themselves.

## Log

### [SWE] 2026-06-29 — Implementation

**Reproduced the Tester's failing order first (with a scratch HOME so the real store was never at risk)**
`pytest test_secret_store_config.py test_cli.py test_run_command.py -p no:randomly`, with
`HOME`/`ZENML_CONFIG_PATH` pointed at a throwaway store, reproduced it: **5 failed** and the throwaway
`zenml.db` had a leaked `decode-llm-creds` (`EntityExistsError` / `Secret already exists` in
`test_run_command.py`). Confirmed the leak object: the active `SqlZenStore` URL during the
`run_command` tests was the throwaway "real" store, not the per-test `tmp_path`.

**Root cause (NOT what the task hypothesised; nailed it empirically).** The task framing assumed the
isolation was "set-once / session-scoped and a sibling disturbs it." It is already a **function-scoped
autouse** fixture that resets the ZenML singletons + re-pins `ZENML_CONFIG_PATH` per test. `--setup-show`
proved the real fault: in the failing order the runtime conftest's `isolated_kitaru_store` **does not run
at all** for the second runtime file (only 9 of 21 expected setups; `inline_wait_resolver` likewise
errored "fixture not found" when `test_hitl.py` was the 2nd runtime file → 10 errors). Mechanism:
under `--import-mode=importlib`, a per-package `conftest` is reliably applied only when its tests are
collected **contiguously**; a non-runtime file collected *between* two runtime files
(`test_cli.py`, or — under `pytest-randomly` running unit+integration together — any non-runtime file)
de-associates `tests/unit/decode/runtime/conftest.py` from the second runtime file. I falsified two
wrong leads on the way (it is NOT the `from …conftest import make_scripted_agent` dotted import, and NOT
the missing `runtime/__init__.py` — adding/removing both left the count at 9). `tests/unit/decode/`
**cannot** be made a package (`__init__.py` there would shadow the real `decode` source package).

**The fix (test-infra only).**
1. **Relocated the runtime fixtures to the rootdir `tests/conftest.py`** — the only ancestor always
   in scope for every collected test, so its autouse + named fixtures apply in **any** collection
   order (proven: a rootdir autouse fires 21/21; a decode-level one is robust only within `decode/`
   and would still break if an integration file interleaves under full-suite randomization). The
   fixtures live in `tests/support/runtime_fixtures.py` and are re-exported by the rootdir conftest;
   `isolated_kitaru_store` is autouse but **gated** to the unit runtime package
   (`request.path.parent.name == "runtime"`) so it is a pure no-op (imports nothing) for the rest of
   the suite. The integration capstone keeps its own self-contained isolation (parent `integration`,
   excluded by the gate; its same-named fixture also overrides mine for those tests).
2. **Moved `make_scripted_agent` out of the conftest** into `tests/support/runtime_agents.py` (good
   practice — don't import helpers from a conftest); updated the 4 importers.
3. **Defense in depth — unique per-test secret name + teardown delete + an isolation tripwire.** New
   `runtime_secret_name` fixture yields `decode-test-creds-<uuid>`, wires it through BOTH
   `settings.runtime_secret_name` and the `RUNTIME_SECRET_NAME` env (so the in-flow `reload_settings`
   keeps it), `_assert_store_isolated_under(tmp_path)` BEFORE any secret op (loud failure if isolation
   regresses — the AC-endorsed "active store path is the tmp one" guard), and best-effort
   `delete_secret` on teardown. Adopted in `test_secret_store_config.py`, `test_credentials_proxy.py`
   (the pre-existing 061 test), and `test_run_command.py` — the hardcoded `decode-llm-creds` is gone
   from the tests, so even a hypothetical fall-through can never collide with or leave a real-store
   `decode-llm-creds`. Production default `runtime_secret_name` is unchanged.
4. **Guards:** `tests/unit/decode/runtime/test_store_isolation.py` (fast, in-process: active store is
   the tmp SQLite store + a secret round-trips only there) and
   `tests/integration/test_runtime_store_isolation.py` (the definitive one: runs the Tester's exact
   failing trio in a **subprocess** sandboxed to a `tmp_path` "real" store, asserts exit 0 AND no
   `decode-*` secret leaked — it would fail again if the autouse de-association recurred).

**Files modified**
- `tests/conftest.py` — re-export the runtime fixtures from `support.runtime_fixtures` so they
  register at the always-in-scope rootdir conftest (with a comment on the importlib reason).
- `tests/unit/decode/runtime/conftest.py` — **deleted** (contents moved; this dir is where the
  collection-order fragility lived).
- `tests/support/runtime_fixtures.py` — **new**: `isolated_kitaru_store` (gated autouse),
  `runtime_secret_name`, `inline_wait_resolver`, `WaitRecorder`, + store-isolation guard/teardown.
- `tests/support/runtime_agents.py` — **new**: `make_scripted_agent` (out of the conftest).
- `tests/unit/decode/runtime/{test_flow,test_credentials_proxy,test_run_command,test_secret_store_config}.py`
  — import `make_scripted_agent` from `support`; use the unique `runtime_secret_name` fixture.
- `tests/unit/decode/runtime/test_store_isolation.py` — **new** in-process guard.
- `tests/integration/test_runtime_store_isolation.py` — **new** subprocess forced-order guard.

**Tests**
- Unit: 1012 passing, 0 failing (was 1010 baseline + 2 new store-isolation guards).
- Integration: 20 passing (was 19 + 1 new forced-order guard).
- `make ci`: 1032 passed in 114s, 0 warnings (`filterwarnings=["error"]`); `uv lock --check` clean.
- No `src/` change (`git diff --name-only -- src/` empty); no `docs/` change.

**Evidence**
```
# BEFORE (repro, scratch HOME): 5 failed; throwaway zenml.db secret table = ['decode-llm-creds']
# AFTER, scratch HOME:
$ make ci                       → 1032 passed in 114.42s; scratch store secrets: []
$ pytest tests/unit/decode/runtime/ -p no:randomly  → 46 passed
$ pytest <secret_store, test_cli, run_command> -p no:randomly  → 65 passed; isolated setups 21/21
$ pytest <secret_store, test_cli, test_hitl> -p no:randomly     → 69 passed (named fixture OK)

# AFTER, the developer's REAL live ZenML server ambient (http://127.0.0.1:8383):
$ # secrets BEFORE: ['wiki-token', 'webhook-token']
$ pytest <secret_store, test_cli, run_command> tests/unit/decode/runtime/ -p no:randomly  → 90 passed
$ # secrets AFTER:  ['wiki-token', 'webhook-token']   → decode-* leaked: []  (real store untouched)
```

**Notes**
- The fix is test-infra only; no production code or docs touched (confirmed by the empty `src/` and
  `docs/` diffs). DO NOT commit — handing to the Tester first.
- The runtime conftest deletion is the substantive structural change: it moves the long teardown
  comment (SQLite engine dispose + idle event-loop close, tasks 059/064) into `support/runtime_fixtures.py`
  verbatim, gated so it only runs for runtime tests.

### [Tester] 2026-06-29 18:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` 144 files formatted; `make lint-check` all checks passed; `uv lock --check` clean)
- Unit tests: 1012 passed / 0 failed (`make unit-tests`, 57.99s)
- Integration tests: 20 passed / 0 failed (`make integration-tests`, 41.08s) — includes the new `test_runtime_store_isolation.py`
- Warnings: 0 (suite runs under `filterwarnings=["error"]`, pyproject.toml:108 — any warning would fail a test; 1032 passed ⟹ 0)

**E2E adversarial pass** (the whole task is store isolation, so all break paths attack the leak path; every subprocess ran under a throwaway `HOME`/`ZENML_CONFIG_PATH` so a regression could not reach the developer's real store)
- Happy path: `pytest tests/unit/decode/runtime/ -p no:randomly` → 46 passed (PASS)
- Break path 1 (collection order — Tester's original failing trio): `HOME=<scratch> ZENML_CONFIG_PATH=<scratch> pytest test_secret_store_config.py test_cli.py test_run_command.py -p no:randomly` → 65 passed; throwaway store secret scan = `[]`, decode-* leaks = `[]` (PASS)
- Break path 2 (order — 2nd runtime file = `test_hitl.py`, the historical `inline_wait_resolver` "fixture not found" case): same sandbox → 69 passed; no leak (PASS). Also `test_credentials_proxy → test_cli → test_secret_store_config` → 56 passed, no leak; `test_run_command → tools/test_bash → test_secret_store_config` → 35 passed, no leak (PASS)
- Break path 3 (regression-detection / "does the guard have teeth"): (a) inspected `tests/integration/test_runtime_store_isolation.py:69,73` — asserts `returncode == 0` AND no `decode-*` in the sandboxed config_dir; (b) confirmed the secret table is literally named `secret` and the guard's `_secret_names_in` helper returns `['decode-test-creds-probe123']` when fed a local store holding one → the leak-scan assertion fires on a real leak (PASS). Temporarily simulating the de-association (autouse gate → `False` + dropped the in-fixture asserts) left no persistent leak because the unique-name + best-effort teardown (defense-in-depth #2) cleaned up — demonstrating the belt-and-braces layer; the returncode assertion remains the primary catch for the historical collision/error failure mode.
- Break path 4 (no-op gate for non-runtime tests): a non-runtime test requesting `isolated_kitaru_store` receives `None`; `test_cli.py` → 44 passed in 2.33s (not slowed/altered) (PASS)
- Final leftover-artifact check (real ambient REST store `http://127.0.0.1:8383`, read-only, after the full suite + all experiments): secrets = `['wiki-token', 'webhook-token']`, decode-* leftovers = `[]` (PASS)

**Acceptance criteria**
- [x] PASS — Adverse-order proof, real store never written — `tests/integration/test_runtime_store_isolation.py` passes (1 passed); independently reproduced the trio in a sandboxed subprocess (65/69/56/35 passed across four interleavings), zero `decode-*` in the throwaway store each time; real store unchanged.
- [x] PASS — Unique-per-test secret name + teardown, no leftover — `runtime_secret_name` fixture (`tests/support/runtime_fixtures.py:142`) yields `decode-test-creds-<uuid>`, wired via `settings.runtime_secret_name` + `RUNTIME_SECRET_NAME`, best-effort `delete_secret` on teardown; final real-store scan shows zero `decode-*`.
- [x] PASS — Isolation re-asserted per test (autouse), order-independent — fixtures registered at rootdir `tests/conftest.py:22`; `isolated_kitaru_store` autouse + gated (`runtime_fixtures.py:45,61`); `pytest tests/unit/decode/runtime/ -p no:randomly` → 46 passed and forced adverse orders pass; `_assert_store_isolated_under` tripwire (`runtime_fixtures.py:110`) guards each secret op.
- [x] PASS — No production code changed; existing tests green — `git status --porcelain -- src/ docs/` empty; uncommitted diff is exclusively `tests/` + the task file; runtime/061/064 tests all green.
- [x] PASS — `make ci` green, 0 warnings, lock clean — 1012 unit + 20 integration = 1032 passed, `filterwarnings=["error"]` ⟹ 0 warnings, `uv lock --check` clean.

**Evidence**
```
$ make unit-tests        → 1012 passed in 57.99s
$ make integration-tests → 20 passed in 41.08s  (incl. test_runtime_store_isolation.py)
$ pytest tests/unit/decode/runtime/ -p no:randomly → 46 passed
$ HOME=<scratch> ZENML_CONFIG_PATH=<scratch> pytest \
    test_secret_store_config.py test_cli.py test_run_command.py -p no:randomly
                         → 65 passed; throwaway store decode-* leaks = []
$ real REST store http://127.0.0.1:8383 (read-only, post-suite): ['wiki-token','webhook-token'] — decode-*: []
$ git diff --name-only HEAD -- src/ docs/  → (empty)
```

**Other issues found**
- None blocking. Note (non-blocking, for awareness): the integration guard sandboxes via a fresh `ZENML_CONFIG_PATH`, so its file-scan only catches a fall-through to a *local* store; a hypothetical fall-through to a REST server would instead be caught by the in-process `_assert_store_isolated_under` URL tripwire (loud assert) — so both store types are covered, but by different layers. No change required.
- The `code-review` plugin is enabled in `.claude/settings.json` but is a slash-command plugin not invocable from the Tester agent context; performed the equivalent manual diff review (no defects found — no weakened assertions, all signatures typed, no `print()` in changed code, diff scoped to `tests/`).

**VERDICT: PASS**

### [PA] 2026-06-29 — Acceptance Review (re-review, PR #19, 065 delta)

**VERDICT: ACCEPT**

Acceptance for a test-infra-only task is "the feature still behaves as before; the developer's real
store is never touched." Both confirmed from the user's perspective.

- **Genuinely test-infra only** — `git show cf86260 --name-only` touches only `tasks/065-…` +
  `tests/` (conftest, support/, integration/, unit/runtime/). Zero `src/` and zero `docs/` changes →
  no user-facing behavior change; the runtime still behaves exactly as accepted for 057-064.
- **Adverse-order guard has teeth** — `tests/integration/test_runtime_store_isolation.py` runs the
  Tester's exact failing trio (`test_secret_store_config → test_cli → test_run_command`) in a
  subprocess sandboxed to a throwaway `HOME`/`ZENML_CONFIG_PATH`, and asserts BOTH `returncode == 0`
  AND no `decode-*` secret in the sandboxed store — it would fail again if the isolation regressed.
  Ran the integration suite → 20 passed (incl. this guard + the runtime capstone); ran the 064 unit
  delta → 64 passed.
- **Developer's real store never touched (observed live)** — after my own delta run with the
  developer's real ambient ZenML config, I scanned
  `~/Library/Application Support/zenml/local_stores/default_zen_store/zenml.db` directly:
  `total_secrets=0, decode-*=[]`. The per-test re-pinned isolation + unique `decode-test-creds-<uuid>`
  names + teardown delete held — exactly the "run tests in any order without risk" user story.

Spot-check that 057-062 still hangs together: the runtime capstone (`test_runtime_capstone.py`) and
the `decode run` happy path (`test_run_command.py::test_run_command_prints_the_agents_output`) are
green in the runs above. Did not re-litigate the already-accepted slice.

User satisfaction guaranteed. Hand off to the PR Reviewer.
