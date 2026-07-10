"""The Skills Catalog: bundled + project-local instruction documents and a loader (ADR-0004).

A **skill** is a reusable, named Markdown instruction document pulled in on demand: a directory
``<name>/SKILL.md`` (YAML ``name`` / ``description`` frontmatter + a Markdown body), parsed into
a :class:`~decode.entities.skill_def.SkillDef`. Built-ins ship as packaged data under
:mod:`decode.skills.builtin`; project skills live at ``<cwd>/<settings.skills_dir>``.
:func:`load_skills` merges them keyed by frontmatter name, a project skill intentionally
overriding a same-name built-in.
"""
