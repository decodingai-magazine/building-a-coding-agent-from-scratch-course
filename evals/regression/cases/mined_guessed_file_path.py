"""Case 22 (MINED) — a file is opened at a path the tree really holds, not a guessed one (ADR-0022 §8).

Mined from the LIVE ``decode-prod`` project on 2026-09-11 (``evals/regression/mining/``): signature
``long | - | bash | -``, trace ``01a08614-aaf4-752a-857a-209f2ca411c9`` (thread
``69b1d5ef-b0b7-4788-9488-3d1870241ae3``, tagged ``regression-case`` in Opik) — and the SAME behavior
in its sibling ``01a0861b-799c-7833-b78f-e8595294bc36``, two runs out of two.

**What the traces show.** Asked to *"add a hello line to README"*, both runs opened with
``read(path="README")`` — a path neither tree holds (the file is ``README.md``). The tool span carries
``ToolRetryError``, and a whole model leg is then spent recovering (run 1 re-guessed ``README.md``,
run 2 fell back to ``glob("README*")``). Both runs finished correctly, which is why mining surfaced
them under ``long`` rather than ``errors`` — the wasted leg is the behavior, not a crash.

**The case.** A small repo-shaped Workspace whose readme is ``README.md`` and the production prompt's
first clause. (The recorded prompt continued *"and commit … Do NOT push"* — the trial runner's
boilerplate. The guess happens on leg one, before any commit matters, so the case drops it: a smaller
fixture, no git seed, fewer paid legs, same behavior graded.) ONE deterministic metric —
:class:`~evals.harness.metrics.ToolArgsNeverMetric` over ``read``'s ``path`` — because the fact being
graded is about EVERY read the run made, not about one of them: look first (``glob`` / ``grep``) or
open a path the fixture really seeded; never a guess. Paths are compared by basename so an absolute
path into the temp Workspace grades the same as a relative one.

Recovering does not hide the guess, which is what makes this case gradable at all: decode's ``read``
raises ``ModelRetry`` on a missing path (``No such file: 'README'.``), so the failed leg leaves a
``RetryPromptPart`` in the history and KEEPS its ``ToolCallPart`` — the part
:mod:`evals.harness.driver` reads into ``tool_calls``. Both mined runs recovered on the next leg and
both would still score ``0.0``
(``tests/unit/evals/regression/test_cases_mined.py::test_a_read_that_raised_is_still_recorded_so_recovering_cannot_hide_the_guess``).

``fixed_in`` is ``"unfixed"``: nothing on this branch changes how the agent picks its first path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.harness.metrics import ToolArgsNeverMetric
from evals.regression.case import RegressionCase

# A repo root in miniature: the readme the prompt names loosely is README.md, exactly as in the two
# mined traces, and a couple of neighbours so a listing is worth doing.
SEEDED_FILES: dict[str, str] = {
    "README.md": "# sample-service\n\nA small service used in the docs.\n",
    "pyproject.toml": '[project]\nname = "sample-service"\nversion = "0.1.0"\n',
    "src/app/main.py": 'def main() -> None:\n    print("hello from sample-service")\n',
}

# What a read is allowed to open: the basenames the fixture actually seeded.
SEEDED_NAMES = frozenset(Path(relative).name for relative in SEEDED_FILES)


def _reads_a_path_the_tree_does_not_hold(args: dict[str, Any]) -> bool:
    """True when this ``read`` call names a file the fixture never seeded — the mined violation.

    Compared by basename: the agent may open ``README.md`` or the absolute path to it inside the temp
    Workspace, and both are the same correct behavior; ``README`` is the guess the traces showed. A
    call with no usable ``path`` counts as a violation — a read that cannot name its target is not a
    verified one.
    """
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return True
    return Path(path).name not in SEEDED_NAMES


def _fixture(workspace: Path) -> None:
    """Seed the miniature repo root the prompt talks about."""
    for relative, body in SEEDED_FILES.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


CASE = RegressionCase(
    id="22-guessed-file-path",
    prompt="add a hello line to README",
    fixture=_fixture,
    difficulty="easy",
    symptom=(
        "the first tool call opened a guessed path (`README`) the tree does not hold, so the read "
        "failed with a retry and a whole model leg went on recovering — in both recorded runs."
    ),
    assertion=(
        "The response reports the change it made to the project's readme file, naming the file it "
        "actually edited."
    ),
    metrics=[
        ToolArgsNeverMetric(
            "read",
            _reads_a_path_the_tree_does_not_hold,
            description="a path the Workspace does not hold",
            name="read_path_exists",
        )
    ],
    max_requests=8,
    tags=["mined", "path-discipline", "read"],
    source_trace_id="01a08614-aaf4-752a-857a-209f2ca411c9",
    thread_id="69b1d5ef-b0b7-4788-9488-3d1870241ae3",
    fixed_in="unfixed",
)
