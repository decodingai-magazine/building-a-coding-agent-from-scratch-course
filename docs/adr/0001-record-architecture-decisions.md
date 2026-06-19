# 0001. Record architecture decisions

**Status:** Accepted
**Date:** 2026-06-19

## Context

We expect to make architectural decisions over the lifetime of this project — choices that the code itself doesn't fully explain (which inference backend, the local-vs-remote sandbox seam, the context-compaction strategy, choosing Kitaru for durability, which async model). Six months from now, we will not remember why we chose what we chose. Without a record, we will re-litigate the same decisions and probably reach different conclusions. As a teaching codebase, the *why* behind each choice is itself part of the material.

## Decision

We will use Architecture Decision Records, as described by Michael Nygard, stored in `docs/adr/` as `NNNN-kebab-title.md`. Each ADR has four sections: Status, Context, Decision, Consequences. Numbering is monotonic and never reused; superseded ADRs keep their number and link to their replacement.

## Consequences

- Every non-obvious architectural choice gets a one-page record.
- New contributors (and course readers) can read `docs/adr/` to understand why the codebase is shaped the way it is.
- Prior ADRs are read during planning to avoid re-proposing settled questions.
- Cost: ~30 minutes per ADR. We accept this cost because the alternative — re-deriving past reasoning — is more expensive.
