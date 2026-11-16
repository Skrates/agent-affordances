"""A fake `agy` on PATH: the tool is verified against a real child process, not a mock."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

FAKE_AGY = """\
#!{python}
import json, os, sys, time
from pathlib import Path

plan = json.loads(Path(os.environ["FAKE_AGY_PLAN"]).read_text())
Path(plan["argv_file"]).write_text(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}))
if plan.get("helper_sleep"):
    # A helper that outlives agy and keeps the inherited stdout open, as a subagent would.
    import subprocess
    subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({{plan['helper_sleep']}})"])
time.sleep(plan.get("sleep", 0))
for name, content in plan.get("write", {{}}).items():
    Path(name).write_text(content)
sys.stderr.write(plan.get("stderr", ""))
sys.stdout.write(plan.get("stdout", ""))
sys.exit(plan.get("exit", 0))
"""


class FakeAgy:
    """Scripts the next `agy` run and reads back what it was called with."""

    def __init__(self, root: Path) -> None:
        self._plan = root / "plan.json"
        self._argv = root / "argv.json"
        self.plan()

    def plan(self, result: dict[str, object] | None = None, **behaviour: object) -> None:
        """Script the run: `result` printed as JSON, or `stdout=` verbatim.

        Also `sleep`, `helper_sleep`, `write` (files to create), `stderr` and `exit`.
        """
        if result is not None:
            behaviour["stdout"] = json.dumps(result)
        self._plan.write_text(json.dumps({"argv_file": str(self._argv), **behaviour}))

    @property
    def called(self) -> bool:
        return self._argv.exists()

    @property
    def argv(self) -> list[str]:
        return json.loads(self._argv.read_text())["argv"]

    @property
    def cwd(self) -> str:
        return json.loads(self._argv.read_text())["cwd"]

    def flag(self, name: str) -> list[str]:
        """Every value passed for a repeatable flag."""
        argv = self.argv
        return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == name]


@pytest.fixture
def agy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeAgy:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "agy"
    script.write_text(textwrap.dedent(FAKE_AGY).format(python=sys.executable))
    script.chmod(0o755)
    fake = FakeAgy(tmp_path)
    monkeypatch.setenv("FAKE_AGY_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return fake


@pytest.fixture
def job(tmp_path: Path) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    return directory


@pytest.fixture
def success() -> Callable[..., dict[str, object]]:
    def build(response: str = "the reply", **extra: object) -> dict[str, object]:
        return {
            "status": "SUCCESS",
            "response": response,
            "conversation_id": "c-1",
            "usage": {"total_tokens": 1234},
            **extra,
        }

    return build
