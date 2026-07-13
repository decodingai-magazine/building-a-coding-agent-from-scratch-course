# decode

**decode** is a terminal **coding agent** you run in your terminal — a [Pydantic AI](https://ai.pydantic.dev) ReAct loop on a selectable LLM provider (**Gemini**, **OpenRouter**, or a model you serve on **Modal**), driving file / bash / web / LSP tools and read-only subagents behind a `prompt_toolkit` + `Rich` TUI, with an ask-before-every-tool permission gate, project memory, replayable sessions, docker/modal sandboxing, a durable headless runtime (Kitaru), and Opik tracing.

This repository is an **educational, open-source course** that builds `decode` from scratch, step by step. It is a single Python package (`decode`); each module under `src/decode/` maps to one part of the architecture, and every non-obvious design choice ships as an ADR in [`docs/adr/`](docs/adr/).

**What's built today:** the agent loop + TUI + gated tools + memory + session log ([ADR-0002](docs/adr/0002-milestone-1-vanilla-agent-architecture.md)) · permission modes & agents catalog ([ADR-0003](docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md)) · skills ([ADR-0004](docs/adr/0004-milestone-3-skills.md)) · selectable LLM providers ([ADR-0005](docs/adr/0005-multi-llm-provider-support.md)) · context compaction ([ADR-0006](docs/adr/0006-conversation-compaction.md)) · LSP code intelligence ([ADR-0007](docs/adr/0007-lsp-integration.md)) · the Kitaru durable runtime + replay ([ADR-0008](docs/adr/0008-kitaru-durable-runtime.md), [ADR-0010](docs/adr/0010-runtime-replay.md)) · sandboxed isolated workspaces ([ADR-0011](docs/adr/0011-sandboxing-and-credential-proxy.md), [ADR-0012](docs/adr/0012-isolated-workspace.md)) · Explore subagents ([ADR-0013](docs/adr/0013-explore-subagents.md)) · Opik observability ([ADR-0014](docs/adr/0014-opik-observability.md)). MCP tools and evaluations are later milestones.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — the package/runtime manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`). uv installs the pinned Python 3.12 for you.
- **An API key for one LLM provider.** By default a **Gemini API key** ([Google AI Studio](https://aistudio.google.com/apikey) — free tier); or run for free on **OpenRouter** (`:free` models) or a model you serve on **Modal** ($30 free credits). See [LLM providers](#configure--llm-providers).

## Install

```bash
git clone git@github.com:decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + wire git hooks   (or just: uv sync)
```

To type just **`decode`** from **any project directory**, put it on your PATH:

```bash
make install-cli    # uv tool install --editable .  — the command tracks your source
```

Then `cd` into any project and run it:

```bash
cd ~/my-project
decode              # start a fresh session in this directory
decode --resume     # continue the most recent session here
```

`decode` always operates on the directory you launch it from, and writes everything it produces under **`<cwd>/.decode/`** (gitignored): `sessions/*.jsonl` (replayable transcripts), `MEMORY.md` (cross-session memory), `logs/decode.log` (logs stay off the terminal).

If `decode` isn't found afterward, run `uv tool update-shell` and restart your shell. Uninstall with `make uninstall-cli`.

## Configure & LLM providers

Config comes from environment variables, including a local `.env` (loaded via pydantic-settings). Precedence: **shell env var → `.env` → built-in default**. Every variable is documented in [`.env.example`](.env.example); missing required config prints a one-line hint and exits, never a traceback.

```bash
cp .env.example .env    # then set GEMINI_API_KEY=your-key-here
```

The agent loop runs on one selectable **LLM provider** — set `LLM_PROVIDER` plus that provider's secret(s); each provider has its own model variable (the others are ignored):

| Provider (`LLM_PROVIDER`) | Model variable | Default | Notes |
|---|---|---|---|
| `gemini` (default) | `GEMINI_MODEL` | `gemini-2.5-flash` | free credits on [Google AI Studio](https://aistudio.google.com/apikey) |
| `openrouter` | `OPENROUTER_MODEL` | `openrouter/free` | the [Free Models Router](https://openrouter.ai/docs/guides/routing/routers/free-router) — auto-routes across free tool-capable models so one congested provider can't 429-block you. Adding $10 credits raises the free daily cap (~50 → ~1000 req/day); free models still cost $0. |
| `modal` | `MODAL_ENDPOINT_MODEL` | `openai/gpt-oss-120b` | serve your own model — set `MODAL_ENDPOINT_URL` (+ proxy tokens unless `--unauthenticated`). See [`MODAL_MODELS.md`](MODAL_MODELS.md) for picking a model and creating the endpoint. |

**Pick a tool-capable model.** The loop needs tool-calling + streaming; the shipped defaults are known-good. Swap to a pinned model that lacks tool support and the loop breaks (the model narrates instead of calling tools). The wiring decision is [ADR-0005](docs/adr/0005-multi-llm-provider-support.md).

## Use

```bash
decode             # after `make install-cli` (or `uv run decode`)
```

You get an interactive REPL: type a message, the agent streams a reply, and every tool use **asks for approval first**.

| Action | Key |
|---|---|
| Send a message | `Enter` |
| **Steer** a running turn (redirect it now) | `Enter` while it's working |
| **Follow-up** (queue work for when it's done) | `Alt+Enter` while it's working |
| **Abort** the current turn | `Esc` |
| Approve / deny a tool | type `y` / `n` at the prompt |
| Quit | `Ctrl-D` or `/quit` |

**Tools the agent can call:** `read` · `glob` · `grep` · `lsp` (code intelligence) · `agent` (Explore subagents) · `write` · `edit` · `bash` · `todo_write` (a task checklist) · `web_fetch` (HTML→Markdown) · `ask_user`. Read-only tools auto-allow; everything else gates.

**Skills** are reusable playbooks you trigger with `/<name>` (or that the agent invokes itself), living in `.decode/skills/<name>/SKILL.md`. Example — clone a repo, explore it, and write an `ARCHITECTURE.md` with Mermaid diagrams:

```bash
/repo-architecture https://github.com/iusztinpaul/designing-real-world-ai-agents-workshop
```

**Resume:** `decode --resume` (most recent session) or `decode --resume <session-id>`.

**Memory.** `decode` loads `AGENTS.md` (walking upward from the working dir) and `.decode/MEMORY.md` into context, and on exit appends a one-sentence session summary to `.decode/MEMORY.md` so the next session has context.

## Headless runtime (`decode run`)

`decode run "<task>"` is the unattended counterpart to the REPL: it runs one task to completion with no human at the keyboard and prints the answer on stdout (pipe-clean). It builds the **same** agent but drives it through a [Kitaru](https://docs.zenml.io/) **durable flow** — every model/tool call is checkpointed, so an expensive run survives a crash and resumes instead of re-paying for finished work ([ADR-0008](docs/adr/0008-kitaru-durable-runtime.md)).

```bash
decode run "list the python files under src and summarize what the cli module does"
```

- **Bypass by default** — every tool runs with no approval prompt. `decode run --hitl` instead pauses the whole execution on a durable Kitaru wait for `write`/`edit`/`bash`/`ask_user`; resolve from another terminal with `kitaru executions input <exec_id> --wait <name> --value 'true'`.
- **Offline local stack** — no Kitaru server or `kitaru init` needed. Inspect runs with `kitaru executions list` / `get <id>` / `logs <id>`; `kitaru login` starts the optional local web dashboard at `http://127.0.0.1:8383` (`kitaru logout` falls back to the server-less local database if the daemon hangs).
- **Guards** — the same provider-key guard as the REPL; `RUNTIME_ENABLED=false` disables the subcommand with a friendly line.

> **macOS: the local Kitaru server crashes mid-run.** A run starts fine, then floods with `RemoteDisconnected` followed by `Connection refused` on `127.0.0.1:8383`. The server *daemon* died — its log (`~/Library/Application Support/kitaru/zen_server/daemon/service.log`) ends with `objc[…]: +[NSCharacterSet initialize] may have been in progress in another thread when fork() was called … Crashing instead.` That is Apple's ObjC fork-safety abort: the daemon forks while the Apple runtime is initializing on another thread, and macOS kills the child rather than inherit a half-built runtime. Fix it either way:
>
> ```bash
> uv run kitaru logout                                          # simplest: no daemon, no crash
> OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run kitaru login   # or keep the dashboard
> ```
>
> Prefer `logout` unless you actually want the web dashboard — `decode run`, `kitaru executions`, and `kitaru secrets` all work against the server-less local database. Confirm with `kitaru info`: `Local server: registered but unavailable` means a stale registration is still pointing at the dead daemon.

### Replay & what-if

Every `decode run` records a checkpoint per model call and per tool call, so you can re-run any recorded execution from any anchor with the **model swapped** and see what would have happened ([ADR-0010](docs/adr/0010-runtime-replay.md)):

```bash
decode run "…"                                              # stderr prints exec_id + a replay hint
kitaru executions get <ID>                                  # list the checkpoint anchors
decode replay <ID> --from decode_runtime_model_request --model gemini-2.5-pro
```

Upstream of `--from` serves from the original run's cache; the anchor and downstream re-execute for real. The new fork's `exec_id` prints on stderr — compare fork vs original with `kitaru executions get`. `--from` is required; a trustworthy what-if does a **baseline rerun** first (no `--model`) and diffs the fork against that. `decode replay` is bypass-only (HITL replays re-ask every wait — use `kitaru executions replay`).

### Keeping keys out of the flow payload

Two opt-in, headless-only surfaces (both off by default; details in [`.env.example`](.env.example) and [ADR-0008 §5](docs/adr/0008-kitaru-durable-runtime.md)):

- `RUNTIME_SECRET_STORE_MODEL_KEY=true` — flow-mode model construction resolves the provider key from a Kitaru secret (`kitaru secrets set decode-llm-creds --private --GEMINI_API_KEY=…`), so the execution's serialized arguments carry only the secret *name*, never the raw key.
- `RUNTIME_SECRET_STORE_CONFIG=true` — hydrate the **whole** `decode run` config (provider, model, keys, tuning) from that same secret, keyed by `.env.example` names. Real process env still wins; values land in `Settings` only, never `os.environ`. The REPL never reads the secret and never imports Kitaru.

Both are secret-store **lookups**, not the sandbox [Credential Proxy](#credential-proxy-a-worker-that-holds-no-secret) below — different secret, different hiding place. (They shipped under the name "Credentials Proxy", retired in ADR-0008 §5 for exactly that confusion.) [`CREDENTIALS.md`](CREDENTIALS.md) tells the two apart and walks an end-to-end test of each, on and off.

## Context compaction

A long conversation grows toward the model's context window; `decode` keeps it in budget with a cheapest-first cascade that runs automatically at the end of each turn ([ADR-0006](docs/adr/0006-conversation-compaction.md)):

| Tier | Fires at | What happens |
|---|---|---|
| **Microcompaction** (no LLM, in-memory) | ~60% of the window | old tool-output bodies are blanked for the next turn; not persisted (`--resume` replays the full transcript) |
| **Full compaction** (one LLM call) | ~80%, or manual **`/compact`** | older turns collapse into a summary, recent turns stay verbatim; persisted (`--resume` continues the compacted conversation) |

The footer's fill gauge (`○ ◔ ◑ ◕ ●` + %) tracks the same window — green/yellow/red at the same thresholds. On exit, `.decode/MEMORY.md` is compressed at its 200-line cap by one cheap LLM call (drop-oldest stays the fallback). Tune with `COMPACTION_CONTEXT_WINDOW_TOKENS`, `COMPACTION_RESERVE_FRACTION`, `MICROCOMPACTION_RESERVE_FRACTION`, `COMPACTION_ENABLED` — all optional, see [`.env.example`](.env.example).

## LSP / code intelligence

`decode` can see your Python as a semantic graph by talking to a Language Server over LSP — it ships **`ty`** (Astral's type-checker) by default ([ADR-0007](docs/adr/0007-lsp-integration.md)). Two channels:

- **The `lsp` tool** — `definition` / `references` / `hover` / `diagnostics` on demand; read-only, so it auto-allows like `read`.
- **Post-edit diagnostics** — after a successful `write`/`edit` of a `.py` file, its errors are appended to the tool result as an `LSP diagnostics (ty) — fix these:` block, so the agent fixes its own mistakes inline.

Best-effort: an absent or slow server degrades silently — no turn ever breaks. Tune with `LSP_ENABLED`, `LSP_SERVER_COMMAND` / `LSP_SERVER_ARGS` (swap in `pylsp` or any stdio server), `LSP_DIAGNOSTICS_ON_EDIT`, `LSP_REQUEST_TIMEOUT_S`.

## Explore subagents

For a question that spans many files, the agent can spawn **Explore subagents** instead of pulling the whole codebase into its own context: each `agent(prompt)` call runs a **read-only** child (`read`/`glob`/`grep`/`lsp` only — never `write`/`edit`/`bash`/`web_fetch`) that hands back one compressed report ([ADR-0013](docs/adr/0013-explore-subagents.md)). The `agent` tool is itself read-only, so it auto-allows; several calls in one turn **fan out in parallel**; children are silent-until-done and their transcripts ephemeral (`--resume` keeps only the spawn + report). Tune with `SUBAGENT_MAX_PARALLEL` (default 4), `SUBAGENT_MAX_REQUESTS` (25), `SUBAGENT_RESULT_MAX_BYTES` (16000).

## Sandboxing

By default (`SANDBOX_MODE=none`) `bash` runs as a host subprocess and the file tools edit your working directory directly. Set a **Sandbox Mode** and the agent's *whole* tool scope — file tools **and** `bash` — moves into a fully **isolated Workspace**, while decode's own artifacts (sessions, memory, logs, permission file) stay in your launch directory ([ADR-0012](docs/adr/0012-isolated-workspace.md)):

| `SANDBOX_MODE` | Where the tools run | The Workspace |
|---|---|---|
| `none` (default) | host subprocess + direct file tools | none — zero change, no Docker/Modal needed |
| `docker` | one session-persistent **local** container | `/workspace` is a **live bind mount** of the host `.decode/sandbox/` |
| `modal` | one session-persistent **remote** `modal.Sandbox` | nothing runs on your machine; `/workspace` is bootstrap-uploaded at launch and exported back on exit / `/ship` |

Both modes are one unified executor with **fresh-exec** semantics: the filesystem persists across calls, but `cd`/`export` don't (chain them: `cd /workspace/app && …`). The sandbox starts eagerly at launch (a `sandbox:<mode>` banner segment), and `bash` stays gated exactly as before — the sandbox is defense-in-depth *beneath* the approval prompt.

**Work on any repo, and get a branch back:**

```bash
SANDBOX_MODE=docker decode --repo git@github.com:you/project.git
#   … the agent reads, edits, and runs bash entirely inside /workspace …
/ship          # or just quit — decode pushes a `decode/<session-id>` branch back to the repo
```

- **`--repo <url-or-path>`** (or `SANDBOX_REPO`; add `--local` for a fast local clone) clones at committed `HEAD` using your ambient git credentials. A bad repo degrades to an empty Workspace with one friendly line; `--repo` without a sandbox mode is a friendly config error. Works headless too: `SANDBOX_MODE=docker decode run --repo <url> "<task>"`.
- **Hand-back on exit or `/ship`** — decode commits any uncommitted model work (model commits are preserved, never rewritten), points a `decode/<session-id>` branch at the result, and pushes it. Every git command runs **host-side** — no credential ever enters the sandbox. A failed push still leaves the local branch in `.decode/sandbox` and names it; an unchanged Workspace is skipped.
- **Startup guards** — a selected backend that isn't available fails with one friendly line (Docker daemon down, or missing `modal token set` credentials), in the REPL and the headless pre-flight alike.
- **Isolation honesty** — docker is a boundary for *accidental* misbehavior (shared kernel on Linux; Docker Desktop's VM adds one on macOS); **modal** is the rung for genuinely untrusted code (nothing executes on your machine). gVisor/Kata are zero-code daemon-config upgrades; see [ADR-0011's isolation table](docs/adr/0011-sandboxing-and-credential-proxy.md#isolation-backends-compared--why-docker--modal).

Tunables (all optional, documented in [`.env.example`](.env.example)): `SANDBOX_IMAGE` (default `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — python + uv preinstalled; each backend adds git), `SANDBOX_TIMEOUT_S` (modal lifetime), `SANDBOX_GIT_USER_NAME`/`_EMAIL` (the in-Workspace commit identity), `SANDBOX_GIT_TOKEN` (below).

### Credential Proxy (a Worker that holds no secret)

A sandboxed Worker sometimes needs an authenticated tool call, but a prompt-injected agent can read anything in the Worker's env — so no token should live there. Opt-in, headless + docker only ([ADR-0011 §6](docs/adr/0011-sandboxing-and-credential-proxy.md)): the Worker is pointed at a `mitmproxy` container that injects the credential **after** the request leaves the Worker, so the resolved credentials live only in the proxy container's env.

- **GitHub shortcut** — for *push a branch / open a PR*, just set `SANDBOX_GIT_TOKEN` non-empty. The docker proxy auto-engages and builds the two GitHub header rules from that one token; **modal** can't run a co-located proxy, so it injects the same token directly into the sandbox instead (use a fine-grained, repo-scoped PAT) — the deliberate per-backend trade-off of [ADR-0012 §10](docs/adr/0012-isolated-workspace.md).
- **Any other host** — add a `SandboxProxyRule` to `DEFAULT_PROXY_RULES` in [`src/decode/sandbox/proxy.py`](src/decode/sandbox/proxy.py) (a `{{ secret-name.key }}` header template resolved from a Kitaru secret), create the secret (`kitaru secrets set …`), and set `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`.

Confirm the Worker is token-free with `docker exec <worker-id> env | grep -i token` (prints nothing). Egress is cooperative — this is not an exfiltration barrier. The whole boundary is exercised by `uv run pytest tests/integration/test_sandbox_capstone.py -k credential_proxy` (Docker required, no PAT needed); [`CREDENTIALS.md`](CREDENTIALS.md) walks the manual end-to-end test, with the flag on and off.

## Monitoring / Observability (Opik)

Set one variable and `decode` sends a **Trace** of every turn to [Opik](https://www.comet.com/opik) — every model and tool call as a Span with inputs/outputs, latency, tokens, and (for priced models) cost ([ADR-0014](docs/adr/0014-opik-observability.md)):

```bash
OPIK_API_KEY=your-comet-key     # free at comet.com; optional: OPIK_WORKSPACE, OPIK_PROJECT_NAME
```

One Trace per REPL turn (a session's traces group into one Thread; the approve/resume leg of a gated tool stays in the same trace) and one Trace per `decode run` (Thread = the Kitaru exec id; stdout stays pipe-clean). Explore subagent children nest inside the parent turn's trace with visible token spend; memory write-back and compaction ride along too. **Unset** (the default) it's a silent no-op — no line, no spans, no network. Self-host by pointing `OPIK_URL_OVERRIDE` at your instance's OTLP base; export never touches global `OTEL_*` env vars.

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

Tests mirror `src/` 1:1 under `tests/`; model calls use Pydantic AI's `TestModel`/`FunctionModel`, so the suite needs **no network and no API key**. Conventions and the development workflow live in [`AGENTS.md`](AGENTS.md); design decisions in [`docs/adr/`](docs/adr/); canonical terms in [`docs/glossary.md`](docs/glossary.md).

## License

[Apache-2.0](LICENSE).
