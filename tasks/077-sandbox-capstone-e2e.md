---
id: 077-sandbox-capstone-e2e
feature: sandboxing
status: done
---

# Sandbox capstone — offline executor-contract + selection slice, skipif real docker/modal/proxy

Tags: `sandbox`, `test`
Depends on: #076
Blocks: —

This is the living proof for the sandboxing feature (ADR-0011, tasks 071-076), in the style of
`test_milestone1_capstone` / `test_runtime_capstone` / `test_lsp_capstone`: an **always-run offline
slice** (no docker, no modal, no network) plus **`skipif`-guarded real-infra smokes** so `make ci`
stays green without infrastructure. New file `tests/integration/test_sandbox_capstone.py`.

## Scope

- **Always-run offline slice (no infra):**
  - **Executor contract:** drive the `CommandExecutor` seam through `bash` with `LocalExecutor` (and a
    `FakeExecutor` double) — a command round-trips to an `ExecResult` and renders; the `note` field
    surfaces on a simulated timeout; `none`-mode rendering is byte-identical.
  - **Selection swap:** patching `settings.sandbox_mode` makes `bash` select the right executor class
    (docker/modal impls faked so no infra is touched), and the `bash` **description** changes per mode
    (`none` byte-identical; docker persistent-shell paragraph; modal remote-scratch paragraph).
  - **REPL kitaru/sandbox-free:** `none` mode imports no docker/modal sandbox module; `import decode.cli`
    imports no kitaru.
  - **Credential map:** `build_credential_map` resolves templates via a patched `kitaru.get_secret`
    hermetically; empty `DEFAULT_PROXY_RULES` → empty map; no value is logged.
  - **Replay-safety config:** with `sandbox_mode != "none"`, the bypass `_build_runtime_agent` is built
    with the verified `bash` re-execute-on-replay checkpoint config (patched seam).
- **`skipif`-guarded real-infra smokes** (each SKIPS, never fails, when its infra is absent — mirroring
  the LSP `ty`-guarded + runtime local-stack tests):
  - **real docker** (`skipif` daemon unreachable): `DockerExecutor` persistent-shell round-trip (state
    persists across two `run`s; a timeout resets + says so; `aclose` removes the container; observability
    lines emitted).
  - **real modal** (`skipif` no creds): `ModalExecutor` round-trip (fs persists; local tree absent;
    timeout kills the exec not the sandbox; `terminate` on `aclose`).
  - **real docker + proxy** (`skipif` no docker): the full stack — an authenticated outbound call from the
    worker succeeds via injected header though the worker env holds no secret (the credential-boundary
    scan). (May reference/thin-wrap the 075 proxy integration test.)

## Acceptance criteria

- [x] The always-run offline slice passes with **no** docker/modal/network/key and proves: the executor
  contract, the mode→executor selection swap, the per-mode bash description (`none` byte-identical), the
  REPL importing no kitaru/sandbox-impl, the hermetic credential-map resolution, and the replay-safety
  checkpoint config.
- [x] The three real-infra smokes SKIP cleanly when their infra is absent and PASS when present (guarded
  exactly like the LSP/runtime capstones); a run with no infra shows them **skipped**, not failed.
- [x] The capstone is hermetic under `filterwarnings=["error"]` run alone (executors close deterministically;
  no leaked subprocess/async resources) — matching the runtime capstone's disposal discipline.
- [x] `make ci` green with 0 warnings on an infra-less machine (all real-infra tests skipped);
  `make integration-tests` runs the offline slice; `uv lock --check` passes.
- [x] The module docstring documents the feature end-to-end (doubles as documentation), naming which
  boundaries are real vs faked (the executor contract is real via `LocalExecutor`; docker/modal are faked
  offline and real-under-skipif).

## Out of scope

- New product code (all in 071-075).
- A deployed-stack proxy test (local/offline + skipif-docker only, matching the runtime capstone's
  local-stack scope).

## Log

### [SWE] 2026-07-03 — Implementation

**Files modified**
- `tests/integration/test_sandbox_capstone.py` — NEW. The sandboxing capstone: an always-run offline
  slice (13 tests) + 3 `skipif`-guarded real-infra smokes, in the M1/runtime/LSP-capstone style. No
  product code changed (all in 071-075); the capstone consumes it.
- `tasks/077-sandbox-capstone-e2e.md` — status → in-progress; acceptance criteria checked.

**Tests** (`tests/integration/test_sandbox_capstone.py`)
- Offline slice (always-run, no docker/modal/network/key):
  - Executor contract — a real `echo` round-trips the run seam through the real `build_agent` registry +
    real `PermissionGate` + host `LocalExecutor` to a rendered `ExecResult`; the `note` surfaces on a
    simulated docker timeout (stubbed executor); `none`-mode `_render` is byte-identical.
  - Selection swap — `SANDBOX_MODE` → the right executor class (real `select_executor`, inert); `bash`
    routes a command through the docker-selected (stubbed) executor; per-mode `bash` description
    (`none` base; docker/modal append their paragraph), captured as the model sees it.
  - REPL free — subprocess `sys.modules` asserts `none` imports no docker/modal executor module and
    `import decode.cli` imports no kitaru.
  - Credential map — `build_credential_map` resolves `{{ name.key }}` via a patched `kitaru.get_secret`;
    `DEFAULT_PROXY_RULES == []` → `{}`; no resolved value in logs (names only).
  - Replay-safety — `_build_runtime_agent` (spied `KitaruAgent`) gets
    `tool_checkpoint_config_by_name={bash: {"cache": False}}` in docker mode; none-mode has no such kwarg.
- Real-infra smokes (`skipif` on the SAME predicates as the executors' own tests — `docker info`;
  modal creds presence): real `DockerExecutor` persistent-shell round-trip (state persists, timeout
  resets + notes, `aclose` removes the container, observability lines); real `ModalExecutor`
  remote-scratch round-trip (fs persists, local tree absent, timeout kills exec not sandbox, terminate
  on `aclose`); real docker Credential-Proxy boundary (injected header ARRIVES, worker env holds no
  secret) — a lean slice of the 075 topology, all torn down in `finally`.

**Acceptance criteria** — all 5 verified (see Evidence):
- [x] offline slice passes with no infra/key — 13 passed with `GEMINI_API_KEY` unset + docker unreachable
  + modal creds hidden.
- [x] the 3 real-infra smokes SKIP cleanly when absent, PASS when present.
- [x] hermetic under `filterwarnings=["error"]` run alone — 16 passed alone, infra up.
- [x] `make ci` green infra-less (all real-infra skipped); `uv lock --check` passes.
- [x] module docstring documents the feature end-to-end, naming REAL vs FAKED boundaries.

**Evidence**
```
# offline slice, NO infra + NO key (GEMINI_API_KEY unset, docker unreachable, modal hidden)
$ env -u GEMINI_API_KEY -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET DOCKER_HOST=unix:///nonexistent \
    uv run pytest tests/integration/test_sandbox_capstone.py -k "not real" -q
13 passed, 3 deselected in 4.76s

# whole file, infra hidden → real-infra smokes SKIP (never fail)
$ ... (docker unreachable + modal creds hidden) uv run pytest .../test_sandbox_capstone.py -rs -q
13 passed, 3 skipped in 3.63s
  SKIPPED test_real_docker_persistent_shell_contract   — the docker daemon is not reachable
  SKIPPED test_real_modal_remote_scratch_contract      — modal account credentials are not present
  SKIPPED test_real_docker_credential_proxy_boundary   — the docker daemon is not reachable

# real-infra smokes FOR REAL (docker up + modal creds present)
$ uv run pytest .../test_sandbox_capstone.py -k "real_docker" -q   → 2 passed
$ uv run pytest .../test_sandbox_capstone.py -k "real_modal"  -q   → 1 passed
  cost hygiene: 0 decode containers/networks left; modal Sandbox.list() → NONE (clean)

# hermetic alone under filterwarnings=["error"], infra up
$ uv run pytest tests/integration/test_sandbox_capstone.py -q     → 16 passed in 15.81s

# make ci on an infra-less machine (docker unreachable + modal hidden + no key)
$ ... make ci   → 1228 passed, 22 skipped in 146.37s   (uv lock --check clean; format/lint clean)
$ uv lock --check → Resolved 149 packages   (clean)

# full suite WITH real infra (docker up + modal present)
$ make integration-tests → 56 passed in 103.98s   (no container/sandbox/network leak)
$ make pre-commit        → 1194 passed
```

**Notes**
- No product code touched — the capstone only consumes 071-075. Task 076's docs
  (AGENTS.md/README.md/.env.example/MODAL_MODELS.md) were left untouched.
- Cross-test import is not viable here (importlib mode, no `tests/integration/__init__.py`, no existing
  precedent), so the `skipif` predicates are defined locally byte-for-byte identical to the executors'
  own tests (the established per-file convention every sandbox integration test already follows), and the
  proxy smoke re-implements a *lean* slice of the 075 topology rather than the whole file.

### [Tester] 2026-07-03 14:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 163 files formatted; `ruff check` → all
  checks passed; `uv lock --check` → resolved 149 packages)
- Unit tests: within the 1228-passed infra-less suite (no failures)
- Integration tests: PASS — 56 passed infra-up; 1228 passed / 22 skipped infra-less
- Warnings: 0 (`filterwarnings=["error"]` is always on; every run above is warning-clean)

**E2E adversarial pass** — this machine has docker reachable + modal creds (`~/.modal.toml`), so the
real-infra smokes were exercised for real, not just skip-verified.
- Happy path (offline slice, NO infra + NO key): `env -u GEMINI_API_KEY -u MODAL_TOKEN_ID -u
  MODAL_TOKEN_SECRET DOCKER_HOST=unix:///nonexistent uv run pytest test_sandbox_capstone.py -k "not
  real" -q` → `13 passed, 3 deselected` (PASS)
- Happy path (whole file, real infra): `uv run pytest test_sandbox_capstone.py -v` → `16 passed in
  18.73s`, all 3 real smokes PASSED, 0 warnings (PASS)
- Break path 1 (infra-gating — the CI-without-infra guarantee): whole file with docker unreachable +
  modal creds hidden (redirected HOME) + key unset → `13 passed, 3 skipped`, reasons "the docker daemon
  is not reachable" / "modal account credentials are not present" — the 3 smokes SKIP, never fail (PASS)
- Break path 2 (mutation — replay-safety not theater): mutated shipped `flow._build_runtime_agent`
  `{"cache": False}` → `False` (the exact bug the docstring warns of) → `test_sandbox_bypass_agent_gets_
  the_cache_false_bash_checkpoint` FAILED loudly (`{'bash': False} != {'bash': {'cache': False}}`);
  reverted via `git checkout --` (PASS — the test is real proof)
- Break path 3 (mutation — byte-identity not theater): mutated `bash._render` to drop the trailing
  period → `test_none_mode_rendering_is_byte_identical...` FAILED loudly (`'Exit code: 0\n\n...' !=
  'Exit code: 0.\n\n...'`); reverted (PASS)
- Break path 4 (mutation — description wiring not theater): mutated `bash.bash_description` so the docker
  suffix is not appended → `test_bash_description_adapts_per_mode` FAILED loudly (docker_desc == base);
  reverted (PASS)
- Cost hygiene after every infra run: `docker ps -a` / `docker network ls` → 0 decode containers, 0
  decode networks; `modal.Sandbox.list` → 0 live `decode-sandbox` sandboxes (PASS)

**Acceptance criteria**
- [x] PASS — offline slice passes with no infra/key & proves the six areas — `13 passed, 3 deselected`
  (key unset + docker bogus + modal hidden). Each proof cross-checked real: executor-contract drives
  real `build_agent` + `PermissionGate` + host `LocalExecutor` (`_drive_one_gated_bash_turn`, asserts a
  `bash` `PermissionRequested` event + the real echo round-trip); selection swap uses the real
  `select_executor`; `none` description byte-identical (docker/modal == none + suffix); REPL laziness via
  fresh-interpreter `sys.modules` scans; credential map via real `build_credential_map` + patched
  `kitaru.get_secret`; replay-safety via real `_build_runtime_agent`. Mutation break-paths 2–4 confirm
  three of these catch the real regression.
- [x] PASS — 3 real-infra smokes SKIP cleanly absent, PASS present — infra-hidden → `13 passed, 3
  skipped` (correct reasons at :655/:705/:821); infra-up → all 3 PASSED. Skip predicates
  (`_docker_available` / `_modal_credentials_present`) are byte-identical to `test_docker_executor.py` /
  `test_modal_executor.py` / `test_credential_proxy.py`. Gating is per-test `@skipif` on infra only (not
  blanket): with infra present the real assertions run — the credential-proxy boundary asserts BOTH the
  injected `X-Decode-Proxy-Auth: <secret>` ARRIVES at the upstream AND the worker's `docker exec env`
  holds no secret (the genuine boundary invariant, not a weaker check).
- [x] PASS — hermetic under `filterwarnings=["error"]` run alone — file alone → `16 passed in 18.73s`,
  0 warnings, 0 container/network/sandbox litter after (deterministic executor disposal).
- [x] PASS — `make ci` green 0 warnings infra-less; `make integration-tests`; `uv lock --check` — full
  suite with docker unreachable + modal hidden + key unset → `1228 passed, 22 skipped`, 0 warnings, exit
  0, the 3 capstone smokes among the skips; `ruff format --check` + `ruff check` clean; `uv lock --check`
  clean. Infra-up `make integration-tests` → `56 passed`, 0 leak.
- [x] PASS — module docstring documents the feature end-to-end, naming REAL vs FAKED — three modes +
  one-seam design + credential boundary (§6) + replay-safety `{"cache": False}` (§5) + REPL laziness;
  explicitly lists REAL (run seam via `LocalExecutor`, `select_executor`, per-mode description,
  `build_credential_map`, `_build_runtime_agent`) vs FAKED (`FunctionModel`, stubbed docker/modal at the
  `select_executor` seam, patched `kitaru.get_secret`, spied `KitaruAgent`). Cross-checked accurate
  against `bash.py` / `proxy.py` / `flow.py`.

**Evidence**
```
# offline slice, NO infra + NO key
$ env -u GEMINI_API_KEY -u MODAL_TOKEN_ID -u MODAL_TOKEN_SECRET DOCKER_HOST=unix:///nonexistent \
    uv run pytest tests/integration/test_sandbox_capstone.py -k "not real" -q
13 passed, 3 deselected in 3.94s

# whole file, infra hidden → smokes SKIP (never fail)
$ ... DOCKER_HOST=unix:///nonexistent HOME=<no ~/.modal.toml> uv run pytest .../test_sandbox_capstone.py -rs -q
13 passed, 3 skipped   (:655 docker / :705 modal / :821 docker)

# whole file, real infra (docker + modal up)
$ uv run pytest tests/integration/test_sandbox_capstone.py -v   → 16 passed in 18.73s

# make ci on an infra-less machine (docker unreachable + modal hidden + no key)
$ uv lock --check → Resolved 149 packages ; ruff format --check / ruff check → clean
$ uv run pytest tests/unit tests/integration -rs -q → 1228 passed, 22 skipped in 137.59s  (0 warnings)

# full integration suite, real infra
$ uv run pytest tests/integration -rs -q → 56 passed in 96.86s   (0 warnings)
$ docker ps -a / network ls → 0 decode litter ; modal Sandbox.list → 0 live sandboxes

# mutation break-paths (shipped code mutated, test run, git-reverted)
flow.py {"cache": False}→False   → test_..._cache_false_bash_checkpoint FAILED (real proof)
bash._render drop "."            → test_..._byte_identical...          FAILED (real proof)
bash_description drop suffix     → test_bash_description_adapts_per_mode FAILED (real proof)
```

**Other issues found**
- None blocking. Non-blocking note: `test_sandbox_bypass_agent_gets_the_cache_false_bash_checkpoint`
  also asserts `checkpoint_strategy == "calls"`, reading the live `settings.runtime_checkpoint_strategy`
  default rather than pinning it — defensible (ADR-0011 §5 replay-safety genuinely requires the `"calls"`
  strategy for the `{"cache": False}` opt-out to bite), and the SWE flags it in-test as "the replay-ready
  default (settings)". If that default ever flips to `"turn"` this assertion would fail alongside the
  real coupling it guards, which is the correct signal. No action needed.
- git diff scope is exactly the task file (tracked) + the new `test_sandbox_capstone.py` (untracked); no
  product/`src`/docs/Makefile/pyproject regression.

**VERDICT: PASS**

### [PA] 2026-07-03 14:40 — Acceptance Review (whole sandboxing feature, PR #21, tasks 071-077)

**VERDICT: ACCEPT**

Feature-level user-POV gate for the sandboxing feature (ADR-0011). Walked every user-facing surface
against the shipped code + docs (not just the task files). All acceptance criteria hold from a real
user's perspective.

**User journey verified**
- **Default `SANDBOX_MODE=none` is byte-identical (the load-bearing opt-in promise).** `_sandbox_config_error()`
  returns `None` with **no probe** for `none` (`cli.py:198`); `_get_executor()` keeps the eager
  `LocalExecutor` and imports no sandbox module (`bash.py:100`); `bash_description` returns the base
  **unchanged** (`bash.py:173`). A user who sets nothing sees zero change.
- **Startup guards are friendly, never tracebacks.** Both the REPL (`cli.py:398-402`) and the headless
  `decode run`/`replay` pre-flight (`cli.py:327-330`) echo one action-oriented line to stderr + `Exit(1)`:
  `"…Docker daemon is not reachable — start Docker and retry (see .env.example)."` /
  `"…Modal credentials are missing — run \`modal token set …\` (see .env.example)."` Presence-only (no
  modal import, no network), matching the provider-key guard convention.
- **The model is never surprised.** The per-mode `bash` description suffixes (`bash.py:65-82`) state the
  live semantics — docker's persistent shell + shared `/workspace` + timeout-resets-shell; modal's remote
  empty scratch, no local tree, `cd`/`export` reset per call.
- **`decode run` + replay in a sandbox re-executes bash** (side effects re-run, not served stale) via the
  `{"cache": False}` bash checkpoint when `sandbox_mode != "none"` — the honest ADR-0010 reconciliation,
  proven end-to-end in the 075 QA (file `1→2` on replay vs a forced-cache control `1→1`).
- **Credential Proxy: a Worker that holds no token.** `DEFAULT_PROXY_RULES` ships **empty** (opt-in,
  `proxy.py:80`); the resolved map reaches only the proxy container's env; the README 3-step operator
  setup matches the shipped `github-auth` example; `import decode.cli` imports no kitaru (REPL invariant).

**ADR scope delivered** — 3 executors behind ONE seam (`select_executor` returns only `CommandExecutor`
in every branch, `sandbox/__init__.py:33-56`); callers see only `ExecResult` (no Docker/Modal types leak
up); secrets never reach the model or the sandbox payload (credential-boundary proven by the worker-env
scan in 075/077).

**Docs reconciliation (076) is honest** — the two AGENTS.md invariants, the four Testing-E2E rows, the
README `## Sandboxing` section, the `.env.example` block, and the MODAL account-vs-endpoint token note all
match shipped behavior. Notably the SWE corrected a *fictional* `docker ps --filter label=…` peek to plain
`docker ps` after verifying `_docker_run_args` adds no label — docs fixed to reality, not the reverse.

**Two out-of-scope drifts — judged NON-BLOCKING, agreed:**
1. Glossary named the proxy topology class `DockerProxy` while the code ships `DockerCredentialProxy`
   (`proxy.py:153`) — a contributor-doc reference, invisible to any user, and the canonical *term*
   "Credential Proxy" is used correctly everywhere. `DockerProxy` had **zero** code/test coupling
   (`grep` clean in `src/`+`tests/`). Fixed inline in `docs/glossary.md` as prescribed glossary
   rename-tracking (I author the glossary). ADR-0011 retains `DockerProxy` as its design-time name (an
   Accepted ADR is a point-in-time record; not edited).
2. README `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b` (lines 89, 98) vs `settings.py:123` default
   `Qwen/Qwen3.6-35B-A3B-FP8` — a **pre-existing** Milestone-5 provider-doc drift, unrelated to
   sandboxing; the 076 diff correctly did not touch it. Worth a separate docs-drift follow-up; NOT a
   reason to block this feature.

All user acceptance criteria verified from the user POV. Hand off to the PR Reviewer.
