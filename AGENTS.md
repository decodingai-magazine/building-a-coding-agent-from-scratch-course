# decode

**decode** is a terminal **coding agent** ("agentic harness") built from scratch, step by step, as an educational open-source course. It is a single Python package, `decode`, exposing a TUI you run in your terminal: a Pydantic-AI ReAct loop driving file/bash/web/MCP tools, with pluggable inference (Gemini / OpenRouter / Modal), local + remote sandboxing, Opik observability, and a Kitaru durability runtime. Standalone single-package Python (`cli-tool-python` shape); the TUI is a module *inside* the package (`prompt_toolkit` input + `Rich` output), not a separate service.

License: **Apache-2.0**. Depth references below name **squid scaffold specs** (shipped in the `iusztinpaul/squid` plugin, not this repo) — read them via the plugin cache.

# Key Components

Single package — one bullet for the package, then the internal module map under [Project Structure](#project-structure). Modules are built incrementally as the course progresses; only `config/`, `entities/`, and `logging.py` are foundational from day one.

- **`decode`** — [`src/decode/`](src/decode/): the whole coding agent. Python 3.12+, `cli-tool-python` shape (Click entrypoint launching the TUI). Conventions: async I/O for network/DB, sync for CPU; infrastructure imported directly (no premature interfaces); shared models in `entities/`, narrow types in `<module>/types.py`; every entrypoint calls `init_logger()` at module level before any project import. Depth: squid spec `python-backend` + `cli-tool-python`.

# Project Structure

The intended target tree. Most `src/` subpackages are created **when you reach their step** — do not pre-create empty packages. `tests/` mirrors `src/` 1:1.

```
.
├── AGENTS.md / CLAUDE.md          # this memory file (+ Claude Code import)
├── pyproject.toml                 # uv + hatchling; deps grow per step
├── Makefile                       # install / test / lint / format / pre-commit / build / ci
├── .pre-commit-config.yaml        # format + lint (commit) · unit tests (push)
├── .env.example                   # config & secrets surface
├── docs/
│   ├── adr/                       # Architecture Decision Records (Nygard)
│   └── glossary.md                # ubiquitous language
├── tasks/                         # file-based tracker — one md per task
├── tests/{unit,integration}/      # unit mirrors src/ 1:1; integration touches real infra
└── src/decode/
    ├── __init__.py
    ├── logging.py                 # init_logger() — module-level in every entrypoint
    ├── cli.py                     # Click entrypoint → launches the TUI        [bootstrap]
    ├── config/settings.py         # pydantic-settings; module-level `settings` singleton
    ├── entities/                  # shared models: Message, Conversation, ToolCall, Task…
    ├── tui/                       # input: prompt_toolkit · output: Rich (answers via SSE)
    ├── harness/                   # message Queue + Priority Gate around the loop
    ├── agent/                     # Pydantic-AI ReAct loop (LLM ⇄ Tools)
    ├── agents/                    # agents catalog (Build/Plan/Explore/Code-Reviewer) + subagents
    ├── tools/                     # file I/O, Bash, web, tasks, MCP factory, skill dispatcher, LSP, AskUser
    ├── permissions/               # allow/ask/deny · modes (default/plan/edit/bypass) · settings.json
    ├── sandbox/                   # Bash execution seam — none (host) / docker (local) / modal (remote)
    ├── services/lsp/              # LSP Service — hand-rolled stdio client; FIRST concrete services/ entry (ADR-0007)
    ├── services/                  # services interface: LLM gateway, memory, MCP servers land here later
    ├── runtime/                   # Kitaru durable flow + `decode run` (ADR-0008); HITL/creds-proxy later
    ├── context/                   # context engineering: compaction + conversation log (JSONL)
    ├── memory/                    # AGENTS.md / MEMORY.md loading
    └── observability/             # Opik tracing
```

**Scripts & entrypoints.** Operator scripts in `scripts/`; CLI entrypoint declared in `pyproject.toml` `[project.scripts]` (`decode = "decode.cli:cli"`). **Every entrypoint module calls `init_logger()` at module level before any project import.**

# Tech Stack

Single Python toolchain — `uv`, `ruff`, `pytest`. **Python 3.12+.**

| Layer | Choice | Notes |
|---|---|---|
| Package/deps | `uv` (+ `hatchling` build) | `uv.lock` committed; `uv sync` is the installer. |
| Lint/format | `ruff` | One config block in `pyproject.toml`; format + check are separate passes. |
| Test | `pytest` (+ `asyncio`, `mock`) | `tests/` mirrors `src/`; `filterwarnings=["error"]`. |
| CLI / TUI | `click` · `prompt_toolkit` · `rich` | Click wrapper is thin; logic in pure functions. |
| Agent loop | `pydantic-ai` | ReAct loop (LLM ⇄ tools). *added at its step* |
| MCP | `fastmcp` | MCP tool factory + servers. *added at its step* |
| Code intelligence | `ty` (stdio LSP server) | Python `lsp` tool + post-edit diagnostics over a hand-rolled stdio client; swappable (`pylsp`), dev-group, pre-1.0, best-effort (ADR-0007). *added at its step* |
| Inference | `google-genai` (Gemini) · OpenRouter · Modal | Behind one **LLM Gateway**; OpenRouter is OpenAI-compatible. *added per step* |
| Observability | `opik` | Tracing + eval harness. *added at its step* |
| Sandbox / serving | Docker (local) · `modal` (remote) — behind one `run` seam by `SANDBOX_MODE` | Three executors: `none` (host, default) / `docker` / `modal`; docker via the CLI (no SDK). gVisor/Kata are free daemon-config upgrades; Firecracker a non-goal (ADR-0011). |
| Durability | `kitaru[local,pydantic-ai,llm]` | Durable headless flow (`decode run`) wrapping `build_agent()` via the `KitaruAgent` PydanticAI adapter — checkpoints + replay, local stack, offline (ADR-0008). Needs pydantic-ai 1.x: depend on `pydantic-ai-slim[google,openai]`, not the meta package (ADR-0009). HITL / creds-proxy / scheduling are later steps. |
| Datastore | SQLite | Conversation log is JSONL today; compaction landed on it (ADR-0006). SQLite remains a deferred durable-store option. |

Per-step libraries are `uv add`-ed when you reach them (see the commented block in `pyproject.toml`) — the initial install stays light.

## Access Documentation

Use the `context7` MCP server (when connected) to look up authoritative usage for any tech-stack item or external service above; fall back to web search otherwise.

**Reference docs (`llms.txt` — fetch on demand).** Each link below is an *index* of doc pages. Fetch the index first, then fetch only the specific page(s) you need. Do **not** pull whole `llms-full.txt` files into context unless a task truly requires the full reference.

- **Pydantic AI:** https://pydantic.dev/docs/ai/llms.txt — append `.md` to any doc page for raw markdown (e.g. `.../agents/index.md`).
- **Modal:** https://modal.com/llms.txt — full reference at https://modal.com/llms-full.txt (large; only if needed).
- **OpenRouter:** https://openrouter.ai/docs/llms.txt
- **Opik:** https://www.comet.com/docs/opik/llms.txt — also append `/llms.txt` to any section URL for a scoped index.
- **Kitaru (by ZenML):** https://docs.zenml.io/llms.txt — full reference at https://docs.zenml.io/llms-full.txt.

## Running commands

All core verbs run at the repo root via the [`Makefile`](Makefile), wrapping `uv`:

| Verb | What it does |
|---|---|
| `make install` | `uv sync` + install git hooks. |
| `make test` | Full suite (`uv run pytest`). `make unit-tests` / `make integration-tests` for subsets. |
| `make lint-check` / `make lint-fix` · `make format-check` / `make format-fix` | `ruff check` / `ruff format` (assert vs write). |
| `make pre-commit` | `format-check + lint-check + unit-tests` (the fast gate). |
| `make build` | `uv build` → wheel + sdist. |
| `make ci` | What CI runs: `uv lock --check` + format-check + lint-check + test. |
| `make help` | Curated target list. |

Commands not wrapped by `make` — use the runner directly: `uv run <cmd>`, `uvx <one-shot-tool>`.

**Manual QA order:** `format-fix → lint-fix → format-check → lint-check → pre-commit → unit-tests`.

**Dependencies & env vars.** Add runtime deps with `uv add <pkg>`; dev tools with `uv add --group dev <pkg>` (PEP 735 — never `[project.optional-dependencies]`). New env vars → `.env.example` + `config/settings.py`; never read `os.environ` deep in call sites.

## Infrastructure & external services

Access infra **CLI-only** (no web UIs) so runs are reproducible and the orchestrator can spot-check by re-running commands.

- **Git / GitHub:** `git`; `gh` for PRs, issues, Actions logs.

For each external-service slug below (wrapped in `<!-- stack:* -->` for find-and-delete), the one-liner + its CLI. Grep `<!-- stack:` to locate or remove one.

- **Gemini** — primary LLM API via the `google-genai` SDK; `GEMINI_API_KEY` (no dedicated CLI).
- **OpenRouter** — OpenAI-compatible inference backend; `openrouter` CLI.
- **Modal** — remote sandbox + open-model serving/inference backend; `modal run` / `modal deploy` / `modal token set`.
- **Opik** — LLM tracing + eval harness; `opik` CLI.
- **Kitaru** — runtime (durability, HITL); `kitaru` CLI. More within the `kitaru` skills and docs.

- **Project MCP servers:** *AGENT: fill in any MCP server this project's code talks to and the config it needs.*

# Key Principles You Will Respect All Over Your Work

- Always prioritize removing instructions over adding more.
- Whenever you add a new rule to memory (e.g. `AGENTS.md`), support it with a concise explanation plus good and bad examples. Good: "a 200-token chunk size", "sub-100ms latency". Bad: "a powerful architecture", "a robust pipeline".
- **Build it step by step.** This is a teaching codebase — favour the simplest thing that works and is readable over the clever or the speculative. One concept per step; no abstraction without a second concrete caller.
- **Infrastructure is imported, not abstracted.** Call `modal` / `opik` / `pydantic-ai` / `sqlite3` directly. Introduce an interface only when a real second implementation arrives (e.g. the local-vs-remote sandbox split, which is a genuine seam).
- **Datetimes are timezone-aware (UTC).** Reject naive `datetime` at every boundary. Type-annotate everything, including `-> None`. Library code never `print()`s — use the logger; user-facing CLI output goes through `click.echo` / `rich`.

# Developing New Features & Bug Fixes

This project uses the **squid** agent team (`iusztinpaul/squid` plugin). Direct chat for trivial edits; for one or a few groomed tasks use **`/implement-task`**; for a whole feature use **`/plan`** then **`/implement-night`** (or run **`/review`** / **`/review-ci`** standalone). Per-role rules ship with the plugin.

| Role | Responsibility |
|---|---|
| Product Architect (PA) | Grooms a feature into a Tasks Plan; authors ADRs + glossary; user-POV acceptance. |
| Software Engineer | Implements code + tests; commits each task after the Tester passes. |
| Tester | Full suite + e2e adversarial QA. |
| PR Reviewer | Diff review — correctness, simplicity, tests, standards, docs. |
| On-Call | Watches CI; diagnoses failures and hands fix tasks to the SWE. |

```
/plan  →  approved Tasks Plan (+ optional ADR) + branch
/implement-night:  /implement-task → /review → /review-ci  →  human squash-merges
```

Engineering discipline — TDD-first, branch off the active branch, run end-to-end before hand-off, regression-test-first for bugs, the format/lint/unit cadence — is enforced by the pipelines.

**Tracker:** `TRACKER_MODE: file`. One `tasks/<NNN>-<slug>.md` per task with a `status:` frontmatter field (`pending` → `in-progress` → `done`) and an append-only `## Log`. See [`tasks/README.md`](tasks/README.md).

Project-specific invariants the agents can't infer:

- **Sandbox is the one real abstraction.** `bash` execution dispatches by **Sandbox Mode** (`none` / `docker` / `modal`) behind a single `run` seam (`tools/exec.py::CommandExecutor`): three executors — the host `LocalExecutor` (`none`, the default) plus the local Docker and remote Modal sandboxes — live behind it. Don't leak Docker or Modal types upward: callers see only `ExecResult`. Firecracker is a non-goal and gVisor/Kata are zero-code daemon-config upgrades the docker CLI inherits, not shipped executors (ADR-0011 + its isolation table).
- **Secrets never reach the model or the sandbox payload.** Realized by the **Credential Proxy** (ADR-0011 §6; headless + docker only): the **Worker** that runs model-chosen commands holds no token — the resolved **Proxy Rule** credential map lives only in the mitmproxy proxy container, which injects the header *after* the request leaves the worker. Distinct from the Kitaru **Secret-Store Config**, which hydrates the harness's own `Settings` (never a worker env) — ADR-0008 §5.

# Testing E2E

The automated proof that the whole M1 stack hangs together is the capstone integration test
[`tests/integration/test_milestone1_capstone.py`](tests/integration/test_milestone1_capstone.py):
it drives a six-step conversation (read → gated write approve → gated write deny → todo_write →
ask_user → web_fetch) through the **real** `build_agent()` + `Runner` + `render_event` + session
log + memory write-back, swapping only the network boundary (`FunctionModel` for the model,
`httpx.MockTransport` for the web tool). Run it with `make integration-tests` (or the full gate,
`make ci`). It needs no API key and makes no network call.

What follows is the **manual** e2e pass against a real Gemini — exercise each surface like a user,
then try to break it (the adversarial half is the Tester's job).

**Launch.** One env var, no service to start first:

```bash
export GEMINI_API_KEY=…        # the only required secret (see .env.example); or put it in .env
uv run decode                  # the REPL: a "> " prompt + a footer hint render
```

A bare `uv run decode` with **no** `GEMINI_API_KEY` must print one friendly line on stderr —
`decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — and exit
non-zero, **not** a traceback (the task-004 startup guard in `cli.py`).

For each surface below: the thing to type, and what "working" looks like.

| Surface | Type this | Working looks like |
|---|---|---|
| **Plain chat** | `what can you do?` | the answer **streams** in token-by-token above the prompt; the prompt stays pinned at the bottom. |
| **Read (gated)** | `read pyproject.toml` | a `permission? read …` prompt appears; type `y` → a panel with the numbered file contents; type `n` → the model is told it was denied and adapts. |
| **Write (gated, approve)** | `create a file hello.txt that says hi` | `permission? write …` → `y` → the file **appears** on disk (`cat hello.txt`) and the model confirms. |
| **Write (gated, deny)** | repeat the write, answer `n` | the file is **not** created (`ls hello.txt` → absent) and the model is told the write was denied (it does not pretend it wrote it). |
| **Bash** | `run the tests with make unit-tests` | `permission? bash …` → `y` → a panel with the command's stdout/stderr (truncated past the cap); a runaway command is bounded by `bash_timeout_s`. |
| **Todo checklist** | `make a 3-step plan to add a CLI flag and track it` | a blue **tasks** panel renders the checklist (`[ ]` / `[~]` / `[x]`) and re-renders as the model updates statuses. |
| **web_fetch** | `fetch https://example.com and summarize it` | `permission? web_fetch …` → `y` → the page comes back as Markdown (HTML stripped) and the model summarizes it. |
| **ask_user** | `deploy my app` (something underspecified) | the model calls `ask_user`; an `ask: …` question renders with a `type your answer:` cue; your next typed line **is** the answer and the turn resumes with it. |
| **lsp (code intelligence)** | `where is build_agent defined?` | the model calls `lsp` (`definition`); it **auto-allows** (read-only — no prompt) and the answer cites the location (`src/decode/agent/factory.py:68:5`). Then ask it to `write a broken bad.py with a syntax error` → the approved write's result carries an appended `LSP diagnostics (ty) — fix these:` block and the model corrects it. |
| **decode run (headless)** | `decode run "list the python files"` (a separate command, not in the REPL) | the agent tool-loops **headlessly** through a Kitaru durable flow — every tool runs inline under bypass with no prompt — and prints the result, exit `0`. The run is recorded as an inspectable checkpointed execution; a fresh re-run is a **new** execution (crash-resume replay of finished checkpoints is exercised in 059 / the capstone, not here). `RUNTIME_ENABLED=false` → one friendly stderr line, non-zero exit, no flow built (ADR-0008). |
| **decode run --hitl (durable HITL)** | `decode run --hitl "create config.toml, then deploy"` in one terminal; resolve from a **second** terminal | the gating headless run: read-only tools run inline, but a `write`/`edit`/`bash` (or `ask_user`/`exit_plan_mode`) **pauses the whole execution on a durable Kitaru wait**. While it polls, run `kitaru executions list` to find the waiting `<exec_id>` and the wait `<name>`, then `kitaru executions input <exec_id> --wait <name> --value 'true'` (approve), `'false'` (deny → the run stops, the tool never ran), or `'"staging"'` (an `ask_user` answer). The run resumes from that point and prints the result. An unanswered wait eventually times out and the run pauses, printing the `<exec_id>` + the `kitaru executions input` hint, exit `0`. **The timeout differs by wait kind (a known limitation — decode does not fork the adapter):** the `ask_user`/`exit_plan_mode` answer waits decode drives itself honor `runtime_wait_timeout_s`; the native `write`/`edit`/`bash` **approval** waits the adapter raises use its fixed `600s` default and ignore the setting (ADR-0008 §3). |
| **decode run --model (model override)** | `decode run --model gemini-2.5-pro "list the python files"` | same headless bypass run as above, but the **Model Override** overrides only the active provider's model id for this run (the provider stays `LLM_PROVIDER`-selected — no cross-provider swap; ADR-0010 §2). The answer prints on **stdout** (pipe-clean); on **stderr** the durable `exec_id: <id>` + a paste-ready `replay it with a change:  decode replay <id> --model gemini-2.5-pro` hint. Presence, not correctness — a model id wrong for the provider is not validated here; it fails at the first model request. Because the model rides through as a durable **flow input**, a later `decode replay` can swap it (ADR-0010 §4). |
| **decode replay --model (what-if replay)** | keep an `exec_id` from a `decode run` above, then `decode replay <exec_id> --from <checkpoint> --model gemini-2.5-pro` | re-executes that recorded **bypass** run from `--from` with the model swapped: turns before `--from` serve from the original run's **cache**, the anchor + downstream re-execute for real, so the swap only bites downstream (ADR-0010 §5). The (possibly changed) answer prints on **stdout**; the **new Fork** `exec_id:`, the `original:` id, and a `compare them:  kitaru executions get <new>  vs  kitaru executions get <original>` diff hint print on **stderr**. `--from` is **required** — Kitaru has no default anchor, so omitting it prints one friendly line (find checkpoints with `kitaru executions get <exec_id>`; `--model` omitted replays as-is). **Bypass-only:** a **HITL** exec_id is refused with one friendly line pointing at `kitaru executions replay <id>` (a HITL replay re-asks every wait on the local stack — ADR-0010 §5,7; answer-reuse is deferred, [`tasks/future/hitl-replay-answer-reuse.md`](tasks/future/hitl-replay-answer-reuse.md)). An ambiguous/invalid `--from` or a diverged swap each print one friendly line, non-zero exit, never a traceback. *Offline-provable scope:* the bypass model-swap re-executing downstream is proven hermetically by `tests/integration/test_runtime_capstone.py::test_model_swap_replay_re_executes_downstream_turns`; the deferred HITL answer-reuse needs a deployed stack. |

**Sandboxing** (ADR-0011 — where model-chosen `bash` runs; the default `SANDBOX_MODE=none` keeps today's
host `LocalExecutor`, so every row above is byte-unchanged unless you relaunch with a mode set):

| Surface | Type this | Working looks like |
|---|---|---|
| **bash (docker Sandbox)** | launch `SANDBOX_MODE=docker decode` (needs a running Docker daemon), then `read pyproject.toml, then run 'export X=1 && cd /tmp', then a second bash 'echo $X && pwd'` | the sandbox **starts at launch** (a `Decode - starting docker sandbox …` line + a `sandbox:docker` banner segment — `docker ps` shows the container before any bash runs); each `bash` still gates (`permission? bash …` → `y`); the **persistent shell** prints `1` and `/tmp` on the second call — `cd` / `export` / installs carry across `bash` calls — and your repo is bind-mounted at `/workspace`. A timeout kills+restarts the shell (state resets: cwd back to `/workspace`, env cleared) and the reply says so. **Manual-QA peek** (while the session is live): `docker ps` shows a `python:3.12-slim` container running `sleep infinity` (no custom label — filter by `ancestor=python:3.12-slim` if noisy); `docker exec -it <id> bash` drops you inside it at `/workspace` (= your repo, one shared tree with the host file tools). **Guard:** with the daemon stopped, `decode` prints `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).` and exits non-zero (no traceback). |
| **bash (modal Sandbox)** | launch `SANDBOX_MODE=modal decode` (needs `modal token set` account creds), then `run 'ls -a /workspace && pwd'`, then `run 'git clone <small-repo> && ls'` | `/workspace` starts **empty except your project's `.decode/skills/`** (seeded at `/workspace/.decode/skills` so skill scripts run remotely) — the rest of the local tree is NOT synced (the mode-specific `bash` description tells the model: work with other code via `git clone` / `git fetch` / generate). A `git clone` / `pip install` **persists** across `bash` calls (one remote sandbox fs), but `cd` / `export` reset per call (each command is a fresh `exec`, like `none`); the `cwd` is never a remote working directory (host paths are meaningless remotely — it only locates the skills seed at sandbox creation). **Guard:** `SANDBOX_MODE=modal` with no Modal creds → ``Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).``, non-zero exit — presence only (no `modal` import, no network call). |
| **decode run in a Sandbox** | `SANDBOX_MODE=docker decode run "run 'uname -a', then tell me the kernel version"` — then `decode replay <exec_id> --from <checkpoint>` it | the headless **bypass** run executes `bash` inside the container with no prompt (bypass) and prints the answer, exit `0`. Because sandbox `bash` has real side effects, a `decode replay` **re-executes** the sandbox `bash` (it is NOT served stale from cache — the flow sets `{"cache": False}` on the `bash` checkpoint when `SANDBOX_MODE != none`, ADR-0011 §5), unlike a `none`-mode cached turn. The same backend guard runs in the headless pre-flight (daemon down / modal creds absent → one stderr line, non-zero exit, **no flow built**). |
| **Credential Proxy (headless + docker)** | add a **Proxy Rule** to `DEFAULT_PROXY_RULES` in `src/decode/sandbox/proxy.py` (the shipped example: `github-auth` → `Authorization: Bearer {{ github-token.value }}` on `api.github.com`), `kitaru secrets set github-token --private --value=<PAT>`, set `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`, then `SANDBOX_MODE=docker decode run "use python urllib to GET https://api.github.com/user and print the login"` | the **Worker** container's request succeeds **authenticated** though the worker holds **no** token — a mitmproxy **Credential Proxy** container injects the header *after* the request leaves the worker. Prove the worker is token-free: `docker exec <worker-id> env \| grep -i token` prints nothing (the resolved credential map lives only in the proxy container's env, `DEFAULT_PROXY_RULES` ships empty = opt-in). Headless + docker only; the REPL never builds it (never imports kitaru). Cooperative egress, not an exfiltration barrier. **Guard:** same docker daemon guard; a no-op unless `sandbox_mode=docker` **and** `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`. |

**Mid-turn interaction** (while a turn is streaming — ADR-0002 §4-5):

- **Steer** — start a long turn, then type a line and press plain **Enter**. It is injected at the
  next model-request boundary (never mid-stream/mid-tool); the model sees it on the next leg.
- **Follow-up** — press **Alt+Enter** instead. It is queued and drained only when the turn would
  otherwise stop, continuing the conversation as a new turn.
- **Abort** — press **Esc**. The turn stops at the next boundary, keeps the work done so far, and
  the REPL returns to idle (a `[aborted]` marker renders).

**Persistence + memory across sessions:**

- `decode --resume` (or `decode --resume <session-id>`) replays the latest (or named) session log
  from `.decode/sessions/*.jsonl` — the prior conversation is seeded and you continue it.
- On quit (`/quit` or `Ctrl-D`), one cheap Gemini call appends a dated one-line summary to
  `.decode/MEMORY.md`. Quit, `cat .decode/MEMORY.md` (a new `- YYYY-MM-DD: …` bullet), then relaunch —
  that line is injected back into the agent's instructions (it can recall what the last session did).

## Headless replay & what-if (Kitaru operator surface — documented, not wrapped)

`decode replay` wraps only the **bypass model-swap** common case, 1:1 over Kitaru's native flow-object
replay (ADR-0010 §5). The full **checkpoint → replay → diff → decide** loop lives on Kitaru's own CLI /
SDK — decode deliberately does **not** re-implement diff, cohort, or checkpoint-override machinery
(ADR-0010 §6, non-goals). Everything below is that Kitaru operator surface, verified against the
installed **kitaru 0.18** + docs.zenml.io ("Replay and Overrides", "Replay and improve"). Claims are
scoped to what actually ships — where something is a pattern or a roadmap item, it says so.

**Three runs, not two.** The trustworthy what-if is three runs, and the middle one is the point:

| Run | What it is | Role |
|---|---|---|
| **Observed** | the original recorded run | what actually happened |
| **Baseline Rerun** | `kitaru executions replay <id> --from <cp>` with **no** change | the *control* — proves replay reproduces faithfully |
| **Fork** | same `--from`, **one** input changed (e.g. `--args '{"model":…}'`) | your change |

You diff the **Fork against the Baseline Rerun**, not against the Observed run — the control isolates
your one variable. If the Baseline Rerun does not reproduce the Observed run (a nondeterministic tool,
external state, time-dependent output), the diff is untrustworthy; pin the nondeterminism first.

**CLI replay with overrides** (the surface `decode replay --model` wraps a slice of):

```bash
kitaru executions replay <exec_id> --from <cp>                                   # Baseline Rerun (control)
kitaru executions replay <exec_id> --from <cp> --args '{"model":"gemini-2.5-pro"}'   # Fork (flow-input swap)
kitaru executions replay <exec_id> --from <cp> --overrides '{"checkpoint.<name>":<value>}'  # checkpoint-output swap
```

- `--args` = **flow-input** overrides (the CLI mirror of `flow.replay(..., model=…)`; `decode replay
  --model` surfaces this common case). The **Model Override** rides here.
- `--overrides checkpoint.<name>` = a **Checkpoint Override**: substitute a recorded checkpoint's single
  output at its **direct consumers**, re-executing from those consumers forward. Keys **must** start with
  `checkpoint.` (any other prefix raises `KitaruUsageError`) and the overridden checkpoint must expose a
  single output.
- **`--overrides checkpoint.X` is the tool-output mock stand-in.** Per-tool-call `output=` / `raise_=`
  mocks (force one tool call to return a fake value or fail) are **Kitaru roadmap, not shipped** — the
  ZenML guide flags this explicitly. Today, override the tool's recorded checkpoint output instead.

**Diff = compare the two execution records.** There is **no `kitaru diff` CLI and no `.diff()` SDK
method in kitaru 0.18** (verified — do not assume one). The diff is a manual comparison of the two
records; the ZenML "Replay and improve" guide's own pattern:

```bash
kitaru executions get <fork_exec_id>        # decision, per-checkpoint outputs, cost, latency
kitaru executions get <baseline_rerun_id>   # the control to compare against
```

SDK equivalent — `KitaruClient().executions.get(fork.exec_id)` vs `.get(rerun.exec_id)`, comparing their
fields (cost/latency/decision). Because the Baseline Rerun reproduced the observed baseline, any
difference is attributable to your one change. `decode replay` prints the same hint on stderr
(`kitaru executions get <new> vs <original>`), pointing only at this confirmed surface.

**Cohort: scale the winning change across recent runs** — an **example pattern on the SDK primitives,
NOT a core Kitaru API.** The ZenML "Replay and improve" guide ships it as `run_cohort` (+ `cost` /
`latency` / `quality_judge` metric callables) in the **kitaru examples repo**
(`examples/end_to_end/pydantic_replay_fork`), and states outright it is *"not in the `kitaru` package —
copy or adapt"* (`import kitaru_recipes` is **not** an installed module — verified):

```python
from cohort import run_cohort                 # from the EXAMPLE dir, not `import kitaru`
from utils import cost, latency, quality_judge
# exec_ids: recent runs, e.g. KitaruClient().executions.list(flow="run_agent_task")
report = run_cohort(exec_ids, baseline_model="gemini-2.5-flash",
                    variant_model="gemini-2.5-pro", metrics=[cost, latency, quality_judge])
report.summary()      # per-metric baseline-vs-variant deltas + an is-it-better verdict
report.regressions()  # the metrics / decisions that got worse
```

For each recent run it reproduces the baseline, replays the variant, and scores the pair — so you decide
on a cohort, not a single lucky run.

**Waits re-ask on replay.** A replayed run **re-asks** every `wait()` — Kitaru "does not support
overriding or pre-populating wait results." That is exactly why `decode replay` is **bypass-only** (a
bypass run has no waits to re-ask) and HITL answer-reuse is deferred to a deployed stack
([`tasks/future/hitl-replay-answer-reuse.md`](tasks/future/hitl-replay-answer-reuse.md), ADR-0010 §7). A
HITL exec_id passed to `decode replay` is refused with a friendly pointer to `kitaru executions replay`.
Related honesty note: on a `decode run --hitl` **pause**, Kitaru itself prints a `Waiting for input…`
line to **stdout** (framework behavior) — the pipe-clean guarantee is about the completed **bypass**
answer, not the HITL pause path.

**An agent can drive the whole loop.** Kitaru exposes this replay surface over an **MCP server**
(`kitaru-mcp` console script), so a coding agent (Claude Code, Codex, Cursor) can pull a recent run,
propose a change, replay it against the control, compare, and decide whether to widen to a cohort — the
future automation hook (no decode work now).

# Documentation Conventions

- **ADRs** at [`docs/adr/`](docs/adr/) — `NNNN-kebab-title.md`, Nygard template (Status / Context / Decision / Consequences). Every non-obvious architectural choice (which inference backend, the sandbox abstraction, the compaction strategy, choosing Kitaru) ships with one. squid spec: `adr`.
- **Glossary** at [`docs/glossary.md`](docs/glossary.md) — one canonical name per domain concept (Harness, Agent Loop, Priority Gate, Sandbox, Compaction, Subagent…), used identically in code / docs / specs / conversation; update it in the same PR that introduces or renames a concept. squid spec: `ubiquitous-language`.
