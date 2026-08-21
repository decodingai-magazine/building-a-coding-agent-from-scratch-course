---
name: skills-release
description: Use when cutting a release of the kitaru-skills repository — pushing develop to main, bumping the kitaru plugin version, tagging a version, or publishing a GitHub Release for the Claude Code marketplace.
---

# Skills Release

## Overview

`main` is the distribution branch: `/plugin marketplace add zenml-io/kitaru-skills`
installs whatever `main` holds, immediately. `develop` is the working base. A
release moves `main` forward to `develop`'s current commit as a fast-forward
and stamps a plugin version, so `main` is always an exact snapshot of
`develop` at release time.

The `main-release-only` ruleset blocks all pushes and PR merges to `main`
except by repository admins. A release is therefore an admin running this
checklist — never a PR into `main`, never a force push.

Versions are this repository's own plugin versions. Do not reuse Kitaru
product version numbers; state tested Kitaru compatibility in the release
notes instead.

## Preconditions

Stop and resolve before releasing if any of these fail:

- On `develop` with a clean working tree.
- `git fetch origin` done; local `develop` in sync with `origin/develop`.
- Fast-forward is possible:
  `git merge-base --is-ancestor origin/main origin/develop` exits 0.
  A non-zero exit means `main` has commits `develop` lacks — investigate how
  they got there; never force-push over them.

## Steps

1. Review what ships: `git log --oneline origin/main..origin/develop`.
   Draft release notes from these commits (what changed, why it matters to
   installers), plus a "Tested against Kitaru X.Y" line when known.
2. Agree the next version with the user: minor for new or changed skills,
   patch for fixes and docs-only changes.
3. Bump all three version fields to the same value:
   - `.claude-plugin/plugin.json` → `.version`
   - `.claude-plugin/marketplace.json` → `.metadata.version`
   - `.claude-plugin/marketplace.json` → `.plugins[0].version`
4. Validate: `jq . .claude-plugin/plugin.json` and
   `jq . .claude-plugin/marketplace.json` parse, and all three fields match:

   ```bash
   jq -r .version .claude-plugin/plugin.json
   jq -r '.metadata.version, .plugins[0].version' .claude-plugin/marketplace.json
   ```

5. Commit only the two JSON files on `develop` with subject
   `Release vX.Y.Z`, then push `develop`.
6. Tag the release commit: `git tag vX.Y.Z && git push origin vX.Y.Z`.
7. Fast-forward `main`: `git push origin develop:main`. No `--force` ever —
   a rejection means the push is not a fast-forward; go back to
   Preconditions.
8. Publish the drafted notes via a file, never inline in the command —
   commit subjects routinely contain backticks, which the shell would
   execute as command substitution inside a `--notes "..."` argument.
   Write the notes to a scratch file with a file-writing tool (or a
   quoted heredoc, `cat > notes.md <<'EOF'`), then:

   ```bash
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file notes.md
   ```
9. Verify: `git ls-remote origin refs/heads/main refs/heads/develop` shows
   identical SHAs, and the GitHub Release page exists.

## Recovery

- Push to `main` rejected: confirm admin (bypass) rights and re-run the
  ancestor check from Preconditions.
- Wrong version committed but not yet tagged: fix the JSON files and amend
  the release commit before tagging.
- Tag already pushed with wrong content: if the GitHub Release is not yet
  published, delete and re-create the tag; otherwise cut a follow-up patch
  release rather than rewriting history.

## Maintenance

The canonical copy of this skill is `.claude/skills/skills-release/SKILL.md`.
`.agents/skills/skills-release` is a symlink to it — edit only the canonical
file.
