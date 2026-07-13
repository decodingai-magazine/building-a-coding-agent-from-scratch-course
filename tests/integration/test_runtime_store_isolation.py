"""Forced adverse-order guard: the runtime tests never write a developer's real ZenML store (task 065).

This reproduces the exact order the Tester (task 064 round 2) used to surface the hazard — a
secret-creating runtime file, then a NON-isolated ZenML-touching file (``test_cli.py``), then another
runtime file — in a **subprocess** whose ``HOME`` / ``ZENML_CONFIG_PATH`` point at a throwaway
"real" store under ``tmp_path``. Before the fix, the second runtime file's autouse Kitaru-store
isolation was de-applied (a non-contiguous-collection quirk of ``--import-mode=importlib`` when the
runtime fixtures lived in the per-package conftest), so a ``create_secret`` fell through to that store
and left a ``decode-llm-creds`` behind (5-6 tests failed too). With the fixtures registered at the
rootdir conftest the isolation applies in any order; this test asserts the trio passes AND the
throwaway store is never written. It would FAIL again if the isolation regressed — without ever
touching the developer's actual ZenML store (the subprocess is sandboxed to ``tmp_path``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The Tester's original failing order: secret-creating runtime file → non-isolated cli file → runtime.
# The secret-creating file is now ``test_store_isolation.py`` (it round-trips the ``decode-dev``
# Environment Bucket); the file the order was first found with, ``test_secret_store_config.py``, went
# away with the secret-store config source (ADR-0015 §4, task 097).
_ADVERSE_ORDER = (
    "tests/unit/decode/runtime/test_store_isolation.py",
    "tests/unit/decode/test_cli.py",
    "tests/unit/decode/runtime/test_run_command.py",
)


def _secret_names_in(config_dir: Path) -> list[str]:
    """Return every secret name persisted in any SQLite store under ``config_dir`` (the fake store)."""
    import sqlite3

    names: list[str] = []
    for db in config_dir.rglob("*.db"):
        con = sqlite3.connect(db)
        try:
            names += [row[0] for row in con.execute("SELECT name FROM secret")]
        except sqlite3.Error:
            pass  # no secret table yet — nothing was written, which is the passing case
        finally:
            con.close()
    return names


def test_adverse_collection_order_never_writes_a_real_store(tmp_path: Path) -> None:
    """Run the Tester's failing trio in a sandboxed subprocess; it passes and leaks no secret."""
    fake_home = tmp_path / "fake-home"
    config_dir = fake_home / "zenml-config"
    config_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "HOME": str(fake_home),  # the subprocess's "real" home — a throwaway, never the developer's
        "ZENML_CONFIG_PATH": str(config_dir),
        "ZENML_ANALYTICS_OPT_IN": "false",
    }

    result = subprocess.run(
        [sys.executable, "-m", "pytest", *_ADVERSE_ORDER, "-p", "no:randomly", "-q"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, (
        "the adverse order regressed:\n" + result.stdout[-4000:] + result.stderr[-2000:]
    )
    leaked = _secret_names_in(config_dir)
    assert not any(name.startswith("decode-") for name in leaked), (
        f"a runtime test wrote a secret to the sandboxed 'real' store — isolation regressed: {leaked}"
    )
