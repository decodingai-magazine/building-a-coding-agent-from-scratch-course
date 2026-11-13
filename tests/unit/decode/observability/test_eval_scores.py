from __future__ import annotations

import pytest
from pydantic import SecretStr

from decode.config.settings import settings
from decode.observability.eval_scores import EvalScore, report_eval_scores


def test_score_reporting_skips_when_langfuse_is_not_configured(monkeypatch, mocker):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr(""), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr(""), raising=False)
    langfuse = mocker.patch("langfuse.Langfuse")

    assert report_eval_scores([EvalScore("session-1", "reward", 1.0)]) is None

    langfuse.assert_not_called()


@pytest.mark.parametrize(
    ("secret_key", "public_key"),
    [("secret", ""), ("", "public")],
)
def test_score_reporting_rejects_incomplete_credentials(monkeypatch, secret_key, public_key):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr(secret_key), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr(public_key), raising=False)

    with pytest.raises(ValueError, match="requires both Langfuse API keys"):
        report_eval_scores([EvalScore("session-1", "reward", 1.0)])


def test_score_reporting_sends_session_scores_and_shuts_down(monkeypatch, mocker):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("secret"), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr("public"), raising=False)
    monkeypatch.setattr(
        settings, "langfuse_base_url", "https://us.cloud.langfuse.com", raising=False
    )
    client_cls = mocker.patch("langfuse.Langfuse")
    score = EvalScore(
        session_id="session-1",
        name="benchmark_reward",
        value=0.5,
        comment="one trial",
        metadata={"task_id": "task-1"},
    )

    assert report_eval_scores([score]) == 1

    client_cls.assert_called_once_with(
        public_key="public",
        secret_key="secret",
        host="https://us.cloud.langfuse.com",
        environment=settings.decode_env,
        tracing_enabled=False,
    )
    client = client_cls.return_value
    client.create_score.assert_called_once_with(
        name="benchmark_reward",
        value=0.5,
        session_id="session-1",
        data_type="NUMERIC",
        comment="one trial",
        metadata={"task_id": "task-1"},
        environment=settings.decode_env,
    )
    client.flush.assert_called_once_with()
    client.shutdown.assert_called_once_with()


def test_empty_score_batch_does_not_create_a_client(monkeypatch, mocker):
    monkeypatch.setattr(settings, "langfuse_secret_key", SecretStr("secret"), raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", SecretStr("public"), raising=False)
    client = mocker.patch("langfuse.Langfuse")

    assert report_eval_scores([]) == 0

    client.assert_not_called()
