---
title: "Sandboxing methods — decode vs the reference harnesses (+ Codex)"
created: 2026-07-04
purpose: >
  The core sandboxing/isolation methods, framed per harness, cross-referenced
  between decode's implementation (ADR-0011 + ADR-0012) and the research wiki.
grounded_in:
  - research-coding-agent-from-scratch/wiki/notes/execution-isolation-models.md
  - research-coding-agent-from-scratch/wiki/comparisons/isolation-claude-code-vs-opencode.md
  - research-coding-agent-from-scratch/wiki/repos/claude-code/SANDBOX.md
  - research-coding-agent-from-scratch/wiki/repos/pi/SANDBOX.md
  - research-coding-agent-from-scratch/wiki/repos/opencode/PERMISSION_ISOLATION.md
  - research-coding-agent-from-scratch/raw/how-openai-codex-works.md
decode_refs: [docs/adr/0011-sandboxing-and-credential-proxy.md, docs/adr/0012-isolated-workspace.md]
note: >
  claude-code / opencode / pi are deep-dived from source in the wiki; Codex is
  inferred from the ByteByteGo overview (architecture-level, not a code audit).
---

# Sandboxing methods — decode vs the reference harnesses (+ Codex)

The intersection of decode's shipped sandboxing methods with the research wiki's
taxonomy, framed per harness. The wiki reduces "sandbox" to **two orthogonal axes**
(`wiki/notes/execution-isolation-models.md`):

- **Axis A — where the command runs:** this machine vs. another machine.
- **Axis B — is there a kernel boundary:** raw `spawn` vs. an OS jail (Seatbelt / bubblewrap / container / microVM).

Every method below is a move on that grid. The three points on it: **① direct** (no boundary),
**② local sandbox** (boundary co-located on your host), **③ remote** (another machine) — where ③
further splits into **③a relocate the agent**, **③b relocate the server**, **③c swap the backend**.

---

## At a glance — methods × harnesses

| Method | claude-code | opencode | pi | Codex | **decode (ours)** |
|---|---|---|---|---|---|
| **1. Policy vs. enforcement** | policy (`canUseTool`) **+** OS jail | policy only (no enforce layer) | `tool_call` hook **+** external jail | prompt rules **+** bidirectional App-Server approval | permission gate (policy) **+** container (enforce) |
| **2. none/local/remote ladder** | ② default · ① opt-out · ③a | **① only** · ② none · ③b | ① default · ② opt-in · ③c | ② + ③ (per-task cloud container) | `SANDBOX_MODE` = none(①) / docker(②) / modal(③c) |
| **3. Enforcement bet** | **PREVENT** (jail blocks) | **RECOVER** (git-shadow snapshots / undo) | **EXTERNALIZE** (`nono`/container) | prevent-by-container + recover-by-PR | **EXTERNALIZE + RECOVER** (container is the jail; git hand-back ships) |
| **4. I/O placement** | **MASK** (jail bash; files stay host-side) | (no jail; direct) | **SWAP THE SET** (`ExecutionEnv` = FS & Shell) | all tools run *in* the container | **SWAP THE SET** (`SandboxBackend` seam, task 081) |
| **5. Remote family** | ③a relocate the **agent** | ③b relocate the **server** | ③c swap the **backend** | ③a relocate (App Server in cloud) | ③c swap the backend (modal) |
| **6. Untrusted-remote quadrant** | — (wiki's open gap) | — (open gap) | — (open gap) | **FILLS:** cloud sandbox + repo clone + PR | **FILLS:** docker/modal + `--repo` + host-side git hand-back |
| **(egress / secrets)** | filtering proxy + allowlist + ask-on-miss | — | ext-only domain allowlist | (contained inside the cloud) | credential proxy (**injects** after egress) + host-side git (no cred in sandbox) |

Reading it: **dimension ② is where the reference repos diverge hardest** (claude-code makes it default,
pi opt-in, opencode declines it). **The untrusted-remote quadrant (row 6) is the gap the wiki says all
three leave open** — Codex and decode are its two fillers.

---

## Per-method detail

### 1. Separate policy from enforcement
The wiki's #1 builder takeaway: allow/deny/ask is *advisory*; the jail is the boundary. claude-code says
it in code — `excludedCommands` "is a convenience, NOT a security boundary."

- **claude-code:** Layer 1 `canUseTool` (policy) + Layer 2 `wrapWithSandbox` (OS enforcement).
- **opencode:** policy only (ask/allow/deny + TCC protected-path list); no enforcement layer.
- **pi:** `tool_call` hook = policy (can block); enforcement externalized out of core.
- **Codex:** sandbox permission rules ride in the prompt; the App Server pauses mid-task and asks the client allow/deny. Policy explicit; the cloud container is enforcement.
- **decode:** permission gate (allow/ask/deny modes, ADR-0002) = policy; the container = enforcement.

### 2. The none / local / remote ladder
- **claude-code:** ① opt-out only · **② default** (Seatbelt/bwrap) · ③a remote.
- **opencode:** **① default & only** · ② none by design · ③b remote.
- **pi:** **① default** · ② opt-in (`nono` / sandbox ext) · ③c remote.
- **Codex:** ② + ③ — a per-task *cloud* container (kernel boundary on another machine).
- **decode:** the ladder as config — `SANDBOX_MODE=none`(①) / `docker`(② local container) / `modal`(③c remote container).

### 3. The enforcement bet: prevent vs. recover vs. externalize
Spine of `wiki/comparisons/isolation-claude-code-vs-opencode.md`.

- **claude-code = PREVENT:** OS jail blocks the irreversible (exfiltration, out-of-repo writes) at the kernel.
- **opencode = RECOVER:** git-shadow snapshots undo bad edits — but "undo reverses local writes, never egress."
- **pi = EXTERNALIZE:** no jail in core; run *inside* `nono`/a container — the deployment env is the jail.
- **Codex = PREVENT-by-container + RECOVER-by-git:** the cloud sandbox is the boundary; it proposes a *PR* you review before merge.
- **decode = EXTERNALIZE + RECOVER:** like pi, the container *is* the jail (we ship no seccomp); like opencode's git idea, we add a git-recovery substrate — but repurposed from "undo" to "**ship**" (the `decode/<id>` hand-back; the merge is the real gate).

### 4. I/O placement: mask one fs vs. swap the whole backend "as a set"
The most directly-lifted method — pi's headline insight (`wiki/repos/pi/SANDBOX.md §3`).

- **claude-code = MASK:** the jail wraps the *bash* process; `read`/`write`/`edit` stay on the host — one fs, masked. Remote just relocates the agent so the mask co-locates.
- **opencode:** no jail; direct on the host.
- **pi = SWAP THE SET:** `ExecutionEnv = FileSystem & Shell`; override `read+write+edit+bash` *together*. The wiki's warning: "remoting only bash leaves the agent reading local files about a remote tree."
- **Codex:** moot — the *whole* toolset runs inside the container (relocated), so there's no host/sandbox split to reconcile.
- **decode = SWAP THE SET:** pi's pattern, implemented. Our `SandboxBackend` seam carries exec **+** file ops; task 081 routes `read/write/edit/glob/grep` through it into the Workspace. We explicitly **rejected** the "mirror the sandbox fs to the host" alternative — the deletion-blindness hole.

### 5. Remote family: relocate the executor vs. swap the backend
`execution-isolation-models.md §③` splits remote into two families.

- **claude-code = ③a relocate the AGENT** (headless loop on the remote box; jail travels).
- **opencode = ③b relocate the SERVER** (client/server split; unjailed on the far side).
- **pi = ③c swap the BACKEND** (agent local; `BashOperations`/`ExecutionEnv` retargets over SSH/container).
- **Codex = ③a-family relocate:** the App Server (all core logic) runs *in* the cloud container; the VS Code/web client is thin.
- **decode = ③c swap** (pi's family) for modal — harness stays on your machine, exec + file ops delegate to the remote sandbox. **The contrast: Codex relocates the brain into the cloud; decode keeps the brain local and swaps the hands.**

### 6. The untrusted-remote quadrant: container + repo clone + git hand-back
The wiki is explicit that **all three reference repos leave this open** — "neither per-command
Seatbelt/bwrap nor undo-only snapshots fully isolate a host you don't trust — that's where containers
(Docker), microVMs (Firecracker), gVisor enter."

- **claude-code / opencode / pi:** don't ship it.
- **Codex FILLS IT:** "each task runs in its own isolated cloud sandbox, preloaded with your repository"; a worker provisions the container with the checked-out repo, runs the App Server, streams to the browser, state on the server → proposes a PR.
- **decode FILLS IT:** container (docker/modal) + `--repo` clone into `/workspace` + host-side git hand-back (`decode/<id>` branch). Our **modal** mode is the direct analog of Codex's cloud sandbox.
  - **Divergence:** Codex runs the agent *inside* the cloud and proposes the PR *from there*; decode swaps the backend to reach the container (pi-style) and does the git push **host-side — no credential ever enters the sandbox** (ADR-0012 §8). Plus the credential proxy (mitmproxy injects the token *after* egress) — a cousin of claude-code's filtering-proxy egress mediation, inverted from *filter* to *inject*.

---

## Synthesis

The wiki predicted a quadrant its three reference harnesses don't reach — **kernel boundary + another
machine + untrusted code**. **Codex and decode are the two fillers of that gap**, and they split on
exactly two axes the wiki names:

1. **Remote family** — Codex relocates (③a) the whole agent into a cloud container; decode swaps the
   backend (③c, pi-style) into docker/modal.
2. **Git role** — Codex proposes a PR from *inside* the cloud; decode pushes a branch **host-side** so
   secrets never enter the sandbox.

Everything else decode built is a recombination of the reference harnesses: pi's "swap the set" (Method 4)
+ claude-code's "policy ≠ enforcement" (Method 1) + opencode's "git as a safety substrate" (Method 3),
with containers/modal filling the untrusted-remote quadrant.
