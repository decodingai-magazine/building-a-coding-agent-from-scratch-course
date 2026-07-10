# decode

**decode** — terminal **coding agent** ("agentic harness") built step by step as an educational open-source course (Apache-2.0). Single Python package `decode` (Python 3.12+, `cli-tool-python` shape): Click entrypoint launches a TUI (`prompt_toolkit` input + `Rich` output — a module *inside* the package, not a separate service); Pydantic-AI ReAct loop drives file/bash/web/MCP tools; pluggable inference (Gemini / OpenRouter / Modal); local + remote sandboxing; Opik observability; Kitaru durability runtime. Depth references name **squid scaffold specs** (in `iusztinpaul/squid` plugin, not this repo) — read via plugin cache; package depth: `python-backend` + `cli-tool-python`.

# Project Structure

Target tree. Create `src/` subpackages **at their step** — never pre-create empty packages. `tests/` mirrors `src/` 1:1. Only `config/`, `entities/`, `logging.py` foundational from day one.

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
    ├── agents/                    # agents catalog: Build/Plan/Code-Reviewer (primary) + Explore (subagent, spawned via the agent tool)
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

Conventions: async I/O for network/DB, sync for CPU; shared models in `entities/`, narrow types in `<module>/types.py`; operator scripts in `scripts/`; CLI entrypoint in `pyproject.toml` `[project.scripts]` (`decode = "decode.cli:cli"`). **Every entrypoint module calls `init_logger()` at module level before any project import.**

# Tech Stack

`uv`, `ruff`, `pytest`. **Python 3.12+.** Per-step libraries `uv add`-ed when reached (commented block in `pyproject.toml`) — initial install stays light.

| Layer | Choice | Notes |
|---|---|---|
| Package/deps | `uv` (+ `hatchling` build) | `uv.lock` committed; `uv sync` installs. |
| Lint/format | `ruff` | One config block in `pyproject.toml`; format + check separate passes. |
| Test | `pytest` (+ `asyncio`, `mock`) | `filterwarnings=["error"]`. |
| CLI / TUI | `click` · `prompt_toolkit` · `rich` | Thin Click wrapper; logic in pure functions. |
| Agent loop | `pydantic-ai` | ReAct loop (LLM ⇄ tools). |
| MCP | `fastmcp` | MCP tool factory + servers. |
| Code intelligence | `ty` (stdio LSP server) | `lsp` tool + post-edit diagnostics over hand-rolled stdio client; swappable (`pylsp`), dev-group, pre-1.0, best-effort (ADR-0007). |
| Inference | `google-genai` (Gemini) · OpenRouter · Modal | One **LLM Gateway**; OpenRouter OpenAI-compatible. |
| Observability | `opik` | Tracing + eval harness. |
| Sandbox / serving | Docker (local) · `modal` (remote) — one `run` seam by `SANDBOX_MODE` | Executors: `none` (host, default) / `docker` / `modal`; docker via CLI (no SDK). gVisor/Kata free daemon-config upgrades; Firecracker non-goal (ADR-0011). |
| Durability | `kitaru[local,pydantic-ai,llm]` | Durable headless flow (`decode run`) wraps `build_agent()` via `KitaruAgent` PydanticAI adapter — checkpoints + replay, local stack, offline (ADR-0008). Needs pydantic-ai 1.x: depend on `pydantic-ai-slim[google,openai]`, not the meta package (ADR-0009). |
| Datastore | SQLite | Conversation log JSONL today; compaction on it (ADR-0006). SQLite = deferred durable-store option. |

## Docs & external services

`context7` MCP server (when connected) for authoritative tech-stack usage; else web search. `llms.txt` links = *indexes* — fetch index, then only needed pages; never whole `llms-full.txt` unless truly required.

- **Pydantic AI:** https://pydantic.dev/docs/ai/llms.txt — append `.md` to any doc page for raw markdown (e.g. `.../agents/index.md`).
- **Modal:** https://modal.com/llms.txt — full: https://modal.com/llms-full.txt (large).
- **OpenRouter:** https://openrouter.ai/docs/llms.txt
- **Opik:** https://www.comet.com/docs/opik/llms.txt — append `/llms.txt` to any section URL for scoped index.
- **Kitaru (by ZenML):** https://docs.zenml.io/llms.txt — full: https://docs.zenml.io/llms-full.txt.

Infra access **CLI-only** (no web UIs) — reproducible, spot-checkable:

- **Git / GitHub:** `git`; `gh` for PRs, issues, Actions logs.
- **Gemini** — primary LLM API via `google-genai` SDK; `GEMINI_API_KEY` (no CLI).
- **OpenRouter** — OpenAI-compatible inference; `openrouter` CLI.
- **Modal** — remote sandbox + open-model serving/inference; `modal run` / `modal deploy` / `modal token set`.
- **Opik** — LLM tracing + evals; `opik` CLI.
- **Kitaru** — runtime (durability, HITL); `kitaru` CLI + `kitaru` skills/docs.
- **Project MCP servers:** *AGENT: fill in any MCP server this project's code talks to and the config it needs.*

## Running commands

Core verbs at repo root via [`Makefile`](Makefile) wrapping `uv`; anything unwrapped: `uv run <cmd>`, `uvx <one-shot-tool>`.

| Verb | What it does |
|---|---|
| `make install` | `uv sync` + git hooks. |
| `make test` | Full suite (`uv run pytest`); subsets: `make unit-tests` / `make integration-tests`. |
| `make lint-check` / `make lint-fix` · `make format-check` / `make format-fix` | `ruff check` / `ruff format` (assert vs write). |
| `make pre-commit` | `format-check + lint-check + unit-tests` (fast gate). |
| `make build` | `uv build` → wheel + sdist. |
| `make ci` | `uv lock --check` + format-check + lint-check + test. |
| `make help` | Curated target list. |

**Manual QA order:** `format-fix → lint-fix → format-check → lint-check → pre-commit → unit-tests`.

**Deps & env vars.** Runtime: `uv add <pkg>`; dev: `uv add --group dev <pkg>` (PEP 735 — never `[project.optional-dependencies]`). New env vars → `.env.example` + `config/settings.py`; never read `os.environ` deep in call sites.

# Key Principles

- Remove instructions over adding; minimum words that achieve the goal.
- New memory rule → concise why + good/bad examples. Good: "a 200-token chunk size", "sub-100ms latency". Bad: "a powerful architecture", "a robust pipeline".
- **Step by step.** Teaching codebase — simplest readable thing beats clever or speculative. One concept per step; no abstraction without a second concrete caller.
- **Infrastructure imported, not abstracted.** Call `modal` / `opik` / `pydantic-ai` / `sqlite3` directly; interface only when a real second implementation arrives (e.g. local-vs-remote sandbox seam).
- **Datetimes timezone-aware (UTC)** — reject naive `datetime` at every boundary. Type-annotate everything, incl. `-> None`. Library code never `print()`s — logger only; user-facing CLI output via `click.echo` / `rich`.

# Developing New Features & Bug Fixes

**squid** agent team (`iusztinpaul/squid` plugin). Trivial edits: direct chat. Groomed task(s): **`/implement-task`**. Whole feature: **`/plan`** then **`/implement-night`** (standalone: **`/review`** / **`/review-ci`**). Per-role rules ship with plugin.

| Role | Responsibility |
|---|---|
| Product Architect (PA) | Grooms feature into Tasks Plan; ADRs + glossary; user-POV acceptance. |
| Software Engineer | Code + tests; commits each task after Tester passes. |
| Tester | Full suite + e2e adversarial QA. |
| PR Reviewer | Diff review — correctness, simplicity, tests, standards, docs. |
| On-Call | Watches CI; diagnoses failures, hands fix tasks to SWE. |

```
/plan  →  approved Tasks Plan (+ optional ADR) + branch
/implement-night:  /implement-task → /review → /review-ci  →  human squash-merges
```

Pipelines enforce discipline: TDD-first, branch off active branch, e2e before hand-off, regression-test-first for bugs, format/lint/unit cadence.

**Tracker:** `TRACKER_MODE: file`. One `tasks/<NNN>-<slug>.md` per task: `status:` frontmatter (`pending` → `in-progress` → `done`) + append-only `## Log`. See [`tasks/README.md`](tasks/README.md).

Invariants agents can't infer:

- **A sandbox mode = one isolated Workspace behind one seam.** `bash` + file/search tools dispatch by **Sandbox Mode** (`none` / `docker` / `modal`) through ONE `SandboxExecutor` (`sandbox/executor.py`, a `tools/exec.py::CommandExecutor`) over a thin `SandboxBackend` seam — `DockerBackend` (local) + `ModalBackend` (remote), both **fresh-exec** (each exec = new process: `cd` / `export` do NOT persist across calls; filesystem does). `/workspace` ≡ host `.decode/sandbox` (`settings.sandbox_workspace_dir`) ≡ `git clone` of `--repo`/`SANDBOX_REPO` (else empty scratch). File tools hit the sandbox filesystem *through* the seam, no host mirror: docker = pathlib on bind mount, modal = `SandboxFilesystem` API + remote `find`/`grep` (rejected mtime-delta mirror can't propagate a remote `rm` → `read`/`glob` would lie). `none` byte-identical to host (`LocalExecutor`, direct pathlib, `deps.cwd == launch cwd`). No Docker/Modal types leak upward — callers see only `ExecResult` + `FileStat`. Firecracker non-goal; gVisor/Kata zero-code daemon upgrades (ADR-0012 supersedes ADR-0011 §2,3; ADR-0011 §1,5-7 + isolation table retained).
- **Harness artifacts anchor to launch cwd; only tool scope moves into Workspace.** Sandbox mode: `deps.cwd` (file/search + `bash` scope) = Workspace; every harness artifact (`.decode/sessions`, `.decode/MEMORY.md`, logs, `.decode/skills`, `.decode/settings.json` permissions) anchors to launch cwd via `AgentDeps.harness_home` (**Harness Home**). Skills catalog/dispatcher + memory injection read `harness_home`; file/search tools + `bash` read `deps.cwd`. `none`: `harness_home == deps.cwd == launch cwd`. Good: session log header records Harness Home → `--resume` finds it. Bad: session log under `deps.cwd` — ships into user's branch, vanishes with sandbox (ADR-0012 §6).
- **Workspace ships back as git branch, host-side only.** **Hand-back** (`sandbox/handback.py`): secures final Workspace onto deterministic `decode/<session-id>` **Session Branch** (auto-commits uncommitted model work; model's own commits never rewritten), `git push origin` — `--repo <URL>` → remote, `--repo <local path>` → local source repo — with **ambient host git creds**. Every git command runs host-side against `.decode/sandbox`; **no credential ever enters the sandbox** (same invariant Credential Proxy upholds). Local branch survives a failed push (friendly line names it + location). Triggers: **REPL exit**, idle-only **`/ship`**, **headless `decode run --repo` completion** — NOT `decode run --hitl --repo` (intentionally unwired). Skips: no-repo / non-git / unchanged-vs-cloned-HEAD (ADR-0012 §8).
- **Secrets never reach the model or sandbox payload.** **Credential Proxy** (ADR-0011 §6, retained ADR-0012 §9; headless + docker only): the **Worker** running model-chosen commands holds no token — resolved **Proxy Rule** credential map lives only in the mitmproxy proxy container, injecting the header *after* the request leaves the worker. Distinct from Kitaru **Secret-Store Config** — hydrates harness `Settings`, never a worker env (ADR-0008 §5).

# Testing E2E

Automated M1 proof: capstone [`tests/integration/test_milestone1_capstone.py`](tests/integration/test_milestone1_capstone.py) — six-step conversation (read → gated write approve → gated write deny → todo_write → ask_user → web_fetch) through **real** `build_agent()` + `Runner` + `render_event` + session log + memory write-back; only network boundary swapped (`FunctionModel` for model, `httpx.MockTransport` for web tool). Run: `make integration-tests` or `make ci`. No API key, no network.

Below: **manual** e2e vs real Gemini — exercise each surface, then try to break it (adversarial half = Tester's job).

**Launch** (one env var, no service):

```bash
export GEMINI_API_KEY=…        # the only required secret (see .env.example); or put it in .env
uv run decode                  # the REPL: a "> " prompt + a footer hint render
```

No `GEMINI_API_KEY` → one friendly stderr line — `decode: set GEMINI_API_KEY in your environment or .env to start (see .env.example).` — exit non-zero, no traceback (task-004 guard in `cli.py`).

Per surface: what to type + what "working" looks like.

| Surface | Type this | Working looks like |
|---|---|---|
| **Plain chat** | `what can you do?` | answer **streams** token-by-token above prompt; prompt pinned at bottom. |
| **Read (gated)** | `read pyproject.toml` | `permission? read …` prompt; `y` → panel with numbered file contents; `n` → model told denied, adapts. |
| **Write (gated, approve)** | `create a file hello.txt that says hi` | `permission? write …` → `y` → file **appears** on disk (`cat hello.txt`), model confirms. |
| **Write (gated, deny)** | repeat write, answer `n` | file **not** created (`ls hello.txt` → absent); model told denied (doesn't pretend it wrote). |
| **Bash** | `run the tests with make unit-tests` | `permission? bash …` → `y` → panel with stdout/stderr (truncated past cap); runaway command bounded by `bash_timeout_s`. |
| **Todo checklist** | `make a 3-step plan to add a CLI flag and track it` | blue **tasks** panel renders checklist (`[ ]` / `[~]` / `[x]`), re-renders as statuses update. |
| **web_fetch** | `fetch https://example.com and summarize it` | `permission? web_fetch …` → `y` → page as Markdown (HTML stripped), model summarizes. |
| **ask_user** | `deploy my app` (underspecified) | model calls `ask_user`; `ask: …` question + `type your answer:` cue; next typed line **is** the answer, turn resumes. |
| **lsp (code intelligence)** | `where is build_agent defined?` | model calls `lsp` (`definition`); **auto-allows** (read-only, no prompt); answer cites `src/decode/agent/factory.py:68:5`. Then `write a broken bad.py with a syntax error` → approved write's result carries appended `LSP diagnostics (ty) — fix these:` block; model corrects. |
| **agent (Explore subagents)** | `explore how permissions and sandboxing each work across the repo, in parallel` | one or more `agent(prompt)` calls; each **auto-allows** (READ_ONLY — "can only cause reads"); result panel = child's **compressed report**. Child = read-only **Explore subagent** (`read`/`glob`/`grep`/`lsp` only — no `write`/`edit`/`bash`/`web_fetch`); several calls in one response **fan out in parallel** (native `asyncio.create_task`, no custom gather), capped `subagent_max_parallel` (default 4); child bounded `subagent_max_requests` (default 25), report truncated to `subagent_result_max_bytes` (default 16000). Children **silent-until-done** (no-op event sink), transcripts ephemeral — `--resume` carries only spawn call + folded report (ADR-0013). |
| **Opik tracing (observability)** | `export OPIK_API_KEY=<comet-key>` (free at comet.com), relaunch `uv run decode`, run any turn; open it in Opik UI | on launch `Decode - Opik tracing on (project 'decode').` prints **once, before banner**; unset key → **byte-identical** (zero spans, no network, no line). One REPL **turn** = one **Trace** (root **Span** `chat_turn`); session's traces = one **Thread** keyed on session id — gated tool's approve/resume leg + follow-ups ride the **same** trace (latency honestly includes gate wait). Every LLM + tool call = **Span**: inputs/outputs, latency, tokens (`gen_ai.usage.*`), cost for models Opik prices (Gemini yes; OpenRouter/Modal may be tokens-only). Memory write-back + compaction ride the one global `instrument_pydantic_ai()`; subagent `agent(...)` child **nests inside** parent turn's trace, child tokens visible (closes ADR-0013 §9). `--resume` mints fresh session id → **new** Thread. **Headless:** a `decode run` = one Trace (`decode_run` / `decode_run_hitl`), Thread = Kitaru exec_id; activation surfaces only in LOG — **stdout stays exactly the answer** (pipe-clean), stderr untouched. `--hitl` pause closes run's span, resume opens fresh one under same Thread; real provider on `checkpoint_strategy="calls"` may export some model spans as **siblings** of run root (documented ceiling — tokens ride every span). Evals/experiments = M13 (ADR-0014). |
| **decode run (headless)** | `decode run "list the python files"` (separate command, not in REPL) | agent tool-loops **headlessly** through Kitaru durable flow — every tool inline under bypass, no prompt — prints result, exit `0`. Recorded as inspectable checkpointed execution; fresh re-run = **new** execution (crash-resume replay exercised in 059 / capstone). `RUNTIME_ENABLED=false` → one friendly stderr line, non-zero exit, no flow built (ADR-0008). |
| **decode run --hitl (durable HITL)** | `decode run --hitl "create config.toml, then deploy"`; resolve from a **second** terminal | read-only tools run inline; `write`/`edit`/`bash` (or `ask_user`/`exit_plan_mode`) **pauses whole execution on durable Kitaru wait**. While it polls: `kitaru executions list` → waiting `<exec_id>` + wait `<name>`; then `kitaru executions input <exec_id> --wait <name> --value 'true'` (approve), `'false'` (deny → run stops, tool never ran), or `'"staging"'` (`ask_user` answer). Run resumes, prints result. Unanswered wait times out → run pauses, prints `<exec_id>` + `kitaru executions input` hint, exit `0`. **Timeout differs by wait kind (known limitation — decode doesn't fork adapter):** `ask_user`/`exit_plan_mode` answer waits honor `runtime_wait_timeout_s`; native `write`/`edit`/`bash` approval waits use adapter's fixed `600s`, ignore the setting (ADR-0008 §3). |
| **decode run --model (model override)** | `decode run --model gemini-2.5-pro "list the python files"` | same bypass run; **Model Override** swaps only active provider's model id for this run (provider stays `LLM_PROVIDER`-selected — no cross-provider swap; ADR-0010 §2). Answer → **stdout** (pipe-clean); **stderr** → durable `exec_id: <id>` + paste-ready `replay it with a change:  decode replay <id> --model gemini-2.5-pro` hint. Presence, not correctness — wrong model id for provider fails at first model request. Model rides as durable **flow input** → later `decode replay` can swap it (ADR-0010 §4). |
| **decode replay --model (what-if replay)** | keep an `exec_id` from a `decode run`, then `decode replay <exec_id> --from <checkpoint> --model gemini-2.5-pro` | re-executes that recorded **bypass** run from `--from` with model swapped: turns before `--from` serve from original run's **cache**, anchor + downstream re-execute — swap bites only downstream (ADR-0010 §5). Answer → **stdout**; **stderr** → new Fork `exec_id:`, `original:` id, `compare them:  kitaru executions get <new>  vs  kitaru executions get <original>` diff hint. `--from` **required** (no default anchor); omitted → one friendly line (checkpoints via `kitaru executions get <exec_id>`; `--model` omitted = replay as-is). **Bypass-only:** HITL exec_id refused with friendly line pointing at `kitaru executions replay <id>` (HITL replay re-asks every wait — ADR-0010 §5,7; answer-reuse deferred, [`tasks/future/hitl-replay-answer-reuse.md`](tasks/future/hitl-replay-answer-reuse.md)). Ambiguous/invalid `--from` or diverged swap → one friendly line, non-zero, never traceback. *Offline-provable:* `tests/integration/test_runtime_capstone.py::test_model_swap_replay_re_executes_downstream_turns`; deferred HITL answer-reuse needs deployed stack. |

**Sandboxing** (ADR-0012 — see invariants above for the seam / Workspace / fresh-exec / Harness Home model). Default `SANDBOX_MODE=none` keeps every row above byte-unchanged. In `docker` / `modal` the **whole tool scope** — file/search tools **and** `bash` — is the one Workspace; decode's artifacts stay at Harness Home. `--repo`/`SANDBOX_REPO` needs a sandbox mode; with `SANDBOX_MODE=none` → `Decode: --repo/SANDBOX_REPO clones a repo into the isolated sandbox Workspace, which only exists in a sandbox mode …`, non-zero, no traceback.

| Surface | Type this | Working looks like |
|---|---|---|
| **file tools + bash (docker Sandbox)** | launch `SANDBOX_MODE=docker decode --repo <url-or-local-path>` (needs running Docker daemon; `--local` = fast local-path clone), then `glob **/*.py, read one of them, write a NOTES.md, then run 'echo hi > side.txt && cat side.txt'` | launch: `Decode - cloning <repo> into the workspace…` (only when repo given AND Workspace still empty), then `Decode - starting docker sandbox (ghcr.io/astral-sh/uv:python3.12-bookworm-slim)…` + `sandbox:docker` banner segment — `docker ps` shows a `sleep infinity` container before any bash (clone failure degrades to empty Workspace + one friendly line). File/search tools + `bash` share the one `/workspace` tree — **live bind mount**, always truthful: bash-written file → `read`; bash `rm` → `glob`. `bash` still gates; fresh-exec (chain: `cd /workspace/app && …`); timeout kills only that `docker exec` — container + filesystem survive, reply says timed out. `lsp` + post-edit diagnostics **run** (host `ty` reads bind mount). **`git` installed into container at startup** (slim base ships none), identity preconfigured (`SANDBOX_GIT_USER_*`, default `decode`/`decode@localhost`) → model `git commit` in `/workspace` works; push/PR creds stay host-side (hand-back) or ride Credential Proxy. `web_fetch` stays **gated** (host network). Exit or `/ship` → hand-back (see `/ship` row). **QA peek:** `docker exec -it <id> bash` (filter `ancestor=ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) → `/workspace` = host `.decode/sandbox`. **Guard:** daemon stopped → `Decode: SANDBOX_MODE=docker but the Docker daemon is not reachable — start Docker and retry (see .env.example).`, non-zero, no traceback. |
| **file tools + bash (modal Sandbox)** | launch `SANDBOX_MODE=modal decode --repo <url>` (needs `modal token set` creds), then `glob **/*.py, read one, then run 'ls -a /workspace'` | after clone line: `Decode - starting modal sandbox …` **and** `Decode - uploading the workspace to the modal sandbox…` — Workspace **bootstrap-uploaded** (ONE tar, host → remote; NOT `add_local_dir`-seeded, NOT mtime-synced) → `/workspace` holds the cloned repo + seeded `.decode/skills`, not empty scratch. File tools go directly against remote `SandboxFilesystem` (`read`/`write`/`edit`); `glob`/`grep` = remote `find`/`grep` — truthful, no host mirror. `bash` gates; fresh-exec. Sandbox outliving `SANDBOX_TIMEOUT_S` recreated on next op, **re-bootstrapped from last local `.decode/sandbox` state**; reply notes in-sandbox changes since last export may be lost. `lsp` + post-edit diagnostics **best-effort-disabled** (host `ty` can't reach remote fs — ADR-0012 §7). **`git` baked into image** (`apt_install("git")` + `git config` layer for `SANDBOX_GIT_USER_*` identity, both cached) → `git commit` works out of box. `SANDBOX_GIT_TOKEN` set → token injected INTO modal sandbox (`GITHUB_TOKEN` via `modal.Secret` + credential-helper layer) → model can `git push` / open PR from inside — **one** `SANDBOX_GIT_TOKEN` serves both backends (docker feeds it to Credential Proxy, worker token-free; modal direct-injects), the docker-proxy vs modal-direct-inject trade-off (ADR-0012 §10; use scoped PAT — modal keeps it in-sandbox). `web_fetch` gated. Session end or `/ship` → `/workspace` **exported** to host `.decode/sandbox` for hand-back. **Guard:** no Modal creds → ``Decode: SANDBOX_MODE=modal but Modal credentials are missing — run `modal token set …` (see .env.example).``, non-zero, presence only (no `modal` import, no network call). |
| **decode run in a Sandbox** | `SANDBOX_MODE=docker decode run --repo <url> "run 'uname -a', then read README.md and summarize it"` — then `decode replay <exec_id> --from <checkpoint>` it | headless **bypass** run executes `bash` + file tools inside Workspace, no prompt, prints answer, exit `0`. Sandbox `bash` has real side effects → `decode replay` **re-executes** it, NOT served stale from cache (flow sets `{"cache": False}` on `bash` checkpoint when `SANDBOX_MODE != none`, ADR-0011 §5), unlike a `none`-mode cached turn. Completion → Workspace **auto-shipped** as `decode/<exec_id>` branch host-side (outcome line → **stderr**, stdout pipe-clean) — **only `decode run --repo`, NOT `decode run --hitl --repo`** (intentionally unwired). Same backend guard in headless pre-flight (daemon down / modal creds absent / `--repo` in `none` mode → one stderr line, non-zero, **no flow built**). |
| **/ship (git hand-back)** | in a `SANDBOX_MODE=docker decode --repo <url>` session, do work, type `/ship` (idle-only, like `/compact` / `/clear`) | Workspace secured onto `decode/<session-id>` branch, pushed host-side: `Decode - handed the workspace back on branch decode/<id> (pushed to origin).` (modal exports `/workspace` first; docker's mount already live). Failed push never loses work — `Decode - could not push decode/<id> to origin; the results are safe on the local branch decode/<id> in .decode/sandbox — push it yourself when ready.` Unchanged/non-git → `Decode - the workspace is unchanged from the cloned HEAD, so there is nothing to hand back.` `none` mode / no `--repo` → `Decode - no sandbox workspace to ship.` Same hand-back runs automatically on REPL exit (silent no-op on skip). |
| **Credential Proxy (headless + docker)** | add a **Proxy Rule** to `DEFAULT_PROXY_RULES` in `src/decode/sandbox/proxy.py` (shipped example: `github-auth` → `Authorization: Bearer {{ github-token.value }}` on `api.github.com`), `kitaru secrets set github-token --private --value=<PAT>`, set `SANDBOX_CREDENTIAL_PROXY_ENABLED=true`, then `SANDBOX_MODE=docker decode run "use python urllib to GET https://api.github.com/user and print the login"` | Worker's request succeeds **authenticated** though worker holds **no** token — mitmproxy container injects header *after* request leaves worker (see invariant). Prove token-free: `docker exec <worker-id> env \| grep -i token` prints nothing (`DEFAULT_PROXY_RULES` ships empty = opt-in). **GitHub shortcut (ADR-0012 §10):** plain push/PR needs no Proxy Rule or Kitaru secret — set `SANDBOX_GIT_TOKEN` **non-empty**; proxy **auto-engages**, `github_token_rules` builds the two GitHub header rules (Bearer `api.github.com`, Basic `github.com`) from that one token (same token modal direct-injects); **git installed into proxy-wired worker** so its `git push` over the Basic rule has a client (worker still token-free). `DEFAULT_PROXY_RULES` + flag = general path for other hosts. Headless + docker only; REPL never builds it (never imports kitaru). Cooperative egress, not an exfiltration barrier. **Guard:** same docker daemon guard; no-op unless `sandbox_mode=docker` **and** (`SANDBOX_CREDENTIAL_PROXY_ENABLED=true` **or** non-empty `SANDBOX_GIT_TOKEN`). |

**Mid-turn interaction** (while a turn streams — ADR-0002 §4-5):

- **Steer** — type a line + plain **Enter**: injected at next model-request boundary (never mid-stream/mid-tool).
- **Follow-up** — **Alt+Enter**: queued, drained only when turn would otherwise stop; continues as new turn.
- **Abort** — **Esc**: turn stops at next boundary, keeps work done, REPL idle (`[aborted]` marker).

**Persistence + memory across sessions:**

- `decode --resume` (or `decode --resume <session-id>`) replays latest (or named) session log from `.decode/sessions/*.jsonl`; prior conversation seeded, continue it.
- On quit (`/quit` or `Ctrl-D`), one cheap Gemini call appends a dated one-line summary (`- YYYY-MM-DD: …`) to `.decode/MEMORY.md`; relaunch injects it into agent instructions.

## Headless replay & what-if (Kitaru operator surface — documented, not wrapped)

`decode replay` wraps only the **bypass model-swap** common case, 1:1 over Kitaru's native flow-object replay (ADR-0010 §5). Full **checkpoint → replay → diff → decide** loop = Kitaru's own CLI/SDK — decode deliberately does **not** re-implement diff, cohort, or checkpoint-override machinery (ADR-0010 §6). Below: that operator surface, verified against installed **kitaru 0.18** + docs.zenml.io ("Replay and Overrides", "Replay and improve"); patterns / roadmap items flagged as such.

**Three runs, not two.** Trustworthy what-if = three runs; the middle is the point:

| Run | What it is | Role |
|---|---|---|
| **Observed** | original recorded run | what actually happened |
| **Baseline Rerun** | `kitaru executions replay <id> --from <cp>`, **no** change | *control* — proves replay reproduces faithfully |
| **Fork** | same `--from`, **one** input changed (e.g. `--args '{"model":…}'`) | your change |

Diff **Fork vs Baseline Rerun**, not vs Observed — the control isolates your one variable. Baseline Rerun ≠ Observed (nondeterministic tool, external state, time)? Diff untrustworthy; pin the nondeterminism first.

**CLI replay with overrides** (surface `decode replay --model` wraps a slice of):

```bash
kitaru executions replay <exec_id> --from <cp>                                   # Baseline Rerun (control)
kitaru executions replay <exec_id> --from <cp> --args '{"model":"gemini-2.5-pro"}'   # Fork (flow-input swap)
kitaru executions replay <exec_id> --from <cp> --overrides '{"checkpoint.<name>":<value>}'  # checkpoint-output swap
```

- `--args` = **flow-input** overrides (CLI mirror of `flow.replay(..., model=…)`; `decode replay --model` surfaces this). The **Model Override** rides here.
- `--overrides checkpoint.<name>` = **Checkpoint Override**: substitute a recorded checkpoint's single output at its **direct consumers**, re-executing from there forward. Keys **must** start with `checkpoint.` (else `KitaruUsageError`); overridden checkpoint must expose a single output.
- **`--overrides checkpoint.X` = the tool-output mock stand-in.** Per-tool-call `output=` / `raise_=` mocks (fake value / forced failure) are **Kitaru roadmap, not shipped** — ZenML guide flags this. Today: override the tool's recorded checkpoint output.

**Diff = compare the two execution records.** **No `kitaru diff` CLI, no `.diff()` SDK method in kitaru 0.18** (verified — do not assume one). Manual comparison; the ZenML guide's own pattern:

```bash
kitaru executions get <fork_exec_id>        # decision, per-checkpoint outputs, cost, latency
kitaru executions get <baseline_rerun_id>   # the control to compare against
```

SDK: `KitaruClient().executions.get(fork.exec_id)` vs `.get(rerun.exec_id)` — compare cost/latency/decision. Baseline reproduced → any difference attributable to your one change. `decode replay` prints the same stderr hint (`kitaru executions get <new> vs <original>`), pointing only at this confirmed surface.

**Cohort: scale the winning change across recent runs** — **example pattern on SDK primitives, NOT a core Kitaru API.** ZenML "Replay and improve" guide ships `run_cohort` (+ `cost` / `latency` / `quality_judge` metric callables) in the **kitaru examples repo** (`examples/end_to_end/pydantic_replay_fork`); *"not in the `kitaru` package — copy or adapt"* (`import kitaru_recipes` is **not** an installed module — verified):

```python
from cohort import run_cohort                 # from the EXAMPLE dir, not `import kitaru`
from utils import cost, latency, quality_judge
# exec_ids: recent runs, e.g. KitaruClient().executions.list(flow="run_agent_task")
report = run_cohort(exec_ids, baseline_model="gemini-2.5-flash",
                    variant_model="gemini-2.5-pro", metrics=[cost, latency, quality_judge])
report.summary()      # per-metric baseline-vs-variant deltas + an is-it-better verdict
report.regressions()  # the metrics / decisions that got worse
```

Per run: reproduce baseline, replay variant, score the pair — decide on a cohort, not one lucky run.

**Waits re-ask on replay.** A replayed run **re-asks** every `wait()` — Kitaru "does not support overriding or pre-populating wait results." Hence `decode replay` is bypass-only + HITL answer-reuse deferred (see replay row above). Honesty note: on a `decode run --hitl` **pause**, Kitaru itself prints `Waiting for input…` to **stdout** (framework behavior) — the pipe-clean guarantee covers the completed **bypass** answer only.

**A subagent run = one opaque checkpoint.** A whole `agent(...)` spawn — the child's entire nested loop — is one opaque tool call → **one** checkpoint under `"calls"`: nested child model calls are **not** replay anchors; a `decode replay --model` swap does **not** reach inside a child (child rides parent's model — `AgentDef` has no model field). Read-only child's cached summary is replay-safe → `agent` never joins the sandbox-bash cache-disable set; child token spend stays folded into that one tool call, invisible until Opik (M10) — ADR-0013 §9.

**An agent can drive the whole loop.** Kitaru exposes this replay surface over an **MCP server** (`kitaru-mcp` console script) — a coding agent (Claude Code, Codex, Cursor) can pull a recent run, propose a change, replay vs control, compare, widen to a cohort — future automation hook (no decode work now).

# Documentation Conventions

- **ADRs** at [`docs/adr/`](docs/adr/) — `NNNN-kebab-title.md`, Nygard template (Status / Context / Decision / Consequences). Every non-obvious architectural choice ships one. squid spec: `adr`.
- **Glossary** at [`docs/glossary.md`](docs/glossary.md) — one canonical name per domain concept (Harness, Agent Loop, Priority Gate, Sandbox, Compaction, Subagent…), identical in code / docs / specs / conversation; update in the same PR that introduces or renames one. squid spec: `ubiquitous-language`.
