---
id: 142
feature: modal-remote-headless
status: pending
---

# Modal Headless App — `scripts/modal_headless.py`: in-app image + synchronous `modal run` path

Tags: `infra`, `runtime`, `enhancement`
Depends on: None (141 recommended first; no code dependency)
Blocks: 143, 145

This task implements ADR-0020 §1–§4 (launch surface: the synchronous half). An operator script
(operator scripts live in `scripts/`, outside the `decode` import graph), mirroring
`scripts/register_kitaru_agent.py`'s shape: pure, unit-tested helpers + a thin Modal surface.

## Scope

- **App + image.** `modal.App("decode-headless")` in `scripts/modal_headless.py`. Image built
  **in-app** with `modal.Image` — uv-synced deps + the local decode source baked at build
  (suggested: `modal.Image.debian_slim(python_version="3.12")` + the uv-project sync API
  (`Image.uv_sync()` or equivalent — verify the exact current API against Modal docs via
  context7) + `apt_install("git")`; mirror `src/decode/sandbox/modal_backend.py`'s in-code image
  idioms). NOT `flow.Dockerfile`, NOT a registry. The repo lands at a fixed in-image path with
  its `.venv`, so the `decode` console script exists at a deterministic absolute path.
- **Function `run_task(task, repo=None, sandbox_mode="none", model=None)`** — runs `decode run`
  as a **subprocess** of the baked console script (the same surface a laptop and agent v3 use),
  in a Harness Home OUTSIDE the repo checkout (ADR-0012 §6), streaming child stdout/stderr into
  the function log; returns a small dict: answer (or its tail), decode session id, shipped
  Session Branch or None, exit code. Timeout generous (same 1800s default reasoning as agent
  registration; overridable).
- **Sandbox compatibility: `none` and `modal` ONLY** (ADR-0020 §2):
  - Validate `sandbox_mode` in the local entrypoint BEFORE `.remote()` (no container spend on a
    typo) AND defensively in-function. `docker` → exactly ONE friendly `Decode:`-prefixed line
    ("no Docker daemon on Modal — use none or modal") + non-zero.
  - `none`: the gVisor container itself is the isolation. `repo` given → the HARNESS clones it
    into an in-container scratch dir (plain `git clone`) and launches decode with that cwd —
    decode never sees `--repo`, so its ADR-0012 §3 guard stays intact. No Hand-back on this
    path (documented in the returned payload / log line).
  - `modal`: pass `--repo` through to `decode run --repo` — decode-native clone, nested Modal
    Sandbox via the existing `ModalBackend` (its sandboxes land in `decode-sandbox-local`,
    since `DECODE_ENV=local`), Hand-back ships `decode/<session-id>`.
- **Nested-sandbox auth (`modal` mode).** Verify `modal.Sandbox.create` from inside the
  Function authenticates via the container's ambient Modal identity. If decode's
  `cli._modal_credentials_present()` presence guard false-negatives in-container (it checks
  only the token env pair and `~/.modal.toml`), extend it with the in-container identity marker
  — keep it env-presence-only, no `modal` import (ADR-0011 §1). This is the ONLY permitted
  `src/` change in this task; anything larger → stop and escalate to the PA.
- **Secret `decode-headless`** attached via `modal.Secret.from_name("decode-headless")`:
  provider keys + `KITARU_API_URL` + `KITARU_API_KEY` + `KITARU_AGENT_ID` + optional
  `SANDBOX_GIT_TOKEN` (ADR-0020 §4). `DECODE_ENV=local` set in the function env — process env
  feeds `Settings`; no Environment Bucket, no `.env` in-container. Recording Seam semantics
  unchanged: user-launched run → graceful degrade with ONE warning if the workspace is
  unreachable.
- **Hand-back credential.** When `SANDBOX_GIT_TOKEN` is present, configure the container's git
  credential helper off it (reuse `decode.sandbox.workspace.GIT_CREDENTIAL_HELPER` /
  `sandbox_git_token()` idioms) so the host-side (= container-side) `git push` works; absent →
  Hand-back skips gracefully with its existing friendly line. Never log the token value.
- **Sync entry:** `@app.local_entrypoint()` →
  `uv run modal run scripts/modal_headless.py --task "…" [--repo …] [--sandbox-mode none|modal] [--model …]`.
- **Tests:** `tests/unit/scripts/test_modal_headless.py` driving the pure helpers — decode argv
  building per mode, sandbox-mode validation (docker rejection line), none-mode clone-cwd
  resolution, result-payload shaping — no Modal calls, `subprocess` mocked (mirror
  `test_register_kitaru_agent.py`).

## Acceptance Criteria

- [ ] `scripts/modal_headless.py` exists; image is built in-app from uv-synced deps + local source; `grep -rn "Dockerfile" scripts/modal_headless.py` returns nothing.
- [ ] `sandbox_mode="docker"` is rejected client-side with exactly ONE `Decode:`-prefixed friendly line and a non-zero exit — unit-tested; no Modal object is created on that path.
- [ ] Unit tests cover: argv for `none` (no `--repo` passed to decode; cwd = the harness clone when `repo` given), argv for `modal` (`--repo` passed through), model override pass-through, docker rejection.
- [ ] `SANDBOX_GIT_TOKEN` handling: helper configures git credentials only when present; unit test asserts no token value appears in any built argv or log format string.
- [ ] [HUMAN] `uv run modal run scripts/modal_headless.py --task "run bash to print uname -a and pwd and report both" --sandbox-mode none` returns a Linux answer naming an in-container path — proof the run executed on Modal, not the laptop.
- [ ] [HUMAN] Same task with `--sandbox-mode modal` reports `/workspace` — proof the nested Modal Sandbox executed the bash, with no laptop `modal token` involved beyond launching.
- [ ] [HUMAN] After a run, `uv run kitaru session list --agent decode --origin recorded --size 3` shows the new Kitaru Session (Recording Seam on via the secret).
- [ ] [HUMAN] With `SANDBOX_GIT_TOKEN` in the secret: `--sandbox-mode modal --repo <writable-repo>` and a commit-but-don't-push task ships a `decode/<session-id>` branch (`git ls-remote <repo> 'refs/heads/decode/*'`); with the key absent from the secret, the run still answers and prints the Hand-back skip line.
- [ ] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator fires one remote headless run from a clean laptop
1. Operator creates the secret once: `modal secret create decode-headless GEMINI_API_KEY=… KITARU_API_URL=… KITARU_API_KEY=… KITARU_AGENT_ID=…` (values never committed)
2. Operator runs `uv run modal run scripts/modal_headless.py --task "explain what this repo does" --sandbox-mode none`
3. Terminal streams the run; the final answer prints; exit 0
4. `uv run kitaru session list --agent decode --origin recorded --size 1` shows the session

### Story: Operator ships work back from a fully remote run
1. Operator adds `SANDBOX_GIT_TOKEN` to the `decode-headless` secret (scoped, revocable PAT)
2. Runs `… --sandbox-mode modal --repo https://github.com/you/repo.git --task "add a hello line to README, commit, do NOT push"`
3. Output ends with the Hand-back line naming `decode/<session-id>`
4. `git ls-remote` shows the branch; the laptop's working tree and `.decode/sandbox` are untouched

### Story: Operator typos docker mode and pays nothing
1. Operator runs `… --sandbox-mode docker`
2. One friendly line: docker is unavailable on Modal (no daemon) — use `none` or `modal`
3. Exit non-zero before any container starts

## Out of scope

- Fire-and-forget / N attempts (task 143). Docs rewrite (task 146).
- Any Environment Bucket usage in-container (ADR-0020 §4: `DECODE_ENV=local` + secret env).
- Changing decode's `--repo`-under-`none` guard (ADR-0012 §3 stands; the harness clone covers it).

---

Refs: ADR-0020 §1–§4, ADR-0012 §3/§6/§8, ADR-0016 §2/§4, ADR-0019 §3

## Log
