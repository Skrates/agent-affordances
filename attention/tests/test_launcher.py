"""The launcher is verified end to end: a real exec into a fake `hive` script."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from hive_attention.cli import RECURSION_GUARD

SRC = Path(__file__).resolve().parents[1] / "src"


def fake_hive(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "hive"
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(0o755)
    return script


def launch(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "hive_attention.cli", *args],
        env={"PYTHONPATH": str(SRC), "PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_forwards_argv_verbatim_without_a_shell(tmp_path: Path) -> None:
    out = tmp_path / "argv.json"
    hive = fake_hive(
        tmp_path,
        f"""\
        printf '%s\\n' "$@" > {out}
        """,
    )
    awkward = ["resolve", "42", "--response", 'Use "C"; $(rm -rf /) `x` & keep *', "--next"]
    result = launch(awkward, {"HIVE_BIN": str(hive)})
    assert result.returncode == 0, result.stderr
    assert out.read_text().splitlines() == ["attention", *awkward]


def test_preserves_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    hive = fake_hive(
        tmp_path,
        """\
        printf '{"result":"empty"}\\n'
        echo "diagnostic" >&2
        exit 5
        """,
    )
    result = launch(["next", "--json"], {"HIVE_BIN": str(hive)})
    assert result.returncode == 5
    assert json.loads(result.stdout) == {"result": "empty"}
    assert result.stderr == "diagnostic\n"


def test_signals_reach_hive_because_the_launcher_becomes_it(tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    hive = fake_hive(
        tmp_path,
        f"""\
        trap 'echo terminated; exit 143' TERM
        touch {ready}
        while :; do sleep 0.05; done
        """,
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "hive_attention.cli", "queue"],
        env={"PYTHONPATH": str(SRC), "PATH": os.environ["PATH"], "HIVE_BIN": str(hive)},
        stdout=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline, "fake hive never started"
        time.sleep(0.02)
    process.send_signal(signal.SIGTERM)
    stdout, _ = process.communicate(timeout=10)
    assert process.returncode == 143
    assert stdout.strip() == "terminated"


def test_falls_back_to_hive_on_path(tmp_path: Path) -> None:
    fake_hive(tmp_path, 'echo "via PATH: $*"\n')
    result = launch(["capabilities"], {"PATH": f"{tmp_path}:{os.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == "via PATH: attention capabilities\n"


def test_missing_hive_is_diagnosed(tmp_path: Path) -> None:
    result = launch(["queue"], {"HIVE_BIN": str(tmp_path / "nope")})
    assert result.returncode == 127
    assert "cannot find HIVE_BIN=" in result.stderr
    assert result.stdout == ""
    bare = launch(["queue"], {"PATH": str(tmp_path)})
    assert bare.returncode == 127
    assert "`hive` on PATH" in bare.stderr


@pytest.mark.parametrize("via", ["guard", "self"])
def test_recursion_is_refused(tmp_path: Path, via: str) -> None:
    if via == "guard":
        result = launch(["queue"], {RECURSION_GUARD: "1", "HIVE_BIN": "/bin/echo"})
    else:
        launcher = tmp_path / "hive-attention"
        launcher.write_text(
            f"#!{sys.executable}\nfrom hive_attention.cli import main\nraise SystemExit(main())\n"
        )
        launcher.chmod(0o755)
        result = subprocess.run(
            [str(launcher), "queue"],
            env={"PYTHONPATH": str(SRC), "PATH": os.environ["PATH"], "HIVE_BIN": str(launcher)},
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 127
    assert "hive-attention" in result.stderr


def test_unexecutable_hive_is_diagnosed_without_a_traceback(tmp_path: Path) -> None:
    hive = tmp_path / "hive"
    hive.write_text("#!/nonexistent/interpreter\n")
    hive.chmod(0o755)
    result = launch(["queue"], {"HIVE_BIN": str(hive)})
    assert result.returncode == 126
    assert result.stderr.startswith(f"hive-attention: cannot execute {hive}: ")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
