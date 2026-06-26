# Modal Model Catalog — Picking & Serving a Model for the `decode` Harness

> **Snapshot:** 2026-06-26 · workspace `p-b-iusztin` · environment `main` (Starter plan).
> Catalog and benchmark figures were read from the live Modal **Auto Endpoints** "Create Endpoint"
> flow (`https://modal.com/endpoints/p-b-iusztin/main/create`). Benchmark numbers are Modal's own
> **estimated** performance preview, not measured production numbers — treat them as relative, not absolute.
> Catalogs change; re-check the dashboard before committing GPU budget.

## TL;DR

For a ReAct coding-agent harness (LLM ⇄ tools loop, OpenAI-compatible serving), the ranking is:

| Pick | Model | Model ID | Agentic GPU | Why |
|---|---|---|---|---|
| **Best fit** | GPT-OSS 120B | `openai/gpt-oss-120b` | **1×B200** | Native OpenAI tool-call format → cleanest harness integration; single GPU; highest interactivity |
| **Dev default** | Qwen3.6 35B A3B FP8 | `Qwen/Qwen3.6-35B-A3B-FP8` | **1×H100** | Cheapest serve (MoE ~3B active); strong Qwen tool-calling; iterate without burning credits |
| **Max capability** | GLM 5.2 FP8 | `zai-org/GLM-5.2-FP8` | **8×B200** | Flagship agentic-coding tuning; expensive — reserve for hard tasks |

**Why tool-calling dominates the choice:** every turn of the harness is "emit a valid tool call
against a schema → read the result → decide again." A model that codes brilliantly but is flaky at
structured tool calls *breaks the loop* (malformed JSON, hallucinated tool names, narrating instead
of calling). For an OpenAI-compatible gateway, the model whose native format **is** OpenAI
function-calling has the fewest failure modes — that's GPT-OSS.

---

## 1. The full catalog (23 models)

All open-weights, served from Modal's catalog. "Vision" = multimodal (irrelevant to this harness —
we send no images — but harmless). MoE models are written `<total>B-A<active>B`; only the active
params drive latency/cost, which is why a 35B-A3B serves on one GPU.

### Qwen (12)
| Name | Model ID | Notes |
|---|---|---|
| Qwen3.5 0.8B | `Qwen/Qwen3.5-0.8B` | too small for a tool loop |
| Qwen3.5 2B | `Qwen/Qwen3.5-2B` | too small |
| Qwen3.5 4B | `Qwen/Qwen3.5-4B` | too small |
| Qwen3.5 9B | `Qwen/Qwen3.5-9B` | borderline; tool discipline weak |
| Qwen3.5 27B FP8 | `Qwen/Qwen3.5-27B-FP8` | dense 27B |
| Qwen3.6 27B | `Qwen/Qwen3.6-27B` | dense 27B |
| Qwen3.6 27B FP8 | `Qwen/Qwen3.6-27B-FP8` | dense 27B, quantized |
| Qwen3.5 35B A3B FP8 | `Qwen/Qwen3.5-35B-A3B-FP8` | MoE, ~3B active |
| Qwen3.6 35B A3B | `Qwen/Qwen3.6-35B-A3B` | MoE, ~3B active |
| **Qwen3.6 35B A3B FP8** | `Qwen/Qwen3.6-35B-A3B-FP8` | **dev-default pick** |
| Qwen3.5 122B A10B FP8 | `Qwen/Qwen3.5-122B-A10B-FP8` | MoE, ~10B active |
| Qwen3.5 397B A17B FP8 | `Qwen/Qwen3.5-397B-A17B-FP8` | frontier MoE; heavy serve |

### Gemma (4)
| Name | Model ID | Notes |
|---|---|---|
| Gemma 4 E2B IT | `google/gemma-4-E2B-it` | too small |
| Gemma 4 E4B IT | `google/gemma-4-E4B-it` | too small |
| Gemma 4 26B A4B IT | `google/gemma-4-26B-A4B-it` | MoE; general-purpose, weaker agentic tool use |
| Gemma 4 31B IT | `google/gemma-4-31B-it` | dense; general-purpose |

### GPT-OSS (1)
| Name | Model ID | Notes |
|---|---|---|
| **GPT-OSS 120B** | `openai/gpt-oss-120b` | **best fit** — OpenAI-native tool calling, MoE, single-GPU |

### Nemotron (1)
| Name | Model ID | Notes |
|---|---|---|
| NVIDIA Nemotron 3 Super 120B A12B NVFP4 | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` | MoE ~12B active; strong reasoning |

### DeepSeek (2)
| Name | Model ID | Notes |
|---|---|---|
| DeepSeek V4 Flash | `deepseek-ai/DeepSeek-V4-Flash` | faster/lighter V4 variant |
| DeepSeek V4 Pro | `deepseek-ai/DeepSeek-V4-Pro` | strongest raw coder; heaviest serve |

### GLM (2)
| Name | Model ID | Notes |
|---|---|---|
| GLM 4.7 | `zai-org/GLM-4.7` | proven agentic-coding workhorse; cheaper fallback to 5.2 |
| **GLM 5.2 FP8** | `zai-org/GLM-5.2-FP8` | **max-capability pick**; 8×B200 |

### Kimi (1)
| Name | Model ID | Notes |
|---|---|---|
| Kimi K2.6 NVFP4 | `nvidia/Kimi-K2.6-NVFP4` | agentic/tool-use-first design; huge MoE → expensive |

---

## 2. Selection criteria for a coding-agent harness

Ranked by how much each matters to `decode`'s loop (`src/decode/agent/loop.py` driving
`src/decode/tools/`):

1. **Tool / function-calling reliability — #1.** The whole loop depends on valid, schema-conformant
   tool calls. Native OpenAI format wins.
2. **Long context (≥128k).** We read whole files, replay session logs, and run compaction. The
   agentic benchmark below uses a **61k-token input**, which mirrors accumulating tool results.
3. **Coding ability** (edit-apply / SWE-bench-style). Important, but #3 — a great coder that can't
   drive tools is useless here.
4. **Instruction following** — permission modes, agent definitions, structured outputs depend on it.
5. **Serveable footprint.** You pay for the GPU for the whole turn. Active params + quantization →
   GPU count → latency & cost. MoE models (low active params) are the sweet spot.

**Hard disqualifier:** the model must serve through vLLM with a working **tool-call parser**
(OpenAI/harmony parser for GPT-OSS, hermes/qwen parser for Qwen). No reliable tool calls → not a
candidate, regardless of coding score. Confirm under *Advanced Configurations* / the recipe.

---

## 3. Benchmarked head-to-head (Modal's estimated preview)

Workload **"Agentic multi-turn"** — input **61,278 tokens**, output **1,521 tokens** (this is the
profile that matches a coding agent accumulating tool-result context):

| Model | Model ID | GPU | Peak interactivity (tok/s/user) | Relative serve cost |
|---|---|---|---|---|
| **GPT-OSS 120B** | `openai/gpt-oss-120b` | **1×B200** | ~234 → 90 | medium |
| **Qwen3.6 35B A3B FP8** | `Qwen/Qwen3.6-35B-A3B-FP8` | **1×H100** | ~86 | **lowest** |
| **GLM 5.2 FP8** | `zai-org/GLM-5.2-FP8` | **8×B200** | ~112 – 168 | **highest** (~8× the GPU at the priciest tier) |

Reading the table:
- **GPT-OSS 120B** delivers the **highest interactivity on a single GPU** — best capability-per-dollar
  for this exact workload, and the cleanest tool-call integration.
- **Qwen3.6 35B A3B FP8** runs on **1×H100** (cheapest config here) at the same 61k context — ideal
  for development iteration where you're hammering the loop.
- **GLM 5.2 FP8** is strong but needs **8×B200**; the cost only makes sense for genuinely hard tasks.

> Cost intuition: B200 > H100 per GPU-hour, and GLM uses **eight** B200s. So for cheap dev,
> Qwen-on-H100 wins on absolute cost; for responsive "real" runs, GPT-OSS-on-one-B200 wins on
> cost-per-token; GLM is the premium option.

---

## 4. Recommendation

- **Default the harness to `openai/gpt-oss-120b`.** Best tool-calling fit + single-GPU + highest
  interactivity.
- **Use `Qwen/Qwen3.6-35B-A3B-FP8` while developing** to keep credit burn low; flip the model id for
  "real" runs.
- **Keep `zai-org/GLM-5.2-FP8` (or cheaper `zai-org/GLM-4.7`) as a switch-up** for the hardest tasks.
- **Skip:** small Qwens (0.8–9B) and Gemma E2B/E4B (can't hold tool discipline); and the frontier
  giants (`Qwen3.5-397B-A17B-FP8`, `DeepSeek-V4-Pro`, `Kimi-K2.6-NVFP4`) — overkill and costly to keep
  warm for a teaching repo.

---

## 5. Setting up an endpoint via the Modal CLI (Auto Endpoints)

Modal's **Auto Endpoints** feature provisions an OpenAI-compatible inference server from a catalog
model with one command. Authoritative docs:
- Endpoints guide: https://modal.com/docs/guide/endpoints
- Endpoint metrics: https://modal.com/docs/guide/endpoint-metrics
- Benchmark an endpoint: https://modal.com/docs/guide/endpoint-benchmarks

> Flag spellings below come from those docs and were cross-checked against the dashboard's
> *Advanced Configurations* (Routing Region + Compute Placement). If a flag name has drifted, run
> `modal endpoint create --help` — the CLI is the source of truth.

### 5.1 Prerequisites — authenticate the CLI

```bash
# Installed already via pyproject (`modal>=1.5.1`); auth once:
modal token set --token-id <your-token-id> --token-secret <your-token-secret>
# or set MODAL_TOKEN_ID / MODAL_TOKEN_SECRET in your environment (see .env.example)
```

### 5.2 Create the endpoint

```bash
# Best-fit pick for the harness:
modal endpoint create --model openai/gpt-oss-120b --env main

# Cheap dev default:
modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main

# Max-capability (heavy/expensive — 8×B200):
modal endpoint create --model zai-org/GLM-5.2-FP8 --env main
```

Modal auto-selects a compatible serving recipe and GPU config, then prints the **endpoint ID**,
**URL**, and a dashboard link. Omit a name and Modal derives one from the model id
(e.g. `gpt-oss-120b`).

### 5.3 Authentication — proxy tokens

Endpoints require auth by default ("Require authentication" in the UI; "No proxy tokens in workspace"
until you make one). Create a token pair:

```bash
modal workspace proxy-tokens create
# →  Modal-Key:    wk-...
#    Modal-Secret: ws-...
```

If RBAC is enabled, scope the token to the environment:

```bash
modal workspace proxy-tokens allow wk-... main
```

For **local development only**, you can skip auth at create time:

```bash
modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main --unauthenticated
```

### 5.4 Verify it — list served models

The endpoint speaks the **OpenAI Chat Completions API at `/v1`**:

```bash
export MODAL_PROXY_TOKEN_ID=wk-...
export MODAL_PROXY_TOKEN_SECRET=ws-...

curl "<your-endpoint-url>/v1/models" \
  -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \
  -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET"
```

A quick chat/tool smoke test:

```bash
curl "<your-endpoint-url>/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" \
  -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET" \
  -d '{
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": "Say hi in one word."}]
      }'
```

### 5.5 Advanced config (region & placement)

Verified in the dashboard's *Advanced Configurations*:

```bash
modal endpoint create \
  --model openai/gpt-oss-120b \
  --env main \
  --routing-region us-east \   # us-west (default) | us-east | eu-west | ap-south
  --colocate-compute           # pin containers to the routing region (UI: "Same as routing region")
```

`--colocate-compute` (UI "Same as routing region") can incur a region-selection price; the default
"Any region" lets Modal place containers by availability/capacity.

### 5.6 Manage endpoints

```bash
modal endpoint list --env main
modal endpoint list --env main --json
modal endpoint stop gpt-oss-120b --env main
```

---

## 6. Wiring an endpoint into `decode`

The endpoint is **OpenAI-compatible**, so it rides the same `OpenAIChatModel` path as OpenRouter.
Selecting it is **shipped** (ADR-0005, tasks 037-039): set the **LLM Provider** to `modal` and the
**Provider Seam** (`agent/factory._build_model()`) constructs the model from the settings below.

1. **Set the env vars** — already wired in `config/settings.py` and mirrored in `.env.example`
   (nothing reads `os.environ` in call sites, per AGENTS.md):
   ```bash
   LLM_PROVIDER=modal                       # select the modal backend
   MODAL_ENDPOINT_URL=https://...           # the endpoint URL; used as base_url = {url}/v1
   MODAL_ENDPOINT_MODEL=openai/gpt-oss-120b # served model id (default: the §4 best-fit pick)
   MODAL_PROXY_TOKEN_ID=wk-...              # Modal-Key   (optional — omit if --unauthenticated)
   MODAL_PROXY_TOKEN_SECRET=ws-...          # Modal-Secret (optional — omit if --unauthenticated)
   ```
   These endpoint vars are **distinct** from the `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` account
   tokens of §5.1 (the CLI/sandbox auth) — overloading the two scopes is exactly what ADR-0005 §2
   avoids.
2. **The Provider Seam builds the model.** For `modal`, `_build_model()` constructs an
   `OpenAIChatModel` over a custom `AsyncOpenAI` client whose `base_url = f"{MODAL_ENDPOINT_URL}/v1"`
   — the bespoke client is needed for the per-user URL and the optional proxy-token headers.
3. **Auth nuance — implemented (ADR-0005 §5).** Modal's proxy uses custom **`Modal-Key` /
   `Modal-Secret`** request headers, not the OpenAI `Authorization: Bearer` scheme:
   - **Both proxy tokens set** → the client sends `Modal-Key` / `Modal-Secret` as **default headers**
     (the secret also rides as the SDK-required non-empty `api_key`).
   - **Neither set** (an `--unauthenticated` endpoint) → no Modal headers and a placeholder
     `api_key="EMPTY"` (the OpenAI SDK requires a non-empty value).
   The cli startup guard (task 039) enforces a **both-or-neither** invariant, so exactly one proxy
   token set is caught as a friendly misconfiguration rather than a silent 401 at the first request.

---

## 7. Caveats

- **Estimates, not measurements.** Section 3 numbers are Modal's preview. Validate with
  `modal endpoint create` + `modal endpoint benchmark` (see endpoint-benchmarks doc) before relying
  on them for budgeting.
- **Cold starts & keep-warm.** A serverless endpoint scales to zero; first request after idle pays a
  cold start. For an interactive TUI, configure min-replicas/keep-warm if latency matters (check
  `modal endpoint create --help`).
- **Tool-call parser is the gate.** Re-confirm the recipe enables OpenAI/hermes-style tool parsing
  for whichever model you pick — it's the single thing that makes or breaks the harness loop.
- **Catalog drift.** Model ids and GPU recipes change. This file is a 2026-06-26 snapshot; re-read the
  dashboard before a long-lived decision.
