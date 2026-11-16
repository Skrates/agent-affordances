"""What a dispatch decides, exercised through a real child process (the fake agy)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from gemini_dispatch.dispatch import DispatchError, run, strip_fence
from gemini_dispatch.models import Mode, Outcome, Request

from .conftest import FakeAgy

Success = Callable[..., dict[str, object]]


def test_reply_and_log_come_back_from_a_successful_turn(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    agy.plan(success("hello"))
    dispatch = run(Request(cwd=job, prompt="say hello", label="greet"))

    assert dispatch.outcome is Outcome.ok
    assert dispatch.reply == "hello"
    assert agy.cwd == str(job.resolve())
    saved = json.loads(dispatch.log.read_text())
    assert saved["prompt"] == "say hello"
    assert saved["result"]["response"] == "hello"
    # The prompt is stored once, under its own key; the argv copy carries a placeholder.
    assert saved["argv"][1:5] == ["-p", "<prompt>", "--output-format", "json"]


def test_an_empty_reply_is_a_failure_even_when_agy_says_success(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    denied = [{"action": "command", "display_name": "Bash"}]
    agy.plan(success("", denied_actions=denied))
    dispatch = run(Request(cwd=job, prompt="judge the image"))

    assert dispatch.outcome is Outcome.empty_reply
    assert "Bash (command)" in dispatch.detail


def test_an_empty_reply_is_accepted_when_the_caller_asked_only_for_a_file(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    agy.plan(success(""), write={"out.png": "pixels"})
    request = Request(cwd=job, prompt="draw", expect=["out.png"], allow_empty=True)

    assert run(request).outcome is Outcome.ok


def test_a_claimed_file_that_was_not_written_fails(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    agy.plan(success("Saved out.png as requested."))
    dispatch = run(Request(cwd=job, prompt="draw", expect=["out.png"]))

    assert dispatch.outcome is Outcome.missing_file
    assert "out.png" in dispatch.detail


def test_a_file_left_from_an_earlier_run_does_not_count(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    stale = job / "out.png"
    stale.write_text("old pixels")
    os.utime(stale, (1_000_000_000, 1_000_000_000))
    agy.plan(success("Saved out.png."))

    assert run(Request(cwd=job, prompt="draw", expect=["out.png"])).outcome is Outcome.missing_file


def test_a_non_success_status_fails(agy: FakeAgy, job: Path) -> None:
    agy.plan({"status": "ERROR", "response": "quota exceeded"})
    dispatch = run(Request(cwd=job, prompt="anything"))

    assert dispatch.outcome is Outcome.agy_failed
    assert dispatch.reply == "quota exceeded"


def test_agys_own_time_limit_is_reported_as_a_timeout(agy: FakeAgy, job: Path) -> None:
    # What agy 1.5.9 prints when --print-timeout expires: an ERROR result and exit status 3.
    limit: dict[str, object] = {
        "status": "ERROR",
        "response": "",
        "finished_reason": "print_timeout",
        "error": "print timeout after 90s with the turn still in progress",
    }
    agy.plan(limit, exit=3)
    dispatch = run(Request(cwd=job, prompt="anything", timeout=90))

    assert dispatch.outcome is Outcome.timeout
    assert "90s" in dispatch.detail


def test_agys_error_text_is_in_the_reason(agy: FakeAgy, job: Path) -> None:
    agy.plan({"status": "ERROR", "response": "", "error": "quota exhausted"})

    assert (
        run(Request(cwd=job, prompt="anything")).detail
        == "agy reported status ERROR: quota exhausted"
    )


def test_unparseable_output_fails_and_is_kept_in_the_log(agy: FakeAgy, job: Path) -> None:
    agy.plan(stdout="Error: not signed in\n", stderr="run agy to log in\n", exit=1)
    dispatch = run(Request(cwd=job, prompt="anything"))

    assert dispatch.outcome is Outcome.agy_failed
    saved = json.loads(dispatch.log.read_text())
    assert saved["stdout"] == "Error: not signed in\n"
    assert saved["stderr"] == "run agy to log in\n"
    assert saved["returncode"] == 1


def test_notices_around_the_result_object_are_tolerated(
    agy: FakeAgy, job: Path, success: Success
) -> None:
    agy.plan(stdout="warning: {auto} mode unavailable\n" + json.dumps(success("fine")) + "\n")

    assert run(Request(cwd=job, prompt="anything")).reply == "fine"


def test_a_hung_agy_is_killed_and_logged(agy: FakeAgy, job: Path) -> None:
    agy.plan(sleep=30)
    dispatch = run(Request(cwd=job, prompt="anything", timeout=1), grace=0)

    assert dispatch.outcome is Outcome.timeout
    assert dispatch.seconds < 10
    assert json.loads(dispatch.log.read_text())["outcome"] == "timeout"


def test_a_timeout_also_ends_the_helpers_agy_started(agy: FakeAgy, job: Path) -> None:
    agy.plan(sleep=30, helper_sleep=30)
    dispatch = run(Request(cwd=job, prompt="anything", timeout=1), grace=0)

    assert dispatch.outcome is Outcome.timeout
    assert dispatch.seconds < 10


def test_an_expected_file_outside_the_working_directory_is_refused(agy: FakeAgy, job: Path) -> None:
    with pytest.raises(DispatchError, match="outside the working directory"):
        run(Request(cwd=job, prompt="draw", expect=["../elsewhere.png"]))
    assert not agy.called


def test_an_outside_image_is_staged_so_its_folder_is_never_opened(
    agy: FakeAgy, job: Path, tmp_path: Path, success: Success
) -> None:
    private = tmp_path / "downloads"
    private.mkdir()
    (private / "key.png").write_bytes(b"png")
    (job / "plate.png").write_bytes(b"png")
    agy.plan(success())
    run(Request(cwd=job, prompt="compare", images=[private / "key.png", job / "plate.png"]))

    prompt = agy.argv[1]
    assert "- Image 1: .gemini-dispatch/inputs/01-key.png" in prompt
    assert "- Image 2: plate.png" in prompt
    assert (job / ".gemini-dispatch/inputs/01-key.png").read_bytes() == b"png"
    assert str(tmp_path) not in " ".join(agy.argv)
    assert agy.flag("--add-dir") == []


def test_only_requested_directories_and_flags_reach_agy(
    agy: FakeAgy, job: Path, tmp_path: Path, success: Success
) -> None:
    guide = tmp_path / "guide"
    guide.mkdir()
    agy.plan(success())
    run(Request(cwd=job, prompt="p", add_dirs=[guide], model="m-1", mode=Mode.plan, timeout=90))

    assert agy.flag("--add-dir") == [str(guide.resolve())]
    assert agy.flag("--model") == ["m-1"]
    assert agy.flag("--mode") == ["plan"]
    assert agy.flag("--print-timeout") == ["90s"]
    assert "--dangerously-skip-permissions" not in agy.argv


def test_a_missing_agy_is_a_diagnostic_not_a_traceback(
    job: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(job))
    with pytest.raises(DispatchError, match="not found on PATH"):
        run(Request(cwd=job, prompt="anything"))


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("```\nthe prompt\n```", "the prompt"),
        ("```text\nline one\nline two\n```\n", "line one\nline two"),
        ("Here it is:\n```\ncode\n```", "Here it is:\n```\ncode\n```"),
        ("```\na\n```\nand\n```\nb\n```", "```\na\n```\nand\n```\nb\n```"),
        ("no fence at all", "no fence at all"),
    ],
)
def test_only_a_fence_around_the_whole_reply_is_removed(reply: str, expected: str) -> None:
    assert strip_fence(reply) == expected
