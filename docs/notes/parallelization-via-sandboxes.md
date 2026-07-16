# Parallelization via sandboxes

**Question:** Can a *single harness* manage multiple jobs in parallel by driving multiple
sandboxes — instead of the current one-sandbox-per-session model?

**Short answer:** Mechanically yes. The sandbox *primitives* already support N isolated
sandboxes. The *wiring* does not — the executor is a process-global singleton and the Workspace
path is fixed, so today one process = one sandbox, structurally. Two roads get you to parallel
jobs: **A) orchestrate N Kitaru executions** (with the design grain, zero sandbox refactor), or
**B) refactor the executor onto `AgentDeps`** and fan out N sandboxes inside one process (against
the grain, more control).

---

## Where things stand today

### The seam is single-lane by construction

`bash` and the file/search tools run through **one** `CommandExecutor`, selected once per process
and memoized in a module global:

```
src/decode/tools/bash.py
  43   _EXECUTOR: CommandExecutor = LocalExecutor()   # module-global singleton
  44   _executor_selected = False
  63   _get_executor()      # memoizes exactly ONE executor for the whole process
 158   active_backend(cwd)  # file tools bridge to the SAME memo
```

`SandboxExecutor` and both backends are explicitly **not** safe for concurrent `exec`/`run` on one
instance (`sandbox/executor.py:116`, `docker_backend.py:77`, `modal_backend.py:107`). One session =
one container / one remote sandbox = one serial lane.

### The one place that already fans out — and shares the single sandbox

The `agent` tool (`tools/agent.py:375`) already runs N children concurrently through one harness:
`asyncio.gather` over N spawns, bounded by a per-loop semaphore (`subagent_max_parallel`). But every
child gets the parent's scope:

```
src/decode/tools/agent.py
 475   cwd=ctx.deps.cwd,               # SAME as parent
 476   harness_home=ctx.deps.harness_home,
```

Children are read-only (`read/glob/grep/lsp`, no `bash`), all reading the **one** session sandbox.
So today's fan-out = N concurrent *readers*, ONE Workspace. Not N isolated jobs.

### Three concrete blockers to N isolated sandboxes in one process

1. **Executor is a process-global singleton** (`bash.py:43`). Every `bash` + file op routes through
   the one memo. One process ⇒ one sandbox.
2. **Workspace path is fixed** (`workspace.py:27`): `workspace_dir(harness_home)` =
   `harness_home/.decode/sandbox`, a single path. N jobs bind-mounting the same host dir collide.
3. **Mode is process-wide**: `settings.sandbox_mode` is read once and applies to everything.

### But the primitives already support N sandboxes

- Backends are plain per-instance objects; **construction is inert**, zero shared state between
  instances (`executor.py:121`, `docker_backend.py:80`). `SandboxExecutor(DockerBackend())` × N is
  fine.
- Each `.create()` is its own `docker run` container / own remote Modal sandbox — real isolation.
- The "not safe for concurrent exec on ONE instance" rule is **satisfied** by giving each job its
  own instance: serialize *within* a job, parallelize *across* jobs — exactly the `gather` shape
  `agent.py` already uses.
- Precedent: `active_executor()` (`bash.py:83`) exists specifically so the eval harness can warm and
  drive an executor per-run from outside the tool path (though still through the global, so it
  serializes).

---

## Current model vs. the target

```
 TODAY — one sandbox per harness process
 ┌──────────────────────────── harness process ────────────────────────────┐
 │                                                                          │
 │   agent loop ──▶ bash / files / lsp ──▶ _get_executor()  (module global) │
 │                                              │                           │
 │                                              ▼                           │
 │                                    ONE SandboxExecutor                    │
 │                                              │                           │
 │                                              ▼                           │
 │                                   ONE Workspace  (.decode/sandbox)        │
 │                                              │                           │
 │                                              ▼                           │
 │                                   ONE container / remote sandbox          │
 └──────────────────────────────────────────────────────────────────────────┘


 TARGET — one harness, N isolated jobs
 ┌──────────────────────────── harness process ────────────────────────────┐
 │                          orchestrator / gather                            │
 │            ┌───────────────────┼───────────────────┐                     │
 │            ▼                   ▼                   ▼                      │
 │        job A deps          job B deps          job C deps                 │
 │      (own executor)      (own executor)      (own executor)               │
 │            │                   │                   │                      │
 │            ▼                   ▼                   ▼                      │
 │      Workspace A           Workspace B          Workspace C               │
 │   .decode/sandbox/A     .decode/sandbox/B     .decode/sandbox/C           │
 │            │                   │                   │                      │
 │            ▼                   ▼                   ▼                      │
 │      container A           container B          container C               │
 └──────────────────────────────────────────────────────────────────────────┘
```

The gap between the two pictures is precisely blockers 1–2: move the executor off the module global
onto per-job state, and give each job a distinct Workspace directory.

---

## Road A — orchestrate N Kitaru executions (with the grain)

Each `decode run` is already a self-contained durable flow execution (`runtime/flow.py:298`
`run_agent_task`) that spins up **its own** sandbox and Workspace clone. Parallelizing = one
controller submitting N executions and gathering results. No sandbox refactor at all.

```
                        ┌─────────────────────────┐
                        │   controller / harness  │
                        │   (submit + gather)     │
                        └────────────┬────────────┘
             ┌───────────────────────┼───────────────────────┐
             ▼                       ▼                       ▼
      ┌────────────┐          ┌────────────┐          ┌────────────┐
      │  flow exec │          │  flow exec │          │  flow exec │
      │  (proc /   │          │  (proc /   │          │  (proc /   │
      │  Modal ctr)│          │  Modal ctr)│          │  Modal ctr)│
      │            │          │            │          │            │
      │  sandbox A │          │  sandbox B │          │  sandbox C │
      │  ws clone  │          │  ws clone  │          │  ws clone  │
      └─────┬──────┘          └─────┬──────┘          └─────┬──────┘
            │  checkpoints           │  checkpoints           │
            ▼                        ▼                        ▼
        durable state            durable state            durable state
        (replay / fork)          (replay / fork)          (replay / fork)
```

On a remote stack this is two nested Modal-App layers already (`runtime/modal_app.py:32`):
`decode-<env>` for the flow containers, `decode-sandbox-<env>` for the bash sandboxes each flow
spawns inside itself.

**Pros**

- **Zero sandbox refactor** — the isolation, per-job Workspace, and clone are already there.
- **Durability for free** — each job checkpoints, so a crash replays finished turns (`flow.py:325`);
  fork/replay/cohort are already an operator surface (`flow.py:558`, kitaru-replay-ops skill).
- **Hard isolation** — separate processes/containers; one job's crash or OOM cannot touch a sibling
  (ZenML terminates sandboxes by id, never the shared App — `modal_app.py:14`).
- **Scales past one machine** — remote stack fans out to Modal; not bounded by one host's cores/RAM.
- **HITL, hand-back, secrets already wired** per execution (`flow.py:190`, ADR-0016).

**Cons**

- **No shared in-process state** — jobs cannot share a warm model client, an in-memory cache, or a
  Python object graph. Coordination is out-of-band (artifacts, the Kitaru store).
- **Per-job overhead** — each execution pays flow start-up + image build/reuse + a fresh sandbox
  create (git+gh install, ~20 s on docker per session — `docker_backend.py:290`).
- **Heavier ops surface** — you are running the Kitaru/Modal stack (`DECODE_ENV != local`), not a
  single laptop process; more moving parts to stand up and watch.
- **Coarse granularity** — the unit of parallelism is a whole run, not a task inside a run. Fine for
  "run 20 tasks", awkward for "one agent that internally forks 3 workers and merges them".

**Best for:** batch/independent jobs — evals, a queue of tasks, cohort what-ifs — where each job is
a full agent run and you want durability + cross-machine scale.

---

## Road B — executor on `AgentDeps`, fan out inside one process (against the grain)

Move the executor off the module global and onto `AgentDeps` (where `cwd`, `gate`, resolvers already
live), give each job a distinct Workspace dir, then `gather` N agent runs in one event loop.

### Sketch of the change

```
# tools/exec.py / agent/deps.py — carry the executor as per-job state
@dataclass
class AgentDeps:
    cwd: Path
    harness_home: Path
    executor: CommandExecutor          # NEW: per-job, not a module global
    workspace: Path                    # NEW: per-job .decode/sandbox/<job-id>
    ...

# tools/bash.py — read the executor from deps, not the memo
async def bash(ctx, command, timeout=None):
    ...
    result = await ctx.deps.executor.run(command, cwd=ctx.deps.cwd, timeout_s=timeout_s)

# workspace.py — parametrize the path per job
def workspace_dir(harness_home: Path, job_id: str) -> Path:
    return (harness_home / settings.sandbox_workspace_dir / job_id)
```

### How a run looks

```
  one asyncio event loop
  ────────────────────────────────────────────────────────────
   gather([ run(job A), run(job B), run(job C) ])
        │            │            │
        ▼            ▼            ▼
   deps.executor  deps.executor  deps.executor     ← three distinct instances
        │            │            │
        ▼            ▼            ▼
   Workspace/A    Workspace/B    Workspace/C        ← distinct paths, no collision
        │            │            │
        ▼            ▼            ▼
   container A    container B    container C         ← "not safe concurrent on ONE
                                                        instance" holds: each job has
                                                        its own instance, serial inside
```

The concurrency machinery is already proven in `agent.py` — per-loop semaphore, `gather`, fresh deps
per child. The change is *which executor* those children reach: their own, not the shared global.

**Pros**

- **Fine-grained** — parallelism inside one run. One agent can fork N workers over N sandboxes and
  merge them in-process (the true "single harness, multiple jobs" ask).
- **Shared process state** — one warm model client / HTTP pool, one event loop, in-memory
  coordination between jobs; no artifact round-trips to pass data.
- **Lightweight to start** — no Kitaru/Modal stack required; runs on a laptop in `DECODE_ENV=local`.
- **Cleaner architecture regardless** — hanging the executor on `AgentDeps` removes a module global
  that already complicates tests (`reset_executor`, `close_executor`) and the eval harness.

**Cons**

- **You own isolation and crash-recovery** — no checkpoints/replay; if the process dies, all N jobs
  die with it. Everything Kitaru gives for free you rebuild or forgo.
- **Bounded by one host** — N containers share one machine's cores/RAM/Docker daemon; no
  cross-machine scale without also adding an orchestrator.
- **Real refactor with blast radius** — the global is assumed across `bash.py`, `files.py`,
  `lsp.py`, plus warm/close/export lifecycle hooks and the TUI startup (`tui/app.py:788`). Every
  reader must switch to `ctx.deps.executor`.
- **Against a stated invariant** — AGENTS.md: *"A sandbox mode = one isolated Workspace behind one
  seam."* The design consciously pushed the parallelism axis to Kitaru; this adds a second axis the
  architecture deliberately avoided.
- **Resource contention on cleanup** — N concurrent `docker run` + git/gh installs hit one daemon;
  teardown/hand-back must be coordinated so a failed job does not strand a container (the `--rm` and
  Modal timeout backstops still apply, but per-job hand-back gets more intricate).

**Best for:** an interactive session that wants to spin up a few isolated worktrees/sandboxes,
compare them, and merge results without leaving the process — where shared memory and low latency
matter more than durability and cross-machine scale.

---

## Recommendation

- Want **many independent jobs, durable, at scale** → **Road A**. It is what the architecture was
  built for; the only work is the orchestration loop over `run_agent_task`.
- Want **in-process fan-out with shared state and fine granularity** → **Road B**, and treat the
  `AgentDeps.executor` refactor as a general cleanup (kill the module global) rather than a one-off.

They are not exclusive: Road B's per-deps executor is the cleaner foundation, and a controller could
still submit Road-A executions for the heavy, durable, cross-machine batches.

---

## Open questions / follow-ups

- **Concurrency ceiling:** how many parallel `docker run` sandboxes does one host tolerate before the
  daemon / disk (each Workspace is a full clone) becomes the bottleneck? Needs a real measurement.
- **Modal container limits:** how many concurrent executions the `decode-<env>` App actually allows
  (Road A's real ceiling) — untraced so far.
- **Skills seeding cost** at N sandboxes (`workspace.py:189` copies skills into every Workspace).
- **Hand-back at width:** N jobs each pushing a `decode/<session-id>` branch — naming and rate limits.
- **Eval harness overlap:** it already drives executors per-run; a Road-B refactor could subsume its
  bespoke path.
