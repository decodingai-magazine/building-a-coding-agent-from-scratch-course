"""Adversarial probes for the HARD Verifiers 015-020 (ADR-0022 §2,§3,§6; task 159).

Same contract as the easy and medium suites: per task one ALTERNATIVE-correct answer that must earn
``1`` (so a grader over-fit to the author's own solution cannot land) and at least one plausible
WRONG answer that must earn ``0`` (so a grader too loose cannot either). Asserted on the reward,
never on an exit code.

Ported from the v1 ``test_oracle_edge_cases_hard.py`` (deleted with the v1 format in task 157) —
every case, with the 015 alternative rewritten because the task itself changed under it:

* ``test_015_passes_when_secrets_move_to_os_getenv`` -> :func:`test_015_accepts_an_os_getenv_answer`
  (the v1 fixture rewrote the whole module; under the new diff bound a wholesale rewrite is no
  longer a correct answer, so the fixture is the same idea kept minimal)
* ``test_015_fails_when_one_secret_is_still_hardcoded`` -> :func:`test_015_rejects_an_answer_that_keeps_one_literal`
* ``test_015_fails_when_the_secret_is_relocated_to_a_non_py_file`` -> :func:`test_015_rejects_a_secret_relocated_to_a_text_file`
* ``test_015_fails_when_a_new_hardcoded_fallback_is_fabricated`` -> :func:`test_015_rejects_a_fabricated_hardcoded_fallback`
* ``test_016_passes_on_a_different_correct_implementation`` -> :func:`test_016_accepts_a_different_correct_implementation`
* ``test_016_fails_when_touching_intervals_are_not_merged`` -> :func:`test_016_rejects_an_implementation_that_leaves_touching_intervals_apart`
* ``test_017_passes_with_a_bucket_is_not_none_guard`` -> :func:`test_017_accepts_a_different_none_sentinel_guard`
* ``test_017_fails_when_a_copy_is_returned_but_state_still_leaks`` -> :func:`test_017_rejects_a_copy_that_still_leaks_state`
* ``test_018_passes_when_reverting_by_position`` -> :func:`test_018_accepts_a_revert_found_by_position`
* ``test_018_fails_when_fixed_directly_without_a_revert_commit`` -> :func:`test_018_rejects_an_in_place_fix_with_no_revert_commit`
* ``test_019_passes_with_manual_capitalization`` -> :func:`test_019_accepts_manual_capitalization`
* ``test_019_fails_when_the_tree_intent_is_dropped`` -> :func:`test_019_rejects_a_resolution_that_drops_the_trees_intent`
* ``test_019_fails_on_leftover_conflict_markers`` -> :func:`test_019_rejects_leftover_conflict_markers`
* ``test_020_passes_with_a_plain_dict_implementation`` -> :func:`test_020_accepts_a_plain_dict_implementation`
* ``test_020_fails_when_counting_is_case_sensitive`` -> :func:`test_020_rejects_case_sensitive_counting`

Plus the three probes that pin what the deleted per-task G-Eval judges used to gesture at, now that
each is a deterministic check (ADR-0022 §6): :func:`test_015_rejects_a_correct_but_bloated_diff`
(015's ``minimal_diff``), :func:`test_019_rejects_leftover_conflict_markers` (019's
``resolution_quality``) and :func:`test_018_rejects_a_rewritten_history` — the last one is the
sharpest, because its answer is correct in every observable way EXCEPT that it rewrote the seeded
commit objects. 020's ``code_quality`` judge has no successor: it was dropped, not replaced.

:func:`test_019_rejects_a_stray_conflict_separator` is the marker half's second probe, added after
QA: the ported case only ever left a WHOLE conflict behind, so a lone ``=======`` — the half-cleanup
a model reaches by deleting the two outer lines — still earned ``1``.

Two further probes keep the rest of 015 honest, each isolating one check:
:func:`test_015_rejects_an_unrelated_new_file` (minimality's file-set half) and
:func:`test_015_rejects_a_literal_left_in_a_comment` (the all-text literal scan — which the v1
relocation port no longer isolates, now that minimality is measured first).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.task_loader import BENCHMARK_TASKS_DIR, load_benchmark_task

from .conftest import AssertReward, GradeWorkspace

# --- 015-secret-scrub -----------------------------------------------------------------------------

# Alternative-correct: os.getenv instead of os.environ, applied as a MINIMAL edit of the seed — no
# literal survives, the accessors are env-backed, and with the vars unset they hand back None.
_SERVICE_OS_GETENV = '''"""Backend service configuration and accessors."""

from __future__ import annotations

import os


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.getenv("API_KEY")


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return os.getenv("DB_PASSWORD")
'''

# Wrong: the API key moved to the environment, but the DB password literal is still in the source.
_SERVICE_ONE_LITERAL_LEFT = '''"""Backend service configuration and accessors."""

from __future__ import annotations

import os


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.environ["API_KEY"]


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return "pr0d-p@ssw0rd-do-not-share"
'''

# Wrong (v1 QA round 1): the literal is moved out of .py into a .txt and read back as a fallback.
# Under v2 the minimality check runs first and already rejects the extra file, so this port no longer
# isolates the all-text literal scan — ``_SERVICE_LITERAL_IN_A_COMMENT`` below is what pins that.
_SERVICE_FILE_FALLBACK = '''"""Backend service configuration and accessors."""

from __future__ import annotations

import os


def _fallback(path: str) -> str:
    return open(path, encoding="utf-8").read().strip()


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.environ.get("API_KEY") or _fallback("secret_api_key.txt")


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return os.environ.get("DB_PASSWORD", "")
'''

# Wrong (v1 QA round 1): env reads with a fabricated NEW hardcoded fallback. The literal scan cannot
# see it (it is not the original), but the env-unset check does.
_SERVICE_FABRICATED_FALLBACK = '''"""Backend service configuration and accessors."""

from __future__ import annotations

import os


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.environ.get("API_KEY", "sk-fallback-hardcoded-should-not-exist")


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return os.environ.get("DB_PASSWORD", "changeme-fallback")
'''

# Wrong for ONE reason only: env-backed, no fallback, and a 3-added/4-deleted diff that clears the
# minimality bound — but the original API key is parked in a comment. Only the all-text literal scan
# can reject this, which is what makes it the probe for that check.
_SERVICE_LITERAL_IN_A_COMMENT = '''"""Backend service configuration and accessors."""

from __future__ import annotations

import os  # the old value was sk-live-9f8a7b6c5d4e3f21ABCDEF


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return os.environ["API_KEY"]


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return os.environ["DB_PASSWORD"]
'''

# Wrong for ONE reason only: behaviourally perfect (no literal, env-backed, raises when unset) but
# the module was rewritten around a new helper — 20+ added/deleted lines where a fix needs 7. This is
# what v1's ``minimal_diff`` judge was asked to eyeball; now it is measured.
_SERVICE_BLOATED_REWRITE = '''"""Backend service configuration and accessors.

Every secret is read from the process environment, never from the source.
"""

from __future__ import annotations

import os


def _require(name: str) -> str:
    """Return the environment variable ``name``, or raise a helpful error when it is unset."""
    try:
        return os.environ[name]
    except KeyError as exc:
        raise RuntimeError(f"the environment variable {name} is not set") from exc


def api_key() -> str:
    """Return the API key used to authenticate outbound requests."""
    return _require("API_KEY")


def db_password() -> str:
    """Return the password used to connect to the primary database."""
    return _require("DB_PASSWORD")
'''

# --- 016-implement-from-spec ----------------------------------------------------------------------

# Alternative-correct: a key= sort and a different loop shape, same merged output.
_INTERVALS_ALTERNATIVE = (
    "def merge_intervals(intervals):\n"
    "    result = []\n"
    "    for interval in sorted(intervals, key=lambda pair: pair[0]):\n"
    "        start, end = interval[0], interval[1]\n"
    "        if result and start <= result[-1][1]:\n"
    "            if end > result[-1][1]:\n"
    "                result[-1][1] = end\n"
    "        else:\n"
    "            result.append([start, end])\n"
    "    return result\n"
)

# Wrong: strict ``<``, so touching intervals like [1,4] and [4,5] are left unmerged.
_INTERVALS_STRICT_COMPARISON = (
    "def merge_intervals(intervals):\n"
    "    merged = []\n"
    "    for start, end in sorted(intervals):\n"
    "        if merged and start < merged[-1][1]:\n"
    "            merged[-1][1] = max(merged[-1][1], end)\n"
    "        else:\n"
    "            merged.append([start, end])\n"
    "    return merged\n"
)

# --- 017-flaky-test-hunt --------------------------------------------------------------------------

# Alternative-correct: a different-but-valid None-sentinel guard than the gold's if-block.
_REGISTRY_CONDITIONAL_GUARD = (
    "def collect(item, bucket=None):\n"
    "    bucket = bucket if bucket is not None else []\n"
    "    bucket.append(item)\n"
    "    return bucket\n"
)

# Wrong: returns a COPY of the bucket but keeps mutating the shared mutable default — the leak, and
# the order-dependent flake, are still there.
_REGISTRY_LEAKY_COPY = (
    "def collect(item, bucket=[]):\n    bucket.append(item)\n    return list(bucket)\n"
)

# --- 018-git-bisect-revert ------------------------------------------------------------------------

# Wrong: the bug is patched in place and committed as an ordinary fix — the suite passes, but the
# required git ACTION (a revert commit on top) never happened.
_IN_PLACE_FIX = """
python3 - <<'PY'
text = open('calc.py', encoding='utf-8').read()
text = text.replace('def multiply(a, b):\\n    return a + b', 'def multiply(a, b):\\n    return a * b')
open('calc.py', 'w', encoding='utf-8').write(text)
PY
git commit -q -am 'fix: correct multiply'
"""

# Correct: a textbook revert, with decode's Hand-back capture commit landing on top of it — exactly
# what a real trial hands back when the agent left a scratch file uncommitted.
_REVERT_THEN_CAPTURE_COMMIT = """
git revert --no-edit HEAD~1
printf 'scratch\\n' > notes.txt
git add notes.txt
git -c user.name=decode -c user.email=decode@localhost commit -q -m "decode session abc123"
"""

# Wrong for ONE reason only: a textbook revert, and then a "tidy up" that rebases the whole history
# under a new committer. Every subject and every file survives byte for byte and the newest subject
# still starts "Revert" — the only thing that changed is the commit OBJECTS, which is exactly what
# "do not rewrite, amend or rebase any existing commit" forbids. So the ONLY check that can reject
# this answer is the seeded ``original-head`` tag no longer being an ancestor of HEAD.
# (A plain ``git rebase --root`` cannot be used here: git is content-addressed, so re-applying the
# same commits with the same identity and dates reproduces the same shas — nothing is rewritten.)
_REVERT_THEN_REWRITE = """
git revert --no-edit HEAD~1
GIT_COMMITTER_NAME="Tidy Bot" git rebase --root --force-rebase
"""

# --- 019-patch-conflict-resolve -------------------------------------------------------------------

# Alternative-correct: capitalizes the name by hand rather than via str.capitalize; same output.
_GREET_MANUAL_CAPITALIZATION = (
    'def greet(name: str) -> str:\n    return f"Hi there, {name[:1].upper() + name[1:].lower()}!"\n'
)

# Wrong: the patch's wording and punctuation are applied, but the tree's capitalization is lost.
_GREET_TREE_INTENT_DROPPED = 'def greet(name: str) -> str:\n    return f"Hi there, {name}!"\n'

# Wrong (boundary): the conflict was "resolved" by leaving the markers in place.
_GREET_WITH_CONFLICT_MARKERS = (
    "def greet(name: str) -> str:\n"
    "<<<<<<< HEAD\n"
    '    return f"Hello, {name.capitalize()}"\n'
    "=======\n"
    '    return f"Hi there, {name}!"\n'
    ">>>>>>> feature\n"
)

# Wrong (boundary): a correct, fully-resolved greeting, but a lone ``=======`` separator was left
# behind mid-line inside the module docstring — the reachable half-cleanup, where a model deletes the
# two outer marker lines and forgets the middle one. Only a marker inside a string or comment is
# syntactically valid Python, so this fixture is exactly the shape the scan has to catch.
_GREET_STRAY_SEPARATOR = '''"""Greeting helpers.

Left over from the merge: =======
"""

from __future__ import annotations


def greet(name: str) -> str:
    return f"Hi there, {name.capitalize()}!"
'''

# --- 020-build-small-tool -------------------------------------------------------------------------

# Alternative-correct: a hand-rolled dict counter instead of collections.Counter; same output.
_WORDFREQ_PLAIN_DICT = (
    "import argparse\n"
    "import string\n"
    "from pathlib import Path\n\n\n"
    "def main() -> None:\n"
    "    parser = argparse.ArgumentParser()\n"
    '    parser.add_argument("path")\n'
    '    parser.add_argument("--top", type=int, default=10)\n'
    "    args = parser.parse_args()\n"
    "    counts = {}\n"
    '    for token in Path(args.path).read_text(encoding="utf-8").split():\n'
    "        word = token.strip(string.punctuation).lower()\n"
    "        if word:\n"
    "            counts[word] = counts.get(word, 0) + 1\n"
    "    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))\n"
    "    for word, count in ordered[: args.top]:\n"
    '        print(f"{word} {count}")\n\n\n'
    'if __name__ == "__main__":\n'
    "    main()\n"
)

# Wrong: never lowercases, so `The`/`the`/`THE` split into separate counts.
_WORDFREQ_CASE_SENSITIVE = (
    "import argparse\n"
    "import string\n"
    "from collections import Counter\n"
    "from pathlib import Path\n\n\n"
    "def main() -> None:\n"
    "    parser = argparse.ArgumentParser()\n"
    '    parser.add_argument("path")\n'
    '    parser.add_argument("--top", type=int, default=10)\n'
    "    args = parser.parse_args()\n"
    "    counts = Counter()\n"
    '    for token in Path(args.path).read_text(encoding="utf-8").split():\n'
    "        word = token.strip(string.punctuation)\n"
    "        if word:\n"
    "            counts[word] += 1\n"
    "    for word, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: args.top]:\n"
    '        print(f"{word} {count}")\n\n\n'
    'if __name__ == "__main__":\n'
    "    main()\n"
)


def test_015_accepts_an_os_getenv_answer(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("015-secret-scrub", files={"service.py": _SERVICE_OS_GETENV})

    assert_reward(result, 1.0)


def test_015_rejects_an_answer_that_keeps_one_literal(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("015-secret-scrub", files={"service.py": _SERVICE_ONE_LITERAL_LEFT})

    assert_reward(result, 0.0)


def test_015_rejects_a_secret_relocated_to_a_text_file(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "015-secret-scrub",
        files={
            "service.py": _SERVICE_FILE_FALLBACK,
            "secret_api_key.txt": "sk-live-9f8a7b6c5d4e3f21ABCDEF\n",
        },
    )

    assert_reward(result, 0.0)


def test_015_rejects_a_literal_left_in_a_comment(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """A minimal, env-backed answer that still carries the secret in a comment: the scan must bite.

    The v1 relocation probe used to pin the all-text literal scan; under v2 it is rejected by the
    minimality check first, so this case — which clears every other check — is the scan's probe.
    """
    result = grade_workspace(
        "015-secret-scrub", files={"service.py": _SERVICE_LITERAL_IN_A_COMMENT}
    )

    assert_reward(result, 0.0)
    assert "hardcoded secret literal" in result.stdout, result.stdout


def test_015_rejects_a_fabricated_hardcoded_fallback(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("015-secret-scrub", files={"service.py": _SERVICE_FABRICATED_FALLBACK})

    assert_reward(result, 0.0)


def test_015_rejects_a_correct_but_bloated_diff(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """The deterministic successor to v1's ``minimal_diff`` judge: behaviour right, diff too big."""
    result = grade_workspace("015-secret-scrub", files={"service.py": _SERVICE_BLOATED_REWRITE})

    assert_reward(result, 0.0)


def test_015_rejects_an_unrelated_new_file(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """Only ``service.py`` may change — a scratch file left in the checkout is not a minimal fix."""
    result = grade_workspace(
        "015-secret-scrub",
        files={"service.py": _SERVICE_OS_GETENV, "notes.md": "moved the secrets to the env\n"},
    )

    assert_reward(result, 0.0)


def test_016_accepts_a_different_correct_implementation(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "016-implement-from-spec", files={"intervals.py": _INTERVALS_ALTERNATIVE}
    )

    assert_reward(result, 1.0)


def test_016_rejects_an_implementation_that_leaves_touching_intervals_apart(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "016-implement-from-spec", files={"intervals.py": _INTERVALS_STRICT_COMPARISON}
    )

    assert_reward(result, 0.0)


def test_017_accepts_a_different_none_sentinel_guard(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "017-flaky-test-hunt", files={"registry.py": _REGISTRY_CONDITIONAL_GUARD}
    )

    assert_reward(result, 1.0)


def test_017_rejects_a_copy_that_still_leaks_state(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("017-flaky-test-hunt", files={"registry.py": _REGISTRY_LEAKY_COPY})

    assert_reward(result, 0.0)


def test_018_accepts_a_revert_found_by_position(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """Alternative-correct: the breaking commit is found by position (HEAD~1), not by message."""
    result = grade_workspace("018-git-bisect-revert", post_setup="git revert --no-edit HEAD~1")

    assert_reward(result, 1.0)


def test_018_accepts_a_revert_under_a_hand_back_capture_commit(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """ADR-0022 §3: a history-inspecting Verifier tolerates the trailing capture commit.

    When the agent leaves work uncommitted, the Hand-back commits it as ``decode <decode@localhost>``
    — so the newest commit at grade time is not the agent's revert. That must not cost the reward.
    """
    result = grade_workspace("018-git-bisect-revert", post_setup=_REVERT_THEN_CAPTURE_COMMIT)

    assert_reward(result, 1.0)


def test_018_rejects_an_in_place_fix_with_no_revert_commit(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("018-git-bisect-revert", post_setup=_IN_PLACE_FIX)

    assert_reward(result, 0.0)


def test_018_rejects_a_rewritten_history(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """A correct revert whose history was then rebased: the seeded commit objects are gone."""
    result = grade_workspace("018-git-bisect-revert", post_setup=_REVERT_THEN_REWRITE)

    assert_reward(result, 0.0)


def test_019_accepts_manual_capitalization(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "019-patch-conflict-resolve", files={"greet.py": _GREET_MANUAL_CAPITALIZATION}
    )

    assert_reward(result, 1.0)


def test_019_rejects_a_resolution_that_drops_the_trees_intent(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "019-patch-conflict-resolve", files={"greet.py": _GREET_TREE_INTENT_DROPPED}
    )

    assert_reward(result, 0.0)


def test_019_rejects_leftover_conflict_markers(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """The deterministic successor to v1's ``resolution_quality`` judge, marker half."""
    result = grade_workspace(
        "019-patch-conflict-resolve", files={"greet.py": _GREET_WITH_CONFLICT_MARKERS}
    )

    assert_reward(result, 0.0)


def test_019_rejects_a_stray_conflict_separator(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    """A lone ``=======`` is a leftover marker too — ``instruction.md`` promises all three."""
    result = grade_workspace(
        "019-patch-conflict-resolve", files={"greet.py": _GREET_STRAY_SEPARATOR}
    )

    assert_reward(result, 0.0)


def test_020_accepts_a_plain_dict_implementation(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace("020-build-small-tool", files={"wordfreq.py": _WORDFREQ_PLAIN_DICT})

    assert_reward(result, 1.0)


def test_020_rejects_case_sensitive_counting(
    grade_workspace: GradeWorkspace, assert_reward: AssertReward
) -> None:
    result = grade_workspace(
        "020-build-small-tool", files={"wordfreq.py": _WORDFREQ_CASE_SENSITIVE}
    )

    assert_reward(result, 0.0)


@pytest.mark.parametrize(
    "task_id",
    [
        "016-implement-from-spec",
        "017-flaky-test-hunt",
        "019-patch-conflict-resolve",
        "020-build-small-tool",
    ],
)
def test_the_swe_bench_tasks_run_exactly_the_node_ids_they_declare(task_id: str) -> None:
    """``[verifier.tests]`` is the contract; ``tests/`` is what runs. They must not drift.

    The whole hidden asset tree is searched, not just ``test.sh``: 017 hands its node ids to
    ``tests/_verify_suite.py``, which runs them in several orders.
    """
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_id)

    hidden = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(Path(task.tests_dir).rglob("*"))
        if path.is_file()
    )

    assert task.fail_to_pass, f"{task_id} is the SWE-bench shape: it must declare a fail_to_pass id"
    for node_id in task.fail_to_pass + task.pass_to_pass:
        assert node_id in hidden, f"{node_id} is declared in task.toml but never run under tests/"
