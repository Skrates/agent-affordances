"""Bounded, read-only structural search for agents."""

from pathlib import Path
from typing import Annotated

import typer

from .matcher import match_forms
from .models import Form, Report
from .repository import Repository, repositories
from .source import read_forms

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Find code to reuse across repositories. Structural similarity is a lead.",
)
Roots = Annotated[
    list[str] | None, typer.Argument(help="Git roots, optionally NAME=PATH. Default: .")
]
Threshold = Annotated[float, typer.Option(min=0.000001, max=1, help="Minimum Jaccard similarity.")]
Limit = Annotated[int, typer.Option(min=1, help="Maximum returned pairs; total count is retained.")]
MinLines = Annotated[int, typer.Option(min=1)]
MinNodes = Annotated[int, typer.Option(min=1)]
Excludes = Annotated[list[str] | None, typer.Option("--exclude", help="Repeatable path glob.")]


def collect(
    roots: list[str] | None,
    include_tests: bool,
    excludes: list[str] | None,
    min_lines: int,
    min_nodes: int,
    scope: str,
    threshold: float,
) -> tuple[list[Repository], list[Form], Report]:
    repos = repositories(roots or [])
    forms: list[Form] = []
    report = Report(
        scope=scope,
        roots={repo.name: str(repo.root) for repo in repos},
        threshold=threshold,
        complete=True,
    )
    seen: set[Path] = set()
    for repo in repos:
        files = repo.source_files(include_tests, excludes or [])
        for path in sorted(files, key=lambda path: (path.is_symlink(), path.as_posix())):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            report.stats.files += 1
            entries, diagnostic = read_forms(repo, path, min_lines, min_nodes)
            forms.extend(entries)
            if diagnostic:
                report.diagnostics.append(diagnostic)
                report.complete = False
    return repos, forms, report


def emit(report: Report, text: bool) -> None:
    if text:
        for match in report.matches:
            refs = [
                f"{loc.repo}/{loc.file}:{loc.start_line}-{loc.end_line} {loc.symbol}"
                for loc in (match.left, match.right)
            ]
            typer.echo(f"{match.score:.3f} {match.language}\n  " + "\n  ".join(refs))
        for group in report.groups:
            refs = [
                f"{loc.repo}/{loc.file}:{loc.start_line}-{loc.end_line} {loc.symbol}"
                for loc in group.members
            ]
            typer.echo(
                f"identical x{len(group.members)} {group.language} {group.nodes} nodes\n  "
                + "\n  ".join(refs)
            )
        stats = report.stats
        typer.echo(
            f"{len(report.matches)}/{stats.matching_pairs} pairs; "
            f"{len(report.groups)}/{stats.identical_groups} identical groups; {stats.forms} forms; "
            f"{stats.comparisons}/{stats.possible_pairs} comparisons; "
            f"complete={str(report.complete).lower()}"
        )
    else:
        typer.echo(report.model_dump_json())
    for diagnostic in report.diagnostics:
        typer.echo(f"{diagnostic.repo}/{diagnostic.file}: {diagnostic.message}", err=True)
    if not report.complete:
        raise typer.Exit(1)


@app.command()
def scan(
    roots: Roots = None,
    changed: Annotated[bool, typer.Option(help="Changed forms versus the entire corpus.")] = False,
    base: Annotated[
        str | None, typer.Option(help="Changes since merge-base with REF, plus edits.")
    ] = None,
    cross_repo: Annotated[
        bool, typer.Option(help="Only pairs from different repositories.")
    ] = False,
    threshold: Threshold = 0.82,
    limit: Limit = 50,
    min_lines: MinLines = 4,
    min_nodes: MinNodes = 20,
    include_tests: bool = False,
    exclude: Excludes = None,
    text: bool = False,
) -> None:
    """Find duplicates; --changed/--base focus the query, never shrink the comparison corpus."""
    if changed and base:
        raise typer.BadParameter("choose --changed or --base")
    try:
        repos, forms, report = collect(
            roots,
            include_tests,
            exclude,
            min_lines,
            min_nodes,
            "base" if base else "changed" if changed else "scan",
            threshold,
        )
        queries = None
        if changed or base:
            ranges: dict[Path, list[tuple[int, int]]] = {}
            for repo in repos:
                for path, spans in repo.changed_lines(base).items():
                    ranges.setdefault(path.resolve(), []).extend(spans)
            queries = {
                i
                for i, form in enumerate(forms)
                if any(
                    start <= form.entry.end_line and end >= form.entry.start_line
                    for start, end in ranges.get(form.path.resolve(), [])
                )
            }
        files = report.stats.files
        report.matches, report.groups, report.stats = match_forms(
            forms, threshold, limit, queries, cross_repo
        )
        report.stats.files = files
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(report, text)


@app.command()
def match(
    target: Annotated[str, typer.Argument(help="FILE:LINE inside the function to find elsewhere.")],
    roots: Roots = None,
    threshold: Threshold = 0.82,
    limit: Limit = 20,
    min_lines: MinLines = 1,
    min_nodes: MinNodes = 1,
    include_tests: bool = False,
    exclude: Excludes = None,
    text: bool = False,
) -> None:
    """Find existing code shaped like the function containing FILE:LINE."""
    try:
        raw, separator, line_text = target.rpartition(":")
        if not separator or not line_text.isdigit() or int(line_text) < 1:
            raise ValueError("target must be FILE:LINE with a positive line number")
        path = Path(raw).expanduser().resolve()
        repos, forms, report = collect(
            roots, include_tests, exclude, min_lines, min_nodes, "match", threshold
        )
        root = next((repo for repo in repos if path.is_relative_to(repo.root)), None)
        if root is None:
            raise ValueError("target must belong to one of the supplied repositories")
        extra, diagnostic = read_forms(root, path, 1, 1)
        if diagnostic:
            raise ValueError(diagnostic.message)
        targets = [
            form for form in extra if form.entry.start_line <= int(line_text) <= form.entry.end_line
        ]
        if not targets:
            raise ValueError("no function with an executable body contains that line")
        target_form = min(targets, key=lambda form: form.entry.end_line - form.entry.start_line)
        query = next(
            (
                i
                for i, form in enumerate(forms)
                if form.path.resolve() == path
                and form.entry.start_line == target_form.entry.start_line
                and form.entry.end_line == target_form.entry.end_line
            ),
            len(forms),
        )
        if query == len(forms):
            forms.append(target_form)
        files = report.stats.files
        report.matches, report.groups, report.stats = match_forms(forms, threshold, limit, {query})
        report.stats.files = files
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(report, text)
