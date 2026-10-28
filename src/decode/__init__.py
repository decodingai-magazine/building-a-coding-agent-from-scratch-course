"""decode — a terminal coding agent (agentic harness)."""

from importlib.metadata import PackageNotFoundError, version

# ``pyproject.toml`` is the ONE place a release version is written: the installed distribution's
# metadata is generated from it, so what ``decode --version`` reports can never drift from what was
# actually published. The fallback is deliberately an obvious not-a-release marker — a source tree
# with no install reports honestly instead of asserting a stale hardcoded version.
try:
    __version__ = version("decode")
except PackageNotFoundError:  # pragma: no cover - only when decode is not installed
    __version__ = "0.0.0.dev0"
