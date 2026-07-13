# Benchmark tasks

The decode outcome benchmark (ADR-0017 §2,5). Each task is a **folder** here —
`evals/benchmark/tasks/<NNN>-<slug>/` — graded by a hidden `verify.sh` oracle. The folder IS the
contract; the loader (`evals/harness/task_loader.py`) validates it and the oracle-sanity harness
(`tests/unit/evals/benchmark/test_oracle_sanity.py`) keeps every oracle honest both directions.

The 20 real tasks land in tasks 108-110. This README plus the loader/oracle harness are the format.

## Folder layout

```
<NNN>-<slug>/
├── task.yaml       # the agent-facing spec (below)
├── setup/          # seeded into the Workspace BEFORE the run
│   ├── ...         # files copied verbatim
│   └── setup.sh    # OPTIONAL, run in the Workspace after seeding
├── verify/         # the HIDDEN oracle — injected only at grade time
│   ├── verify.sh   # REQUIRED: exit 0 (prints PASS) = success
│   └── ...         # OPTIONAL hidden test files
└── solution/       # gold overlay onto setup/ — oracle-sanity ONLY, never in an agent run
    └── ...
```

## `task.yaml`

```yaml
id: 001-fix-flaky-test        # unique, non-empty
prompt: |                     # what the AGENT sees — never mentions verify/ or the oracle
  The test suite is flaky. Make `pytest` pass reliably.
max_steps: 30                 # model-request budget (> 0)
difficulty: medium            # easy | medium | hard
tags: [python, testing]       # slice labels (may be empty)
judges:                       # OPTIONAL G-Eval add-ons (ADR-0017 §7)
  - name: minimal_diff
    task_introduction: Judge whether the fix is minimal.
    evaluation_criteria: Score 1 if only the flaky assertion changed, 0 if unrelated files churned.
```

Unknown keys are rejected — a typo'd field fails loudly rather than being ignored.

## The four folders

- **`setup/`** — copied verbatim into a fresh Workspace before the agent runs. `setup/setup.sh`
  (optional) runs *in* the Workspace after seeding, for state that can't be a committed file: git
  history, sqlite DBs, mixed encodings.
- **`verify/`** — the hidden oracle. `verify.sh` is the grading logic (Terminal-Bench style): exit
  `0` = PASS, non-zero = FAIL. Injected into the Workspace ONLY at grade time; the agent never sees
  it and the `prompt` never names it. **`verify.sh` may only use `bash`, `python`, `git`,
  `sqlite3`** so it runs identically host-side (oracle-sanity) and in the sandbox image.
- **`solution/`** — a committed gold solution overlaid onto `setup/`. Used ONLY by the
  oracle-sanity harness to prove the oracle passes on a correct answer; it never enters an agent
  run.

## Grade-time Workspace

The oracle-sanity harness and the runner build the Workspace the same way: seed `setup/`, run
`setup.sh`, (agent works, or `solution/` is overlaid), inject `verify/` at the Workspace root, then
`bash verify.sh` from that root. So `verify.sh` sees the Workspace files at `./` and its own hidden
helpers alongside.

## Syncing to Opik

`python -m evals sync --benchmark` upserts one item per task (`task_id`, `difficulty`, `tags`) into
the `decode-benchmark-v1` dataset. Idempotent — Opik deduplicates by content.
