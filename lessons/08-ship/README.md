# Lesson 8 — Ship it to your team

Builder → operator: deployed runtime, environment-scoped secrets, a GitHub
pipeline where labeling an issue returns a reviewed PR, and judged
model-comparison cohorts.

## Run it

```bash
./lessons/08-ship/run.sh
```

The secrets story, proven locally: sync your `.env` into a staging Environment
Bucket, then run with `.env` *removed from the chain entirely* — the run
hydrates from the bucket. Then the invariant that makes environments worth
having: a missing bucket (or a key missing *from* it) fails loudly with one
friendly line and never backfills from your laptop.

## Playbook (interactive)

1. **No backfill.** Remove `GEMINI_API_KEY` from the bucket, keep it in `.env`,
   run at `DECODE_ENV=staging` — it fails loudly even though the key sits right
   there. A provisioning gap must not be masked by a developer's laptop.
2. **Process env wins.** `GEMINI_API_KEY=<key> DECODE_ENV=staging uv run decode
   run "hi"` — the escape hatch when a bucket key is stale.
3. **The full pipeline.** Deploy the remote runtime stack —
   [running_the_code/infra.md](../../running_the_code/infra.md). The whole agent
   runs on Modal, checkpoints on a self-hosted Kitaru server, and labeling a
   GitHub issue returns a reviewed PR. 💰 The only part of the course that
   costs real money (~$16/month) — entirely optional.

## Deep dives

- [ADR-0015 — Environment Bucket secrets](../../docs/adr/0015-environment-bucket-secrets.md)
- [running_the_code/credentials.md](../../running_the_code/credentials.md) — every negative case, walked
- [running_the_code/infra.md](../../running_the_code/infra.md)

## Background reading

| Article | Why read it here |
|---|---|
| [Deploying LLMs: Cloud, Metal, Serverless](https://www.decodingai.com/p/deploying-llms-cloud-metal-serverless) | Cloud vs bare-metal vs Modal serverless trade-offs for model serving — the course's own remote-inference seam. |
| [The GitHub Issue AI Butler on Kubernetes](https://www.decodingai.com/p/the-github-issue-ai-butler-on-kubernetes) | End-to-end production deployment of a headless agent pipeline on cloud infra — the same shape as decode's label-an-issue-get-a-PR pipeline. |
