"""gemini-dispatch: hand one self-contained task to Gemini through the Antigravity CLI.

    gemini-dispatch --cwd DIR [--image F]... [--out FILE] --prompt-file brief.txt

Runs `agy -p` in --cwd and prints Gemini's reply on stdout (or writes it to --out). One status
line, any denied actions and agy's own warnings go to stderr. The full exchange is saved under
DIR/.gemini-dispatch/. Exit 0 means there is a reply to read; see EXIT_CODES for the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from .dispatch import DispatchError, run
from .models import EXIT_CODES, Dispatch, Effort, Mode, Outcome, Request

# Plain help: this tool's reader is an agent, and Rich's boxes cost tokens for nothing.
app = typer.Typer(add_completion=False, rich_markup_mode=None)

#: agy's own warnings are worth seeing, a wall of them is not.
MAX_STDERR_LINES = 20


def _usage(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(2)


def _read_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt is not None and prompt_file is not None:
        raise _usage("pass --prompt or --prompt-file, not both")
    if prompt_file is not None:
        text = prompt_file.read_text(encoding="utf-8")
    elif prompt is not None:
        text = prompt
    elif sys.stdin.isatty():
        raise _usage("no prompt: pass --prompt, --prompt-file, or pipe it on stdin")
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise _usage("the prompt is empty")
    return text


def _report(dispatch: Dispatch) -> None:
    result = dispatch.result
    status = result.status if result else "none"
    tokens = result.usage.total_tokens if result else None
    typer.echo(
        f"[gemini-dispatch] {dispatch.outcome.value} status={status} "
        f"{dispatch.seconds:.0f}s tokens={tokens} log={dispatch.log}",
        err=True,
    )
    if dispatch.detail:
        typer.echo(f"[gemini-dispatch] {dispatch.detail}", err=True)
    for action in result.denied_actions if result else []:
        typer.echo(f"[gemini-dispatch] DENIED (not run): {action.label()}", err=True)
    lines = [line for line in dispatch.agy_stderr.splitlines() if line.strip()]
    for line in lines[:MAX_STDERR_LINES]:
        typer.echo(f"[agy] {line}", err=True)
    if len(lines) > MAX_STDERR_LINES:
        typer.echo(f"[agy] … {len(lines) - MAX_STDERR_LINES} more lines in the log", err=True)


@app.command()
def main(
    cwd: Annotated[
        Path,
        typer.Option(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Directory Gemini works in and may write to. Use the job's own folder.",
        ),
    ],
    prompt: Annotated[str | None, typer.Option(help="The task text.")] = None,
    prompt_file: Annotated[
        Path | None,
        typer.Option(exists=True, dir_okay=False, help="Read the task text from this file."),
    ] = None,
    image: Annotated[
        list[Path] | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="Image for Gemini to look at; named 'Image N' in the prompt. Repeatable.",
        ),
    ] = None,
    add_dir: Annotated[
        list[Path] | None,
        typer.Option(
            exists=True,
            file_okay=False,
            help="Extra directory to open to Gemini, with the mode's write rights. Repeatable.",
        ),
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="agy model id (see `agy models`); agy's default if unset.")
    ] = None,
    effort: Annotated[Effort | None, typer.Option(help="Reasoning effort.")] = None,
    mode: Annotated[Mode, typer.Option(help="agy execution mode.")] = Mode.accept_edits,
    timeout: Annotated[int, typer.Option(min=1, help="Seconds agy may take.")] = 600,
    label: Annotated[str, typer.Option(help="Name for this dispatch's log file.")] = "task",
    out: Annotated[
        Path | None,
        typer.Option(
            dir_okay=False,
            help="Write the reply here instead of stdout; written only when the dispatch is ok.",
        ),
    ] = None,
    strip: Annotated[
        bool,
        typer.Option(
            "--strip-fence", help="Drop one Markdown code fence wrapping the whole reply."
        ),
    ] = False,
    expect: Annotated[
        list[str] | None,
        typer.Option(
            help="File (relative to --cwd) Gemini must write during this dispatch. Repeatable."
        ),
    ] = None,
    allow_empty: Annotated[
        bool, typer.Option("--allow-empty", help="Accept a dispatch that returns no text.")
    ] = False,
    skip_permissions: Annotated[
        bool,
        typer.Option(
            "--dangerously-skip-permissions",
            help="Pass agy's flag of the same name. Only with the human's explicit approval.",
        ),
    ] = False,
) -> None:
    """Hand one self-contained task to Gemini through agy and judge the result."""
    if out is not None and not out.resolve().parent.is_dir():
        raise _usage(f"--out: the folder {out.resolve().parent} does not exist")
    request = Request(
        cwd=cwd,
        prompt=_read_prompt(prompt, prompt_file),
        images=image or [],
        add_dirs=add_dir or [],
        model=model,
        effort=effort,
        mode=mode,
        timeout=timeout,
        label=label,
        expect=expect or [],
        allow_empty=allow_empty,
        strip_fence=strip,
        skip_permissions=skip_permissions,
    )
    try:
        dispatch = run(request)
    except DispatchError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(EXIT_CODES[Outcome.agy_failed]) from error

    reply = dispatch.reply
    if dispatch.outcome is Outcome.ok and out is not None:
        out.write_text(reply if reply.endswith("\n") else reply + "\n", encoding="utf-8")
    elif reply:
        typer.echo(reply, nl=not reply.endswith("\n"))
    _report(dispatch)
    raise typer.Exit(EXIT_CODES[dispatch.outcome])
