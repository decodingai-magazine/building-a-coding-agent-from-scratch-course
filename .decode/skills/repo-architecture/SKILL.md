---
name: repo-architecture
description: Clone a GitHub repo and explore it in focused passes to write an ARCHITECTURE.md — the problem it solves, how to use it, its key components/interfaces/connections, and an end-to-end dataflow — all backed by Mermaid diagrams.
---
You turn a GitHub URL into an `ARCHITECTURE.md` a newcomer can read to understand a codebase: what
problem it solves, how to use it, and how it is built. You drive a series of focused **exploration
passes** over the cloned source, then synthesize them into one document. Every claim is grounded in a
file you actually read, and the structure is carried by Mermaid diagrams.

Before you write the file, `read` the bundled `references/architecture-template.md` — it holds the exact
section skeleton and the Mermaid diagram recipes. Follow it.

## Input

The GitHub URL to analyze. If the user's message doesn't contain one, call `ask_user` for it. Accept
`https://github.com/<owner>/<repo>` (with or without `.git`) or `git@github.com:…` forms.

## 1. Clone

Shallow-clone into a scratch working dir so you never touch the user's tree, via `bash`:

```
git clone --depth 1 <url> ./<repo-name>
```

If it fails (private, auth, or 404), report the exact error and stop — don't guess at the contents.
Work inside the clone for the rest of the run.

## 2. Plan the exploration passes

Seed a `todo_write` checklist with one item per pass below; mark each in-progress when you start it and
done when its notes are captured. Every pass is **read-only**: `glob`/`grep` to locate, `read` to
confirm — never describe code you haven't opened.

- **Orientation** — README, docs, the package manifest (`pyproject.toml` / `package.json` / `go.mod` /
  `Cargo.toml` / …), LICENSE, `examples/`. Capture: the problem it solves, who it's for, how it's
  installed and run, and the smallest real usage example you can find.
- **Component inventory** — top-level packages/modules/services and the entrypoints. Capture 5–12 key
  components, each a one-line responsibility + its path.
- **Interfaces** — for each key component, its public surface: exported functions/classes, HTTP/CLI
  routes, events, schemas. Capture how a caller talks to it.
- **Connections** — who imports/calls/depends on whom, plus the data stores and external services.
  Capture the wiring between components.
- **Dataflow** — pick the single most representative end-to-end path (one request, CLI invocation, or
  job) and trace it across components from entry to result, citing each hop.

## 3. Synthesize and write

Write `ARCHITECTURE.md` at the clone root, following `references/architecture-template.md`:

- **What problem it solves** + **How to use it** — from Orientation.
- **Architecture** — the key-components table, then their interfaces and connections, with a Mermaid
  component/graph diagram.
- **End-to-end dataflow** — a prose walkthrough plus a Mermaid sequence diagram of the path from the
  Dataflow pass.

Every box and arrow must reflect a component you actually found and a connection you confirmed — no
invented nodes. Where you couldn't verify something, label it `(unverified)` rather than drawing it as
fact. Finish by reporting the path to the written file and a two-line summary of what the repo does.

> ponytail note: decode has no subagent-spawn tool yet, so the passes run sequentially in this one
> agent (tracked via `todo_write`). When a dispatch tool lands, each pass becomes a parallel explore
> agent — the pass list is already the fan-out.
