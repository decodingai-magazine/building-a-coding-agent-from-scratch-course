"""``decode remote`` — the Modal Headless App and the ``decode`` subcommands that launch it (ADR-0020).

Launch-vs-execute split: the laptop only launches (:mod:`decode.remote.cli`), Modal executes
(:mod:`decode.remote.app`, deployed once with ``decode remote deploy``). Nothing here is imported by
the REPL or by ``decode run``; ``modal`` itself is imported only by the app module and, lazily,
inside the subcommands that talk to the deployment.
"""
