"""Regression guard for the runtime Kitaru-store isolation (task 065).

The runtime tests must never read or write a developer's real ZenML store/server — every secret /
execution op has to hit the per-test ``tmp_path`` SQLite store the autouse ``isolated_kitaru_store``
fixture pins. These two tests make that invariant explicit (it is otherwise only implied by the
fixture's internal ``_assert_store_isolated_under`` guard): if the isolation ever regresses — the
autouse fixture stops applying, or the per-test re-pin stops taking effect — they fail loudly here
instead of silently polluting real infra (which on a developer box is a live ZenML server, not a
file). The faithful cross-file adverse-order reproduction lives in
``tests/integration/test_runtime_store_isolation.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kitaru import create_secret, get_secret

# Booting the real Kitaru/ZenML stack emits two unrelated third-party deprecation warnings (see
# test_flow.py); scope the ignores here so the strict ``filterwarnings=["error"]`` gate stays green.
pytestmark = [
    pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning"),
]


def test_active_store_is_the_per_test_tmp_sqlite_store(isolated_kitaru_store: Path) -> None:
    """The active ZenML store is a SQLite file under this test's ``tmp_path`` — never the real one."""
    from zenml.config.global_config import GlobalConfiguration

    url = str(GlobalConfiguration().store_configuration.url)
    assert url.startswith("sqlite:///"), f"expected a local SQLite store, got {url!r}"
    assert str(isolated_kitaru_store) in url, (
        f"store {url!r} is not under the per-test tmp_path {str(isolated_kitaru_store)!r}"
    )


def test_secret_round_trips_only_within_the_isolated_store(
    isolated_kitaru_store: Path, runtime_secret_name: str
) -> None:
    """A created secret is readable back from the isolated tmp store (a real round-trip, offline)."""
    create_secret(runtime_secret_name, {"GEMINI_API_KEY": "isolated-only"}, private=True)

    assert get_secret(runtime_secret_name).values["GEMINI_API_KEY"] == "isolated-only"
    # The op ran against the tmp store, so the unique-named secret never reaches a real store.
    from zenml.config.global_config import GlobalConfiguration

    assert str(isolated_kitaru_store) in str(GlobalConfiguration().store_configuration.url)
