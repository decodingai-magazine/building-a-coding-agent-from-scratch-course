#!/usr/bin/env bash
#
# N headless agents, one task, N branches to compare — the demo the remote runtime exists for.
#
#   scripts/demo-multiple-attempts.sh <repo-url> "<task>" [attempts]
#
# Each attempt is its own Kitaru execution → its own Modal flow container → its own cloned Workspace
# → its own nested bash sandbox. They share nothing, run concurrently (wall-clock ≈ one attempt), and
# each hands its work back as a `decode/<exec-id>` branch on <repo-url>. The script then prints a
# comparison table and the diff commands.
#
# Two things it is careful about, both learned the hard way (INFRA.md §3):
#
#   * It WARMS the flow image with one throwaway run before fanning out. A cold submit builds the
#     image and pushes it to the `run_agent_task-orchestrator` tag — N cold submits would each build
#     and race to push that same tag.
#   * It tells the model NOT to push and NOT to open a PR. If the model ships its own work the
#     attempts stop being comparable; forbidding it makes the Hand-back (ADR-0012 §8) the only path,
#     so every attempt lands identically as `decode/<exec-id>`.
#
# Costs real money: N × (one agent's tokens + a few minutes of Modal CPU). Needs a deployed stack
# (`scripts/deploy.sh status`) and a repo the SANDBOX_GIT_TOKEN can write to.
set -euo pipefail

REPO="${1:-}"
TASK="${2:-}"
ATTEMPTS="${3:-5}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${REPO_ROOT}/.decode/attempts"

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

log "launching ${ATTEMPTS} attempts"
pids=()
for i in $(seq 1 "${ATTEMPTS}"); do
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
done

printf '\n'
[ "${#shipped[@]}" -gt 0 ] || die "no attempt shipped a branch — INFRA.md §3 'Verify the deploy', check 3"

log "${#shipped[@]}/${ATTEMPTS} attempts shipped a branch. Compare them:"
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
