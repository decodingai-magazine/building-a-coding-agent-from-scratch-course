---
status: done
feature: isolated-workspace
---

# [PA rejection] docker `SANDBOX_GIT_TOKEN` git-push path has no git in the worker

Tags: `rollup`, `pa-rejection`
Refs: commit `7ba7262`, `docs/adr/0012-isolated-workspace.md` §10, `tasks/085-sandbox-isolated-workspace-capstone.md`

## Decision (human, 2026-07-04) — build **(A) Make it work**

The user chose **resolution (A)**: docker keeps in-sandbox `git push`; make git present on the
proxy-wired docker worker. Do **NOT** implement (B) — every AC below that says "(A) OR (B)" resolves to
**(A)**, and the ADR §10 / README / AGENTS.md / settings prose must keep (not retract) the "docker lets
the model `git push` from inside the sandbox" promise.

Implementation guidance (context the current gate hides):
- **git must install on ALL docker workers, wired and unwired.** The current gate
  `if self._network is None and self._proxy_env is None and self._ca_cert_host_path is None` at
  `docker_backend.py:170-171` (and its lock-in test `test_create_skips_git_install_when_proxy_wired`)
  are the bug — remove/relax them so git + the `SANDBOX_GIT_USER_*` identity land on the proxy-wired
  worker too. That test must be **replaced** with one asserting git IS present on a wired worker.
- **Verify apt-through-proxy on real docker first (it likely just works).** The wired worker has
  `http_proxy`/`https_proxy` → the mitmproxy container, which forwards unconfigured hosts (Debian
  mirrors) as a passthrough, and it already trusts the proxy CA — so `apt-get update && apt-get install
  git` should complete. This machine has a live docker daemon; prove it end-to-end rather than assuming
  the old "can't reach mirrors" premise (the PR-Reviewer flagged that premise as suspect). **Fallback
  if apt-through-proxy proves flaky:** bake git into the resolved worker image (a cached layer,
  symmetric with modal's `apt_install("git")`) — but then check the `ancestor=…-slim` hygiene reap
  filter (it matches descendants, so it should still catch a derived image) and fix any exact-argv
  tests that pin the image id.
- **Keep the worker token-free (do NOT regress the security property).** git in the wired worker pushes
  to `github.com` *through* the proxy (`http_proxy` set); the proxy injects the `github-git` Basic
  header after egress. The token must stay in the proxy's env only — never inject `SANDBOX_GIT_TOKEN`
  into the docker worker to make push work. Both reviewers verified the worker is token-free today; the
  fix must preserve that.
- The empty-token gate fix (AC 4 / issue 4) and the glossary "Worker" note (AC 5 / issue 5) ride in the
  same pass, per the Added Acceptance Criteria.

## Scope

The git-auth follow-on (`7ba7262`) PASSED automated QA + PR-review but fails the user-perspective
acceptance review for the **docker** backend of the "one `SANDBOX_GIT_TOKEN`, push/PR from inside the
sandbox on BOTH backends" promise (user intents 3 + 4).

**Modal is fully correct** — `apt_install("git")` + baked identity + `modal.Secret` GITHUB_TOKEN +
credential helper deliver in-sandbox `git commit` and `git push` end-to-end (proven by
`test_real_modal_workspace_has_git` + `test_real_modal_injects_the_git_token_into_the_sandbox`).

**Docker does not deliver in-sandbox git push**, because the ONE docker configuration that carries the
git token — the auto-engaged Credential Proxy — is exactly the configuration where git is **not**
installed in the worker:

- `SANDBOX_GIT_TOKEN` set + `SANDBOX_MODE=docker` (headless) → `_sandbox_proxy` auto-engages
  (`src/decode/runtime/flow.py:318-320`) and builds a **proxy-wired** `DockerBackend(network=…,
  proxy_env=…, ca_cert_host_path=…)` (`flow.py:343-349`).
- `DockerBackend.create` installs git **only for an unwired worker** —
  `if self._network is None and self._proxy_env is None and self._ca_cert_host_path is None`
  (`src/decode/sandbox/docker_backend.py:170-171`). A proxy-wired worker is skipped.
- The worker image is the slim base, which ships **no git** (the premise of this whole commit).
- So on the proxy path the worker has no `git` binary — a model `git push` (or even `git commit`)
  in `/workspace` fails with `git: command not found`.

Meanwhile `github_token_rules` (`src/decode/sandbox/proxy.py`, the `github-git` rule at
`proxy.py:128-129`) builds a `github.com` **Basic `x-access-token:<PAT>`** header rule *specifically*
for "git push over HTTPS" — a rule that has **no git client to consume it** on docker with the default
image. The Bearer `api.github.com` rule still works via `python`/`curl` (both in the slim base), so
opening a PR through the REST API works; pushing a branch with git does not.

**Net user experience (the exact moment that breaks):** a user reads README:352 ("one git token that
lets the model `git push` its branch … from inside the sandbox on **both** backends … **docker**
(headless) feeds it to the Credential Proxy … so the model can `git push`"), sets `SANDBOX_GIT_TOKEN`,
runs `SANDBOX_MODE=docker decode run --repo <url> "commit and push a branch"`, and the model hits
`git: command not found`. Worse — the *same knob* removes git from the worker: **without** the token,
headless docker installs git (unwired) and `git commit` works; **setting** the token (to enable push)
lands on the proxy-wired worker and silently drops git entirely.

The SWE must resolve this in a single coordinated pass, then hand back to the Tester (full pipeline
re-runs from QA). NOTE: the resolution likely needs a **PA/ADR touch** — it turns on whether docker
in-sandbox git push is supported at all with the default image, which is an ADR-0012 §10 statement, not
just a code tweak. Pick ONE resolution below and make code + all doc surfaces agree; escalate to PA if
the decision is non-obvious.

## Acceptance Criteria

- [x] Decide and implement ONE coherent outcome for headless docker + `SANDBOX_GIT_TOKEN`:
  - **(A) Make it work:** git is present in the proxy-wired docker worker so a model `git push` to
    `github.com` over HTTPS authenticates via the proxy's `github-git` Basic rule end-to-end (e.g. git
    baked into the resolved worker image, or provisioned on a path that the isolated egress network
    actually permits). The `git commit` identity works too. OR
  - **(B) Scope docker to REST-only, honestly:** if in-sandbox git push is not supported on docker with
    the default image, say so everywhere and route docker branch-push to the host-side hand-back (§8,
    which already works). The `github-git` (github.com Basic) rule must not be advertised as
    functional-by-default for docker.
- [x] Setting `SANDBOX_GIT_TOKEN` on docker must NOT *reduce* worker capability: it must never silently
  remove `git` (and thus `git commit`) that the un-tokened headless docker worker has. (Resolution (A)
  fixes this directly; resolution (B) must at minimum document it and keep git-commit available.)
- [x] Fix the false code comment `src/decode/sandbox/docker_backend.py:169` — "the credential-proxy
  path does not need git" is contradicted by the `github-git` git-over-HTTPS rule shipped in the same
  commit. The comment must state the real reason git is absent/present on the proxy path.
- [x] Reconcile every prose surface with the shipped reality (they currently over-promise docker):
  `docs/adr/0012-isolated-workspace.md` §10 (the "github.com git transport" claim for docker), the
  `7ba7262` design intent recorded in `tasks/085` logs, `README.md:352` + `README.md:407`,
  `AGENTS.md` docker row + Credential-Proxy row, and the `settings.py` `sandbox_git_token` comment.
- [x] Add a test that proves the chosen outcome for docker: resolution (A) → a `@skipif`-guarded real
  docker test that git is present in the **proxy-wired** worker (and, ideally, a push authenticates via
  the proxy); resolution (B) → an assertion/doc-test that pins the documented REST-only-on-docker scope
  and the hand-back branch-push path.
- [x] Leave modal, `none` mode, the empty-`SANDBOX_GIT_TOKEN` (injects nothing) behavior, and the
  existing green tests unchanged.
- [x] Tester re-runs the full QA suite and PASSES.
- [ ] PA re-runs acceptance review on the git-auth follow-on and ACCEPTS.

## Issues (detail)

### 1. Proxy-wired docker worker has no git — `git push` from inside the sandbox fails (`docker_backend.py:170-171`, `flow.py:343-349`)
- **What the user experiences (wrong):** with `SANDBOX_GIT_TOKEN` set, a headless docker run asking the
  model to push a branch fails `git: command not found`. Setting the token also removes `git commit`
  that the un-tokened docker worker had. The docker half of "push/PR from inside the sandbox on both
  backends" is not delivered with the default image.
- **What the spec / good UX implies (right):** ADR-0012 §10 promises the docker path supports the
  "github.com git transport" via the auto-engaged proxy; README/AGENTS/commit-message say docker lets
  the model `git push` from inside the sandbox. Either that works, or the docs say plainly that it
  doesn't and route docker branch-push to the (working) host-side hand-back.
- **Suggested fix:** SWE + PA decide (A) vs (B) above. If (A), ensure git reaches the proxy-wired
  worker on a network path that actually works (baking git into the resolved image is the obvious
  candidate, since the comment itself notes apt can't reach Debian mirrors on the isolated egress
  network). If (B), correct all five doc surfaces + the code comment and keep git-commit available.

### 2. `github_token_rules` github.com Basic rule has no consumer on docker (`proxy.py:128-129`)
- **What the user experiences (wrong):** the carefully-built git-over-HTTPS Basic rule is dead weight on
  docker (no git client), so the "just set `SANDBOX_GIT_TOKEN`" GitHub shortcut only half-works there
  (REST PR yes, git push no) with no signal to the user.
- **What the spec / good UX implies (right):** every rule the shortcut builds should be usable, or the
  docs should scope which half applies to docker.
- **Suggested fix:** folded into issue 1's resolution — (A) gives the rule a client; (B) documents the
  REST-only scope for docker.

### 3. Documentation over-promises docker (ADR §10, README:352/407, AGENTS.md docker + proxy rows, settings comment, commit msg)
- **What the user experiences (wrong):** the docs read as "docker: set the token, model pushes from
  inside the sandbox," which is false for the default image.
- **What the spec / good UX implies (right):** docs match shipped behavior exactly (the project's
  documentation-discipline bar; the same honesty the modal-LSP and HITL-`--repo` ceilings get).
- **Suggested fix:** reconcile in the same pass as issue 1, per whichever resolution is chosen.

## User Stories

(Inherit from the feature's intents — no new stories. Re-verify each passes after the fix.)

- **Modal push (must stay green):** `SANDBOX_MODE=modal decode --repo <url>` with `SANDBOX_GIT_TOKEN`
  set → the model `git commit`s and `git push`es a branch from inside the sandbox; the token is in the
  sandbox env by design.
- **Docker push (the fix target):** `SANDBOX_MODE=docker decode run --repo <url>` with
  `SANDBOX_GIT_TOKEN` set → either the model `git push`es a branch from inside the sandbox via the
  proxy (worker still token-free), OR the branch ships via the host-side hand-back and the docs never
  claimed otherwise. In no case does setting the token break in-sandbox `git commit`.
- **Empty token (must stay green):** neither backend injects anything; the strict "no secret in the
  sandbox" invariant holds; `none` mode is byte-identical.

---

Refs: commit `7ba7262`, `docs/adr/0012-isolated-workspace.md` §10

---

## PR Reviewer addendum — 2026-07-04 (independent diff review of `7ba7262`)

The PR Reviewer independently reviewed **only** commit `7ba7262` (all six dimensions + the credential-path
seams) and **corroborates this rejection**. Issues 1–3 above are live at the branch tip (`7ba7262` is the
HEAD of `feat/isolated-workspace`, local + origin; no later commit touches `docker_backend.py`). The
proxy-wired docker worker ships no `git`/`gh` binary — the skip at `src/decode/sandbox/docker_backend.py:170-171`
is locked in by `test_create_skips_git_install_when_proxy_wired` — so ADR-0012 §10's in-sandbox
`git push` promise is undeliverable on docker with the default image, and the `github-git` (github.com
Basic) rule from `github_token_rules` has no consumer there. **No duplicate rollup filed** — this is the
active rollup for that Blocker.

Verified clean (no finding): docker worker stays token-free (only `network`/`proxy_env`/`ca_cert_host_path`
reach it; the map rides the proxy `--env-file`, never worker argv/env); modal token rides `modal.Secret`
runtime env, not a cached image layer (helper is single-quoted, reads `$GITHUB_TOKEN` at push time);
`github_token_rules` Basic = `base64("x-access-token:<PAT>")`, `api.github.com` precedes `github.com`
(first-match parent-domain in `proxy_addon._match_host`); literal headers → no Kitaru fetch; no secret is
logged; git-install is best-effort and does not hang the proxy-isolated worker. Tests for the rule builder,
gate, and real-infra smokes are non-vacuous.

Two **net-new findings** this rollup does not yet cover — fix in the same coordinated pass:

### 4. [Nit — corrects AC below] Explicit-empty `SANDBOX_GIT_TOKEN=` ENGAGES the docker proxy (does NOT "inject nothing")

- **What's wrong:** `_sandbox_proxy` gates on `settings.sandbox_git_token is not None`
  (`src/decode/runtime/flow.py:316` + prepend at `:337-339`). An explicitly-empty `SANDBOX_GIT_TOKEN=`
  parses to `SecretStr("")` (verified empirically — `env_ignore_empty` is unset in `settings.py:97`), which
  is `not None`, so the docker proxy **auto-engages** and `github_token_rules("")` injects garbage empty
  `Authorization: Bearer ` / `Basic base64("x-access-token:")` headers on `api.github.com` / `github.com`
  (a malformed empty Bearer can 401 an otherwise-anonymous call). Modal's `if token:` (value emptiness,
  `modal_backend.py`) correctly injects nothing — so the backends diverge and the `.env.example` / ADR §10
  "empty ⇒ inject nothing on either backend" claim is FALSE for docker. Only the *unset* default (`None`)
  behaves; an operator who writes `SANDBOX_GIT_TOKEN=` hits it.
- **Why it matters here:** AC "Leave … the empty-`SANDBOX_GIT_TOKEN` (injects nothing) behavior … unchanged"
  assumes docker already injects nothing on empty — it does not; preserving today's docker behavior would
  preserve the bug.
- **Suggested fix:** gate on the resolved *value*, mirroring modal — resolve
  `token = settings.sandbox_git_token.get_secret_value() if settings.sandbox_git_token else ""` and use
  `bool(token)` for both `proxy_wanted` and the `github_token_rules` prepend.
- **Regression test:** add a gate case with `git_token=SecretStr("")` asserting the docker proxy stays down
  (mirrors `test_sandbox_proxy_is_a_noop_...` in `tests/unit/decode/runtime/test_sandbox_proxy.py`).

### 5. [Nit · doc-discipline → PA] Glossary "Worker" invariant contradicted by the modal token path

- **What's wrong:** `docs/glossary.md` "Worker" entry states, universally over "the Docker container or the
  Modal sandbox", that "no token ever enters it; the resolved map lives only in the proxy container."
  ADR-0012 §10's modal path deliberately injects `GITHUB_TOKEN` into the modal sandbox env — the ADR is
  honest about this ("readable by a prompt-injected agent"), but the glossary invariant was not updated.
- **Suggested fix (PA):** note the opt-in modal `SANDBOX_GIT_TOKEN` exception on the "Worker" glossary
  entry so the ubiquitous-language definition matches shipped behavior. Non-blocking on its own.

## Added Acceptance Criteria

- [x] Docker explicit-empty `SANDBOX_GIT_TOKEN=` (`SecretStr("")`) engages nothing and injects no headers —
  matching modal — covered by a new gate test. (Supersedes the assumption in the AC "Leave … the
  empty-`SANDBOX_GIT_TOKEN` (injects nothing) behavior … unchanged".)
- [x] `docs/glossary.md` "Worker" entry notes the modal `SANDBOX_GIT_TOKEN` in-sandbox-token exception (PA).
- [ ] PR Reviewer re-runs on the re-pushed commit and reports `NO BLOCKERS`.

Refs: PR #25, commit `7ba7262`

---

## Log

### [SWE] 2026-07-04 18:20 — Implementation (resolution A: git on every docker worker)

Built resolution **(A)** as locked by the human Decision — git now installs on the proxy-wired docker
worker too, so a model `git push` to github.com authenticates via the proxy's `github-git` Basic rule
end to end, worker still token-free. Rode Nit 4 (empty-token gate) and Nit 5 (glossary) in the same pass.

**Verified apt-through-proxy works on REAL docker — no image-bake fallback needed.** The wired worker's
`apt-get update && apt-get install git` completes: its `http_proxy` points at mitmproxy, which
passthrough-forwards the Debian mirrors (unmatched host → no header injected), and the proxy CA is
already trusted before apt runs. Proven by the new `@skipif` real-docker test **and** a manual e2e that
ran a real `git commit` in a live proxy-wired worker.

**Files modified**
- `src/decode/sandbox/docker_backend.py` — removed the `if self._network is None and …` gate so
  `_install_git` runs on **every** worker (the proxy-wired one included); replaced the false comment
  (`docker_backend.py:169`, "the credential-proxy path does not need git") with the real reason (the
  worker's `git push` consumes the proxy's Basic rule; apt reaches the mirrors through the proxy);
  updated the module + `_install_git` docstrings.
- `src/decode/runtime/flow.py` — Nit 4: `_sandbox_proxy` now resolves the token to a **value** and gates
  `proxy_wanted` + the `github_token_rules` prepend on `bool(token)` (mirroring modal's `if token:`), so
  an explicit `SANDBOX_GIT_TOKEN=` (`SecretStr("")`) no longer engages the proxy / injects empty headers.
- `src/decode/config/settings.py` — reconciled the `sandbox_git_token` comment (non-empty auto-engage;
  git installed into the token-free worker; explicit-empty injects nothing).
- `docs/glossary.md` — Nit 5: "Worker" entry now notes the opt-in modal `SANDBOX_GIT_TOKEN` in-sandbox
  exception, and that docker keeps the worker token-free even then.
- `docs/adr/0012-isolated-workspace.md` §10 — noted git is installed into the proxy-wired worker (so the
  Basic github.com rule has a client) + the non-empty/explicit-empty gate.
- `README.md` (:352 `SANDBOX_GIT_TOKEN` bullet, :407 Credential-Proxy GitHub shortcut) and `AGENTS.md`
  (Credential-Proxy row) — same reconciliation; the "docker git push from inside works" promise is
  **kept** (now true) per the Decision.
- `tests/unit/decode/sandbox/test_docker_backend.py` — **replaced** the bug-locking
  `test_create_skips_git_install_when_proxy_wired` with `test_create_installs_git_on_a_proxy_wired_worker`
  (git IS installed after CA trust); updated `test_create_trusts_the_ca_synchronously_on_the_proxy_path`
  for the added git-install spawn.
- `tests/unit/decode/runtime/test_sandbox_proxy.py` — Nit 4 regression:
  `test_sandbox_proxy_is_a_noop_in_docker_mode_with_an_empty_git_token`.
- `tests/integration/test_credential_proxy.py` — new `@skipif` real-docker test
  `test_proxy_wired_worker_has_git_and_still_holds_no_token` (git present + identity configured in the
  proxy-wired worker; secret absent from its env).

**Tests**
- Unit: 1412 passing, 0 failing (`uv run pytest tests/unit`).
- Integration (real infra, this machine has a live docker daemon + modal creds):
  - `tests/integration/test_credential_proxy.py` — 5 passing (incl. the new proxy-wired git test).
  - `test_real_docker_workspace_has_git`, `test_real_docker_isolated_workspace_roundtrip_and_handback` — 2 passing (default/unwired path unchanged).
  - `test_real_modal_workspace_has_git`, `test_real_modal_injects_the_git_token_into_the_sandbox`, `test_real_modal_isolated_workspace_roundtrip` — 3 passing (modal unchanged).
- Cost hygiene after every real run: 0 leaked workers / proxy containers / networks; `modal.Sandbox.list()` → 0.

**Acceptance criteria**
- [x] Resolution (A) implemented — git on the proxy-wired worker; push authenticates via the proxy.
- [x] `SANDBOX_GIT_TOKEN` no longer removes git from the docker worker (it installs on all workers).
- [x] False comment `docker_backend.py:169` fixed.
- [x] Prose reconciled: ADR §10, README:352/407, AGENTS.md, `settings.py` comment (promise kept, now true).
- [x] Test proving (A): real-docker `test_proxy_wired_worker_has_git_and_still_holds_no_token`.
- [x] modal / `none` / empty-token / existing green tests unchanged (verified by real-infra runs).
- [x] Nit 4: explicit-empty `SANDBOX_GIT_TOKEN=` engages nothing (new gate test).
- [x] Nit 5: glossary "Worker" entry notes the modal exception.
- [ ] [Tester] full QA re-run + PASS.
- [ ] [PA] acceptance re-review + ACCEPT.
- [ ] [PR Reviewer] re-run on the re-pushed commit → `NO BLOCKERS`.

**Evidence**
```
$ uv run pytest tests/unit -q
1412 passed in 100.54s

$ uv run pytest tests/integration/test_credential_proxy.py -v
test_worker_request_arrives_with_injected_header_but_worker_holds_no_secret PASSED
test_worker_trusts_the_proxy_ca_on_its_very_first_command                   PASSED
test_proxy_wired_worker_has_git_and_still_holds_no_token                     PASSED
test_sandbox_proxy_context_installs_the_seam_then_tears_it_all_down          PASSED
test_sandbox_proxy_context_tears_down_even_when_the_body_raises              PASSED
5 passed in 70.91s

$ uv run pytest ...test_real_docker_workspace_has_git ...roundtrip_and_handback -v
2 passed in 11.31s
$ uv run pytest ...test_real_modal_workspace_has_git ...injects_the_git_token... ...roundtrip -v
3 passed in 22.43s

# Manual e2e — a REAL git commit in a live proxy-wired worker, token-free:
[sandbox] docker start … (proxy-wired)
[sandbox] proxy CA trusted in worker …
[sandbox] git installed in worker …
git --version -> 0 git version 2.39.5
git commit  -> 0 85a6a27 e2e commit
secret leaked into worker env? False
worker routed via proxy? True

# Cost hygiene (after all real runs): workers/proxy/nets all empty; modal Sandbox.list() count: 0
```

**Notes**
- The premise the old comment/skip encoded ("apt can't reach Debian mirrors on the isolated egress
  network") is **false** for this topology — the mitmproxy proxy passthrough-forwards the mirrors and the
  CA is trusted first. Verified live rather than assumed, per the Decision guidance. Fallback (bake git
  into the resolved image) was NOT needed, so the `ancestor=…-slim` reap filter + exact-argv image tests
  stay valid untouched.
- `tasks/085` "design intent" logs are **append-only** review records already routing to this rollup;
  they were not rewritten (that would falsify history). Shipped reality is reconciled in the durable docs.
- Left `tasks/085`'s pre-existing unstaged PR-Reviewer log entry and untracked `docs/notes/` untouched;
  did not recreate the deleted `substack_summarizer.py`. Not committed — Tester goes first.

### [Tester] 2026-07-04 19:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` — format-check → lint-check → 1412 unit tests, all green).
- Unit tests: 1412 passed / 0 failed.
- Integration tests: 97 passed / 0 failed / 0 skipped (real docker + real modal both ran — daemon up, `~/.modal.toml` profile active).
- Warnings: 0 (`filterwarnings=["error"]` holds).

**E2E adversarial pass** (drove real docker myself; did not take the SWE's word)
- Happy path: proxy-wired docker worker via the REAL `github_token_rules(FAKE_PAT)` map (the actual
  `SANDBOX_GIT_TOKEN` shortcut path — the SWE's test used a bespoke rule) → `git --version` = `git version
  2.39.5`, exit 0; identity `decode`/`decode@localhost`. (PASS — apt-through-proxy genuinely installed git,
  not a silent no-op.)
- Break path 1 (**security — load-bearing**): `docker exec <worker> env` on the proxy-wired worker → raw PAT
  ABSENT, base64 `x-access-token:` Basic ABSENT, `http_proxy=` present; the resolved credential IS in the
  proxy container's env. Worker token-free on the github-rules path, injection real. (PASS)
- Break path 2 (**empty-token gate, three states**): `_sandbox_proxy` in docker mode, flag off — `None` →
  no-op, `SecretStr("")` → no-op (no proxy, no garbage headers), real value → engages with hosts
  `['api.github.com','github.com']`. Also verified empirically `SANDBOX_GIT_TOKEN=` parses to `SecretStr("")`
  (`env_ignore_empty` unset), so the Nit-4 premise is real and `bool(token)` fixes it. (PASS)
- Break path 3 (**capability regression**): un-tokened/unwired headless docker worker → `git --version` exit 0,
  identity set. Setting the token does not remove git; the gate removal installs it on every worker. (PASS)
- Break path 4 (**corner: flag ON + empty token**): proxy engages via the flag but builds an EMPTY map — zero
  github rules, zero `Bearer `/`x-access-token:` garbage. (PASS)
- Cost hygiene after every real run: workers / `decode-proxy-` / `decode-sandbox-net-` all 0; `modal.Sandbox.list()` → 0.

**Acceptance criteria**
- [x] PASS — Resolution (A): git in the proxy-wired worker; push auth via proxy Basic rule; commit identity works.
      Evidence: Break path 1 (real docker, git 2.39.5 + identity, worker token-free); `docker_backend.py:177`
      unconditional `await self._install_git(...)`; `test_create_installs_git_on_a_proxy_wired_worker` +
      `test_proxy_wired_worker_has_git_and_still_holds_no_token` PASS. (Literal push to github.com needs a live
      PAT/repo — out of hermetic scope; injection-on-egress is proven by
      `test_worker_request_arrives_with_injected_header_but_worker_holds_no_secret` composed with git-present + Basic-rule-in-proxy-map.)
- [x] PASS — Setting `SANDBOX_GIT_TOKEN` never removes git. Evidence: Break path 3 (unwired still gets git) +
      Break path 1 (wired gets git); `_install_git` runs on every worker.
- [x] PASS — False comment `docker_backend.py:169` fixed. Evidence: now states the real reason (git push
      consumes the proxy `github-git` Basic rule; apt reaches mirrors through the proxy), `docker_backend.py:169-176`.
- [x] PASS — Prose reconciled. Evidence: diff updates ADR §10, README:352/407, AGENTS.md Credential-Proxy row,
      `settings.py` comment, glossary; AGENTS.md docker row was already unconditionally true under (A) (no edit
      needed); `tasks/085` review logs are append-only (correctly not rewritten). All match the Break-path reality.
- [x] PASS — Test proving (A) on docker. Evidence: `test_proxy_wired_worker_has_git_and_still_holds_no_token` (real docker) PASS.
- [x] PASS — modal / none / empty-token / existing tests unchanged. Evidence: only `settings.py`, `flow.py`,
      `docker_backend.py` touched (modal_backend.py + none-mode exec.py untouched); 3 real-modal tests +
      none-mode byte-identical unit test green; 1412 unit + 97 integration all pass.
- [x] PASS — Added AC: explicit-empty `SANDBOX_GIT_TOKEN=` engages nothing / injects no headers. Evidence:
      Break path 2 + Break path 4 + `test_sandbox_proxy_is_a_noop_in_docker_mode_with_an_empty_git_token` PASS.
- [x] PASS — Added AC: glossary "Worker" notes the modal in-sandbox-token exception. Evidence: `docs/glossary.md`
      Worker entry now carries the opt-in modal exception + "docker keeps the worker token-free even then."

**Evidence**
```
$ make pre-commit
... 1412 passed in 99.21s ...           (format-check + lint-check ran first, both clean)

$ uv run pytest tests/integration -p no:randomly -q
97 passed in 352.68s (0:05:52)

# adversarial real-docker (github_token_rules path):
[git --version] exit=0 out='git version 2.39.5'
[identity] name='decode' email='decode@localhost'
[worker env] raw_PAT_leaked=False  basic_leaked=False  http_proxy_set=True
[proxy env] token_present_in_proxy=True
[unwired git] exit=0 identity='decode'
[gate] None->False  Secret('')->False  real->engages hosts=['api.github.com','github.com']
[corner flag-ON+empty] engaged=True hosts=[] github-garbage=False

# hygiene after all real runs: workers=0 proxy=0 nets=0 modal.Sandbox.list()=0
```

**Other issues found**
- None blocking. Note (non-blocking): `_install_git` runs unconditionally even for a custom `SANDBOX_IMAGE`
  that already bakes git — it re-runs `apt-get update && apt-get install git` (a fast no-op when git is
  present), so the "bake git to skip it" docstring line is slightly imprecise (it de-facto shortens, not
  skips, the step). Cosmetic; not an AC. Orchestrator's call whether to file a follow-up.

**VERDICT: PASS**
