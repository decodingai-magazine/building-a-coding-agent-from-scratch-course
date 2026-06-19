"""Project memory: read ``AGENTS.md`` / ``MEMORY.md`` and inject them into the prompt (ADR-0002 §8).

Two pure-ish modules, no agent dependency:

* :mod:`decode.memory.files` — :func:`~decode.memory.files.discover_memory_files` walks from the
  agent's ``cwd`` **up to the filesystem root**, collecting ``AGENTS.md`` and ``MEMORY.md`` at
  each level (``CLAUDE.md`` is the Claude-Code shim and is skipped). The walk is ordered
  **root-most first, cwd-most last** so the most specific memory wins.
* :mod:`decode.memory.service` — :func:`~decode.memory.service.assemble_memory` reads those files,
  joins them with a provenance header per file, and caps ``MEMORY.md`` at
  ``settings.memory_max_lines`` lines AND ``settings.memory_max_bytes`` bytes with a visible
  truncation note.

The factory (:mod:`decode.agent.factory`) registers a dynamic ``@agent.instructions`` hook that
calls :func:`~decode.memory.service.assemble_memory` at prompt-build time — so editing a memory
file takes effect on the next run with no rebuild. Memory **write-back** (the model appending a
one-sentence summary to ``MEMORY.md`` on session exit) is task 013; ``@``-imports and a
user-global ``~/.decode/`` are out of scope for M1.
"""
