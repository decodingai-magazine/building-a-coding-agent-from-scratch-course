---
id: 057-runtime-settings-dep-glossary
feature: kitaru-runtime
status: pending
---

# Kitaru dependency, runtime settings, .env.example mirror, and glossary rows

Tags: `data`, `infra`, `runtime`, `docs`
Depends on: None
Blocks: #058, #059, #060, #061, #062

This task implements ADR-0008 (Kitaru durable runtime). It lands the configuration surface, the
`kitaru` dependency, and confirms the ubiquitous-language rows the rest of the feature reads — ahead
of any reader, exactly like task 050 (LSP) and 041 (compaction) added settings before their
consumers. It stays independently shippable: no `runtime/` code yet, the live TUI is untouched.
ADR-0008 was finalized to **Accepted** and the six glossary rows were applied to `docs/glossary.md`
**at the plan gate** (the design + vocabulary of record for 058-062); this task verifies the rows are
present and used, and adds the dependency + settings.

## Scope

- **Dependency:** `uv add 'kitaru[local,pydantic-ai,llm,mcp]'` (PyPI `0.18.0`, pre-1.0 — pinned by
  `uv.lock`; `requires-python >=3.11`, compatible with the project's 3.12+). Extras: `local` (core
  + local stack), `pydantic-ai` (the `KitaruAgent` adapter decode needs), `llm`, `mcp`. This is a
  **runtime** dependency (kitaru is imported by `runtime/`, unlike `ty` which is a dev-group
  subprocess) — but it is imported **lazily** by the `decode run` entry only (task 058), so the
  REPL path never imports it. Confirm `uv lock --check` stays green and `make install` resolves it.
- **Settings** (add to `Settings` in `config/settings.py`, defaults safe for tests, all
  type-annotated; `Literal`/`Field` as the file requires), under a new
  `# --- Kitaru durable runtime (ADR-0008) ---` block. No readers yet (readers land in 058/059/061):
  - `runtime_enabled: bool = True` — master gate for the whole headless feature. `False` → `decode
    run` exits with a friendly line and never builds a flow (task 058 reads it).
  - `runtime_checkpoint_strategy: Literal["turn", "calls"] = "turn"` — `KitaruAgent` checkpoint
    granularity; `"turn"` (one checkpoint per agent turn, coarse/simplest) is the MVP default,
    `"calls"` is per model/tool call (task 058 reads it).
  - `runtime_wait_timeout_s: float = Field(600.0, gt=0)` — the durable HITL wait poll timeout in
    flow mode (matches Kitaru's local 600s default; task 059 reads it).
  - `runtime_credentials_proxy_enabled: bool = False` — when `True`, flow-mode model construction
    resolves the provider API key through Kitaru secrets instead of reading `SecretStr` from
    settings. Default **`False`** because the secrets-proxy surface is the least-exampled in Kitaru
    (ADR-0008 §5) and must be verified first (task 061 reads it).
  - `runtime_secret_name: str = "decode-llm-creds"` — the Kitaru secret name the credentials proxy
    reads the provider key from when enabled (task 061 reads it).
- **`.env.example`:** add a `# --- Kitaru durable runtime ---` block mirroring all five vars
  (commented, explain-the-default voice matching the LSP/compaction blocks), noting the runtime is a
  second, non-interactive entry path that leaves the TUI untouched, that `local` is the starting
  stack, and that the credentials proxy is opt-in/verify-first.
- **Glossary** (`docs/glossary.md`): the six rows — **Headless Runtime**, **Durable Flow**,
  **Checkpoint**, **Replay**, **Wait (HITL)**, **Credentials Proxy** — were applied at the plan gate.
  This task **verifies** they are present in the table format, cross-reference the existing
  **Decision Channel** / **Sandbox** / **Services Interface** rows correctly, and are used verbatim
  in the code/docs the later tasks ship (no non-canonical synonym is introduced).

## Acceptance criteria

- [ ] `kitaru` appears in `pyproject.toml` `[project.dependencies]` with the `local,pydantic-ai,llm,mcp`
      extras and is pinned in `uv.lock`; `uv lock --check` passes and `make install` installs it.
- [ ] `import kitaru` succeeds in the project venv; `from kitaru import flow` works (smoke-imported in
      a test or asserted via `uv run python -c`).
- [ ] `Settings` exposes `runtime_enabled` (True), `runtime_checkpoint_strategy` ("turn"),
      `runtime_wait_timeout_s` (600.0), `runtime_credentials_proxy_enabled` (False),
      `runtime_secret_name` ("decode-llm-creds") with the exact names/types/defaults above; a unit
      test asserts every default.
- [ ] `runtime_checkpoint_strategy` rejects a value outside `{"turn","calls"}` (Literal validation);
      `runtime_wait_timeout_s` is constrained `> 0` (a `0`/negative env value fails validation); both
      unit-tested.
- [ ] Each new var is env-overridable (e.g. `RUNTIME_ENABLED=false`,
      `RUNTIME_CHECKPOINT_STRATEGY=calls`); a unit test sets them via env/`monkeypatch` and asserts the
      parsed values.
- [ ] `.env.example` lists all five vars under a Kitaru runtime block; the existing env/settings drift
      test confirms every new setting has a matching `.env.example` line.
- [ ] `docs/glossary.md` carries the six rows (Headless Runtime, Durable Flow, Checkpoint, Replay,
      Wait (HITL), Credentials Proxy) in the existing table format; a grep confirms no non-canonical
      synonym ("kitaru wrapper", "durable job", "pause tool") is introduced.
- [ ] `make ci` is green with 0 warnings (`filterwarnings=["error"]`); `uv lock --check` passes.

## User stories

### Story: A developer installs the runtime extra fresh
1. Developer runs `make install`.
2. `uv sync` resolves the pinned `kitaru[local,pydantic-ai,llm,mcp]` from `uv.lock`.
3. `uv run python -c "import kitaru; from kitaru import flow; print(kitaru.__version__)"` prints
   `0.18.0` — the SDK is importable for tasks 058-062 to build a flow.

### Story: A developer turns the headless runtime off
1. Developer sets `RUNTIME_ENABLED=false` in `.env`.
2. `settings.runtime_enabled` reads `False`; task 058's `decode run` will exit with a friendly line
   (its own AC covers the behavior) — the REPL is unaffected.

### Story: A developer tunes checkpoint granularity
1. Developer sets `RUNTIME_CHECKPOINT_STRATEGY=calls` in `.env`.
2. `settings.runtime_checkpoint_strategy` reads `"calls"`; task 058 will pass it to `KitaruAgent`.
3. A bogus `RUNTIME_CHECKPOINT_STRATEGY=hourly` fails validation at load with a clear error, not a
   late crash inside the flow.

## Out of scope
- Any reader of these settings (the flow/CLI land in 058; HITL in 059; sleep in 060; credentials in 061).
- The `runtime/` package and the `decode run` subcommand (task 058).
- The ADR-0008 finalization-to-Accepted edit and the glossary-row authoring (both done at the plan gate, not in this task — this task only verifies the rows).
- A deployable entrypoint / cron (deferred to step 12 per ADR-0008 §6).

## Log
