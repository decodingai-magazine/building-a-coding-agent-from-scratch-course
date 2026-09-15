"""The ``kitaru`` CLI seam both bridge commands shell through (task 165, ADR-0022 §10).

Nothing here starts a process: :func:`evals.harness.kitaru_cli.run_kitaru` is driven with a fake
``subprocess.run`` whose recorded argv IS the assertion — the envelopes it answers with are copies
of what kitaru 0.26.0 actually returned on a local OSS server (``kitaru login --local``).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from evals.harness import kitaru_cli
from evals.harness.kitaru_cli import (
    KITARU_API_URL_ENV,
    KitaruCommandError,
    kitaru_argv,
    kitaru_keys_missing,
    kitaru_server,
    list_sessions,
    resolve_ref,
    run_kitaru,
)

SERVER = "http://localhost:8000"


class FakeRun:
    """A stand-in for ``subprocess.run`` that records argv and replays canned envelopes."""

    def __init__(self, *envelopes: dict[str, Any], returncode: int = 0) -> None:
        self.envelopes = list(envelopes)
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        payload = self.envelopes.pop(0) if self.envelopes else {"ok": True}
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=json.dumps(payload), stderr=""
        )


def install(mocker, *envelopes: dict[str, Any], returncode: int = 0) -> FakeRun:
    fake = FakeRun(*envelopes, returncode=returncode)
    mocker.patch.object(subprocess, "run", fake)
    return fake


# --- the server URL: process env, never Settings ---------------------------------------------------


def test_the_server_url_comes_from_the_exported_process_env(monkeypatch):
    """ADR-0019 §3: decode owns no kitaru connection setting — the operator exports the URL."""
    monkeypatch.setenv(KITARU_API_URL_ENV, SERVER)

    assert kitaru_server() == SERVER


def test_an_unset_server_url_is_none_not_an_empty_string(monkeypatch):
    monkeypatch.delenv(KITARU_API_URL_ENV, raising=False)

    assert kitaru_server() is None


def test_a_blank_server_url_reads_as_unset(monkeypatch):
    monkeypatch.setenv(KITARU_API_URL_ENV, "   ")

    assert kitaru_server() is None


def test_the_keyless_skip_names_both_halves_of_the_join(monkeypatch, mocker):
    """A bridge command needs Opik (to read traces) AND a Kitaru Server (to write sessions)."""
    monkeypatch.delenv(KITARU_API_URL_ENV, raising=False)
    mocker.patch.object(kitaru_cli, "eval_keys_missing", return_value=["OPIK_API_KEY"])

    assert kitaru_keys_missing() == ["OPIK_API_KEY", KITARU_API_URL_ENV]


def test_nothing_is_missing_when_both_halves_resolve(monkeypatch, mocker):
    monkeypatch.setenv(KITARU_API_URL_ENV, SERVER)
    mocker.patch.object(kitaru_cli, "eval_keys_missing", return_value=[])

    assert kitaru_keys_missing() == []


# --- the argv: json, non-interactive, one explicit server ------------------------------------------


def test_every_invocation_asks_for_json_and_never_prompts():
    argv = kitaru_argv(["session", "list"])

    assert argv[:3] == ["kitaru", "session", "list"]
    assert "--output" in argv and argv[argv.index("--output") + 1] == "json"
    assert "--non-interactive" in argv


def test_the_server_is_passed_explicitly_so_a_login_store_cannot_retarget_the_command():
    argv = kitaru_argv(["session", "list"], server=SERVER)

    assert argv[argv.index("--server") + 1] == SERVER


def test_no_server_option_is_added_when_there_is_no_url():
    assert "--server" not in kitaru_argv(["session", "list"])


# --- run_kitaru: one envelope, or one error naming the argv ----------------------------------------


def test_a_successful_invocation_returns_the_parsed_envelope(mocker):
    fake = install(mocker, {"ok": True, "item": {"id": "abc"}})

    envelope = run_kitaru(["agent", "get", "decode"], server=SERVER)

    assert envelope["item"]["id"] == "abc"
    assert fake.calls[0][:3] == ["kitaru", "agent", "get"]
    assert "--server" in fake.calls[0]


def test_an_error_envelope_raises_with_the_servers_own_message(mocker):
    install(mocker, {"ok": False, "error": {"kind": "not_found", "message": "Agent not found."}})

    with pytest.raises(KitaruCommandError) as error:
        run_kitaru(["agent", "get", "decode"], server=SERVER)

    assert "Agent not found." in str(error.value)
    assert "kitaru agent get decode" in str(error.value)


def test_a_non_zero_exit_with_an_error_envelope_still_names_the_reason(mocker):
    install(
        mocker,
        {"ok": False, "error": {"kind": "conflict", "message": "already registered"}},
        returncode=1,
    )

    with pytest.raises(KitaruCommandError) as error:
        run_kitaru(["agent", "register", "decode"], server=SERVER)

    assert "already registered" in str(error.value)


def test_an_error_envelope_on_stderr_is_read_like_one_on_stdout(mocker):
    """Regression (live, kitaru 0.26): a FAILING sub-command writes its envelope to STDERR.

    Parsing stdout alone turned every 404 into "answered with no json envelope", which hid the
    ``not_found`` kind callers branch on (`cohort get` → create it).
    """
    envelope = {
        "ok": False,
        "error": {
            "kind": "not_found",
            "message": "Cohort 'decode-benchmark-failures' was not found.",
        },
    }
    mocker.patch.object(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 4, stdout="", stderr=json.dumps(envelope)
        ),
    )

    with pytest.raises(KitaruCommandError) as error:
        run_kitaru(["cohort", "get", "decode-benchmark-failures"], server=SERVER)

    assert error.value.kind == "not_found"
    assert "was not found" in str(error.value)


def test_unparseable_output_is_one_error_not_a_json_traceback(mocker):
    mocker.patch.object(
        subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="not json", stderr=""),
    )

    with pytest.raises(KitaruCommandError) as error:
        run_kitaru(["session", "list"], server=SERVER)

    assert "kitaru session list" in str(error.value)


def test_a_missing_kitaru_binary_is_one_friendly_error(mocker):
    def explode(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError("kitaru")

    mocker.patch.object(subprocess, "run", explode)

    with pytest.raises(KitaruCommandError) as error:
        run_kitaru(["session", "list"], server=SERVER)

    assert "kitaru" in str(error.value)


# --- references + paging ---------------------------------------------------------------------------


def test_an_explicit_version_reference_is_passed_through_without_a_lookup(mocker):
    fake = install(mocker)

    assert resolve_ref("agent", "decode@2", server=SERVER) == "decode@2"
    assert fake.calls == []


def test_a_bare_name_resolves_to_the_latest_registered_version(mocker):
    fake = install(mocker, {"ok": True, "item": {"name": "opik", "latest_version": 3}})

    assert resolve_ref("importer", "opik", server=SERVER) == "opik@3"
    assert fake.calls[0][:3] == ["kitaru", "importer", "get"]


def test_a_name_with_no_version_yet_is_refused_with_one_line(mocker):
    install(mocker, {"ok": True, "item": {"name": "opik", "latest_version": 0}})

    with pytest.raises(KitaruCommandError) as error:
        resolve_ref("importer", "opik", server=SERVER)

    assert "opik" in str(error.value)


def test_sessions_are_paged_to_exhaustion_so_a_cohort_is_never_short(mocker):
    """`session list` caps a page at 1000; a truncated read means a short cohort, silently."""
    fake = install(
        mocker,
        {"ok": True, "items": [{"id": "s1"}], "page": {"next_cursor": "cur-2"}},
        {"ok": True, "items": [{"id": "s2"}], "page": {"next_cursor": None}},
    )

    sessions = list_sessions(agent="decode", server=SERVER)

    assert [s["id"] for s in sessions] == ["s1", "s2"]
    assert fake.calls[1][fake.calls[1].index("--cursor") + 1] == "cur-2"
    assert fake.calls[0][fake.calls[0].index("--agent") + 1] == "decode"


def test_extra_filters_ride_onto_the_session_query(mocker):
    fake = install(mocker, {"ok": True, "items": [], "page": {"next_cursor": None}})

    list_sessions(extra=["--tag", "regression-case"], server=SERVER)

    assert "--tag" in fake.calls[0]
    assert fake.calls[0][fake.calls[0].index("--tag") + 1] == "regression-case"
