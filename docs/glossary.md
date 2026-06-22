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
<!-- | **Term** | One-sentence definition, identified by `canonical_id`. | Distinctions from adjacent terms; deliberate exclusions. | -->
