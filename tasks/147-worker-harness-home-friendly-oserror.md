---
status: pending
feature: modal-remote-headless
---

# Modal Kitaru Worker: ensure_harness_home OSError → one friendly Decode: line, not a raw traceback

Tags: `infra`, `enhancement`
Depends on: None
Blocks: —

Follow-up filed at the modal-remote-headless PA acceptance review (PR #65). Flagged by the
Tester in tasks/done/145 and again in tasks/done/146; deliberately left unfixed there for scope
discipline. Low real-world risk (the image `mkdir -p`s `HARNESS_HOME` at build time, so the
runtime call is normally a no-op re-creation), but the failure mode is a raw Python traceback in
the Function log — inconsistent with the script's own convention two lines below it
(`credential_error`: one friendly line, named cause, non-zero exit, worker never started).

## Scope

- `scripts/modal_kitaru_worker.py::ensure_harness_home` (line ~225): a failing
  `Path(path).mkdir(parents=True, exist_ok=True)` (permission denied, read-only filesystem,
  disk full) must surface as exactly ONE `Decode:`-prefixed line naming the path and the OS
  error, and `run_worker` must exit non-zero without starting the worker subprocess — mirroring
  the existing `credential_error` pre-flight shape (SWE decides: helper returns an error string
  like `credential_error`, or a `try/except OSError` in `run_worker`; keep it consistent with
  the file's existing pattern).
- No behavior change on the happy path: the directory is still created (or confirmed) before
  the scrub/credential checks run, and nothing else in the pre-flight order moves.
- Unit tests in `tests/unit/scripts/test_modal_kitaru_worker.py`: the failure path (mocked
  `mkdir` raising `OSError`) asserts the one-line message, the non-zero exit, and that
  `subprocess` is never invoked; the happy path stays green untouched.

## Acceptance Criteria

- [ ] A failing `mkdir` produces exactly one `Decode:`-prefixed line naming the Harness Home path and the underlying OS error — no traceback in the Function log.
- [ ] `run_worker` exits non-zero on that path and never starts the `kitaru worker start` subprocess — unit-tested.
- [ ] The happy path is unchanged: existing 39 worker tests stay green untouched.
- [ ] Full unit suite green; `make pre-commit` green.

## User Stories

### Story: Operator reads a spawn-environment failure without decoding a traceback
1. Operator starts the worker (`uv run modal run --detach scripts/modal_kitaru_worker.py`) against an image where `/harness` cannot be created (simulated in tests)
2. The Function log shows one `Decode:` line naming `/harness` and the OS error — same voice as the missing-credential line
3. Exit is non-zero; `kitaru worker list` shows no half-started worker; the operator knows exactly what to fix

---

Refs: tasks/done/145-modal-kitaru-worker-app.md (Tester "Other issues found"), tasks/done/146-docs-remote-story-on-modal.md, ADR-0020 §5

## Log

### [PA] 2026-08-22 23:10 — Grooming

**Summary**
Harden the Modal Kitaru Worker's Harness Home pre-flight: an `OSError` from `ensure_harness_home`
must degrade to the script's own one-friendly-line convention instead of a raw traceback.

**Key decisions**
- Mirror the existing `credential_error` pre-flight shape rather than inventing a new error surface.
- Happy path and pre-flight ordering untouched; this is a message/exit-code fix only.

**Dependencies**
- None (the worker app shipped in task 145; this edits one function + tests).

**User stories**
- 1 story: the operator-facing failure message.

Ready for implementation.
