# 01. Install and run Decode

Install decode, add one API key, run a first session. ~5 minutes.

## 1. Install

Needs [uv](https://docs.astral.sh/uv/) and git. macOS, Linux, or Windows via [WSL2](https://learn.microsoft.com/windows/wsl/install).

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # skip if `uv --version` works
git clone https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course.git
cd building-a-coding-agent-from-scratch-course
make install        # uv sync + git hooks
make install-cli    # `decode` on your PATH, tracking this checkout
```

> ✅ `decode --version` prints a version.

## 2. Add a key

```bash
cp .env.example .env
```

Then set **one** provider in `.env`:

```bash
# Gemini — default provider, free key at https://aistudio.google.com/apikey
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key

# or OpenRouter — free router across tool-capable models, key at https://openrouter.ai
# LLM_PROVIDER=openrouter
# OPENROUTER_API_KEY=sk-or-...
```

Both free tiers rate-limit (Gemini per minute, OpenRouter ~50 requests/day until you add $10). The course default is your own model on Modal: next tutorial.

Optional, 30 seconds: tracing. Key from [comet.com](https://www.comet.com/signup?utm_source=workshop&utm_medium=partner&utm_campaign=paul&utm_content=coding_agent_course) → Settings → API Keys:

```bash
OPIK_API_KEY=your-key
```

Every turn then lands in Opik as a trace, one span per model/tool call.

![Opik Trace](../assets/opik_trace.gif)

## 3. Run

```bash
decode
```

![Decode REPL](../assets/decode_tui_plain.png)

| Action                | Key                        |
| --------------------- | -------------------------- |
| Send                  | `Enter`                    |
| Steer a running turn  | `Enter` while it works     |
| Queue a follow-up     | `Alt+Enter` while it works |
| Abort the turn        | `Esc`                      |
| Approve / deny a tool | `y` / `n`                  |
| Quit                  | `Ctrl-D` or `/quit`        |

Everything decode writes lands under `<cwd>/.decode/` (gitignored): `sessions/*.jsonl`, `MEMORY.md`, `logs/decode.log`.

> ✅ Type `what files are in this directory?`. The agent asks to run a read tool, streams a list, and `.decode/sessions/` holds a `.jsonl`.

```bash
decode --resume               # most recent session in this directory
decode --resume <session-id>  # id = the .jsonl filename stem
decode sessions               # list all sessions from most recent to least
```

Memory: `AGENTS.md` is read at startup; `.decode/MEMORY.md` too, and decode appends a one-line summary on exit.

## 4. Try a skill

Demo skills live in `.decode/skills/`, so launch from the repo root:

```bash
decode
/demo-1-terminal-arcade    # the agent builds a playable Snake game
```

![Skills](../assets/demo-skills.png)

`/commit` and `/review-diff` ship in the package and work anywhere. Demos in your own project: `cp -r <course-repo>/.decode/skills/. ~/my-project/.decode/skills/`.

## 5. Develop

```bash
make pre-commit      # format + lint + unit tests
make test            # unit + integration
make ci              # what CI runs
make help            # all targets
```

Problems: [00_troubleshooting.md](00_troubleshooting.md).

---

**Next:** [02_modal_endpoints.md](02_modal_endpoints.md) — serve your own open-source model on Modal, no rate limits.
