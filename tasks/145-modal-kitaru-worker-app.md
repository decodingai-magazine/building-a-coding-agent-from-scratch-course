---
id: 145
feature: modal-remote-headless
status: pending
---

# Modal-hosted Kitaru Worker — scripts/modal_kitaru_worker.py: replays execute remotely

Tags: `infra`, `runtime`, `enhancement`
Depends on: 142 (shared in-app image build), 144 (agent version 3 registered)
Blocks: 146

This task implements ADR-0020 §3–§5. A long-running deployed Modal Function runs
`kitaru worker start`, so replays/experiments execute on Modal instead of the operator's
laptop. The laptop worker (agent v2, docker) remains the other, unchanged option.

## Scope

- **App `decode-kitaru-worker`** in `scripts/modal_kitaru_worker.py`. Image: **share the image
  build with `scripts/modal_headless.py`** — extract the builder into a helper importable by
  both scripts (second concrete caller; keep it in `scripts/`, never in `src/`). The image
  bakes the repo + `.venv` at the fixed paths agent v3's run spec names (task 144) — a drifted
  path is a spawn failure, so the paths are constants shared with 144's documented registration.
- **Function `run_worker(concurrency: int = 4)`**:
  - Creates the in-container Harness Home dir (v3's `--working-dir`) before starting.
  - **Defensively drops `KITARU_AGENT_ID`** from the process env with one logged line if
    present — a configured agent id makes the Recording Seam probe an agents route the worker's
    task-scoped token cannot use → 403 hard-fail (the documented pitfall: tasks/139,
    08_evals_replays.md §7.3, ADR-0020 §4).
  - Runs `kitaru worker start --concurrency <N>` as a subprocess with **scoped claims** —
    restrict to this workspace's decode replay/evaluator work so the Modal worker never claims
    jobs needing local files (e.g. imports); exact scoping flags per `kitaru worker start
    --help` at implementation time (verify against kitaru docs), streamed to the function log.
  - Long-running: Modal's max function timeout (24h); on expiry the worker simply dies —
    claimed-task handling is kitaru's own timeout story, documented, not engineered around.
- **Secret `decode-kitaru-worker`** attached: `KITARU_API_URL` + `KITARU_API_KEY` + provider
  keys; deliberately **NO `KITARU_AGENT_ID`** (see above; the defensive drop is the backstop,
  the secret's composition is the rule). `DECODE_ENV=local`.
- **Launch surfaces:** `uv run modal deploy scripts/modal_kitaru_worker.py` +
  `uv run modal run --detach scripts/modal_kitaru_worker.py [--concurrency N]` for a worker
  that outlives the terminal. Document observe/stop in the module docstring:
  `uv run kitaru worker list` (live: True), `modal app logs decode-kitaru-worker`,
  `modal app stop decode-kitaru-worker`. (Full operator docs: task 146.)
- **Tests:** `tests/unit/scripts/test_modal_kitaru_worker.py` — worker argv building
  (concurrency, scoping flags), the `KITARU_AGENT_ID` scrub, harness-home creation; subprocess
  and modal mocked.

## Acceptance Criteria

- [ ] Shared image helper used by both `modal_headless.py` and `modal_kitaru_worker.py`; no copy-pasted build block; still no Dockerfile, no registry.
- [ ] `run_worker`'s env scrub removes `KITARU_AGENT_ID` and logs one line naming why — unit-tested.
- [ ] Worker argv includes `--concurrency` and the claim-scoping flags — unit-tested.
- [ ] The in-container decode binary path and Harness Home constants match what task 144 documents for the v3 registration — single source, asserted by a unit test if both live in `scripts/`.
- [ ] [HUMAN] `modal deploy` + `modal run --detach …` starts the worker; from the laptop `uv run kitaru worker list` shows it live.
- [ ] [HUMAN] `uv run kitaru replay create <recorded-session-id> --agent decode@3 --evaluator '<existing evaluator>' --tool-policy '{"default":{"type":"history","scope":"baseline","on_miss":"error_result"}}' --evaluate-baselines` + `kitaru job watch <job-id>`: the Modal worker claims it and the replay reaches a terminal state with **agent-level** output (a provider 503 counts as success for the pipe — 08_evals_replays.md §7.8); no `ModuleNotFoundError` / command-not-found spawn errors.
- [ ] [HUMAN] The laptop worker + agent v2 (docker) still replays — start it per 08_evals_replays.md §5 and confirm one claim; the two workers coexist.
- [ ] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator moves replay execution off the laptop
1. Operator creates the secret: `modal secret create decode-kitaru-worker KITARU_API_URL=… KITARU_API_KEY=… GEMINI_API_KEY=…` — and deliberately no `KITARU_AGENT_ID`
2. `modal deploy`, then `modal run --detach scripts/modal_kitaru_worker.py`
3. `uv run kitaru worker list` from the laptop shows the Modal worker live
4. Operator closes the laptop; a colleague's `kitaru replay create … --agent decode@3` still executes, on Modal

### Story: The 403 trap cannot fire
1. Operator mistakenly adds `KITARU_AGENT_ID` to the worker secret
2. At startup the function logs one line that it dropped the variable and why (task-scoped tokens cannot use agents routes)
3. Replays claim and run instead of hard-failing with `403: Task credentials are not accepted on this route`

## Out of scope

- Self-hosted Kitaru server (feature-level exclusion). Auto-restart/cron re-launch of an
  expired worker — re-run the one command; revisit only if 24h expiry proves painful.
- Importer jobs on the Modal worker (they read local export files; laptop-only).

---

Refs: ADR-0020 §3–§5, ADR-0019 §4 + Amendments §2–3, `tasks/139-worker-lazy-session-failure-one-line.md`, 08_evals_replays.md §5/§7.3

## Log
