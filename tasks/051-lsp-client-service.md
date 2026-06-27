---
id: 051-lsp-client-service
feature: lsp-integration
status: pending
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

- [ ] `src/decode/services/lsp/` exists with `__init__.py`; `tests/unit/decode/services/lsp/` mirrors it.
- [ ] A unit test drives the client against a **fake process** (patched spawn seam) feeding canned
      `Content-Length`-framed JSON-RPC responses, and asserts:
  - [ ] `initialize`→`initialized` handshake is sent in order and the framing round-trips (a written
        frame parses back; a read frame is decoded by exact `Content-Length`).
  - [ ] `definition` returns the canned location as `(path, 1-based line, 1-based column)`.
  - [ ] `references` returns all canned locations (declaration included).
  - [ ] `hover` returns the canned hover text.
  - [ ] a pull `diagnostic` request returns the canned diagnostics as
        `(severity, 1-based line, 1-based column, message)` tuples.
  - [ ] responses are matched to requests by JSON-RPC `id` (an out-of-order/interleaved canned
        response still resolves the right call).
- [ ] **Lazy + cached:** two calls for the same root spawn the fake process **once** (seam invoked
      once); a different root spawns its own.
- [ ] **Broken-spawn cached:** a spawn that fails (seam raises / process exits immediately) is cached
      as unavailable — a second call does NOT re-invoke the spawn seam (no retry storm); the op
      returns "unavailable", not an exception.
- [ ] **Best-effort:** a request that times out (`lsp_request_timeout_s`, patched small) or a malformed
      frame yields "unavailable"; **no exception escapes** the service. Asserted by a unit test.
- [ ] `lsp_enabled == False` returns "unavailable" without invoking the spawn seam (unit-tested).
- [ ] No real `ty`/subprocess/network is started by any unit test; `make ci` green, 0 warnings.

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
