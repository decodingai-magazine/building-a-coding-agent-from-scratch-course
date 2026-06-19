---
id: 010-tools-web-fetch
feature: m1-vanilla-agent
status: done
---

# Tools: web fetch (HTML→Markdown)

## Scope
A simple HTTP GET tool that converts HTML to Markdown to cut tokens (validated against claude-code's WebFetch).

## Acceptance criteria
- [x] `tools/web.py` `web_fetch(url)` uses `httpx.AsyncClient` GET, converts HTML→Markdown, returns text.
- [x] Size-capped; `settings.web_fetch_timeout_s` enforced.
- [x] Network is mocked in tests (no real requests under `filterwarnings=["error"]`).
- [x] Gated.

## Out of scope
- WebSearch; response caching; readability/boilerplate stripping beyond Markdown conversion.

## Dependencies
- Adds `httpx` + `markdownify`.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. HTML→Markdown adopted from claude-code per wiki validation.

### [SWE] 2026-06-19 23:55 — Implementation

**Files modified**
- `src/decode/tools/web.py` — new gated async `web_fetch(url)`: `httpx.AsyncClient` GET under `settings.web_fetch_timeout_s`, follows redirects, HTML→Markdown (script/style stripped), text passthrough, byte size-cap, error→`ModelRetry`.
- `src/decode/tools/registry.py` — registered `web_fetch` in `TOOL_SPECS` (`read_only=True`, still asked in v1).
- `pyproject.toml` / `uv.lock` — added `httpx>=0.28.1` + `markdownify>=1.2.2` (markdownify pulls `beautifulsoup4` transitively, used to fully strip `<script>`/`<style>`).
- `tests/unit/decode/tools/test_web.py` — new; full coverage via `httpx.MockTransport` (no real network).
- `tests/unit/decode/tools/test_registry.py` — extended the expected-tools / read-only assertions to include `web_fetch`.

**Tests**
- Unit: 256 passing, 0 failing (`make unit-tests`); the 21 new `test_web.py` tests + 5 `test_registry.py`.
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] `web_fetch(url)` uses `httpx.AsyncClient` GET, converts HTML→Markdown, returns text — `test_web.py::test_web_fetch_converts_html_to_markdown`, `::test_web_fetch_strips_script_and_style_content`, `::test_web_fetch_passes_plain_text_through_unchanged`.
- [x] Size-capped; `settings.web_fetch_timeout_s` enforced — `::test_web_fetch_caps_oversized_responses`, `::test_web_fetch_uses_the_configured_timeout`.
- [x] Network mocked, no real requests under `filterwarnings=["error"]` — every test uses `httpx.MockTransport`.
- [x] Gated — `::test_web_fetch_requires_approval_when_not_approved`, `::test_web_fetch_runs_through_the_agent_when_approved`.

**Design choices (documented per task brief)**
- `read_only=True`: a GET has no *local* side effect (network egress only), so it is tagged read-only for M3's future auto-allow — but in v1 it STILL raises `ApprovalRequired` and is asked on every call, exactly like the read-only file tools.
- Size cap = **hard 2 MB raw-UTF-8 byte cap** (`_MAX_RESPONSE_BYTES`), truncated at a valid char boundary + a "clipped" notice. Chose a byte cap over the line-snapped `tools/truncate` because web content (minified HTML, single-line text) often has no line structure for `truncate` to snap to, so it would not actually bound the body.
- HTML detection by `Content-Type`; HTML/xhtml → markdownify (ATX headings); `text/*` + json/xml → passthrough; anything else → `ModelRetry`. `<script>`/`<style>` removed via BeautifulSoup before conversion (markdownify's `strip=` only drops tags, not their text bodies).
- Added up-front http(s) URL validation → `ModelRetry` (httpx mangles relative URLs deep in redirect/cookie handling; also makes the agent run deterministic since `TestModel` would synthesise an invalid `"a"`).
- Transport seam `_TRANSPORT` (module attr, `None` in prod) mirrors bash's `_EXECUTOR`; tests patch it with `httpx.MockTransport`. The agent run uses a `FunctionModel` (not `TestModel`) so it can pin a real `https://` URL.

**Evidence**
```
$ make unit-tests
... tests/unit/decode/tools/test_web.py .....................        [ 84%]
    tests/unit/decode/tools/test_registry.py .....                   [ 70%]
============================= 256 passed in 4.98s ==============================

$ make format-check && make lint-check
63 files already formatted
All checks passed!

$ uv lock --check
Resolved 166 packages in 3ms

# End-to-end against a loopback HTTP server (real GET, not MockTransport):
1) gated unapproved -> ApprovalRequired
2) approved real GET output:
   HTTP 200 http://127.0.0.1:.../index.html
   # decode
   A [link](https://anthropic.com) and some text.   <- script/style content stripped
3) bad url -> ModelRetry - 'not-a-url' is not an http(s) URL; ...
4) connection refused -> ModelRetry
```

**Notes**
- No architectural fork: stayed within ADR-0002 §7 (httpx GET → HTML-to-Markdown) and §3 gating; reused the existing `_EXECUTOR`-style seam pattern and `settings.web_fetch_timeout_s`.
- DO NOT COMMIT — awaiting Tester review.

### [Tester] 2026-06-19 23:47 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 63 files formatted; `ruff check`: all checks passed)
- `uv lock --check`: PASS (resolved 166 packages, lockfile in sync)
- Unit tests: 256 passed / 0 failed (`make unit-tests`)
- Integration tests: 0 collected (no infra changes — feature is fully hermetic; not a regression)
- Warnings: 0 (suite runs under `filterwarnings=["error"]`; any warning would have errored)
- Hermeticity proof: re-ran `test_web.py` (21 tests) with `socket.socket.connect` globally monkeypatched to raise → all 21 passed, **zero real network** (MockTransport-backed throughout).

**E2E adversarial pass** (unit-level direct calls + a real-agent `FunctionModel(web_fetch)`+approval run, all MockTransport-backed; real network blocked at the socket layer)
- Happy path: HTML `<h1>`+`<a>` → `# Heading` + `[link](url)`; status+final URL reported (PASS)
- (a) HTML→Markdown, headings/links kept, `<script>`/`<style>` BODIES gone: injected `var secret_token='sk-LEAKED-12345'` + `.x{color:#bada55}` + `alert('xss')` → none present in output (PASS)
- (b) text/plain passthrough verbatim (markdown-looking text not converted) (PASS)
- (c) `image/png` → `ModelRetry` naming the content type (PASS)
- (d) HTTP 404 and 500 → `ModelRetry` (no crash), status echoed in message (PASS)
- (e) timeout (`MockTransport` handler raising `httpx.TimeoutException`) → `ModelRetry` ("…timed out after 30s") (PASS)
- (f) connection error (`httpx.ConnectError`) → `ModelRetry` (PASS)
- (g) size cap: ~3 MB body with multibyte chars straddling the cap → clipped at a valid UTF-8 boundary, notice appended, returned bytes=2,000,099 (≤ cap+prefix/notice slack), **no U+FFFD replacement char** → bounded memory, no split-char corruption (PASS)
- (h) gating: unapproved → `ApprovalRequired`, **0 requests** reached the transport (gated before connect) (PASS)
- (i) scheme reject `file://` / `ftp://` / `javascript:` / `data:` → `ModelRetry`, **0 requests** each (PASS)
- (i) redirect chain 301→302→200 → final URL `/c` reported (PASS)
- (i) Rich-markup-like text (`[bold red]…[/bold red]`) preserved verbatim as data (no eating/injection at the tool layer; render-escaping is the TUI's job) (PASS)
- (i) huge `Content-Length: 999999999` header, tiny body → returns in <10 s, no hang / no pre-alloc (PASS)
- (i) gzip-encoded body → httpx transparently decompresses, then converts (PASS)
- (i) empty 200 HTML body → benign empty-ish string, no crash (PASS)
- (i) genuinely header-less response (raw bytes, no `Content-Type`) → correctly refused as non-text `ModelRetry` (PASS) *(an earlier probe that used `text=` was a harness artifact — httpx auto-synthesizes `text/plain` for `text=`; corrected with `content=` raw bytes)*
- (i) **malformed/deeply-nested HTML → `RecursionError` (FAIL — see AC list)**

**Acceptance criteria**
- [x] PASS — `tools/web.py` `web_fetch(url)` uses `httpx.AsyncClient` GET, converts HTML→Markdown, returns text
      Evidence: `test_web.py::test_web_fetch_converts_html_to_markdown`, `::test_web_fetch_strips_script_and_style_content`, `::test_web_fetch_passes_plain_text_through_unchanged`, `::test_web_fetch_reports_status_and_final_url` all pass; adversarial (a)/(b) confirm headings/links kept and script/style bodies absent.
- [x] PASS — Size-capped; `settings.web_fetch_timeout_s` enforced
      Evidence: `::test_web_fetch_caps_oversized_responses`, `::test_web_fetch_uses_the_configured_timeout` pass; adversarial (g) proves a ~3 MB body is clipped to ~2 MB at a valid char boundary with the notice and no replacement char; setting present at `src/decode/config/settings.py:31`.
- [x] PASS — Network is mocked in tests (no real requests under `filterwarnings=["error"]`)
      Evidence: re-ran all 21 web tests with `socket.connect` blocked → 21 passed, zero connect attempts; every test uses `httpx.MockTransport` via the `_TRANSPORT` seam.
- [x] PASS — Gated
      Evidence: `::test_web_fetch_requires_approval_when_not_approved`, `::test_web_fetch_runs_through_the_agent_when_approved`, `::test_web_fetch_is_tagged_read_only` pass; registered `read_only=True` in `registry.py` (`test_registry.py` updated); adversarial (h) proves 0 requests before approval.

**FAIL — unhandled `RecursionError` on deeply-nested HTML (not in AC, but breaks the "errors never crash the REPL" contract + the brief's "crashes on malformed HTML" probe)**
      Expected: a malformed/pathological HTML page maps to a `ModelRetry` (or is rendered) — never an unhandled exception that crashes the agent turn.
      Actual: a ~6.6 KB page of ~600 nested `<div>` (or `<blockquote>`/`<li>`/`<p>`) raises `RecursionError: maximum recursion depth exceeded` inside `markdownify.process_element` (recurses once per nesting level; Python default recursionlimit=1000, so ~500 levels is enough). The error escapes `web_fetch` unhandled — confirmed both at the unit level and **through the real agent loop** (`FunctionModel(web_fetch)` + approving resolver): `>>> RecursionError ESCAPED the agent turn -> crashes the loop`.
      Why it matters: `web_fetch` ingests arbitrary attacker-controlled remote HTML; this is reachable from the outside with a tiny payload **well under the 2 MB cap** (the byte cap does not protect against it). It is exactly the "non-2xx / timeout / connection / non-text → ModelRetry, never crash" guarantee in the module docstring, but for the malformed-HTML axis.
      Fix: wrap the markdownify conversion (`_html_to_markdown`, called from `_render_body`) so a `RecursionError` (and any unexpected conversion error) maps to a model-readable `ModelRetry` instead of escaping — e.g. catch `RecursionError` in `_render_body`/`_get`'s caller. The guard must cover the whole conversion call, not one tag type (reproduced with `<div>`, `<blockquote>`, `<li>`, `<p>`). Add a regression test (deeply-nested HTML → `ModelRetry`, asserted both at the unit level and that the agent turn does not crash). Note: the BeautifulSoup `decompose()` loop is iterative and survives; only `markdownify` recurses.

**Evidence**
```
$ make unit-tests
... tests/unit/decode/tools/test_web.py .....................   [ 84%]
    tests/unit/decode/tools/test_registry.py .....              [ 70%]
============================= 256 passed in 4.92s ==============================

$ make format-check && make lint-check && uv lock --check
63 files already formatted
All checks passed!
Resolved 166 packages in 2ms

# hermeticity: 21 web tests with socket.connect blocked
21 passed in 0.97s

# adversarial recursion (through the real agent loop):
page size: 6630 bytes (cap is 2000000)
>>> RecursionError ESCAPED the agent turn -> crashes the loop
```

**Other issues found**
- Non-blocking (SSRF, per brief): `web_fetch` will GET any `http(s)` host including `127.0.0.1`/`localhost`/`169.254.169.254` (cloud metadata) and follows redirects to them. Acceptable for M1 (every call is human-approved and gated before connect); flag for a later milestone (SSRF allow/deny list, redirect re-validation, private-IP block) — not a blocker now.
- Non-blocking: redirects are followed without re-checking each hop's scheme/host against the up-front `http(s)` validation. Same milestone as the SSRF note. Not exploitable in v1 (httpx only follows `http(s)` redirect targets).
- Note (no action): `_TEXT_CONTENT_TYPES`/`_TEXT_CONTENT_PREFIXES` correctly refuse a header-less response as non-text (verified). Behaviour is correct.

**VERDICT: FAIL** — 1 blocking defect: unhandled `RecursionError` on deeply-nested HTML escapes `web_fetch` and crashes the agent turn, violating the "errors never crash the REPL" contract. All 4 acceptance criteria otherwise verified, full suite green (256 passed, 0 warnings), format/lint/lock clean, hermetic. SWE: map the markdownify conversion failure to `ModelRetry` and add a deeply-nested-HTML regression test; then re-review.

### [SWE] 2026-06-20 — Fix: guard HTML→Markdown conversion against RecursionError

Addresses the QA blocker (unhandled `RecursionError` on deeply-nested HTML escapes `web_fetch` and crashes the turn).

**Files modified**
- `src/decode/tools/web.py` — wrapped the entire HTML→Markdown conversion in `_html_to_markdown` (BeautifulSoup decompose loop + the `markdownify` call) in a `try`; `RecursionError` and any other unexpected conversion exception now map to a model-readable `ModelRetry` ("could not parse the HTML at <url> (too deeply nested / malformed); try a different page") instead of escaping. `_html_to_markdown` now takes the response `url` so the retry names the page. Did NOT bump `sys.setrecursionlimit` (would only move the cliff / risk a C-stack segfault) — catching `RecursionError` at this boundary is the correct fix.
- `tests/unit/decode/tools/test_web.py` — added 3 regression tests + a helper `_deeply_nested_html(depth=600)` (`"<div>"*600 + "x" + "</div>"*600`, ~6.6 KB, asserted under the byte cap so the cap gives no protection).

**Tests added**
- `test_web_fetch_deeply_nested_html_returns_model_retry` — unit: nested payload via MockTransport → `ModelRetry` (not `RecursionError`), message names the URL and "parse".
- `test_web_fetch_unexpected_conversion_error_returns_model_retry` — unit: a mocked `markdownify` raising `ValueError` also maps to `ModelRetry` (guard covers the whole conversion, not just recursion).
- `test_web_fetch_deeply_nested_html_does_not_crash_the_turn` — through the real agent loop (`FunctionModel` + approving resolver): the turn does NOT crash; the failure surfaces as a `RetryPromptPart(tool_name="web_fetch")` naming "parse" that the model acts on.

**Acceptance criteria**
- [x] Errors never crash the REPL — malformed/pathological HTML now maps to `ModelRetry` (verified `test_web_fetch_deeply_nested_html_returns_model_retry`, `::test_web_fetch_deeply_nested_html_does_not_crash_the_turn`, `::test_web_fetch_unexpected_conversion_error_returns_model_retry`).
- All 4 original AC remain green; no behaviour change for normal pages.

**Evidence**
```
$ .venv/bin/python -m pytest tests/unit/decode/tools/test_web.py -q
24 passed in 1.05s        # 21 original + 3 new

$ make pre-commit
============================= 259 passed in 4.97s ==============================   # 256 + 3 new

$ make format-check && make lint-check && uv lock --check
63 files already formatted
All checks passed!
Resolved 166 packages in 3ms

# end-to-end smoke (real _html_to_markdown):
payload bytes: 6601 (cap is 2000000)
OK ModelRetry: Could not parse the HTML at https://evil.example/nested (too deeply nested); try a different page.
normal markdown -> '# Hi\n\nbody [l](https://x/y)'   # normal page still real Markdown
```

**Notes**
- The through-the-agent regression test originally hung: a `ModelRetry` comes back to the model as a `RetryPromptPart` (not a `ToolReturnPart`), so the prior `_already_fetched` (ToolReturnPart-only) check made the FunctionModel re-call `web_fetch` forever. Fixed the test model to settle on the `RetryPromptPart` (new `_web_fetch_retried` helper) and assert on the retry content.
- SSRF (localhost / link-local / metadata) and redirect-hop re-validation left as-is per the brief (deferred to a later milestone) — not touched.
- NOT committed, per instructions.

### [Tester] 2026-06-20 — RE-QA (RecursionError blocker fix)

Re-review of the SWE fix that wraps the entire HTML→Markdown conversion in `_html_to_markdown`
(BeautifulSoup decompose loop + `markdownify`) so `RecursionError`/any conversion exception map to
a model-readable `ModelRetry` instead of escaping and crashing the turn.

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 63 files formatted; `ruff check`: all checks passed; `make pre-commit` exit 0)
- `uv lock --check`: PASS (resolved 166 packages, lockfile in sync)
- Unit tests: 259 passed / 0 failed (`make pre-commit`); 256 prior + 3 new regression tests
- Integration tests: 0 collected (exit 5 — no infra changes; feature is fully hermetic; not a regression, same as prior QA)
- Warnings: 0 (suite runs under `filterwarnings=["error"]`; re-ran web+registry under `-W error` → 29 passed)
- Hermeticity proof: re-ran all 24 `test_web.py` tests with `socket.socket.connect` globally blocked (autouse fixture) → 24 passed, zero real connect attempts. MockTransport-backed throughout.
- `code-review` plugin: enabled in `.claude/settings.json`; folded into the manual review (diff is small/focused — web.py guard + registry registration + 2 test files + pyproject/uv.lock). No defects beyond the items below; the `except Exception` is scoped to the conversion only (does not mask unrelated bugs); logging uses the module logger, no `print()`.

**E2E adversarial pass** (own crash probe — not trusting the SWE's tests alone; unit-level direct calls + the real agent loop via `FunctionModel(web_fetch)` + approving resolver; real network blocked at the socket layer)
- Happy path: normal HTML `<h1>`+`<a>`+`<script>`+`<style>` → `HTTP 200 …\n\n# Hi\n\nbody [l](https://x/y)`; script/style bodies stripped (PASS — no regression)
- Break path 1 (boundary: deeply-nested HTML, the exact brief payload `"<div>"*800 + "x" + "</div>"*800`, 8801 B, **under** the 2 MB cap): `web_fetch` → `ModelRetry` ("too deeply nested", names the URL), NOT `RecursionError` (PASS)
- Break path 1b (guard spans the whole conversion, not one tag type): `<blockquote>`*800 (20001 B), `<li>`*800 (7201 B), `<p>`*800 (5601 B) — each under cap → each `ModelRetry`, NOT `RecursionError` (PASS)
- Break path 2 (through the REAL agent loop): nested-`<div>` page fetched + approved → turn does **not** crash (`crashed=None`); failure surfaces as a `RetryPromptPart(tool_name="web_fetch")` naming "parse" that the model acts on (PASS — the original blocker no longer escapes the loop)
- Break path 3 (malformed/garbage HTML): unclosed tags, NUL/`\x01\x02` bytes in HTML, broken attributes, 5000 raw `<`/`>`, mismatched-deep `<ul><li>*400</ol>*400` → all return text or `ModelRetry`, **none crash** (PASS)
- Break path 4 (guard covers any conversion exception, not just recursion): monkeypatched `markdownify` to raise `ValueError` → `ModelRetry` ("malformed"), no escape (PASS — matches `test_web_fetch_unexpected_conversion_error_returns_model_retry`)
- Regression re-confirm of prior-green axes (verified via the named tests + probe): HTML→Markdown with script/style bodies gone, text/plain + JSON passthrough verbatim, `image/png`→`ModelRetry`, 404/500→`ModelRetry`, timeout→`ModelRetry`, conn-error→`ModelRetry`, 2 MB byte cap with clipped notice at a valid char boundary, gating (0 requests when unapproved), non-http(s)/`file://`/`ftp://`/empty rejected before connect, redirect chain reports final URL (ALL PASS)

**Acceptance criteria**
- [x] PASS — `tools/web.py` `web_fetch(url)` uses `httpx.AsyncClient` GET, converts HTML→Markdown, returns text
      Evidence: `test_web.py::test_web_fetch_converts_html_to_markdown`, `::test_web_fetch_strips_script_and_style_content`, `::test_web_fetch_passes_plain_text_through_unchanged`, `::test_web_fetch_reports_status_and_final_url` pass; probe happy path confirms `# Hi` + `[l](https://x/y)` and script/style bodies absent; `src/decode/tools/web.py:88` (`async def web_fetch`), `:155` (`client.get`), `:215`/`:237` (HTML→Markdown).
- [x] PASS — Size-capped; `settings.web_fetch_timeout_s` enforced
      Evidence: `::test_web_fetch_caps_oversized_responses`, `::test_web_fetch_uses_the_configured_timeout` pass; byte cap `_MAX_RESPONSE_BYTES = 2_000_000` at `src/decode/tools/web.py:66`, applied in `_cap` (`:221`); timeout wired in `_client` (`:182`); setting at `src/decode/config/settings.py:31` (`web_fetch_timeout_s: float = 30.0`).
- [x] PASS — Network is mocked in tests (no real requests under `filterwarnings=["error"]`)
      Evidence: re-ran all 24 web tests with `socket.connect` blocked → 24 passed, zero connect attempts; every test uses `httpx.MockTransport` via the `_TRANSPORT` seam (`src/decode/tools/web.py:85`); web+registry under `-W error` → 29 passed, 0 warnings.
- [x] PASS — Gated
      Evidence: `::test_web_fetch_requires_approval_when_not_approved` (0 requests when unapproved), `::test_web_fetch_runs_through_the_agent_when_approved`, `::test_web_fetch_is_tagged_read_only` pass; registered `read_only=True` in `registry.py:66-72`; `test_registry.py` updated (expected-tools / read-only / `is_read_only` all include `web_fetch`).

**Blocker re-verification (prior FAIL → now PASS)**
- [x] PASS — unhandled `RecursionError` on deeply-nested HTML no longer escapes `web_fetch`
      Prior actual: `>>> RecursionError ESCAPED the agent turn -> crashes the loop`.
      Now: guard in `_html_to_markdown` (`src/decode/tools/web.py:256-270`) maps `RecursionError`→`ModelRetry` ("too deeply nested") and any other conversion exception→`ModelRetry` ("malformed"), naming the URL. Verified at the unit level (div/blockquote/li/p, all under the byte cap) AND through the real agent loop (turn completes, surfaces a `RetryPromptPart`). 3 regression tests added and pass: `::test_web_fetch_deeply_nested_html_returns_model_retry`, `::test_web_fetch_unexpected_conversion_error_returns_model_retry`, `::test_web_fetch_deeply_nested_html_does_not_crash_the_turn`.

**Evidence**
```
$ make pre-commit
============================= 259 passed in 5.04s ==============================
$ make format-check && make lint-check && uv lock --check
63 files already formatted
All checks passed!
Resolved 166 packages in 3ms
# own crash probe (real network blocked at the socket layer):
PASS unit nested <div>*800 (8801B, under_cap=True)   -> retry: too deeply nested
PASS unit nested <blockquote>*800 (20001B, under_cap=True) -> retry
PASS unit nested <li>*800 (7201B, under_cap=True)    -> retry
PASS unit nested <p>*800 (5601B, under_cap=True)     -> retry
PASS garbage 'unclosed-tags' / 'binary-ish' / 'broken-attrs' / 'just-angle-brackets' / 'mismatched-deep' -> ok (no crash)
PASS markdownify ValueError -> retry: malformed
PASS normal HTML converts + script/style stripped
PASS through-agent nested page: crashed=None, retries_surfaced=True
12/12 passed
# hermeticity: 24 web tests with socket.connect blocked
24 passed in 1.03s
```

**Other issues found**
- Non-blocking (unchanged from prior QA, per brief — deferred to a later milestone): SSRF — `web_fetch` will GET any `http(s)` host including `127.0.0.1` / `localhost` / `169.254.169.254` and follows redirects without re-validating each hop's scheme/host. Acceptable for M1 (every call is human-approved and gated before connect). Not a blocker.

**VERDICT: PASS** — the previously-blocking unhandled `RecursionError` is fixed: deeply-nested/malformed HTML now maps to a model-readable `ModelRetry` (verified across div/blockquote/li/p variants, all under the byte cap, at the unit level AND through the real agent loop, plus arbitrary conversion exceptions and garbage HTML). All 4 acceptance criteria re-verified with evidence; full suite green (259 passed, 0 warnings); format/lint/lock clean; fully hermetic (24 web tests pass with sockets blocked); no regression on any prior-green behaviour. Hand off for commit.
