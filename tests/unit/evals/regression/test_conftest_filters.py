"""The regression gate's ONE warning filter: the leaked provider socket, and nothing else.

``pyproject``'s ``filterwarnings = ["error"]`` turns every warning into a failure — the right default
for ``make ci``, and the reason the pre-merge gate (``evals/regression/test_thresholds.py``) exited
non-zero even on a run whose thresholds it had already judged. The warning is not decode's: each
Regression Case runs through ``run_agent_once_sync`` → its own ``asyncio.run``, and the TLS socket
``google-genai`` opened to the Gemini endpoint (via its cached ``aiohttp`` session) outlives that loop,
so CPython's ``__del__`` fires ``ResourceWarning: unclosed …`` at the next GC. See
``evals/regression/conftest.py`` for the full repro.

These tests pin the fix as a CONTRACT, and they live under ``tests/unit`` on purpose: ``testpaths`` is
``tests/unit`` + ``tests/integration``, so a test placed beside the gate itself would never run again
(that is exactly why the gate directory is invisible to ``make ci``). They import the filter string from
the gate conftest, so there is ONE source of truth, and they assert BOTH halves of "narrow":

* the two ``ResourceWarning`` messages the leak really emits are swallowed, and
* every other warning — a different ``ResourceWarning``, the same text under another category — still
  errors, so the gate keeps failing on warnings that mean something.
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import pytest
from _pytest.config import parse_warning_filter

from evals.regression.conftest import LEAKED_PROVIDER_SOCKET_FILTER, pytest_configure

# The two messages captured from a real (paid) two-case driver run on this branch — CPython's own
# ``socket.__del__`` / ``_SelectorTransport.__del__`` text, fds and addresses included, so a filter
# that only matched a tidied-up paraphrase would be caught here.
OBSERVED_LEAK_MESSAGES = [
    "unclosed <socket.socket fd=11, family=2, type=1, proto=6, "
    "laddr=('192.168.50.20', 63792), raddr=('172.217.114.4', 443)>",
    "unclosed transport <_SelectorSocketTransport fd=11>",
]

# Warnings the gate must KEEP failing on: a ResourceWarning that is not this leak, the leak's own text
# under a different category, and the ordinary suspects.
STILL_ERRORS = [
    (ResourceWarning, "subprocess 4711 is still running"),
    (ResourceWarning, "unclosed file <_io.TextIOWrapper name='reward.txt'>"),
    (ResourceWarning, "unclosed database in <sqlite3.Connection object>"),
    (UserWarning, "unclosed transport <_SelectorSocketTransport fd=11>"),
    (DeprecationWarning, "unclosed <socket.socket fd=11>"),
    (RuntimeWarning, "coroutine 'run_agent_once' was never awaited"),
]


def _apply_gate_filters() -> None:
    """Install ``error`` + the gate's one filter, exactly as pytest layers the ini entries.

    pytest parses each ``filterwarnings`` ini entry with :func:`parse_warning_filter` and applies them
    in order, so an entry appended AFTER ``error`` wins for the messages it matches — which is what
    ``addinivalue_line`` does. Reproducing that order here is what makes these assertions mean
    something about the real run.
    """
    warnings.simplefilter("error")
    warnings.filterwarnings(*parse_warning_filter(LEAKED_PROVIDER_SOCKET_FILTER, escape=False))


def test_pytest_parses_the_filter_as_one_message_scoped_resourcewarning_ignore() -> None:
    """The string is a well-formed, message-scoped, category-scoped ``ignore`` — not a blanket one."""
    action, message, category, module, lineno = parse_warning_filter(
        LEAKED_PROVIDER_SOCKET_FILTER, escape=False
    )

    assert action == "ignore"
    assert category is ResourceWarning
    assert message.startswith("unclosed ")
    # A blanket ``ignore::ResourceWarning`` (empty message) would silence every leak in the gate.
    assert message != ""
    # Module/lineno stay unscoped on purpose: a ``__del__``-time ResourceWarning is attributed to
    # ``asyncio.selector_events`` / ``socket``, never to the library that opened the connection.
    assert (module, lineno) == ("", 0)


@pytest.mark.parametrize("message", OBSERVED_LEAK_MESSAGES)
def test_the_observed_leak_messages_are_swallowed(message: str) -> None:
    """Both real teardown messages pass silently — the gate can now report its own verdict."""
    with warnings.catch_warnings():
        _apply_gate_filters()

        warnings.warn(message, ResourceWarning, stacklevel=1)  # must not raise


@pytest.mark.parametrize(("category", "message"), STILL_ERRORS)
def test_every_other_warning_still_errors(category: type[Warning], message: str) -> None:
    """The filter is narrow: another ResourceWarning, or this text under another class, still fails."""
    with warnings.catch_warnings():
        _apply_gate_filters()

        with pytest.raises(category):
            warnings.warn(message, category, stacklevel=1)


def test_the_gate_conftest_registers_exactly_one_filter() -> None:
    """``pytest_configure`` appends THIS entry and no other — one exemption, not a policy change."""
    recorded: list[tuple[str, str]] = []
    config = SimpleNamespace(
        addinivalue_line=lambda name, line: recorded.append((name, line)),
    )

    pytest_configure(config)  # type: ignore[arg-type]

    assert recorded == [("filterwarnings", LEAKED_PROVIDER_SOCKET_FILTER)]
