"""Run one task through `agy -p` and decide whether it actually succeeded.

agy reports `status: SUCCESS` for a turn that ended, which is not the same as a turn that did
the work: a denied tool call can leave the reply empty, and a file Gemini was told to write may
not exist. `run` checks both, so exit 0 means there is a reply (and the expected files) to read.

Dispatches may share a working directory and run at the same time. Each one claims its log file
by exclusive creation before agy starts, and stages its images in a folder named after that log.

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

#: An opening code-fence line: three or more backticks, then an optional info string.
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<ticks>`{3,})[^`]*$")
_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


class DispatchError(Exception):
    """The dispatch could not be started; the message is the diagnostic."""


def strip_fence(text: str) -> str:
    """Remove one Markdown code fence when it wraps the whole reply, and only then.

    Read line by line, not by pattern over the whole text: the first line opens the fence, the
    last line closes it with at least as many backticks, and whatever lies between is the body,
    including nothing at all. A reply with any other fence inside it is left as it is.
    """
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return text
    opening = _FENCE_OPEN.match(lines[0])
    closing = lines[-1].strip()
    if opening is None or set(closing) != {"`"} or len(closing) < len(opening.group("ticks")):
        return text
    body = "\n".join(lines[1:-1])
    return text if "```" in body else body


def stage_images(cwd: Path, images: list[Path], inputs: Path) -> list[str]:
    """Make every image readable inside `cwd`; return the paths to name in the prompt.

    An image already under `cwd` is named where it is. Any other is copied into `inputs`, a
    folder that belongs to this dispatch alone, so its own folder never joins the workspace
    and a second dispatch in the same directory cannot replace it mid-read.
    """
    refs: list[str] = []
    for number, image in enumerate(images, 1):
        source = image.resolve()
        if source.is_relative_to(cwd):
            refs.append(source.relative_to(cwd).as_posix())
            continue
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


#: What identifies one version of a file: a write changes at least one of these.
Fingerprint = tuple[int, int, int]


def _fingerprints(cwd: Path, expect: list[str]) -> dict[str, Fingerprint | None]:
    """The state of each expected file right now; None for one that is not there."""
    state: dict[str, Fingerprint | None] = {}
    for name in expect:
        try:
            stat = (cwd / name).stat()
        except OSError:
            state[name] = None
        else:
            state[name] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return state


def _missing_files(cwd: Path, before: dict[str, Fingerprint | None]) -> list[str]:
    """Expected files that are absent, empty, or exactly as they were before the dispatch."""
    missing: list[str] = []
    for name, after in _fingerprints(cwd, list(before)).items():
        if after is None or after[1] == 0 or not (cwd / name).is_file():
            missing.append(f"{name} (not written)")
        elif after == before[name]:
            missing.append(f"{name} (unchanged by this dispatch)")
    return missing


def _judge(
    request: Request,
    result: AgyResult | None,
    reply: str,
    returncode: int,
    before: dict[str, Fingerprint | None],
) -> tuple[Outcome, str]:
    if result is None:
        return Outcome.agy_failed, f"agy exited {returncode} without a result object"
    if result.finished_reason == "print_timeout":
        return Outcome.timeout, f"agy's own time limit of {request.timeout}s ran out mid-turn"
    if result.status != "SUCCESS":
        reason = f": {result.error}" if result.error else ""
        return Outcome.agy_failed, f"agy reported status {result.status}{reason}"
    missing = _missing_files(request.cwd, before)
    if missing:
        return Outcome.missing_file, "expected file missing: " + ", ".join(missing)
    if not reply.strip() and not request.allow_empty:
        denied = ", ".join(action.label() for action in result.denied_actions)
        cause = f" after a denied action: {denied}" if denied else ""
        emptied = " once its code fence was removed" if result.response.strip() else ""
        return Outcome.empty_reply, f"Gemini returned no text{emptied}{cause}"
    return Outcome.ok, ""


def _reserve_log(cwd: Path, label: str) -> Path:
    """Create this dispatch's log file now, so no concurrent dispatch can take the same name.

    Exclusive creation is the reservation: two dispatches with one label in one second get
    two files. The stem also names the dispatch's input folder.
    """
    directory = cwd / STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{_UNSAFE_LABEL.sub('-', label).strip('-') or 'task'}"
    serial = 1
    while True:
        path = directory / (f"{stem}.json" if serial == 1 else f"{stem}-{serial}.json")
        try:
            path.open("x", encoding="utf-8").close()
        except FileExistsError:
            serial += 1
        else:
            return path


def _write_log(log: Path, record: dict[str, object]) -> None:
    log.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


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
    log = _reserve_log(cwd, request.label)
    inputs = cwd / STATE_DIR / "inputs" / log.stem
    prompt = compose_prompt(request.prompt, stage_images(cwd, request.images, inputs))
    argv = build_argv(executable, request, prompt)
    record: dict[str, object] = {
        "argv": [*argv[:2], "<prompt>", *argv[3:]],
        "prompt": prompt,
        "outcome": "running",
    }
    _write_log(log, record)
    before = _fingerprints(cwd, request.expect)

    started = time.time()
    timed_out = False
    returncode = -1
    try:
        stdout, stderr, returncode = _run_agy(argv, cwd, request.timeout + grace)
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        stdout, stderr = _text(expired.stdout), _text(expired.stderr)
    except DispatchError as error:
        _write_log(log, {**record, "outcome": Outcome.agy_failed.value, "detail": str(error)})
        raise
    seconds = time.time() - started

    result = None if timed_out else parse_result(stdout)
    reply = result.response if result else ""
    if request.strip_fence:
        reply = strip_fence(reply)
    if timed_out:
        outcome, detail = Outcome.timeout, f"no result after {request.timeout + grace}s; agy killed"
    else:
        outcome, detail = _judge(request, result, reply, returncode, before)

    record |= {
        "outcome": outcome.value,
        "detail": detail,
        "seconds": round(seconds, 1),
        "returncode": None if timed_out else returncode,
        "result": result.model_dump(mode="json") if result else None,
        "stdout": "" if result else stdout,
        "stderr": stderr,
    }
    _write_log(log, record)
    return Dispatch(
        outcome=outcome,
        detail=detail,
        reply=reply,
        result=result,
        agy_stderr=stderr,
        seconds=seconds,
        log=log,
    )
