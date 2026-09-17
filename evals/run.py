"""The ``python -m evals`` CLI body — a Click group with the eval tracks as subcommands (ADR-0017).

``benchmark`` runs the outcome benchmark (ADR-0022 §1); ``regression`` runs the behavior cases
host-native (§8); ``suite`` runs the same cases against their natural-language assertions; ``sync``
upserts the Opik surfaces. Deliberately imports no ``opik`` at module scope — the Opik harness is
pulled in lazily by the tracks that need it, so building the CLI never needs keys or a network.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Iterator

# Opik HTTP statuses that mean "your credential was rejected" (a present-but-invalid OPIK_API_KEY).
_OPIK_AUTH_STATUSES = frozenset({401, 403})


def _opik_error_as_click(exc: Exception) -> click.ClickException:
    """Turn a raw Opik REST ``ApiError`` into ONE friendly CLI line (ADR-0017 §9; task 121).

    A present-but-invalid ``OPIK_API_KEY`` otherwise dumps a ~40-line ``ApiError`` traceback (HTTP
    headers and all) from ``make eval-regression`` — the ritual the docs tell every developer to
    type. An auth status (401/403) names the key exactly as the missing-key guard does; any other
    Opik failure still collapses to a single line naming the status. No secret is ever echoed.
    """
    status = getattr(exc, "status_code", None)
    if status in _OPIK_AUTH_STATUSES:
        message = (
            f"Opik rejected the API key ({status}) — check OPIK_API_KEY "
            "(see the Evals block in .env.example)."
        )
    else:
        detail = f" ({status})" if status is not None else ""
        message = (
            f"Opik request failed{detail} — check OPIK_API_KEY and your Opik workspace "
            "(see the Evals block in .env.example)."
        )
    return click.ClickException(f"evals: {message}")


@contextmanager
def opik_boundary() -> Iterator[None]:
    """Translate a raw Opik ``ApiError`` raised inside the block into a friendly ``ClickException``.

    Wraps each opik-reaching command body so a wrong key exits like a missing one — one line,
    non-zero, no traceback. ``opik`` is imported lazily here (not at module scope) so the CLI stays
    opik-free at build time and ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from opik.rest_api.core.api_error import ApiError

    try:
        yield
    except ApiError as exc:
        raise _opik_error_as_click(exc) from exc


def _validate_job_name(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Reject a ``--job-name`` that is not one safe path segment, before any (billed) trial starts.

    The harness owns the rule (``evals.harness.benchmark.validate_job_name``) because every caller
    of ``new_job_dir`` must obey it; this callback only re-dresses it as the same one-line
    ``Invalid value for '--job-name'`` the other flags produce. Imported lazily so ``--help``, which
    exits before non-eager callbacks run, still pulls in no ``opik`` (ADR-0017 §1).
    """
    if value is None:
        return None
    from evals.harness.benchmark import validate_job_name

    try:
        return validate_job_name(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), ctx=ctx, param=param) from exc


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
    help="Trials per task (Opik trial_count) — the pass@k / pass^k / flakiness axis.",
)
@click.option(
    "--threads",
    type=click.IntRange(min=1),
    default=None,
    help="Trials to run at once (Opik task_threads) [default: 1 for docker, 4 for modal].",
)
@click.option(
    "--job-name",
    default=None,
    callback=_validate_job_name,
    help="Name the job: the Opik experiment AND the Trial Dir parent [default: bench-<UTC stamp>].",
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
    """Run the outcome benchmark as one Opik Experiment (ADR-0022 §1,§3,§6).

    Each selected task runs ``--trials`` Benchmark Trials: a subprocess ``decode run`` against a
    fresh Seed Repo with ``SANDBOX_MODE=--sandbox``, graded host-side by the hidden ``tests/test.sh``
    Verifier on a pristine clone of the handed-back branch, with every trial's evidence left in its
    Trial Dir under ``.decode/evals/runs/<job>/``. The reward is the score of record; pass@1 / pass@k
    / pass^k / flakiness / cost ride ``experiment_scoring_functions`` onto the experiment row and are
    printed here as a Rich table with per-tier rollups, followed by one spend line — the job's wall
    clock, summed agent-run seconds and summed tokens, the raw axes a $/hour or a $/Mtok price
    multiplies (ADR-0022 Amendment §14). Opik + the harness are imported lazily so ``--help`` never
    needs keys or a network (ADR-0017 §1).
    """
    from rich.console import Console

    from evals.harness.aggregates import render_spend_line, render_summary_table
    from evals.harness.benchmark import BenchmarkSelectionError, run_benchmark, summarize

    # One ``--task`` stays a plain id (the common spelling); several become the subset.
    task_id: str | tuple[str, ...] | None = (
        None if not task_ids else task_ids[0] if len(task_ids) == 1 else task_ids
    )
    try:
        with opik_boundary():
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


def settings_project_name() -> str:
    """The Opik project eval runs log under — read lazily so the CLI stays opik/settings-light."""
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
    """Run the behavior Regression Cases host-native as an Opik experiment (ADR-0022 §8).

    Each selected case seeds a fresh temp Workspace, runs the real agent HOST-NATIVE (``none`` mode —
    no docker) under the case's gate policy, and scores its behavior with the case's deterministic
    metrics. ``--difficulty`` slices the run to one tier and names the experiment after it
    (``decode-regression-gate-hard``), so a tier's baseline stays its own. Opik + the harness are
    imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.regression import RegressionSelectionError, run_regression

    try:
        with opik_boundary():
            run_regression(case_id=case_id, difficulty=difficulty)
    except RegressionSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"evals regression: experiment logged under {settings_project_name()}.")


@cli.command()
@click.option("--case", "case_id", default=None, help="Run only this regression case id.")
@click.option(
    "--difficulty",
    type=click.Choice(["easy", "medium", "hard"]),
    default=None,
    help="Run only the cases of this difficulty tier.",
)
def suite(case_id: str | None, difficulty: str | None) -> None:
    """Run the Opik Test Suite regression surface — natural-language assertions (ADR-0022 §8).

    The CONTRAST to ``regression``: the same cases, graded by an LLM judge against each case's
    ``assertion`` (plus the suite's global bars) instead of deterministic metrics, gated on the run's
    ``pass_rate``. Since ``run_tests`` has no per-item filter, ``decode-regression-suite`` is first
    reconciled to exactly the selected cases — Opik versions it on every change, and the line below
    names the version the run billed. Opik + the
    harness are imported lazily so ``--help`` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.datasets import REGRESSION_SUITE_NAME
    from evals.harness.test_suite import (
        SUITE_PASS_BAR,
        SuitePassRateError,
        SuiteSelectionError,
        assert_pass_rate,
        run_test_suite,
    )

    try:
        with opik_boundary():
            run = run_test_suite(case_id=case_id, difficulty=difficulty)
    except SuiteSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    pass_rate = run.result.pass_rate
    click.echo(
        f"evals suite: {REGRESSION_SUITE_NAME} {run.suite_version} pass rate {pass_rate:.0%} (bar {SUITE_PASS_BAR:.0%}), "
        f"logged under {settings_project_name()}."
    )
    try:
        assert_pass_rate(pass_rate)
    except SuitePassRateError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.option(
    "--filter",
    "filter_string",
    default=None,
    help="Opik OQL clause scoping which threads to score (e.g. 'start_time > \"2026-07-01T00:00:00Z\"').",
)
def online(filter_string: str | None) -> None:
    """Score decode's LIVE REPL threads with one conversation-level judge (ADR-0017 §10).

    The production-eval track: instead of driving a run, it grades the traces decode ALREADY emitted
    from real sessions (ADR-0014), inside the LIVE project (``settings.opik_project_name``, NOT
    ``eval_project_name``), via ``evaluate_threads`` with a single conversation judge whose scores log
    back onto those threads. Skips friendly (no error) when keys are missing; ``--filter`` scopes the
    run to recent threads. Opik + the harness are imported lazily so ``--help`` never needs keys or a
    network (ADR-0017 §1).
    """
    from evals.harness.online import (
        format_thread_scores,
        live_project_name,
        online_keys_missing,
        run_online_eval,
    )

    missing = online_keys_missing()
    if missing:
        click.echo("evals online: skipped — set " + ", ".join(missing) + " to score live threads.")
        return

    with opik_boundary():
        result = run_online_eval(filter_string=filter_string)
    lines = format_thread_scores(result)
    if not lines:
        click.echo(f"evals online: no threads to score in {live_project_name()}.")
        return
    for line in lines:
        click.echo(line)
    click.echo(f"evals online: scored {len(lines)} thread(s) in {live_project_name()}.")


@cli.group("online-rule")
def online_rule() -> None:
    """Manage the Opik ONLINE RULE that scores live traces as they arrive (ADR-0022 §13)."""


@online_rule.command("create")
@click.option(
    "--project",
    default=None,
    help="Opik project for the rule [default: the LIVE project, settings.opik_project_name].",
)
@click.option(
    "--model",
    default=None,
    help="Judge model id as your Opik workspace spells it [default: from the eval routing].",
)
@click.option(
    "--sampling",
    type=click.FloatRange(min=0.0, max=1.0),
    default=1.0,
    show_default=True,
    help="Fraction of incoming traces the rule scores.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the exact create payload and write nothing.",
)
def online_rule_create(
    project: str | None, model: str | None, sampling: float, dry_run: bool
) -> None:
    """Create the `response_quality` LLM-as-judge online rule, idempotently (ADR-0022 §13).

    The README's seven-step UI walkthrough as one command: it resolves the LIVE project, looks for
    a rule already named `response_quality` (prints `already exists: <id>` and stops if there is
    one), else creates the LLM-as-judge rule carrying the qualitative prompt from
    `evals.harness.online_rule.RESPONSE_QUALITY_PROMPT`. Skips friendly (no error) without
    `OPIK_API_KEY` — the judge runs on Opik's own provider, so no inference key is needed here; Opik
    + the harness are imported lazily so `--help` never needs keys or a network.
    """
    from evals.harness.keys import eval_keys_missing
    from evals.harness.online_rule import (
        RULE_NAME,
        OnlineRuleError,
        create_response_quality_rule,
    )

    # Opik-only: the rule's judge runs on the workspace's own provider, so no inference key here.
    missing = eval_keys_missing(require_provider=False)
    if missing:
        click.echo(
            "evals online-rule: skipped — set " + ", ".join(missing) + " to create the online rule."
        )
        return

    try:
        with opik_boundary():
            outcome = create_response_quality_rule(
                project=project, model=model, sampling=sampling, dry_run=dry_run
            )
    except OnlineRuleError as exc:
        raise click.ClickException(f"evals online-rule: {exc}") from exc

    if outcome.action == "dry-run":
        click.echo(outcome.payload or "")
        click.echo(
            f"evals online-rule: dry run — would create {RULE_NAME} in {outcome.project} "
            f"on judge model {outcome.model}."
        )
        return
    if outcome.action == "exists":
        click.echo(f"evals online-rule: already exists: {outcome.rule_id} (in {outcome.project}).")
        return
    click.echo(
        f"evals online-rule: created {RULE_NAME} ({outcome.rule_id}) in {outcome.project} "
        f"on judge model {outcome.model}."
    )


@cli.command()
@click.option(
    "--preset",
    type=click.Choice(["errors", "low-quality", "long", "denied", "all"]),
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

    Searches the live Opik project (never `EVAL_PROJECT_NAME`) with the preset's filter, clusters
    every hit by its Signature — (preset, error, last tool, model) — and prints one table per
    signature with the trace ids to pick from; `--json` emits the same groups, uncapped, for task
    164's case authoring. Read-only, so it skips friendly (no error) without `OPIK_API_KEY` alone;
    Opik + the harness are imported lazily so `--help` never needs keys or a network (ADR-0017 §1).
    """
    from evals.harness.keys import eval_keys_missing
    from evals.harness.mine import (
        MineError,
        groups_as_json,
        mine_console,
        open_source,
        render_group_table,
    )

    # Opik-only: mining is a read-only trace query, never an inference call.
    missing = eval_keys_missing(require_provider=False)
    if missing:
        click.echo("evals mine: skipped — set " + ", ".join(missing) + " to search live traces.")
        return

    from evals.harness.mine import mine as mine_traces

    try:
        with opik_boundary():
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
    help="Sync the benchmark tasks into the decode-benchmark Opik dataset.",
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
    """Upsert the eval tracks' Opik surfaces (ADR-0022 §6,8).

    ``--benchmark`` loads ``evals/benchmark/tasks/`` into ``decode-benchmark``; ``--regression``
    loads the case registry into BOTH regression surfaces from one pass — the ``decode-regression``
    dataset the metric gate scores and the ``decode-regression-suite`` Test Suite the
    natural-language assertions are judged in, reconciled to exactly the synced cases so an edit
    replaces its stale item instead of adding a second (both on by default, and the line below names
    the suite version it left). A skip-guarded case stays in the registry but is
    not registered: the Opik surfaces carry what actually runs. ``--difficulty`` slices the upsert to
    one tier, so ``make eval-regression ARGS='--difficulty hard'`` syncs and gates the same eight
    cases. Opik is imported lazily here (not at CLI build time) so ``--help`` never needs keys or a
    network.
    """
    if not benchmark and not regression:
        click.echo("evals sync: nothing selected (pass --benchmark and/or --regression).")
        return

    # Lazy import: keeps the CLI module opik-free at import time (ADR-0017 §1).
    from evals.harness.datasets import (
        BENCHMARK_DATASET_NAME,
        REGRESSION_DATASET_NAME,
        REGRESSION_SUITE_NAME,
        sync_benchmark_dataset,
        sync_regression_cases,
    )

    with opik_boundary():
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
    """Join decode's Opik traces to Kitaru Sessions (ADR-0022 §11).

    Two thin commands over ONE join key, the decode session id: ``import`` backfills Sessions for
    traces decode never recorded, ``cohort from-experiment`` freezes a benchmark job's FAILING trials
    into a replayable Cohort. Both shell out to the ``kitaru`` CLI (the project's CLI-only rule) and
    both need an exported ``KITARU_API_URL`` — register the server's agent / importer / evaluators
    first with ``uv run python scripts/bootstrap_kitaru.py`` (``make kitaru-local`` does both).
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
    help="Opik project to read the traces from [default: the LIVE project].",
)
@click.option("--agent", default="decode", show_default=True, help="Kitaru agent (NAME or NAME@N).")
@click.option(
    "--importer", "importer", default="opik", show_default=True, help="Importer (NAME or NAME@N)."
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
    agent: str,
    importer: str,
    tag: str,
    wait: bool,
) -> None:
    """Backfill Kitaru Sessions from Opik traces (ADR-0022 §11).

    Each trace id is expanded to its whole THREAD — one decode session — fetched with the Opik SDK,
    written as the ``opik`` importer's own envelope under ``.decode/kitaru-imports/<thread>.json``
    and handed to ``kitaru session import``. A thread Kitaru already has is skipped (matched on the
    importer's ``external_id``), so re-running costs one read. A live Worker must be claiming
    (``uv run kitaru worker start``) or ``--wait`` times out: the server executes nothing (ADR-0019).
    Opik + kitaru are imported lazily so ``--help`` needs no keys and no network (ADR-0017 §1).
    """
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
        with opik_boundary():
            source = open_source(project)
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
        click.echo("evals kitaru import: nothing to import (pass trace ids or --thread).")
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

    Reads the Opik experiment's items, keeps the trials that scored 0 (a ``scoring_failed`` score and
    an Infra Error are neither pass nor fail, so both are excluded), resolves each one's Kitaru
    Session by Session Name = the trial's decode ``session_id``, and adds the sessions the cohort
    does not already have. Refuses with ONE line when the experiment recorded no
    ``kitaru_agent_id`` or when no Session resolves — an empty or half-resolved cohort would make a
    replay experiment lie. Opik + kitaru are imported lazily (ADR-0017 §1).
    """
    if _kitaru_skip("cohort", "freeze a cohort"):
        return

    from evals.harness.kitaru_cli import KitaruCommandError, kitaru_server
    from evals.harness.kitaru_cohort import CohortError, build_cohort, open_experiment

    server = kitaru_server()
    try:
        with opik_boundary():
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
