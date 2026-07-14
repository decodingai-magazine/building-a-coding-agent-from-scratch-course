---
name: demo-6-sandbox-feature-pr
description: Demo skill for the meta "decode improves decode" flow — launch decode in a sandboxed clone of the course repo, build a small feature inside the Workspace, let Hand-back push the Session Branch, then open a draft PR against the course repo.
---

The meta demo: **decode improves decode.** Run decode inside an isolated sandbox Workspace that is a
clone of this very course repo, implement one small self-contained feature there, and let the
harness ship the work back as a branch you turn into a draft PR.

Nothing the model does touches your host checkout — the Workspace is an isolated clone, and the
branch only lands on your host through Hand-back (ADR-0012 §8).

## 1. Launch decode against a sandboxed clone of the course repo

Sandbox mode is selected by the **`SANDBOX_MODE` environment variable** — it is an env var, not a
command-line flag; the repo to clone into the Workspace is the **`--repo`** flag. Launch the local
docker rung:

```
SANDBOX_MODE=docker decode --repo git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
```

This clones the course repo into the isolated Workspace (`/workspace` ≡ host `.decode/sandbox`) and
drops you into the REPL, with `bash` and the file tools scoped to that clone. Docker must be
running.

To run the same demo on the remote rung instead, swap the mode — everything else is identical:

```
SANDBOX_MODE=modal decode --repo git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
```

(`SANDBOX_MODE=modal` runs the Workspace on Modal; it needs Modal credentials configured.)

## 2. Build a small feature inside the Workspace

Ask decode for one small, self-contained improvement it can finish and prove in a single session —
for example a tiny helper plus its unit test, a `--version` style flag, or a focused docstring/README
fix. Keep it scoped: this demo is about the round-trip, not a big change.

Drive it the normal way — plan if you like, edit, then run the suite inside the Workspace
(`uv run pytest -q` / `make unit-tests`) until it is green. All of this happens on the sandboxed
clone.

## 3. Exit — Hand-back pushes the Session Branch

Leave the REPL (exit, or `/ship` while idle). On exit, Hand-back secures the final Workspace onto a
deterministic `decode/<session-id>` **Session Branch**, auto-commits any uncommitted model work, and
`git push`es it back to the course repo using your ambient host git credentials — every git command
runs host-side, so no credential ever enters the sandbox. The friendly exit line names the branch it
pushed.

## 4. Open a draft PR against the course repo

Turn that pushed branch into a draft PR with the `gh` CLI, from the branch Hand-back named:

```
gh pr create --draft --repo decodingai-magazine/building-a-coding-agent-from-scratch-course \
  --head decode/<session-id> --title "<feature>" --body "Built inside a decode sandbox Workspace."
```

Report the feature you built, the `decode/<session-id>` branch, and the draft-PR URL.
