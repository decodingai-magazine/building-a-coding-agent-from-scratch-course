"""Worker Task inputs — where ``decode run`` gets its task when the CLI arg is absent (ADR-0019 §4).

Drives the real :func:`decode.runtime.task_inputs.resolve_task_and_model` against the REAL installed
``kitaru.task`` accessor, faked at the ENV boundary only: with ``KITARU_TASK_INPUTS`` set,
``get_task_inputs()`` is a pure ``json.loads`` of that variable (verified against kitaru 0.22.2), so
these tests exercise the actual upstream parsing with no server, no credentials and no network. The
one place a fake module appears is the precedence tests, where the point is that kitaru is *not*
consulted at all.

Three properties carry the design:

* **Precedence**: an explicit CLI task wins outright — kitaru is never even imported; else a Worker
  Task's inputs supply it; else ONE friendly line.
* **A Worker replay never guesses**: malformed inputs, a payload with no recorded prompt in it, or
  an unreadable inputs channel raise :class:`~decode.runtime.task_inputs.WorkerTaskInputError`
  (→ non-zero exit), because a replay that invented its own prompt is a lying experiment. The three
  payloads that DO carry a verbatim prompt — ``{"task": ...}``, an imported ``{"input": ...}`` and a
  bare prompt string — are read, and nothing else is (task 137).
* **The import invariant holds**: ``kitaru.task`` is imported inside the Worker branch only, so every
  user-launched path stays kitaru-free.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import uuid

import pytest

from decode.runtime.task_inputs import WorkerTaskInputError, resolve_task_and_model


@pytest.fixture
def _worker(monkeypatch):
    """Put this process in Worker Task mode, exactly as a Kitaru Worker's spawn env does."""
    monkeypatch.setenv("KITARU_TASK_ID", str(uuid.uuid4()))


def _worker_inputs(monkeypatch, payload: object) -> None:
    """Set ``KITARU_TASK_INPUTS`` to the JSON encoding of ``payload`` (what the Worker exports)."""
    monkeypatch.setenv("KITARU_TASK_INPUTS", json.dumps(payload))


def _kitaru_task_tripwire(monkeypatch) -> None:
    """Make ANY read of the Worker inputs channel explode — the CLI-arg path must never touch it."""

    module = types.ModuleType("kitaru.task")

    def _forbidden() -> object:
        raise AssertionError("kitaru.task must not be consulted when the CLI supplied the task")

    module.get_task_inputs = _forbidden  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kitaru.task", module)


# --- precedence: an explicit CLI task wins, and costs no kitaru read -----------------------------


def test_an_explicit_task_is_returned_as_is(monkeypatch):
    _kitaru_task_tripwire(monkeypatch)

    assert resolve_task_and_model("summarize the cli", None) == ("summarize the cli", None)


def test_an_explicit_task_wins_over_worker_task_inputs(monkeypatch, _worker):
    """AC3: the CLI arg beats the env channel — an operator debugging a Worker can override it."""
    _kitaru_task_tripwire(monkeypatch)
    _worker_inputs(monkeypatch, {"task": "from the worker", "model": "gemini-2.5-pro"})

    assert resolve_task_and_model("from the cli", None) == ("from the cli", None)


def test_an_explicit_model_rides_along_with_an_explicit_task(monkeypatch):
    _kitaru_task_tripwire(monkeypatch)

    assert resolve_task_and_model("do it", "gemini-2.5-flash") == ("do it", "gemini-2.5-flash")


# --- the Worker Task channel ---------------------------------------------------------------------


def test_a_worker_task_supplies_the_task_from_its_inputs(monkeypatch, _worker):
    """AC2: ``KITARU_TASK_ID`` + ``KITARU_TASK_INPUTS`` run the task with no CLI arg at all."""
    _worker_inputs(monkeypatch, {"task": "say hi"})

    assert resolve_task_and_model(None, None) == ("say hi", None)


def test_a_worker_tasks_model_becomes_the_model_override(monkeypatch, _worker):
    """AC4: ``model`` in the inputs is the same Model Override ``--model`` sets (ADR-0019 §4)."""
    _worker_inputs(monkeypatch, {"task": "say hi", "model": "gemini-2.5-pro"})

    assert resolve_task_and_model(None, None) == ("say hi", "gemini-2.5-pro")


def test_a_null_model_in_the_inputs_means_the_configured_default(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": "say hi", "model": None})

    assert resolve_task_and_model(None, None) == ("say hi", None)


def test_a_blank_model_in_the_inputs_means_the_configured_default(monkeypatch, _worker):
    """An input schema with an empty-string default must not select a model id of ``''``."""
    _worker_inputs(monkeypatch, {"task": "say hi", "model": "  "})

    assert resolve_task_and_model(None, None) == ("say hi", None)


def test_an_explicit_model_flag_wins_over_the_inputs_model(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": "say hi", "model": "gemini-2.5-pro"})

    assert resolve_task_and_model(None, "gemini-2.5-flash") == ("say hi", "gemini-2.5-flash")


def test_the_worker_task_is_stripped_of_surrounding_whitespace(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": "  say hi\n"})

    assert resolve_task_and_model(None, None) == ("say hi", None)


# --- the shapes a REPLAY actually delivers (task 137) ---------------------------------------------
# A replay hands the Agent Version's command the BASELINE SESSION's own recorded inputs verbatim
# (kitaru 0.22.2 ``replay_pipeline.py``: ``AgentTask(..., inputs=baseline.inputs)``), so the payload
# is whatever recorded that session — never a hand-written ``{"task": ...}``.


def test_a_recorded_prompt_string_is_the_task(monkeypatch, _worker):
    """``kitaru-pydantic-ai`` records ``inputs = ctx.prompt``: a decode-recorded session replays as a
    bare JSON string, and that string IS the prompt (no guessing involved)."""
    _worker_inputs(monkeypatch, "say hi in exactly three words")

    assert resolve_task_and_model(None, None) == ("say hi in exactly three words", None)


def test_an_imported_sessions_input_key_is_the_task(monkeypatch, _worker):
    """An Opik-imported session records ``{"input": "<prompt>", ...}`` — the shape cohort
    ``decode-bad-request-400@1`` replays with."""
    _worker_inputs(
        monkeypatch,
        {"input": "turn three live articles into a knowledge graph", "logfire.fingerprint": "abc"},
    )

    assert resolve_task_and_model(None, None) == (
        "turn three live articles into a knowledge graph",
        None,
    )


def test_the_canonical_task_key_wins_over_a_recorded_input_key(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": "the canonical one", "input": "the recorded one"})

    assert resolve_task_and_model(None, None) == ("the canonical one", None)


def test_a_recorded_prompt_string_takes_the_configured_model(monkeypatch, _worker):
    """A bare-string payload carries no ``model``, so the run keeps the configured one."""
    _worker_inputs(monkeypatch, "say hi")

    assert resolve_task_and_model(None, None) == ("say hi", None)


def test_a_blank_recorded_prompt_string_is_a_hard_failure(monkeypatch, _worker):
    _worker_inputs(monkeypatch, "   ")

    with pytest.raises(WorkerTaskInputError, match="task"):
        resolve_task_and_model(None, None)


def test_a_non_string_input_key_is_a_hard_failure(monkeypatch, _worker):
    """Widened for the recorded shapes, not for guessing: a structured ``input`` still fails."""
    _worker_inputs(monkeypatch, {"input": {"messages": [{"role": "user", "content": "hi"}]}})

    with pytest.raises(WorkerTaskInputError, match="task"):
        resolve_task_and_model(None, None)


# --- no task anywhere: ONE friendly line ---------------------------------------------------------


def test_no_task_and_no_worker_context_raises_one_friendly_line(monkeypatch):
    """AC1: the missing-argument case names both ways to supply a task, in ONE line."""
    monkeypatch.delenv("KITARU_TASK_ID", raising=False)

    with pytest.raises(WorkerTaskInputError) as excinfo:
        resolve_task_and_model(None, None)

    message = str(excinfo.value)
    assert "TASK" in message
    assert "KITARU_TASK_INPUTS" in message  # ...and the Worker Task channel
    assert "\n" not in message


def test_a_blank_task_argument_reads_as_absent(monkeypatch):
    """``decode run ""`` supplied nothing runnable — treat it as the missing argument it is."""
    monkeypatch.delenv("KITARU_TASK_ID", raising=False)

    with pytest.raises(WorkerTaskInputError):
        resolve_task_and_model("   ", None)


def test_a_blank_task_id_is_not_a_worker_context(monkeypatch):
    """An exported-but-empty ``KITARU_TASK_ID`` is not a Worker Task (matches the Recording Seam)."""
    monkeypatch.setenv("KITARU_TASK_ID", "")
    _kitaru_task_tripwire(monkeypatch)

    with pytest.raises(WorkerTaskInputError, match="TASK"):
        resolve_task_and_model(None, None)


# --- malformed Worker inputs: hard fail, naming the failure ---------------------------------------


def test_malformed_inputs_json_names_the_parse_failure(monkeypatch, _worker):
    """AC5: a Worker replay must never guess — a JSON error is a hard failure that says so."""
    monkeypatch.setenv("KITARU_TASK_INPUTS", "{not json")

    with pytest.raises(WorkerTaskInputError) as excinfo:
        resolve_task_and_model(None, None)

    message = str(excinfo.value)
    assert "KITARU_TASK_INPUTS" in message
    assert "JSONDecodeError" in message  # the parse failure itself, not a generic "bad inputs"
    assert "\n" not in message  # one line, no traceback


def test_an_unreadable_inputs_channel_is_a_hard_failure(monkeypatch, _worker):
    """No ``KITARU_TASK_INPUTS`` → kitaru fetches the task spec; without an API url it raises.

    decode must turn that into its own one-line failure rather than a raw kitaru traceback — the
    accessor's fallback is real (a synchronous spec fetch), and this is the offline half of it.
    """
    monkeypatch.delenv("KITARU_TASK_INPUTS", raising=False)
    monkeypatch.delenv("KITARU_API_URL", raising=False)

    with pytest.raises(WorkerTaskInputError) as excinfo:
        resolve_task_and_model(None, None)

    assert "KITARU_API_URL" in str(excinfo.value)  # kitaru's own cause, kept


@pytest.mark.parametrize(
    "payload",
    [["say hi"], 42, None, True],
    ids=["list", "number", "null", "bool"],
)
def test_inputs_that_are_neither_an_object_nor_a_prompt_string_are_a_hard_failure(
    monkeypatch, _worker, payload
):
    _worker_inputs(monkeypatch, payload)

    with pytest.raises(WorkerTaskInputError, match="task"):
        resolve_task_and_model(None, None)


def test_inputs_without_a_task_key_are_a_hard_failure(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"model": "gemini-2.5-pro"})

    with pytest.raises(WorkerTaskInputError, match="no runnable task"):
        resolve_task_and_model(None, None)


def test_a_blank_task_in_the_inputs_is_a_hard_failure(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": "   "})

    with pytest.raises(WorkerTaskInputError, match="no runnable task"):
        resolve_task_and_model(None, None)


def test_a_non_string_task_in_the_inputs_is_a_hard_failure(monkeypatch, _worker):
    _worker_inputs(monkeypatch, {"task": {"prompt": "say hi"}})

    with pytest.raises(WorkerTaskInputError, match="no runnable task"):
        resolve_task_and_model(None, None)


def test_a_non_string_model_in_the_inputs_is_a_hard_failure(monkeypatch, _worker):
    """The Model Override is a model id; anything else would silently run the wrong model."""
    _worker_inputs(monkeypatch, {"task": "say hi", "model": 7})

    with pytest.raises(WorkerTaskInputError, match="'model'"):
        resolve_task_and_model(None, None)


# --- the import invariant -------------------------------------------------------------------------


def test_resolving_without_a_worker_context_imports_no_kitaru_module(tmp_path):
    """AC6: outside a Worker Task the resolution imports no kitaru at all (ADR-0019 §3,4).

    A clean subprocess from a ``tmp_path`` cwd (no repo ``.env``) with the kitaru env scrubbed keeps
    this honest regardless of what the rest of the suite already imported.
    """
    code = (
        "import sys\n"
        "from decode.runtime.task_inputs import WorkerTaskInputError, resolve_task_and_model\n"
        "assert resolve_task_and_model('do it', None) == ('do it', None)\n"
        "try:\n"
        "    resolve_task_and_model(None, None)\n"
        "except WorkerTaskInputError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('a missing task must raise')\n"
        "leaked = sorted(m for m in sys.modules if m == 'kitaru' or m.startswith('kitaru'))\n"
        "assert not leaked, leaked\n"
        "print('NO_KITARU_OK')\n"
    )
    scrubbed = {"DECODE_ENV", "KITARU_AGENT_ID", "KITARU_API_URL", "KITARU_TASK_ID"}
    child_env = {k: v for k, v in os.environ.items() if k not in scrubbed}

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=child_env,
    )

    assert result.returncode == 0, result.stderr
    assert "NO_KITARU_OK" in result.stdout
