#!/usr/bin/env bash
#
# N headless agents, one task, N branches to compare — the demo the remote runtime exists for.
#
#   scripts/demo-multiple-attempts.sh <repo-url> "<task>" [attempts] [--pr]
#
# Each attempt is its own Kitaru execution → its own Modal flow container → its own cloned Workspace
# → its own nested bash sandbox. They share nothing, run concurrently (wall-clock ≈ one attempt), and
# each hands its work back as a `decode/<exec-id>` branch on <repo-url>. The script then prints a
# comparison table and the diff commands.
#
# `--pr` opens one pull request per shipped branch, HOST-side, after the runs. The models are still
# forbidden to open their own (see below) — a PR raised here is titled and numbered by attempt, which
# is what makes N attempts reviewable side by side. Without it you get branches only.
#
# Two things it is careful about, both learned the hard way (getting_started/INFRA.md §3):
#
#   * It WARMS the flow image with one throwaway run before fanning out. A cold submit builds the
#     image and pushes it to the `run_agent_task-orchestrator` tag — N cold submits would each build
#     and race to push that same tag (and race on the code upload, getting_started/INFRA.md §4).
#   * It tells the model NOT to push and NOT to open a PR. A model that ships its own work names its
#     own branch and sometimes forgets entirely, so the attempts stop being comparable; forbidding it
#     makes the Hand-back (ADR-0012 §8) the only path, and every attempt lands identically as
#     `decode/<exec-id>`.
#
# Costs real money: N × (one agent's tokens + a few minutes of Modal CPU). Needs a deployed stack
# (`scripts/deploy.sh status`) and a repo the SANDBOX_GIT_TOKEN can write to.
set -euo pipefail

REPO="${1:-}"
TASK="${2:-}"
ATTEMPTS="${3:-5}"
OPEN_PRS="${4:-}"   # `--pr` → one PR per shipped branch, opened host-side after the runs

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${REPO_ROOT}/.decode/attempts"   # gitignored, so it stays out of ZenML's code archive

# Seconds between submits. ZenML uploads the code archive with a check-then-copy — a TOCTOU — so N
# submits that all find the archive missing will all upload it, and the losers die with
# `FileExistsError: Destination file 'gs://…/code_uploads/<hash>.tar.gz' already exists`. The warm-up
# below normally uploads that exact archive first, which is the real protection; this stagger covers
# the case where it does not (the repo changed between the warm-up and the fan-out, so the attempts
# archive a different tree — an edit mid-run is enough).
STAGGER_S=8

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

[ -n "${REPO}" ] && [ -n "${TASK}" ] \
  || die "usage: scripts/demo-multiple-attempts.sh <repo-url> \"<task>\" [attempts]"
case "${ATTEMPTS}" in ''|*[!0-9]*) die "attempts must be a number, got: ${ATTEMPTS}" ;; esac
[ "${ATTEMPTS}" -ge 2 ] || die "attempts must be at least 2 — the point is comparing them"

# Normalize with the HARNESS's own normalizer (decode.sandbox.workspace.normalize_repo) rather than a
# second copy of the rules here — a browser URL is decode's problem to understand, not this script's.
normalized="$(cd "${REPO_ROOT}" && uv run python -c \
  'import sys; from decode.sandbox.workspace import normalize_repo; print(normalize_repo(sys.argv[1]))' \
  "${REPO}" 2>/dev/null)" || die "could not normalize ${REPO}"
if [ "${normalized}" != "${REPO}" ]; then
  warn "normalized the repo url to ${normalized}"
  REPO="${normalized}"
fi

# Then check it is readable BEFORE spending money: N agents against an unreachable repo is a typo that
# costs N runs.
git ls-remote --exit-code "${REPO}" HEAD >/dev/null 2>&1 \
  || die "cannot read ${REPO} — is it a clonable git URL you have access to?"

# Same reason: an unauthenticated `gh` must not be discovered AFTER N paid runs have finished.
if [ -n "${OPEN_PRS}" ]; then
  [ "${OPEN_PRS}" = "--pr" ] || die "unknown option ${OPEN_PRS} (did you mean --pr?)"
  command -v gh >/dev/null || die "--pr needs the gh cli"
  gh auth status >/dev/null 2>&1 || die "--pr needs an authenticated gh — run: gh auth login"
fi

# The Makefile's run-remote, inlined so each attempt can be backgrounded with its own log.
run_remote() {
  local task="$1" log_file="$2" repo_arg=("${@:3}")
  ( cd "${REPO_ROOT}" && DOCKER_BUILDKIT=1 KITARU_STACK=prod-modal DECODE_ENV=prod SANDBOX_MODE=modal \
      uv run --group remote decode run "${task}" "${repo_arg[@]}" ) >"${log_file}" 2>&1
}

# decode prints `exec_id: <uuid>` to stderr; the Hand-back branch is its first 8 chars (handback.py).
exec_id_from() { sed -n 's/^exec_id: \([0-9a-f-]*\).*/\1/p' "$1" | head -1; }
branch_for()   { printf 'decode/%s' "${1:0:8}"; }

mkdir -p "${RUN_DIR}"
log "task:     ${TASK}"
log "repo:     ${REPO}"
log "attempts: ${ATTEMPTS} (concurrent — wall-clock is about one attempt)"

# ---------------------------------------------------------------- warm the image (see header)
# Do NOT edit the repo from here on: the warm-up uploads the code archive the attempts will reuse, and
# a tracked-file change invalidates it (the archive is content-hashed), putting the upload race back.
log "warming the flow image with one throwaway run (so the ${ATTEMPTS} attempts do not race to build it)"
if ! run_remote "reply with the single word: warm" "${RUN_DIR}/warmup.log"; then
  tail -5 "${RUN_DIR}/warmup.log" >&2
  die "the warm-up run failed — fix the stack first: scripts/deploy.sh status"
fi
log "image warm"

# ---------------------------------------------------------------- fan out
# The push ban is what keeps the attempts comparable — the Hand-back ships every one of them the same
# way, under a branch named for its execution.
FULL_TASK="${TASK}

Commit your work when you are done. Do NOT push and do NOT open a pull request."

log "launching ${ATTEMPTS} attempts (${STAGGER_S}s apart — see the code-upload TOCTOU above)"
pids=()
for i in $(seq 1 "${ATTEMPTS}"); do
  if [ "${i}" -gt 1 ]; then sleep "${STAGGER_S}"; fi   # `&&` here would trip `set -e` on i=1
  run_remote "${FULL_TASK}" "${RUN_DIR}/attempt-${i}.log" --repo "${REPO}" &
  pids+=("$!")
done

failed=0
for i in $(seq 1 "${ATTEMPTS}"); do
  if wait "${pids[$((i - 1))]}"; then
    log "attempt ${i} finished"
  else
    warn "attempt ${i} FAILED — see ${RUN_DIR}/attempt-${i}.log"
    failed=$((failed + 1))
  fi
done
[ "${failed}" -lt "${ATTEMPTS}" ] || die "every attempt failed — check ${RUN_DIR}/attempt-1.log"

# ---------------------------------------------------------------- collect + compare
# A shallow-ish clone of the target repo, purely to diff the branches the agents shipped.
compare_dir="${RUN_DIR}/compare"
rm -rf "${compare_dir}"
git clone --quiet --filter=blob:none "${REPO}" "${compare_dir}" || die "could not clone ${REPO} to compare"
base="$(git -C "${compare_dir}" rev-parse HEAD)"

printf '\n%-3s  %-38s  %-18s  %-7s  %-9s  %s\n' '#' 'exec_id' 'branch' 'commits' 'files' 'churn'
printf -- '---  --------------------------------------  ------------------  -------  ---------  ---------\n'

shipped=()
shipped_attempt=()
shipped_exec=()
for i in $(seq 1 "${ATTEMPTS}"); do
  log_file="${RUN_DIR}/attempt-${i}.log"
  [ -f "${log_file}" ] || continue
  exec_id="$(exec_id_from "${log_file}")"
  if [ -z "${exec_id}" ]; then
    printf '%-3s  %-38s  %s\n' "${i}" '—' 'no exec_id — the run never started'
    continue
  fi
  branch="$(branch_for "${exec_id}")"

  if ! git -C "${compare_dir}" fetch --quiet origin "refs/heads/${branch}:refs/heads/${branch}" 2>/dev/null; then
    # No branch = the Hand-back shipped nothing: an unchanged Workspace, or a push it could not make.
    printf '%-3s  %-38s  %-18s  %s\n' "${i}" "${exec_id}" "${branch}" 'NOT SHIPPED (see the log)'
    continue
  fi
  commits="$(git -C "${compare_dir}" rev-list --count "${base}..${branch}")"
  files="$(git -C "${compare_dir}" diff --name-only "${base}..${branch}" | wc -l | tr -d ' ')"
  churn="$(git -C "${compare_dir}" diff --shortstat "${base}..${branch}" \
    | sed -n 's/.*, \([0-9]*\) insertion.*, \([0-9]*\) deletion.*/+\1 -\2/p')"
  printf '%-3s  %-38s  %-18s  %-7s  %-9s  %s\n' \
    "${i}" "${exec_id}" "${branch}" "${commits}" "${files}" "${churn:-—}"
  shipped+=("${branch}")
  shipped_attempt+=("${i}")
  shipped_exec+=("${exec_id}")
done

printf '\n'
[ "${#shipped[@]}" -gt 0 ] || die "no attempt shipped a branch — getting_started/INFRA.md §3 'Verify the deploy', check 3"

log "${#shipped[@]}/${ATTEMPTS} attempts shipped a branch."

# ---------------------------------------------------------------- one PR per attempt (--pr)
# Opened HERE, not by the models: the branch is already named for its execution, so the PR can say
# which attempt it is and reviewers can read N of them against each other. `gh pr create` is
# idempotent enough for a re-run — an existing PR for the branch makes it fail, which we tolerate.
if [ -n "${OPEN_PRS}" ]; then
  base_branch="$(git -C "${compare_dir}" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)"
  base_branch="${base_branch#origin/}"
  log "opening ${#shipped[@]} pull requests against ${base_branch:-main}"
  for idx in "${!shipped[@]}"; do
    url="$(gh pr create --repo "${REPO}" \
      --head "${shipped[$idx]}" --base "${base_branch:-main}" \
      --title "attempt ${shipped_attempt[$idx]}/${ATTEMPTS}: ${TASK}" \
      --body "One of ${ATTEMPTS} independent headless attempts at the same task, for comparison.

**Task:** ${TASK}

Run by \`scripts/demo-multiple-attempts.sh\` on the remote runtime: its own Kitaru execution
(\`${shipped_exec[$idx]}\`), its own Modal flow container, its own cloned Workspace and bash sandbox.
The agent was told NOT to push — this branch was shipped by the Hand-back (ADR-0012 §8), which is
why it is named for the execution.

Replay it: \`decode replay ${shipped_exec[$idx]} --from <checkpoint> --model <model-id>\`" 2>&1 | tail -1)" \
      || url="FAILED (does a PR already exist for ${shipped[$idx]}?)"
    printf '  attempt %s → %s\n' "${shipped_attempt[$idx]}" "${url}"
  done
fi

printf '\nCompare them:\n'
printf '\n'
for branch in "${shipped[@]}"; do
  printf '  git -C %s diff %s..%s\n' "${compare_dir}" "${base:0:8}" "${branch}"
done
printf '\n  # side by side, one attempt against another:\n'
if [ "${#shipped[@]}" -ge 2 ]; then
  printf '  git -C %s diff %s..%s\n' "${compare_dir}" "${shipped[0]}" "${shipped[1]}"
fi
printf '\n  # what each agent actually did, turn by turn:\n'
printf '  uv run kitaru executions list\n'
printf '  uv run kitaru executions logs <exec_id>\n\n'
log "logs: ${RUN_DIR}/attempt-*.log   ·   worktree to diff in: ${compare_dir}"
