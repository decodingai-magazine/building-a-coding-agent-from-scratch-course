# Glossary

The canonical vocabulary for the coding agent. When code, docs, specs, or conversation use a domain concept, use the term as it appears here. PRs that introduce or rename a domain concept update this file in the same change.

The seed rows below are taken directly from the project architecture; fill `Notes` and add rows as each step lands.

| Term | Definition | Notes |
|---|---|---|
| **Harness** | The runtime wrapper around the agent loop: message queue, priority gate, and the wiring to TUI, sandbox, hooks, and services. | The whole running system minus the model itself. |
| **Agent Loop** | The Pydantic-AI ReAct loop that alternates between the LLM and tool calls until the task is done. | Also called the "React Loop". One loop per turn. |
| **Priority Gate** | The check between loop iterations that injects newly-arrived user messages from the queue into the running loop. | Lets the user steer mid-run without restarting. Realised by the two queues below (Steering + Follow-up). |
| **Steering** | A mid-turn user line, drained from the steering queue *before each model-request leg* and injected as a user message, so the model sees it on the next leg (never mid-stream/mid-tool). | Plain `Enter` while a turn is busy (ADR-0002 §4). Contrast Follow-up. |
| **Follow-up** | A queued user line drained *only at the would-stop boundary*, continuing the conversation as one more turn-leg once the current turn would otherwise stop. | `Alt+Enter` while busy (`InputIntent.FOLLOW_UP`). Contrast Steering. |
| **Decision Channel** | The single mid-turn input surface (`DecisionChannel`): when a turn must ask the human before continuing, the requester awaits a future on this channel and the main input loop routes the next typed line into it. | One decision pending at a time (single-flight); avoids a second `prompt_async()` deadlock (ADR-0002 §3-4). Serves both Deferred Approval and AskUser. |
| **Deferred Approval** | The ask-on-every-tool permission step: a gated tool raises `ApprovalRequired`, the run resolves to a `DeferredToolRequests` pause, and the human's allow/deny (via the Decision Channel) resolves it before the tool executes. | The deferred-tool mechanism that also enables mid-turn steering (ADR-0002 §2-3). |
| **AskUser** | The one blocking, *ungated* tool: the agent asks the human a free-form question mid-turn and the typed answer becomes the tool result. | Rides the Decision Channel, not the permission gate (gating it would double-prompt) — ADR-0002 §7. |
| **Sandbox** | The execution environment for Bash/tool side effects — `local` (Docker/Firecracker) or `remote` (Modal), behind one `run` seam. | The one deliberate infrastructure abstraction (see ADR). |
| **Services Interface** | The boundary exposing LLM Gateway, Memory, LSP servers, and MCP servers to the agent. | Distinct from `tools/` (what the model calls). |
| **Compaction** | Context-engineering step that summarizes old turns when tokens approach the window limit; microcompaction drops tool outputs. | Keeps a recovery log in SQLite. |
| **Subagent** | A child agent spawned by the main agent via the Agent tool; runs a scoped task and returns a compressed result. | From the Agents Catalog (Build/Plan/Explore/Code-Reviewer). |
| **Skill** | A user-defined capability under `.agents/skills`, surfaced to the model via a dispatcher tool and wrapped in a `<system_reminder>`. | Capped at ~1% of the context window. |
| **Permission Mode** | The gate's evaluation mode — one of `default`, `plan`, `edit`, `bypass` — that, with the tool's Tool Kind and any matching Permission Rule, decides allow/ask/deny for a tool call. | Replaces M1's single `ask` mode (ADR-0003 §1). Mutable on the gate; cycled by Shift+Tab. |
| **Default Mode** | Permission Mode where read-only tools auto-allow and mutating tools ask the human. | The startup default (ADR-0003 §1). |
| **Plan Mode** | Permission Mode where read-only tools auto-allow and every mutating tool is denied with a reason telling the model to present its plan and call `exit_plan_mode`. | Entered via `enter_plan_mode`, `/mode plan`, or the `plan` agent (ADR-0003 §1,8). |
| **Edit Mode** | Permission Mode where read-only and file-edit tools (`write`/`edit`) auto-allow but other mutating tools (`bash`) ask. | a.k.a. acceptEdits; `exit_plan_mode` lands here on approval (ADR-0003 §1,8). |
| **Bypass Mode** | Permission Mode where every tool call is allowed with no prompt. | No human gate — use with care (ADR-0003 §1). |
| **Tool Kind** | A tool's permission classification — `read_only`, `file_edit`, or `other` — declared once on its registry spec and read by the gate so Edit Mode can tell a file edit from a shell exec. | Replaces M1's single `read_only` bool (derived from it). `todo_write` is `read_only` (in-memory, no side effect) — ADR-0003 §2. |
| **Permission Rule** | An allow/deny entry of the form `Tool(pattern)` (or bare `Tool`), matched against the call's subject (bash→command, file→path, web→url). | Two sources: the user's optional `.decode/settings.json` and an agent's catalog frontmatter. Precedence: deny → allow → mode (ADR-0003 §3-4). The `always` answer persists a user allow rule. |
| **Agents Catalog** | The set of built-in agent personas (Build, Plan, Explore, Code-Reviewer), each a Markdown file with YAML frontmatter (name, description, tools allowlist, default mode, optional allow/deny rules) plus a system-prompt body, loaded and validated at startup. | Used as the main agent only this milestone — no subagent spawning (ADR-0003 §5). |
| **Agent (persona)** | One Agents Catalog entry: a validated `AgentDef` carrying the system prompt, the allowed tool set, optional agent-scoped rules, and the default Permission Mode the agent runs under. | Distinct from the Pydantic-AI `Agent` object; selecting one sets `deps.active_agent`, resets the gate mode, and loads its rules (ADR-0003 §5-7). |
| **EnterPlanMode / ExitPlanMode** | Model-callable tools that switch the gate into Plan Mode and back. `exit_plan_mode` asks the human to approve the plan (via the Decision Channel); on approval it switches to Edit Mode. | Ungated controls — they ride the Decision Channel, not the permission gate (ADR-0003 §8). |
<!-- | **Term** | One-sentence definition, identified by `canonical_id`. | Distinctions from adjacent terms; deliberate exclusions. | -->
