#!/usr/bin/env bash
# Oracle for 012-makefile-doctor: PASS iff the repaired Makefile actually builds ``artifact.txt``
# from a clean state. The sandbox base image ships no ``make`` (and verify may use only bash + python3
# + git + sqlite3), so a tiny portable "mini-make" in python emulates GNU make's two graded rules:
#   1. recipe lines MUST be tab-indented — a space-indented recipe line is make's "missing separator"
#      error, so we FAIL on it exactly as make would;
#   2. building the ``build`` target runs its prerequisites (depth-first) then its own recipe.
# We delete any pre-existing artifact.txt / data.txt first, so only the Makefile's own rules can
# produce the artifact (hand-creating the file without fixing the Makefile cannot pass). Any correct
# fix shape works — a ``prepare`` prerequisite or inlining the data step into ``build`` both build.
set -euo pipefail

python3 - <<'PY'
import os
import subprocess
import sys


class MakeError(Exception):
    """A structural problem GNU make itself would reject (e.g. a space-indented recipe)."""


def parse_makefile(text: str) -> dict[str, dict]:
    """Parse targets into {name: {"prereqs": [...], "recipe": [cmd, ...]}} (mini GNU-make subset)."""
    targets: dict[str, dict] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        # A comment (first non-whitespace char is '#') is a no-op in make and must be skipped BEFORE
        # any target/recipe check, so a colon inside it (e.g. "# Fixed: use a tab") is never mistaken
        # for a target header that would swallow the following recipe.
        if raw.lstrip().startswith("#"):
            continue
        if raw.startswith("\t"):
            if current is None:
                raise MakeError(f"line {lineno}: recipe line with no target")
            targets[current]["recipe"].append(raw[1:])
            continue
        if raw.strip() == "":
            continue
        if raw[0].isspace():
            # A non-tab indented, non-blank line where make expects a recipe: "missing separator".
            if current is not None:
                raise MakeError(f"line {lineno}: recipe is indented with spaces, not a tab")
            continue
        if ":" in raw:
            name, _, prereqs = raw.partition(":")
            name = name.strip()
            targets[name] = {"prereqs": prereqs.split(), "recipe": []}
            current = name
    return targets


def build(target: str, targets: dict[str, dict], done: set[str]) -> None:
    if target in done:
        return
    if target not in targets:
        raise MakeError(f"no rule to make target '{target}'")
    for prereq in targets[target]["prereqs"]:
        build(prereq, targets, done)
    for command in targets[target]["recipe"]:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            raise MakeError(f"recipe for '{target}' failed: {command!r}\n{result.stderr}")
    done.add(target)


# Clean slate: only the Makefile's rules may create the artifact and its input.
for stale in ("artifact.txt", "data.txt"):
    try:
        os.remove(stale)
    except FileNotFoundError:
        pass

try:
    text = open("Makefile", encoding="utf-8").read()
except FileNotFoundError:
    print("FAIL: Makefile is missing")
    sys.exit(1)

try:
    targets = parse_makefile(text)
    build("build", targets, set())
except MakeError as exc:
    print(f"FAIL: `make build` would not succeed: {exc}")
    sys.exit(1)

try:
    produced = open("artifact.txt", encoding="utf-8").read()
except FileNotFoundError:
    print("FAIL: building did not produce artifact.txt")
    sys.exit(1)

if produced.strip() != "payload":
    print(f"FAIL: artifact.txt should contain 'payload', got {produced!r}")
    sys.exit(1)

print("PASS")
PY
