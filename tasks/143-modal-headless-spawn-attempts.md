---
id: 143
feature: modal-remote-headless
status: pending
---

# Modal Headless App — fire-and-forget spawn + N parallel attempts (successor of demo-multiple-attempts.sh)

Tags: `infra`, `runtime`, `enhancement`
Depends on: 142
Blocks: 146

This task implements ADR-0020 §1 (launch surface: the asynchronous half). The retired
`scripts/demo-multiple-attempts.sh` demo — N independent headless attempts at one task, N
comparable branches — reborn on Modal, minus every ZenML trap it existed to dodge (warm-up
runs, code-upload TOCTOU, stagger): the image is built once at deploy and every spawn shares it.

## Scope

- **Deploy path:** `uv run modal deploy scripts/modal_headless.py` publishes the app; document
  it in the module docstring (full docs are task 146).
- **Spawn helper** in the same script: a second `@app.local_entrypoint()` (suggested name
  `attempts`) →
  `uv run modal run scripts/modal_headless.py::attempts --task "…" --repo <url> --attempts 5 [--sandbox-mode modal] [--detach]`
  - Spawns N independent `run_task` calls via `Function.spawn` (each attempt = its own gVisor
    container = its own Workspace = its own `decode/<session-id>` branch; truly parallel, no
    stagger).
  - Appends the push-ban paragraph to the task (as the old demo did): "Commit your work when
    you are done. Do NOT push and do NOT open a pull request." — the Hand-back is the only ship
    path, so the N branches stay comparable.
  - Default: wait on the N `FunctionCall`s, then print a comparison table — attempt #, decode
    session id, branch, shipped/NOT SHIPPED, exit — plus copy-paste `git ls-remote` / `git diff`
    hints (as the old demo's tail did). `--detach`: print the N function-call ids + the
    `modal app logs decode-headless` line and exit immediately (true fire-and-forget).
  - `--attempts 1` is legal (plain fire-and-forget single run); `--repo` required when
    `--attempts > 1` (unshipped attempts cannot be compared).
- **Pure helpers unit-tested** (`tests/unit/scripts/test_modal_headless.py` extended): task
  suffixing, table-row formatting from result payloads, attempts/repo argument validation.

## Acceptance Criteria

- [ ] `uv run modal deploy scripts/modal_headless.py` publishes the `decode-headless` app (documented; [HUMAN] to execute).
- [ ] The `attempts` entrypoint validates inputs client-side: `--attempts 0` and `--attempts 3` without `--repo` each fail with one friendly line, unit-tested.
- [ ] The spawned task text carries the push-ban paragraph verbatim — unit-tested.
- [ ] Table rows render correctly from result payloads for shipped, not-shipped, and failed attempts — unit-tested.
- [ ] `--detach` prints one function-call id per attempt and exits without waiting — unit-tested at the helper level.
- [ ] [HUMAN] `… ::attempts --task "add a hello line to README and commit" --repo <writable-repo> --attempts 3 --sandbox-mode modal` completes with ≥1 shipped attempt; `git ls-remote <repo> 'refs/heads/decode/*'` shows one branch per shipped attempt; the printed table matches.
- [ ] [HUMAN] Wall-clock for 3 attempts ≈ one attempt (parallel, no warm-up run).
- [ ] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator compares three attempts at one refactor
1. Operator has deployed the app once (`modal deploy`)
2. Runs the `attempts` entrypoint with `--attempts 3 --repo <their repo>` and a refactor task
3. ~One attempt's wall-clock later, a table lists 3 rows: session id, branch, shipped, churn hints
4. Operator pastes the printed `git diff` commands to compare branch vs branch

### Story: Operator fires and forgets overnight
1. Operator adds `--detach` to the same command and closes the laptop
2. Next morning: `git ls-remote <repo> 'refs/heads/decode/*'` lists the shipped branches;
   `uv run kitaru session list --agent decode --origin recorded` shows N recorded sessions;
   `modal app logs decode-headless` has the per-attempt logs

## Out of scope

- `--pr` auto-PR opening (operator one-liner; see feature plan out-of-scope).
- Cross-attempt evaluation/scoring — that is Kitaru's cohort/evaluator surface (08_evals_replays.md).

---

Refs: ADR-0020 §1, ADR-0012 §8, retired `scripts/demo-multiple-attempts.sh` (deleted in 141; git history)

## Log
