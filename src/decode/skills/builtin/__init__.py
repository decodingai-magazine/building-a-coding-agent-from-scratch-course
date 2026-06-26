"""Bundled built-in Skills Catalog directories (packaged data; ADR-0004 §3).

Each subdirectory here is one built-in skill in the Agent Skills directory convention
``<name>/SKILL.md`` (``commit/SKILL.md`` / ``review-diff/SKILL.md``) — YAML frontmatter (``name`` /
``description``) + a Markdown instruction body. They are loaded as **packaged data** via
:mod:`importlib.resources` nested traversal by :mod:`decode.skills.loader`, so they ship inside the
installed wheel (hatchling includes every file under the package directory by default, the nested
``<name>/SKILL.md`` data files included — verified against ``uv build``). The skill subdirectories are
resource dirs, **not** Python packages — they carry no ``__init__.py``; ``importlib.resources``
traverses them without one. This module exists only to make ``builtin`` an importable package for
resource loading.
"""
