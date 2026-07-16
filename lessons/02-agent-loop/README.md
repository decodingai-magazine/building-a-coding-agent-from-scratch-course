# Lesson 2 — The agent loop & the human in it

One user turn, end-to-end: prompt → tool call → approval → streamed render →
session log. Steer it mid-flight, queue follow-ups, abort — and pick a model
that can actually call tools.

## Run it

```bash
./lessons/02-agent-loop/run.sh
```

Headless slice of the ReAct loop: the model reasons, calls tools, observes
results, answers. Then the provider seam: same loop, different model behind it.

## Playbook (interactive)

```bash
uv run decode
```

1. **The gate.** Ask it to `create a file called loop_demo.txt with one haiku
   about tool calling` — read-only tools auto-allow, but `write` stops and asks
   `y/n`. Deny it once (`n`) and watch the model adapt.
2. **Steer.** Give it something slow (`summarize every ADR in docs/adr/`), then
   press `Enter` mid-turn and type `only the first three, as one table` — the
   turn redirects *now*.
3. **Follow-up.** Same setup, but `Alt+Enter`: the message queues and runs when
   the turn finishes.
4. **Abort.** `Esc` kills the turn cleanly; the session log keeps everything up
   to the abort.
5. **Model choice.** Set a non-tool-capable model and watch the loop break —
   the model *narrates* tool calls instead of making them. This is why the
   defaults are pinned to tool-capable models.

## Deep dives

- `src/decode/agent/` · `src/decode/tui/` · `src/decode/tools/` · `src/decode/harness/`
- [ADR-0002 — vanilla agent architecture](../../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)
- [ADR-0005 — multi-LLM provider support](../../docs/adr/0005-multi-llm-provider-support.md)

## Background reading

| Article | Why read it here |
|---|---|
| [LLM Agents Demystified](https://www.decodingai.com/p/llm-agents-demystified) | ReAct thought→action→observation loop explained with prompt structure and tool management. |
| [Why MCP Breaks Old Enterprise AI Architectures](https://www.decodingai.com/p/why-mcp-breaks-old-enterprise-ai) | Why MCP standardizes tools/resources/prompts on independent servers — background for the MCP tool factory. |
| [Build with MCP Like a Real Engineer](https://www.decodingai.com/p/build-with-mcp-like-a-real-engineer) | Hands-on MCP host-client-server wiring for an AI PR reviewer — concrete tool-integration architecture. |
| [AI Agents in 5 Levels of Difficulty](https://www.decodingai.com/p/ai-agents-in-5-levels-of-difficulty) | Progression from basic tool-calling to full agentic systems — state, memory, reasoning, coordination. |
| [AI Workflows vs Agents: The Autonomy Slider](https://www.decodingai.com/p/ai-workflows-vs-agents-the-autonomy) | The autonomy-slider framing behind "human in the loop": how much the LLM decides vs the developer. |
| [Structured Outputs: The Silent Hero of Production AI](https://www.decodingai.com/p/llm-structured-outputs-the-only-way) | Tool calls *are* structured outputs — JSON-from-scratch vs Pydantic validation vs native API enforcement. |
| [Tool Calling From Scratch to Production: The Complete Guide](https://www.decodingai.com/p/tool-calling-from-scratch-to-production) | The 5-step request-execute-respond tool loop from scratch, plus read-vs-write action risk — the approval-gate motivation. |
| [You're Not Building Agents: Learn the Fundamentals From Scratch](https://www.decodingai.com/p/ai-agents-planning) | ReAct vs Plan-and-Execute planning patterns — the loop mechanics that make an agent an agent. |
| [Building Production ReAct Agents From Scratch Is Simple](https://www.decodingai.com/p/building-production-react-agents) | A production ReAct loop dissected from source: model/tools nodes, conditional edges, state, tool error handling. |
