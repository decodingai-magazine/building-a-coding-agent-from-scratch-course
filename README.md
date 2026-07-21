<div align="center">
  <img src="assets/coding-agent-logo.png" alt="decode logo" width="140">
  <h1>Building a Coding Agent from Scratch</h1>
  <h3>From agent user to agent builder — learn how coding agents like Claude Code actually work by building your own, step by step</h3>
  <p class="tagline">Open-source course by <a href="https://www.decodingai.com">Decoding AI</a> in collaboration with <a href="https://modal.com">Modal</a>, <a href="https://www.comet.com/site/products/opik/">Opik</a> and <a href="https://www.zenml.io">ZenML</a>.</p>
</div>

<p align="center">
  <img src="assets/demo-frames.gif" alt="decode in the terminal" width="800">
</p>

## 📖 About This Course

You use a coding agent every day — Claude Code, Cursor, Codex — and you have no idea what happens between your prompt and its answer. Most engineers are fine with that. It costs more than they think.

In [one public test by LangChain](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness), changing only the *harness* around a coding agent — same model throughout — moved it from roughly 30th place into the top 5 on the Terminal-Bench benchmark. The harness decides what the model sees, what it's allowed to touch, and what happens when it's wrong — and it's the part nobody teaches.

This course teaches it the only way that sticks: you build one. Lesson by lesson, from an empty repo, you'll write **decode** — a terminal coding agent you can point at your own projects the same way you point Claude Code at them today:

- a [Pydantic AI](https://ai.pydantic.dev) **ReAct loop** on a selectable LLM provider — **Gemini**, **OpenRouter**, or an open model you serve yourself on **Modal**,
- driving **file / bash / web / LSP tools** and parallel read-only **Explore subagents**,
- behind a `prompt_toolkit` + `Rich` **TUI** that asks before every tool call and lets you steer, queue, or abort a running turn,
- with **cross-session memory**, **context compaction**, **replayable sessions**, and **Docker/Modal sandboxes** that hand the agent's work back as a git branch,
- a **durable headless runtime** (Kitaru) that survives `kill -9` and supports model-swapped replay,
- **Opik tracing** and an **eval suite** that answers the question tests can't: *is the agent actually good?*

By the end, nothing in your daily tools is magic anymore. You'll know why the agent asks before running `bash`, why it compacts your conversation near 80% of the context window, and why a secret must never get anywhere near the model's context — because you built the code that enforces all three.

The codebase is finished and honest about how it got here: 18 Architecture Decision Records in [`docs/adr/`](docs/adr/) and 1,800+ tests that run without an API key. The dead ends are in there too — we built a credential proxy to hide the git token from the sandbox, then [deleted it](docs/adr/0016-drop-credential-proxy.md) when we proved it protected nothing. The postmortem is part of the course.

<p align="center">
  <img src="assets/architecture.png" alt="decode architecture" width="620">
</p>

## 🤖 What You'll Do

- **Build one user turn end-to-end**: prompt in → model call → tool call → `y/n` approval → streamed answer → a session log you can replay tomorrow.
- **Contain the agent, rung by rung**: an allow/ask/deny permission gate, then a local Docker Workspace, then a remote Modal sandbox where nothing executes on your machine.
- **Treat the context window as a budget**: memory, compaction, skills, LSP code intelligence — each one a before/after experiment with a measured cost curve, not folklore.
- **`kill -9` a run mid-task and resume it**: checkpointed headless execution that never re-pays for finished work, plus durable human-in-the-loop waits you resolve from another terminal.
- **Replay history with the model swapped**: take a recorded run and ask "what would `gemini-2.5-pro` have done from this exact point?"
- **Fan out subagents in parallel**: read-only Explore children with budgets, report contracts, and failure notes instead of silent holes.
- **Evaluate the thing you built**: outcome benchmarks, behavior regression probes, and an LLM judge — because a green test suite can't tell you the agent got worse at its job.
- **Ship it to a team**: a teammate labels a GitHub issue and receives a reviewed pull request; you compare 5 models on the same task and merge the winner.

## 🎯 What You'll Learn

Every lesson works concrete-first: you watch `decode` do something — gate a `bash` call, compact a conversation near 80% of the window, resume a killed run — and then pull out the principle that generalizes to any agentic system:

- The anatomy of an agentic harness: harness vs loop vs model, and the seams between them.
- ReAct agent loops with tool calling and streaming (Pydantic AI).
- Human-in-the-loop design: approval gates, steering, follow-ups, aborts, durable waits.
- Context engineering with measurement: memory, compaction tiers, skills, LSP, truncation.
- Sandboxing and the trust ladder: permissions → Docker → remote Modal — with a credential story that is true as written.
- Durable execution, crash recovery, and replay with checkpoints (Kitaru / ZenML).
- Multi-provider inference, including serving your own open model on Modal.
- Subagent architectures: catalogs, parallel fan-out, report contracts.
- Observability and evaluation for agents: tracing, benchmarks, regression probes, LLM judges (Opik).
- The engineering discipline that holds it together: ADRs, a glossary, deterministic tests with a scripted model, CI, `uv`/`ruff`/`pytest`.

## 👥 Who Should Join?

**This course is for people who learn by building.** You'll finish with your own working coding agent, a mental model of every layer inside the tools you already use, and a codebase full of patterns to steal for your own agentic applications. Frameworks churn every six months; the mental models you build here outlast them. Skip this layer and you're betting your work on tools you can't inspect.

| Target Audience | Why Join? |
|-----------------|-----------|
| ML/AI Engineers | Build a complete agentic system — loop, tools, sandbox, evals — instead of another Notebook demo. |
| Software Engineers | Stop treating the agent in your terminal as a black box; understand and extend it from the inside. |
| AI/Platform Engineers | Learn the ops half nobody covers: sandboxing, durability, secrets, observability, shipping agents to a team. |

## 🎓 Prerequisites

| Category | Requirements |
|----------|-------------|
| **Skills** | - Python (Intermediate) <br/> - LLMs & agents (Beginner) |
| **Hardware** | Modern laptop/PC. Docker optional (for the local sandbox); everything heavier runs in the cloud. |
| **Level** | Intermediate (but with a little sweat and patience, anyone can do it) |

## 💰 Cost Structure

The course is open-source and free! Running the code costs **$0** if you stick to free tiers:

| Service | Cost |
|---------|------|
| Gemini API (default provider) | free tier ([Google AI Studio](https://aistudio.google.com/apikey)) |
| OpenRouter (alternative provider) | $0 on `:free` models (optional $10 credit raises the daily cap) |
| Modal (self-served open models + remote sandbox) | $30 free credits |
| Opik (tracing + evals) | free tier |
| Kitaru (durable runtime) | free, runs locally offline |
| GCP — deploy the agent to run remotely *(optional)* | ~$16/month while it's up — see [infra.md](getting_started/infra.md) |

The **only** part that costs real money is the optional last step: deploying the agent to GCP so headless runs execute entirely in the cloud ([infra.md](getting_started/infra.md)). Everything else — including the whole lesson track — runs free and local, and the stack tears down with one command when you're done.

**Reading-only? Everything's free!**

## ⚙️ How It Works

Self-paced, project-based, and open-source — no paywall, no certificate theater:

1. **Read the lesson** on [Decoding AI](https://www.decodingai.com) — each one covers one layer of the agent.
2. **Run the matching code** — every lesson maps to specific modules in this repo (see the [course outline](#-course-outline)).
3. **Go one level deeper** when you want the *why* — every non-obvious decision has an ADR recording the alternatives we rejected.
4. **Make it yours** — point `decode` at your own projects, swap the provider, break it, extend it. The 1,800+ tests will tell you when you've broken something real.

## 📬 Learn How to Build Coding Agents From Scratch

> Join 40k+ engineers subscribed to [the Decoding AI Magazine](https://www.decodingai.com/) to learn to build coding agents from scratch.

<a href="https://www.decodingai.com/" target="_blank">
  <img src="assets/decodingai.jpg" alt="Decoding AI Magazine" width="100%"/>
</a>

## 📚 Course Outline

Eight lessons, each pairing a written deep-dive with the code that implements it. The full codebase is already here — lessons publish progressively on [Decoding AI](https://www.decodingai.com) and get linked below as they go live.

| Lesson | Title | Description | Key code & docs |
|--------|-------|-------------|-----------------|
| 1 | What we're building + system design | Why the harness — not the model — decides how good your agent is. Anatomy of harness vs loop vs model, and the milestone map for the whole build. | [ADR index](docs/adr/) |
| 2 | The agent loop & the human in it | One user turn, end-to-end: prompt → tool call → approval → streamed render → session log. Steer it mid-flight, queue follow-ups, abort — and pick a model that can actually call tools. | `src/decode/agent/`, `src/decode/tui/`, `src/decode/tools/` · [ADR-0002](docs/adr/0002-milestone-1-vanilla-agent-architecture.md), [ADR-0005](docs/adr/0005-multi-llm-provider-support.md) |
| 3 | Durable execution, HITL & replay | `kill -9` a headless run and watch it resume from checkpoints. Durable human-in-the-loop waits, and what-if replay with the model swapped. | `src/decode/runtime/` · [ADR-0008](docs/adr/0008-kitaru-durable-runtime.md), [ADR-0010](docs/adr/0010-runtime-replay.md) |
| 4 | Context engineering: the window is a budget | Context engineering without measurement is folklore. Five moves — memory, compaction, skills, LSP, truncation — each a measured before/after experiment. | `src/decode/context/`, `src/decode/memory/`, `src/decode/skills/`, `src/decode/services/lsp/` · [ADR-0006](docs/adr/0006-conversation-compaction.md), [ADR-0007](docs/adr/0007-lsp-integration.md) |
| 5 | Containing the agent: permissions → sandbox | The trust ladder: the permission gate proper, then isolated Docker/Modal Workspaces with git hand-back — plus the credential proxy we built and then deleted, postmortem included. | `src/decode/permissions/`, `src/decode/sandbox/` · [ADR-0003](docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md), [ADR-0012](docs/adr/0012-isolated-workspace.md), [ADR-0016](docs/adr/0016-drop-credential-proxy.md) |
| 6 | Agents catalog, subagents & parallel fan-out | Build/Plan/Code-Reviewer agents and the read-only Explore subagent — parallel fan-out with budgets, report contracts, and no silent failures. | `src/decode/agents/` · [ADR-0013](docs/adr/0013-explore-subagents.md), [ADR-0017](docs/adr/0017-resilient-parallel-subagent-fanout.md) |
| 7 | Is the agent good? The eval stack | The suite is green — 1,800+ tests — and it still can't tell you the agent got worse. Outcome benchmarks, regression probes, LLM-as-judge, and online evals over live traffic. | `evals/`, `src/decode/observability/` · [ADR-0014](docs/adr/0014-opik-observability.md), [ADR-0017](docs/adr/0017-decode-eval-suite.md), [getting_started/evals.md](getting_started/evals.md) |
| 8 | Ship it to your team | Builder → operator: deployed runtime, environment-scoped secrets, a GitHub pipeline where labeling an issue returns a reviewed PR, and judged model-comparison cohorts. | [ADR-0015](docs/adr/0015-environment-bucket-secrets.md) · [credentials.md](getting_started/credentials.md), [infra.md](getting_started/infra.md) |

## 🏗️ Project Structure

One Python package; each module maps to one part of the architecture:

```
.
├── docs/
│   ├── adr/                  # Architecture Decision Records — the "why" of every choice
│   ├── glossary.md           # one canonical name per concept
│   └── evals.md              # the four-track eval suite, mapped
├── evals/                    # benchmark + regression probes + demo skills
├── tests/{unit,integration}/ # mirrors src/ 1:1; milestone capstones prove each milestone
└── src/decode/
    ├── cli.py                # Click entrypoint → launches the TUI
    ├── tui/                  # input: prompt_toolkit · output: Rich
    ├── harness/              # message queue + priority gate around the loop
    ├── agent/                # the Pydantic-AI ReAct loop (LLM ⇄ tools)
    ├── agents/               # agents catalog: Build / Plan / Code-Reviewer + Explore subagent
    ├── tools/                # file I/O, bash, web, todo, skills dispatch, LSP, ask_user
    ├── permissions/          # allow/ask/deny · modes · settings.json
    ├── sandbox/              # bash + file tools seam: none (host) / docker / modal
    ├── services/lsp/         # hand-rolled stdio LSP client (ty)
    ├── runtime/              # Kitaru durable flow: decode run / replay / HITL
    ├── context/              # compaction + conversation log (JSONL)
    ├── memory/               # AGENTS.md / MEMORY.md loading + write-back
    ├── observability/        # Opik tracing
    └── config/, entities/    # settings singleton · shared models
```

## 🚀 Getting Started

Everything lives under [`getting_started/`](getting_started/) — one core guide, plus one focused guide per side quest:

| Guide | What's inside |
|-------|---------------|
| [install_and_usage.md](getting_started/install_and_usage.md) | The core path: requirements, install, LLM provider setup (Gemini / OpenRouter / Modal), the REPL, and the dev workflow — about 5 minutes to a running agent. |
| [runtime.md](getting_started/runtime.md) | Headless runs (`decode run`), durable checkpoints, human-in-the-loop waits, and model-swapped replay. |
| [sandboxing.md](getting_started/sandboxing.md) | Isolated Docker/Modal Workspaces, working on any repo with `--repo`, and the git hand-back. |
| [credentials.md](getting_started/credentials.md) | Environments & secrets, walked end-to-end. |
| [modal_models.md](getting_started/modal_models.md) | Picking and serving your own open model on Modal. |
| [infra.md](getting_started/infra.md) | Deploying the remote runtime stack to the cloud. 💰 *The only part of the course that costs real money (~$16/month on GCP) — and it's entirely optional.* |

**Pro tip:** Read the accompanying lessons first for a better understanding of the system you'll build.

## 🤝 Sponsors

<p align="center">
  <img src="assets/github-repo-banner-dark.png" alt="Sponsored by Modal, Opik and Kitaru" width="760">
</p>

<p align="center">
  Special thanks to <a href="https://modal.com" target="_blank"><b>Modal</b></a>, <a href="https://www.comet.com/site/products/opik/" target="_blank"><b>Opik</b></a> (by Comet), and <a href="https://www.zenml.io" target="_blank"><b>Kitaru</b></a> (by ZenML) for sponsoring this open-source course and keeping it free!
</p>

## 💡 Questions and Troubleshooting

Have questions or running into issues? We're here to help!

Open a [GitHub issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) for:
- Questions about the course material
- Technical troubleshooting (setup, providers, sandbox, runtime)
- Clarification on concepts

Known gotchas (macOS Kitaru daemon crash, non-tool-capable models, stale sandbox Workspaces) are documented inline in the [`getting_started/`](getting_started/) guides.

## ❓ FAQ

**Is the code complete, or does it grow with the lessons?**
Complete. The full agent is in this repo today — lessons publish progressively and walk you through how it got here, decision by decision.

**Do I need a paid API key?**
No. The default Gemini provider has a free tier, OpenRouter routes across `:free` models, and Modal gives $30 in credits — see [Cost Structure](#-cost-structure).

**How does this compare to the paid [Agent Engineering course](https://academy.towardsai.net/courses/agent-engineering?ref=b3ab31)?**
They sit one layer apart. [Agent Engineering](https://academy.towardsai.net/courses/agent-engineering?ref=b3ab31) (with Towards AI) teaches you to design, evaluate, and deploy production multi-agent *applications* — research agents, writing workflows, MCP servers, CI/CD. This free course goes underneath: you build the *coding agent harness itself* — the loop, the permission gate, the sandbox, the durable runtime that tools like Claude Code are made of. If Agent Engineering teaches you to engineer agents, this course teaches you to build the tool that builds them. They pair well; neither requires the other.

## 🥂 Contributing

As an open-source course, we may not be able to fix all the bugs that arise.

If you find any bugs and know how to fix them, support future readers by contributing to this course with your bug fix.

You can always contribute by:
- Forking the repository
- Fixing the bug (run `make ci` — the suite needs no API key)
- Creating a pull request

We will deeply appreciate your support for the AI community and future readers 🤗

## 👨‍🏫 Course Author

<table style="border-collapse: collapse; border: none;">
  <tr style="border: none;">
    <td width="15%" style="border: none;">
      <a href="https://github.com/iusztinpaul" target="_blank">
        <img src="https://github.com/iusztinpaul.png" width="100" style="border-radius: 50%;" alt="Paul Iusztin"/>
      </a>
    </td>
    <td width="85%" style="border: none;">
      <b><a href="https://github.com/iusztinpaul" target="_blank">Paul Iusztin</a></b> — AI Engineer & Founder of <a href="https://www.decodingai.com">Decoding AI</a><br/>
      After shipping 21 AI applications, Paul uses his best-selling <a href="https://www.amazon.com/LLM-Engineers-Handbook-engineering-production/dp/1836200072">LLM Engineer's Handbook</a>, Decoding AI Magazine, and courses like this one to lead 160,000+ AI engineers out of demo purgatory and into production-grade engineering.
    </td>
  </tr>
</table>

## 📬 Learn How to Build Coding Agents From Scratch

> Join 40k+ engineers subscribed to [the Decoding AI Magazine](https://www.decodingai.com/) to learn to build coding agents from scratch.

<a href="https://www.decodingai.com/" target="_blank">
  <img src="assets/decodingai.jpg" alt="Decoding AI Magazine" width="100%"/>
</a>

## License

This course is an open-source project released under the [Apache-2.0 license](LICENSE). Thus, as long you distribute our LICENSE and acknowledge this repository, you can safely clone or fork this project and use it as a source of inspiration for your educational projects (e.g., university, college degree, personal projects, etc.).
