---
id: 050-lsp-settings-and-dep
feature: lsp-integration
status: pending
---

# LSP settings, .env.example mirror, and the `ty` dev dependency

Tags: `data`, `infra`, `lsp`
Depends on: None
Blocks: #051, #052, #053, #054

This task implements ADR-0007 (LSP integration) — it lands the configuration surface and the
language-server binary the rest of the feature reads. No readers yet (exactly like task 041 added
the compaction settings ahead of their readers); this stays independently shippable.

## Scope

Add the LSP configuration surface and pin the language-server binary. Follow the recent
compaction-block convention in `config/settings.py` (the block at settings.py:67-88) and mirror
every new variable in `.env.example` (the `# --- Context compaction ---` block at .env.example:71-87
is the pattern).

- **Dependency:** add `ty` (Astral's Rust type-checker / language server) to the **dev** group
  via `uv add --dev ty` (PEP 735 `[dependency-groups] dev` — AGENTS.md: dev tools via the dev
  group, never `[project.optional-dependencies]`). `ty` is invoked as a **subprocess** (`ty server`),
  never imported, so the dev group is the right home; `uv.lock` pins it (ty is pre-1.0 / preview —
  record that honestly per ADR-0007). Confirm `uv.lock` updates and `uv lock --check` stays green.
- **Settings** (add to `Settings`, defaults safe for tests, all timezone/type-annotated as the file
  requires), under a new `# --- LSP / code intelligence (ADR-0007) ---` block:
  - `lsp_enabled: bool = True` — master gate for the WHOLE feature (the `lsp` tool AND the enricher AND
    any server spawn). When `False`, no server is ever launched.
  - `lsp_server_command: str = "ty"` — the swappable Language Server executable.
  - `lsp_server_args: list[str] = ["server"]` — the args appended to the command (so the spawn is
    `[lsp_server_command, *lsp_server_args]`). Default launches `ty server` (stdio LSP). Documented
    drop-in fallback: `pylsp` / any stdio LSP server.
  - `lsp_diagnostics_on_edit: bool = True` — gates ONLY the passive Diagnostics Enricher (task 053).
  - `lsp_request_timeout_s: float = 10.0` — per-request best-effort wall-clock timeout (the server's
    initialize can be slow; the lazy/cached single spawn amortizes it). Use a `Field(..., gt=0)`
    bound consistent with the other float settings.
- **`.env.example`:** add a `# --- LSP / code intelligence ---` block mirroring all five vars
  (commented, with the same explain-the-default voice as the compaction block), noting the server is
  swappable and that the feature is best-effort (absent/old `ty` degrades silently).

## Acceptance criteria

- [ ] `ty` appears in `pyproject.toml`'s `[dependency-groups] dev` and in `uv.lock`; `uv lock --check`
      passes and `make install` (`uv sync`) installs it.
- [ ] `Settings` exposes `lsp_enabled` (True), `lsp_server_command` ("ty"), `lsp_server_args`
      (`["server"]`), `lsp_diagnostics_on_edit` (True), `lsp_request_timeout_s` (10.0) with the exact
      names/types/defaults above; a unit test asserts every default.
- [ ] `lsp_request_timeout_s` is constrained `> 0` (a `0`/negative env value fails validation); unit-tested.
- [ ] Each new var is overridable from the environment (e.g. `LSP_SERVER_COMMAND=pylsp`,
      `LSP_ENABLED=false`); a unit test sets them via `monkeypatch`/env and asserts the parsed values
      (including `lsp_server_args` parsing a list from the env per pydantic-settings).
- [ ] `.env.example` lists all five vars under an LSP block; a test (or the existing env/settings
      drift test, if present) confirms every new setting has a matching `.env.example` line.
- [ ] `make ci` is green with 0 warnings (`filterwarnings=["error"]`).

## User stories

### Story: A developer points decode at a different language server
1. Developer opens `.env`, uncomments `LSP_SERVER_COMMAND=pylsp` and `LSP_SERVER_ARGS=["-v"]` (or the
   pylsp invocation).
2. Developer runs `uv run python -c "from decode.config.settings import settings; print(settings.lsp_server_command, settings.lsp_server_args)"`.
3. The output is `pylsp ['-v']` — the swap is config-only, no code change.

### Story: A developer turns the whole feature off
1. Developer sets `LSP_ENABLED=false` in `.env`.
2. `settings.lsp_enabled` reads `False`; downstream tasks (052/053/054) treat this as "never spawn a
   server, no `lsp` tool effect, no enricher" (their own ACs cover the behavior).

### Story: A developer installs the project fresh
1. Developer runs `make install`.
2. `uv sync` resolves the pinned `ty` from `uv.lock` into the dev environment; `uv run ty --version`
   prints a version (proving the binary is on PATH for later tasks to spawn).

## Out of scope
- Any reader of these settings (the client/tool/enricher land in 051/052/053).
- Promoting `ty` to a runtime dependency (it stays dev-group; graceful degradation when absent is the
  feature's contract — ADR-0007).

## Log
### [PA] 2026-06-27 — Grooming

**Summary**
Lands the LSP config surface (5 settings) + the `ty` dev dependency ahead of any reader, mirroring
the compaction-settings-first pattern (task 041).

**Key decisions**
- `ty` goes in the dev group (invoked as a subprocess, never imported); pre-1.0, pinned by `uv.lock`.
- Master gate `lsp_enabled` gates the whole feature; `lsp_diagnostics_on_edit` gates only the enricher.
- Server is swappable via `lsp_server_command` + `lsp_server_args` (pylsp drop-in documented).

**Dependencies**
- None.

**User stories**
- 3 stories: swap the server, disable the feature, fresh install resolves `ty`.

Ready for implementation.
