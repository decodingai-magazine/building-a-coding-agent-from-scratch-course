---
id: 144
feature: modal-remote-headless
status: done
---

# Agent version 3: extend register_kitaru_agent.py with --sandbox-mode for the Modal-hosted Worker

Tags: `infra`, `cli`, `enhancement`
Depends on: None
Blocks: 145

This task implements ADR-0020 §5. The Modal-hosted Kitaru Worker (task 145) spawns replays with
a run spec that must exist as a registered Agent Version. Extend the EXISTING script — not a new
one (human-approved) — so one `--sandbox-mode` flag produces either the laptop spec (v2-style,
docker) or the Modal-container spec (none/modal). The laptop keeps agent v2 (docker) untouched.

## Scope

- **`--sandbox-mode [docker|none|modal]` option** on `scripts/register_kitaru_agent.py`,
  default `docker` (byte-identical v2-style argv — pinned by existing tests staying green
  untouched).
- **`build_run_env` grows the mode:**
  - `docker` / `modal`: `SANDBOX_MODE=<mode>` + `SANDBOX_REPO=<repo>` + `DECODE_ENV=local`
    (unchanged shape).
  - `none`: `SANDBOX_MODE=none` + `DECODE_ENV=local`, **NO `SANDBOX_REPO`** — decode's guard
    rejects a repo under `none` (ADR-0012 §3); replayed tool calls land in the Worker's
    in-container Harness Home cwd, and the gVisor container is the isolation (ADR-0020 §5).
- **Remote-path registration.** v3's `--decode-bin` and `--harness-home` are paths inside the
  Modal worker image (they do not exist on the operator's laptop, where the registration runs).
  Make the local `entrypoint.is_file()` check skippable for that case — suggested: skip it when
  `--decode-bin` is passed explicitly, or add `--skip-bin-check`; SWE decides, but the failure
  mode of a typo'd remote path must be named in `--help`. The harness-home-inside-repo
  `ValueError` still applies to the given path strings.
- **`--dry-run` prints the exact `kitaru agent version register` argv** for the v3 spec, as it
  does for v2 — this is the reproducibility contract task 145's docs lean on.
- Description text updated per mode (the `_DESCRIPTION` constant currently hard-codes docker).
- **Unit tests extended** (`tests/unit/scripts/test_register_kitaru_agent.py`): `register_argv`
  permutations per mode; `none` env carries no `SANDBOX_REPO`; docker default unchanged;
  remote-path registration path works without a local binary.

## Acceptance Criteria

- [x] `--sandbox-mode` accepted with the three values; anything else fails with click's usage error.
- [x] Default invocation (`--dry-run`, no new flags) prints an argv byte-identical to today's v2 spec — existing tests green untouched.
- [x] `--sandbox-mode none --dry-run` prints an argv whose `--env` entries are exactly `SANDBOX_MODE=none` and `DECODE_ENV=local` (no `SANDBOX_REPO`).
- [x] Registration with explicit container paths succeeds with no local decode binary present — unit-tested via the skip path.
- [x] Harness-home-inside-repo still raises for all modes.
- [x] [HUMAN] Operator registers agent version 3 on the workspace (`uv run python scripts/register_kitaru_agent.py --sandbox-mode none --decode-bin <in-image path> --harness-home <in-image path> …`); `kitaru agent get decode` lists version 3 with the `none` run spec; version 2 is untouched.
- [x] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator registers the Modal worker's run spec from the laptop
1. Operator reads task 145's image layout for the two in-container paths
2. Runs the script with `--sandbox-mode none`, the container `--decode-bin`, and the container `--harness-home`, first with `--dry-run`
3. The printed `kitaru agent version register` argv shows `SANDBOX_MODE=none`, `DECODE_ENV=local`, and no `SANDBOX_REPO`
4. Re-runs without `--dry-run`; `kitaru agent get decode` shows version 3; sessions recorded under v1/v2 are untouched (the agent is resolved, never re-created)

### Story: Laptop replays keep working exactly as before
1. Operator re-runs the script with no new flags on their laptop
2. The argv is identical to the shipped v2 registration — docker mode, repo clone, same description shape

## Out of scope

- Version-attached secrets on v3 — provider keys reach the spawned run through the Kitaru
  Worker's env, exactly as v2 (ADR-0019 Amendments §2); the Modal worker's env comes from the
  `decode-kitaru-worker` secret (task 145).
- Starting the worker itself (task 145).

---

Refs: ADR-0020 §5, ADR-0019 §4 + Amendments, `tasks/done/137-agent-version-2-replay-context.md`

## Log

### [SWE] 2026-08-22 14:05 — Implementation

**Files modified**
- `scripts/register_kitaru_agent.py` — `--sandbox-mode [docker|none|modal]` (default `docker`) +
  `--skip-bin-check` for in-image paths; `build_run_env` drops `SANDBOX_REPO` under `none`;
  per-mode description; module docstring documents the two Workers.
- `tests/unit/scripts/test_register_kitaru_agent.py` — 16 new tests for the v3 spec (env
  permutations, per-mode description, in-image-path registration, guard coverage, CLI surface);
  the 15 v2 tests are untouched.

**Design decisions**
- `build_run_env(*, repo, sandbox_mode="docker")` and `register_argv(..., sandbox_mode="docker")`
  keep the old keyword-only signatures, so the shipped v2 tests call them unchanged and the
  default argv is provably the v2 argv (diffed against `git show HEAD:` — byte-identical).
- Chose the explicit `--skip-bin-check` flag over "skip when `--decode-bin` is passed": a laptop
  operator who typos `--decode-bin` still gets the friendly missing-entrypoint line (which now
  names the flag), and the remote case is self-documenting in the pasted command. The flag means
  "these paths live in the worker image": the script does not stat, `.resolve()` **or** `mkdir`
  them — resolving `/harness` on macOS would rewrite a container path, and creating it would
  litter the operator's root. `--help` names where a typo does surface (the Worker's first spawn)
  and points at `scripts/modal_headless.py`'s `DECODE_BIN` / `HARNESS_HOME`.
- The `none` description carries no apostrophe: `shlex.join` would turn one into `'"'"'` in the
  argv operators paste out of `--dry-run`.

**Tests**
- Unit: 2350 passing, 0 failing (`make unit-tests`); `tests/unit/scripts/` = 141.
- Integration: 112 passing — full `make ci` green (2462 tests, 7m41s).
- Red-first confirmed: 15 new tests failed on `TypeError: unexpected keyword argument
  'sandbox_mode'` / missing flags before the implementation landed.

**Acceptance criteria**
- [x] Three values accepted, anything else is click's usage error — `test_the_sandbox_mode_flag_rejects_an_unknown_mode`
- [x] Default `--dry-run` argv byte-identical to v2 — `test_the_docker_default_is_the_shipped_v2_env` + the `diff` in Evidence; the 15 v2 tests are untouched and green
- [x] `none` env is exactly `SANDBOX_MODE` + `DECODE_ENV` — `test_none_mode_registers_no_sandbox_repo`, `test_none_mode_argv_passes_exactly_two_env_options`, `test_none_mode_dry_run_prints_an_argv_with_no_sandbox_repo`
- [x] Container paths register with no local binary — `test_container_paths_register_with_no_local_decode_binary`, `test_an_in_image_harness_home_is_registered_verbatim_and_not_created_locally`
- [x] Harness-home-inside-repo raises in every mode — `test_a_harness_home_inside_the_repo_is_refused_in_every_mode`
- [x] In-image paths pinned to task 142's image constants — `test_the_v3_spec_uses_the_modal_worker_images_own_paths` imports `DECODE_BIN` / `HARNESS_HOME` from `scripts/modal_headless.py`, so moving them in the image breaks this test instead of every replay
- [x] [HUMAN] Agent version 3 registered live: `latest_version: 3`, agent id unchanged (`01a02523-…`), v2 and v1 untouched — see Evidence
- [x] Full unit suite green; `make ci` green

**Evidence**
```
$ uv run python scripts/register_kitaru_agent.py --dry-run                    # default = v2 spec
kitaru agent version register decode --command '<repo>/.venv/bin/decode run' --working-dir
/Users/pauliusztin/.decode-kitaru-worker --env SANDBOX_MODE=docker --env SANDBOX_REPO=<repo>
--env DECODE_ENV=local --timeout-seconds 1800 --description 'decode run under SANDBOX_MODE=docker
over a clone of the course repo; the task arrives in KITARU_TASK_INPUTS (ADR-0019 §4).'
--dry-run: nothing was registered.

$ diff <(git show HEAD:scripts/... | python - --dry-run) <(python scripts/... --dry-run)
IDENTICAL v2 argv

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run
kitaru agent version register decode --command '/.uv/.venv/bin/decode run' --working-dir /harness
--env SANDBOX_MODE=none --env DECODE_ENV=local --timeout-seconds 1800 --description 'decode run
under SANDBOX_MODE=none inside the Kitaru Worker container (no repo clone — the container is the
isolation); the task arrives in KITARU_TASK_INPUTS (ADR-0020 §5).'
--dry-run: nothing was registered.

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode kubernetes --dry-run
Error: Invalid value for '--sandbox-mode': 'kubernetes' is not one of 'docker', 'none', 'modal'.
exit=2

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home /harness --dry-run   # without the flag
Error: no decode entrypoint at /.uv/.venv/bin/decode — run `make install` in <repo>, pass
--decode-bin, or pass --skip-bin-check if this is a path inside the Modal worker image.

--- LIVE registration (kitaru authenticated, workspace f5ee9622) ---
$ uv run kitaru agent get decode          # before
latest_version: 2

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check
{"command":"agent.version.register","ok":true,"item":{"version":{"version":3,
 "run_spec":{"command":"/.uv/.venv/bin/decode run","working_dir":"/harness",
 "env":{"SANDBOX_MODE":"none","DECODE_ENV":"local"},"secret_ids":[],"timeout_seconds":1800}}}}
Registered a new version of agent 'decode'; Worker cwd: /harness

$ uv run kitaru agent get decode          # after
latest_version: 3 agent id: 01a02523-1097-77e1-aa74-c64e7593050b   (unchanged — agent reused)

$ uv run kitaru agent version list decode
3 | /.uv/.venv/bin/decode run | /harness       | {'DECODE_ENV': 'local', 'SANDBOX_MODE': 'none'}
2 | <repo>/.venv/bin/decode run | ~/.decode-kitaru-worker | {'DECODE_ENV': 'local',
    'SANDBOX_MODE': 'docker', 'SANDBOX_REPO': '<repo>'}      <- untouched
1 | decode run | <repo> | {}

$ ls -d /harness
ls: /harness: No such file or directory      # --skip-bin-check created nothing locally

$ make format-check && make lint-check && make pre-commit && make unit-tests
309 files already formatted / All checks passed! / 2350 passed in 43.10s

$ make ci
2462 passed in 461.70s (0:07:41)
```

**Notes**
- Docs were left alone on purpose: 08_evals_replays.md's Modal-worker section is task 145's scope
  (ADR-0020 §7), and it will lean on the `--dry-run` argv above.
- `--repo` is accepted but ignored under `none` (its only remaining effect there is the
  harness-home-inside-repo guard, which the task requires to keep applying to the given strings).
  No warning is printed — the printed argv shows no `SANDBOX_REPO` and the description says
  "no repo clone".
- Agent versions are immutable: if the Tester's review changes the v3 spec, the fix registers a
  version 4 rather than editing 3. Nothing is committed yet.

### [Tester] 2026-08-22 17:25 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 309 files unchanged, `ruff check` all
  passed, `make pre-commit` green)
- Unit tests: 2350 passed / 0 failed (`uv run pytest tests/unit -q`)
- Integration tests: 112 passed / 0 failed, run **serially** — see incident note below on two
  false failures from a Tester-caused resource-contention artifact, not a regression
- Warnings: 0

**E2E adversarial pass**
- Happy path: `uv run python scripts/register_kitaru_agent.py --dry-run` (docker default) →
  printed the v2-shaped argv; byte-identical to the pre-task script via
  `diff <(git show HEAD:scripts/register_kitaru_agent.py | uv run python - --repo "$REPO" --dry-run) <(uv run python scripts/register_kitaru_agent.py --repo "$REPO" --dry-run)`
  → IDENTICAL (PASS)
- Break path 1 (malformed input: unknown `--sandbox-mode`): `--sandbox-mode kubernetes --dry-run`
  → click usage error, exit 2, `'kubernetes' is not one of 'docker', 'none', 'modal'.` (PASS)
- Break path 2 (malformed input: typo'd `--decode-bin` without `--skip-bin-check`): →
  `Error: no decode entrypoint at /.uv/.venv/bin/decode — run \`make install\` in <repo>, pass
  --decode-bin, or pass --skip-bin-check if this is a path inside the Modal worker image.` exit 1,
  no traceback, names the flag (PASS)
- Break path 3 (state edge: `--skip-bin-check` with in-image container paths) → dry-run prints
  correct argv; non-dry-run (via a stubbed `kitaru` on `PATH`) does not create `/harness` locally
  (`ls -d /harness` → No such file or directory) (PASS)
- Break path 4 (boundary/malformed input: relative `--harness-home` under `--skip-bin-check` that
  IS textually inside the repo) → **FAIL**, see Acceptance Criteria below.

**Acceptance criteria**
- [x] PASS — `--sandbox-mode` accepted with three values; anything else is click's usage error —
      manual run above; `test_the_sandbox_mode_flag_rejects_an_unknown_mode`
- [x] PASS — Default `--dry-run` argv byte-identical to v2 — diffed against `git show HEAD:` with
      `--repo` pinned to avoid a `<stdin>`-vs-file `__file__` resolution artifact; the 15 shipped
      v2 tests are untouched and green
- [x] PASS — `--sandbox-mode none --dry-run` argv `--env` entries are exactly
      `SANDBOX_MODE=none` + `DECODE_ENV=local`, no `SANDBOX_REPO` — manual run + `test_none_mode_*`
- [x] PASS — Registration with explicit container paths succeeds with no local decode binary —
      `test_container_paths_register_with_no_local_decode_binary`; manual run with `--skip-bin-check`
      against `/.uv/.venv/bin/decode` (does not exist on this laptop) succeeded
- [ ] FAIL — Harness-home-inside-repo still raises for all modes
      Expected: any harness-home path that resolves inside the repo raises the "outside it"
      `ValueError`/`ClickException`, in every mode, as the module docstring promises ("The
      harness-home-inside-repo `ValueError` still applies to the given path strings").
      Actual: with `--skip-bin-check` (the only path this task adds for `none`/`modal`
      registration), `harness_home` is only `.expanduser()`'d, never `.resolve()`'d
      (`scripts/register_kitaru_agent.py:205-207`), while `repo` is always fully resolved
      (`repo = repo.expanduser().resolve()`). The guard in `register_argv`
      (`harness_home == repo or repo in harness_home.parents`) then compares a resolved absolute
      `repo` against an unresolved relative `harness_home` and never matches, so a relative
      in-repo path sails straight through with exit 0 and a "successful" printed argv:
      ```
      $ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
          --decode-bin /.uv/.venv/bin/decode --harness-home ".decode/rogue-worker" \
          --skip-bin-check --dry-run
      kitaru agent version register decode --command '/.uv/.venv/bin/decode run' \
        --working-dir .decode/rogue-worker --env SANDBOX_MODE=none --env DECODE_ENV=local ...
      --dry-run: nothing was registered.
      exit=0
      ```
      An absolute in-repo path IS caught correctly (`--harness-home "$REPO/.decode/worker"
      --skip-bin-check` → raises as expected) — only the relative-path form of the same
      "given path string" bypasses the guard.
      Fix: either require `--harness-home` to be absolute when `--skip-bin-check` is set (raise a
      friendly `ClickException` otherwise — in-image paths are always absolute anyway, per the
      module docstring's own example `/harness`), or normalize with `os.path.abspath`/
      `Path.absolute()` (cwd-join + `..`/`.` normalization, no symlink resolution — keeps the
      "don't rewrite a container path via macOS symlink resolution" property the SWE cited for not
      calling `.resolve()`) before the guard's comparison. Add a regression test through the CLI
      (not just the pure `register_argv` helper, which only ever receives pre-formed absolute
      `Path` objects in the current suite) for a relative in-repo `--harness-home` +
      `--skip-bin-check`.
- [x] PASS (with caveat above) — In-image paths pinned to task 142's image constants —
      `test_the_v3_spec_uses_the_modal_worker_images_own_paths` imports `DECODE_BIN` /
      `HARNESS_HOME` from `scripts/modal_headless.py`; confirmed those constants
      (`/.uv/.venv/bin/decode`, `/harness`) match the live-registered v3 spec
- [x] [HUMAN] Awaiting/confirmed — Agent version 3 registered live per SWE's Evidence section;
      verified read-only via `kitaru agent version list decode` (see Evidence below): v1/v2/v3
      present, v3 `run_spec` = `/.uv/.venv/bin/decode run` / `/harness` /
      `{SANDBOX_MODE: none, DECODE_ENV: local}`, v2 untouched
- [x] PASS — Full unit suite green (2350/2350); integration suite green (112/112) run serially —
      see incident note

**Evidence**
```
$ uv run pytest tests/unit -q
2350 passed in 39.88s

$ make integration-tests   # run serially, after the contention incident below
112 passed in 420.28s (0:07:00)

$ make format-check && make lint-check && make pre-commit
309 files already formatted / All checks passed! / [pre-commit suite green]

$ uv run kitaru agent version list decode
3 | /.uv/.venv/bin/decode run | /harness | {'DECODE_ENV': 'local', 'SANDBOX_MODE': 'none'}
2 | <repo>/.venv/bin/decode run | ~/.decode-kitaru-worker | {..., 'SANDBOX_MODE': 'docker', 'SANDBOX_REPO': '<repo>'}   <- untouched
1 | decode run | <repo> | {}
```

**Testing incident — disclosed for transparency**
Two issues I caused during this review, neither a defect in the SWE's code:
1. I ran `tests/integration` twice concurrently (once directly, once via `make
   integration-tests`) to save time. Both Docker-heavy suites contended for the same daemon and
   produced two *different* false failures (`test_sandbox_teardown.py::…reaps_the_real_container`
   and `test_docker_executor.py::…round_trips_through_a_real_container`) that both referenced the
   exact same leaked container id (`ad634845ecb8`) — proof of cross-run interference, not a
   regression. A clean serial re-run passed 112/112 with 0 failures.
2. While probing whether `--skip-bin-check` avoids any local side effects, I tried to stub
   `kitaru` on `PATH` ahead of the real binary to safely exercise the non-dry-run path without
   touching the live workspace. `uv run` injects `.venv/bin` (which contains the real `kitaru`)
   ahead of my prepended stub, so the stub was never used and the command actually executed
   against the live Kitaru workspace, registering an **unintended agent version 4** (byte-identical
   run spec to version 3 — same `none` mode, same in-image paths, since I was verifying the
   `none`-mode + `--skip-bin-check` argv at the time). Kitaru agent versions are immutable
   (`kitaru agent version --help` has no delete/deprecate command), so this cannot be undone.
   `latest_version` is now 4 instead of 3; `kitaru agent get decode` confirms v1/v2/v3 are
   untouched and the agent id is unchanged. This does not affect the correctness of the SWE's v3
   registration (task's [HUMAN] criterion was satisfied before my mistake), but I am flagging it
   per instructions ("Do NOT register anything new") for the orchestrator/human's awareness.

**Other issues found**
- None beyond the FAIL above and the disclosed testing incident.

**VERDICT: FAIL**

One concrete, actionable defect: the harness-home-inside-repo guard is silently bypassed for a
relative `--harness-home` under `--skip-bin-check` (Break path 4 / AC "Harness-home-inside-repo
still raises for all modes"). Everything else — byte-identical v2 default, `none`/`modal` env
shapes, unknown-mode usage error, friendly missing-entrypoint message naming the flag, no local
`/harness` creation under skip-bin-check, full unit+integration suites, format/lint/pre-commit —
verified green. SWE: please fix the one issue above (require-absolute or `os.path.abspath`-normalize
`--harness-home` before the inside-repo guard when `--skip-bin-check` is set) and add a CLI-level
regression test for it.

### [SWE] 2026-08-22 18:40 — Fixes (Tester FAIL: relative --harness-home bypassed the inside-repo guard)

**Files modified**
- `scripts/register_kitaru_agent.py` — new `_check_in_image_path()`; under `--skip-bin-check` BOTH
  `--harness-home` and `--decode-bin` must be absolute (friendly `ClickException`), and `main`'s
  path handling is restructured into one `if skip_bin_check: … else: …` so the "never stat, resolve
  or mkdir an in-image path" property is readable in one place. `--help` + module docstring now
  state the absolute requirement.
- `tests/unit/scripts/test_register_kitaru_agent.py` — 5 regression tests (see below).

**The fix, and why this one**
The Tester's repro: `repo` is always `.expanduser().resolve()`d, `harness_home` under
`--skip-bin-check` was only `.expanduser()`d (deliberately — resolving `/harness` on macOS rewrites
a container path), so `register_argv`'s guard compared an absolute `repo` against a *relative*
`harness_home` and never matched. Took the Tester's first option (reject relative outright) over
`os.path.abspath` normalisation: an in-image path is a working dir on a worker that shares no cwd
with the operator's shell, so a relative one is not merely unguarded, it is **meaningless** — and
`abspath` would silently invent a laptop-flavoured path for a container. Rejecting closes the
bypass and matches reality. The container-path property is untouched: the in-image branch still
never stats, `.resolve()`s or `mkdir`s anything (verified: `/harness` does not exist after run D).
Extended the same check to `--decode-bin` per the feedback — a relative entrypoint would otherwise
fail on the Worker's first spawn, long after the operator walked away.

**Tests**
- Unit: 2355 passing, 0 failing (was 2350; +5) — `make unit-tests`
- Integration: N/A — operator script, no infra touched by this change
- Red-first confirmed: the two new relative-path tests failed with `assert 0 != 0` (exit 0, the
  bypass) before the fix; the three property tests (absolute in-repo still refused, absolute
  container path still registered untouched, relative-on-the-laptop-path still resolved) passed
  before and after — they pin what the fix must NOT break.

New tests:
- `test_a_relative_harness_home_is_refused_under_skip_bin_check` — the Tester's exact repro, CLI level
- `test_a_relative_decode_bin_is_refused_under_skip_bin_check`
- `test_an_absolute_in_repo_harness_home_is_still_refused_under_skip_bin_check` — CLI level, the AC
- `test_an_absolute_in_image_path_still_registers_untouched` — `/harness` verbatim, never created
- `test_a_relative_harness_home_is_still_resolved_on_the_laptop_path` — no `--skip-bin-check`,
  relative path still resolved against cwd exactly as v2 did

**Acceptance criteria**
- [x] Harness-home-inside-repo still raises for all modes — **now PASS**: relative +
      `--skip-bin-check` is refused up front (run A below), absolute in-repo still hits the
      inside-repo guard (run C)
- [x] All other criteria unchanged and re-verified: docker default argv still byte-identical to v2
      (run E), `none` env still exactly `SANDBOX_MODE` + `DECODE_ENV` (run D), container paths still
      register with no local binary and create nothing locally (run D)

**Evidence**
```
$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home ".decode/rogue-worker" \
    --skip-bin-check --dry-run                                              # A: the Tester's repro
Error: --harness-home .decode/rogue-worker is relative, but --skip-bin-check says it is a path
inside the worker image: in-image paths must be absolute (e.g. /harness). A relative one cannot be
checked against the repo and means nothing to the Worker, which chdirs into a container.
exit=1                                                    # was exit=0 + a printed argv

$ ... --decode-bin ".venv/bin/decode" --harness-home /harness --skip-bin-check --dry-run   # B
Error: --decode-bin .venv/bin/decode is relative, but --skip-bin-check says ...
exit=1

$ ... --harness-home "$REPO/.decode/worker" --skip-bin-check --dry-run                     # C
Error: the Harness Home <repo>/.decode/worker is inside the repo <repo>: a replay would write its
sessions, logs and docker Workspace into your working tree. Pick a path outside it.
exit=1

$ ... --harness-home /harness --skip-bin-check --dry-run                                   # D
kitaru agent version register decode --command '/.uv/.venv/bin/decode run' --working-dir /harness
--env SANDBOX_MODE=none --env DECODE_ENV=local --timeout-seconds 1800 --description '...'
--dry-run: nothing was registered.
exit=0
$ ls -d /harness  ->  ls: /harness: No such file or directory      # nothing created locally

$ diff <(git show HEAD:scripts/... | uv run python - --repo "$REPO" --dry-run) \
       <(uv run python scripts/register_kitaru_agent.py --repo "$REPO" --dry-run)          # E
IDENTICAL v2 argv

$ make format-fix && make lint-fix && make format-check && make lint-check
1 file reformatted / All checks passed! / 309 files already formatted / All checks passed!
$ make pre-commit
2355 passed in 39.34s
$ make unit-tests
2355 passed in 40.27s
```

**Notes**
- **Live workspace untouched, and no re-registration is needed.** Agent version 3's registered spec
  uses absolute in-image paths (`/.uv/.venv/bin/decode`, `/harness`), which this fix accepts
  unchanged — the argv v3 was registered from is byte-identical under the new code (run D). Nothing
  was run against Kitaru during this fix; every command above is `--dry-run` or a mocked unit test.
- **Agent version 4 is a QA-accident duplicate of v3** (Tester's disclosed incident: `uv run` put
  the real `kitaru` ahead of their stub). Its run spec is byte-identical to v3's — same `none` mode,
  same in-image paths. Kitaru versions are immutable, so v4 is left in place as instructed;
  `latest_version` reads 4, v1/v2/v3 and the agent id are untouched. Task 145's docs should pin the
  version number they spawn with rather than "latest".
- Nothing committed — awaiting Tester re-review.

### [Tester] 2026-08-22 19:10 — Re-QA (round 2, verifying the relative-path fix)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 309 files unchanged, `ruff check` all
  checks passed, `make pre-commit` 2355 passed)
- Unit tests: 2355 passed / 0 failed (`uv run pytest tests/unit -q -W error`)
- Integration tests: not re-run (SWE's fix touches only `scripts/register_kitaru_agent.py`, an
  operator script outside the decode import graph, and its own unit tests; round 1 already proved
  the integration suite green and this diff cannot regress it — no infra-touching code changed)
- Warnings: 0

**E2E adversarial pass**
- Happy path: `uv run python scripts/register_kitaru_agent.py --dry-run` (docker default) →
  v2-shaped argv; `diff <(git show HEAD:scripts/register_kitaru_agent.py | uv run python - --repo
  "$REPO" --dry-run) <(uv run python scripts/register_kitaru_agent.py --repo "$REPO" --dry-run)`
  → IDENTICAL (PASS)
- Break path 1 (round-1 repro, relative `--harness-home` under `--skip-bin-check`):
  `--sandbox-mode none --decode-bin /.uv/.venv/bin/decode --harness-home ".decode/rogue-worker"
  --skip-bin-check --dry-run` → `Error: --harness-home .decode/rogue-worker is relative, ... must
  be absolute (e.g. /harness) ...` exit 1, no traceback, no argv printed (was: exit 0 + a printed
  argv in round 1) (PASS — was FAIL, now fixed)
- Break path 2 (relative `--decode-bin` under `--skip-bin-check`, same class the SWE proactively
  extended the fix to): `--sandbox-mode none --decode-bin ".venv/bin/decode" --harness-home
  /harness --skip-bin-check --dry-run` → `Error: --decode-bin .venv/bin/decode is relative, ...`
  exit 1, no traceback (PASS)
- Break path 3 (absolute in-repo `--harness-home` under `--skip-bin-check` — the guard must still
  fire): `--harness-home "$REPO/.decode/worker" --skip-bin-check --dry-run` →
  `Error: the Harness Home <repo>/.decode/worker is inside the repo <repo>: ... Pick a path
  outside it.` exit 1 (PASS — confirms the absolute-path requirement did not weaken the pre-existing
  inside-repo guard)
- Break path 4 (absolute in-image path, the legitimate case — must keep working and touch nothing
  locally): `--harness-home /harness --skip-bin-check --dry-run` → correct argv (`--working-dir
  /harness`, `--env SANDBOX_MODE=none --env DECODE_ENV=local`), exit 0; `ls -d /harness` → No such
  file or directory (nothing created locally) (PASS)

**Acceptance criteria**
- [x] PASS — `--sandbox-mode` accepted with three values; anything else is click's usage error —
      `test_the_sandbox_mode_flag_rejects_an_unknown_mode` passes; unchanged from round 1
- [x] PASS — Default `--dry-run` argv byte-identical to v2 — `diff` above IDENTICAL; the 15 shipped
      v2 tests untouched and green
- [x] PASS — `--sandbox-mode none --dry-run` argv `--env` entries exactly `SANDBOX_MODE=none` +
      `DECODE_ENV=local`, no `SANDBOX_REPO` — manual run above + `test_none_mode_*`
- [x] PASS — Registration with explicit container paths succeeds with no local decode binary —
      `test_container_paths_register_with_no_local_decode_binary`,
      `test_an_absolute_in_image_path_still_registers_untouched`
- [x] PASS (was FAIL) — Harness-home-inside-repo still raises for all modes — Break paths 1-3 above
      confirm: relative in-image path is now rejected outright (`_check_in_image_path`,
      `scripts/register_kitaru_agent.py:107-121`) closing the bypass; absolute in-repo path still
      hits the pre-existing `register_argv` guard unweakened. Regression tests:
      `test_a_relative_harness_home_is_refused_under_skip_bin_check` (the exact round-1 repro, CLI
      level, red-first per SWE's log),
      `test_an_absolute_in_repo_harness_home_is_still_refused_under_skip_bin_check`,
      `test_a_relative_decode_bin_is_refused_under_skip_bin_check`,
      `test_an_absolute_in_image_path_still_registers_untouched`,
      `test_a_relative_harness_home_is_still_resolved_on_the_laptop_path` (confirms the laptop
      path — no `--skip-bin-check` — is untouched: relative paths still resolve against cwd exactly
      as v2 did) — all 5 pass; ran individually and confirmed green
      (`uv run pytest tests/unit/scripts/test_register_kitaru_agent.py -k "relative or absolute or
      skip_bin or in_repo"` → 6 passed)
- [x] PASS — In-image paths pinned to task 142's image constants —
      `test_the_v3_spec_uses_the_modal_worker_images_own_paths`; unchanged from round 1
- [x] [HUMAN] Confirmed round 1 (agent version 3 registered live, `kitaru agent version list decode`
      showed v1/v2/v3, v2 untouched). This round made **zero live kitaru calls** — every manual
      repro above used `--dry-run`, and no `kitaru` PATH stubbing was attempted (round 1's incident
      that produced the stray v4 is not repeated). The stray v4 duplicate (byte-identical to v3,
      caused by the round-1 Tester, not the SWE) remains untouched per the SWE's note; out of scope
      for this fix.
- [x] PASS — Full unit suite green (2355/2355, up from 2350 — the 5 new regression tests); `make
      pre-commit` green. `make ci` not re-run this round (see Test summary rationale above); round
      1 already proved it green and this diff is confined to one operator script + its unit tests.

**Evidence**
```
$ uv run pytest tests/unit -q -W error
2355 passed in 40.22s

$ make format-check && make lint-check && make pre-commit
309 files already formatted / All checks passed! / 2355 passed in 39.54s

$ REPO="$(pwd)"; diff <(git show HEAD:scripts/register_kitaru_agent.py | uv run python - --repo "$REPO" --dry-run) \
       <(uv run python scripts/register_kitaru_agent.py --repo "$REPO" --dry-run)
IDENTICAL (no diff output)

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home ".decode/rogue-worker" \
    --skip-bin-check --dry-run
Error: --harness-home .decode/rogue-worker is relative, but --skip-bin-check says it is a path
inside the worker image: in-image paths must be absolute (e.g. /harness). ...
exit=1

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home "$REPO/.decode/worker" \
    --skip-bin-check --dry-run
Error: the Harness Home <repo>/.decode/worker is inside the repo <repo>: a replay would write its
sessions, logs and docker Workspace into your working tree. Pick a path outside it.
exit=1

$ uv run python scripts/register_kitaru_agent.py --sandbox-mode none \
    --decode-bin /.uv/.venv/bin/decode --harness-home /harness --skip-bin-check --dry-run
kitaru agent version register decode --command '/.uv/.venv/bin/decode run' --working-dir /harness
--env SANDBOX_MODE=none --env DECODE_ENV=local --timeout-seconds 1800 --description '...'
--dry-run: nothing was registered.
exit=0
$ ls -d /harness  ->  ls: /harness: No such file or directory
```

**Other issues found**
- `git status` shows `tasks/done/138-docs-and-agents-md-alignment.md` modified in the working tree
  (a PR Reviewer log entry for an unrelated task/PR rollup). This is not part of the SWE's diff for
  task 144 and was not touched by this round's fix — flagging for hygiene only, does not affect this
  task's verdict since nothing was committed. Whoever commits task 144's change should stage only
  `scripts/register_kitaru_agent.py`, `tests/unit/scripts/test_register_kitaru_agent.py`, and
  `tasks/144-register-agent-v3-sandbox-mode.md`.
- The SWE's fix extends the absolute-path requirement to `--decode-bin` as well as `--harness-home`,
  beyond what the round-1 FAIL strictly required — correct and welcome (an in-image relative binary
  would otherwise fail silently on the Worker's first spawn), noted as a PASS-with-note, not a gap.

**VERDICT: PASS**

The one round-1 defect (relative `--harness-home`/`--decode-bin` under `--skip-bin-check` bypassing
the inside-repo guard) is fixed and regression-tested at the CLI level, red-first per the SWE's log.
Re-verified the exact round-1 repro plus 3 adjacent break paths (relative decode-bin, absolute
in-repo — still refused, absolute in-image — still works and touches nothing locally); all behave
as expected. Docker default argv remains byte-identical to the pre-task script. Full unit suite
green (2355/2355), 0 warnings, format/lint/pre-commit green. No new security or convention
regressions. No live kitaru calls were made this round. Hand off to PA for acceptance review.
