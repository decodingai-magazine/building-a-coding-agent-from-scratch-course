"""Bundled built-in Skills Catalog directories (packaged data; ADR-0004 §3).

Each subdirectory is one built-in skill (``<name>/SKILL.md``), loaded via
:mod:`importlib.resources` by :mod:`decode.skills.loader` so it ships in the installed wheel.
The skill subdirectories are resource dirs, not Python packages (no ``__init__.py``). This
module exists only to make ``builtin`` an importable package for resource loading.
"""
