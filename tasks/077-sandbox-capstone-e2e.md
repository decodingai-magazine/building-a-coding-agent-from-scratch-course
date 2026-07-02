---
id: 077-sandbox-capstone-e2e
feature: sandboxing
status: pending
---

# Sandbox capstone — offline executor-contract + selection slice, skipif real docker/modal/proxy

Tags: `sandbox`, `test`
Depends on: #076
Blocks: —

This is the living proof for the sandboxing feature (ADR-0011, tasks 071-076), in the style of
`test_milestone1_capstone` / `test_runtime_capstone` / `test_lsp_capstone`: an **always-run offline
slice** (no docker, no modal, no network) plus **`skipif`-guarded real-infra smokes** so `make ci`
stays green without infrastructure. New file `tests/integration/test_sandbox_capstone.py`.

## Scope

- **Always-run offline slice (no infra):**
  - **Executor contract:** drive the `CommandExecutor` seam through `bash` with `LocalExecutor` (and a
    `FakeExecutor` double) — a command round-trips to an `ExecResult` and renders; the `note` field
    surfaces on a simulated timeout; `none`-mode rendering is byte-identical.
  - **Selection swap:** patching `settings.sandbox_mode` makes `bash` select the right executor class
    (docker/modal impls faked so no infra is touched), and the `bash` **description** changes per mode
    (`none` byte-identical; docker persistent-shell paragraph; modal remote-scratch paragraph).
  - **REPL kitaru/sandbox-free:** `none` mode imports no docker/modal sandbox module; `import decode.cli`
    imports no kitaru.
  - **Credential map:** `build_credential_map` resolves templates via a patched `kitaru.get_secret`
    hermetically; empty `DEFAULT_PROXY_RULES` → empty map; no value is logged.
  - **Replay-safety config:** with `sandbox_mode != "none"`, the bypass `_build_runtime_agent` is built
    with the verified `bash` re-execute-on-replay checkpoint config (patched seam).
- **`skipif`-guarded real-infra smokes** (each SKIPS, never fails, when its infra is absent — mirroring
  the LSP `ty`-guarded + runtime local-stack tests):
  - **real docker** (`skipif` daemon unreachable): `DockerExecutor` persistent-shell round-trip (state
    persists across two `run`s; a timeout resets + says so; `aclose` removes the container; observability
    lines emitted).
  - **real modal** (`skipif` no creds): `ModalExecutor` round-trip (fs persists; local tree absent;
    timeout kills the exec not the sandbox; `terminate` on `aclose`).
  - **real docker + proxy** (`skipif` no docker): the full stack — an authenticated outbound call from the
    worker succeeds via injected header though the worker env holds no secret (the credential-boundary
    scan). (May reference/thin-wrap the 075 proxy integration test.)

## Acceptance criteria

- [ ] The always-run offline slice passes with **no** docker/modal/network/key and proves: the executor
  contract, the mode→executor selection swap, the per-mode bash description (`none` byte-identical), the
  REPL importing no kitaru/sandbox-impl, the hermetic credential-map resolution, and the replay-safety
  checkpoint config.
- [ ] The three real-infra smokes SKIP cleanly when their infra is absent and PASS when present (guarded
  exactly like the LSP/runtime capstones); a run with no infra shows them **skipped**, not failed.
- [ ] The capstone is hermetic under `filterwarnings=["error"]` run alone (executors close deterministically;
  no leaked subprocess/async resources) — matching the runtime capstone's disposal discipline.
- [ ] `make ci` green with 0 warnings on an infra-less machine (all real-infra tests skipped);
  `make integration-tests` runs the offline slice; `uv lock --check` passes.
- [ ] The module docstring documents the feature end-to-end (doubles as documentation), naming which
  boundaries are real vs faked (the executor contract is real via `LocalExecutor`; docker/modal are faked
  offline and real-under-skipif).

## Out of scope

- New product code (all in 071-075).
- A deployed-stack proxy test (local/offline + skipif-docker only, matching the runtime capstone's
  local-stack scope).

## Log
