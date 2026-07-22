<div align="center">
  <img src="assets/coding-agent-logo.png" alt="decode logo" width="140">
  <h1>Building a Coding Agent From Scratch</h1>
  <h3>The harness — not the model — is what makes a coding agent good. Build one from scratch: a bare agent loop that grows, lesson by lesson, into a swarm of remote agents running in parallel in the cloud</h3>
  <p class="tagline">Open-source course by <a href="https://www.decodingai.com">Decoding AI</a> in collaboration with <a href="https://modal.com?source=decodingai&campaign=harnesseng">Modal</a>, <a href="https://www.comet.com/site/?utm_source=newsletter&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course">Opik (by Comet)</a> and <a href="https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand">Kitaru (by ZenML)</a>.</p>
</div>

<p align="center">
  <img src="https://img.shields.io/badge/type-open--source_course-8a2be2" alt="Open-source course">
  <img src="https://img.shields.io/badge/cost_to_run-%240-2ea44f" alt="$0 to run">
  <img src="https://img.shields.io/badge/articles-8-4c8eda" alt="8 articles">
  <img src="https://img.shields.io/badge/videos-4-ff0000" alt="4 videos">
  <img src="https://img.shields.io/badge/code-from_scratch-orange" alt="Code from scratch">
  <img src="https://img.shields.io/badge/license-Apache--2.0-lightgrey" alt="Apache-2.0 license">
</p>

<p align="center">
  <img src="assets/demo-frames.gif" alt="decode in the terminal" width="800">
</p>

> **Try the finished agent first — 5 minutes, $0:**
>
> ```bash
> # install uv first if you don't have it:  curl -LsSf https://astral.sh/uv/install.sh | sh
> git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
> cd building-a-coding-agent-from-scratch-course
> make install
> cp .env.example .env   # set GEMINI_API_KEY — free at https://aistudio.google.com/apikey
> uv run decode
> ```
>
> Then type `/demo-` and pick a demo — see [what they do](#-see-it-work) below. Full guide: [install_and_usage.md](getting_started/install_and_usage.md).

## 📖 About This Course

Here's the most underrated result in agent engineering: in [one public experiment by LangChain](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness), changing only the *harness* — same model throughout — moved a coding agent from roughly 30th place into the top 5 on Terminal-Bench. Not a bigger model. Not more compute. Just the layer that decides what the model sees, what it touches, and what happens when it's wrong. That layer is the entire difference between an LLM in a chat window and the Claude Code you trust with your repo — and it's the part nobody teaches.

This course teaches it by making you build it. We spent months under the hood of Claude Code (via its leaked source), [OpenCode](https://github.com/anomalyco/opencode), [Pi](https://github.com/earendil-works/pi), and [Aider](https://github.com/aider-ai/aider), and distilled what we found into 8 lessons where, starting from an empty repo, you build **decode** — a terminal coding agent that grows lesson by lesson from a bare agent loop into a swarm of remote agents running in parallel in the cloud. By lesson 2 you type a prompt and watch your own agent ask permission before running `bash`; by lesson 8 it's deployed to the cloud, building the same feature 5–10× in parallel and handing you back reviewed pull requests to merge. One headless core, two ways to drive it: an interactive TUI wired to one live session, and a remote runtime that runs N headless copies in parallel. Under the hood: a [Pydantic AI](https://ai.pydantic.dev) **ReAct loop** on Gemini, OpenRouter, or an open model you serve on [Modal](https://modal.com/docs/guide/endpoints?source=decodingai&campaign=harnesseng); **file / bash / web / LSP tools** and parallel **Explore subagents**; Docker/[Modal](https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng) **sandboxes**; a durable [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) **runtime**; [Opik](https://www.comet.com/site/?utm_source=newsletter&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) **tracing and evals**.

Finish the build and you walk away with two things. The skill behind that leaderboard jump — engineering custom harnesses for your own AI products. And x-ray vision into the tools you already pay for: nothing Claude Code or Codex does is magic once you've built the code underneath, which is what turns you from user into power user. Skip this layer and you're betting your work on tools you can't inspect.

The codebase is finished and honest: 1,800+ tests that run without an API key, and the dead ends kept in — we built a credential proxy to hide the git token from the sandbox, then deleted it when we proved it protected nothing. The postmortem is part of the course.

<p align="center">
  <img src="assets/architecture.png" alt="decode architecture" width="620">
</p>
<p align="center"><i>Two interface modes on the left, the headless harness on the right, the evals plane underneath.</i></p>

### The agent is ~20 lines. The course is everything else.

```python
agent = Agent(
    build_model(settings.llm_provider),        # gemini | openrouter | modal
    deps_type=AgentDeps,                       # cwd, event sink, permission gate
    output_type=[str, DeferredToolRequests],   # final answer, or tools paused for approval
)
register_tools(agent)                          # read, edit, bash, grep, ...

async with agent.iter(prompt, message_history=history) as run:
    async for node in run:                     # model request → tool calls → repeat
        stream_events(node)
```

That's the *entire* tool-calling agent — the thing people call "the agent" ends here. Everything else in this repo — the permission gate, the sandbox, compaction, the steering queue, the durable runtime, the subagent fan-out, the evals — is the harness. That's what the 8 lessons build.

## 🎮 See It Work

The finished agent ships with demo skills under [`.decode/skills/`](.decode/skills/) — open the TUI, type `/demo-`, pick one, and watch the harness you're about to build do real work:

<table>
  <tr>
    <td width="50%">
      <img src="assets/demo-skills.png" alt="The demo skills listed inside the TUI"/>
      <p align="center"><i>Six demos, one keystroke away</i></p>
    </td>
    <td width="50%">
      <img src="assets/demo-snake-game.png" alt="A playable Snake game built by decode"/>
      <p align="center"><i><code>/demo-1-terminal-arcade</code> — one prompt, a playable Snake game</i></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="assets/demo-repo-pulse.png" alt="Live GitHub repo data rendered as a web dashboard"/>
      <p align="center"><i><code>/demo-3-repo-pulse</code> — live GitHub API data rendered as a dashboard</i></p>
    </td>
    <td width="50%">
      <img src="assets/demo-knowledge-graph.png" alt="An interactive knowledge graph scraped from web articles"/>
      <p align="center"><i><code>/demo-6-article-kg</code> — web articles scraped into an interactive knowledge graph</i></p>
    </td>
  </tr>
</table>

And the machinery underneath is real infrastructure you'll stand up yourself, not a diagram:

<table>
  <tr>
    <td width="50%">
      <img src="assets/kitaru-replay.png" alt="A durable run recorded step by step in Kitaru"/>
      <p align="center"><i>Every run recorded step by step in <a href="https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand">Kitaru</a> — kill it, resume it, replay it with the model swapped</i></p>
    </td>
    <td width="50%">
      <img src="assets/modal-sandboxes.png" alt="Live Modal sandboxes executing the agent's tools"/>
      <p align="center"><i>The agent's <code>bash</code> runs in disposable <a href="https://modal.com/docs/guide/sandboxes?source=decodingai&campaign=harnesseng">Modal sandboxes</a> — six live here, ~1.3s cold start</i></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="assets/modal-open-model.png" alt="A self-served open model endpoint on Modal"/>
      <p align="center"><i>Your own Qwen3.6-35B served on an H200 via a <a href="https://modal.com/docs/guide/endpoints?source=decodingai&campaign=harnesseng">Modal endpoint</a> — same harness, your model</i></p>
    </td>
    <td width="50%">
      <img src="assets/opik-threads.png" alt="Sessions traced in Opik with secrets scrubbed"/>
      <p align="center"><i>Every session traced in <a href="https://www.comet.com/site/?utm_source=newsletter&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course">Opik</a> — note the secrets scrubbed before they reach a log</i></p>
    </td>
  </tr>
</table>

## 🤖 What You'll Do

- **Build one user turn end-to-end**: prompt in → model call → tool call → `y/n` approval → streamed answer → a session log you can `--resume` tomorrow — then steer it mid-turn, queue follow-ups, or abort, all through the same channel that gates every tool call.
- **`kill -9` a run mid-task and resume it**: checkpointed headless execution that never re-pays for finished work, plus durable human-in-the-loop waits you resolve from another terminal.
- **Replay history with the model swapped**: take a recorded run and ask "what would `gemini-2.5-pro` have done from this exact point?"
- **Contain the agent, rung by rung**: four permission modes with an allow/ask/deny decision matrix, then a local Docker Workspace, then a remote Modal sandbox where nothing executes on your machine.
- **Treat the context window as a budget**: memory, compaction, skills, LSP code intelligence — each one a before/after experiment with a measured cost curve, not folklore.
- **Fan out subagents in parallel**: one call spawns N read-only Explore children with budgets, report contracts, and failure notes instead of silent holes — and you feel exactly why owning your inference endpoint matters when the burst hits it.
- **Evaluate the thing you built**: outcome benchmarks, behavior regression probes, online evals, and an LLM judge — because a green test suite can't tell you the agent got worse at its job. Then run the whole eval harness at scale, headless on Kitaru + Modal.
- **Run swarms of remote agents**: deploy the stack to GCP + Modal; a teammate labels a GitHub issue and receives a reviewed pull request; the same feature runs 5–10× in parallel and the judged winner merges.

Every step works concrete-first: watch `decode` do it — gate a `bash` call, compact near 80% of the window, resume a killed run — then pull out the principle that generalizes to any agentic system: context engineering, the trust ladder, durable execution, subagent design, agent evals. The [course outline](#-course-outline) maps each lesson to its code modules.

## 👥 Who Should Join?

**For: engineers who learn by building.** You'll finish with your own working coding agent, a mental model of every layer in the tools you already use, and patterns to steal for your own agentic applications.

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

Running the code costs **$0** if you stick to free tiers:

| Service | Cost |
|---------|------|
| Gemini API (default provider) | free tier ([Google AI Studio](https://aistudio.google.com/apikey)) |
| OpenRouter (alternative provider) | $0 on `:free` models (optional $10 credit raises the daily cap) |
| [Modal](https://modal.com?source=decodingai&campaign=harnesseng) (self-served open models + remote sandbox) | $30 free credits |
| [Opik](https://www.comet.com/site/?utm_source=newsletter&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) (tracing + evals) | free tier |
| [Kitaru](https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand) (durable runtime) | free, runs locally offline |
| GCP — deploy the agent to run remotely *(optional)* | ~$16/month while it's up — see [infra.md](getting_started/infra.md) |

**Reading-only? Everything's free!**

## ⚙️ How It Works

Self-paced and project-based — no paywall, no certificate theater: read the lesson on [Decoding AI](https://www.decodingai.com), run the matching code — then point `decode` at your own projects and break it.

## 📬 Learn How to Build Coding Agents From Scratch

> Join 40k+ engineers subscribed to [the Decoding AI Magazine](https://www.decodingai.com/) to learn to build coding agents from scratch.

<a href="https://www.decodingai.com/" target="_blank">
  <img src="assets/decodingai.jpg" alt="Decoding AI Magazine" width="100%"/>
</a>

## 📚 Course Outline

Eight written lessons and four videos — video 2 covers lessons 2–3, video 3 covers lessons 4–6. The full codebase is already here — lessons publish progressively on [Decoding AI](https://www.decodingai.com) and the links below light up as they go live.

| Lesson | Written Lesson | Video Lesson | Description | Running the code |
|--------|----------------|--------------|-------------|------------------|
| 1 | [Building a Coding Agent From Scratch: Harness Architecture](https://www.decodingai.com/p/building-a-coding-agent-from-scratch-system-design) — **live now** | 🎬 *Video 1 — coming soon* | decode's architecture top-down: the harness vs the agent loop vs the model, every component and interface, and how they all connect — the map for the whole build. | [install_and_usage.md](getting_started/install_and_usage.md) |
| 2 | The Agent Loop Plugged Into the TUI (The Interactive Mode) — *next week* | 🎬 *Video 2 — coming soon* | The ReAct loop and the core tools — `read` / `edit` / `bash`, and why `bash` is the scary one. Approve every tool call, steer mid-turn, queue follow-ups, abort; session logs with `--resume`; three LLM providers (Gemini / OpenRouter / Modal) plus Opik tracing. | `src/decode/agent/`, `src/decode/tui/`, `src/decode/tools/` |
| 3 | The Runtime: Durable Execution, HITL & Replays (The Headless Mode) — *coming soon* | 🎬 *Video 2 — coming soon* | Two execution semantics, chosen by entry path: `asyncio.sleep` vs `kitaru.wait`. `kill -9` a headless run and resume from checkpoints, durable human-in-the-loop waits resolved from another terminal, and three-run replay with the model swapped as a flow input. | `src/decode/runtime/` · [runtime.md](getting_started/runtime.md) |
| 4 | Containing the Agent: Permissions & Sandbox — *coming soon* | 🎬 *Video 3 — coming soon* | The trust ladder: four permission modes (default / edit / plan / bypass) and the allow/ask/deny decision matrix, then isolated Workspaces — local via Docker, remote via Modal — with git hand-back. | `src/decode/permissions/`, `src/decode/sandbox/` · [sandboxing.md](getting_started/sandboxing.md) |
| 5 | Context Engineering for Coding Agents — *coming soon* | 🎬 *Video 3 — coming soon* | Context engineering without measurement is folklore. Five moves — memory that persists, compaction that evicts (micro at ~60%, full at ~80%), skills that never load until needed, LSP precision instead of bulk file reads, truncation — each proven with a measured before/after in Opik. | `src/decode/context/`, `src/decode/memory/`, `src/decode/skills/`, `src/decode/services/lsp/` |
| 6 | Agents Catalog, Subagents & Parallel Fan-out — *coming soon* | 🎬 *Video 3 — coming soon* | Build/Plan/Code-Reviewer agents plus the read-only Explore subagent: one `agent` call fans out N parallel children under a width cap, each bound by a report contract and a budget split — and why owning your inference endpoint matters once the fan-out burst hits its concurrency limits. | `src/decode/agents/` |
| 7 | Does It Work? Benchmarks, Regression & Online AI Evals — *coming soon* | 🎬 *Video 4 — coming soon* | Three questions, three mechanisms: benchmarks answer *does it work?*, regression probes answer *does it still work?*, online evals over live traffic answer *does it keep working?* — because a green 1,800-test suite can't answer any of them. Then the whole eval harness runs at scale, headless on Kitaru + Modal. | `evals/`, `src/decode/observability/` · [evals.md](getting_started/evals.md) |
| 8 | Running Swarms of Remote Agents — *coming soon* | No video | Builder → operator: deploy the stack to GCP + Modal with environment-scoped secrets, then two case studies — a feature shipped as a cloud-reviewed GitHub PR, and the same feature built 5–10× in parallel, judged, winner merged. | [credentials.md](getting_started/credentials.md), [infra.md](getting_started/infra.md) |

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

## 🤝 Sponsors

<p align="center">
  <img src="assets/github-repo-banner-dark.png" alt="Sponsored by Modal, Opik and Kitaru" width="760">
</p>

<p align="center">
  Special thanks to <a href="https://modal.com?source=decodingai&campaign=harnesseng" target="_blank"><b>Modal</b></a>, <a href="https://www.comet.com/site/?utm_source=newsletter&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course" target="_blank"><b>Opik</b></a> (by Comet), and <a href="https://www.zenml.io/product/kitaru?utm_source=decodingai&utm_medium=referral&utm_campaign=coding-agent-course&utm_content=brand" target="_blank"><b>Kitaru</b></a> (by ZenML) for sponsoring this open-source course and keeping it free!
</p>

<p align="center">
  Opik and Kitaru are open source, too — star them: <a href="https://github.com/comet-ml/opik" target="_blank">Opik on GitHub</a> · <a href="https://github.com/zenml-io/kitaru" target="_blank">Kitaru on GitHub</a>.
</p>

## 💡 Questions and Troubleshooting

Open a [GitHub issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) for course questions, setup trouble, or concept clarifications. Known gotchas (macOS Kitaru daemon crash, non-tool-capable models, stale sandbox Workspaces) are documented inline in the [`getting_started/`](getting_started/) guides.

## ❓ FAQ

**Do I need a paid API key?**
No. The default Gemini provider has a free tier, OpenRouter routes across `:free` models, and [Modal](https://modal.com?source=decodingai&campaign=harnesseng) gives $30 in credits — see [Cost Structure](#-cost-structure).

**Why Python and not TypeScript or Go?**
Accessibility — our audience knows Python. Claude Code, OpenCode, and Pi picked TypeScript; Go compiles to one tidy binary; and Aider proves Python can carry a serious coding agent. The course focuses on the design decisions, which transfer to any language — the implementation is swappable.

**Why build from scratch instead of extending Pi, DeepAgents, or an existing harness?**
Because adding custom logic to an existing harness is the easy part — *knowing what to add* requires understanding the internals. That's the fundamentals, and it's what still makes AI engineers valuable. Build a coding agent once and you're equipped to build a custom agent for any use case.

**Why is there no vector database or codebase index?**
Deliberately. Memory is plain files — `AGENTS.md` for your instructions, `MEMORY.md` for what the agent learns — and the repo is explored just-in-time with grep. Fresh reads beat a stale index, and you get to see exactly what the agent knows.

## 🥂 Contributing

Found a bug and know the fix? Fork, fix, run `make ci` (no API key needed), and open a pull request. Future readers thank you 🤗

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

## ⭐ One More Thing

If this course removes one layer of magic from the tools you use every day, [star the repo](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course) — stars are how other engineers find it.

And tell us: **which part of your daily coding agent is still a black box to you — the loop, the sandbox, the memory, something else?** [Open an issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) and say so. We read every one.

## License

Released under [Apache-2.0](LICENSE) — clone, fork, and build on it; keep the LICENSE and credit this repo.
