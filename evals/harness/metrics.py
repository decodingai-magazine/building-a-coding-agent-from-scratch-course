"""Custom Opik code metrics for the eval harness (ADR-0017 §4,7; task 104).

Every metric here subclasses :class:`opik.evaluation.metrics.base_metric.BaseMetric` and returns a
:class:`~opik.evaluation.metrics.score_result.ScoreResult` — a ``value`` in ``[0, 1]`` plus a
human-readable ``reason``. They grade the mechanical, code-decidable facts of a run (which tool was
used, whether the hidden oracle passed, how many steps, how big the diff); anything a machine cannot
score — quality, groundedness, minimal-diff judgement — is a G-Eval judge instead
(``evals/harness/judges.py``).

The inputs come from the task-fn output dict Opik hands each metric (built from an
:class:`~evals.harness.driver.EvalRunRecord` plus the recorded verify result and diff, wired in task
106) merged with the dataset item. Opik matches a metric's ``score`` parameter names against that
dict; every parameter carries a default and every metric absorbs the rest through
``**ignored_kwargs`` — so a missing or malformed field yields a graceful ``0.0`` with a reason,
never a raise. The Opik built-ins (``Equals``, ``Contains``, ``IsJson``, ``RegexMatch``) are used
directly where they fit and are deliberately NOT wrapped here.

Every metric is constructed with ``track=False``: the ``BaseMetric`` default (``track=True``) wraps
each ``score()`` in ``opik.track(...)``, which opens a real outbound HTTPS round-trip to comet.com —
so the offline unit suite (and ``make ci``, ADR-0017 §9) would phone home. The eval-time span
nesting comes from the enclosing ``evaluate()`` call, not from tracking each metric individually.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable, Iterable
from typing import Any

from opik.evaluation.metrics.base_metric import BaseMetric
from opik.evaluation.metrics.score_result import ScoreResult


def _tool_call_names(tool_calls: Any) -> list[str] | None:
    """Best-effort tool-name extraction from a task-fn ``tool_calls`` list, else ``None``.

    Accepts the shapes the output can take: dicts (``{"name": ...}``),
    :class:`~evals.harness.driver.ToolCallRecord`-like objects (a ``.name`` attribute), or plain
    strings. Anything else (a non-iterable, a string blob) returns ``None`` so the caller can score
    a graceful ``0.0`` instead of raising.
    """
    if tool_calls is None or isinstance(tool_calls, str) or not isinstance(tool_calls, Iterable):
        return None
    names: list[str] = []
    for call in tool_calls:
        if isinstance(call, str):
            names.append(call)
        elif isinstance(call, dict) and "name" in call:
            names.append(str(call["name"]))
        elif hasattr(call, "name"):
            names.append(str(call.name))
    return names


def _tool_call_args(tool_calls: Any, tool_name: str) -> list[dict[str, Any]] | None:
    """The decoded ``args`` dicts of every call to ``tool_name`` in ``tool_calls``.

    Returns ``None`` when ``tool_calls`` is unusable (missing / a string blob / non-iterable) so the
    caller can grade a graceful ``0.0`` rather than raise, and an empty list when the field is usable
    but ``tool_name`` was never called. Each matched call's ``args`` is coerced to a dict: a
    ``ToolCallRecord``-style payload already carries a mapping, but a raw JSON string (the shape the
    driver records when a ``ToolCallPart`` is not a mapping) is best-effort ``json.loads``-ed; anything
    that does not decode to a dict becomes ``{}`` so a predicate always sees a mapping.
    """
    if tool_calls is None or isinstance(tool_calls, str) or not isinstance(tool_calls, Iterable):
        return None
    matched: list[dict[str, Any]] = []
    for call in tool_calls:
        if isinstance(call, dict):
            name, args = call.get("name"), call.get("args")
        elif hasattr(call, "name"):
            name, args = call.name, getattr(call, "args", None)
        else:
            continue
        if name == tool_name:
            matched.append(_coerce_args_dict(args))
    return matched


def _coerce_args_dict(args: Any) -> dict[str, Any]:
    """Coerce a tool call's recorded ``args`` to a dict — JSON-decoding a string, else ``{}``."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            decoded = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


class ToolArgsMetric(BaseMetric):
    """Score ``1.0`` when SOME recorded call to ``tool_name`` has args satisfying ``predicate``.

    :class:`ToolCalledMetric` only proves a tool WAS called; some probes need to grade the CALL's
    arguments — a genuinely multi-step plan is ``todo_write`` with ``>= 3`` items, a skill-dispatch
    probe wants the ``skill`` tool called with the RIGHT ``name``. ``predicate`` is a plain
    ``dict -> bool`` callable evaluated against each matching call's decoded args; the metric passes
    when any one call satisfies it. ``description`` is the human phrase the ``reason`` cites (e.g.
    "at least 3 todo items"). A predicate that raises on a malformed args dict is treated as an
    unmet condition, never a crash. A missing / malformed ``tool_calls`` field, or ``tool_name`` never
    called, scores a graceful ``0.0`` — same offline ``track=False`` posture as the other metrics.
    """

    def __init__(
        self,
        tool_name: str,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        description: str,
        name: str,
    ) -> None:
        super().__init__(name=name, track=False)
        self.tool_name = tool_name
        self.predicate = predicate
        self.description = description

    def score(self, tool_calls: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        calls = _tool_call_args(tool_calls, self.tool_name)
        if calls is None:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No usable tool_calls recorded; cannot check {self.tool_name!r} args ({self.description}).",
            )
        if not calls:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"{self.tool_name!r} was not called; cannot check its args ({self.description}).",
            )
        matched = any(self._satisfies(args) for args in calls)
        return ScoreResult(
            name=self.name,
            value=1.0 if matched else 0.0,
            reason=f"{self.tool_name!r} args {'satisfy' if matched else 'do NOT satisfy'}: {self.description}.",
        )

    def _satisfies(self, args: dict[str, Any]) -> bool:
        """Whether ``args`` meets the predicate — a raising predicate counts as unmet, never a crash."""
        try:
            return bool(self.predicate(args))
        except Exception:  # a malformed args dict must not abort scoring — grade it as unmet
            return False


class ToolCalledMetric(BaseMetric):
    """Score ``1.0`` when ``tool_name`` appears in the run's ``tool_calls``, else ``0.0``."""

    def __init__(self, tool_name: str, name: str | None = None) -> None:
        super().__init__(name=name or f"tool_called_{tool_name}", track=False)
        self.tool_name = tool_name

    def score(self, tool_calls: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        names = _tool_call_names(tool_calls)
        if names is None:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No usable tool_calls recorded; cannot confirm {self.tool_name!r} was called.",
            )
        called = self.tool_name in names
        return ScoreResult(
            name=self.name,
            value=1.0 if called else 0.0,
            reason=f"{self.tool_name!r} {'was' if called else 'was NOT'} called; tools used: {names}.",
        )


class ToolNotCalledMetric(BaseMetric):
    """Score ``1.0`` when ``tool_name`` is ABSENT from the run's ``tool_calls``, else ``0.0``.

    A missing / malformed ``tool_calls`` field means no tool was recorded, so the forbidden tool was
    — trivially — not called: that scores ``1.0``.
    """

    def __init__(self, tool_name: str, name: str | None = None) -> None:
        super().__init__(name=name or f"tool_not_called_{tool_name}", track=False)
        self.tool_name = tool_name

    def score(self, tool_calls: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        names = _tool_call_names(tool_calls) or []
        called = self.tool_name in names
        return ScoreResult(
            name=self.name,
            value=0.0 if called else 1.0,
            reason=f"{self.tool_name!r} {'was' if called else 'was NOT'} called; tools used: {names}.",
        )


class VerifyOracleMetric(BaseMetric):
    """Map the runner's recorded verify result to ``1.0`` (exit 0 = PASS) or ``0.0``.

    The metric never RUNS anything — the task fn already ran ``verify.sh`` in the sandbox after the
    agent finished (ADR-0017 §5) and recorded ``{"exit_code": ..., "stdout": ...}``. Here we only
    map that recorded result. A missing / malformed ``verify`` mapping (no integer ``exit_code``)
    scores a graceful ``0.0``.
    """

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name=name or "verify_oracle", track=False)

    def score(self, verify: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        exit_code = verify.get("exit_code") if isinstance(verify, dict) else None
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason="No verify result recorded (missing or non-integer exit_code).",
            )
        stdout = str(verify.get("stdout", "")) if isinstance(verify, dict) else ""
        passed = exit_code == 0
        snippet = stdout.strip()[:200]
        return ScoreResult(
            name=self.name,
            value=1.0 if passed else 0.0,
            reason=f"verify.sh exit_code={exit_code} ({'PASS' if passed else 'FAIL'}). stdout: {snippet!r}",
        )


class MaxStepsMetric(BaseMetric):
    """Score ``1.0`` when the run's ``steps`` is within the item's ``max_steps`` budget, else ``0.0``.

    ``steps`` is the model-request count from the record; ``max_steps`` comes from the dataset item.
    The reason always carries the observed step count. A missing ``steps`` or ``max_steps`` scores a
    graceful ``0.0``.
    """

    def __init__(self, name: str | None = None) -> None:
        super().__init__(name=name or "max_steps", track=False)

    def score(self, steps: Any = None, max_steps: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not _is_real_int(steps) or not _is_real_int(max_steps):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Cannot score step budget: steps={steps!r}, max_steps={max_steps!r}.",
            )
        within = steps <= max_steps
        return ScoreResult(
            name=self.name,
            value=1.0 if within else 0.0,
            reason=f"steps={steps} {'<=' if within else '>'} max_steps={max_steps}.",
        )


class DiffLinesMetric(BaseMetric):
    """Score ``1.0`` when the recorded diff's changed-line count is ``<= max_lines``, else ``0.0``.

    "Changed lines" are unified-diff body lines starting with ``+`` or ``-``, excluding the
    ``+++`` / ``---`` file headers. An empty diff (``""``) is a valid record of "no changes" and
    scores ``1.0``; a missing diff (``None`` / absent) scores a graceful ``0.0``.
    """

    def __init__(self, max_lines: int, name: str | None = None) -> None:
        super().__init__(name=name or f"diff_lines_le_{max_lines}", track=False)
        self.max_lines = max_lines

    def score(self, diff: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(diff, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No diff recorded (got {type(diff).__name__}); cannot count changed lines.",
            )
        changed = _changed_line_count(diff)
        within = changed <= self.max_lines
        return ScoreResult(
            name=self.name,
            value=1.0 if within else 0.0,
            reason=f"{changed} changed line(s) {'<=' if within else '>'} max_lines={self.max_lines}.",
        )


class FileDiffLinesMetric(BaseMetric):
    """Score ``1.0`` when the seeded ``path`` changed by ``<= max_lines`` lines during the run.

    The regression task-fn records the run's final Workspace as ``file_state`` (a
    ``{path: content}`` snapshot), NOT a unified ``diff`` — so :class:`DiffLinesMetric` (which reads a
    ``diff`` string a benchmark run computes) has nothing to grade on a regression payload. This metric
    closes that gap: it holds the probe's known ``baseline`` for ``path`` and, at score time, diffs it
    against ``file_state[path]`` and counts changed lines with the SAME counter
    :class:`DiffLinesMetric` uses — so an edit-precision / minimal-diff probe grades on how much of the
    seeded file the agent actually rewrote. A single-line replacement is one ``-`` plus one ``+`` = two
    changed lines. ``path`` absent from the snapshot (the agent never wrote it, or deleted it) or a
    missing / malformed ``file_state`` scores a graceful ``0.0``.
    """

    def __init__(self, path: str, baseline: str, max_lines: int, name: str | None = None) -> None:
        super().__init__(name=name or f"file_diff_lines_le_{max_lines}", track=False)
        self.path = path
        self.baseline = baseline
        self.max_lines = max_lines

    def score(self, file_state: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(file_state, dict):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No file_state recorded (got {type(file_state).__name__}); cannot diff {self.path!r}.",
            )
        final = file_state.get(self.path)
        if not isinstance(final, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"{self.path!r} absent from the final file_state; nothing to diff.",
            )
        diff = "\n".join(
            difflib.unified_diff(self.baseline.splitlines(), final.splitlines(), lineterm="")
        )
        changed = _changed_line_count(diff)
        within = changed <= self.max_lines
        return ScoreResult(
            name=self.name,
            value=1.0 if within else 0.0,
            reason=f"{changed} changed line(s) {'<=' if within else '>'} max_lines={self.max_lines} in {self.path!r}.",
        )


class FileEqualsMetric(BaseMetric):
    """Score ``1.0`` when ``file_state[path]`` equals ``expected`` byte-for-byte (as text), else ``0.0``.

    The exact-match counterpart to :class:`FileDiffLinesMetric` (which grades a line-count budget): a
    step-efficiency probe asks for a file containing EXACTLY a value, so a trailing newline or extra
    prose is a fail, not a within-threshold pass. Reads the run's ``file_state`` snapshot the
    regression task-fn records ({path: content}); ``path`` absent (never written / deleted) or a
    missing / malformed ``file_state`` scores a graceful ``0.0``.
    """

    def __init__(self, path: str, expected: str, name: str | None = None) -> None:
        super().__init__(name=name or f"file_equals_{path}", track=False)
        self.path = path
        self.expected = expected

    def score(self, file_state: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(file_state, dict):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No file_state recorded (got {type(file_state).__name__}); cannot check {self.path!r}.",
            )
        actual = file_state.get(self.path)
        if not isinstance(actual, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"{self.path!r} absent from the final file_state; nothing to compare.",
            )
        equal = actual == self.expected
        return ScoreResult(
            name=self.name,
            value=1.0 if equal else 0.0,
            reason=f"{self.path!r} {'equals' if equal else 'does NOT equal'} the expected content "
            f"({self.expected!r} vs {actual!r}).",
        )


class NewFileNameMetric(BaseMetric):
    """Score ``1.0`` when the run created ≥1 file matching ``suffix`` AND every one obeys ``predicate``.

    The memory-obedience probe (a seeded ``AGENTS.md`` rule such as "every new Python file's name starts
    with ``dc_``") grades on the NAME of the file the agent chose, not its content — so neither
    :class:`FileEqualsMetric` (a known path) nor a tool-arg check fits. This metric reads the run's
    ``file_state`` snapshot, keeps the paths ending in ``suffix``, and passes only when at least one such
    file exists (the agent did create the file) AND every matched file's basename satisfies ``predicate``
    (each obeys the naming rule). ``description`` is the human phrase the ``reason`` cites. A predicate
    that raises on a name is treated as a violation, never a crash; a missing / malformed ``file_state``
    or no matching file scores a graceful ``0.0`` — the same offline ``track=False`` posture as the rest.
    """

    def __init__(
        self,
        suffix: str,
        predicate: Callable[[str], bool],
        *,
        description: str,
        name: str,
    ) -> None:
        super().__init__(name=name, track=False)
        self.suffix = suffix
        self.predicate = predicate
        self.description = description

    def score(self, file_state: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(file_state, dict):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No file_state recorded (got {type(file_state).__name__}); cannot check {self.description}.",
            )
        matched = [
            path for path in file_state if isinstance(path, str) and path.endswith(self.suffix)
        ]
        if not matched:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No file ending in {self.suffix!r} was created; cannot check {self.description}.",
            )
        offenders = [path for path in matched if not self._obeys(path)]
        ok = not offenders
        return ScoreResult(
            name=self.name,
            value=1.0 if ok else 0.0,
            reason=(
                f"All {len(matched)} {self.suffix!r} file(s) obey: {self.description}."
                if ok
                else f"{len(offenders)} {self.suffix!r} file(s) violate {self.description}: {offenders}."
            ),
        )

    def _obeys(self, path: str) -> bool:
        """Whether ``path``'s basename meets the predicate — a raising predicate counts as a violation."""
        basename = path.rsplit("/", 1)[-1]
        try:
            return bool(self.predicate(basename))
        except Exception:  # a malformed name must not abort scoring — grade it as a violation
            return False


class JsonSchemaMetric(BaseMetric):
    """Score ``1.0`` when the run's ``output`` is JSON that validates against a pydantic ``schema``.

    The JSON-output-contract probe asks the agent to answer ONLY as JSON matching a schema; grading it
    needs BOTH that the text parses as JSON (Opik's ``IsJson`` built-in covers that) AND that the parsed
    object satisfies the declared shape. This metric closes the second half: it ``json.loads`` the
    ``output`` and calls ``schema.model_validate`` on the result. Any failure — non-string output, a
    parse error, a validation error, or JSON that is not an object the model accepts — is a graceful
    ``0.0`` with a reason, never a raise (the contract is strict on purpose: a ```` ```json ```` fence or
    trailing prose breaks the parse, which is the point of an output-contract probe). ``track=False`` for
    the same offline reason as the rest.
    """

    def __init__(self, schema: type[Any], *, name: str | None = None) -> None:
        super().__init__(name=name or f"json_schema_{schema.__name__}", track=False)
        self.schema = schema

    def score(self, output: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(output, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No output text recorded (got {type(output).__name__}); cannot validate JSON.",
            )
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError) as exc:
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Output is not valid JSON ({exc}); the answer broke the JSON-only contract.",
            )
        try:
            self.schema.model_validate(payload)
        except Exception as exc:  # a pydantic ValidationError (or any validator raise) is a fail
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"Output JSON does not match {self.schema.__name__}: {exc}",
            )
        return ScoreResult(
            name=self.name,
            value=1.0,
            reason=f"Output is JSON matching {self.schema.__name__}.",
        )


class OutputContainsMetric(BaseMetric):
    """Score ``1.0`` when ``needle`` appears in the run's final assistant ``output`` text.

    Case-insensitive by default (an agent may phrase the answer in any casing). Used where a probe's
    behavior is confirmed by the agent NAMING something in its answer — e.g. the LSP-diagnostics probe
    asserts the seeded error is surfaced in the reply. A missing / non-string ``output`` scores a
    graceful ``0.0``.
    """

    def __init__(self, needle: str, case_sensitive: bool = False, name: str | None = None) -> None:
        super().__init__(name=name or f"output_contains_{needle}", track=False)
        self.needle = needle
        self.case_sensitive = case_sensitive

    def score(self, output: Any = None, **ignored_kwargs: Any) -> ScoreResult:
        if not isinstance(output, str):
            return ScoreResult(
                name=self.name,
                value=0.0,
                reason=f"No output text recorded (got {type(output).__name__}).",
            )
        haystack = output if self.case_sensitive else output.lower()
        needle = self.needle if self.case_sensitive else self.needle.lower()
        present = needle in haystack
        return ScoreResult(
            name=self.name,
            value=1.0 if present else 0.0,
            reason=f"{self.needle!r} {'found' if present else 'NOT found'} in output.",
        )


class ToolNotSucceededMetric(BaseMetric):
    """Score ``1.0`` when ``tool_name`` never SUCCEEDED — not called, or every call the gate denied.

    Stricter-than-absent counterpart to :class:`ToolNotCalledMetric`: a plan-mode probe wants "zero
    SUCCESSFUL write/edit calls", which a denied attempt still satisfies (the agent tried to edit but
    ``enter_plan_mode`` had flipped the gate to ``PLAN``, so the write was denied and never landed).
    Reads ``tool_calls`` (every attempt) and ``denied_tools`` (the gate-denied ones); the tool
    succeeded ``count(tool_calls) - count(denied_tools)`` times, and the metric passes when that is
    ``<= 0``. A missing / malformed ``tool_calls`` means nothing ran, so — trivially — nothing
    succeeded: that scores ``1.0``.
    """

    def __init__(self, tool_name: str, name: str | None = None) -> None:
        super().__init__(name=name or f"tool_not_succeeded_{tool_name}", track=False)
        self.tool_name = tool_name

    def score(
        self, tool_calls: Any = None, denied_tools: Any = None, **ignored_kwargs: Any
    ) -> ScoreResult:
        names = _tool_call_names(tool_calls) or []
        denied = denied_tools if isinstance(denied_tools, list) else []
        called = names.count(self.tool_name)
        denied_count = denied.count(self.tool_name)
        succeeded = called - denied_count
        ok = succeeded <= 0
        return ScoreResult(
            name=self.name,
            value=1.0 if ok else 0.0,
            reason=f"{self.tool_name!r} succeeded {max(succeeded, 0)} time(s) (called {called}, denied {denied_count}).",
        )


def _is_real_int(value: Any) -> bool:
    """True only for a genuine ``int`` — ``bool`` is an ``int`` subclass we must reject here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _changed_line_count(diff: str) -> int:
    """Count added / removed body lines in a unified diff, excluding the ``+++`` / ``---`` headers."""
    count = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            count += 1
    return count
