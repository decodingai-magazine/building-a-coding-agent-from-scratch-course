"""Unit coverage for the committed demo skills (ADR-0017 §2 Track A, tasks 117-119).

The demo skills live at the repo root under ``.decode/skills/demo-N-*/`` and are graded by humans,
not the harness — so the only thing to pin automatically is that they stay *loadable* and that the
fixtures their bodies reference stay honest: demo-2's seeded repo must genuinely fail exactly two
tests as committed. Everything runs through decode's REAL skills loader
(``decode.skills.loader``), mirroring ``test_loader.py``.

The prompt-only demos — demo-1 (terminal-arcade), demo-3 (repo-pulse), demo-4 (review-swarm),
demo-5 (sandbox-feature-pr), and demo-6 (article-kg) — ship no fixtures, so they owe the
loader-parse coverage plus a pin on the contract their body promises. demo-5 gets one extra guard:
its documented invocation must match the REAL CLI (``--repo`` exists; sandbox mode is the
``SANDBOX_MODE`` env var, never an invented ``--sandbox`` flag).
"""

from __future__ import annotations

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

DEMO_1 = "demo-1-terminal-arcade"
DEMO_2 = "demo-2-bug-hunt"
DEMO_3 = "demo-3-repo-pulse"
DEMO_4 = "demo-4-review-swarm"
DEMO_5 = "demo-5-sandbox-feature-pr"
DEMO_6 = "demo-6-article-kg"
# All but demo-2 are prompt-only (SKILL.md, no sibling resources).
PROMPT_ONLY_DEMOS = [DEMO_1, DEMO_3, DEMO_4, DEMO_5, DEMO_6]
AUTHORED_DEMOS = [DEMO_1, DEMO_2, DEMO_3, DEMO_4, DEMO_5, DEMO_6]


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
    # Discovery over the real repo-root skills dir surfaces all five by frontmatter name, so they
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
    # demo-2 ships a ``references/`` sibling -> ``resource_dir`` set; the prompt-only demos are
    # SKILL.md-only -> ``None``. This is what lets the loader render its fixtures cwd-relative.
    found = loader.discover_project_skills(REPO_ROOT)

    assert found[DEMO_2].resource_dir == DEMO_SKILLS_DIR / DEMO_2
    for prompt_only in PROMPT_ONLY_DEMOS:
        assert found[prompt_only].resource_dir is None


# demo-1: the arcade body pins the single-file, pure-stdlib curses contract


def test_demo_1_pins_single_file_stdlib_curses_contract():
    body = _skill_text(DEMO_1)

    assert "snake.py" in body
    assert "curses" in body
    assert "standard library" in body.lower()


# demo-3: the repo-pulse body pins the live-API, single-file dashboard contract


def test_demo_3_pins_live_api_dashboard_contract():
    body = _skill_text(DEMO_3)

    assert "api.github.com" in body
    assert "dashboard.html" in body
    # A full year of weekly commit counts in ONE request — never paging /commits.
    assert "stats/commit_activity" in body
    assert "52" in body
    # Charts are drawn directly in the HTML as inline SVG — no chart library, no image files.
    assert "<svg" in body
    assert "matplotlib" not in body.lower()
    assert "base64" not in body.lower()


# demo-4: the review-swarm body fans out three parallel read-only Explore subagents


def test_demo_4_fans_out_three_subagents_over_the_named_modules():
    body = _skill_text(DEMO_4)

    # The three decode modules the swarm reviews, one Explore subagent each (ADR-0013 fan-out).
    for module in ("src/decode/permissions/", "src/decode/sandbox/", "src/decode/context/"):
        assert module in body, f"body must name the {module} review target"
    # The merged verdict is severity-ranked and carries a text diagram per module.
    for severity in ("Critical", "Major", "Minor"):
        assert severity in body, f"verdict must rank by {severity}"
    assert re.search(r"mermaid|ascii", body, re.IGNORECASE), "verdict must include a text diagram"


# demo-5: the documented invocation matches the REAL CLI (no invented --sandbox flag)


def _cli_option_names(command: click.Command) -> set[str]:
    """Every long-option string declared on a Click command (e.g. ``--repo``)."""
    return {opt for param in command.params for opt in getattr(param, "opts", [])}


def test_cli_exposes_repo_but_no_sandbox_flag():
    # Guards the demo-5 body against drift: ``--repo`` is real, ``--sandbox`` was never added.
    root_opts = _cli_option_names(cli)
    run_opts = _cli_option_names(cli.commands["run"])

    assert "--repo" in root_opts
    assert "--repo" in run_opts
    assert "--sandbox" not in root_opts
    assert "--sandbox" not in run_opts


def test_demo_5_body_uses_the_real_invocation_shape():
    body = _skill_text(DEMO_5)

    # The documented launch uses SANDBOX_MODE=docker + --repo, names the modal rung, and ends in a
    # draft PR — and never invents a --sandbox flag the CLI does not have.
    assert "SANDBOX_MODE=docker" in body
    assert "SANDBOX_MODE=modal" in body
    assert "--repo" in body
    assert "--sandbox" not in body
    assert "gh pr create" in body and "--draft" in body


def test_demo_5_targets_the_real_course_repo():
    body = _skill_text(DEMO_5)

    # The invocation clones the actual course repo (matches ``git remote get-url origin``).
    assert "decodingai-magazine/building-a-coding-agent-from-scratch-course" in body


def test_demo_5_covers_pushing_the_branch_and_the_prepush_timeout():
    # The wrap-up must be reliable even when the model pushes the branch itself (no sandbox / its
    # own feature branch): the observed run flailed because ``git push`` fires this repo's pre-push
    # hook (the full unit suite, ~2 min) and got killed by a short tool timeout, then bypassed the
    # gate with ``--no-verify``. The body must name the pre-push suite, tell the model to give the
    # push a generous timeout, and steer it away from ``--no-verify``.
    body = _skill_text(DEMO_5)

    assert "pre-push" in body
    assert re.search(r"git push", body)
    assert "timeout" in body.lower()
    assert (
        "--no-verify" in body
    )  # named specifically to say "do not reach for it to dodge the gate"


# demo-6: the article-kg body pins the fetch → extract → self-contained interactive page contract


def test_demo_6_pins_the_articles_and_the_kg_page_contract():
    body = _skill_text(DEMO_6)

    # The two live Decoding AI sources, fetched with decode's own web_fetch tool.
    for slug in (
        "understanding-neo4j-graph-agent-memory-system",
        "ship-a-knowledge-graph-ontology-in-5-minutes",
    ):
        assert f"https://www.decodingai.com/p/{slug}" in body
    assert "keep-knowledge-graph-clean" not in body
    assert "web_fetch" in body
    # Two artifacts: the extraction checkpoint and the page embedding the same data.
    assert "graph.json" in body
    assert "kg.html" in body
    assert "const GRAPH" in body
    # The page is hand-rolled and self-contained: a vanilla-JS force sim, no graph library/CDN.
    assert re.search(r"force", body, re.IGNORECASE)
    assert "vanilla" in body.lower()
    for library in ("d3", "cytoscape", "vis-network"):
        assert not re.search(rf"\buse {library}\b", body, re.IGNORECASE)
    # The interactions the demo promises.
    assert "drag" in body.lower()
    assert "hover" in body.lower()
    # Anti-placeholder guard (observed bug): the body must forbid a substitution token like
    # ``{{GRAPH}}`` and require pasting the literal JSON, and the verify step must catch a
    # leftover ``{{`` so a never-substituted template can't pass as "self-contained".
    assert "{{" in body, "body must call out the placeholder token to forbid it"
    assert "placeholder" in body.lower()


def test_demo_artifacts_land_in_the_outputs_dir():
    # Demo work products default into `.decode/outputs/` (gitignored — `.decode/*` minus skills),
    # so a demo run never litters the project tree. demo-5 is exempt: its work product is a
    # Session Branch + draft PR, not files.
    for demo in (DEMO_1, DEMO_2, DEMO_3, DEMO_4, DEMO_6):
        assert ".decode/outputs/" in _skill_text(demo), f"{demo} must target .decode/outputs/"


def test_no_demo_mentions_the_credential_proxy():
    # Credential-proxy involvement is an explicit non-goal (ADR-0017 Context) — keep it out of all
    # authored demo bodies, not just demo-5.
    for demo in AUTHORED_DEMOS:
        assert "credential proxy" not in _skill_text(demo).lower()


# fixtures the bodies reference exist


def test_demo_2_buggy_repo_fixtures_exist():
    repo = DEMO_SKILLS_DIR / DEMO_2 / "references" / "buggy_repo"

    assert (repo / "stats.py").is_file()
    assert (repo / "test_stats.py").is_file()


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
