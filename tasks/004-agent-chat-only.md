---
id: 004-agent-chat-only
feature: m1-vanilla-agent
status: pending
---

# Agent loop: chat-only (Pydantic AI + Gemini)

## Scope
The Pydantic AI agent on Gemini with streaming, no tools — the first real round-trip. Replaces the stub turn handler. See [ADR-0002 §1–2](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md).

## Acceptance criteria
- [ ] `agent/factory.py` builds `Agent(output_type=[str, DeferredToolRequests], deps_type=AgentDeps)` on Gemini via the `google-gla:` API-key path; model id from `settings.gemini_model`.
- [ ] `agent/loop.py` `run_turn()` drives `agent.iter()`, streams `TextPartDelta` → `AssistantTextDelta` events, drains steering before each model-request leg; `message_history` carries across turns.
- [ ] Unit tests use `pydantic_ai.models.test.TestModel` (no network); `uv run decode` holds a real Gemini chat.
- [ ] Confirmed against the installed SDK: `GoogleProvider` API-key kwarg, model id, and that a steering user-message can be appended at the deferred resume.

## Out of scope
- Tools, the permission gate (task 005+).

## Dependencies
- Adds `pydantic-ai` (pulls `google-genai`).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan + ADR-0002. Resolve the flagged Pydantic AI unknowns here.
