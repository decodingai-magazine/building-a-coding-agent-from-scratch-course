from click.testing import CliRunner

from decode.cli import cli


def test_cli_runs_and_exits_zero():
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    assert "decode" in result.output


def test_cli_accepts_resume_flag():
    result = CliRunner().invoke(cli, ["--resume"])
    assert result.exit_code == 0


def test_cli_accepts_named_resume():
    result = CliRunner().invoke(cli, ["--resume", "2026-06-19_abc"])
    assert result.exit_code == 0
