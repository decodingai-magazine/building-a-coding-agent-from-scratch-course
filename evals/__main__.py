"""``python -m evals`` entrypoint.

Thin by design: bootstrap logging, then hand off to the Click group in :mod:`evals.run`.
``init_logger()`` runs at module level before any other project import (the entrypoint convention).
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

from evals.run import cli  # noqa: E402  (intentional post-logger import)

if __name__ == "__main__":
    cli()
