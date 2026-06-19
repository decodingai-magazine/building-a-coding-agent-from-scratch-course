---
id: 010-tools-web-fetch
feature: m1-vanilla-agent
status: pending
---

# Tools: web fetch (HTML→Markdown)

## Scope
A simple HTTP GET tool that converts HTML to Markdown to cut tokens (validated against claude-code's WebFetch).

## Acceptance criteria
- [ ] `tools/web.py` `web_fetch(url)` uses `httpx.AsyncClient` GET, converts HTML→Markdown, returns text.
- [ ] Size-capped; `settings.web_fetch_timeout_s` enforced.
- [ ] Network is mocked in tests (no real requests under `filterwarnings=["error"]`).
- [ ] Gated.

## Out of scope
- WebSearch; response caching; readability/boilerplate stripping beyond Markdown conversion.

## Dependencies
- Adds `httpx` + `markdownify`.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. HTML→Markdown adopted from claude-code per wiki validation.
