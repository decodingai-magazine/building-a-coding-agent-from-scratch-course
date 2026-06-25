"""Unit tests for the permission rule engine (``decode.permissions.rules``).

ADR-0003 §4 / task 018: a **Permission Rule** is ``Tool(pattern)`` or a bare ``Tool``, parsed
into ``(tool_name, pattern | None)`` and matched (glob via ``fnmatch``) against a per-kind
**subject** (``bash`` → the command; file tools → the path; ``web_fetch`` → the url; bare ``Tool``
matches any call of that tool). Rules load from the user ``.decode/settings.json`` shape
``{"permissions": {"allow": [...], "deny": [...]}}`` into a :class:`RuleSet`; a missing/malformed
file is non-fatal (logged, treated as no rules).
"""

import json
import logging

import pytest

from decode.entities.permissions import PermissionRequest
from decode.permissions import rules
from decode.permissions.types import ToolKind

# --- parsing rule strings -------------------------------------------------------------------


def test_parse_rule_with_pattern():
    rule = rules.parse_rule("bash(rm *)")

    assert rule.tool_name == "bash"
    assert rule.pattern == "rm *"


def test_parse_bare_rule_has_no_pattern():
    rule = rules.parse_rule("read")

    assert rule.tool_name == "read"
    assert rule.pattern is None


def test_parse_rule_strips_surrounding_whitespace():
    rule = rules.parse_rule("  web_fetch( https://example.com/* )  ")

    assert rule.tool_name == "web_fetch"
    assert rule.pattern == "https://example.com/*"


@pytest.mark.parametrize("text", ["", "   ", "()", "(pattern)"])
def test_parse_rule_rejects_a_missing_tool_name(text):
    with pytest.raises(ValueError, match="rule"):
        rules.parse_rule(text)


# --- subject extraction per tool kind -------------------------------------------------------


def test_subject_for_bash_is_the_command():
    subject = rules.subject_for("bash", '{"command": "rm -rf x"}')

    assert subject == "rm -rf x"


@pytest.mark.parametrize("tool_name", ["read", "write", "edit", "glob", "grep"])
def test_subject_for_file_tools_is_the_path(tool_name):
    subject = rules.subject_for(tool_name, '{"path": "src/app.py"}')

    assert subject == "src/app.py"


def test_subject_for_web_fetch_is_the_url():
    subject = rules.subject_for("web_fetch", '{"url": "https://example.com/a"}')

    assert subject == "https://example.com/a"


def test_subject_for_other_tool_is_the_tool_name():
    # Anything without a known subject field falls back to the tool name itself.
    subject = rules.subject_for("todo_write", '{"items": []}')

    assert subject == "todo_write"


def test_subject_for_handles_malformed_args_json():
    # A non-JSON args blob must not crash; fall back to the tool name.
    subject = rules.subject_for("bash", "not json at all")

    assert subject == "bash"


# --- matching rule against request ----------------------------------------------------------


def _request(tool_name: str, subject: str, kind: ToolKind = ToolKind.OTHER) -> PermissionRequest:
    return PermissionRequest(tool_name=tool_name, args="", kind=kind, subject=subject)


def test_matches_bare_rule_matches_any_call_of_that_tool():
    rule = rules.parse_rule("read")

    assert rules.matches(rule, _request("read", "anything.txt")) is True
    assert rules.matches(rule, _request("read", "")) is True


def test_bare_rule_does_not_match_a_different_tool():
    rule = rules.parse_rule("read")

    assert rules.matches(rule, _request("write", "anything.txt")) is False


def test_matches_glob_pattern_against_subject():
    rule = rules.parse_rule("bash(rm *)")

    assert rules.matches(rule, _request("bash", "rm -rf x")) is True
    assert rules.matches(rule, _request("bash", "npm run test")) is False


def test_pattern_rule_requires_the_same_tool_name():
    rule = rules.parse_rule("bash(rm *)")

    assert rules.matches(rule, _request("write", "rm -rf x")) is False


def test_matches_npm_glob_pattern():
    rule = rules.parse_rule("bash(npm run test:*)")

    assert rules.matches(rule, _request("bash", "npm run test:unit")) is True
    assert rules.matches(rule, _request("bash", "npm run build")) is False


# --- loading a RuleSet from .decode/settings.json -------------------------------------------


def test_load_rule_set_parses_allow_and_deny(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"permissions": {"allow": ["read", "bash(npm *)"], "deny": ["bash(rm *)"]}}),
        encoding="utf-8",
    )

    rule_set = rules.load_rule_set(path)

    assert [r.tool_name for r in rule_set.allow] == ["read", "bash"]
    assert [r.tool_name for r in rule_set.deny] == ["bash"]


def test_load_rule_set_missing_file_is_empty(tmp_path):
    rule_set = rules.load_rule_set(tmp_path / "does-not-exist.json")

    assert rule_set.allow == []
    assert rule_set.deny == []


def test_load_rule_set_missing_file_logs_nothing_noisy(tmp_path, caplog):
    # A missing file is the common case (the file is optional) — it must NOT warn.
    with caplog.at_level(logging.WARNING):
        rules.load_rule_set(tmp_path / "absent.json")

    assert caplog.records == []


def test_load_rule_set_malformed_json_is_non_fatal_and_warns(tmp_path, caplog):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        rule_set = rules.load_rule_set(path)

    assert rule_set.allow == []
    assert rule_set.deny == []
    assert any("settings.json" in r.message or "malformed" in r.message for r in caplog.records)


def test_load_rule_set_unknown_shape_is_non_fatal(tmp_path):
    # A valid-JSON file missing the "permissions" key yields no rules (not a crash).
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"something_else": 1}), encoding="utf-8")

    rule_set = rules.load_rule_set(path)

    assert rule_set.allow == []
    assert rule_set.deny == []


def test_load_rule_set_skips_unparseable_rule_strings(tmp_path, caplog):
    # One bad rule string must not sink the whole file; the good rules still load.
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"permissions": {"allow": ["read", "()"]}}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        rule_set = rules.load_rule_set(path)

    assert [r.tool_name for r in rule_set.allow] == ["read"]


# --- building a persistable allow-rule string from a request --------------------------------


def test_allow_rule_string_for_a_subject():
    request = _request("bash", "npm run test:unit")

    assert rules.allow_rule_string(request) == "bash(npm run test:unit)"


def test_allow_rule_string_falls_back_to_bare_tool_when_subject_is_the_tool_name():
    # When the subject IS the tool name (no meaningful subject), persist a bare ``Tool`` rule.
    request = _request("todo_write", "todo_write")

    assert rules.allow_rule_string(request) == "todo_write"


def test_allow_rule_string_falls_back_to_bare_tool_when_subject_is_empty():
    request = _request("noop", "")

    assert rules.allow_rule_string(request) == "noop"


# --- persisting an allow rule to .decode/settings.json --------------------------------------


def test_persist_allow_rule_creates_the_file_and_parent_dir(tmp_path):
    path = tmp_path / "nested" / "settings.json"
    request = _request("bash", "npm run test:unit")

    rules.persist_allow_rule(path, request)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["bash(npm run test:unit)"]


def test_persist_allow_rule_appends_to_existing_rules(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"permissions": {"allow": ["read"], "deny": ["bash(rm *)"]}}),
        encoding="utf-8",
    )

    rules.persist_allow_rule(path, _request("bash", "npm run test:unit"))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["read", "bash(npm run test:unit)"]
    assert data["permissions"]["deny"] == ["bash(rm *)"]  # untouched


def test_persist_allow_rule_is_idempotent(tmp_path):
    path = tmp_path / "settings.json"
    request = _request("bash", "npm run test:unit")

    rules.persist_allow_rule(path, request)
    rules.persist_allow_rule(path, request)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["bash(npm run test:unit)"]  # no duplicate


def test_persist_allow_rule_then_load_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    rules.persist_allow_rule(path, _request("bash", "npm run test:unit"))

    rule_set = rules.load_rule_set(path)

    assert rule_set.matching_allow(_request("bash", "npm run test:unit")) is not None


def test_persist_allow_rule_preserves_unrelated_top_level_keys(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"other": 42, "permissions": {"allow": []}}), encoding="utf-8")

    rules.persist_allow_rule(path, _request("read", "x.txt"))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["other"] == 42


# --- RuleSet.matched_* helpers --------------------------------------------------------------


def test_rule_set_first_matching_allow():
    rule_set = rules.RuleSet(allow=[rules.parse_rule("bash(npm *)")], deny=[])

    assert rule_set.matching_allow(_request("bash", "npm run test")) is not None
    assert rule_set.matching_allow(_request("bash", "rm -rf x")) is None


def test_rule_set_first_matching_deny():
    rule_set = rules.RuleSet(allow=[], deny=[rules.parse_rule("bash(rm *)")])

    assert rule_set.matching_deny(_request("bash", "rm -rf x")) is not None
    assert rule_set.matching_deny(_request("bash", "ls")) is None
