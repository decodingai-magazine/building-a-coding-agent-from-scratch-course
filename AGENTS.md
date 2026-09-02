# decode

**decode** — terminal **coding agent** ("agentic harness") built step by step as an educational open-source course (Apache-2.0). Single Python package `decode` (Python 3.12+, `cli-tool-python` shape): Click entrypoint launches a TUI (`prompt_toolkit` input + `Rich` output — a module *inside* the package, not a separate service); Pydantic-AI ReAct loop drives file/bash/web/MCP tools; pluggable inference (Gemini / OpenRouter / Modal); local + remote sandboxing; Opik observability; Kitaru session recording + replay. Depth references name **squid scaffold specs** (in `iusztinpaul/squid` plugin, not this repo) — read via plugin cache; package depth: `python-backend` + `cli-tool-python`.

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
    ├── tui/                       # input: prompt_toolkit · output: Rich (answers stream as in-process async events)
    ├── harness/                   # message Queue + Priority Gate around the loop
    ├── agent/                     # Pydantic-AI ReAct loop (LLM ⇄ Tools)
    ├── agents/                    # agents catalog: Build/Plan/Code-Reviewer (primary) + Explore (subagent, spawned via the agent tool)
    ├── tools/                     # file I/O, Bash, web, tasks, MCP factory, skill dispatcher, LSP, AskUser
    ├── permissions/               # allow/ask/deny · modes (default/plan/edit/bypass) · settings.json
    ├── sandbox/                   # Bash execution seam — none (host) / docker (local) / modal (remote)
    ├── services/lsp/              # LSP Service — hand-rolled stdio client; FIRST concrete services/ entry (ADR-0007)
    ├── services/                  # services interface: LLM gateway, memory, MCP servers land here later
    ├── runtime/                   # plain headless `decode run` + the Recording Seam (ADR-0019)
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
| Sandbox / serving | Docker (local) · `modal` (remote) — one `run` seam by `SANDBOX_MODE` | Executors: `none` (host, default) / `docker` / `modal`; docker via CLI (no SDK). gVisor/Kata free daemon-config upgrades; Firecracker non-goal (ADR-0011). Modal also **hosts the harness**: `decode-headless` (remote `decode run` + N attempts + a `nightly` cron + a proxy-authed `webhook`) and `decode-kitaru-worker` (replays off-laptop, agent v3) — operator scripts, in-app images, no server (ADR-0020). |
| Recording / replay | `kitaru[cli,mcp,worker]` + `kitaru-pydantic-ai` | **Recording Seam** (`runtime/recording.py`) wraps `build_agent()` in `KitaruAgent` when configured — REPL and `decode run` alike record Kitaru Sessions on the managed workspace; **Replays** run from the top on an operator's Kitaru Worker (ADR-0019). The adapter caps `pydantic-ai <2.23`, so depend on `pydantic-ai-slim[google,openai]>=2.22,<2.23` — the slim package, never the meta one (ADR-0009 as amended by ADR-0019 §2); lift the pin when the adapter does. |
| Datastore | SQLite | Conversation log JSONL today; compaction on it (ADR-0006). SQLite = deferred persistent-store option. |

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
- **Modal** — remote sandbox, open-model serving/inference, and the two harness apps (`scripts/modal_headless.py`, `scripts/modal_kitaru_worker.py`); `modal run` / `modal deploy` / `modal secret create` / `modal app logs|stop` / `modal token set`. Runbook: [`running_the_code/07_infra.md`](running_the_code/07_infra.md).
- **Opik** — LLM tracing + evals; `opik` CLI.
- **Kitaru** — session recording + replay on the managed workspace; `kitaru` CLI (`status` / `session` / `replay` / `worker` / `agent` / `cohort`) + `kitaru` skills/docs.
- **Project MCP servers:** *AGENT: fill in any MCP server this project's code talks to and the config it needs.*

## Running commands

Core verbs at repo root via [`Makefile`](Makefile) wrapping `uv` — `make help` lists them (install · test · unit-tests · integration-tests · lint-check/lint-fix · format-check/format-fix · pre-commit · build · ci); anything unwrapped: `uv run <cmd>`, `uvx <one-shot-tool>`.

**Manual QA order:** `format-fix → lint-fix → format-check → lint-check → pre-commit → unit-tests`.

**Evals** (ADR-0017, never in `make ci`): `make eval-benchmark` (outcome) + `make eval-regression` (behavior gate) — need `OPIK_API_KEY` + the provider key, cost money, skip friendly without; full map in [`running_the_code/05_evals.md`](running_the_code/05_evals.md).

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
- **Workspace ships back as git branch, host-side only.** **Hand-back** (`sandbox/handback.py`): secures final Workspace onto deterministic `decode/<session-id>` **Session Branch** (auto-commits uncommitted model work; model's own commits never rewritten), `git push origin` — `--repo <URL>` → remote, `--repo <local path>` → local source repo — with **ambient host git creds**. Every git command runs host-side against `.decode/sandbox`; **hand-back puts no credential in the sandbox** — the property stands on its own, and holds whether or not `SANDBOX_GIT_TOKEN` is set (ADR-0016 §4). Local branch survives a failed push (friendly line names it + location). Triggers: **REPL exit**, idle-only **`/ship`**, **headless `decode run --repo` completion** (host-side, in the runner process — the "inside the flow" constraint died with the flow, ADR-0019 §1). Skips: no-repo / non-git / unchanged-vs-cloned-HEAD (ADR-0012 §8).
- **Secrets never reach the model's context; the sandbox gets exactly what the user hands it, no more** (ADR-0016 — supersedes ADR-0011 §6 + ADR-0012 §10; the **Credential Proxy is deleted**, clean break, no shim). Two halves, both true in *every* mode: (a) **context** — `Settings` is never serialised into a prompt, secrets are `SecretStr`, log lines carry names never values; (b) **sandbox** — the **Worker** holds only the opt-in credential the user gives it: today `SANDBOX_GIT_TOKEN` and nothing else, direct-injected as `GITHUB_TOKEN` in **BOTH** backends (docker: value-less `-e GITHUB_TOKEN`, so the token never sits in a host-visible argv; modal: `modal.Secret`) + a git credential-helper (`x-access-token:$GITHUB_TOKEN`) so `git push` over HTTPS works and `gh` authenticates off it. **A sandboxed process CAN read `$GITHUB_TOKEN`** — hand it a **scoped, revocable** PAT. The **Environment Bucket** hydrates the *harness's* `Settings`; it is never poured into a worker env. Good: `SANDBOX_GIT_TOKEN` unset → sandbox holds no credential at all, host-side Hand-back still ships the branch. Bad: a `decode-prod` bucket poured into the Worker env "so the tools just work".
- **One config surface, two injection mechanisms, selected by `DECODE_ENV`** (ADR-0015, supersedes ADR-0008 §5's secret-store knobs — deleted, no shim). `Settings` is the single source of truth: at `local` (the default) it reads `.env`; at `dev`/`staging`/`prod` it reads the derived **Environment Bucket** (Kitaru secret `decode-<env>`, no override knob) and **drops `.env` from the chain entirely** — a key missing from the bucket fails loudly (`make sync-secrets ENV=<env>`), never backfilled from a developer's file. Precedence always: process env > (`.env` | bucket) > defaults; bucket values land in `Settings` only, never `os.environ`. Hydration is process-scoped (at singleton construction), so TUI and headless behave identically. Tightened invariant (ADR-0019 §3): **no kitaru import unless recording is configured (or a worker task / remote-bucket context)** — every import site sits inside one of those three branches, so the default `DECODE_ENV=local` + unset `KITARU_AGENT_ID` run loads no kitaru module at all. Good: a missing bucket → ONE friendly startup line, exit non-zero. Bad: a `.env` `GEMINI_API_KEY` silently filling a gap in `decode-prod`.
- **Recording is an observer for a human, a precondition for a replay** (ADR-0019 §3). ONE **Recording Seam** (`runtime/recording.py`) wraps the built agent in `KitaruAgent` iff `KITARU_AGENT_ID` + the adapter's own `KITARU_API_URL` are present — REPL (turns grouped by `session_name` = decode session id) and headless `decode run` alike; decode owns no other recording machinery and no url/key settings (the adapter's client resolves those). Two failure modes, split on `KITARU_TASK_ID`: a **user-launched** run degrades to the bare agent with ONE warning line and still exits 0; a **Kitaru-Worker-spawned** run hard-fails, because an unrecorded replay is a lying experiment (which is also why a Worker Task counts as configured without an agent id — ADR-0019 Amendments §3). Good: workspace unreachable mid-demo → the paid run finishes, one stderr line says it was not recorded. Bad: a replay that quietly runs unrecorded.

# Testing E2E

Manual e2e QA playbook — what to type at each surface and what "working" looks like (chat, gated read/write, bash, todo, web_fetch, ask_user, lsp, agent subagents, docker/modal sandboxing, headless `decode run`, model override, session recording, worker replay, mid-turn steer/abort, persistence/memory, `/ship`, sandbox git token) → skill **manual-e2e-qa**. Automated M1 proof: capstone [`tests/integration/test_milestone1_capstone.py`](tests/integration/test_milestone1_capstone.py) through real `build_agent()` + `Runner` with only the network boundary swapped — run `make integration-tests` or `make ci`.

# Kitaru replay & what-if (operator surface)

Recording and replay are an **operator** surface, not a code path: nothing here runs in CI (ADR-0019, "Test surface"). The managed workspace is `https://f5ee9622-kitaru.cloudinfra.zenml.io` (`kitaru status` names it); runbook: [`running_the_code/03_runtime.md`](running_the_code/03_runtime.md).

- **Record** — export `KITARU_API_URL` + set `KITARU_AGENT_ID`, then any REPL turn or `decode run` files a **Kitaru Session** (`kitaru session get <id>` shows it node by node); freeze a set of them as a **Cohort** (`decode-bad-request-400@1`).
- **Replay** — `kitaru replay create <session-id> --agent decode@<version>` re-runs a Session **from the top** on a **Kitaru Worker** you start yourself (`kitaru worker start` — the server executes nothing). No override = **Baseline Replay**, the control; overrides (model / system prompt / prompt / params) + `--tool-policy` make it a what-if.
- **Agent Version** — the registered run spec the Worker spawns (`scripts/register_kitaru_agent.py`): `decode run` with no inline prompt, `SANDBOX_MODE=docker`, repo clone, Harness Home outside the repo. A code change = a new version.
- Investigating a bad session, authoring an evaluator → skill **kitaru-investigation**; designing/running a what-if replay or cohort experiment → skill **kitaru-replay-experiment**; pulling sessions in from another trace store → skill **kitaru-importer-builder**.

# Documentation Conventions

- **ADRs** at [`docs/adr/`](docs/adr/) — `NNNN-kebab-title.md`, Nygard template (Status / Context / Decision / Consequences). Every non-obvious architectural choice ships one. squid spec: `adr`.
- **Glossary** at [`docs/glossary.md`](docs/glossary.md) — one canonical name per domain concept (Harness, Agent Loop, Priority Gate, Sandbox, Compaction, Subagent…), identical in code / docs / specs / conversation; update in the same PR that introduces or renames one. squid spec: `ubiquitous-language`.
