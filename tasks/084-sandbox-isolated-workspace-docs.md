---
id: 084-sandbox-isolated-workspace-docs
feature: isolated-workspace
status: done
---

# Docs — AGENTS.md invariants + Sandboxing e2e rows, README, .env.example, MODAL note

Tags: `sandbox`, `docs`
Depends on: #083
Blocks: —

Reconcile every prose surface to the isolated-workspace reality (ADR-0012), including the built git
hand-back. Docs only — the suite stays green. Mirrors the 076 docs task.

## Scope

- **`AGENTS.md`:**
  - Rewrite the **Sandbox invariant**: a sandbox mode is a fully isolated workspace — ONE
    `SandboxExecutor` over two thin backend adapters (docker/modal), fresh-exec both; `/workspace` ≡
    host `.decode/sandbox` ≡ a git clone of `--repo` (or empty); **file tools operate on the sandbox
    filesystem through the backend seam** (docker: bind-mount pathlib; modal: SandboxFilesystem +
    remote find/grep), not on a mirror. Add the **Harness-home invariant**: harness artifacts
    (`.decode/sessions`, `MEMORY.md`, logs, `.decode/skills`, permission file) anchor to the launch
    cwd; only the agent's tool scope (`deps.cwd`) moves into the workspace. Add the **Hand-back
    invariant**: the harness ships the Workspace's results as a `decode/<session-id>` branch pushed
    back to the repo — **host-side only, no credential ever enters the sandbox**; the local branch
    always exists even if the push fails.
  - Rewrite the **Testing-E2E "Sandboxing" rows**: retire persistent-shell / empty-scratch language;
    document the unified fresh-exec Workspace, `--repo`/`--local`, file tools in the Workspace,
    docker's live mount vs modal's bootstrap-upload + export, the revival-restore note, LSP
    (none+docker on; modal best-effort-off), gated `web_fetch`, the auto-ship on exit, and a
    **`/ship`** row ("Type this: `/ship` — Working looks like: prints `decode/<id>` + the push
    outcome; `none` mode → 'no sandbox workspace'"). Keep the Credential-Proxy row.
- **`README.md`:** rewrite `## Sandboxing` to the isolated-Workspace model + a `--repo` quickstart
  ending with the hand-back — "on exit (or `/ship`) decode pushes a `decode/<id>` branch back to your
  repo with your own git creds" (branch naming, push-to-remote vs local, never-lose-results).
- **`.env.example`:** rewrite the sandbox block; document `SANDBOX_REPO`; note the hand-back needs no
  new var (uses `--repo` + ambient git creds); retire stale scratch/persistent-shell/sync comments.
- **MODAL note:** account- vs endpoint-token distinction retained; note the Workspace is bootstrap-
  uploaded + exported (not `add_local_dir`-seeded, not mtime-synced), file ops use `SandboxFilesystem`,
  and the hand-back push is host-side (never in the sandbox).

## Acceptance criteria

- [x] The three AGENTS.md invariants (Sandbox + Harness-home + Hand-back) match shipped behavior; no
  persistent-shell / empty-scratch / mtime-sync language remains.
- [x] The AGENTS.md Testing-E2E sandbox rows describe the unified Workspace, `--repo`, file-tools-in-
  Workspace, docker-mount vs modal-bootstrap/export, LSP posture, gated `web_fetch`, auto-ship, and
  `/ship` — each accurate against shipped code (branch naming, host-side push, layered durability,
  `none`-mode friendly line).
- [x] README `## Sandboxing` + `--repo` quickstart + the hand-back section accurate and
  self-consistent.
- [x] `.env.example` documents `SANDBOX_REPO`, no stale comments; every `SANDBOX_*` var matches
  `config/settings.py`.
- [x] No `src/` change; suite green, 0 warnings (`uv run pytest tests/unit`, 1394 passed — used per the
  task's Verify note instead of `make ci`, which lints the untracked WIP red).

## Out of scope

- Glossary + ADR-0012 (grooming commit). Capstone (085). Auto-PR docs (deferred to M14).

## Log

### [SWE] 2026-07-04 07:34 — Implementation

**Files modified**
- `AGENTS.md` — rewrote the Sandbox invariant + added the Harness-home and Hand-back invariants; rewrote
  the Testing-E2E "Sandboxing" intro + rows (docker/modal/decode-run) and added a `/ship` row (kept the
  Credential-Proxy row); updated the Secrets invariant's ADR ref.
- `README.md` — rewrote `## Sandboxing` to the isolated-Workspace model (new table + one-unified-executor
  paragraph) + a `--repo` quickstart ending in the git hand-back; kept the startup-guard,
  isolation-honesty, and Credential-Proxy subsections.
- `.env.example` — rewrote the sandbox block to the isolated-Workspace reality; documented `SANDBOX_REPO`
  + the "hand-back needs no new var" note; retired the scratch/persistent-shell/not-mounted comments.
- `tasks/084-sandbox-isolated-workspace-docs.md` — status → in-progress; checked the 5 acceptance criteria.

DOCS ONLY — **no `src/` change** (verified `git status`: only `.env.example`, `AGENTS.md`, `README.md`,
`tasks/084`; the untracked WIP `src/decode/tools/substack_summarizer.py` + `docs/notes/` untouched).

**Tests**
- Unit: 1394 passing, 0 failing, 0 warnings — `uv run pytest tests/unit` (run before and after the edits;
  docs don't affect it). Used per the task's Verify note **instead of** `make ci`, which lints the
  untracked WIP (`substack_summarizer.py`) red — a pre-existing condition unrelated to this change.
- Integration: N/A — docs-only, no infra change.

**Acceptance criteria**
- [x] Three AGENTS.md invariants (Sandbox rewrite + Harness-home + Hand-back) match shipped code; no
  stale persistent-shell / empty-scratch-as-whole-fs / mtime-sync claims remain (the only surviving
  "empty scratch" = the correct ADR-0012 no-repo Workspace; "mtime-sync" only names the rejected design).
- [x] Testing-E2E rows accurate against shipped code — unified fresh-exec Workspace, `--repo`/`--local`,
  file-tools-in-Workspace, docker live-mount vs modal bootstrap-upload+export, revival-restore note,
  LSP (none+docker on / modal best-effort-off), gated `web_fetch`, auto-ship, and the `/ship` row.
- [x] README `## Sandboxing` + `--repo` quickstart + hand-back accurate + self-consistent.
- [x] `.env.example` documents `SANDBOX_REPO` + hand-back-needs-no-var; every `SANDBOX_*` var matches
  `config/settings.py` (all 7: MODE/IMAGE/WORKSPACE_DIR/REPO/TIMEOUT_S/CREDENTIAL_PROXY_ENABLED/PROXY_IMAGE
  with matching defaults).
- [x] No `src/` change; suite green (0 warnings).

**Evidence**
```
$ git status --porcelain
 M .env.example
 M AGENTS.md
 M README.md
 M tasks/084-sandbox-isolated-workspace-docs.md
?? docs/notes/
?? src/decode/tools/substack_summarizer.py

$ uv run pytest tests/unit -q
........................................................................ [100%]
1394 passed in 88.58s (0:01:28)
```

Accuracy cross-checks against shipped code (`sandbox/{executor,docker_backend,modal_backend,workspace,
handback}.py`, `tools/{files,bash}.py`, `cli.py`, `tui/app.py`, `agent/deps.py`, `config/settings.py`):
one `SandboxExecutor` + two thin backends, fresh-exec both; `/workspace` ≡ host `.decode/sandbox` ≡ git
clone of `--repo`; file tools through the seam (docker pathlib-on-mount + `WorkspaceEscape`; modal
`SandboxFilesystem` + remote find/grep; bootstrap-upload + export; revival-restore); harness_home vs
deps.cwd; hand-back branch naming + skip conditions + host-side push + never-lose-results; the `/ship` /
`_ship_on_exit` / `_auto_ship_headless` triggers. **HITL caveat confirmed in `cli.py`:** `_auto_ship_headless`
is called only in the bypass `run()` path (not in `_run_hitl`), so `decode run --hitl --repo` does NOT
auto-ship — stated honestly in AGENTS.md (invariant + decode-run row).

**Notes**
- `src/decode/config/settings.py` carries a now-stale inline comment on `sandbox_workspace_dir`
  (lines ~249-251: "The project tree is deliberately NOT mounted … file tools keep working on the real
  tree host-side") that contradicts ADR-0012. Left untouched (this task is docs-only, no `src/` edits);
  flagging it for a follow-up task so a future agent isn't misled by the code comment.
- MODAL note requirements (account-vs-endpoint tokens; bootstrap-upload+export not add_local_dir/mtime;
  `SandboxFilesystem` file ops; host-side hand-back) are covered in the AGENTS.md modal row + `.env.example`
  sandbox block + README. `MODAL_MODELS.md` was intentionally left untouched (outside the declared file
  set; its account-vs-endpoint distinction + sandbox-uses-account-tokens facts stay accurate under ADR-0012).

### [SWE] 2026-07-04 09:12 — Addendum: settings.py comment reconciliation (orchestrator-approved)

Reconciled the flagged stale `sandbox_workspace_dir` comment in `src/decode/config/settings.py` (~248-251)
to ADR-0012 reality: the HOST dir bind-mounted at docker's `/workspace` IS the isolated Workspace (a `git
clone` of `sandbox_repo`, or empty); file tools operate on it THROUGH the backend seam (docker pathlib on
the mount), not "the real tree host-side"; skills are seeded in host-side by `seed_skills`, not a read-only
mount. **Comment-only** — `git diff` confirms only the 4 `#` lines changed (field/type/default `Path(".decode/sandbox")`
untouched). Grep sweep of `src/` (`NOT mounted|real tree host-side|persistent shell|writable scratch|read-only
.*skills|mtime|mirror|marker`): **settings.py only** — every other hit is either "mirror/mirroring" in the
analogy sense or correctly describes the retired ADR-0011 machinery as deleted/replaced (executor.py:23 &
workspace.py:165 say seed_skills "replaces" the read-only mount; the `mtime` hits name the *rejected* design).
Suite re-run green: `uv run pytest tests/unit` → 1394 passed, 0 warnings. Untracked WIP untouched.

### [Tester] 2026-07-04 10:20 — QA

Docs-reconciliation accuracy audit: opened every cited shipped module (078-083) and checked each prose
claim against the code line, not the prose. This is a docs-only change, so the "adversarial pass" is an
accuracy attack — hunting FALSE / over-claimed prose and surviving ADR-0011-era staleness.

**Test summary**
- Format / lint (scoped, per the task's Verify note — `make ci` lints the untracked WIP red): `ruff
  format --check` + `ruff check` on `src/decode/config/settings.py` → both clean.
- Unit tests: 1394 passed / 0 failed — `uv run pytest tests/unit` (`filterwarnings=["error"]`, so a
  clean pass = 0 warnings). Docs+comment can't move it; confirmed unchanged from the SWE run.
- `import decode.cli` → OK (the comment-only settings edit did not break import).

**E2E adversarial pass (accuracy break paths)**
- Happy path (does the diff describe the shipped feature?): read `sandbox/{executor,docker_backend,
  modal_backend,workspace,handback}.py`, `tools/{files,lsp}.py`, `tui/app.py`, `agent/deps.py`,
  `cli.py`, `config/settings.py` → the three invariants + every Testing-E2E row + README + `.env.example`
  match the code. PASS.
- Break 1 (over-claim: HITL auto-ship — the task's flagged FAIL trap): `cli.py` calls
  `_auto_ship_headless` ONLY at the end of the bypass `run()` path (line 633); `_run_hitl` (678-719)
  never calls it. Docs state exactly this ("only for `decode run --repo`, NOT `decode run --hitl --repo`
  … intentionally unwired" — invariant + decode-run row). NOT over-claimed. PASS.
- Break 2 (surviving ADR-0011 staleness): grep `persistent shell|empty scratch|not mounted|mtime|mirror|
  real tree host-side|add_local_dir|writable scratch|skills.*read-only` over the 3 changed docs → every
  hit is (a) the correct ADR-0012 "empty scratch" = the no-repo Workspace, (b) `mirror`/`mtime`/
  `add_local_dir` explicitly named as the REJECTED/retired design ("the rejected alternative was a …
  mirror kept converged by an mtime-delta sync"; "NOT `add_local_dir`-seeded, NOT mtime-synced"), or
  (c) a false positive ("mirrors src/ 1:1", "CLI mirror of flow.replay", skill "read-only passes"). No
  `persistent shell` / `NOT mounted` / `real tree host-side` survives as current behavior. PASS.
- Break 3 (exact-string drift: do the quoted hand-back lines match code?): every quoted `/ship`
  line matches `handback.py` + `tui/app.py` verbatim — `handed the workspace back on branch … (pushed
  to origin).` (h.b.:157), `could not push … push it yourself when ready.` (h.b.:163), `the workspace is
  unchanged from the cloned HEAD, so there is nothing to hand back.` (h.b.:145), `_SHIP_NO_WORKSPACE =
  "Decode - no sandbox workspace to ship."` (app.py:464, the none/no-repo case). PASS (one cosmetic note
  below).
- Break 4 (fabricated cross-refs): every `ADR-0012 §2-9` the docs cite exists in
  `docs/adr/0012-isolated-workspace.md` (Decision §1-9) and is topically correct; "supersedes ADR-0011
  §2,3; retains §1,5-7" matches the ADR header verbatim; the §9 credential-proxy-retained ref is right.
  PASS.
- Break 5 (env↔settings parity / phantom var): all 7 `SANDBOX_*` in `.env.example` match
  `config/settings.py` defaults (MODE=none, IMAGE=ghcr.io/astral-sh/uv:python3.12-bookworm-slim,
  WORKSPACE_DIR=.decode/sandbox, REPO= empty, TIMEOUT_S=600.0, CREDENTIAL_PROXY_ENABLED=false,
  PROXY_IMAGE=mitmproxy/mitmproxy); no undocumented or phantom var. PASS.

**Acceptance criteria**
- [x] PASS — Three invariants match shipped behavior; no stale persistent-shell/empty-scratch-as-fs/
      mtime-sync language remains. Evidence: Sandbox invariant vs `executor.py` (one `SandboxExecutor` +
      `SandboxBackend` seam, fresh-exec) + `docker_backend.py` (bind-mount pathlib, `WorkspaceEscape`) +
      `modal_backend.py` (`SandboxFilesystem` + remote find/grep, bootstrap-upload, NO `add_local_dir`) +
      `files.py` (file tools through the seam; `none` = direct pathlib). Harness-home invariant vs
      `deps.py` (`cwd` = tool scope, `harness_home` = artifact root, defaults to `cwd`). Hand-back
      invariant vs `handback.py` (host-side git only, secure-before-push, skip conditions).
- [x] PASS — Testing-E2E rows accurate. Evidence: launch lines `tui/app.py:1072/1085/1087`; LSP posture
      `tools/lsp.py:111` (modal off) + comment (none/docker on); `web_fetch` host-side/gated unchanged;
      auto-ship `cli.py:633` (+ HITL-not-wired); `/ship` `tui/app.py:478-511`; none-mode line 500-501.
- [x] PASS — README `## Sandboxing` + `--repo` quickstart + hand-back accurate + self-consistent
      (branch naming, URL→remote vs local-path→local origin, never-lose-results, ambient creds — all
      vs `handback.py` + `workspace.py::_git_clone`).
- [x] PASS — `.env.example` documents `SANDBOX_REPO` + "hand-back needs no new var"; all 7 vars match
      `config/settings.py`; stale scratch/persistent-shell/not-mounted comments retired.
- [x] PASS — No `src/` logic change: `git diff src/decode/config/settings.py` is comment-only (field
      `sandbox_workspace_dir: Path = Path(".decode/sandbox")` byte-unchanged), the new comment matches
      ADR-0012 (file tools through the seam; skills seeded by `seed_skills`, not a mount; the dir IS the
      Workspace). `git diff --stat` = exactly the 5 declared files. Suite green (1394, 0 warnings).

**Evidence**
```
$ uv run pytest tests/unit -q
1394 passed in 88.97s (0:01:28)

$ uv run ruff format --check src/decode/config/settings.py && uv run ruff check src/decode/config/settings.py
1 file already formatted
All checks passed!

$ git diff src/decode/config/settings.py | grep -E '^[-+]' | grep -vE '^(\+\+\+|---)' | grep -vE '^[-+][[:space:]]*#'
(empty — every changed line is a comment)

$ git diff --stat
 .env.example | AGENTS.md | README.md | src/decode/config/settings.py | tasks/084-…md   (5 files)
```

**Other issues found (all minor — do NOT block; orchestrator to triage)**
- N1 (cosmetic): the `/ship` row phrases "An **unchanged / non-git** Workspace skips:" then quotes only
  the *unchanged* line. The non-git skip actually emits a different exact string
  (`the workspace is not a git repo with an origin remote, so there is nothing to hand back.`,
  `handback.py:136`). Both are friendly skips and the quoted line is verbatim-correct for the common
  case; the bundling is slightly imprecise. Optional one-word fix.
- N2 (cosmetic): docs use `decode/<session-id>` / `decode/<id>` as the branch placeholder; the shipped
  branch is `decode/<first-8-alnum>` (`handback.py` `_SHORT_ID_LEN = 8`). Reads as a placeholder and the
  message format matches, so not misleading — noting for completeness.
- N3 (follow-up, OUTSIDE 084's approved settings.py scope): `config/settings.py` still carries ADR-0011
  staleness beyond the one approved comment — the section header `# --- Sandboxing (ADR-0011) ---`
  (line 233) and the `sandbox_mode` comment "settings only here — no executor readers yet (they land in
  tasks 072-075)" (lines 234-235). The executors landed (078-083) and ADR-0012 supersedes. Not a defect
  in the delivered change (084's settings edit was orchestrator-scoped to the `sandbox_workspace_dir`
  comment only), but it is exactly the ADR-0011 staleness this feature retires — worth a tiny follow-up
  so a future agent reading settings.py isn't misled.

**VERDICT: PASS**

### [SWE] 2026-07-04 11:05 — Addendum: final settings.py sandboxing-block ADR reconciliation (orchestrator-approved)

Finished the ADR-0011-era staleness sweep the Tester flagged (N3) in the `src/decode/config/settings.py` sandboxing block, comment-only: header `# --- Sandboxing (ADR-0011) ---` → `# --- Sandboxing (ADR-0012; ADR-0011 §1,§5-7 retained) ---`; the false "no executor readers yet (they land in tasks 072-075)" → "the sandbox executor + its docker/modal backends read these settings"; sandbox_repo's false "; no reader yet" dropped (cli.py resolves it, `prepare_workspace` clones — landed 082); and the four stale ADR-0011-era "Read by 072/073 / 073 / 075" task pointers dropped (now covered by the block intro; readers verified: docker/modal backends, modal backend, runtime flow, proxy.py). `git diff` is comment-only (non-`#` grep empty; every `sandbox_*` field/type/default byte-unchanged); scoped `ruff format --check` + `ruff check` clean; `uv run pytest tests/unit` → 1394 passed, 0 warnings. Not committed. Nothing further stale in the block — last settings.py pass.
