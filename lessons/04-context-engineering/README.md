# Lesson 4 — Context engineering: the window is a budget

Context engineering without measurement is folklore. Five moves — memory,
compaction, skills, LSP, truncation — each a measured before/after experiment.

## Run it

```bash
./lessons/04-context-engineering/run.sh
```

Two headless slices: memory injection (the agent already knows the project's
conventions without reading a file) and LSP as a context move (definitions and
references instead of dumping whole files into the window).

## Playbook (interactive)

```bash
uv run decode
```

1. **The gauge.** Watch the footer context gauge as you converse — the window
   is a budget and the harness meters it.
2. **Compaction.** Paste something huge (or just talk long enough), then
   `/compact` — one LLM call summarizes the conversation; the gauge drops, the
   thread continues. Automatic tiers: cheap trim near ~60%, summary near ~80%.
3. **Skills.** `/demo-1-terminal-arcade` — a 200-line markdown playbook loaded
   *by name only* until triggered. Dozens of skills cost almost no context.
4. **Memory write-back.** Quit and restart — `.decode/MEMORY.md` gained a
   one-sentence session summary, and it's back in context now.

## Deep dives

- `src/decode/context/` · `src/decode/memory/` · `src/decode/skills/` · `src/decode/services/lsp/`
- [ADR-0006 — conversation compaction](../../docs/adr/0006-conversation-compaction.md)
- [ADR-0007 — LSP integration](../../docs/adr/0007-lsp-integration.md)

## Background reading

| Article | Why read it here |
|---|---|
| [Memory: The Secret Sauce of AI Agents](https://www.decodingai.com/p/memory-the-secret-sauce-of-ai-agents) | Designing short-term working context plus procedural/semantic/episodic long-term memory layers. |
| [Context Engineering: 2025's #1 Skill in AI](https://www.decodingai.com/p/context-engineering-2025s-1-skill) | The core concept: filling the context window with the right info, at the right time, in the right format. |
| [How Does Memory for AI Agents Work?](https://www.decodingai.com/p/how-does-memory-for-ai-agents-work) | Four-layer memory model treating the context window as RAM — frames why AGENTS.md/MEMORY.md exist. |
| [Your RAG Pipeline Is Overkill](https://www.decodingai.com/p/recursive-language-models) | Beating context-window limits by letting the model programmatically explore context instead of stuffing it. |
| [From Harness Lock-In to Portable Context Layer](https://www.decodingai.com/p/the-context-layer) | Memory as a portable context layer served via MCP/skills — decoupling what the agent knows from the harness. |
| [Build, Configure, or Use As-Is: The Agentic Harness](https://www.decodingai.com/p/agentic-harness-system-design) | The skills + memory sections of the harness teardown: skills as cheap configuration, memory as the one component you build. |
