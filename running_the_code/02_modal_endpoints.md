# 02 — Serve your own model on Modal

Replace the rate-limited free key with an open-weights model you serve yourself. Every lesson is tested against the default below. $30 signup credits ≈ 7 hours on 1×H100.

## 1. Create the endpoint

```bash
# 1. authenticate — tokens from https://modal.com?source=decodingai&campaign=harnesseng (one-time, writes ~/.modal.toml)
uv run modal token set --token-id <your-token-id> --token-secret <your-token-secret>

# 2. serve the course default — Modal picks GPU + recipe, prints the endpoint URL
uv run modal endpoint create --model Qwen/Qwen3.6-35B-A3B-FP8 --env main

# 3. mint a proxy token pair so the endpoint is not open to the world
uv run modal workspace proxy-tokens create        # → Modal-Key: wk-... / Modal-Secret: ws-...
uv run modal workspace proxy-tokens allow wk-... main
```

Lost the URL: `uv run modal endpoint list --env main`.

## 2. Point decode at it

In `.env`:

```bash
LLM_PROVIDER=modal
MODAL_ENDPOINT_URL=https://your-workspace--your-app.modal.run   # decode calls {url}/v1
MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
MODAL_PROXY_TOKEN_ID=wk-...          # both, or neither (an --unauthenticated endpoint)
MODAL_PROXY_TOKEN_SECRET=ws-...
```

Two token pairs, do not mix them: `MODAL_TOKEN_*` authenticate the CLI and are **not** `.env` settings; `MODAL_PROXY_TOKEN_*` are how decode calls the model.

> ✅ Returns your model id:
>
> ```bash
> set -a && . ./.env && set +a
> curl "$MODAL_ENDPOINT_URL/v1/models" -H "Modal-Key: $MODAL_PROXY_TOKEN_ID" -H "Modal-Secret: $MODAL_PROXY_TOKEN_SECRET"
> ```

Then `decode` as before.

## 3. Cold starts and cost

The endpoint scales to zero (Min 0), so the first turn of a session waits for a GPU. While iterating, open the endpoint in the dashboard, **AUTOSCALING → Edit → Override**, set **Min 1**:

![Min containers = 1](../assets/modal_setup_endpoint.gif)

A warm container bills idle time. Back to Min 0, or stop it, when done:

```bash
uv run modal endpoint list --env main
uv run modal endpoint stop <endpoint-id> --env main
```

`COMPACTION_CONTEXT_WINDOW_TOKENS=262144` in `.env` skips decode's startup probe of `/v1/models`, the one request that waits on a cold endpoint.

## 4. Other models

Same command, different `--model`; then re-point `MODAL_ENDPOINT_URL` / `MODAL_ENDPOINT_MODEL`.

| Pick | Model ID | GPU | Why |
|---|---|---|---|
| **Course default** | `Qwen/Qwen3.6-35B-A3B-FP8` | 1×H100 | cheapest by far (MoE, ~3B active); reliable tool calling |
| Middle ground | `openai/gpt-oss-120b` | 1×B200 | native OpenAI tool-call format; ~2–3× faster per user |
| Max capability | `zai-org/GLM-5.2-FP8` | 8×B200 | strongest agentic coding; ~8× the GPU cost |

Hard requirement: served through vLLM with a working tool-call parser. Skip models under ~10B (lose tool discipline). Full catalog and benchmarks in the Modal dashboard; docs: [endpoints](https://modal.com/docs/guide/endpoints?source=decodingai&campaign=harnesseng).

---

**Next:** [03_sandboxing.md](03_sandboxing.md) — move the agent's tools into an isolated Workspace, work on any repo, get a branch back.
