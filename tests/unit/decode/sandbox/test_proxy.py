"""Hermetic unit tests for the sandbox Credential Proxy host-side pieces (``decode.sandbox.proxy``).

These need **no docker daemon and no secret store**: since ADR-0015 §6 the template resolver
(:func:`build_credential_map`) is a **pure function of the hydrated** ``Settings`` — a
``{{ field_name }}`` template names a Settings field, so a test just monkeypatches that field (no
kitaru, no network, no store stubs). The :class:`DockerCredentialProxy` assertions cover only its pure
properties (naming, the worker proxy env, the pre-start guard). The real container topology — a live
mitmproxy container, the CA mount, the header injection, the credential boundary — lives in the
``@skipif``-guarded ``tests/integration/test_credential_proxy.py`` (it needs a real daemon and SKIPs
cleanly without one).
"""

from __future__ import annotations

import base64
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from decode.config.settings import settings
from decode.sandbox.proxy import (
    _GH_PLACEHOLDER_TOKEN,
    DEFAULT_PROXY_RULES,
    DockerCredentialProxy,
    SandboxProxyRule,
    build_credential_map,
    github_token_rules,
)

# The resolved secret value the tests inject — the string that must NEVER appear in a log line.
_SECRET_VALUE = "ghp_super_secret_token_value"


# SandboxProxyRule shape


def test_sandbox_proxy_rule_holds_name_hosts_and_headers():
    rule = SandboxProxyRule(
        name="github-auth",
        hosts=["api.github.com"],
        headers={"Authorization": "Bearer {{ sandbox_git_token }}"},
    )

    assert rule.name == "github-auth"
    assert rule.hosts == ["api.github.com"]
    assert rule.headers == {"Authorization": "Bearer {{ sandbox_git_token }}"}


def test_sandbox_proxy_rule_is_frozen():
    rule = SandboxProxyRule(name="r", hosts=["h"], headers={})

    # Frozen: a rule is an immutable declaration (a stray reassignment is a bug caught here).
    with pytest.raises(AttributeError):
        rule.name = "other"  # type: ignore[misc]


def test_default_proxy_rules_ships_empty():
    # Opt-in: the shipped default is an empty list → an empty credential map → a passthrough proxy.
    assert DEFAULT_PROXY_RULES == []


# build_credential_map: host-side resolution from the hydrated Settings (ADR-0015 §6)


def test_build_credential_map_resolves_a_secretstr_field_into_the_host_header_value(monkeypatch):
    # AC: a ``{{ field_name }}`` template resolves from the hydrated Settings — the SecretStr is
    # unwrapped (``.get_secret_value()``), not str()'d into "**********".
    monkeypatch.setattr(settings, "sandbox_git_token", SecretStr(_SECRET_VALUE))
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ sandbox_git_token }}"},
        )
    ]

    result = build_credential_map(rules)

    assert result == {"api.github.com": {"Authorization": f"Bearer {_SECRET_VALUE}"}}


def test_build_credential_map_stringifies_a_plain_field(monkeypatch):
    # Not every proxied header is a secret: a plain (non-SecretStr) field resolves by str().
    monkeypatch.setattr(settings, "opik_workspace", "acme")
    rules = [
        SandboxProxyRule(name="ws", hosts=["h.test"], headers={"X-Ws": "{{ opik_workspace }}"})
    ]

    assert build_credential_map(rules) == {"h.test": {"X-Ws": "acme"}}


def test_build_credential_map_applies_one_rule_to_each_of_its_hosts(monkeypatch):
    monkeypatch.setattr(settings, "opik_workspace", "V")
    rules = [
        SandboxProxyRule(
            name="multi", hosts=["a.test", "b.test"], headers={"X-Auth": "{{ opik_workspace }}"}
        )
    ]

    result = build_credential_map(rules)

    assert result == {"a.test": {"X-Auth": "V"}, "b.test": {"X-Auth": "V"}}


def test_build_credential_map_merges_multiple_rules_on_the_same_host(monkeypatch):
    monkeypatch.setattr(settings, "opik_workspace", "V")
    rules = [
        SandboxProxyRule(name="one", hosts=["h.test"], headers={"A": "{{ opik_workspace }}"}),
        SandboxProxyRule(name="two", hosts=["h.test"], headers={"B": "static"}),
    ]

    result = build_credential_map(rules)

    assert result == {"h.test": {"A": "V", "B": "static"}}


def test_build_credential_map_leaves_a_template_free_value_untouched():
    rules = [SandboxProxyRule(name="static", hosts=["h.test"], headers={"X-Fixed": "plain-value"})]

    assert build_credential_map(rules) == {"h.test": {"X-Fixed": "plain-value"}}


def test_build_credential_map_resolves_several_templates_in_one_rule(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_git_token", SecretStr(_SECRET_VALUE))
    monkeypatch.setattr(settings, "opik_workspace", "acme")
    rules = [
        SandboxProxyRule(
            name="two-headers",
            hosts=["h.test"],
            headers={"H1": "Bearer {{ sandbox_git_token }}", "H2": "{{ opik_workspace }}"},
        )
    ]

    assert build_credential_map(rules) == {
        "h.test": {"H1": f"Bearer {_SECRET_VALUE}", "H2": "acme"}
    }


def test_build_credential_map_empty_rules_yields_an_empty_map():
    # Opt-in: no rules → an empty map → a passthrough proxy (it injects nothing).
    assert build_credential_map([]) == {}


def test_build_credential_map_rejects_an_unknown_settings_field():
    # AC (credential boundary): an unknown field must fail LOUDLY, naming it — never a silent skip
    # that would send an unauthenticated request while looking fine.
    rules = [SandboxProxyRule(name="r", hosts=["h"], headers={"A": "{{ nonexistent_field }}"})]

    with pytest.raises(ValueError, match="nonexistent_field"):
        build_credential_map(rules)


def test_build_credential_map_rejects_an_attribute_that_is_not_a_settings_field():
    # Only real Settings FIELDS resolve: a stray attribute (``model_config``, a method) is a typo,
    # not a credential — it must fail rather than inject a repr into a header.
    rules = [SandboxProxyRule(name="r", hosts=["h"], headers={"A": "{{ model_config }}"})]

    with pytest.raises(ValueError, match="model_config"):
        build_credential_map(rules)


def test_build_credential_map_rejects_an_unset_field(monkeypatch):
    # AC: a None value raises rather than injecting a header with nothing in it (the mirror of the
    # old missing-secret contract — an empty ``Bearer`` is worse than a loud failure).
    monkeypatch.setattr(settings, "sandbox_git_token", None)
    rules = [
        SandboxProxyRule(name="r", hosts=["h"], headers={"A": "Bearer {{ sandbox_git_token }}"})
    ]

    with pytest.raises(ValueError, match="sandbox_git_token"):
        build_credential_map(rules)


def test_build_credential_map_rejects_an_empty_field_value(monkeypatch):
    # An explicit ``SANDBOX_GIT_TOKEN=`` parses to ``SecretStr("")`` — empty, so it must raise too.
    monkeypatch.setattr(settings, "sandbox_git_token", SecretStr(""))
    rules = [
        SandboxProxyRule(name="r", hosts=["h"], headers={"A": "Bearer {{ sandbox_git_token }}"})
    ]

    with pytest.raises(ValueError, match="sandbox_git_token"):
        build_credential_map(rules)


def test_build_credential_map_logs_names_never_values(monkeypatch, caplog):
    # SECURITY (task-061 discipline): the resolved VALUE must never reach a log line — only rule /
    # host / header NAMES, so an operator can correlate an injection without leaking the secret.
    monkeypatch.setattr(settings, "sandbox_git_token", SecretStr(_SECRET_VALUE))
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ sandbox_git_token }}"},
        )
    ]

    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.proxy"):
        build_credential_map(rules)

    assert _SECRET_VALUE not in caplog.text  # the resolved value never appears
    assert "github-auth" in caplog.text  # the rule name does (for correlation)
    assert "Authorization" in caplog.text  # the header name does


def test_the_proxy_module_resolves_a_template_without_ever_importing_kitaru():
    """The seam (ADR-0015 §6): proxy rules read ``Settings``, so kitaru's ONLY ``get_secret`` seam is
    the Environment-Bucket settings source.

    Run in a clean subprocess (like the ``local``-never-imports-kitaru invariant in
    ``tests/unit/decode/config/test_env_bucket.py``) so the assertion is independent of what the rest
    of the suite already imported: import the proxy module, resolve a templated rule for real, and
    prove ``kitaru`` never landed in ``sys.modules``.
    """
    code = (
        "import sys\n"
        "from pydantic import SecretStr\n"
        "from decode.config.settings import settings\n"
        "from decode.sandbox.proxy import SandboxProxyRule, build_credential_map\n"
        "settings.sandbox_git_token = SecretStr('tok')\n"
        "rule = SandboxProxyRule(name='r', hosts=['h'], "
        "headers={'Authorization': 'Bearer {{ sandbox_git_token }}'})\n"
        "assert build_credential_map([rule]) == {'h': {'Authorization': 'Bearer tok'}}\n"
        "leaked = sorted(m for m in sys.modules if m == 'kitaru' or m.startswith('kitaru.'))\n"
        "assert not leaked, leaked\n"
        "print('NO_KITARU_OK')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120.0, check=False
    )

    assert result.returncode == 0, result.stderr
    assert "NO_KITARU_OK" in result.stdout


# github_token_rules: the SANDBOX_GIT_TOKEN one-knob shortcut


def test_github_token_rules_builds_bearer_api_then_basic_git():
    # api.github.com FIRST (Bearer for the REST/PR API), github.com SECOND (Basic for git-over-HTTPS).
    rules = github_token_rules("ghp_abc123")

    assert [r.name for r in rules] == ["github-api", "github-git"]
    assert rules[0].hosts == ["api.github.com"]
    assert rules[0].headers == {"Authorization": "Bearer ghp_abc123"}
    assert rules[1].hosts == ["github.com"]
    # git transport wants Basic base64("x-access-token:<PAT>"), NOT Bearer.
    expected_basic = base64.b64encode(b"x-access-token:ghp_abc123").decode()
    assert rules[1].headers == {"Authorization": f"Basic {expected_basic}"}


def test_github_token_rules_resolve_untouched_with_the_api_host_first():
    # Fed through build_credential_map the literal values pass through untouched (no {{ }} → no field
    # lookup at all), and api.github.com is the FIRST map key so _match_host picks Bearer for the API
    # host (github.com parent-matches api.github.com; _match_host returns the first match).
    result = build_credential_map(github_token_rules("ghp_x"))

    assert list(result) == ["api.github.com", "github.com"]  # insertion order == match precedence
    assert result["api.github.com"] == {"Authorization": "Bearer ghp_x"}


# DockerCredentialProxy: pure properties (no docker)


def test_worker_proxy_env_points_http_and_https_at_the_proxy_container():
    proxy = DockerCredentialProxy(credential_map={})

    env = proxy.worker_proxy_env

    url = f"http://{proxy._container_name}:8080"
    assert env["http_proxy"] == url
    assert env["https_proxy"] == url
    assert env["HTTP_PROXY"] == url and env["HTTPS_PROXY"] == url
    assert env["no_proxy"] == "localhost,127.0.0.1"
    # The proxy env carries only the proxy URL + the gh decoy — never a resolved credential.
    assert not any(_SECRET_VALUE in v for v in env.values())


def test_worker_proxy_env_carries_a_decoy_gh_token_that_is_not_a_credential():
    """``gh`` gets a placeholder ``GH_TOKEN``, and it authenticates nothing (ADR-0012 §10).

    ``gh`` refuses to issue ANY request when it finds no token in the env — it fails locally with
    "gh auth login" and never reaches the proxy that would have authenticated it. So the worker is
    handed a decoy: gh proceeds, sends ``Authorization: token <decoy>``, and the addon overwrites that
    header with the real credential after the request has left the worker. The invariant is unchanged —
    this string is not a secret, and a real credential must never appear here.
    """
    proxy = DockerCredentialProxy(
        credential_map={"api.github.com": {"Authorization": f"Bearer {_SECRET_VALUE}"}}
    )

    env = proxy.worker_proxy_env

    assert env["GH_TOKEN"] == _GH_PLACEHOLDER_TOKEN
    # The decoy is inert: it is not the real credential, and it carries no PAT shape.
    assert _SECRET_VALUE not in env["GH_TOKEN"]
    assert not env["GH_TOKEN"].startswith(("ghp_", "github_pat_"))
    # Even with a resolved map in hand, NOTHING in the worker's env is the credential.
    assert not any(_SECRET_VALUE in value for value in env.values())


def test_ca_cert_host_path_raises_before_start():
    proxy = DockerCredentialProxy(credential_map={})

    # The CA does not exist until start() runs mitmdump — asking for it early is a programming error.
    with pytest.raises(RuntimeError):
        _ = proxy.ca_cert_host_path


def test_each_proxy_instance_gets_unique_network_and_container_names():
    # Per-run unique names so two concurrent headless runs never collide on the network / container.
    a = DockerCredentialProxy(credential_map={})
    b = DockerCredentialProxy(credential_map={})

    assert a.network != b.network
    assert a._container_name != b._container_name


def test_run_proxy_container_keeps_the_secret_off_the_docker_argv(mocker, tmp_path):
    # SECURITY hardening: the resolved map rides a private ``--env-file`` (0600, deleted right after
    # docker run), NOT ``-e DECODE_CREDENTIAL_MAP=<json>`` — so the secret never appears on the
    # ``docker run`` argv (host ``ps``). Hermetic: ``_docker`` is faked (no daemon).
    proxy = DockerCredentialProxy({"api.github.com": {"Authorization": f"Bearer {_SECRET_VALUE}"}})
    proxy._cert_dir = tmp_path
    captured: dict[str, object] = {}

    def _fake_docker(*args: str) -> str:
        captured["argv"] = args
        env_file = args[args.index("--env-file") + 1]
        captured["env_file_content"] = Path(env_file).read_text(encoding="utf-8")
        return "container123\n"

    mocker.patch("decode.sandbox.proxy._docker", side_effect=_fake_docker)
    mocker.patch("decode.sandbox.proxy._proxy_image", return_value="mitmproxy/mitmproxy")

    cid = proxy._run_proxy_container()

    assert cid == "container123"
    argv = captured["argv"]
    assert isinstance(argv, tuple)
    # The map is handed via --env-file, never `-e DECODE_CREDENTIAL_MAP=...`.
    assert "--env-file" in argv
    assert not any(str(a).startswith("DECODE_CREDENTIAL_MAP=") for a in argv)
    # The resolved secret value is NOWHERE in the docker argv (host `ps` stays clean).
    assert not any(_SECRET_VALUE in str(a) for a in argv)
    # It IS delivered to the proxy via the env-file (compact JSON) ...
    assert "DECODE_CREDENTIAL_MAP=" in str(captured["env_file_content"])
    assert _SECRET_VALUE in str(captured["env_file_content"])
    # ... and that env-file is deleted the moment docker run consumed it (minimal on-disk lifetime).
    env_file_path = argv[argv.index("--env-file") + 1]
    assert not Path(env_file_path).exists()
