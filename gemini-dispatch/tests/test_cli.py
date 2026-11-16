"""The command line: where the reply goes, what stderr says, and the exit status."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from gemini_dispatch.cli import app

from .conftest import FakeAgy

Success = Callable[..., dict[str, object]]
runner = CliRunner()


def test_reply_on_stdout_and_status_on_stderr(agy: FakeAgy, job: Path, success: Success) -> None:
    agy.plan(success("the answer"), stderr="auto mode unavailable, using default\n")
    result = runner.invoke(app, ["--cwd", str(job), "--prompt", "q"])

    assert result.exit_code == 0
    assert result.stdout == "the answer\n"
    assert "[gemini-dispatch] ok status=SUCCESS" in result.stderr
    assert "tokens=1234" in result.stderr
    assert "[agy] auto mode unavailable, using default" in result.stderr


def test_out_file_gets_the_unfenced_reply_and_stdout_stays_empty(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    agy.plan(success("```\n[Shot 1] a prompt\n```"))
    out = job / "prompt.txt"
    result = runner.invoke(
        app, ["--cwd", str(job), "--prompt", "q", "--out", str(out), "--strip-fence"]
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert out.read_text() == "[Shot 1] a prompt\n"


def test_out_file_is_left_alone_when_the_dispatch_fails(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    out = job / "prompt.txt"
    out.write_text("the last good prompt\n")
    agy.plan(success("", denied_actions=[{"action": "command", "display_name": "Bash"}]))
    result = runner.invoke(app, ["--cwd", str(job), "--prompt", "q", "--out", str(out)])

    assert result.exit_code == 4
    assert out.read_text() == "the last good prompt\n"
    assert "DENIED (not run): Bash (command)" in result.stderr


def test_exit_statuses_tell_the_failures_apart(agy: FakeAgy, job: Path, success: Success) -> None:
    base = ["--cwd", str(job), "--prompt", "q"]

    agy.plan({"status": "ERROR", "response": ""})
    assert runner.invoke(app, base).exit_code == 1
    agy.plan(success("done"))
    assert runner.invoke(app, [*base, "--expect", "out.png"]).exit_code == 5
    assert runner.invoke(app, ["--cwd", str(job / "nowhere"), "--prompt", "q"]).exit_code == 2
    assert runner.invoke(app, [*base, "--prompt-file", str(job / "none.txt")]).exit_code == 2


def test_a_bad_out_folder_is_caught_before_the_dispatch_is_spent(agy: FakeAgy, job: Path) -> None:
    out = job / "missing" / "prompt.txt"
    result = runner.invoke(app, ["--cwd", str(job), "--prompt", "q", "--out", str(out)])

    assert result.exit_code == 2
    assert not agy.called


def test_prompt_is_read_from_a_file_or_stdin(agy: FakeAgy, job: Path, success: Success) -> None:
    brief = job / "brief.txt"
    brief.write_text("from the file")
    agy.plan(success())

    runner.invoke(app, ["--cwd", str(job), "--prompt-file", str(brief)])
    assert agy.argv[1] == "from the file"
    runner.invoke(app, ["--cwd", str(job)], input="from stdin")
    assert agy.argv[1] == "from stdin"


def test_an_empty_prompt_never_reaches_agy(agy: FakeAgy, job: Path) -> None:
    result = runner.invoke(app, ["--cwd", str(job), "--prompt", "   "])

    assert result.exit_code == 2
    assert not agy.called
