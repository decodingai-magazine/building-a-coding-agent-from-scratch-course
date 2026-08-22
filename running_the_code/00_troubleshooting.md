# Troubleshooting

Every known failure in one place. Decode's startup guards check **presence only** and print one line, never a traceback — so start by matching the line you got.

Setup and first-run problems are below. Two areas keep their own tables next to the feature that produces them: **sandbox modes** in [04_sandboxing.md](04_sandboxing.md#troubleshooting) and the **headless runtime** (recording + replay) in [03_runtime.md](03_runtime.md#troubleshooting).

## Install

| What you see | What it means | Fix |
| --- | --- | --- |
| `uv: command not found` | uv installed but not on your PATH yet | restart your shell. |
| `decode: command not found` | `make install-cli` ran, but uv's tool bin isn't on your PATH | `uv tool update-shell`, restart the shell. Or skip the CLI install and use `uv run decode` from the repo. |
| `modal: command not found` | the `modal` CLI is a project dependency, not a global one | run it as `uv run modal …` from the repo (`make install` already installed it). |
| `uv sync` complains the lockfile is out of date | `pyproject.toml` and `uv.lock` drifted | `uv lock` then `make install`. `make ci` enforces this with `uv lock --check`. |

## Providers and keys

| What you see | What it means | Fix |
| --- | --- | --- |
| `Decode: set GEMINI_API_KEY in your environment or .env to start` | no key, an empty one, or an unfilled placeholder (`changeme`) — all three read as unset | put a real key in `.env`. Confirm with `grep -c '^GEMINI_API_KEY=.\+' .env`. |
| `Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY …` | provider switched, its key not set | set `OPENROUTER_API_KEY`, or drop `LLM_PROVIDER` to fall back to Gemini. |
| `Decode: LLM_PROVIDER=modal needs MODAL_ENDPOINT_URL …` | provider set to `modal` before the endpoint exists | create the endpoint ([01_install_and_usage.md step 3a](01_install_and_usage.md#3a-modal--your-own-open-source-model-recommended)), then paste its URL and model id into `.env`. |
| `Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither …` | one of `MODAL_PROXY_TOKEN_ID` / `_SECRET` is set | set both, or neither for an `--unauthenticated` endpoint. |
| `401` from your Modal endpoint | proxy tokens missing, mismatched, or not allowed on that env | re-mint with `uv run modal workspace proxy-tokens create`, then `uv run modal workspace proxy-tokens allow wk-... main`. |
| The first Modal turn hangs for a long time, then works | cold start — the GPU scaled to zero and is waking | expected on `Min 0`. Set `COMPACTION_CONTEXT_WINDOW_TOKENS` to skip the startup probe; keep-warm (`Min ≥ 1`) removes it entirely but bills idle GPU. |
| `429` / quota errors mid-turn | the Gemini free tier's per-minute or daily cap | wait out the minute, switch to `LLM_PROVIDER=openrouter`, or move to Modal — no rate limits on your own endpoint. |

Account tokens (`MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) are **not** decode settings — `.env` does nothing for them. Use `uv run modal token set …`, which writes `~/.modal.toml`.

## Running the agent

| What you see | What it means | Fix |
| --- | --- | --- |
| The agent **describes** the tool it would use instead of using it | the pinned model has no tool-calling | go back to a shipped default (`Qwen/Qwen3.6-35B-A3B-FP8`, `gemini-3.5-flash`, `openrouter/free`). Tool support is not optional for the loop. |
| `Decode: no known context window for model …; assuming …` | informational — the model isn't in the static table and the probe didn't answer | harmless; set `COMPACTION_CONTEXT_WINDOW_TOKENS` to the window your model actually has, and the probe is skipped entirely. |
| `/` shows no demo skills | decode was launched outside the course repo — skills load from the **launch** directory | relaunch from the repo, or copy `.decode/skills/` into your project. |
| `--resume` finds nothing | sessions are per-directory, under the launch cwd | relaunch from the same directory; transcripts are in `.decode/sessions/`. |
| LSP tools return nothing useful | the `ty` server is missing or slow; LSP is best-effort and degrades silently by design | it's a dev-group dependency — `make install` (not a bare `uv sync --no-dev`) installs it. |
| Tracing shows nothing in Opik | tracing is presence-based; no `OPIK_API_KEY` means a silent no-op | set `OPIK_API_KEY`, and `OPIK_WORKSPACE` if yours isn't `default`. Traces file under `decode-<DECODE_ENV>` unless `OPIK_PROJECT_NAME` overrides it. |

## Still stuck?

Logs never touch the terminal — they're at `.decode/logs/decode.log` under the directory you launched from. Turn up the detail with `LOG_LEVEL=DEBUG` in `.env`, reproduce, then read the tail.

If that doesn't explain it, [open an issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) with that tail plus your provider and model.
