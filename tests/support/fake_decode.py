"""A stand-in ``decode run`` for the trial-runner tests (ADR-0022 §1; task 160).

:func:`run_trial` launches ``decode run`` as a SUBPROCESS, so its unit tests need a subprocess that
behaves like one — without a model, a key, a sandbox or a daemon. This module writes a tiny python
script to disk and returns the ``decode_command`` a test hands :func:`~evals.harness.trial.run_trial`;
the script honours the flags the trial passes (``--repo`` / ``--summary-json`` / ``--max-requests`` /
``--model``) and plays one of eight recorded behaviours:

* ``oracle`` — the agent that wins: clones the Seed Repo, writes the answer, commits and pushes the
  ``decode/<short-id>`` Session Branch, then writes a summary naming it;
* ``nop`` — a run that changed nothing: a summary with ``handback: null`` (what decode writes when the
  Workspace is unchanged), so the trial grades the base commit;
* ``capped`` — a run stopped by its request ceiling: a wrong answer IS handed back, and the summary
  says ``exit_reason: request_limit``;
* ``lying-branch`` — a summary naming a Session Branch that was never pushed (the broken-clone case);
* ``no-summary`` — a process that died before writing its summary (the infra-error case);
* ``hang`` — a process that never finishes and dies on the trial's SIGINT (no summary);
* ``hang-handback`` — the realistic timeout: SIGINT is caught, the summary is written from the
  process's own ``finally``, and the run exits — decode's documented timeout behaviour (ADR-0022 §4);
* ``hang-ignore-sigint`` — the wedged run: SIGINT is IGNORED, by it and by a grandchild it spawns in
  the SAME process group, so only the trial's SIGKILL escalation ends the group. It prints a second
  json line naming that grandchild's pid, which is how the test proves nothing survived.

Every mode prints ONE json line on stdout first — its argv, its cwd, its process-group id, the
``SANDBOX_WORKSPACE_DIR`` it was handed and a listing of the Seed Repo as the "agent" sees it. That
line is the evidence the tests read: the group id proves ``start_new_session=True``, the workspace
proves each trial got its own, and the listing proves ``tests/`` and ``solution/`` are not there
while the agent runs.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

# The Session Branch the ``oracle`` mode pushes — a fixed short id keeps the assertions literal.
FAKE_BRANCH = "decode/abcd1234"

# The answer the synthetic task's Verifier looks for (``support.benchmark_tasks.DEFAULT_TEST_SCRIPT``).
FAKE_ANSWER = "42"

_SOURCE = '''\
"""A recorded stand-in for ``decode run``; see tests/support/fake_decode.py."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MODE = "__MODE__"
BRANCH = "__BRANCH__"
ANSWER = "__ANSWER__"


def option(name):
    """The value of ``--flag value`` in argv, or ``None`` when the flag was not passed."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def git(cwd, *args):
    subprocess.run(
        [
            "git", "-C", str(cwd),
            "-c", "user.name=fake-agent",
            "-c", "user.email=fake@decode.local",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def solve(repo, answer):
    """Do what an agent does: clone, write an answer, commit, push the Session Branch."""
    work = Path("workspace").resolve()
    subprocess.run(["git", "clone", "--quiet", repo, str(work)], check=True, capture_output=True)
    (work / "answer.txt").write_text(answer + "\\n", encoding="utf-8")
    git(work, "checkout", "-q", "-b", BRANCH)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "solve")
    git(work, "push", "-q", "origin", BRANCH)
    return BRANCH


def write_summary(branch, exit_reason="completed"):
    path = option("--summary-json")
    if path is None:
        return
    handback = {"branch": branch, "pushed": True} if branch else None
    summary = {
        "session_id": BRANCH.split("/")[-1],
        "kitaru_session_id": None,
        "exit_reason": exit_reason,
        "error": None,
        "requests": 2,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": None,
        "handback": handback,
        "output": "done",
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2) + "\\n", encoding="utf-8")


def main():
    repo = option("--repo")
    print(json.dumps({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "pgid": os.getpgrp(),
        "workspace": os.environ.get("SANDBOX_WORKSPACE_DIR"),
        "seed": sorted(os.listdir(repo)) if repo else [],
    }))
    sys.stdout.flush()
    if MODE == "no-summary":
        return
    if MODE == "hang":
        time.sleep(600)
        return
    if MODE == "hang-ignore-sigint":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        # No ``start_new_session``: the grandchild stays in THIS process group, and ignores the
        # interrupt too, so it outlives the SIGINT exactly like a wedged sandbox would.
        child = subprocess.Popen(
            [sys.executable, "-c", "import signal, time\\n"
             "signal.signal(signal.SIGINT, signal.SIG_IGN)\\ntime.sleep(600)"],
        )
        print(json.dumps({"child_pid": child.pid}))
        sys.stdout.flush()
        time.sleep(600)
        return
    if MODE == "hang-handback":
        def on_sigint(signum, frame):
            write_summary(None)
            sys.exit(130)

        signal.signal(signal.SIGINT, on_sigint)
        time.sleep(600)
        return
    if MODE == "oracle":
        write_summary(solve(repo, ANSWER))
    elif MODE == "capped":
        write_summary(solve(repo, "wrong"), exit_reason="request_limit")
    elif MODE == "lying-branch":
        write_summary("decode/never-pushed")
    else:
        write_summary(None)


main()
'''


def write_fake_decode(directory: Path, mode: str) -> tuple[str, ...]:
    """Write the fake ``decode`` at ``directory/fake_decode.py`` and return its ``decode_command``.

    The command runs on THIS interpreter (``sys.executable``), exactly like the real default
    ``(sys.executable, "-m", "decode")``, so a trial's subprocess machinery is exercised verbatim.
    """
    script = directory / f"fake_decode_{mode.replace('-', '_')}.py"
    script.write_text(
        _SOURCE.replace("__MODE__", mode)
        .replace("__BRANCH__", FAKE_BRANCH)
        .replace("__ANSWER__", FAKE_ANSWER),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return (sys.executable, str(script))
