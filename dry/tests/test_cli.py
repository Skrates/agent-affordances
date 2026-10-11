"""Real Git and the installed CLI exercise discovery and changed-line semantics."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from afford_dry.cli import app

runner = CliRunner()
SOURCE = "def alpha(xs):\n    ys = xs.filter(odd)\n    zs = map(ys, inc)\n    return list(zs)\n"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "old.py").write_text(SOURCE)
    git(tmp_path, "add", "old.py")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture")
    return tmp_path


def invoke(*args: str) -> dict:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_new_function_matches_unchanged_code(repo: Path) -> None:
    (repo / "new.py").write_text(SOURCE.replace("alpha", "beta"))
    report = invoke("scan", "--changed", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["stats"]["matching_pairs"] == 1
    assert report["matches"][0]["left"]["file"] == "new.py"
    assert report["matches"][0]["right"]["file"] == "old.py"
    assert not (repo / ".metrics").exists()


def test_only_touched_functions_are_queries_and_staged_changes_count(repo: Path) -> None:
    path = repo / "old.py"
    path.write_text(SOURCE + "\n\n" + SOURCE.replace("alpha", "beta"))
    git(repo, "add", "old.py")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "two functions")
    path.write_text(path.read_text().replace("def beta(xs):", "def beta(items):"))
    git(repo, "add", "old.py")
    report = invoke("scan", "--changed", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["matches"][0]["left"]["symbol"] == "beta"


def test_base_includes_committed_and_uncommitted_changes(repo: Path) -> None:
    git(repo, "branch", "baseline")
    (repo / "new.py").write_text(SOURCE.replace("alpha", "beta"))
    git(repo, "add", "new.py")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "new")
    assert invoke("scan", "--changed", str(repo))["stats"]["queries"] == 0
    report = invoke("scan", "--base", "baseline", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["stats"]["matching_pairs"] == 1


def test_match_can_query_ignored_target_and_retains_full_corpus(repo: Path) -> None:
    (repo / ".gitignore").write_text("scratch.py\n")
    (repo / "scratch.py").write_text(SOURCE.replace("alpha", "scratch"))
    report = invoke("match", f"{repo}/scratch.py:2", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["matches"][0]["left"]["symbol"] == "scratch"
    assert report["matches"][0]["right"]["symbol"] == "alpha"


def test_ignore_tests_declarations_and_cut_languages(repo: Path) -> None:
    (repo / ".gitignore").write_text("ignored.py\n")
    for name in ["ignored.py", "test_copy.py", "a.java", "a.clj", "a.d.ts"]:
        (repo / name).write_text(SOURCE)
    assert invoke("scan", str(repo))["stats"]["forms"] == 1
    report = invoke("scan", "--include-tests", str(repo))
    assert report["stats"]["matching_pairs"] == 1
    assert (
        invoke("scan", "--include-tests", "--exclude", "test_*", str(repo))["stats"]["forms"] == 1
    )


def test_cross_repo_lookup_and_labels(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    (other / "copy.py").write_text(SOURCE)
    report = invoke("scan", "--cross-repo", f"a={repo}", f"b={other}")
    assert report["stats"]["matching_pairs"] == 1
    assert {report["matches"][0][side]["repo"] for side in ["left", "right"]} == {"a", "b"}
    assert report["roots"] == {"a": str(repo), "b": str(other)}


def test_deletion_inside_function_selects_surviving_form(repo: Path) -> None:
    (repo / "old.py").write_text(SOURCE.replace("    zs = map(ys, inc)\n", ""))
    report = invoke("scan", "--changed", "--min-lines", "1", str(repo))
    assert report["stats"]["queries"] == 1


def test_parse_failure_outputs_partial_report_and_nonzero(repo: Path) -> None:
    (repo / "broken.py").write_text("def broken(\n")
    result = runner.invoke(app, ["scan", str(repo)])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["complete"] is False
    assert report["diagnostics"][0]["file"] == "broken.py"


def test_limit_discloses_omissions_and_order_is_stable(repo: Path) -> None:
    for name in ["b", "c", "d"]:
        (repo / f"{name}.py").write_text(SOURCE.replace("alpha", name))
    report = invoke("scan", "--limit", "2", str(repo))
    assert len(report["matches"]) == 2
    assert report["stats"]["matching_pairs"] == 6
    assert report["truncated"] is True
    assert report == invoke("scan", "--limit", "2", str(repo))


@pytest.mark.parametrize(
    "options",
    [
        ["--threshold", "nan"],
        ["--threshold", "0"],
        ["--threshold", "1.1"],
        ["--limit", "0"],
        ["--changed", "--base", "HEAD"],
        ["--base", "missing-ref"],
    ],
)
def test_invalid_inputs_are_usage_errors(repo: Path, options: list[str]) -> None:
    result = runner.invoke(app, ["scan", *options, str(repo)])
    assert result.exit_code == 2


def test_bad_match_location_and_duplicate_roots_are_errors(repo: Path) -> None:
    for args in [
        ["match", f"{repo}/old.py:9999", str(repo)],
        ["scan", str(repo), str(repo)],
        ["match", "not-a-location", str(repo)],
    ]:
        assert runner.invoke(app, args).exit_code == 2


def test_recursive_exclusion_and_unicode_paths(repo: Path) -> None:
    folder = repo / "generated" / "nested"
    folder.mkdir(parents=True)
    (folder / "copy.py").write_text(SOURCE)
    (repo / "álfur spaced.py").write_text(SOURCE.replace("alpha", "beta"))
    report = invoke("scan", "--changed", "--exclude", "generated/**", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["matches"][0]["left"]["file"] == "álfur spaced.py"


def test_match_keeps_small_target_when_other_functions_survive_filters(repo: Path) -> None:
    path = repo / "old.py"
    path.write_text(SOURCE + "\ndef small(xs):\n    return xs\n")
    report = invoke("match", f"{path}:7", "--min-lines", "4", str(repo))
    assert report["stats"]["queries"] == 1
    assert report["stats"]["forms"] == 2


def test_ignored_target_does_not_admit_other_ignored_functions(repo: Path) -> None:
    (repo / ".gitignore").write_text("scratch.py\n")
    path = repo / "scratch.py"
    path.write_text(SOURCE.replace("alpha", "query") + "\n" + SOURCE.replace("alpha", "other"))
    report = invoke("match", f"{path}:2", str(repo))
    assert report["stats"]["forms"] == 2
    assert report["stats"]["matching_pairs"] == 1
    assert report["matches"][0]["right"]["file"] == "old.py"


def test_narrowest_explicit_target_is_chosen_before_corpus_filters(repo: Path) -> None:
    source = (
        "def factory(value):\n    class Inner:\n        def read(self):\n"
        "            return self.value\n    return Inner()\n"
    )
    path = repo / "target.py"
    path.write_text(source)
    (repo / "outer_copy.py").write_text(source.replace("factory", "other_factory"))
    (repo / "copy.py").write_text("def read_copy(self):\n\n\n    return self.value\n")
    report = invoke("match", f"{path}:4", "--min-lines", "4", str(repo))
    assert report["matches"][0]["left"]["symbol"] == "Inner.read"
    assert report["matches"][0]["right"]["symbol"] == "read_copy"
    assert report["matches"][0]["score"] == 1.0
