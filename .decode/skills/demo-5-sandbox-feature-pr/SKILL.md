---
name: demo-5-sandbox-feature-pr
description: Demo skill for the meta "decode improves decode" flow — launch decode in a sandboxed clone of the course repo, plan and build one small feature inside the Workspace, let Hand-back push the Session Branch, then open a draft PR against the course repo.
---

The meta demo: **decode improves decode.** Run decode inside an isolated sandbox Workspace that is
a clone of this very course repo, plan and implement one small self-contained feature there, and
let the harness ship the work back as a branch you turn into a draft PR.

Nothing the model does touches your host checkout — the Workspace is an isolated clone, and the
branch only lands on your host through Hand-back (ADR-0012 §8).

## 1. Launch decode against a sandboxed clone of the course repo (human step)

Sandbox mode is selected by the **`SANDBOX_MODE` environment variable** — it is an env var, not a
command-line flag; the repo to clone into the Workspace is the **`--repo`** flag. Launch the local
docker rung (Docker must be running):

```
SANDBOX_MODE=docker decode --repo git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
```

This clones the course repo into the isolated Workspace (`/workspace` ≡ host `.decode/sandbox`)
and drops you into the REPL, with `bash` and the file tools scoped to that clone.

To run the same demo on the remote rung instead, swap `SANDBOX_MODE=docker` for
`SANDBOX_MODE=modal` — everything else is identical (Modal credentials must be configured).

## 2. Plan the feature — in plan mode

If you are decode reading this inside the Workspace, start here — step 1 is the human's launch
command, already done; do NOT try to run `decode` yourself. Enter **plan mode** by calling
`enter_plan_mode` with **no arguments** (it takes none — you present the plan later, to
`exit_plan_mode`), explore the code read-only, then present a short plan before touching anything.
Pick ONE feature — small, self-contained, provable in a single session:

- A `decode --version` flag that prints the installed package version, plus its unit test.
- One small pure helper (e.g. in `tools/` or `entities/`) with a focused unit test.
- A scoped docstring/README fix that resolves a real inaccuracy you found while exploring.

The plan should name the files to touch, the test that proves the feature, and nothing else.
Exit plan mode (`exit_plan_mode`) once the human approves it.

## 3. Build it inside the Workspace

Implement the plan: edit, then run the suite inside the Workspace (`uv run pytest -q` /
`make unit-tests`) until it is green. Keep the final green test output — it goes in the PR body.
All of this happens on the sandboxed clone.

## 4. Exit — Hand-back pushes the Session Branch (human step)

Leave the REPL (exit, or `/ship` while idle). On exit, Hand-back secures the final Workspace onto
a deterministic `decode/<session-id>` **Session Branch**, auto-commits any uncommitted model work,
and `git push`es it back to the course repo using your ambient host git credentials — every git
command runs host-side, so no credential ever enters the sandbox. The friendly exit line names the
branch it pushed.

## 5. Open a draft PR against the course repo

Turn the pushed branch into a draft PR with the `gh` CLI, from the branch Hand-back named:

```
gh pr create --draft --repo decodingai-magazine/building-a-coding-agent-from-scratch-course \
  --head decode/<session-id> --title "<feature>" \
  --body "<what & why, 2-3 lines>

Test evidence:
\`\`\`
<the green pytest summary line>
\`\`\`

Built end-to-end inside a decode sandbox Workspace (SANDBOX_MODE=docker) and shipped by Hand-back."
```

Report the feature you built, the `decode/<session-id>` branch, and the draft-PR URL.
