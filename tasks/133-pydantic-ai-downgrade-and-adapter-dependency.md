---
id: 133
feature: kitaru-replay-runtime
status: pending
---

# Dependency swap: pin pydantic-ai-slim >=2.22,<2.23 and add kitaru-pydantic-ai

Tags: `infra`, `refactor`
Depends on: 131, 132 (all dead-API surfaces gone first, so fallout here is ONLY the
pydantic-ai 2.23+ → 2.22 API delta)
Blocks: 134

This task implements ADR-0019 (§ Dependency). The `kitaru-pydantic-ai` adapter caps
`pydantic-ai <2.23`; decode currently pins `pydantic-ai-slim[google,openai]>=2.33.0`.
Human-approved: DOWNGRADE.

## Scope

- `pyproject.toml`: change `pydantic-ai-slim[google,openai]>=2.33.0` →
  `pydantic-ai-slim[google,openai]>=2.22,<2.23`; add `kitaru-pydantic-ai>=0.1.0` to runtime
  deps. `kitaru[cli,mcp,worker]>=0.22.2` stays. Re-examine the stale cap comments
  (`pydantic <2.13` / `click <8.3` cite "kitaru→zenml, ADR-0009" — zenml is out of the tree;
  lift a cap ONLY if `uv lock` proves it resolves AND the suite is green, otherwise keep the
  cap and fix the comment to cite ADR-0019).
- `uv lock` / `uv sync`; commit `uv.lock`.
- Fix every pydantic-ai 2.23+ API usage in `src/` and `tests/` that 2.22 breaks (agent loop,
  factory, deferred-tool surfaces, capstones — discovered by running the suite; this fallout
  is explicitly in-scope per the grilled decisions).
- Rewrite `tests/unit/decode/test_kitaru_dependency.py`: assert the NEW contract —
  `kitaru` importable, the old durability surface (`flow`/`checkpoint`/`wait`) absent,
  `from kitaru_pydantic_ai import KitaruAgent` importable, and its constructor accepts
  `(agent, agent_id=None, agent_version_id=None, session_name=None, batch_size=20)`
  (signature-inspection, no server).

## Acceptance Criteria

- [ ] `uv lock` resolves with `pydantic-ai-slim` in `[2.22, 2.23)` and `kitaru-pydantic-ai>=0.1.0` alongside `kitaru 0.22.2` (if it cannot, STOP and escalate — do not improvise pins).
- [ ] `python -c "from kitaru_pydantic_ai import KitaruAgent"` succeeds in the synced env.
- [ ] Rewritten dependency smoke test green: old surface absent, new surface present.
- [ ] `make ci` fully green (format, lint, unit, integration) — the feature-level "(a)" gate.
- [ ] No `pydantic_ai` version-conditional shims left behind — code targets 2.22 cleanly.

## Out of scope

- Wrapping anything in `KitaruAgent` (134/135) — this task only makes it importable.
- Chasing newer pydantic/click majors beyond the lock-proof described above.

## Log
