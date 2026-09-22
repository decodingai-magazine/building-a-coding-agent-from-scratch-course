"""Record a seed batch of ``decode run`` Sessions so an investigation has something to judge.

    uv run python scripts/seed_kitaru_sessions.py              # 30 runs, 3 at a time
    uv run python scripts/seed_kitaru_sessions.py --dry-run    # print the 30 commands, run nothing
    uv run python scripts/seed_kitaru_sessions.py --only cutoff --limit 2

Thirty scenarios over ten prompts, in the three shapes decode's own Opik history shows
(``python -m evals mine --preset errors``), so the verdicts in an investigation can draw a line:

* ``good`` — a small task the agent finishes; verdict ``acceptable``;
* ``cutoff`` — a multi-step task under ``--max-requests 1``: the run stops on
  ``UsageLimitExceeded`` after its first tool call, task unfinished; verdict ``problematic``;
* ``crash`` — the model request itself fails, never the agent: ``--model no-such-model``
  (``ModelHTTPError``), or the provider made unreachable / unauthenticated for that one run
  (``httpx.ConnectError`` on modal, a ``400`` on gemini, a ``401`` on openrouter); verdict
  ``problematic``. On modal these are what ``decode-connection-error`` flags; the cut-off runs are
  what ``decode-request-limit`` flags.

Every run is one Kitaru Session (both ``KITARU_*`` lines in ``.env``); the script refuses to start
when recording is off, because thirty unrecorded runs are thirty wasted model calls. An operator
script like ``bootstrap_kitaru.py``: ``click.echo`` output, ``decode run`` as a subprocess with
``--summary-json`` for the session id / exit reason / error, nothing imported from ``evals``.
The Kitaru Session is found by that decode session id (= its ``session_name``, ADR-0022 §10); a
run the seam could not record says so with ONE stderr line, which is what "NOT RECORDED" reports.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import click

Kind = Literal["good", "cutoff", "crash"]
KINDS: tuple[Kind, ...] = ("good", "cutoff", "crash")

# A model id no provider serves: the request fails before the agent loop starts.
BOGUS_MODEL = "no-such-model"

# One `decode run` = one Session; a seed run that hangs on a dead endpoint is still bounded.
RUN_TIMEOUT_SECONDS = 600

# The Recording Seam's one degrade line (runtime/recording.py): present = this run was not recorded.
NOT_RECORDED_MARKER = "[kitaru] not recording this run"

# Small tasks the agent finishes in a few requests.
GOOD_PROMPTS: tuple[str, ...] = (
    "say hi in exactly three words",
    "which python version does this repo target? read pyproject.toml and answer in one line",
    "count the lines in Makefile and report the number",
    "list the make targets in Makefile, one per line, no descriptions",
    "what does `make ci` run? read the Makefile and answer in two sentences",
    "read README.md and give the project's one-line description",
    "how many ADRs are under docs/adr? answer with the number only",
)

# Tasks that need several tool calls; under --max-requests 1 they stop after the first one.
CUTOFF_PROMPTS: tuple[str, ...] = (
    "list every python file under src, then count their total lines",
    "find every TODO in src and summarise them as a table",
    "read docs/glossary.md, then check which of its terms appear in AGENTS.md",
)


@dataclass(frozen=True)
class Scenario:
    """One seed run: what it asks and how it is made to succeed or fail."""

    number: int
    kind: Kind
    prompt: str
    extra_argv: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    """What one seed run left behind, from `decode run --summary-json`."""

    scenario: Scenario
    returncode: int
    session_id: str | None
    recorded: bool
    exit_reason: str | None
    error: str | None


def crash_env(provider: str) -> dict[str, str]:
    """The one env override that makes the provider's request fail for a single run.

    Process env beats ``.env`` (ADR-0021), so the child sees the broken value and nothing else
    changes. Each provider fails its own way: modal cannot connect, gemini rejects the key with a
    400, openrouter with a 401.
    """
    if provider == "modal":
        return {"MODAL_ENDPOINT_URL": "http://127.0.0.1:9"}
    if provider == "gemini":
        return {"GEMINI_API_KEY": "invalid-seed-key"}
    if provider == "openrouter":
        return {"OPENROUTER_API_KEY": "invalid-seed-key"}
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}")


def scenarios(provider: str) -> list[Scenario]:
    """The 30 seed runs: 14 good, 8 cut off, 8 crashed, over ten prompts."""
    rows: list[Scenario] = []
    for i in range(14):
        rows.append(Scenario(len(rows) + 1, "good", GOOD_PROMPTS[i % len(GOOD_PROMPTS)]))
    for i in range(8):
        prompt = CUTOFF_PROMPTS[i % len(CUTOFF_PROMPTS)]
        rows.append(Scenario(len(rows) + 1, "cutoff", prompt, extra_argv=("--max-requests", "1")))
    for i in range(4):
        prompt = GOOD_PROMPTS[i % len(GOOD_PROMPTS)]
        rows.append(Scenario(len(rows) + 1, "crash", prompt, extra_argv=("--model", BOGUS_MODEL)))
    for i in range(4):
        prompt = GOOD_PROMPTS[(i + 4) % len(GOOD_PROMPTS)]
        rows.append(Scenario(len(rows) + 1, "crash", prompt, env=crash_env(provider)))
    return rows


def run_argv(scenario: Scenario, summary_json: Path) -> list[str]:
    """The exact ``decode run`` command for one scenario, on this venv's interpreter."""
    return [
        sys.executable,
        "-m",
        "decode",
        "run",
        *scenario.extra_argv,
        "--summary-json",
        str(summary_json),
        scenario.prompt,
    ]


def render_command(scenario: Scenario) -> str:
    """One paste-able line per scenario, env overrides first — what ``--dry-run`` prints."""
    prefix = "".join(f"{key}={shlex.quote(value)} " for key, value in sorted(scenario.env.items()))
    argv = ["uv", "run", "decode", "run", *scenario.extra_argv, scenario.prompt]
    return f"{prefix}{shlex.join(argv)}"


def run_scenario(scenario: Scenario) -> Outcome:
    """Run one scenario to completion and read its summary; a missing summary is an error row."""
    with tempfile.TemporaryDirectory(prefix="decode-seed-") as tmp:
        summary_json = Path(tmp) / "summary.json"
        env = {**os.environ, **scenario.env}
        stderr = ""
        try:
            completed = subprocess.run(
                run_argv(scenario, summary_json),
                env=env,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT_SECONDS,
                check=False,
            )
            returncode, stderr = completed.returncode, completed.stderr
        except subprocess.TimeoutExpired as exc:
            returncode, stderr = 124, str(exc.stderr or "")
        summary = _read_summary(summary_json)
    return Outcome(
        scenario=scenario,
        returncode=returncode,
        session_id=summary.get("session_id"),
        recorded=bool(summary) and NOT_RECORDED_MARKER not in stderr,
        exit_reason=summary.get("exit_reason"),
        error=_error_line(summary.get("error")),
    )


def _read_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _error_line(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip().splitlines()[-1][:80]


def render_row(outcome: Outcome) -> str:
    """``#  kind  exit_reason  session id  [NOT RECORDED]  error`` — one line per finished run."""
    session = outcome.session_id or "-"
    recorded = "" if outcome.recorded else "  NOT RECORDED"
    error = f"  {outcome.error}" if outcome.error else ""
    return (
        f"{outcome.scenario.number:>2}  {outcome.scenario.kind:<6}  "
        f"{outcome.exit_reason or 'no summary':<13}  {session}{recorded}{error}"
    )


def render_tally(outcomes: list[Outcome]) -> str:
    """Counts per kind, plus how many were actually recorded as Kitaru Sessions."""
    parts = [f"{kind}: {sum(1 for o in outcomes if o.scenario.kind == kind)}" for kind in KINDS]
    recorded = sum(1 for o in outcomes if o.recorded)
    return f"{len(outcomes)} run(s) — {', '.join(parts)} — {recorded} recorded as Kitaru Sessions"


def _provider() -> str:
    from decode.config.settings import settings

    return settings.llm_provider


def _recording_is_configured() -> bool:
    from decode.runtime.recording import recording_is_configured

    return recording_is_configured()


@click.command()
@click.option("--dry-run", is_flag=True, help="Print the decode commands instead of running them.")
@click.option(
    "--only", type=click.Choice(KINDS), default=None, help="Run one kind of scenario only."
)
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Run at most N scenarios.")
@click.option(
    "--jobs", type=click.IntRange(min=1), default=3, show_default=True, help="Runs at once."
)
def main(dry_run: bool, only: Kind | None, limit: int | None, jobs: int) -> None:
    """Record 30 decode runs (good / cut off / crashed) as Kitaru Sessions."""
    selected = [s for s in scenarios(_provider()) if only is None or s.kind == only]
    if limit is not None:
        selected = selected[:limit]
    if dry_run:
        for scenario in selected:
            click.echo(f"# {scenario.number:>2} {scenario.kind}")
            click.echo(render_command(scenario))
        return
    if not _recording_is_configured():
        click.echo(
            "seed: recording is off — put KITARU_API_URL + KITARU_AGENT_ID in .env first "
            "(running_the_code/06_evals_replays.md §1)."
        )
        sys.exit(1)
    click.echo(
        f"seed: {len(selected)} run(s), {jobs} at a time; one line per finished run — "
        "`kitaru session list --agent decode` shows them by decode session id."
    )
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        outcomes: list[Outcome] = []
        for outcome in pool.map(run_scenario, selected):
            outcomes.append(outcome)
            click.echo(render_row(outcome))
    click.echo(render_tally(outcomes))
    if not any(o.recorded for o in outcomes):
        click.echo("seed: nothing was recorded — check `uv run kitaru status` and the .env lines.")
        sys.exit(1)


if __name__ == "__main__":
    main()
