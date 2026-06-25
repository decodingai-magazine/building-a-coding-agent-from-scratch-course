"""Bundled built-in Skills Catalog files (packaged data; ADR-0004 §3).

The ``*.md`` files in this package are the two built-in skills (``commit`` / ``review-diff``) — YAML
frontmatter (``name`` / ``description``) + a Markdown instruction body. They are loaded as **packaged
data** via :mod:`importlib.resources` by :mod:`decode.skills.loader`, so they ship inside the installed
wheel (hatchling includes every file under the package directory by default, ``.md`` included —
verified against ``uv build``). This module exists only to make ``builtin`` an importable package for
resource loading.
"""
