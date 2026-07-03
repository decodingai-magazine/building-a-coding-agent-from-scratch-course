"""Hermetic unit tests for the sandbox Credential Proxy host-side pieces (``decode.sandbox.proxy``).

These need **no docker daemon**: the template resolver (:func:`build_credential_map`) is driven with a
**patched** ``kitaru.get_secret`` so it never touches a real secret store, and the
:class:`DockerCredentialProxy` assertions cover only its pure properties (naming, the worker proxy
env, the pre-start guard). The real container topology — a live mitmproxy container, the CA mount, the
header injection, the credential boundary — lives in the ``@skipif``-guarded
``tests/integration/test_credential_proxy.py`` (it needs a real daemon and SKIPs cleanly without one).
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from kitaru.errors import KitaruRuntimeError

from decode.sandbox.proxy import (
    DEFAULT_PROXY_RULES,
    DockerCredentialProxy,
    SandboxProxyRule,
    build_credential_map,
)

# The resolved secret value the tests inject — the string that must NEVER appear in a log line.
_SECRET_VALUE = "ghp_super_secret_token_value"


def _patch_get_secret(mocker, values_by_name: dict[str, dict[str, str]]):
    """Patch ``kitaru.get_secret`` to return a fake ``Secret`` (``.values``) per name; return the spy.

    ``build_credential_map`` does a lazy ``from kitaru import get_secret`` inside its resolver, so
    patching the attribute on the ``kitaru`` module is what the call actually resolves — no real secret
    store is touched.
    """

    def _fake(name: str):
        return SimpleNamespace(values=values_by_name[name])

    return mocker.patch("kitaru.get_secret", side_effect=_fake)


# --- SandboxProxyRule shape -------------------------------------------------------------------


def test_sandbox_proxy_rule_holds_name_hosts_and_headers():
    rule = SandboxProxyRule(
        name="github-auth",
        hosts=["api.github.com"],
        headers={"Authorization": "Bearer {{ github-token.value }}"},
    )

    assert rule.name == "github-auth"
    assert rule.hosts == ["api.github.com"]
    assert rule.headers == {"Authorization": "Bearer {{ github-token.value }}"}


def test_sandbox_proxy_rule_is_frozen():
    rule = SandboxProxyRule(name="r", hosts=["h"], headers={})

    # Frozen: a rule is an immutable declaration (a stray reassignment is a bug caught here).
    with pytest.raises(AttributeError):
        rule.name = "other"  # type: ignore[misc]


def test_default_proxy_rules_ships_empty():
    # Opt-in: the shipped default is an empty list → an empty credential map → a passthrough proxy.
    assert DEFAULT_PROXY_RULES == []


# --- build_credential_map: host-side template resolution --------------------------------------


def test_build_credential_map_resolves_a_template_into_host_header_value(mocker):
    _patch_get_secret(mocker, {"github-token": {"value": _SECRET_VALUE}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    result = build_credential_map(rules)

    assert result == {"api.github.com": {"Authorization": f"Bearer {_SECRET_VALUE}"}}


def test_build_credential_map_applies_one_rule_to_each_of_its_hosts(mocker):
    _patch_get_secret(mocker, {"tok": {"k": "V"}})
    rules = [
        SandboxProxyRule(
            name="multi", hosts=["a.test", "b.test"], headers={"X-Auth": "{{ tok.k }}"}
        )
    ]

    result = build_credential_map(rules)

    assert result == {"a.test": {"X-Auth": "V"}, "b.test": {"X-Auth": "V"}}


def test_build_credential_map_merges_multiple_rules_on_the_same_host(mocker):
    _patch_get_secret(mocker, {"s": {"k": "V"}})
    rules = [
        SandboxProxyRule(name="one", hosts=["h.test"], headers={"A": "{{ s.k }}"}),
        SandboxProxyRule(name="two", hosts=["h.test"], headers={"B": "static"}),
    ]

    result = build_credential_map(rules)

    assert result == {"h.test": {"A": "V", "B": "static"}}


def test_build_credential_map_leaves_a_template_free_value_untouched(mocker):
    spy = _patch_get_secret(mocker, {})
    rules = [SandboxProxyRule(name="static", hosts=["h.test"], headers={"X-Fixed": "plain-value"})]

    result = build_credential_map(rules)

    assert result == {"h.test": {"X-Fixed": "plain-value"}}
    spy.assert_not_called()  # no template → no secret fetch


def test_build_credential_map_caches_each_secret_across_headers(mocker):
    spy = _patch_get_secret(mocker, {"tok": {"a": "AA", "b": "BB"}})
    rules = [
        SandboxProxyRule(
            name="two-headers",
            hosts=["h.test"],
            headers={"H1": "{{ tok.a }}", "H2": "{{ tok.b }}"},
        )
    ]

    build_credential_map(rules)

    assert spy.call_count == 1  # the secret is fetched once and reused for both header templates


def test_build_credential_map_empty_rules_yields_an_empty_map(mocker):
    spy = _patch_get_secret(mocker, {})

    assert build_credential_map([]) == {}
    spy.assert_not_called()


def test_build_credential_map_propagates_a_missing_secret_error(mocker):
    # AC (credential boundary): a missing secret must surface Kitaru's OWN error, not be silently
    # skipped — the run fails loudly rather than sending an unauthenticated request.
    def _raise(name: str):
        raise KitaruRuntimeError(f"secret {name!r} not found")

    mocker.patch("kitaru.get_secret", side_effect=_raise)
    rules = [SandboxProxyRule(name="r", hosts=["h"], headers={"A": "{{ absent.key }}"})]

    with pytest.raises(KitaruRuntimeError):
        build_credential_map(rules)


def test_build_credential_map_propagates_a_missing_key_error(mocker):
    # A present secret that lacks the referenced key also fails loudly (KeyError), never a silent skip.
    _patch_get_secret(mocker, {"tok": {"present": "V"}})
    rules = [SandboxProxyRule(name="r", hosts=["h"], headers={"A": "{{ tok.absent }}"})]

    with pytest.raises(KeyError):
        build_credential_map(rules)


def test_build_credential_map_logs_names_never_values(mocker, caplog):
    # SECURITY (task-061 discipline): the resolved VALUE must never reach a log line — only rule /
    # host / header NAMES, so an operator can correlate an injection without leaking the secret.
    _patch_get_secret(mocker, {"github-token": {"value": _SECRET_VALUE}})
    rules = [
        SandboxProxyRule(
            name="github-auth",
            hosts=["api.github.com"],
            headers={"Authorization": "Bearer {{ github-token.value }}"},
        )
    ]

    with caplog.at_level(logging.DEBUG, logger="decode.sandbox.proxy"):
        build_credential_map(rules)

    assert _SECRET_VALUE not in caplog.text  # the resolved value never appears
    assert "github-auth" in caplog.text  # the rule name does (for correlation)
    assert "Authorization" in caplog.text  # the header name does


# --- DockerCredentialProxy: pure properties (no docker) ---------------------------------------


def test_worker_proxy_env_points_http_and_https_at_the_proxy_container():
    proxy = DockerCredentialProxy(credential_map={})

    env = proxy.worker_proxy_env

    url = f"http://{proxy._container_name}:8080"
    assert env["http_proxy"] == url
    assert env["https_proxy"] == url
    assert env["HTTP_PROXY"] == url and env["HTTPS_PROXY"] == url
    assert env["no_proxy"] == "localhost,127.0.0.1"
    # The proxy env carries only the proxy URL — never a resolved credential.
    assert not any(_SECRET_VALUE in v for v in env.values())


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
