# Troubleshooting

Startup guards check **presence only** and print one line, never a traceback — match the line you got.

Feature-specific tables live next to the feature: **Modal endpoints** [02_modal_endpoints.md](02_modal_endpoints.md#8-troubleshooting) · **sandbox modes** [03_sandboxing.md](03_sandboxing.md#6-troubleshooting) · **headless harness on Modal** [04_deploy.md](04_deploy.md#8-troubleshooting) · **recording & replays** [06_evals_replays.md](06_evals_replays.md#7-troubleshooting).

## Install

| What you see | What it means | Fix |
| --- | --- | --- |
| `uv: command not found` | not on PATH yet | restart your shell. |
| `decode: command not found` | uv's tool bin not on PATH | `uv tool update-shell`, restart. Or use `uv run decode` from the repo. |
| `modal: command not found` | `modal` is a project dependency, not global | `uv run modal …` from the repo. |
| `uv sync` says the lockfile is out of date | `pyproject.toml` and `uv.lock` drifted | `uv lock` then `make install`. `make ci` enforces with `uv lock --check`. |

## Providers and keys

| What you see | What it means | Fix |
| --- | --- | --- |
| `Decode: set GEMINI_API_KEY in your environment or .env to start` | no key, empty, or placeholder (`changeme`) | real key in `.env`. Confirm: `grep -c '^GEMINI_API_KEY=.\+' .env`. |
| `Decode: LLM_PROVIDER=openrouter needs OPENROUTER_API_KEY …` | provider switched, key not set | set `OPENROUTER_API_KEY`, or drop `LLM_PROVIDER`. |
| `Decode: LLM_PROVIDER=modal needs MODAL_ENDPOINT_URL …` | provider `modal` before the endpoint exists | create it ([02 §1](02_modal_endpoints.md#1-create-the-endpoint)), paste URL + model id into `.env`. |
| `Decode: LLM_PROVIDER=modal proxy tokens are both-or-neither …` | one of `MODAL_PROXY_TOKEN_ID` / `_SECRET` set | set both, or neither (`--unauthenticated` endpoint). |
| `401` from your Modal endpoint | proxy tokens missing, mismatched, or not allowed on that env | `uv run modal workspace proxy-tokens create`, then `… allow wk-... main`. |
| First Modal turn hangs, then works | cold start | [02 §5](02_modal_endpoints.md#5-cold-starts-and-cost). |
| `ModelHTTPError: status_code: 503, model_name: Qwen/…` from your Modal endpoint | cold start: the endpoint scaled to zero (Min 0) and no container was ready yet | wait a few minutes, then resend. To avoid it, keep a container warm: open the endpoint in the dashboard, go to **AUTOSCALING → Edit → Override**, and set **Min 1** (see [02 §5. Cold starts and cost](02_modal_endpoints.md#5-cold-starts-and-cost)). |
| `429` / quota errors mid-turn | Gemini free-tier cap | wait, switch to `LLM_PROVIDER=openrouter`, or move to Modal. |


## Running the agent

| What you see | What it means | Fix |
| --- | --- | --- |
| Agent **describes** the tool instead of using it | pinned model has no tool-calling | back to a shipped default (`Qwen/Qwen3.6-35B-A3B-FP8`, `gemini-3.8-flash`, `openrouter/free`). |
| `Decode: no known context window for model …; assuming …` | informational — model not in the static table, probe didn't answer | set `COMPACTION_CONTEXT_WINDOW_TOKENS` to the real window. |
| `/` shows no demo skills | launched outside the course repo — skills load from the launch directory | relaunch from the repo, or copy `.decode/skills/` into your project. |
| `--resume` finds nothing | sessions are per-directory | relaunch from the same directory; transcripts in `.decode/sessions/`. |
| LSP tools return nothing | `ty` server missing or slow; best-effort by design | dev-group dependency — `make install`, not `uv sync --no-dev`. |
| Nothing in Opik | no `OPIK_API_KEY` → silent no-op | set it, plus `OPIK_WORKSPACE` if not `default`. Project = `decode-<DECODE_ENV>` unless `OPIK_PROJECT_NAME` overrides. |

## Still stuck?

Logs: `.decode/logs/decode.log` under the launch directory. `LOG_LEVEL=DEBUG` in `.env`, reproduce, read the tail. Then [open an issue](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course/issues) with that tail, provider, and model.
