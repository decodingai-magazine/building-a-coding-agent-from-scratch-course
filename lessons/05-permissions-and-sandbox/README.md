# Lesson 5 — Containing the agent: permissions → sandbox

The trust ladder: the permission gate proper, then isolated Docker/Modal
Workspaces with git hand-back — plus the credential proxy we built and then
deleted, postmortem included.

## Run it

```bash
./lessons/05-permissions-and-sandbox/run.sh   # needs the Docker daemon running
```

A headless run whose *entire* tool scope (file tools **and** bash) lives inside
an isolated Docker Workspace cloned from this repo — then the hand-back: the
agent's work comes home as a `decode/<session-id>` git branch, pushed host-side
with your ambient credentials. No credential ever enters the sandbox.

## Playbook (interactive)

1. **The gate, rung by rung.** In the REPL, ask for a read (auto-allowed), a
   write (`y/n` prompt), a bash command (`y/n` prompt). Deny one and watch the
   model adapt.
2. **Rules.** Add a rule to `.decode/settings.json` (e.g. allow `bash(git *)`),
   restart, and watch that class of call skip the prompt — the safety layer is
   deterministic, not prompt-based.
3. **The remote rung.** `SANDBOX_MODE=modal uv run decode --repo <url>` —
   nothing executes on your machine at all (needs `modal token set`).
4. **`/ship`.** In a sandboxed REPL session, `/ship` triggers the hand-back
   without quitting.
5. **The model pushes itself (opt-in).** Set `SANDBOX_GIT_TOKEN` to a
   fine-grained, repo-scoped PAT and the model can `git push` / `gh pr create`
   from inside the Workspace. A sandboxed process *can read that token* — which
   is exactly why the default is unset, and why hand-back never needed it.

## Deep dives

- `src/decode/permissions/` · `src/decode/sandbox/`
- [ADR-0003 — permission system + agents catalog](../../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md)
- [ADR-0012 — isolated Workspace](../../docs/adr/0012-isolated-workspace.md)
- [ADR-0016 — dropping the credential proxy](../../docs/adr/0016-drop-credential-proxy.md) — the postmortem
- [getting_started/sandboxing.md](../../getting_started/sandboxing.md) · [credentials.md](../../getting_started/credentials.md)

## Background reading

| Article | Why read it here |
|---|---|
| [Build, Configure, or Use As-Is: The Agentic Harness](https://www.decodingai.com/p/agentic-harness-system-design) | The permissions + sandbox sections of the harness teardown: deterministic allow/ask/deny rules, agent modes, sandbox jails derived from the same rules — "the permission layer has almost no AI in it yet is what makes the system safe to run." |

No newsletter deep-dive covers this lesson yet — the primary sources are the
ADRs above, especially the [credential-proxy postmortem](../../docs/adr/0016-drop-credential-proxy.md).
