"""Drift guard: ``decode.__version__`` ≡ the version declared in ``pyproject.toml``.

The version used to be written twice — ``pyproject.toml`` and a literal in ``decode/__init__.py`` —
and ``decode --version`` read the *latter*. Bumping one and forgetting the other made the CLI report
a version that was never published, and nothing caught it: no test asserted the two agreed.
``__init__`` now derives it from the installed distribution's metadata, which hatchling generates
from ``pyproject.toml``, leaving exactly one place to bump. This pins that derivation both at the
package and at the user-facing flag.
"""

import tomllib
from pathlib import Path

from click.testing import CliRunner

import decode
from decode.cli import cli

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def declared_version() -> str:
    """``project.version`` in ``pyproject.toml`` — the one place a release version is written."""
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_package_version_is_the_declared_version():
    """A mismatch means the derivation broke, or a second literal crept back into ``__init__.py``."""
    assert decode.__version__ == declared_version(), (
        f"decode.__version__ is {decode.__version__!r} but pyproject.toml declares "
        f"{declared_version()!r}. __init__.py must derive it from the installed metadata "
        "(importlib.metadata.version('decode')) and never hardcode it — a second literal is how "
        "`decode --version` starts reporting a version that was never published. If the literals "
        "genuinely agree upstream, the editable install is stale instead: re-run `uv sync`."
    )


def test_cli_version_flag_prints_the_declared_version():
    """The user-facing half: ``decode --version`` exits 0 and names the declared version."""
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert declared_version() in result.output
