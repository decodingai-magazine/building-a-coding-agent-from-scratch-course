"""Bundled built-in Agents Catalog files (packaged data; ADR-0003 §5).

The ``*.md`` files in this package are the four built-in personas (Build / Plan / Explore /
Code-Reviewer) — YAML frontmatter + a system-prompt body. They are loaded as **packaged data**
via :mod:`importlib.resources` by :mod:`decode.agents.loader`, so they ship inside the installed
wheel (hatchling includes every file under the package directory by default, ``.md`` included —
verified against ``uv build``). This module exists only to make ``builtin`` an importable package
for resource loading.
"""
