"""Adversarial probes for the hard benchmark oracles 015-020 (task 110, "attack your own oracles").

The two-direction oracle-sanity sweep only proves each oracle PASSes on the gold ``solution/`` and
FAILs on the untouched ``setup/``. That leaves two blind spots: rejecting a valid ALTERNATIVE-correct
answer (over-fit to the author's exact solution) and accepting a plausible WRONG answer. Each hard
task below pins at least one of each, so a grader that is too strict or too loose can never land
silently. Tasks whose state is built by ``setup.sh`` (018 git history) run it here too, exactly as the
runner and oracle-sanity harness do; git-state answers are applied via a ``post_setup`` snippet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.harness.task_loader import (
    BENCHMARK_TASKS_DIR,
    VERIFY_SCRIPT_NAME,
    load_benchmark_task,
)

SETUP_SCRIPT_NAME = "setup.sh"


def _grade(
    task_slug: str,
    tmp_path: Path,
    *,
    overlay_files: dict[str, str] | None = None,
    post_setup: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Reproduce the grade-time Workspace, drop in a submitted answer, then run ``verify.sh``.

    Seeds ``setup/``, runs ``setup.sh`` if present, writes ``overlay_files`` (the submitted answer),
    runs an optional ``post_setup`` bash snippet (for git-state answers), injects ``verify/`` and
    grades — the same order the runner and the oracle-sanity harness use.
    """
    task = load_benchmark_task(BENCHMARK_TASKS_DIR / task_slug)
    subprocess.run(["cp", "-R", f"{task.setup_dir}/.", str(tmp_path)], check=True)

    if (tmp_path / SETUP_SCRIPT_NAME).is_file():
        setup = subprocess.run(
            ["bash", SETUP_SCRIPT_NAME], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert setup.returncode == 0, f"setup.sh failed: {setup.stderr}"

    for name, content in (overlay_files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if post_setup is not None:
        acted = subprocess.run(
            ["bash", "-c", post_setup], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert acted.returncode == 0, f"post_setup failed: {acted.stderr}"

    subprocess.run(["cp", "-R", f"{task.verify_script.parent}/.", str(tmp_path)], check=True)
    return subprocess.run(
        ["bash", VERIFY_SCRIPT_NAME],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_pass(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"expected PASS, got exit {result.returncode}\n{result.stdout}"
    assert "PASS" in result.stdout


def _assert_fail(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0, f"expected FAIL, got exit 0\n{result.stdout}"


# --- 015-secret-scrub -----------------------------------------------------------------------------


def test_015_passes_when_secrets_move_to_os_getenv(tmp_path: Path) -> None:
    # Alternative-correct: os.getenv instead of os.environ — no literal, env-backed accessors.
    getenv_service = (
        "import os\n\n\n"
        "def api_key() -> str:\n"
        '    return os.getenv("API_KEY")\n\n\n'
        "def db_password() -> str:\n"
        '    return os.getenv("DB_PASSWORD")\n'
    )
    _assert_pass(_grade("015-secret-scrub", tmp_path, overlay_files={"service.py": getenv_service}))


def test_015_fails_when_one_secret_is_still_hardcoded(tmp_path: Path) -> None:
    # Wrong: the API key moved to env, but the DB password literal is still baked into the source.
    partial_service = (
        "import os\n\n\n"
        "def api_key() -> str:\n"
        '    return os.environ["API_KEY"]\n\n\n'
        "def db_password() -> str:\n"
        '    return "pr0d-p@ssw0rd-do-not-share"\n'
    )
    _assert_fail(
        _grade("015-secret-scrub", tmp_path, overlay_files={"service.py": partial_service})
    )


def test_015_fails_when_the_secret_is_relocated_to_a_non_py_file(tmp_path: Path) -> None:
    # Wrong (QA round 1): the literal is moved out of .py into a .txt and read back as a fallback.
    # The all-text scan catches the relocated literal, and the env-unset check catches the fallback.
    fallback_service = (
        "import os\n\n\n"
        "def _fallback(path: str) -> str:\n"
        '    return open(path, encoding="utf-8").read().strip()\n\n\n'
        "def api_key() -> str:\n"
        '    return os.environ.get("API_KEY") or _fallback("secret_api_key.txt")\n\n\n'
        "def db_password() -> str:\n"
        '    return os.environ.get("DB_PASSWORD", "")\n'
    )
    _assert_fail(
        _grade(
            "015-secret-scrub",
            tmp_path,
            overlay_files={
                "service.py": fallback_service,
                "secret_api_key.txt": "sk-live-9f8a7b6c5d4e3f21ABCDEF\n",
            },
        )
    )


def test_015_fails_when_a_new_hardcoded_fallback_is_fabricated(tmp_path: Path) -> None:
    # Wrong (QA round 1): env reads with a fabricated NEW hardcoded fallback secret. The literal scan
    # cannot see it (it is not the original), but the env-unset check does: a non-empty return with
    # the env var unset proves a hardcoded fallback still exists.
    fabricated_service = (
        "import os\n\n\n"
        "def api_key() -> str:\n"
        '    return os.environ.get("API_KEY", "sk-fallback-hardcoded-should-not-exist")\n\n\n'
        "def db_password() -> str:\n"
        '    return os.environ.get("DB_PASSWORD", "changeme-fallback")\n'
    )
    _assert_fail(
        _grade("015-secret-scrub", tmp_path, overlay_files={"service.py": fabricated_service})
    )


# --- 016-implement-from-spec ----------------------------------------------------------------------


def test_016_passes_on_a_different_correct_implementation(tmp_path: Path) -> None:
    # Alternative-correct: a key= sort and a different loop shape, same merged output.
    alt_intervals = (
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
    _assert_pass(
        _grade("016-implement-from-spec", tmp_path, overlay_files={"intervals.py": alt_intervals})
    )


def test_016_fails_when_touching_intervals_are_not_merged(tmp_path: Path) -> None:
    # Wrong: uses strict `<`, so touching intervals like [1,4] and [4,5] are left unmerged.
    strict_intervals = (
        "def merge_intervals(intervals):\n"
        "    merged = []\n"
        "    for start, end in sorted(intervals):\n"
        "        if merged and start < merged[-1][1]:\n"
        "            merged[-1][1] = max(merged[-1][1], end)\n"
        "        else:\n"
        "            merged.append([start, end])\n"
        "    return merged\n"
    )
    _assert_fail(
        _grade(
            "016-implement-from-spec", tmp_path, overlay_files={"intervals.py": strict_intervals}
        )
    )


# --- 017-flaky-test-hunt --------------------------------------------------------------------------


def test_017_passes_with_a_bucket_is_not_none_guard(tmp_path: Path) -> None:
    # Alternative-correct: a different-but-valid None-sentinel guard than the gold's if-block.
    alt_registry = (
        "def collect(item, bucket=None):\n"
        "    bucket = bucket if bucket is not None else []\n"
        "    bucket.append(item)\n"
        "    return bucket\n"
    )
    _assert_pass(
        _grade("017-flaky-test-hunt", tmp_path, overlay_files={"registry.py": alt_registry})
    )


def test_017_fails_when_a_copy_is_returned_but_state_still_leaks(tmp_path: Path) -> None:
    # Wrong: returns a copy of the bucket but keeps mutating the shared mutable default — the leak
    # (and the order-dependent flake) is still there, so the 3-run suite loop fails.
    leaky_registry = (
        "def collect(item, bucket=[]):\n    bucket.append(item)\n    return list(bucket)\n"
    )
    _assert_fail(
        _grade("017-flaky-test-hunt", tmp_path, overlay_files={"registry.py": leaky_registry})
    )


# --- 018-git-bisect-revert ------------------------------------------------------------------------


def test_018_passes_when_reverting_by_position(tmp_path: Path) -> None:
    # Alternative-correct: find the breaking commit by position (HEAD~1) rather than by message.
    _assert_pass(
        _grade("018-git-bisect-revert", tmp_path, post_setup="git revert --no-edit HEAD~1")
    )


def test_018_fails_when_fixed_directly_without_a_revert_commit(tmp_path: Path) -> None:
    # Wrong: the bug is patched in place and committed as a normal fix — tests pass, but there is no
    # revert commit, so the required git action was not performed.
    direct_fix = (
        "python3 - <<'PY'\n"
        "text = open('calc.py', encoding='utf-8').read()\n"
        "text = text.replace('def multiply(a, b):\\n    return a + b', "
        "'def multiply(a, b):\\n    return a * b')\n"
        "open('calc.py', 'w', encoding='utf-8').write(text)\n"
        "PY\n"
        "git commit -q -am 'fix: correct multiply'"
    )
    _assert_fail(_grade("018-git-bisect-revert", tmp_path, post_setup=direct_fix))


# --- 019-patch-conflict-resolve -------------------------------------------------------------------


def test_019_passes_with_manual_capitalization(tmp_path: Path) -> None:
    # Alternative-correct: capitalizes the name by hand rather than via str.capitalize; same output.
    manual_greet = (
        "def greet(name: str) -> str:\n"
        '    return f"Hi there, {name[:1].upper() + name[1:].lower()}!"\n'
    )
    _assert_pass(
        _grade("019-patch-conflict-resolve", tmp_path, overlay_files={"greet.py": manual_greet})
    )


def test_019_fails_when_the_tree_intent_is_dropped(tmp_path: Path) -> None:
    # Wrong: the patch's wording/punctuation is applied but the tree's capitalization is lost.
    dropped_greet = 'def greet(name: str) -> str:\n    return f"Hi there, {name}!"\n'
    _assert_fail(
        _grade("019-patch-conflict-resolve", tmp_path, overlay_files={"greet.py": dropped_greet})
    )


def test_019_fails_on_leftover_conflict_markers(tmp_path: Path) -> None:
    # Wrong (boundary): the conflict was "resolved" by leaving the markers in place.
    marked_greet = (
        "def greet(name: str) -> str:\n"
        "<<<<<<< HEAD\n"
        '    return f"Hello, {name.capitalize()}"\n'
        "=======\n"
        '    return f"Hi there, {name}!"\n'
        ">>>>>>> feature\n"
    )
    _assert_fail(
        _grade("019-patch-conflict-resolve", tmp_path, overlay_files={"greet.py": marked_greet})
    )


# --- 020-build-small-tool -------------------------------------------------------------------------


def test_020_passes_with_a_plain_dict_implementation(tmp_path: Path) -> None:
    # Alternative-correct: a hand-rolled dict counter instead of collections.Counter; same output.
    dict_wordfreq = (
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
    _assert_pass(
        _grade("020-build-small-tool", tmp_path, overlay_files={"wordfreq.py": dict_wordfreq})
    )


def test_020_fails_when_counting_is_case_sensitive(tmp_path: Path) -> None:
    # Wrong: never lowercases, so `The`/`the`/`THE` split into separate counts.
    case_sensitive_wordfreq = (
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
    _assert_fail(
        _grade(
            "020-build-small-tool", tmp_path, overlay_files={"wordfreq.py": case_sensitive_wordfreq}
        )
    )
