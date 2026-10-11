"""Git owns file discovery, ignore rules, and the definition of changed lines."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXTENSIONS = {
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "target",
    "dist",
    "build",
    "out",
    "coverage",
    ".metrics",
    "testdata",
    "__pycache__",
}


def language_of(path: str | Path) -> str | None:
    file = Path(path)
    return (
        None if file.name.endswith((".d.ts", ".d.mts", ".d.cts")) else EXTENSIONS.get(file.suffix)
    )


def is_test_file(path: str | Path) -> bool:
    file = Path(path)
    return (
        bool(TEST_DIRS.intersection(file.parts))
        or file.name == "conftest.py"
        or (file.name.startswith("test_") and file.suffix == ".py")
        or file.name.endswith(("_test.py", "_test.go"))
        or file.stem.endswith((".test", ".spec"))
    )


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def paths(data: bytes) -> set[str]:
    # Filesystem decoding preserves non-UTF-8 Git paths rather than conflating them.
    import os

    return {os.fsdecode(part) for part in data.split(b"\0") if part}


@dataclass(frozen=True)
class Repository:
    name: str
    root: Path

    def source_files(self, include_tests: bool, excludes: list[str]) -> list[Path]:
        names = paths(
            git(self.root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        )
        return [
            self.root / name
            for name in sorted(names)
            if language_of(name)
            and (include_tests or not is_test_file(name))
            and SKIP_DIRS.isdisjoint(Path(name).parts)
            and not any(Path(name).full_match(pattern) for pattern in excludes)
            and (self.root / name).is_file()
        ]

    def changed_lines(self, base: str | None) -> dict[Path, list[tuple[int, int]]]:
        revision = git(self.root, "merge-base", base, "HEAD").decode().strip() if base else "HEAD"
        names = paths(git(self.root, "diff", "--name-only", "-z", "--diff-filter=ACMR", revision))
        untracked = paths(git(self.root, "ls-files", "--others", "--exclude-standard", "-z"))
        changed: dict[Path, list[tuple[int, int]]] = {}
        for name in sorted(names | untracked):
            path = self.root / name
            if not path.is_file() or language_of(path) is None:
                continue
            if name in untracked:
                changed[path] = [(1, 2**63 - 1)]
                continue
            patch = git(
                self.root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--unified=0",
                revision,
                "--",
                name,
            ).decode(errors="replace")
            ranges = []
            for line in patch.splitlines():
                hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
                if hunk:
                    start = int(hunk[1])
                    count = int(hunk[2]) if hunk[2] is not None else 1
                    # A deletion has no new lines. Both sides of its new-file anchor
                    # can belong to the affected surviving form.
                    ranges.append((max(1, start), start + max(1, count) - (count > 0)))
            changed[path] = ranges
        return changed


def repositories(specs: list[str]) -> list[Repository]:
    result = []
    for spec in specs or ["."]:
        label, separator, raw = spec.partition("=")
        path = Path(raw if separator else spec).expanduser().resolve()
        root = Path(git(path, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        name = label if separator else root.name
        if not name:
            raise ValueError("repository label must not be empty")
        if any(repo.root == root or repo.name == name for repo in result):
            raise ValueError(f"duplicate repository path or label: {spec}")
        result.append(Repository(name, root))
    return result
