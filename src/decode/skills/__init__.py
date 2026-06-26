"""The Skills Catalog: bundled + project-local instruction documents and a loader (ADR-0004).

A **skill** is a reusable, named Markdown instruction document the model (or the user) pulls in on
demand — a `commit` skill that stages and commits the working tree, a `review-diff` skill that reviews
it, plus any number a team drops into their repo. Structurally a near-twin of the Agents Catalog: each
skill is a directory ``<name>/SKILL.md`` (YAML `name` / `description` frontmatter + a Markdown body),
parsed + validated into a :class:`~decode.entities.skill_def.SkillDef`.

Skills load from **two sources** (ADR-0004 §3): built-ins ship as *packaged data* under
:mod:`decode.skills.builtin` as ``<name>/SKILL.md`` directories (loaded via :mod:`importlib.resources`,
so they live in the installed wheel); project-local skills are discovered at
``<cwd>/<settings.skills_dir>/<name>/SKILL.md`` (cwd-relative, like memory). :func:`load_skills` merges
them keyed by **frontmatter name** (the directory name is cosmetic), with a project skill
intentionally overriding a built-in of the same name (``source`` keeps the provenance traceable). This
module is **pure load + merge** (task 025): the dispatcher tool (026), catalog injection (027), and the
``/<skill-name>`` TUI command (028) come later.
"""
