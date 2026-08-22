---
id: 144
feature: modal-remote-headless
status: pending
---

# Agent version 3: extend register_kitaru_agent.py with --sandbox-mode for the Modal-hosted Worker

Tags: `infra`, `cli`, `enhancement`
Depends on: None
Blocks: 145

This task implements ADR-0020 §5. The Modal-hosted Kitaru Worker (task 145) spawns replays with
a run spec that must exist as a registered Agent Version. Extend the EXISTING script — not a new
one (human-approved) — so one `--sandbox-mode` flag produces either the laptop spec (v2-style,
docker) or the Modal-container spec (none/modal). The laptop keeps agent v2 (docker) untouched.

## Scope

- **`--sandbox-mode [docker|none|modal]` option** on `scripts/register_kitaru_agent.py`,
  default `docker` (byte-identical v2-style argv — pinned by existing tests staying green
  untouched).
- **`build_run_env` grows the mode:**
  - `docker` / `modal`: `SANDBOX_MODE=<mode>` + `SANDBOX_REPO=<repo>` + `DECODE_ENV=local`
    (unchanged shape).
  - `none`: `SANDBOX_MODE=none` + `DECODE_ENV=local`, **NO `SANDBOX_REPO`** — decode's guard
    rejects a repo under `none` (ADR-0012 §3); replayed tool calls land in the Worker's
    in-container Harness Home cwd, and the gVisor container is the isolation (ADR-0020 §5).
- **Remote-path registration.** v3's `--decode-bin` and `--harness-home` are paths inside the
  Modal worker image (they do not exist on the operator's laptop, where the registration runs).
  Make the local `entrypoint.is_file()` check skippable for that case — suggested: skip it when
  `--decode-bin` is passed explicitly, or add `--skip-bin-check`; SWE decides, but the failure
  mode of a typo'd remote path must be named in `--help`. The harness-home-inside-repo
  `ValueError` still applies to the given path strings.
- **`--dry-run` prints the exact `kitaru agent version register` argv** for the v3 spec, as it
  does for v2 — this is the reproducibility contract task 145's docs lean on.
- Description text updated per mode (the `_DESCRIPTION` constant currently hard-codes docker).
- **Unit tests extended** (`tests/unit/scripts/test_register_kitaru_agent.py`): `register_argv`
  permutations per mode; `none` env carries no `SANDBOX_REPO`; docker default unchanged;
  remote-path registration path works without a local binary.

## Acceptance Criteria

- [ ] `--sandbox-mode` accepted with the three values; anything else fails with click's usage error.
- [ ] Default invocation (`--dry-run`, no new flags) prints an argv byte-identical to today's v2 spec — existing tests green untouched.
- [ ] `--sandbox-mode none --dry-run` prints an argv whose `--env` entries are exactly `SANDBOX_MODE=none` and `DECODE_ENV=local` (no `SANDBOX_REPO`).
- [ ] Registration with explicit container paths succeeds with no local decode binary present — unit-tested via the skip path.
- [ ] Harness-home-inside-repo still raises for all modes.
- [ ] [HUMAN] Operator registers agent version 3 on the workspace (`uv run python scripts/register_kitaru_agent.py --sandbox-mode none --decode-bin <in-image path> --harness-home <in-image path> …`); `kitaru agent get decode` lists version 3 with the `none` run spec; version 2 is untouched.
- [ ] Full unit suite green; `make ci` green.

## User Stories

### Story: Operator registers the Modal worker's run spec from the laptop
1. Operator reads task 145's image layout for the two in-container paths
2. Runs the script with `--sandbox-mode none`, the container `--decode-bin`, and the container `--harness-home`, first with `--dry-run`
3. The printed `kitaru agent version register` argv shows `SANDBOX_MODE=none`, `DECODE_ENV=local`, and no `SANDBOX_REPO`
4. Re-runs without `--dry-run`; `kitaru agent get decode` shows version 3; sessions recorded under v1/v2 are untouched (the agent is resolved, never re-created)

### Story: Laptop replays keep working exactly as before
1. Operator re-runs the script with no new flags on their laptop
2. The argv is identical to the shipped v2 registration — docker mode, repo clone, same description shape

## Out of scope

- Version-attached secrets on v3 — provider keys reach the spawned run through the Kitaru
  Worker's env, exactly as v2 (ADR-0019 Amendments §2); the Modal worker's env comes from the
  `decode-kitaru-worker` secret (task 145).
- Starting the worker itself (task 145).

---

Refs: ADR-0020 §5, ADR-0019 §4 + Amendments, `tasks/done/137-agent-version-2-replay-context.md`

## Log
