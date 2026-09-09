---
status: done
feature: remote-headless-article-6
---

# Modal Headless App: a `nightly` cron and a proxy-authed `webhook` trigger

Tags: `infra`, `feature`
Depends on: 150
Blocks: —

Filed from the article-6 criteria audit: the article promises runs "hooked to CRON jobs, webhooks or
any other events", and every remote run still began with a human typing `modal run`.

## Scope

- `scripts/modal_image.py`: `build_image(extra_packages=…)` installs app-only pip requirements into
  the SAME venv, between the locked-deps layer and the source layer (the Worker image passes none).
- `scripts/modal_headless.py`:
  - `nightly` — `@app.function(schedule=modal.Cron(…))`; schedule + job read from the laptop's
    `DECODE_NIGHTLY_*` env at `modal deploy`: schedule on the Function, job as a
    `Secret.from_dict` env. No cron env → no schedule (a plain deploy is unchanged). A cron without a
    task / docker mode / non-numeric ceiling dies on the laptop with one line. Runs
    `run_task.local(**kwargs)`.
  - `webhook` — `@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)`; JSON body =
    `::main`'s knobs (`task` required); `spawn`s `run_task`, answers with call id + watch commands;
    400 with the shared one-line message for docker / empty task. Holds no Secret.
  - `--max-requests` threaded through `::main`, `::attempts`, nightly (`DECODE_NIGHTLY_MAX_REQUESTS`)
    and the webhook body to `decode run --max-requests`.
- Docs: 07_infra §2b, ADR-0020 Amendment §8, glossary, AGENTS.md, README.

## Acceptance Criteria

- [x] Pure helpers unit-tested: schedule on/off, job env picks only set nightly keys (never the cron, never a provider key), config errors (no task, docker, bad count), run kwargs + defaults, webhook error/kwargs/response.
- [x] `nightly.local()` calls `run_task.local` with the env's kwargs; unconfigured → one line, nothing runs.
- [x] `webhook.local()` spawns once with the body's kwargs; a bad body is a 400 and spawns nothing.
- [x] `modal deploy scripts/modal_headless.py` builds the image (FastAPI layer) and registers `nightly` (no schedule) + `webhook` (URL printed).
- [x] one POST to the webhook with `Modal-Key`/`Modal-Secret` returned `{"status": "spawned", "call_id": "fc-01M1HVD6…"}` (HTTP 200); `FunctionCall.from_id(...).get()` returned `exit_code=0`, answer `Hello! How can I help you today?`, session `47e32258-…`.
- [ ] [HUMAN] a deploy with `DECODE_NIGHTLY_CRON` prints the registered line and the schedule shows on the Modal dashboard.

## Log
### [SWE] 2026-09-02 20:00 — Implemented
Two Functions + 9 pure helpers + 24 unit tests; docs. See the operator gate above for the two [HUMAN] checks.
### [Tester] 2026-09-02 — e2e
Deployed `decode-headless` (image + `nightly` + `webhook` URL printed with the proxy-auth key icon). Webhook POST → spawned run → completed. Nightly deploy-with-schedule left to the operator (it starts a recurring paid job).
