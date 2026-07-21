# Modal Model Catalog — Picking & Serving a Model for the `decode` Harness

> **Snapshot:** 2026-06-26, read from Modal's **Auto Endpoints** "Create Endpoint" flow. Benchmark
> figures are Modal's own **estimates** — treat them as relative, not absolute. Catalogs drift;
> re-check the dashboard before committing GPU budget.

## TL;DR

For a ReAct coding-agent harness (LLM ⇄ tools loop, OpenAI-compatible serving):

| Pick | Model ID | Agentic GPU | Why |
|---|---|---|---|
| **Best fit** | `openai/gpt-oss-120b` | **1×B200** | Native OpenAI tool-call format → cleanest harness integration; single GPU; highest interactivity |
| **Dev default** | `Qwen/Qwen3.6-35B-A3B-FP8` | **1×H100** | Cheapest serve (MoE ~3B active); strong Qwen tool-calling; iterate without burning credits |
| **Max capability** | `zai-org/GLM-5.2-FP8` | **8×B200** | Flagship agentic-coding tuning; expensive — reserve for hard tasks (`zai-org/GLM-4.7` is the cheaper fallback) |

**The rest of the catalog** (23 models: 12 Qwen, 4 Gemma, GPT-OSS, Nemotron, 2 DeepSeek, 2 GLM,
Kimi) is browsable in the dashboard. Skip the small models (Qwen ≤9B, Gemma E2B/E4B — can't hold
tool discipline) and the frontier giants (`Qwen3.5-397B-A17B-FP8`, `DeepSeek-V4-Pro`,
`nvidia/Kimi-K2.6-NVFP4` — costly overkill for a teaching repo).

## Selection criteria for a coding-agent harness

Ranked by how much each matters to `decode`'s loop:

1. **Tool / function-calling reliability — #1.** Native OpenAI format wins.
2. **Long context (≥128k)** — whole files, session replays, compaction; the agentic benchmark below
   uses a 61k-token input, mirroring accumulated tool results.
3. **Coding ability** — important, but a great coder that can't drive tools is useless here.
4. **Instruction following** — permission modes, agent definitions, structured outputs.
5. **Serveable footprint** — active params + quantization → GPU count → latency & cost; MoE models
   are the sweet spot.

**Hard disqualifier:** the model must serve through vLLM with a working **tool-call parser**
(OpenAI/harmony for GPT-OSS, hermes/qwen for Qwen). Confirm under *Advanced Configurations*.

## Benchmarked head-to-head (Modal's estimated preview)

Workload "Agentic multi-turn" — input 61,278 tokens, output 1,521 tokens:

| Model ID | GPU | Peak interactivity (tok/s/user) | Relative serve cost |
|---|---|---|---|
| `openai/gpt-oss-120b` | **1×B200** | ~234 → 90 | medium |
| `Qwen/Qwen3.6-35B-A3B-FP8` | **1×H100** | ~86 | **lowest** |
| `zai-org/GLM-5.2-FP8` | **8×B200** | ~112–168 | **highest** (~8× the GPU at the priciest tier) |

Cost intuition: for cheap dev, Qwen-on-H100 wins on absolute cost; for responsive real runs,
GPT-OSS-on-one-B200 wins on cost-per-token; GLM is the premium option.

## Setting up an endpoint via the Modal CLI (Auto Endpoints)

Docs: [endpoints guide](https://modal.com/docs/guide/endpoints?source=decodingai&campaign=harnesseng) ·
[metrics](https://modal.com/docs/guide/endpoint-metrics?source=decodingai&campaign=harnesseng) ·
[benchmarks](https://modal.com/docs/guide/endpoint-benchmarks?source=decodingai&campaign=harnesseng). If a flag has drifted, `modal
endpoint create --help` is the source of truth.

### Authenticate the CLI

```bash
modal token set --token-id <your-token-id> --token-secret <your-token-secret>
# or set MODAL_TOKEN_ID / MODAL_TOKEN_SECRET (see .env.example)
```

These **account** tokens authenticate the CLI *and* the Modal Sandbox (`SANDBOX_MODE=modal`) —
distinct from the **endpoint/proxy** tokens below, which are how `decode` *calls* your served model.

### Create the endpoint

```bash
modal endpoint create --model openai/gpt-oss-120b --env main          # best fit
modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main    # cheap dev default
modal endpoint create --model zai-org/GLM-5.2-FP8 --env main         # max capability (8×B200)
```

Modal auto-selects a serving recipe + GPU config and prints the endpoint ID, URL, and dashboard link.

### Authentication — proxy tokens

Endpoints require auth by default. Create a token pair (and scope it to the env if RBAC is on):

```bash
modal workspace proxy-tokens create        # → Modal-Key: wk-... / Modal-Secret: ws-...
modal workspace proxy-tokens allow wk-... main
```

For local development only, skip auth at create time with `--unauthenticated`.

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
| Min / Max / Buffer containers | — (not supported) | AUTOSCALING → Edit → Override |

Autoscaling is **dashboard-only**: **Min ≥ 1** = keep-warm (no cold starts, you pay for idle GPU);
**Min 0** = scale-to-zero (first request after idle pays a cold start).

### Manage

```bash
modal endpoint list --env main
modal endpoint stop gpt-oss-120b --env main
```

## Wiring an endpoint into `decode`

The endpoint is OpenAI-compatible, so it rides the same `OpenAIChatModel` path as OpenRouter
(ADR-0005): set the provider to `modal` and the Provider Seam (`agent/factory._build_model()`)
builds the model from settings:

```bash
LLM_PROVIDER=modal
MODAL_ENDPOINT_URL=https://...           # used as base_url = {url}/v1
MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b # served model id
MODAL_PROXY_TOKEN_ID=wk-...              # optional — omit if --unauthenticated
MODAL_PROXY_TOKEN_SECRET=ws-...
```

Auth nuance: Modal's proxy uses custom `Modal-Key` / `Modal-Secret` headers, not `Authorization:
Bearer`. Both proxy tokens set → sent as default headers; neither set → no headers and a placeholder
`api_key="EMPTY"`. The startup guard enforces both-or-neither, so a half-set pair is a friendly
config error, not a silent 401.

## Caveats

- **Estimates, not measurements** — validate with `modal endpoint benchmark` before budgeting.
