"""The regression ritual's own pytest options — ``--difficulty`` slices the gate (ADR-0022 §8).

This conftest exists so ``uv run pytest evals/regression/test_thresholds.py --difficulty hard`` runs
the eight hard Regression Cases instead of all twenty runnable ones — the same tier vocabulary
``python -m evals regression --difficulty`` uses, so ``make eval-regression ARGS='--difficulty hard'``
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


def pytest_configure(config: pytest.Config) -> None:
    """Append the leaked-provider-socket exemption to the ini ``filterwarnings`` chain.

    ``addinivalue_line`` appends, and pytest applies the ini entries in order, so this one takes
    precedence over ``pyproject``'s ``error`` for the messages it matches and for nothing else. It is
    registered here — the gate's own conftest — rather than in ``pyproject.toml`` so ``make ci`` and
    every ``tests/unit`` run keep erroring on ALL warnings, this leak included.
    """
    config.addinivalue_line("filterwarnings", LEAKED_PROVIDER_SOCKET_FILTER)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--difficulty`` (``None`` = the whole suite) for the threshold-gate ritual."""
    parser.addoption(
        "--difficulty",
        action="store",
        default=None,
        choices=("easy", "medium", "hard"),
        help="Run only the Regression Cases of this difficulty tier (default: all of them).",
    )
