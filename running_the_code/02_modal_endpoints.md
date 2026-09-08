# Modal Model Catalog — Picking & Serving a Model for the `decode` Harness

> **Snapshot:** 2026-06-26, from Modal's **Auto Endpoints** "Create Endpoint" flow. Benchmark figures are Modal's own **estimates** — relative only; validate with `modal endpoint benchmark`. Re-check the dashboard before committing GPU budget.

> Modal also hosts decode's headless harness ([04_deploy.md](04_deploy.md)) and, later, its replay worker ([07_evals_replays_deploy.md](07_evals_replays_deploy.md)).

## TL;DR

| Pick | Model ID | Agentic GPU | Why |
|---|---|---|---|
| **Course default** | `Qwen/Qwen3.6-35B-A3B-FP8` | **1×H100** | cheapest serve by a wide margin (MoE, ~3B active); reliable tool-calling via the `hermes`/`qwen` parser. Hundreds of loop re-runs without rationing credits |
| **Middle ground** | `openai/gpt-oss-120b` | **1×B200** | native OpenAI tool-call format → least harness friction; ~2–3× the interactivity |
| **Max capability** | `zai-org/GLM-5.2-FP8` | **8×B200** | flagship agentic-coding tuning; 8× the GPU at the priciest tier. `zai-org/GLM-4.7` = cheaper fallback |

A cost/capability ladder, not a ranking. Every lesson is built and tested against the Qwen default.

Rest of the catalog (23 models: 12 Qwen, 4 Gemma, GPT-OSS, Nemotron, 2 DeepSeek, 2 GLM, Kimi) is in the dashboard. Skip small models (Qwen ≤9B, Gemma E2B/E4B — can't hold tool discipline) and frontier giants (`Qwen3.5-397B-A17B-FP8`, `DeepSeek-V4-Pro`, `nvidia/Kimi-K2.6-NVFP4` — overkill).

## Selection criteria for a coding-agent harness

Ranked for `decode`'s loop while you learn it:

1. **Tool / function-calling reliability.** Mis-formatted tool calls = debugging the model instead of the harness. Native OpenAI format (GPT-OSS) is smoothest; Qwen's `hermes`/`qwen` parser clears the bar.
2. **Cost per learning iteration.** Hundreds of runs, most on turns you already understand. ~10× cheaper serve = ~10× more attempts per credit.
3. **Long context (≥128k)** — whole files, session replays, compaction. The agentic benchmark below uses a 61k-token input.
4. **Coding ability** — a great coder that can't drive tools is useless here.
5. **Instruction following** — permission modes, agent definitions, structured outputs.
6. **Serveable footprint** — active params + quantization → GPU count → latency & cost. MoE is the sweet spot (35B-A3B on one H100).

**Hard disqualifier:** must serve through vLLM with a working **tool-call parser** (OpenAI/harmony for GPT-OSS, hermes/qwen for Qwen). Confirm under *Advanced Configurations*.

## Benchmarked head-to-head (Modal's estimated preview)

Workload "Agentic multi-turn" — input 61,278 tokens, output 1,521 tokens:

| Model ID | GPU | Peak interactivity (tok/s/user) | Relative serve cost |
|---|---|---|---|
| `Qwen/Qwen3.6-35B-A3B-FP8` (course default) | **1×H100** | ~86 | **lowest** |
| `openai/gpt-oss-120b` | **1×B200** | ~234 → 90 | medium |
| `zai-org/GLM-5.2-FP8` | **8×B200** | ~112–168 | **highest** (~8× the GPU) |

Qwen: slowest per user, cheapest by far — while building, GPU-hours are the scarce resource. GPT-OSS: ~2–3× interactivity for a real per-hour step up. GLM: most capable on hard agentic tasks, priced accordingly. Numbers are relative; B200 vs H100 is a hardware difference too.

## Setting up an endpoint via the Modal CLI (Auto Endpoints)

Docs: [endpoints](https://modal.com/docs/guide/endpoints?source=decodingai&campaign=harnesseng) · [metrics](https://modal.com/docs/guide/endpoint-metrics?source=decodingai&campaign=harnesseng) · [benchmarks](https://modal.com/docs/guide/endpoint-benchmarks?source=decodingai&campaign=harnesseng). `modal endpoint create --help` is the source of truth for flags.

### Authenticate the CLI

```bash
modal token set --token-id <your-token-id> --token-secret <your-token-secret>
# or set MODAL_TOKEN_ID / MODAL_TOKEN_SECRET (see .env.example)
```

These **account** tokens authenticate the CLI and the Modal Sandbox (`SANDBOX_MODE=modal`). Read from `~/.modal.toml` / `os.environ` by the `modal` library, never by `Settings` — not `.env` keys. The **proxy** pair below *is* a `Settings` field (`MODAL_PROXY_TOKEN_ID` / `_SECRET`): rides `.env`, and doubles as the headless webhook's auth ([04_deploy.md §4](04_deploy.md#4-run-a-task-from-a-webhook)).

### Create the endpoint

```bash
modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main    # course default
modal endpoint create --model openai/gpt-oss-120b --env main         # middle ground
modal endpoint create --model zai-org/GLM-5.2-FP8 --env main         # max capability (8×B200)
```

Modal picks the serving recipe + GPU and prints endpoint ID, URL, dashboard link.

### Authentication — proxy tokens

Endpoints require auth by default. Create a pair (scope to the env if RBAC is on):

```bash
modal workspace proxy-tokens create        # → Modal-Key: wk-... / Modal-Secret: ws-...
modal workspace proxy-tokens allow wk-... main
```

Local dev only: `--unauthenticated` at create time.

### Verify — the endpoint speaks OpenAI Chat Completions at `/v1`

```bash
curl "<your-endpoint-url>/v1/models" \
  -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \
  -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET"
```

### Advanced config — CLI vs dashboard

| Knob | CLI at create | Dashboard after create |
|---|---|---|
| Routing region | `--routing-region` (us-west default, us-east, eu-west, ap-south) | Details → Routing Region |
| Compute placement | `--colocate-compute` (pin containers to the routing region) | Details → Compute Placement |
| Min / Max / Buffer containers | — | AUTOSCALING → Edit → Override |

Autoscaling is dashboard-only. **Min ≥ 1** = keep-warm (no cold starts, idle GPU billed); **Min 0** = scale-to-zero (first request after idle pays a cold start).

### Manage

```bash
modal endpoint list --env main
modal endpoint stop qwen3-6-35b-a3b-fp8 --env main
```

## Wiring an endpoint into `decode`

OpenAI-compatible, so it rides the same `OpenAIChatModel` path as OpenRouter (ADR-0005); the Provider Seam (`agent/factory._build_model()`) builds it from settings:

```bash
LLM_PROVIDER=modal
MODAL_ENDPOINT_URL=https://...                    # base_url = {url}/v1
MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8     # served model id
MODAL_PROXY_TOKEN_ID=wk-...                       # optional — omit if --unauthenticated
MODAL_PROXY_TOKEN_SECRET=ws-...
```

Auth: custom `Modal-Key` / `Modal-Secret` headers, not `Authorization: Bearer`. Both set → default headers; neither → no headers, placeholder `api_key="EMPTY"`. Startup guard enforces both-or-neither.

Moving up the ladder = re-point `MODAL_ENDPOINT_URL` / `MODAL_ENDPOINT_MODEL`. No code change.

## Killing cold starts while you work

**Min 0** (default) scales to zero between sessions → the first turn of every session waits for the GPU. While iterating, set **minimum containers to 1**: open the endpoint, **AUTOSCALING → Edit → Override**:

![Setting the minimum number of containers to 1 on a Modal endpoint](../assets/modal_setup_endpoint.gif)

A warm container bills idle time. Keep Min 1 for a working session, then back to 0 — or `uv run modal endpoint stop <endpoint-id> --env main` — when done.
