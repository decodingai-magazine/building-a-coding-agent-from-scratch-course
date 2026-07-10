# decode

**decode** is a terminal **coding agent** you run in your terminal — a [Pydantic AI](https://ai.pydantic.dev) ReAct loop on a selectable LLM provider (**Gemini**, **OpenRouter**, or a model you serve on **Modal**), driving file / bash / web tools behind a `prompt_toolkit` + `Rich` TUI, with an ask-before-every-tool permission gate, project memory, and replayable sessions.

This repository is an **educational, open-source course** that builds `decode` from scratch, step by step. It is a single Python package (`decode`); each module under `src/decode/` maps to one part of the architecture.

> **Status — Milestone 1 (vanilla on-device agent).** What's built today: the agent loop with **selectable LLM providers** (Gemini / OpenRouter / Modal — run it for free; see [LLM providers](#llm-providers)), the steering TUI, the core tools, the permission gate, memory, and a session log. Later milestones add observability (Opik) and MCP; the durability runtime (Kitaru — see [Headless runtime](#headless-runtime-decode-run)) and [sandboxing](#sandboxing) have since landed — see [`AGENTS.md`](AGENTS.md) and the architecture decisions in [`docs/adr/0002-milestone-1-vanilla-agent-architecture.md`](docs/adr/0002-milestone-1-vanilla-agent-architecture.md) and [`docs/adr/0005-multi-llm-provider-support.md`](docs/adr/0005-multi-llm-provider-support.md).

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — the package/runtime manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`). uv installs the pinned Python 3.12 for you.
- **An API key for one LLM provider.** By default that's a **Gemini API key** ([Google AI Studio](https://aistudio.google.com/apikey) — free tier); you can instead run for free on **OpenRouter** (`:free` models) or a model you serve on **Modal** ($30 free credits). See [LLM providers](#llm-providers).

## Install

```bash
git clone git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

### Run `decode` as a CLI tool (recommended)

`make install` lets you run the agent with `uv run decode`. To type just **`decode`** from **any project directory**, put it on your PATH:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

Then `cd` into any project and run it directly:

```bash
cd ~/my-project
decode              # start a fresh session in this directory
decode --resume     # continue the most recent session here
```

`decode` always operates on the directory you launch it from (its working dir), and writes everything it produces under **`<cwd>/.decode/`** in that project:

- `.decode/sessions/*.jsonl` — replayable session transcripts (used by `decode --resume`).
- `.decode/MEMORY.md` — the one-sentence-per-session memory the agent appends on exit and reloads next time.
- `.decode/logs/decode.log` — the log file (logs go here, **not** to the terminal, so the REPL stays clean).

The whole `.decode/` directory is gitignored.

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

## Configure

`decode` reads its config from environment variables, including a local `.env` file (loaded via pydantic-settings):

```bash
cp .env.example .env
# then edit .env and set:
#   GEMINI_API_KEY=your-key-here
#   GEMINI_MODEL=gemini-2.5-flash   # optional; this is the default
```

`.env` is gitignored. Precedence is **shell env var → `.env` → built-in default**. (If the selected provider's required config is missing, `decode` prints a one-line hint and exits instead of crashing.)

## LLM providers

`decode` runs the agent loop on one selectable **LLM provider**, and every option has a free path:

- **Gemini** (default) — free credits on [Google AI Studio](https://aistudio.google.com/apikey).
- **OpenRouter** — an OpenAI-compatible gateway with `:free` model ids.
- **Modal** — open-source models you serve yourself on Modal's $30 of free credits.

The default is `gemini`, so an existing `.env` that only sets `GEMINI_API_KEY` keeps working untouched. To switch, set `LLM_PROVIDER=<name>` plus that provider's secret(s) in `.env` — e.g. OpenRouter's free tier:

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key-here
# OPENROUTER_MODEL=openrouter/free   # the default: the Free Models Router (see below)
```

**The default `openrouter/free` is the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router):** it auto-routes each request to whatever free model is currently available and filters for the tool-calling the agent loop needs, so one congested provider can't hard-block you with a `429`. Pinning a single `:free` id instead (e.g. `qwen/qwen3-coder:free`) makes you eat that provider's rate limit alone.

**About OpenRouter's free rate limits.** `:free` models share a pool with daily caps. Per OpenRouter's current policy, adding **$10 of credits** raises the free-tier daily cap (roughly **50 → 1000 requests/day**) and unlocks BYOK — and free models still cost **$0** to run, so the $10 just sits in your balance. For an agent loop (many requests per turn) this is the difference between hitting the wall in minutes and a usable day; confirm the live figures at [openrouter.ai/settings/credits](https://openrouter.ai/settings/credits). If you'd rather not add credits, **Gemini's** per-key free tier is the more reliable free path (it's the default provider).

### Choosing the model

Each provider has its **own** model variable — set the one for your active `LLM_PROVIDER` (in `.env` or as a shell env var); the others are ignored. All three ship a sensible default, so this is optional:

| Provider (`LLM_PROVIDER`) | Model variable | Default | Pick another |
|---|---|---|---|
| `gemini` | `GEMINI_MODEL` | `gemini-2.5-flash` | any Gemini model id (e.g. `gemini-2.5-pro`) |
| `openrouter` | `OPENROUTER_MODEL` | `openrouter/free` (Free Models Router) | any id from [openrouter.ai/models](https://openrouter.ai/models) — the `:free` ones cost nothing |
| `modal` | `MODAL_ENDPOINT_MODEL` | `openai/gpt-oss-120b` | must match the model your endpoint serves — see [`MODAL_MODELS.md`](MODAL_MODELS.md) |

For example, to run Gemini's larger model:

```bash
LLM_PROVIDER=gemini          # the default, so optional
GEMINI_MODEL=gemini-2.5-pro
```

**Pick a tool-capable model.** The agent loop needs a model that supports **tool-calling + streaming**. The shipped defaults are known-good — `OPENROUTER_MODEL=openrouter/free` (which itself filters for tool-calling) and `MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b` — so for **both** OpenRouter and Modal, if you swap to a pinned model, pick a tool-capable one or the loop breaks (the model narrates instead of emitting valid tool calls).

**Run on Modal.** Set `LLM_PROVIDER=modal` + `MODAL_ENDPOINT_URL` + `MODAL_ENDPOINT_MODEL` (plus the `MODAL_PROXY_TOKEN_ID` / `MODAL_PROXY_TOKEN_SECRET` request headers, unless the endpoint was created `--unauthenticated`). See [`MODAL_MODELS.md`](MODAL_MODELS.md) for picking a model and creating the endpoint (`modal endpoint create`, `modal workspace proxy-tokens create`, and the wiring).

Every variable and its guidance lives in [`.env.example`](.env.example); the wiring decision and trade-offs are recorded in [`docs/adr/0005-multi-llm-provider-support.md`](docs/adr/0005-multi-llm-provider-support.md).

## Use

```bash
decode             # after `make install-cli` (or `uv run decode`)
```

You get an interactive REPL. Type a message and the agent streams a reply; when it wants to use a tool it **asks for approval first** (every tool, every time, in M1). Your messages echo as `you "…"` and the agent's streamed answer is labelled `Decode …`, so the conversation reads clearly.

| Action | Key |
|---|---|
| Send a message | `Enter` |
| **Steer** a running turn (redirect it now) | `Enter` while it's working |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn | `Esc` |
| Approve / deny a tool | type `y` / `n` at the prompt |
| Quit | `Ctrl-D` or `/quit` |

**Tools the agent can call** (all gated): `read` · `glob` · `grep` · `lsp` (Python code intelligence) · `write` · `edit` · `bash` · `todo_write` (a task checklist) · `web_fetch` (HTML→Markdown) · `ask_user`.

**Skills** are reusable playbooks you trigger with `/<name>` (or that the agent invokes itself). They live in `.decode/skills/<name>/SKILL.md` and ship with the repo. For example, `repo-architecture` clones a GitHub repo, explores it in read-only passes, and writes an `ARCHITECTURE.md` — problem · usage · components · interfaces · end-to-end dataflow, all backed by Mermaid diagrams:

```bash
/repo-architecture https://github.com/iusztinpaul/designing-real-world-ai-agents-workshop
```

**Resume a previous session:**

```bash
decode --resume            # the most recent session
decode --resume <session>  # a specific session id / filename
```

**Memory.** `decode` loads `AGENTS.md` (walking from the working dir upward) and the harness `.decode/MEMORY.md` into its context, and on exit appends a one-sentence summary of the session to `.decode/MEMORY.md` so the next session has a little context. Full transcripts are saved to `.decode/sessions/*.jsonl` and logs to `.decode/logs/decode.log` (all gitignored under `<cwd>/.decode/`).

## Headless runtime (`decode run`)

`decode run "<task>"` is the **unattended** counterpart to the REPL: it runs a single task to completion with no human at the keyboard and prints the agent's final answer. It builds the **same** agent as the TUI but drives it through a [Kitaru](https://docs.zenml.io/) **durable flow** — each turn is checkpointed, so an expensive multi-tool run survives a crash and resumes from where it stopped instead of re-paying for finished work. The design is recorded in [`docs/adr/0008-kitaru-durable-runtime.md`](docs/adr/0008-kitaru-durable-runtime.md).

```bash
decode run "list the python files under src and summarize what the cli module does"
```

The agent tool-loops headlessly and prints the result; the process exits `0`. Each run is recorded as a durable, inspectable Kitaru **checkpointed execution**; a *crashed* run can be resumed with its finished checkpoints replayed from cache (full crash-resume lands in a later step). A fresh re-run of the same task is a **new** execution, not a cache hit.

- **No human in the loop (this slice).** The run is autonomous, so it executes under **bypass** — every tool runs with no approval prompt (an `ask_user` becomes a no-op the agent works around). Durable human-in-the-loop approvals are a later step.
- **Setup.** It runs on Kitaru's **local stack, fully offline** — no Kitaru server and no `kitaru init` are required (a `default` stack is used). The interactive REPL is unaffected and never loads Kitaru.
- **Inspect a run.** Executions are recorded on the local stack: `kitaru executions list` / `get <id>` / `logs <id>` / `replay <id>`. For a web view, `kitaru login` (no args) starts Kitaru's bundled local dashboard at `http://localhost:8383`.
- **Guards.** `decode run` needs the same provider config as the REPL (e.g. `GEMINI_API_KEY`); a missing key prints one friendly line and exits non-zero. Set `RUNTIME_ENABLED=false` to disable the subcommand entirely (it then exits with a friendly line and builds no flow).
- **`sleep` is a durable timer in the durable run.** In a durable headless run (`decode run --hitl`), `sleep` becomes a Kitaru wait — the execution can pause and the process exit, then resume — instead of pinning a worker; in the TUI it stays a plain in-process `asyncio.sleep` (ADR-0008 §4).

### Local Kitaru server & dashboard (optional) — and how to fix a hang

`decode run` needs **no** server — it works against Kitaru's offline local store (see *Setup* above). Start a local server only when you want the **web dashboard** (execution timeline + per-checkpoint view) or the REST API:

```bash
kitaru login                 # start + connect to a local server → http://127.0.0.1:8383
kitaru login --port 9000     # ...or on another port → http://127.0.0.1:9000
kitaru status                # show the connection + whether the daemon is running
```

`kitaru login` with **no server argument** starts Kitaru's bundled dashboard and connects your client to it. Open **http://127.0.0.1:8383** to browse executions — and to eyeball a replay **fork** next to its original run.

**Troubleshooting a hang / `Connection refused`.** If `decode run` (or any `kitaru …` command) hangs on retries against `127.0.0.1:8383` (`Connection refused`), the local server daemon has stopped but your client is still pointed at it. Recover either way:

- **`kitaru login`** — restart the daemon at the same URL; or
- **`kitaru logout`** — disconnect and fall back to the **direct local database** (server-less, so it *can't* hang). `decode run` and `kitaru executions …` keep working; you only lose the web UI until you `kitaru login` again.

`kitaru status` shows which mode you're in (`Connection: local Kitaru server` vs `local database`) and whether the daemon is running. This only affects the interactive `kitaru` / `decode run` surface — the automated tests always use their own isolated store and are never affected by it.

### Replay & what-if (checkpoint → replay → compare)

Every `decode run` records a **checkpoint per model call and per tool call** (the default `"calls"` strategy), so you can re-run any recorded execution from any point with **one thing changed** — the model — and see what *would* have happened. `decode replay` is a thin wrapper over Kitaru's native flow replay: everything upstream of the anchor serves from the original run's cache; the anchor and everything downstream re-execute for real ([`docs/adr/0010-runtime-replay.md`](docs/adr/0010-runtime-replay.md)).

**1. Record a run.** Give it something that exercises several tools, so the record has many anchors:

```bash
decode run "List the Python files in src/decode/agent, then read each one and write agent_overview.md with a one-sentence summary of every file."
```

The answer prints on **stdout**; `exec_id: <ID>` and a paste-ready replay hint print on **stderr**. Copy the `<ID>` (or find it later with `kitaru executions list --flow run_agent_task`).

**2. Inspect the anchors.** `kitaru executions get <ID>` lists the checkpoints — one per model/tool call. The task above (glob + 4 reads + 2 writes = **7 tool calls**) records **16**:

```
decode_runtime_model_request     ← turn 1: decide to glob
glob_tool                        ← list the .py files
decode_runtime_model_request_2   ← turn 2
read_tool                        ← read file 1
decode_runtime_model_request_3
read_tool_2                      ← read file 2
… (a model request + read_tool per remaining file: read_tool_3, read_tool_4) …
decode_runtime_model_request_6
write_tool                       ← write agent_overview.md
… (write_tool_2, decode_runtime_model_request_8 = final answer) …
_capture_runtime_output          ← terminal output sink (NOT a replay anchor)
```

**3. Replay with a model swap.** Anchor **before** a model call so the swap actually bites:

```bash
# swap the model for the whole run (anchor at the first model call):
decode replay <ID> --from decode_runtime_model_request --model gemini-2.5-pro

# …or keep the early tool work from cache and re-execute from the 5th model call onward:
decode replay <ID> --from decode_runtime_model_request_5 --model gemini-2.5-pro
```

The (possibly changed) answer prints on **stdout**; the new **Fork** `exec_id` + a compare hint print on **stderr**.

**4. Compare the fork against the original** — cost, latency, per-checkpoint outputs, the final decision:

```bash
kitaru executions get <FORK_ID>
kitaru executions get <ID>
```

For a side-by-side web view, `kitaru login` and open the dashboard.

**Rules of the road:**

- `--from` is **required** — Kitaru has no default anchor. Omitting it prints one friendly line, not a traceback.
- Anchor at a `decode_runtime_model_request*` (or a tool checkpoint), **not** `_capture_runtime_output`: the sink is downstream of the model, so a model swap there changes nothing.
- **A trustworthy what-if is three runs.** Do a **baseline rerun** with no `--model` first (`decode replay <ID> --from <cp>`) to prove replay reproduces the original, then diff your fork against *that*, not the raw original.
- On the local stack, the fork's **stdout** may echo the cached baseline text for the anchored leg — the real delta is in the per-checkpoint outputs / cost / latency you compare in step 4.
- `decode replay` is **bypass-only**; a `decode run --hitl` execution is refused with a pointer to `kitaru executions replay` (a HITL replay re-asks every wait).
- Want cheap, coarse records instead? `RUNTIME_CHECKPOINT_STRATEGY=turn decode run "…"` records **one** checkpoint for the whole run — replayable only as a whole.

### Credentials proxy (keep the model key out of the flow payload)

By default a headless run reads the model key from `.env` (e.g. `GEMINI_API_KEY`) exactly like the REPL. For a **deployed** flow you don't want the raw key serialized into the execution's arguments — so `decode` can instead resolve the key from a **Kitaru secret** at model construction, leaving only the secret *name* in the flow. The design is in [`docs/adr/0008-kitaru-durable-runtime.md`](docs/adr/0008-kitaru-durable-runtime.md) §5; it is **opt-in** and off by default.

Create the secret once (the raw key then lives only in Kitaru, never in the flow payload):

```bash
kitaru secrets set decode-llm-creds --private --GEMINI_API_KEY=…   # CLI; OPENROUTER_API_KEY for openrouter
```

```python
from kitaru import create_secret  # Python equivalent
create_secret("decode-llm-creds", {"GEMINI_API_KEY": "…"}, private=True)
```

Then turn the proxy on in `.env`:

- `RUNTIME_CREDENTIALS_PROXY_ENABLED=true` — flow-mode model construction resolves the provider key from a Kitaru secret instead of the `SecretStr` in settings. **Default `false`** (and it only ever applies to `decode run`; the interactive REPL is untouched).
- `RUNTIME_SECRET_NAME` (default `decode-llm-creds`) — the Kitaru secret name the key is read from. The secret's key must be the provider's env-var name (`GEMINI_API_KEY` / `OPENROUTER_API_KEY`).

With it on, a `decode run` flow constructs the model with the key fetched from the secret; the execution's serialized arguments carry only the task and the secret name. The settings key is no longer required (or consulted) for that provider — so a leftover `GEMINI_API_KEY` in `.env` is harmless. A missing secret (or one without the provider key) is caught by a **proxy-aware pre-flight** before any flow is built: `decode run` prints one friendly line naming the fix (`kitaru secrets set <name> --GEMINI_API_KEY=…`) and exits non-zero — never a traceback, and never a silent fallback to the settings key. (Modal's dual proxy-token auth is a separate sandbox-header surface and is not routed through this proxy.)

### Secret-store config source (centralize the whole config in one Kitaru secret)

The credentials proxy above resolves just the *model key*. You can go one step further and keep the **whole** `decode run` configuration — provider, model, every key, the compaction/LSP tuning — in the **same** Kitaru secret, so an operator manages one place instead of an `.env`. Because `decode`'s settings already map every `.env.example` variable to a field, this needs no per-variable wiring. Set the values on the secret named by `RUNTIME_SECRET_NAME` (default `decode-llm-creds`), keyed by their `.env.example` names:

```bash
kitaru secrets set decode-llm-creds --private \
  --LLM_PROVIDER=gemini --GEMINI_MODEL=gemini-2.5-flash --GEMINI_API_KEY=…
```

Then turn the source on in `.env`:

- `RUNTIME_SECRET_STORE_CONFIG=true` — a `decode run` flow hydrates its `Settings` from that secret. **Default `false`**, and **headless-only**: bare `decode` (the REPL) never reads the secret and never imports Kitaru.

Two rules make it safe:

- **The real process env wins.** Precedence is `env > Kitaru secret > .env > defaults`, so anything you actually export still overrides the secret (handy for a one-off `GEMINI_MODEL=… decode run …`); the secret overrides `.env` and the built-in defaults.
- **Values land in `Settings`, never `os.environ`.** The hydrated config lives only in the in-process `Settings` object — it is **never** written to the process or worker environment, so a model-chosen `bash` command can never read a Kitaru-sourced secret out of its env. The singleton is restored when the flow exits, so a later in-process run is unaffected.

With the source on, `decode run` runs a **secret-store pre-flight** before building the flow: it hydrates `Settings` from the secret up front and validates the result, so the provider *key* can live **only in the secret** (no `.env` entry and no credentials-proxy flag needed) and still satisfy the startup config guard. A missing secret, or a stored value that fails validation (e.g. a bogus `LLM_PROVIDER`), exits with one friendly line naming the fix (`kitaru secrets set <name> …`) and a non-zero code — never a traceback from inside the flow. (You can still keep the key in `.env` and let the secret carry only the model/tuning if you prefer.)

This **secret-store config source** is distinct from the future **credential proxy** (mitmproxy header injection for a sandboxed worker's *tool* credentials), which is deferred to the sandbox milestone. With both this and the credentials proxy on, they compose: the model key resolves through the proxy and the rest of the surface hydrates from the same secret — a coherent run with no raw key in the flow payload.

## Context compaction

A long conversation grows toward the model's context window. `decode` keeps it in budget with a **cheapest-first cascade** that runs automatically at the end of each turn — measured against how full the window is — plus a manual override. The wiring and trade-offs are recorded in [`docs/adr/0006-conversation-compaction.md`](docs/adr/0006-conversation-compaction.md).

| Tier | Fires when | Working looks like |
|---|---|---|
| **Microcompaction** (no LLM, in-memory) | input usage reaches **~60%** of the window | a `Decode - microcompacted context (elided N old tool output(s), …)` line; old tool-output bodies are blanked for the next turn. **Not persisted** — `--resume` still replays the full transcript. |
| **Full compaction** (one LLM call) | input usage reaches **~80%** of the window, **or** you type **`/compact`** | a `Decode - compacted context (~N tokens → summary + M recent messages).` line; older turns collapse into a summary and recent turns stay verbatim. **Persisted** — `decode --resume` continues the *compacted* conversation. |

The footer carries a **fill gauge** — `○ ◔ ◑ ◕ ●` plus a percentage of the window used — that tracks the same window: **green** below ~60%, **yellow** ~60–80%, **red** at/above ~80%, so you watch the context approach compaction.

On exit, a second level compresses `.decode/MEMORY.md`: once it reaches its **200-line** cap, one cheap LLM call dedupes and merges the highest-signal facts instead of dropping the oldest lines (drop-oldest stays the guaranteed fallback, so the cap is always enforced).

Tune it in `.env` — every setting is optional with a safe default:

- `COMPACTION_CONTEXT_WINDOW_TOKENS` — your model's **input** window in tokens (default `1048576`, Gemini 2.5 Flash). The single source of truth that also drives the gauge.
- `COMPACTION_RESERVE_FRACTION` (default `0.20`) — full compaction fires at `window * (1 - reserve)`, i.e. **80%** full.
- `MICROCOMPACTION_RESERVE_FRACTION` (default `0.40`) — microcompaction fires earlier, at **60%** full (keep it larger than the full reserve so it fires first).
- `COMPACTION_ENABLED=false` disables the **automatic** cascade; manual `/compact` still works.

## LSP / code intelligence

Beyond the text tools (`read`, `grep`), `decode` can see your Python as a **semantic graph** by talking to a Language Server over LSP. It ships **`ty`** (Astral's type-checker, same vendor as `ruff`/`uv`) by default. The design and trade-offs are recorded in [`docs/adr/0007-lsp-integration.md`](docs/adr/0007-lsp-integration.md).

Two channels deliver it:

- **The `lsp` tool** (active) — the agent calls it on demand with one of four ops: `definition` (jump to where a symbol is defined), `references` (find every use), `hover` (type / signature / docs), and `diagnostics` (a file's problems, all severities). It's read-only, so the gate auto-allows it like `read` — no prompt.
- **Post-edit diagnostics** (passive) — after a successful `write` / `edit` of a `.py` file, that file's **errors** are appended to the tool's result as an `LSP diagnostics (ty) — fix these:` block, so the agent sees and fixes its mistakes inline. Errors only — clean files (and warnings-only files) stay silent.

It is **best-effort**: if `ty` isn't installed (it's a dev-group dependency) or the server is missing / slow, both channels degrade silently — the agent just falls back to the text tools and no turn ever breaks.

Tune it in `.env` — every setting is optional with a safe default:

- `LSP_ENABLED=false` disables the whole feature (no Language Server is ever spawned).
- `LSP_SERVER_COMMAND` / `LSP_SERVER_ARGS` — **swap the server**: the default is `ty` + `["server"]`; drop in `pylsp` (or any stdio LSP server) by overriding these (the spawn is `[LSP_SERVER_COMMAND, *LSP_SERVER_ARGS]`).
- `LSP_DIAGNOSTICS_ON_EDIT=false` turns off only the post-edit diagnostics; the `lsp` tool still works.
- `LSP_REQUEST_TIMEOUT_S` (default `10.0`) — per-request wall-clock timeout.

## Explore subagents

For a big question that spans many files — *"how does the permission gate decide?"*, *"trace how a
`bash` call reaches the sandbox"* — the agent can spawn **Explore subagents** instead of pulling the
whole codebase into its own context. It calls the model-facing **`agent` tool** with a scoped prompt;
each call runs a **read-only** child that reads the code and hands back **one compressed report**, so
the parent pays for the answer, not the raw file bytes. The design and trade-offs are recorded in
[`docs/adr/0013-explore-subagents.md`](docs/adr/0013-explore-subagents.md).

- **Read-only by construction.** A child gets only the read tools — `read` / `glob` / `grep` / `lsp`
  — never `write` / `edit` / `bash` / `web_fetch`, so it can explore but cannot change anything or
  reach the network. The `agent` tool is itself read-only, so like `read` it **auto-allows**: no
  approval prompt, for the spawn or its child.
- **Parallel fan-out.** When the model issues several `agent(...)` calls in one turn, they run
  **concurrently** — N investigations at once — so a broad question is answered in one parallel sweep
  instead of a serial crawl.
- **Silent until done.** A child's own steps don't stream to the TUI; you see the spawn as a tool
  call and then its folded report, not a running commentary. Transcripts are ephemeral, so
  `decode --resume` carries only the spawn call and the report.

Tune it in `.env` — every setting is optional with a safe default:

- `SUBAGENT_MAX_PARALLEL` (default `4`) — how many children run at once (kept modest, because fan-out
  multiplies model calls against the provider's free-tier rate limits).
- `SUBAGENT_MAX_REQUESTS` (default `25`) — each child's model-request budget, a runaway cap.
- `SUBAGENT_RESULT_MAX_BYTES` (default `16000`) — the size cap on the report a child hands back.

## Sandboxing

By default `decode` runs model-chosen `bash` commands as a **host subprocess** in your working directory, and the file tools (`read` / `write` / `edit` / `glob` / `grep`) edit that directory directly — fast, and byte-identical to every earlier milestone. Set the **Sandbox Mode** (`SANDBOX_MODE=docker` or `modal`) and `decode` instead gives the agent a **fully isolated Workspace**: its *whole* tool scope — the file tools **and** `bash` — operates inside a `git clone` of a repo you point it at, contained behind one execution seam, while decode's own artifacts (sessions, memory, logs, the permission file) stay put in your launch directory. The design is in [`docs/adr/0012-isolated-workspace.md`](docs/adr/0012-isolated-workspace.md), which supersedes the additive `bash`-only sandbox of [ADR-0011](docs/adr/0011-sandboxing-and-credential-proxy.md).

| `SANDBOX_MODE` | Where the tools run | The Workspace |
|---|---|---|
| `none` (default) | a host subprocess + direct-pathlib file tools, in your working directory | there is none — today's behavior, **zero change**; no Docker or Modal needed |
| `docker` | one session-persistent **local** container | `/workspace` is a **live bind mount** of the host `.decode/sandbox/` — the file tools and `bash` share it, always truthful (a `bash` write shows up in `read`, a `rm` in `glob`) |
| `modal` | one session-persistent **remote** `modal.Sandbox` | nothing runs on your machine; `/workspace` is **bootstrap-uploaded** once at launch, file ops go straight against the remote filesystem, and it's exported back to the host on exit / `/ship` |

Both sandbox modes are **one unified executor**: the Workspace is a `git clone` of `--repo` (or `SANDBOX_REPO`) — or an empty scratch if you give none — and each `bash`/tool exec is **fresh** (the filesystem persists across calls, but `cd` / `export` don't — chain them: `cd /workspace/app && …`). The sandbox **starts eagerly at launch** (a `Decode - starting <mode> sandbox …` line, then a `sandbox:<mode>` banner segment — so `docker ps` shows the container before any `bash` runs), and `bash` stays gated exactly as before — the Sandbox is defense-in-depth *beneath* the same approval prompt.

**Work on any repo, and get a branch back.** Point `decode` at a repo and it clones it into the Workspace at launch; when you finish it pushes your work back as a branch — with your own git credentials, and no secret ever inside the sandbox:

```bash
# clone a repo into an isolated docker Workspace and work on it:
SANDBOX_MODE=docker decode --repo git@github.com:you/project.git
#   … the agent reads, edits, and runs bash entirely inside /workspace …
/ship          # or just quit — decode pushes a `decode/<session-id>` branch back to the repo
```

- **`--repo <url-or-path>`** clones a URL or a local path (add **`--local`** for a fast `git clone --local` of a local path) at its committed `HEAD`, using your **ambient git credentials** (SSH agent / credential helper). A bad repo degrades to an empty Workspace with one friendly line, never a crash; `--repo` without a sandbox mode is a friendly config error. It also works headless: `SANDBOX_MODE=docker decode run --repo <url> "<task>"`.
- **Hand-back on exit or `/ship`.** decode commits any uncommitted model work (your model's own commits are preserved, never rewritten), points a deterministic **`decode/<session-id>`** branch at the result, and `git push`es it: `--repo <URL>` lands the branch on the **remote**, `--repo <local path>` in the **local source repo**. Every git command runs **host-side** — no credential ever enters the sandbox (the same guarantee the Credential Proxy below upholds).
- **Never lose results.** If the push can't reach the origin, the branch still exists locally in `.decode/sandbox` and decode names it so you can push it yourself. An unchanged Workspace (nothing committed, nothing dirty) is skipped. Turning a pushed branch into a PR (`gh pr create`) is planned for a later milestone.

**Startup guard.** Like the provider-key guard, a selected backend that isn't available fails with one friendly line and a non-zero exit (never a traceback), in both the REPL and the headless `decode run` / `decode replay` pre-flight:

- `SANDBOX_MODE=docker` with the Docker daemon down → `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).`
- `SANDBOX_MODE=modal` with no Modal **account** credentials → ``Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).``

Those account tokens (`modal token set`, i.e. `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) are what the Modal **Sandbox** authenticates with — distinct from the endpoint/proxy tokens `decode` uses to *call* a Modal-served model (see [`MODAL_MODELS.md`](MODAL_MODELS.md)).

Tune it in `.env` — every setting is optional with a safe default:

- `SANDBOX_MODE` (default `none`) — `none` / `docker` / `modal`.
- `SANDBOX_REPO` (default empty) — the repo (URL or local path) cloned into the Workspace at launch; the `--repo` flag overrides it and `--local` picks a fast local clone. Empty means an empty Workspace. The **hand-back needs no extra variable** — it reuses `--repo` / `SANDBOX_REPO` and your ambient git credentials.
- `SANDBOX_IMAGE` (default `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) — the **Worker** image (`docker` pulls it; `modal` maps it via `Image.from_registry`). Must include `bash`; the default is astral's `uv` variant of python-slim, so both sandboxes come **preconfigured with `uv`** and skill scripts run via `uv run` out of the box. **git** isn't in the slim base, so each backend adds it (modal bakes an `apt_install("git")` layer; docker installs it into the container at startup) — a model `git` command in the Workspace just works.
- `SANDBOX_TIMEOUT_S` (default `600.0`) — max lifetime of a **remote** (modal) Sandbox before Modal reaps it (docker's session container has no lifetime cap).
- `SANDBOX_GIT_USER_NAME` / `SANDBOX_GIT_USER_EMAIL` (default `decode` / `decode@localhost`) — the git identity preconfigured in the Workspace (`git config --global`), so a model `git commit` succeeds instead of erroring *"Please tell me who you are"*. The default matches the identity the hand-back stamps its own capture commit with. Set them to your own (e.g. `SANDBOX_GIT_USER_NAME="$(git config user.name)"`) to author the model's commits as yourself, or both empty to skip. **Note:** this is only the commit *identity* — pushing a branch / opening a PR needs *credentials* (below).
- `SANDBOX_GIT_TOKEN` (default empty) — **one** git token that lets the model `git push` its branch / `gh pr create` **from inside the sandbox** on **both** backends (results go remote→remote, no host-side export). Set it once; each backend injects it its own way — the deliberate **docker vs modal split** (ADR-0012 §10): **modal** puts it **directly** in the sandbox env (`GITHUB_TOKEN` via `modal.Secret` + a baked git credential helper), so it's readable in-sandbox; **docker** (headless) feeds it to the [Credential Proxy](#credential-proxy-a-worker-that-holds-no-secret), which **auto-engages** when this is set **non-empty** and injects the auth header **after egress**, so the worker holds no token (and git is installed into that worker so its `git push` over the injected header has a client). Because modal keeps it in the sandbox, use a **fine-grained, repo-scoped PAT** (Contents + Pull requests). Empty — unset or an explicit `SANDBOX_GIT_TOKEN=` — ⇒ inject nothing (the docker proxy stays down); the credential-free path stays the host-side hand-back above.

### Quickstart — try each mode

Four ways to launch it, one per row of the table above. Each still gates `bash` / `write` exactly like `none`; run the **check** in a second terminal to see the isolation for yourself. `modal` needs `modal token set` first.

**1 · docker, no repo** — an isolated empty scratch:

```bash
SANDBOX_MODE=docker decode
```

The banner shows `sandbox:docker` and `docker ps` lists the container **at launch** (before any `bash` runs); `ls /workspace` shows only `.decode/skills/`, and a `write` lands under the host `.decode/sandbox/`, never in your tree. Fresh-exec: `export X=1` then a second `bash` `echo $X` prints empty (the filesystem persists across calls, the shell doesn't), and a symlink escape (`ln -s /etc/passwd evil`, then `read evil`) is refused.

**2 · docker, with a repo** — clone in, branch back out:

```bash
SANDBOX_MODE=docker decode --repo /path/to/repo --local   # or --repo <git-url>
```

`/workspace` is the clone (`git -C /workspace log -1` matches its HEAD); change a file, then `/ship` (or quit) lands a `decode/<session-id>` branch in the source repo. The push ran **host-side** with no secret in the sandbox — `docker exec <id> env | grep -i token` prints nothing.

**3 · modal, no repo** — the same, but nothing runs on your machine:

```bash
SANDBOX_MODE=modal decode
```

`modal container list` shows it running, and a `bash` `uname -a` reports a remote Linux host, not your Mac. A `git clone` into `/workspace` persists across `bash` calls; the Workspace is swept back to `.decode/sandbox/` on exit.

**4 · modal, with a repo** — the clone is bootstrap-uploaded remotely, branch back:

```bash
SANDBOX_MODE=modal decode --repo /path/to/repo --local
```

The clone is uploaded to the remote `/workspace` at launch; `/ship` (or quit) sweeps it back and pushes `decode/<session-id>` host-side, exactly as docker does.

The offline half of the capstone proves all four with **no key and no infra** (the real docker/modal legs skip cleanly when unavailable): `uv run pytest tests/integration/test_sandbox_capstone.py`.

### Isolation honesty

`decode` ships **two** rungs of a longer ladder, and is deliberately clear about what each buys:

- **Docker (local)** — a filesystem + namespace boundary for **accidental** misbehavior, not a hostile-code jail (a shared kernel on Linux; on macOS, Docker Desktop's VM adds a boundary for free).
- **Modal (remote)** — the rung for genuinely **untrusted** code: nothing executes on your own machine.

gVisor / Kata are **zero-code** upgrades a Linux operator gets by setting the docker daemon's default runtime — every sandbox command inherits it, because `decode` drives the standard docker CLI; Firecracker and Wasm are non-goals. The full backend comparison is [ADR-0011's isolation table](docs/adr/0011-sandboxing-and-credential-proxy.md#isolation-backends-compared--why-docker--modal).

### Credential Proxy (a Worker that holds no secret)

A sandboxed **Worker** sometimes needs to make an authenticated tool call — but a prompt-injected agent can read anything in the Worker's environment, so no token should ever live there. The **Credential Proxy** (headless + docker only, **opt-in**) solves this the canonical way: the Worker is pointed at a `mitmproxy` container that injects the credential **after** the request has left the Worker, so the Worker holds no secret and the resolved credentials live only in the proxy container's env. Design in ADR-0011 §6.

This is the **docker** path. **Modal** can't run a co-located proxy, so it takes the simpler, less-hardened route — inject a scoped token straight into the sandbox via `SANDBOX_GIT_TOKEN` (above). That per-backend trade-off is ADR-0012 §10.

> **GitHub shortcut.** For the common *push a branch / open a PR* case you need **none** of the setup below — just set `SANDBOX_GIT_TOKEN` (above) **non-empty**. The docker proxy **auto-engages** and builds the two GitHub header rules (Bearer for `api.github.com`, Basic for `github.com`) from that one token — the same token modal direct-injects — and git is installed into the proxy-wired worker so its `git push` over the Basic rule has a client (worker still token-free). The three pieces below are the **general** path for any *other* host/header.

Set it up in three pieces:

1. **A Proxy Rule** — add a `SandboxProxyRule` to `DEFAULT_PROXY_RULES` in `src/decode/sandbox/proxy.py` (it ships **empty** = opt-in). Each rule maps hosts to header templates; a `{{ secret-name.key }}` template is resolved host-side from a Kitaru secret at flow start:

   ```python
   DEFAULT_PROXY_RULES = [
       SandboxProxyRule(
           name="github-auth",
           hosts=["api.github.com"],
           headers={"Authorization": "Bearer {{ github-token.value }}"},
       ),
   ]
   ```

2. **The Kitaru secret** the template reads (the raw token then lives only in Kitaru):

   ```bash
   kitaru secrets set github-token --private --value=<your-token>
   ```

3. **Turn it on** in `.env` and run headless in docker mode:

   ```bash
   SANDBOX_CREDENTIAL_PROXY_ENABLED=true
   # then, e.g.:
   SANDBOX_MODE=docker decode run "use python urllib to GET https://api.github.com/user and print the login"
   ```

The Worker's request is authenticated though it holds no token — confirm with `docker exec <worker-id> env | grep -i token` (nothing). Egress is **cooperative** (the Worker is *pointed* at the proxy, not forced through it), so this is not an exfiltration barrier; an internal-only default-deny network is the upgrade path.

That whole boundary — authenticated request out, empty-token env in the Worker — is exercised end-to-end (Docker required) by `uv run pytest tests/integration/test_sandbox_capstone.py -k credential_proxy`, no PAT of your own needed.

This sandbox **Credential Proxy** (a *tool* credential for the Worker) is distinct from the [credentials proxy](#credentials-proxy-keep-the-model-key-out-of-the-flow-payload) above, which keeps the *model* key out of the flow payload by hydrating `Settings` — different secret, different mechanism.

## Monitoring / Observability (Opik)

Set one variable and `decode` sends a **Trace** of every turn to [Opik](https://www.comet.com/opik) — so you can see what a turn actually did: which model and tool calls happened, with what inputs and outputs, how much latency, how many tokens, and (for priced models) what it cost. It's **monitoring, not evaluation**. The design and trade-offs are recorded in [`docs/adr/0014-opik-observability.md`](docs/adr/0014-opik-observability.md).

**Turn it on** — presence-based, like every other optional surface. Set `OPIK_API_KEY` in `.env` (or your shell):

```bash
# get a free key at comet.com, then:
OPIK_API_KEY=your-comet-key
# optional grouping (defaults shown):
# OPIK_WORKSPACE=default        # the Comet workspace
# OPIK_PROJECT_NAME=decode      # the project traces are grouped under
```

On launch the REPL prints one line — `Decode - Opik tracing on (project 'decode').` — and every turn is traced. **Leave `OPIK_API_KEY` unset** (the default) and tracing is a **silent no-op**: no line, no spans, no network — `decode` is byte-identical to a build without it.

**What you get:**

- **One Trace per REPL turn** (root **Span** `chat_turn`); the Opik UI groups a session's traces into one **Thread** keyed on the session id. A gated tool's approve/resume leg and any follow-up stay in the *same* trace, so turn latency honestly includes the time you spent at the approval prompt. (`decode --resume` mints a fresh session id, so a resumed conversation starts a **new** Thread.)
- **One Trace per `decode run`** (headless), grouped into a Thread keyed on the Kitaru execution id. The activation line goes only to the log, so a piped `decode run` prints **exactly the answer** on stdout.
- **Every LLM and tool call is a Span** with its inputs/outputs, latency, and tokens (`gen_ai.usage.*`). Opik estimates **cost** server-side for priced models (Gemini yes; open models via OpenRouter/Modal may be tokens-only).
- **Subagents ride along.** An Explore subagent's child run nests inside the parent turn's trace, so **per-child token spend is now visible** (this closes a gap left open when subagents shipped in M9).
- **Memory write-back and compaction ride along too** — they're pydantic-ai agents like the main loop, so one global instrumentation call covers them with no extra wiring; each shows up as its own small trace (or nested, when it runs inside a turn).

**Self-host Opik** instead of Comet cloud by pointing the exporter at your instance's OTLP base:

```bash
# the exporter appends /v1/traces; a trailing slash is fine. Unset = Comet cloud.
OPIK_URL_OVERRIDE=http://localhost:5173/api/v1/private/otel
```

Export is configured **from these settings** (never via global `OTEL_*` env vars), so it never disturbs any other OpenTelemetry SDK in the process. Evaluations and experiments built *on top of* these traces are a later milestone (M13).

## Develop

All verbs run at the repo root via the [`Makefile`](Makefile) (wrapping `uv`):

```bash
make test            # full test suite (unit + integration)
make unit-tests      # unit only
make lint-check      # ruff check        (lint-fix to auto-fix)
make format-check    # ruff format check (format-fix to apply)
make pre-commit      # format + lint + unit tests (the fast gate)
make ci              # what CI runs: lockfile check + format + lint + full suite
make help            # all targets
```

Tests mirror `src/` 1:1 under `tests/`; model calls use Pydantic AI's `TestModel`/`FunctionModel`, so the suite needs **no network and no API key**. Conventions and the development workflow live in [`AGENTS.md`](AGENTS.md).

## License

[Apache-2.0](LICENSE).
