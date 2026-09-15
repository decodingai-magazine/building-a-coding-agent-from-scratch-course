# 0023. Kitaru Workers run locally; the managed workspace is a results store, never a host

Status: Accepted
Date: 2026-09-15

Supersedes [ADR-0020](0020-remote-headless-on-modal.md) §5 (the Modal-hosted Kitaru Worker + its
`none` Agent Version) and the `decode-kitaru-worker` half of §4 (its Modal Secret). ADR-0020 §§1–3,
the `decode-headless` half of §4, §6 and every Amendment about the Modal Headless App stand.
[ADR-0019](0019-kitaru-replay-runtime.md) and [ADR-0022](0022-evals-v2-own-harbor-on-opik.md) §11 (a
Kitaru Server is one URL) stand unchanged.

## Context

ADR-0020 §5 put a Kitaru Worker on Modal so replays kept running with the laptop closed. In practice it
never ran for the course:

- It needs a server reachable from Modal. The default server is now the local OSS deployment
  (`make kitaru-local`, ADR-0022 §11) on `localhost:8000`, which a Modal container cannot dial, and the
  managed workspace is deactivated.
- A container cannot `kitaru login`, so it needs a control plane `ZENPROKEY_…` that was never minted
  (tasks/153).
- It cost a second Modal app, a second Secret, a second Agent Version (`SANDBOX_MODE=none`) every
  server had to carry, an extra layer in the shared image (`add_local_python_source("scripts")`), and a
  runbook (`07_evals_replays_deploy.md`) — all for a path no reader could follow end to end.

The course wants one replay story a reader can actually run: record, freeze a cohort, replay on a
Worker in your own shell.

## Decision

1. **Kitaru Workers run only on the operator's machine** (`kitaru worker start`). There is no hosted
   Worker: `scripts/modal_kitaru_worker.py`, its tests, the `decode-kitaru-worker-<env>` app + Secret and
   `running_the_code/07_evals_replays_deploy.md` are deleted. Clean break, no shim.
2. **One Agent Version.** `scripts/bootstrap_kitaru.py` registers only the laptop spec —
   `decode run`, `SANDBOX_MODE=docker`, repo clone, Harness Home outside the repo — so a fresh server
   names it `decode@1`. The `none` spec and its drift guard against the image's paths are gone.
3. **The only remote Kitaru surface is where results go.** `KITARU_API_URL` may name the managed
   workspace instead of the local server; recorded Sessions, cohorts and replay results land there.
   It stays a URL switch (ADR-0022 §11): the workspace stores, the laptop executes.
4. **The Modal image serves one app.** `decode.remote.image` loses `KITARU_BIN` and the `scripts`
   source layer; `DECODE_BIN` / `HARNESS_HOME` stay, read by `decode.remote.headless`. The Modal
   Headless App keeps recording through the Recording Seam unchanged.

## Consequences

- **Gained:** one replay path in the docs and the code; one Agent Version per server; one Modal app;
  a smaller image tail; no credential a reader cannot mint blocks any runbook step.
- **Lost (accepted):** replays stop when the laptop sleeps. A long cohort experiment needs a machine
  that stays up — any box you can run `kitaru worker start` on is a laptop for this purpose.
- **Operator action:** servers that already carry the retired `none` Agent Version (the course
  workspace's `decode@3`) keep it — versions are immutable and nothing here unregisters them. Target
  the `SANDBOX_MODE=docker` version; never match on the number.
- **Still open:** tasks/153's control plane key now gates only recording a Modal headless run into the
  managed workspace.
