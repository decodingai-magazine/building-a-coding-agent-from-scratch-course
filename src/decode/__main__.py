"""``python -m decode`` entrypoint.

Thin by design: bootstrap logging, then hand off to the Click group in :mod:`decode.cli`.
``init_logger()`` runs at module level before any other project import (the entrypoint convention).

The installed ``decode`` script is the user-facing entrypoint; this module is what lets a CHILD
process reach the same cli through the parent's own interpreter — a Benchmark Trial launches
``[sys.executable, "-m", "decode", "run", …]`` so the subprocess is guaranteed to run the harness's
venv rather than whatever ``decode`` happens to be first on ``$PATH`` (ADR-0022 §1).
"""

from __future__ import annotations

from decode.logging import init_logger

init_logger()

from decode.cli import cli  # noqa: E402  (intentional post-logger import)

if __name__ == "__main__":
    cli()
