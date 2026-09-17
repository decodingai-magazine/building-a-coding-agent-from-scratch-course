"""The regression ritual's own pytest options — ``--difficulty`` slices the gate (ADR-0022 §8).

This conftest exists so ``uv run pytest evals/regression/test_thresholds.py --difficulty hard`` runs
the eight hard Regression Cases instead of all twenty runnable ones — the same tier vocabulary
``python -m evals regression --difficulty`` uses, so ``make eval-regression-dataset ARGS='--difficulty hard'``
forwards ONE flag to both halves of the ritual (the sync and the gate).

It is scoped to ``evals/regression/`` on purpose: ``testpaths`` is ``tests/unit`` + ``tests/integration``,
so plain ``pytest`` / ``make ci`` never collects this directory and never sees the option.
"""

from __future__ import annotations

import pytest

# ONE exemption from ``pyproject``'s ``filterwarnings = ["error"]``, scoped to this directory.
#
# The leak is upstream and not decode's: every Regression Case runs through
# ``evals/harness/driver.py::run_agent_once_sync``, i.e. its own ``asyncio.run``. The Gemini call goes
# ``pydantic-ai → google-genai → aiohttp``, and google-genai caches that session on its API client, so
# the TLS socket it opened to the model endpoint (``raddr=('172.217.114.4', 443)``) outlives the loop
# that created it. When the next GC collects it, CPython's own ``socket.__del__`` /
# ``asyncio.selector_events._SelectorTransport.__del__`` emit exactly two messages:
#
#     ResourceWarning: unclosed <socket.socket fd=11, family=2, type=1, proto=6, laddr=…, raddr=…>
#     ResourceWarning: unclosed transport <_SelectorSocketTransport fd=11>
#
# Under ``error`` that fails the gate's pytest process AFTER the thresholds were already judged — a run
# that cost real money reports a red that says nothing about the agent. Decode cannot close the socket:
# the session is private to google-genai's client and bound to a loop that is already gone.
#
# The message regex names the two shapes rather than a bare ``unclosed``, so an unclosed FILE or
# sqlite connection in a case fixture — a bug we would want to see — still errors, as does every other
# warning class. ``module``/``lineno`` are deliberately left unscoped: a ``__del__``-time
# ResourceWarning is attributed to ``socket``/``asyncio``, never to the library that opened the socket.
# Pinned by ``tests/unit/evals/regression/test_conftest_filters.py`` (this directory is outside
# ``testpaths``, so its own tests would never run in CI).
LEAKED_PROVIDER_SOCKET_FILTER = (
    r"ignore:unclosed (transport|<socket\.socket|<ssl\.SSLSocket):ResourceWarning"
)

# The SECOND exemption, same shape, also upstream: a ``modal`` judge (Qwen behind SGLang, ADR-0022 §7)
# answers with fields litellm's own pydantic types do not model (``provider_specific_fields=
# {'matched_stop': …}``, a ``Choices`` where ``StreamingChoices`` is declared). The judge call itself
# is fine — G-Eval reads its score off the returned object — but AFTERWARDS litellm's logger and Opik's
# ``@track`` decorator both ``model_dump()`` that response to record it, and pydantic emits
#
#     UserWarning: Pydantic serializer warnings:
#       PydanticSerializationUnexpectedValue(Expected 10 fields but got 5: Expected `Message` …)
#
# Under ``error`` that warning becomes an exception inside those two bookkeeping hooks, which both
# catch it and print a full traceback per judge call (``Error creating standard logging object`` /
# ``Unexpected exception happened when tried to finalize span``) — pages of noise, one per judged
# item, and a judge span in Opik without its output. Scores are never touched. Message-scoped to
# pydantic's exact prefix and to ``UserWarning``, so any other UserWarning still errors.
JUDGE_RESPONSE_SERIALIZER_FILTER = r"ignore:Pydantic serializer warnings:UserWarning"

GATE_WARNING_FILTERS = (LEAKED_PROVIDER_SOCKET_FILTER, JUDGE_RESPONSE_SERIALIZER_FILTER)


def pytest_configure(config: pytest.Config) -> None:
    """Append the gate's two exemptions to the ini ``filterwarnings`` chain.

    ``addinivalue_line`` appends, and pytest applies the ini entries in order, so these take
    precedence over ``pyproject``'s ``error`` for the messages they match and for nothing else. They
    are registered here — the gate's own conftest — rather than in ``pyproject.toml`` so ``make ci``
    and every ``tests/unit`` run keep erroring on ALL warnings, these two included.
    """
    for entry in GATE_WARNING_FILTERS:
        config.addinivalue_line("filterwarnings", entry)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--difficulty`` (``None`` = the whole suite) for the threshold-gate ritual."""
    parser.addoption(
        "--difficulty",
        action="store",
        default=None,
        choices=("easy", "medium", "hard"),
        help="Run only the Regression Cases of this difficulty tier (default: all of them).",
    )
