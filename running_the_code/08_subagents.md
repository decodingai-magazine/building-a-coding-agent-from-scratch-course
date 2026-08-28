# Agents & Subagents

decode ships an **Agents Catalog** ([ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md))
— one persona per bundled Markdown file in [`src/decode/agents/builtin/`](../src/decode/agents/builtin/). A persona
scopes the run: system prompt + tool allowlist + default [Permission Mode](../docs/glossary.md) + its own
allow/deny rules. Every agent rides the same configured model.

| Agent | Tools | Default mode | Use it for |
|---|---|---|---|
| `build` | read/glob/grep/lsp + write/edit/bash/… | `default` | the full coding agent — reads, edits, runs commands (the startup default) |
| `plan` | read/glob/grep/lsp/web_fetch/todo_write | `plan` | research + a plan, zero changes on disk |
| `code-reviewer` | read/glob/grep/lsp/web_fetch/todo_write | `default` | reviewing a diff for correctness, simplicity, tests, standards |
| `explore` | read/glob/grep/lsp | `default` | **subagent only** — read-only codebase questions ([ADR-0013](../docs/adr/0013-explore-subagents.md)) |

## 1. Pick one at startup — `--agent`

```bash
decode                             # build (the default)
decode --agent plan
decode --agent code-reviewer
decode --agent plan --mode edit    # override the agent's own default mode
```

Omit `--mode` and you get the agent's default mode from its frontmatter. An unknown name refuses to
start with one friendly line listing the **primary** names.

## 2. Switch mid-session — `/agent`

```
/agent plan
/agent build
```

Same guard as the flag; bare `/agent` prints the usage line. The switch is live — same session, same
conversation, new persona and mode.

## 3. Fan out Explore subagents — the `agent` tool

`explore` is `subagent: true`: **not** selectable by `--agent` or `/agent`. The main agent spawns it
through the model-callable `agent` tool ([`src/decode/tools/agent.py`](../src/decode/tools/agent.py)), one
read-only child per prompt, in parallel — just ask for it in the REPL:

```
Explore the sandbox seam and the permission gate in parallel, then compare how each dispatches.
```

Children run read-only (read/glob/grep/lsp), never prompt for approval, and hand back reports the
parent folds into one answer. Concurrency ceiling: `SUBAGENT_MAX_PARALLEL` (default `4`); the report
budget `SUBAGENT_RESULT_MAX_BYTES` is split across children, so a wide fan-out costs the parent the
same as a narrow one.

> **Headless has no persona switch.** `decode run` takes `--hitl` / `--model` / `--repo` / `--local`
> — no `--agent`. It always runs `build`.

## Adding your own agent

Drop a new `.md` in [`src/decode/agents/builtin/`](../src/decode/agents/builtin/): YAML frontmatter
(`name`, `description`, `tools`, `mode`, optional `allow` / `deny` / `subagent`) then the system-prompt
body. Validation is loud — an unknown tool name or a malformed rule raises at load naming the file.
