---
id: 146
feature: modal-remote-headless
status: pending
---

# Docs: the new remote story — 07_infra rewrite, 08_evals_replays Modal-worker option, cross-refs

Tags: `docs`
Depends on: 141, 142, 143, 144, 145
Blocks: —

This task implements ADR-0020 §7. Docs describe only what shipped; every command shown must
have been executed by the tasks above.

## Scope

- **`running_the_code/07_infra.md` — rewrite around the Modal story:**
  - "The shape today" table gains two rows: **Modal Headless App** (`decode-headless`;
    `modal run` sync / `modal deploy` + `attempts` spawn; sandbox `none`/`modal` only, docker
    rejected) and **Modal-hosted Kitaru Worker** (`decode-kitaru-worker`; replays remote via
    agent v3). Modal's row updated: it hosts the harness again — but launch-vs-execute split,
    not a server (ADR-0020 §1).
  - New sections: **secrets setup** — the exact `modal secret create decode-headless …` and
    `modal secret create decode-kitaru-worker …` commands with key NAMES only (values never
    committed; note the deliberate `KITARU_AGENT_ID` asymmetry and why, ref 08 §7.3); **run &
    verify** — the sync run, the N-attempts run, the branch check, the worker replay check
    (reuse the executed AC commands from 142/143/145); **costs** — usage-based Modal only, the
    ~$16/month VM stays dead.
  - **Trim the stale GCP appendix** to a short retirement note (~15 lines): what the stack was,
    why it died (ADR-0019), where the full text lives (git history), and that its deleted
    surfaces (`deploy.sh`, `run-remote`, `demo-multiple-attempts.sh`, `flow.Dockerfile`,
    `remote` dep group) were removed by this feature. The traps-as-lessons prose goes; the
    pointer preserves it.
- **`running_the_code/08_evals_replays.md`:** §5 gains the Modal worker as the second way to
  run a worker (one paragraph + the two commands, agent `decode@3`, `none` sandbox note);
  §7.3's pitfall gains one sentence: the `decode-kitaru-worker` secret deliberately omits
  `KITARU_AGENT_ID`, and the Modal worker scrubs it defensively.
- **`running_the_code/02_modal_endpoints.md`:** one cross-ref line near the top — Modal also
  hosts decode's headless harness and Kitaru Worker, see 07_infra.md.
- **`AGENTS.md`:** update the Modal rows minimally — tech-stack table ("Sandbox / serving" row
  gains the headless harness + hosted worker mention) and the infra-access CLI list; nothing
  else.
- **Verify** `.env.example` needs no new keys (the feature added no `Settings` fields) and that
  glossary/ADR references (committed at grooming) match what shipped.

## Acceptance Criteria

- [ ] 07_infra.md head table names both new Modal apps with their launch commands; every command block in the new sections is copy-paste runnable and was executed by a prior task's AC.
- [ ] The GCP appendix is ≤ ~20 lines: retirement note + git-history pointer; `grep -n "deploy.sh\|run-remote\|KITARU_STACK" running_the_code/` returns hits only inside that note (historical prose), nowhere as live instructions.
- [ ] Secret-creation commands show key names for both secrets; `KITARU_AGENT_ID` appears in `decode-headless` and explicitly NOT in `decode-kitaru-worker`, with the one-sentence why.
- [ ] 08_evals_replays.md §5 offers laptop and Modal worker paths; §7.3 carries the secret-composition sentence.
- [ ] 02_modal_endpoints.md carries the cross-ref; AGENTS.md Modal rows updated; `.env.example` unchanged (verified).
- [ ] All intra-doc links resolve; `make ci` green.

## User Stories

### Story: New reader sets up the full remote story from 07_infra alone
1. Reader opens 07_infra.md, reads the shape-today table
2. Follows the secrets section: creates both Modal secrets from their `.env` values
3. Runs the sync headless command, then the worker deploy + one replay — every command works verbatim
4. Nothing in the doc tells them to provision GCP, run deploy.sh, or toggle Docker Desktop settings

### Story: Operator hits the 403 and the docs already name it
1. A replay hard-fails with `403: Task credentials are not accepted on this route`
2. Operator finds 08_evals_replays.md §7.3: the worker env must not carry `KITARU_AGENT_ID`, and the `decode-kitaru-worker` secret omits it by design
3. Operator fixes the secret; the replay claims and runs

## Out of scope

- The `manual-e2e-qa` skill and squid-plugin docs (separate surfaces, separate round).
- Re-teaching the retired GCP traps — git history is the archive.

---

Refs: ADR-0020 §7, tasks 141–145

## Log
