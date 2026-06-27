---
id: 051-lsp-client-service
feature: lsp-integration
status: done
---

# Hand-rolled LSP Service: JSON-RPC-over-stdio client with a fakeable seam

Tags: `infra`, `lsp`, `services`
Depends on: #050
Blocks: #052, #053, #054, #056

This task implements ADR-0007 (LSP integration). It creates the **first** entry under `services/`
(AGENTS.md's target tree already reserves `services/` for "LSP servers"): a thin, hand-rolled
JSON-RPC-2.0-over-stdio client for one stdio **Language Server**. No protocol library — teaching the
wire is the point (no `multilspy`, no `lsprotocol`).

## Scope

Create `src/decode/services/lsp/` (with `__init__.py` and a `tests/unit/decode/services/lsp/`
mirror). Internal class/function/module names are the SWE's choice; the **contract and seams** below
are the requirement.

- **The client** (~120 lines): spawn the server as an asyncio subprocess
  (`asyncio.create_subprocess_exec(settings.lsp_server_command, *settings.lsp_server_args, ...)` with
  stdin/stdout pipes) under a given project root (`cwd`). Implement:
  - **Framing:** `Content-Length: <n>\r\n\r\n<json>` read/write on stdio (parse the header, read
    exactly `n` bytes, `json.loads`; write the symmetric frame).
  - **Handshake:** `initialize` request (rootUri = the cwd `file://` URI, minimal client
    capabilities incl. pull diagnostics) → await the response → send `initialized` notification.
  - **Per-file sync:** `textDocument/didOpen` with the file's current on-disk UTF-8 content + a
    `file://` URI before any position/diagnostic request (re-`didOpen`/`didChange` is acceptable —
    keep it simple; the on-disk file is the source of truth).
  - **Requests, matched by JSON-RPC `id`:** `textDocument/definition`,
    `textDocument/references` (with `context.includeDeclaration: true`), `textDocument/hover`, and a
    **pull** `textDocument/diagnostic` request→response (NO async `publishDiagnostics` handling).
    Each maps the LSP result into a small decode-native value object (locations as
    `(path, line, character)`, hover as text, diagnostics as `(severity, line, character, message)`),
    not raw LSP dicts leaking upward.
  - **Position basis:** the client speaks LSP's **0-based** line/character on the wire. Expose a
    1-based line/column surface to callers (decode is 1-based everywhere the user sees — `read`'s
    `cat -n`, `grep`'s `path:lineno`); convert at this boundary so task 052/053 never juggle bases.
  - **Shutdown:** `shutdown` request then `exit` notification, then terminate the subprocess; expose a
    callable the app-exit path (task 054) invokes. Idempotent and never raises.
- **Lazy, cached, per-root spawn behind a module-level seam — MIRROR `bash.py:45`'s `_EXECUTOR`
  and `web.py:80`'s `_TRANSPORT`:** a module-level cache keyed by project root spawns **one** server
  per root on first use and reuses it. A **failed/broken spawn is cached** (a sentinel) so a missing
  or crashing `ty` does not trigger a retry storm on every edit/tool call — that root is "unavailable"
  until the process restarts. The spawn point itself must be a **patchable seam** so unit tests inject
  a **fake process** whose stdout yields canned `Content-Length`-framed JSON-RPC responses (NO real
  `ty`, no real subprocess in unit tests — AGENTS.md test discipline).
- **Best-effort everywhere — never throw into the loop or the tool layer.** A spawn failure, a
  timeout (`settings.lsp_request_timeout_s` per request; bound the initialize too), a closed pipe, or
  a malformed frame resolves to a sentinel/`None` "unavailable", never an exception escaping the
  service. `settings.lsp_enabled == False` short-circuits to "unavailable" without spawning anything.
- **Async** throughout (asyncio subprocess + pipes) to fit decode's async tool/loop. Library code
  **logs** (module logger) — never `print`s. Type-annotate everything, including `-> None`.
- **Public surface** the next tasks consume (names SWE's choice; shape fixed):
  - an `async` way to run each of the four ops for a `(cwd, path, [line, column])`, returning the
    native value object or an "unavailable" signal;
  - a **sync** `diagnostics-on-edit` helper used by task 053's enricher (see that task) — it bridges
    sync→async internally; placing it here keeps one cache/one client;
  - an `async`/sync shutdown entry used by task 054.

## Acceptance criteria

- [x] `src/decode/services/lsp/` exists with `__init__.py`; `tests/unit/decode/services/lsp/` mirrors it.
- [x] A unit test drives the client against a **fake process** (patched spawn seam) feeding canned
      `Content-Length`-framed JSON-RPC responses, and asserts:
  - [x] `initialize`→`initialized` handshake is sent in order and the framing round-trips (a written
        frame parses back; a read frame is decoded by exact `Content-Length`).
  - [x] `definition` returns the canned location as `(path, 1-based line, 1-based column)`.
  - [x] `references` returns all canned locations (declaration included).
  - [x] `hover` returns the canned hover text.
  - [x] a pull `diagnostic` request returns the canned diagnostics as
        `(severity, 1-based line, 1-based column, message)` tuples.
  - [x] responses are matched to requests by JSON-RPC `id` (an out-of-order/interleaved canned
        response still resolves the right call).
- [x] **Lazy + cached:** two calls for the same root spawn the fake process **once** (seam invoked
      once); a different root spawns its own.
- [x] **Broken-spawn cached:** a spawn that fails (seam raises / process exits immediately) is cached
      as unavailable — a second call does NOT re-invoke the spawn seam (no retry storm); the op
      returns "unavailable", not an exception.
- [x] **Best-effort:** a request that times out (`lsp_request_timeout_s`, patched small) or a malformed
      frame yields "unavailable"; **no exception escapes** the service. Asserted by a unit test.
- [x] `lsp_enabled == False` returns "unavailable" without invoking the spawn seam (unit-tested).
- [x] No real `ty`/subprocess/network is started by any unit test; `make ci` green, 0 warnings.

## User stories

(The "user" here is the next task's caller + the test harness; stories are the test contract.)

### Story: A caller asks the service where a symbol is defined
1. Caller invokes the service's `definition` op for `(cwd, "pkg/mod.py", line=10, column=5)`.
2. The service lazily spawns the (fake, in tests) server, handshakes, `didOpen`s the file, sends
   `textDocument/definition`, and matches the response by id.
3. The caller receives `("pkg/other.py", 3, 1)` (1-based) — never a raw LSP dict, never an exception.

### Story: The configured server binary is missing
1. The spawn seam raises `FileNotFoundError` (server not on PATH).
2. The service caches the root as unavailable and returns "unavailable" for this and every subsequent
   op on that root.
3. The spawn seam is invoked exactly once — no retry storm — and nothing is raised into the caller.

### Story: A request hangs past the timeout
1. The fake server never answers a `diagnostic` request.
2. After `lsp_request_timeout_s` the service abandons the request and returns "unavailable".
3. The turn/edit that called it is unaffected (best-effort).

## Out of scope
- The `lsp` tool (052), the enricher's files.py wiring (053), app-exit wiring (054) — this task only
  builds the service + its seams + unit tests.
- A protocol library, `publishDiagnostics` push handling, non-stdio transports, multi-language routing.

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
The hand-rolled JSON-RPC-over-stdio LSP Service — decode's first `services/` entry. Lazy per-root
spawn, broken-spawn caching, best-effort, behind a patchable seam mirroring bash's `_EXECUTOR`.

**Key decisions**
- No protocol library — frame/handshake/requests by hand (teaching the wire).
- Module-level per-root cache + patchable spawn seam → unit tests feed canned frames, never spawn `ty`.
- Client owns the 0-based↔1-based conversion so tools/enricher stay 1-based like the rest of decode.
- Pull diagnostics only; best-effort returns "unavailable" rather than raising.

**Dependencies**
- #050 — `lsp_server_command`/`args`/`request_timeout_s`/`enabled` settings + the `ty` binary.

**User stories**
- 3 stories framed as the caller/test contract: definition happy-path, missing-binary (cached
  unavailable, one spawn), request timeout (best-effort).

**Open questions**
- None.

Ready for implementation.

### [SWE] 2026-06-27 14:05 — Implementation

**Files modified**
- `src/decode/services/__init__.py` — new Services Interface package (docstring only; first concrete entry is `lsp/`).
- `src/decode/services/lsp/__init__.py` — public surface re-export (`definition`/`references`/`hover`/`diagnostics`, `diagnostics_on_edit`, `shutdown_all`, value objects + `UNAVAILABLE`).
- `src/decode/services/lsp/types.py` — decode-native value objects `Location`, `Diagnostic` (both 1-based) + the `Unavailable`/`UNAVAILABLE` enum sentinel.
- `src/decode/services/lsp/client.py` — the hand-rolled JSON-RPC-2.0-over-stdio client: `Content-Length` framing, `initialize`→`initialized` handshake, per-file `didOpen`/`didChange`, the 4 ops + pull `diagnostic` matched by `id`, 0-based↔1-based conversion, `shutdown`/`exit`/terminate. Raises a private `LspError` on any wire failure.
- `src/decode/services/lsp/service.py` — module-level per-root cache + patchable `_spawn_process` seam (mirrors `bash._EXECUTOR`/`web._TRANSPORT`), broken-spawn `_BROKEN` sentinel caching, best-effort async ops, the sync `diagnostics_on_edit` enricher bridge (`anyio.from_thread.run`), and async `shutdown_all`.
- `tests/support/lsp_fakes.py` — `FakeLanguageServer` (parses the client's framed requests, feeds canned id-matched responses; supports hang/decoy/malformed) + `DeadProcess`; no subprocess, no network.
- `tests/unit/decode/services/{__init__,lsp/__init__,lsp/conftest}.py` — test-package mirror + autouse per-root cache reset.
- `tests/unit/decode/services/lsp/test_service.py` — 22 unit tests covering the full contract.
- `pyproject.toml` / `uv.lock` — `uv add anyio` (the sync bridge imports `anyio` directly).

**Tests**
- Unit: 22 passing, 0 failing (`tests/unit/decode/services/lsp/`).
- Full gate: `make ci` green — 871 passing, 0 warnings (lock-check + format-check + lint-check + full suite under `filterwarnings=["error"]`).

**Acceptance criteria**
- [x] package + test mirror exist — `src/decode/services/lsp/__init__.py`, `tests/unit/decode/services/lsp/`.
- [x] handshake order + framing round-trip — `test_definition_handshake_order_and_1based_location`, `test_framing_roundtrips_both_directions`.
- [x] `definition` 1-based location — `test_definition_handshake_order_and_1based_location` (+ wire-conversion `test_definition_converts_1based_position_to_0based_wire`).
- [x] `references` all locations, declaration included — `test_references_returns_all_locations_with_declaration`.
- [x] `hover` text — `test_hover_returns_text`.
- [x] pull `diagnostic` as `(severity, 1-based line, 1-based column, message)` — `test_diagnostics_returns_1based_tuples`.
- [x] matched by `id`, out-of-order tolerant — `test_out_of_order_response_resolves_by_id`.
- [x] lazy + cached (same root spawns once; different root its own) — `test_same_root_spawns_once`, `test_different_roots_spawn_separately`.
- [x] broken-spawn cached, no retry storm, returns unavailable — `test_broken_spawn_is_cached_no_retry_storm`, `test_dead_process_is_cached_unavailable`.
- [x] best-effort timeout / malformed → unavailable, no exception escapes — `test_request_timeout_returns_unavailable`, `test_malformed_frame_returns_unavailable`.
- [x] `lsp_enabled == False` → unavailable without spawning — `test_disabled_short_circuits_without_spawning`.
- [x] no real `ty`/subprocess/network in any unit test; `make ci` green, 0 warnings.

**Evidence**
```
$ uv run pytest tests/unit/decode/services/lsp/ -q
......................                                                   [100%]
22 passed in 0.10s

$ make ci   # uv lock --check + format-check + lint-check + full suite
============================= 871 passed in 8.95s ==============================

# Manual e2e against the REAL `ty server` (ty 0.0.55) via the real spawn seam — proves the
# hand-rolled wire actually handshakes with a live Language Server (unit tests fake the process):
definition : Location(path='pkg/mod.py', line=1, column=5)
references : [Location(path='pkg/mod.py', line=1, column=5), Location(path='pkg/mod.py', line=6, column=12)]
hover      : def greet(name: str) -> str
diagnostics: [Diagnostic(severity=1, line=9, column=10, message='Object of type `Literal["not an int"]` is not assignable to `int`')]
on_edit    : [Diagnostic(severity=1, line=9, column=10, message='...')]   # sync bridge, from a worker-thread portal
shutdown   : ok, cache = {}
```

**Notes**
- `Unavailable` is a single-member enum sentinel kept **distinct from `None`**: `None` = "server answered, found nothing" (no definition / no hover); `UNAVAILABLE` = "no answer at all" (disabled / unspawnable / timeout / malformed) — so task 052's tool can map only `UNAVAILABLE` to `ModelRetry`.
- Added `anyio` as a direct runtime dep (it was transitive via pydantic-ai/httpx) because `service.py` imports it directly for the sync→async enricher bridge.
- The 4 ops are `async def (cwd, path, [line, column])`; `diagnostics_on_edit` is the **sync** enricher entry (patched out in unit tests per ADR-0007 §5, exercised for real in the e2e above); `shutdown_all` is async for the `run_app` exit path (task 054 — it `await`s next to the memory write-back).
- A mid-session stale/late response is naturally tolerated by the match-by-id read loop (it skips non-matching frames), so no client eviction on per-op error is needed — kept minimal per the teaching-codebase principle.
- Not committed — handing off to the Tester first.

### [Tester] 2026-06-27 14:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 125 files formatted; `ruff check` all passed)
- Unit tests: 862 passed / 0 failed (22 of them LSP)
- Integration tests: 9 passed / 0 failed
- `make ci` (lock-check + format + lint + full suite): 871 passed
- Warnings: 0 (`filterwarnings=["error"]`)
- code-review plugin enabled in `.claude/settings.json`; folded a manual diff review into the checklist below (no defects found).

**E2E adversarial pass** (script `scratchpad/adv051.py` — 13 break paths via the faked spawn seam; all PASS)
- Happy path (real `ty` 0.0.55 through the UNPATCHED seam, `scratchpad/realty051.py`): definition→`Location('pkg/mod.py',1,5)`, hover→`def greet(name: str) -> str`, references→declaration+call-site, pull diagnostics→2 type errors at 1-based positions, `shutdown_all` cleared cache. PASS
- Break 1 (framing: multi-byte UTF-8 body — Content-Length is bytes): `café 日本語 🚀` round-trips exactly. PASS
- Break 2 (framing: frame fragmented byte-by-byte across reads): reassembles. PASS
- Break 3 (framing: short frame CL=100 > body + EOF): `LspError("pipe closed mid-message")`, no hang, no escape. PASS
- Break 4 (framing: header missing Content-Length): `LspError("missing Content-Length header")`, no infinite loop. PASS
- Break 5 (match-by-id: 3 interleaved decoys + a notification + a server→client request before the real response): resolves the right call. PASS
- Break 6 (broken-spawn `FileNotFoundError`: ALL four ops): every op → `UNAVAILABLE`, seam invoked exactly once (no retry storm). PASS
- Break 7 (best-effort: query a file absent on disk — `didOpen` read_text would raise): → `UNAVAILABLE`, no `FileNotFoundError` escapes. PASS
- Break 8 (boundary positions: `line=1,col=1`→wire(0,0); `line=0,col=-5` clamps to wire(0,0)): no negative wire coords. PASS
- Break 9 (hover shapes: plain str / list-of-MarkedString / empty): flattened; empty→`None` (distinct from UNAVAILABLE). PASS
- Break 10 (diagnostics: missing `items` key / wrong-typed list result): → `[]`, no crash. PASS
- Break 11 (JSON-RPC `error` object instead of `result`): → `UNAVAILABLE`, no exception. PASS
- Break 12 (concurrency: two `asyncio.gather`'d ops on a fresh root): no exception escapes (note below). PASS
- Break 13 (disabled: ops + `diagnostics_on_edit` with `lsp_enabled=False`): short-circuit, seam never called. PASS
- Real sync→async bridge (`scratchpad/bridge051.py`): `diagnostics_on_edit` invoked via `anyio.to_thread.run_sync` from a real worker thread exercises the actual `anyio.from_thread.run` (the one path the unit suite patches out) → returns the 1-based Diagnostic. PASS

**Test-discipline proof**
- Patched `asyncio.create_subprocess_exec` to raise `AssertionError` (`scratchpad/sabotage_plugin.py`) and re-ran the 22 LSP unit tests: **all 22 still pass** → no unit test spawns a real subprocess. The seam is faked in every spawn path (grep: 0 `create_subprocess`/`subprocess` refs in `tests/`). Outbound-TCP guard active during the run; 0 network.

**Acceptance criteria**
- [x] PASS — package + test mirror exist — `src/decode/services/lsp/{__init__,client,service,types}.py`, `tests/unit/decode/services/lsp/`.
- [x] PASS — handshake order + framing round-trip — `test_definition_handshake_order_and_1based_location` (asserts `initialize→initialized→didOpen→definition`), `test_framing_roundtrips_both_directions`.
- [x] PASS — `definition` 1-based location — `test_definition_handshake_order_and_1based_location` (wire 0-based `(2,0)` → `(3,1)`); real-`ty` smoke confirms.
- [x] PASS — `references` all locations incl. declaration — `test_references_returns_all_locations_with_declaration` + `context.includeDeclaration:true` asserted.
- [x] PASS — `hover` text — `test_hover_returns_text`; adversarial str/list/empty shapes also pass.
- [x] PASS — pull `diagnostic` as `(severity, 1-based line, col, message)` — `test_diagnostics_returns_1based_tuples`; pull request/response only (no `publishDiagnostics`).
- [x] PASS — matched by JSON-RPC id, out-of-order tolerant — `test_out_of_order_response_resolves_by_id` + adversarial 3-decoy/notification/server-request probe.
- [x] PASS — lazy + cached: same root once, different root its own — `test_same_root_spawns_once` (`spawn.call_count==1`), `test_different_roots_spawn_separately` (==2).
- [x] PASS — broken-spawn cached, no retry storm — `test_broken_spawn_is_cached_no_retry_storm`, `test_dead_process_is_cached_unavailable`; adversarial all-4-ops probe confirms one spawn call.
- [x] PASS — best-effort timeout / malformed → UNAVAILABLE, no escape — `test_request_timeout_returns_unavailable`, `test_malformed_frame_returns_unavailable`; adversarial short-frame/error-response confirm.
- [x] PASS — `lsp_enabled==False` → UNAVAILABLE without spawning — `test_disabled_short_circuits_without_spawning` (`spawn.assert_not_called()`).
- [x] PASS — no real `ty`/subprocess/network in any unit test; `make ci` green, 0 warnings — sabotage-probe proof above; `make ci` → 871 passed.
- [x] PASS — UNAVAILABLE distinct from None — `test_definition_null_result_is_none_not_unavailable`.

**Evidence**
```
$ make ci
============================= 871 passed in 8.20s ==============================

$ uv run pytest tests/unit/decode/services/lsp/ -q
22 passed in 0.11s

$ bash run_sabotage.sh   # asyncio.create_subprocess_exec patched to raise
22 passed in 0.19s       # → no real subprocess spawned by any unit test

$ uv run python realty051.py   # real ty 0.0.55 via UNPATCHED seam
definition  : Location(path='pkg/mod.py', line=1, column=5)
hover       : def greet(name: str) -> str
references  : [Location(path='pkg/mod.py', line=1, column=5), Location(path='pkg/mod.py', line=5, column=10)]
diagnostics : [Diagnostic(severity=1, line=5, column=16, message='Argument to function `greet` is incorrect: Expected `str`, found `Literal[123]`'), Diagnostic(severity=1, line=6, column=10, message='Object of type `Literal["not an int"]` is not assignable to `int`')]
cache after shutdown: {}
```

**Other issues found** (non-blocking — PASS-with-note)
- Convention sweep clean: no `print()` in `services/`, no secrets/`os.environ`, no naive datetime, logger used (9 sites), every def/async def annotated incl. `-> None`. Diff scoped to LSP files only (pyproject/uv.lock `anyio` promotion + tasks/051 + new `services/` + tests).
- Benign concurrency note (out of scope, no defect): two `asyncio.gather`'d ops on a not-yet-spawned root *could* race the lazy-cache check and double-spawn before one populates `_CLIENTS` (observed 1 spawn here, but not guaranteed under arbitrary scheduling). Best-effort holds — no exception escapes — and the documented usage is one-request-at-a-time (the `lsp` tool / enricher call sequentially), so this is not in scope for 051. Worth a one-line follow-up if a future caller drives the service concurrently.

**VERDICT: PASS**
