---
name: commit
description: Stage the appropriate changes and commit them with a Conventional Commits message.
---
You commit the work in the **current working tree** autonomously: you stage the right files, write
the message, and run `git commit`. You commit exactly what you stage.

1. Inspect the tree with `git status` and `git diff` (and `git diff --cached` for anything already
   staged). If there is nothing to commit, say so and stop.
2. Stage the changes that belong in this commit with `git add`. Do not stage unrelated edits, build
   artifacts, or secrets; if the tree mixes unrelated changes, stage the coherent subset and say what
   you left out.
3. Compose a **Conventional Commits** message:
   - Subject `type(scope): summary` — `type` ∈ feat, fix, refactor, docs, test, chore, build, ci,
     perf; imperative summary ≤ 72 chars.
   - A blank line, then a body explaining **why** the change was made and any notable trade-offs.
4. Run `git commit` with that message, committing exactly what you staged. Describe only changes you
   actually saw in the diff; never invent them. Report the resulting commit subject.
