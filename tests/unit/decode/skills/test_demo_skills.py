"""Unit coverage for the committed demo skills (ADR-0017 §2 Track A, tasks 117-119).

The demo skills live at the repo root under ``.decode/skills/demo-N-*/`` and are graded by humans,
not the harness — so the only thing to pin automatically is that they stay *loadable* and that the
fixtures their bodies reference stay honest: demo-2's seeded repo must genuinely fail exactly two
tests as committed, and demo-4's CSV must carry every mess type its body promises. Everything runs
through decode's REAL skills loader (``decode.skills.loader``), mirroring ``test_loader.py``.

Task 119 adds three prompt-only demos — demo-5 (review-swarm), demo-6 (sandbox-feature-pr), and
demo-7 (todoist-app). They ship no fixtures, so they only owe the loader-parse coverage plus one
extra guard for demo-6: its documented invocation must match the REAL CLI (``--repo`` exists;
sandbox mode is the ``SANDBOX_MODE`` env var, never an invented ``--sandbox`` flag).
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click
import pytest

from decode.cli import cli
from decode.skills import loader

# ``tests/unit/decode/skills/test_demo_skills.py`` -> repo root -> the committed demo skills tree.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEMO_SKILLS_DIR = REPO_ROOT / ".decode" / "skills"

# The demo skills this task family authors (demo-1 ships separately and is untouched).
DEMO_2 = "demo-2-bug-hunt"
DEMO_3 = "demo-3-terminal-arcade"
DEMO_4 = "demo-4-data-detective"
DEMO_5 = "demo-5-review-swarm"
DEMO_6 = "demo-6-sandbox-feature-pr"
DEMO_7 = "demo-7-todoist-app"
# demo-3 and the three task-119 demos are prompt-only (SKILL.md, no sibling resources).
PROMPT_ONLY_DEMOS = [DEMO_3, DEMO_5, DEMO_6, DEMO_7]
AUTHORED_DEMOS = [DEMO_2, DEMO_3, DEMO_4, DEMO_5, DEMO_6, DEMO_7]


def _skill_text(dir_name: str) -> str:
    """Read a demo skill's committed ``SKILL.md`` from the repo-root skills tree."""
    return (DEMO_SKILLS_DIR / dir_name / "SKILL.md").read_text(encoding="utf-8")


# SKILL.md parses through the real loader


@pytest.mark.parametrize("dir_name", AUTHORED_DEMOS)
def test_demo_skill_md_parses_through_the_real_loader(dir_name):
    # Each demo's SKILL.md must survive decode's own parser: frontmatter name matches the directory
    # convention (dir name is cosmetic but we keep them aligned) and both fields are non-empty.
    skill = loader.parse_skill_file(_skill_text(dir_name), source="test")

    assert skill.name == dir_name
    assert skill.name.startswith("demo-")
    assert skill.description.strip()
    assert skill.body.strip()


def test_authored_demos_appear_in_the_project_catalog():
    # Discovery over the real repo-root skills dir surfaces all three by frontmatter name, so they
    # show up in the Skills Catalog and dispatch via ``/demo-2-bug-hunt`` etc.
    found = loader.discover_project_skills(REPO_ROOT)

    assert set(AUTHORED_DEMOS) <= set(found)


def test_authored_demos_load_alongside_the_builtins():
    # The merged catalog (built-ins + project) keeps the built-ins and adds the demos — nothing is
    # dropped by the merge.
    skills = loader.load_skills(REPO_ROOT)

    assert {"commit", "review-diff"} <= set(skills)
    assert set(AUTHORED_DEMOS) <= set(skills)


def test_resource_shipping_demos_carry_a_resource_dir():
    # demo-2 and demo-4 ship ``references/`` siblings -> ``resource_dir`` set; the prompt-only demos
    # are SKILL.md-only -> ``None``. This is what lets the loader render their fixtures cwd-relative.
    found = loader.discover_project_skills(REPO_ROOT)

    assert found[DEMO_2].resource_dir == DEMO_SKILLS_DIR / DEMO_2
    assert found[DEMO_4].resource_dir == DEMO_SKILLS_DIR / DEMO_4
    for prompt_only in PROMPT_ONLY_DEMOS:
        assert found[prompt_only].resource_dir is None


# demo-5: the review-swarm body fans out three parallel read-only Explore subagents


def test_demo_5_fans_out_three_subagents_over_the_named_modules():
    body = _skill_text(DEMO_5)

    # The three decode modules the swarm reviews, one Explore subagent each (ADR-0013 fan-out).
    for module in ("src/decode/permissions/", "src/decode/sandbox/", "src/decode/context/"):
        assert module in body, f"body must name the {module} review target"
    # The merged verdict is severity-ranked and carries a text diagram per module.
    for severity in ("Critical", "Major", "Minor"):
        assert severity in body, f"verdict must rank by {severity}"
    assert re.search(r"mermaid|ascii", body, re.IGNORECASE), "verdict must include a text diagram"


# demo-6: the documented invocation matches the REAL CLI (no invented --sandbox flag)


def _cli_option_names(command: click.Command) -> set[str]:
    """Every long-option string declared on a Click command (e.g. ``--repo``)."""
    return {opt for param in command.params for opt in getattr(param, "opts", [])}


def test_cli_exposes_repo_but_no_sandbox_flag():
    # Guards the demo-6 body against drift: ``--repo`` is real, ``--sandbox`` was never added.
    root_opts = _cli_option_names(cli)
    run_opts = _cli_option_names(cli.commands["run"])

    assert "--repo" in root_opts
    assert "--repo" in run_opts
    assert "--sandbox" not in root_opts
    assert "--sandbox" not in run_opts


def test_demo_6_body_uses_the_real_invocation_shape():
    body = _skill_text(DEMO_6)

    # The documented launch uses SANDBOX_MODE=docker + --repo, names the modal rung, and ends in a
    # draft PR — and never invents a --sandbox flag the CLI does not have.
    assert "SANDBOX_MODE=docker" in body
    assert "SANDBOX_MODE=modal" in body
    assert "--repo" in body
    assert "--sandbox" not in body
    assert "gh pr create" in body and "--draft" in body


def test_demo_6_targets_the_real_course_repo():
    body = _skill_text(DEMO_6)

    # The invocation clones the actual course repo (matches ``git remote get-url origin``).
    assert "decodingai-magazine/building-a-coding-agent-from-scratch-course" in body


def test_no_demo_mentions_the_credential_proxy():
    # Credential-proxy involvement is an explicit non-goal (ADR-0017 Context) — keep it out of all
    # authored demo bodies, not just demo-6.
    for demo in AUTHORED_DEMOS:
        assert "credential proxy" not in _skill_text(demo).lower()


# demo-7: the todo-app body pins the zero-dep, single-file, localStorage contract


def test_demo_7_pins_single_file_localstorage_contract():
    body = _skill_text(DEMO_7)

    assert "index.html" in body
    assert "localStorage" in body
    # The three filter states the body promises.
    for state in ("all", "active", "done"):
        assert state in body


# fixtures the bodies reference exist


def test_demo_2_buggy_repo_fixtures_exist():
    repo = DEMO_SKILLS_DIR / DEMO_2 / "references" / "buggy_repo"

    assert (repo / "stats.py").is_file()
    assert (repo / "test_stats.py").is_file()


def test_demo_4_messy_csv_exists():
    assert (DEMO_SKILLS_DIR / DEMO_4 / "references" / "messy_sales.csv").is_file()


# demo-2: the seeded repo genuinely fails exactly two tests as committed


def test_demo_2_buggy_repo_fails_exactly_two_tests(tmp_path):
    # The demo only works if the seeded suite is genuinely red on arrival — copy it out (as the body
    # tells the agent to) and run pytest against the copy, isolated from this project's config.
    repo_copy = tmp_path / "buggy_repo"
    shutil.copytree(DEMO_SKILLS_DIR / DEMO_2 / "references" / "buggy_repo", repo_copy)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_stats.py",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
        ],
        cwd=repo_copy,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, f"expected failures, got green:\n{result.stdout}"
    assert " error" not in result.stdout, (
        f"collection/import error, not a clean fail:\n{result.stdout}"
    )
    failed = re.search(r"(\d+) failed", result.stdout)
    assert failed is not None, f"no pytest summary line found:\n{result.stdout}"
    assert failed.group(1) == "2", f"expected exactly 2 failures:\n{result.stdout}"


# demo-4: the CSV carries every listed mess type


def _csv_rows() -> list[dict[str, str]]:
    path = DEMO_SKILLS_DIR / DEMO_4 / "references" / "messy_sales.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_messy_csv_has_duplicate_rows():
    rows = [tuple(row.items()) for row in _csv_rows()]

    assert len(rows) != len(set(rows)), "expected at least one exact duplicate row"


def test_messy_csv_has_mixed_date_formats():
    # "Mixed" means more than one format family in the same column.
    families = {
        "iso": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
        "us_slash": re.compile(r"^\d{2}/\d{2}/\d{4}$"),
        "named_month": re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$"),
    }
    dates = [row["order_date"] for row in _csv_rows()]
    present = {name for name, pat in families.items() if any(pat.match(d) for d in dates)}

    assert len(present) >= 2, f"expected mixed date formats, saw only {present}"


def test_messy_csv_has_currency_strings():
    # At least one amount is a currency string (symbol or thousands separator), not a plain number.
    amounts = [row["amount"] for row in _csv_rows()]

    assert any(re.search(r"[€$,]", value) for value in amounts), (
        "expected currency-formatted amounts"
    )


def test_messy_csv_has_missing_values():
    rows = _csv_rows()

    assert any(value.strip() == "" for row in rows for value in row.values()), (
        "expected at least one blank cell"
    )
