"""Where a headless run gets its task: the CLI argument, else a Worker Task's inputs (ADR-0019 §4).

A Kitaru Worker replaying a Session spawns the **Agent Version**'s command in a fresh process with
``KITARU_TASK_ID`` (and usually ``KITARU_TASK_INPUTS``) in the env — the command line carries no
prompt, because the prompt belongs to the task, not to the registered command. So ``decode run``'s
``TASK`` argument is optional and :func:`resolve_task_and_model` settles where the run's task (and
its Model Override) actually come from:

1. **The CLI argument wins.** ``decode run "<task>"`` is unchanged, and on this path kitaru is never
   even imported — which is what keeps the "no kitaru import unless recording is configured (or a
   Worker Task / remote bucket)" invariant true for every user-launched run (ADR-0019 §3).
2. **Else a Worker Task's inputs**, read through ``kitaru.task.get_task_inputs()`` — the accessor
   itself (kitaru 0.22.2) returns ``json.loads(KITARU_TASK_INPUTS)`` when that variable is set and
   otherwise fetches the task spec over one synchronous request. decode does not second-guess it:
   this module owns the CONTRACT of what comes back, not the transport.
3. **Else ONE friendly line** and a non-zero exit — the missing-argument case, phrased so it names
   both ways to supply a task.

The input contract is ``{"task": "<prompt>", "model": "<id>" | null}``: ``task`` required, ``model``
optional and mapped to the same Model Override ``--model`` sets. Anything else — malformed JSON, a
missing/blank ``task``, a non-string ``model``, an unreadable inputs channel — is a
:class:`WorkerTaskInputError`, never a fallback. A Worker replay that invented its own prompt would
produce an experiment whose result means nothing, so it fails loudly instead of guessing.

``KITARU_REPLAY_ID`` is deliberately absent here: replay plumbing is adapter-native, and decode
reads only the task inputs (ADR-0019 §4).
"""

from __future__ import annotations

import logging

from decode.runtime.recording import is_worker_task, one_line

logger = logging.getLogger(__name__)

# Set by a Kitaru Worker alongside ``KITARU_TASK_ID``; named in the failure lines because it is the
# variable an operator can actually inspect. Read by ``kitaru.task``, never by decode.
TASK_INPUTS_ENV = "KITARU_TASK_INPUTS"

# The one friendly line for "no task anywhere" — both ways to supply one, in a single sentence.
NO_TASK_MESSAGE = (
    'decode run needs a TASK to run: pass it as an argument (decode run "<task>"), or launch it as '
    f"a Kitaru Worker Task, which supplies it in {TASK_INPUTS_ENV}."
)

# How much of the received inputs rides on a failure line before it is cut — enough to recognise the
# payload, short enough to stay one readable line.
_INPUTS_MAX_CHARS = 120


class WorkerTaskInputError(RuntimeError):
    """No runnable task: none was given, or a Worker Task's inputs could not be trusted."""


def resolve_task_and_model(task: str | None, model: str | None) -> tuple[str, str | None]:
    """Return ``(task, model-override)`` for this headless run (ADR-0019 §4).

    ``task`` / ``model`` are the CLI argument and ``--model`` flag, either of which may be ``None``.
    An explicit value always wins over the Worker Task's inputs — an operator debugging a replay by
    hand overrides it from the command line — and a CLI task short-circuits the whole worker branch,
    so no kitaru module is imported on a user-launched path.

    Raises :class:`WorkerTaskInputError` (one line, no traceback) when there is no task at all, or
    when a Worker Task's inputs are unreadable or off-contract.
    """
    explicit_task = (task or "").strip()
    explicit_model = _clean_model_override(model)
    if explicit_task:
        return explicit_task, explicit_model

    if not is_worker_task():
        raise WorkerTaskInputError(NO_TASK_MESSAGE)

    inputs = _read_worker_task_inputs()
    worker_task = _task_from_inputs(inputs)
    worker_model = _model_from_inputs(inputs)
    logger.debug(
        "task taken from the Kitaru Worker Task inputs (model=%r, cli model=%r)",
        worker_model,
        explicit_model,
    )
    return worker_task, explicit_model if explicit_model is not None else worker_model


def _clean_model_override(model: str | None) -> str | None:
    """A Model Override as ``None`` (use the configured model) or a non-blank model id."""
    cleaned = (model or "").strip()
    return cleaned or None


def _read_worker_task_inputs() -> object:
    """This Worker Task's raw inputs, via ``kitaru.task`` — the ONLY kitaru import on this path.

    The import lives inside the function (and inside the ``is_worker_task()`` branch of
    :func:`resolve_task_and_model`) so a user-launched ``decode run`` never loads kitaru at all.

    Every failure mode of the accessor — malformed ``KITARU_TASK_INPUTS`` JSON, a spec fetch with no
    ``KITARU_API_URL``, an HTTP error, a task that is not an agent task — becomes one
    :class:`WorkerTaskInputError` naming the cause. The cause is kept as ``__cause__`` for the log.
    """
    try:
        from kitaru.task import get_task_inputs

        return get_task_inputs()
    except Exception as error:  # broad on purpose: any unreadable inputs channel fails the run
        raise WorkerTaskInputError(
            f"this Kitaru Worker Task's inputs could not be read: {one_line(error)}. decode run "
            f"reads its task from {TASK_INPUTS_ENV} (or the task spec) when no TASK argument is "
            "given, and a Worker replay must never guess its own prompt."
        ) from error


def _task_from_inputs(inputs: object) -> str:
    """The required ``task`` prompt, or a :class:`WorkerTaskInputError` naming what arrived."""
    if isinstance(inputs, dict):
        task = inputs.get("task")
        if isinstance(task, str) and task.strip():
            return task.strip()
    raise WorkerTaskInputError(
        f"this Kitaru Worker Task's inputs carry no runnable 'task': expected "
        f'{{"task": "<prompt>", "model": "<id>"|null}} in {TASK_INPUTS_ENV}, got {_summarize(inputs)}'
    )


def _model_from_inputs(inputs: object) -> str | None:
    """The optional ``model`` id as a Model Override; ``null`` / blank / absent → the default."""
    model = inputs.get("model") if isinstance(inputs, dict) else None
    if model is None:
        return None
    if not isinstance(model, str):
        raise WorkerTaskInputError(
            f"this Kitaru Worker Task's inputs carry a 'model' that is not a model id: expected a "
            f"string or null in {TASK_INPUTS_ENV}, got {_summarize(model)}"
        )
    return _clean_model_override(model)


def _summarize(value: object) -> str:
    """``value`` as one short, single-line repr for a failure message."""
    text = " ".join(repr(value).split())
    return text if len(text) <= _INPUTS_MAX_CHARS else f"{text[:_INPUTS_MAX_CHARS]}…"
