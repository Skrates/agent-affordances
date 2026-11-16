"""Run one task through `agy -p` and decide whether it actually succeeded.

agy reports `status: SUCCESS` for a turn that ended, which is not the same as a turn that did
the work: a denied tool call can leave the reply empty, and a file Gemini was told to write may
not exist. `run` checks both, so exit 0 means there is a reply (and the expected files) to read.

Nothing outside the working directory is opened to Gemini unless the caller passes `--add-dir`:
images are staged into the working directory and named by relative path, so neither the image's
folder nor the caller's home path reaches the prompt.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .models import AgyResult, Dispatch, Outcome, Request

#: Everything this tool writes lives here, inside the dispatch's working directory.
STATE_DIR = ".gemini-dispatch"
#: Extra seconds the process may run past agy's own `--print-timeout` before it is killed.
GRACE_SECONDS = 60

_FENCE = re.compile(r"\A\s*```[\w+-]*[ \t]*\n(?P<body>.*?)\n[ \t]*```\s*\Z", re.DOTALL)
_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


class DispatchError(Exception):
    """The dispatch could not be started; the message is the diagnostic."""


def strip_fence(text: str) -> str:
    """Remove one Markdown code fence when it wraps the whole reply, and only then."""
    match = _FENCE.match(text)
    if match is None or "```" in match.group("body"):
        return text
    return match.group("body")


def stage_images(cwd: Path, images: list[Path]) -> list[str]:
    """Make every image readable inside `cwd`; return the paths to name in the prompt.

    An image already under `cwd` is named where it is. Any other is copied to
    `.gemini-dispatch/inputs/NN-<name>`, so its own folder never joins the workspace.
    """
    refs: list[str] = []
    for number, image in enumerate(images, 1):
        source = image.resolve()
        if source.is_relative_to(cwd):
            refs.append(source.relative_to(cwd).as_posix())
            continue
        inputs = cwd / STATE_DIR / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        staged = inputs / f"{number:02d}-{source.name}"
        shutil.copyfile(source, staged)
        refs.append(staged.relative_to(cwd).as_posix())
    return refs


def compose_prompt(prompt: str, image_refs: list[str]) -> str:
    if not image_refs:
        return prompt
    listing = "\n".join(f"- Image {n}: {ref}" for n, ref in enumerate(image_refs, 1))
    return (
        "Open and look at these image files before answering "
        "(paths are relative to the current directory):\n" + listing + "\n\n" + prompt
    )


def build_argv(agy: str, request: Request, prompt: str) -> list[str]:
    argv = [agy, "-p", prompt, "--output-format", "json", "--mode", request.mode.value]
    argv += ["--print-timeout", f"{request.timeout}s"]
    if request.model:
        argv += ["--model", request.model]
    if request.effort:
        argv += ["--effort", request.effort.value]
    for directory in request.add_dirs:
        argv += ["--add-dir", str(directory.resolve())]
    if request.skip_permissions:
        argv.append("--dangerously-skip-permissions")
    return argv


def parse_result(stdout: str) -> AgyResult | None:
    """The result object in agy's output, tolerating notices printed around it."""
    decoder = json.JSONDecoder()
    start = stdout.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(stdout, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and ("status" in value or "response" in value):
            return AgyResult.model_validate(value)
        start = stdout.find("{", start + 1)
    return None


def _missing_files(cwd: Path, expect: list[str], started: float) -> list[str]:
    """Expected files that are absent, empty, or left over from before this dispatch."""
    missing: list[str] = []
    for name in expect:
        path = cwd / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"{name} (not written)")
        elif path.stat().st_mtime < started - 1:
            missing.append(f"{name} (older than this dispatch)")
    return missing


def _judge(
    request: Request, result: AgyResult | None, returncode: int, started: float
) -> tuple[Outcome, str]:
    if result is None:
        return Outcome.agy_failed, f"agy exited {returncode} without a result object"
    if result.finished_reason == "print_timeout":
        return Outcome.timeout, f"agy's own time limit of {request.timeout}s ran out mid-turn"
    if result.status != "SUCCESS":
        reason = f": {result.error}" if result.error else ""
        return Outcome.agy_failed, f"agy reported status {result.status}{reason}"
    missing = _missing_files(request.cwd, request.expect, started)
    if missing:
        return Outcome.missing_file, "expected file missing: " + ", ".join(missing)
    if not result.response.strip() and not request.allow_empty:
        denied = ", ".join(action.label() for action in result.denied_actions)
        cause = f" after a denied action: {denied}" if denied else ""
        return Outcome.empty_reply, f"Gemini returned no text{cause}"
    return Outcome.ok, ""


def _log_path(cwd: Path, label: str) -> Path:
    directory = cwd / STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{_UNSAFE_LABEL.sub('-', label).strip('-') or 'task'}"
    path = directory / f"{stem}.json"
    serial = 2
    while path.exists():
        path = directory / f"{stem}-{serial}.json"
        serial += 1
    return path


def _text(captured: str | bytes | None) -> str:
    if captured is None:
        return ""
    return captured if isinstance(captured, str) else captured.decode("utf-8", "replace")


def _kill_group(process: subprocess.Popen[str]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def _run_agy(argv: list[str], cwd: Path, limit: int) -> tuple[str, str, int]:
    """Run agy in its own process group, so a timeout or an interrupt takes its helpers with it.

    agy starts subagents. Killing only agy would leave them holding the output pipes, and the
    read that follows the kill would wait on them for as long as they live.
    """
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        too_long = error.errno == errno.E2BIG
        hint = " (the prompt is too long to pass as one argument)" if too_long else ""
        raise DispatchError(f"could not start agy: {error}{hint}") from error
    try:
        stdout, stderr = process.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(argv, limit, output=stdout, stderr=stderr) from None
    except BaseException:
        _kill_group(process)
        raise
    return stdout, stderr, process.returncode


def run(request: Request, *, agy: str = "agy", grace: int = GRACE_SECONDS) -> Dispatch:
    """Dispatch `request`, save the evidence, and return the verdict."""
    executable = shutil.which(agy)
    if executable is None:
        raise DispatchError(f"`{agy}` not found on PATH (the Antigravity CLI must be installed)")
    cwd = request.cwd.resolve()
    request = request.model_copy(update={"cwd": cwd})
    for name in request.expect:
        if not (cwd / name).resolve().is_relative_to(cwd):
            raise DispatchError(f"--expect {name!r} is outside the working directory")
    prompt = compose_prompt(request.prompt, stage_images(cwd, request.images))
    argv = build_argv(executable, request, prompt)
    log = _log_path(cwd, request.label)

    started = time.time()
    timed_out = False
    returncode = -1
    try:
        stdout, stderr, returncode = _run_agy(argv, cwd, request.timeout + grace)
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        stdout, stderr = _text(expired.stdout), _text(expired.stderr)
    seconds = time.time() - started

    result = None if timed_out else parse_result(stdout)
    if timed_out:
        outcome, detail = Outcome.timeout, f"no result after {request.timeout + grace}s; agy killed"
    else:
        outcome, detail = _judge(request, result, returncode, started)

    record = {
        "argv": [*argv[:2], "<prompt>", *argv[3:]],
        "prompt": prompt,
        "outcome": outcome.value,
        "detail": detail,
        "seconds": round(seconds, 1),
        "returncode": None if timed_out else returncode,
        "result": result.model_dump(mode="json") if result else None,
        "stdout": "" if result else stdout,
        "stderr": stderr,
    }
    log.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return Dispatch(
        outcome=outcome,
        detail=detail,
        reply=result.response if result else "",
        result=result,
        agy_stderr=stderr,
        seconds=seconds,
        log=log,
    )
