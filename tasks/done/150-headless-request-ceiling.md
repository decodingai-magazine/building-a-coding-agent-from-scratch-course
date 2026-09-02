---
status: done
feature: remote-headless-article-6
---

# `decode run --max-requests`: a request ceiling for runs nobody watches

Tags: `runtime`, `enhancement`
Depends on: None
Blocks: 151

Filed from the article-6 criteria audit: `decode run` had no stop condition of its own — the evals
driver caps model requests, the Modal app caps wall-clock, the headless runner capped nothing. A cron
or webhook run (task 151) has nobody watching its token bill.

## Scope

- `Settings.runtime_max_requests: int | None` (`RUNTIME_MAX_REQUESTS`, default `None` = unbounded,
  `gt=0`); `.env.example` entry.
- `decode run --max-requests N` (`IntRange(min=1)`), flag > setting > unbounded, threaded to
  `run_headless_task(..., max_requests=)` → `agent.run(usage_limits=UsageLimits(request_limit=N))`.
  Unbounded passes `usage_limits=None` — byte-identical to the REPL.
- Past the cap: `UsageLimitExceeded` escapes the runner (the `finally` still reaps + hands back),
  `decode run` prints ONE `Decode: the run stopped at its request ceiling (…)` line on stderr, stdout
  stays empty, exit 1.
- Docs: 03_runtime.md bullet, glossary Headless Runtime row, ADR-0020 Amendment §9.

## Acceptance Criteria

- [x] A scripted agent that never stops calling tools is cut off at N legs and raises `UsageLimitExceeded`.
- [x] The reap and the Hand-back still run on a capped run.
- [x] Unset at both levels → `usage_limits=None`; the setting caps alone; the flag wins over the setting.
- [x] `--max-requests 0` is a Click usage error; the CLI turns the cap into one friendly line + exit 1, no traceback, empty stdout.
- [x] `Settings`: default `None`, `RUNTIME_MAX_REQUESTS=0` rejected.
- [x] Full unit suite green; ruff format + check green.

## Log
### [SWE] 2026-09-02 20:00 — Implemented
Settings + CLI flag + runner `UsageLimits` + 10 unit tests; docs in 03_runtime / glossary / ADR-0020 §9. 2458 unit tests green.
### [Tester] 2026-09-02 — e2e
`LLM_PROVIDER=gemini decode run --max-requests 1 "List the python files under src/decode/runtime with bash, then …"` → one `Decode: the run stopped at its request ceiling (… request_limit of 1 …)` line on stderr, empty stdout, exit 1.

