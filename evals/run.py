"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark; ``regression`` runs the behavior cases host-native;
``suite`` runs natural-language assertions; ``mine`` searches live traces; ``sync`` updates datasets.
Langfuse and its evaluation harness are imported lazily so CLI help never needs keys or a network.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Iterator

_LANGFUSE_AUTH_STATUSES = frozenset({401, 403})


def _langfuse_error_as_click(exc: Exception) -> click.ClickException:
    """Turn a Langfuse SDK failure into one safe, actionable CLI line."""
    status = getattr(exc, "status_code", None)
    if status in _LANGFUSE_AUTH_STATUSES:
        message = (
            f"Langfuse rejected the API keys ({status}) — check LANGFUSE_PUBLIC_KEY and "
            "LANGFUSE_SECRET_KEY (see the Evals block in .env.example)."
        )
    else:
        detail = f" ({status})" if status is not None else ""
        message = (
            f"Langfuse request failed{detail} — check Langfuse credentials and project "
            "(see the Evals block in .env.example)."
        )
    return click.ClickException(f"evals: {message}")


@contextmanager
def langfuse_boundary() -> Iterator[None]:
    """Translate Langfuse API exceptions without leaking response bodies or credentials."""
    try:
        yield
    except Exception as exc:
        if not type(exc).__module__.startswith("langfuse."):
            raise
        raise _langfuse_error_as_click(exc) from exc


def _validate_job_name(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Reject a ``--job-name`` that is not one safe path segment, before any (billed) trial starts.

    The harness owns the rule (``evals.harness.benchmark.validate_job_name``) because every caller
    of ``new_job_dir`` must obey it; this callback only re-dresses it as the same one-line
    ``Invalid value for '--job-name'`` the other flags produce. Imported lazily so ``--help``
    remains network- and credential-free.
    """
    if value is None:
        return None
    from evals.harness.benchmark import validate_job_name

    try:
        return validate_job_name(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc


def _echo_langfuse_reporting(track: str, reported: int | None) -> None:
    """Make whether Langfuse received evaluation scores visible in the CLI."""
    if reported is None:
        click.echo(
            f"evals {track}: Langfuse score reporting skipped — configure "
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY."
        )
    else:
        click.echo(f"evals {track}: reported {reported} score(s) to Langfuse.")


@click.group()
def cli() -> None:
    """decode eval suite — benchmark + regression harness (ADR-0017)."""


@cli.command()
@click.option(
    "--task",
    "task_ids",
    multiple=True,
    help="Run only this benchmark task id (repeat for a hand-picked subset in one experiment).",
)
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only tasks of this difficulty tier.",
)
@click.option(
    "--sandbox",
    type=click.Choice(["docker", "modal"]),
    default="docker",
    show_default=True,
    help="The sandbox rung each Trial's `decode run` executes in.",
)
@click.option(
    "--trials",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Trials per task — the pass@k / pass^k / flakiness axis.",
)
@click.option(
    "--threads",
    type=click.IntRange(min=1),
    default=None,
    help="Trials to run at once [default: 1 for docker, 4 for modal].",
)
@click.option(
    "--job-name",
    default=None,
    callback=_validate_job_name,
    help="Name the job: the Langfuse experiment and Trial Dir parent [default: bench-<UTC stamp>].",
)
@click.option(
    "--model",
    default=None,
    help="Override the model every Trial runs on (`decode run --model`).",
)
def benchmark(
    task_ids: tuple[str, ...],
    difficulty: str | None,
    sandbox: str,
    trials: int,
    threads: int | None,
    job_name: str | None,
    model: str | None,
) -> None:
    """Run the outcome benchmark as one Langfuse experiment (ADR-0022 §1,§3,§6).

    Each selected task runs ``--trials`` Benchmark Trials: a subprocess ``decode run`` against a
    fresh Seed Repo with ``SANDBOX_MODE=--sandbox``, graded host-side by the hidden ``tests/test.sh``
    Verifier on a pristine clone of the handed-back branch, with every trial's evidence left in its
    Trial Dir under ``.decode/evals/runs/<job>/``. The reward is the score of record; pass@1 / pass@k
    / pass^k / flakiness / cost ride ``experiment_scoring_functions`` onto the experiment row and are
    printed here as a Rich table with per-tier rollups, followed by one spend line — the job's wall
    clock, summed agent-run seconds and summed tokens, the raw axes a $/hour or a $/Mtok price
    multiplies (ADR-0022 Amendment §14). Langfuse + the harness are imported lazily so ``--help`` never
    needs keys or a network (ADR-0017 §1).
    """
    from rich.console import Console

    from evals.harness.aggregates import render_spend_line, render_summary_table
    from evals.harness.benchmark import BenchmarkSelectionError, run_benchmark, summarize
    from evals.harness.langfuse_reporting import report_benchmark_scores

    # One ``--task`` stays a plain id (the common spelling); several become the subset.
    task_id: str | tuple[str, ...] | None = (
        None if not task_ids else task_ids[0] if len(task_ids) == 1 else task_ids
    )
    try:
        with langfuse_boundary():
            run = run_benchmark(
                task_id=task_id,
                difficulty=difficulty,
                sandbox=sandbox,
                trials=trials,
                threads=threads,
                job_name=job_name,
                model=model,
            )
    except BenchmarkSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    summary = summarize(run.result, trials=trials)
    Console().print(render_summary_table(summary))
    click.echo(render_spend_line(summary))
    click.echo(
        f"evals benchmark: experiment {run.experiment_name} logged under "
        f"{settings_project_name()}; trial dirs in {run.job_dir}."
    )
    _echo_langfuse_reporting("benchmark", report_benchmark_scores(run.result))


def settings_project_name() -> str:
    """The Langfuse project eval runs log under; read lazily so CLI help stays credential-free."""
    from decode.config.settings import settings

    return settings.eval_project_name


@cli.command()
@click.option("--case", "case_id", default=None, help="Run only this regression case id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only the cases of this difficulty tier.",
)
def regression(case_id: str | None, difficulty: str | None) -> None:
    """Run the behavior Regression Cases host-native as a Langfuse experiment (ADR-0022 §8).

    Each selected case seeds a fresh temp Workspace, runs the real agent HOST-NATIVE (``none`` mode —
    no docker) under the case's gate policy, and scores its behavior with the case's deterministic
    metrics. ``--difficulty`` slices the run to one tier and names the experiment after it
    (``decode-regression-gate-hard``), so a tier's baseline stays its own. Langfuse + the harness are
    imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.langfuse_reporting import report_regression_scores
    from evals.harness.regression import RegressionSelectionError, run_regression

    try:
        with langfuse_boundary():
            result = run_regression(case_id=case_id, difficulty=difficulty)
    except RegressionSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"evals regression: experiment logged under {settings_project_name()}.")
    _echo_langfuse_reporting("regression", report_regression_scores(result))


@cli.command()
@click.option("--case", "case_id", default=None, help="Run only this regression case id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only the cases of this difficulty tier.",
)
def suite(case_id: str | None, difficulty: str | None) -> None:
    """Run the Langfuse experiment regression surface — natural-language assertions (ADR-0022 §8).

    The CONTRAST to ``regression``: the same cases, graded by an LLM judge against each case's
    ``assertion`` (plus the suite's global bars) instead of deterministic metrics, gated on the run's
    ``pass_rate``. ``decode-regression-suite`` is first reconciled to exactly the selected cases;
    its checksum version is included in experiment metadata and the line below names the run.
    Langfuse + the
    harness are imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.datasets import REGRESSION_SUITE_NAME
    from evals.harness.langfuse_reporting import report_suite_scores
    from evals.harness.test_suite import (
        SUITE_PASS_BAR,
        SuiteJudgeRouteError,
        SuitePassRateError,
        SuiteSelectionError,
        assert_pass_rate,
        run_test_suite,
    )

    try:
        with langfuse_boundary():
            run = run_test_suite(case_id=case_id, difficulty=difficulty)
    except (SuiteSelectionError, SuiteJudgeRouteError) as exc:
        raise click.ClickException(str(exc)) from exc

    pass_rate = run.result.pass_rate
    click.echo(
        f"evals suite: {REGRESSION_SUITE_NAME} {run.suite_version} pass rate {pass_rate:.0%} (bar {SUITE_PASS_BAR:.0%}), "
        f"logged under {settings_project_name()}; report in {run.report_path}."
    )
    _echo_langfuse_reporting(
        "suite",
        report_suite_scores(
            run.session_ids,
            pass_rate=pass_rate,
            suite_version=run.suite_version,
        ),
    )
    try:
        assert_pass_rate(pass_rate)
    except SuitePassRateError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option(
    "--preset",
    type=click.Choice(["errors", "long", "denied", "all"]),
    default="errors",
    show_default=True,
    help="Which regression shape to mine for.",
)
@click.option(
    "--since",
    default=None,
    help="Only traces after this tz-aware ISO timestamp (e.g. 2026-09-04T00:00:00Z).",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="Traces fetched per preset (also the window the client-side presets rank within).",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the groups as JSON.")
def mine(preset: str, since: str | None, limit: int, as_json: bool) -> None:
    """Mine the LIVE project for regressions worth turning into cases (ADR-0022 §8).

    Searches live Langfuse observations (never `EVAL_PROJECT_NAME`), clusters
    every hit by its Signature — (preset, error, last tool, model) — and prints one table per
    signature with the trace ids to pick from; `--json` emits the same groups, uncapped, for task
    164's case authoring. Read-only, so it skips without inference-provider credentials;
    Langfuse + the harness are imported lazily so `--help` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.keys import eval_keys_missing
    from evals.harness.mine import (
        MineError,
        groups_as_json,
        mine_console,
        open_source,
        render_group_table,
    )

    # Mining is a read-only trace query, never an inference call.
    missing = eval_keys_missing(require_provider=False)
    if missing:
        click.echo("evals mine: skipped — set " + ", ".join(missing) + " to search live traces.")
        return

    from evals.harness.mine import mine as mine_traces

    try:
        with langfuse_boundary():
            source = open_source()
            groups = mine_traces(source, preset=preset, since=since, limit=limit)
    except MineError as exc:
        raise click.ClickException(f"evals mine: {exc}") from exc

    if as_json:
        click.echo(groups_as_json(groups))
        return

    if not groups:
        click.echo(f"evals mine: no {preset} traces in {source.project}.")
        return

    console = mine_console()
    for group in groups:
        console.print(render_group_table(group))
    total = sum(group.count for group in groups)
    click.echo(
        f"evals mine: {total} trace(s) in {len(groups)} signature(s) from {source.project} "
        f"(preset {preset})."
    )


@cli.command()
@click.option(
    "--benchmark/--no-benchmark",
    "benchmark",
    default=True,
    show_default=True,
    help="Sync the benchmark tasks into the decode-benchmark Langfuse dataset.",
)
@click.option(
    "--regression/--no-regression",
    "regression",
    default=True,
    show_default=True,
    help="Sync the Regression Cases into the decode-regression dataset AND their Test Suite.",
)
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Sync only the tasks / cases of this difficulty tier.",
)
def sync(benchmark: bool, regression: bool, difficulty: str | None) -> None:
    """Upsert the eval tracks' Langfuse datasets (ADR-0022 §6,8).

    ``--benchmark`` loads ``evals/benchmark/tasks/`` into ``decode-benchmark``; ``--regression``
    loads the case registry into BOTH regression surfaces from one pass — the ``decode-regression``
    dataset the metric gate scores and the ``decode-regression-suite`` Test Suite the
    natural-language assertions are judged in, reconciled to exactly the synced cases so an edit
    replaces its stale item instead of adding a second (both on by default, and the line below names
    the suite version it left). A skip-guarded case stays in the registry but is
    not registered: Langfuse datasets carry what actually runs. ``--difficulty`` slices the upsert to
    one tier, so ``make eval-regression-dataset ARGS='--difficulty hard'`` syncs and gates the same eight
    cases. Langfuse is imported lazily here (not at CLI build time) so ``--help`` never needs keys or a
    network.
    """
    if not benchmark and not regression:
        click.echo("evals sync: nothing selected (pass --benchmark and/or --regression).")
        return

    # Lazy import: keeps the CLI module credential-free at import time (ADR-0017 §1).
    from evals.harness.datasets import (
        BENCHMARK_DATASET_NAME,
        REGRESSION_DATASET_NAME,
        REGRESSION_SUITE_NAME,
        sync_benchmark_dataset,
        sync_regression_cases,
    )

    with langfuse_boundary():
        if benchmark:
            from evals.harness.benchmark import select_tasks
            from evals.harness.task_loader import load_benchmark_tasks

            tasks = select_tasks(load_benchmark_tasks(), task_id=None, difficulty=difficulty)
            sync_benchmark_dataset(tasks)
            click.echo(f"evals sync: upserted {len(tasks)} task(s) into {BENCHMARK_DATASET_NAME}.")

        if regression:
            from evals.regression.loader import load_cases, runnable_cases, select_cases

            selected = select_cases(load_cases(), difficulty=difficulty)
            cases = runnable_cases(selected)
            skipped = [case.id for case in selected if case.skip_reason is not None]
            note = f" ({len(skipped)} skipped: {', '.join(skipped)})" if skipped else ""
            surfaces = sync_regression_cases(cases)
            click.echo(
                f"evals sync: upserted {len(cases)} case(s){note} into {REGRESSION_DATASET_NAME} "
                f"and {REGRESSION_SUITE_NAME} {surfaces.suite_version}."
            )


@cli.group("kitaru")
def kitaru() -> None:
    """Join decode's Langfuse traces to Kitaru Sessions (ADR-0022 §11).

    Two thin commands over ONE join key, the decode session id: ``import`` backfills Sessions for
    traces decode never recorded, ``cohort from-experiment`` freezes a benchmark job's FAILING trials
    into a replayable Cohort. Both shell out to the ``kitaru`` CLI (the project's CLI-only rule) and
    both need ``KITARU_API_URL`` (exported or in ``.env``, ADR-0022 §18) — register the server's
    agent / importer / evaluators first with ``make kitaru-bootstrap`` (``make kitaru-local`` does both).
    """


def _kitaru_skip(command: str, purpose: str) -> bool:
    """Print the ONE friendly skip line when a key is missing; True means "do nothing"."""
    from evals.harness.kitaru_cli import kitaru_keys_missing

    missing = kitaru_keys_missing()
    if not missing:
        return False
    click.echo(f"evals kitaru {command}: skipped — set " + ", ".join(missing) + f" to {purpose}.")
    return True


@kitaru.command("import")
@click.argument("trace_ids", nargs=-1)
@click.option(
    "--thread",
    "threads",
    multiple=True,
    help="Import this thread (a decode session id) whole; repeatable, and needs no trace id.",
)
@click.option(
    "--project",
    default=None,
    help="Langfuse project to read the traces from [default: the configured project].",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="With no trace id / --thread: import the project's newest N threads [default: all].",
)
@click.option("--agent", default="decode", show_default=True, help="Kitaru agent (NAME or NAME@N).")
@click.option(
    "--importer",
    "importer",
    default="langfuse@1",
    show_default=True,
    help="Importer (NAME or NAME@N).",
)
@click.option(
    "--tag", default="regression-case", show_default=True, help="Tag every imported Session."
)
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for the Worker to settle the import (a tag and the session id need it).",
)
def kitaru_import(
    trace_ids: tuple[str, ...],
    threads: tuple[str, ...],
    project: str | None,
    limit: int | None,
    agent: str,
    importer: str,
    tag: str,
    wait: bool,
) -> None:
    """Backfill Kitaru Sessions from Langfuse traces (ADR-0022 §11).

    No trace id and no ``--thread`` = the whole project, newest thread first, capped by ``--limit``.
    Each trace id is expanded to its whole THREAD — one decode session — fetched from Langfuse,
    written in the ``langfuse`` importer's envelope under ``.decode/kitaru-imports/<thread>.json``
    and handed to ``kitaru session import``. A thread Kitaru already has is skipped (matched on the
    importer's ``external_id``), so re-running costs one read. A live Worker must be claiming
    (``uv run kitaru worker start``) or ``--wait`` times out: the server executes nothing (ADR-0019).
    Langfuse + kitaru are imported lazily so ``--help`` needs no keys and no network (ADR-0017 §1).
    """
    if limit is not None and (trace_ids or threads):
        raise click.UsageError("--limit applies only when no trace id / --thread is given.")
    if _kitaru_skip("import", "backfill sessions"):
        return

    from evals.harness.kitaru_cli import KitaruCommandError, kitaru_server, resolve_ref
    from evals.harness.kitaru_import import (
        TraceImportError,
        format_outcome,
        open_source,
        run_import,
    )

    server = kitaru_server()
    try:
        with langfuse_boundary():
            source = open_source(project)
            if not trace_ids and not threads:
                threads = tuple(source.recent_threads(limit))
            outcomes = run_import(
                source,
                trace_ids=list(trace_ids),
                threads=list(threads),
                agent_ref=resolve_ref("agent", agent, server=server),
                importer_ref=resolve_ref("importer", importer, server=server),
                tag=tag,
                wait=wait,
                server=server,
            )
    except (TraceImportError, KitaruCommandError) as exc:
        raise click.ClickException(f"evals kitaru import: {exc}") from exc

    if not outcomes:
        click.echo("evals kitaru import: nothing to import (the Langfuse project has no sessions).")
        return
    for outcome in outcomes:
        click.echo(format_outcome(outcome))
    imported = sum(1 for outcome in outcomes if outcome.status == "imported")
    click.echo(
        f"evals kitaru import: {imported} of {len(outcomes)} thread(s) imported from "
        f"{source.project} into {server}."
    )


@kitaru.group("cohort")
def kitaru_cohort() -> None:
    """Freeze Kitaru Sessions into a replayable Cohort (ADR-0022 §11)."""


@kitaru_cohort.command("from-experiment")
@click.argument("experiment_name")
@click.option(
    "--cohort",
    "cohort_name",
    default="decode-benchmark-failures",
    show_default=True,
    help="The cohort the failing trials are frozen into.",
)
@click.option("--agent", default="decode", show_default=True, help="Kitaru agent (name or UUID).")
def kitaru_cohort_from_experiment(experiment_name: str, cohort_name: str, agent: str) -> None:
    """Freeze a benchmark job's FAILING trials into the next Cohort version (ADR-0022 §11).

    Reads the Langfuse experiment's items, keeps the trials that scored 0 (an unscorable evaluation and
    an Infra Error are neither pass nor fail, so both are excluded), resolves each one's Kitaru
    Session by Session Name = the trial's decode ``session_id``, and adds the sessions the cohort
    does not already have. Refuses with ONE line when the experiment recorded no
    ``kitaru_agent_id`` or when no Session resolves — an empty or half-resolved cohort would make a
    replay experiment lie. Langfuse + kitaru are imported lazily (ADR-0017 §1).
    """
    if _kitaru_skip("cohort", "freeze a cohort"):
        return

    from evals.harness.kitaru_cli import KitaruCommandError, kitaru_server
    from evals.harness.kitaru_cohort import CohortError, build_cohort, open_experiment

    server = kitaru_server()
    try:
        with langfuse_boundary():
            items, config = open_experiment(experiment_name)
            outcome = build_cohort(items, config, cohort=cohort_name, agent=agent, server=server)
    except (CohortError, KitaruCommandError) as exc:
        raise click.ClickException(f"evals kitaru cohort: {exc}") from exc

    for trial in outcome.unresolved:
        click.echo(
            f"evals kitaru cohort: no Session for trial {trial.task_id or '?'} "
            f"(session_id={trial.session_id or '-'}) — not frozen."
        )
    if outcome.added == 0:
        click.echo(
            f"evals kitaru cohort: {outcome.version_ref} already holds every failing Session "
            f"({outcome.session_count}); nothing added."
        )
        return
    created = " (cohort created)" if outcome.created_cohort else ""
    click.echo(
        f"evals kitaru cohort: {outcome.version_ref} — {outcome.session_count} session(s), "
        f"{outcome.added} added from {experiment_name}{created}."
    )
