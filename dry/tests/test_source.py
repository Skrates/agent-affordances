"""Language contracts adapted from Dryer's 0BSD fixtures, plus parser failure cases."""

from pathlib import Path

import pytest

from afford_dry.models import Diagnostic, Form
from afford_dry.repository import Repository, language_of
from afford_dry.source import read_forms

SAMPLES = {
    "py": (
        "def alpha(xs):\n    ys = xs.filter(odd)\n    return len(ys.total)\n",
        "def beta(items):\n    kept = items.filter(even)\n    return len(kept.count)\n",
    ),
    "ts": (
        "function alpha(xs: number[]) {\n const ys = xs.filter(odd);\n return map(ys, inc);\n}\n",
        "function beta(items: number[]) {\n const kept = items.filter(even);\n"
        " return map(kept, dec);\n}\n",
    ),
    "tsx": (
        "const alpha = (xs: number[]) => {\n const ys = xs.filter(odd);\n"
        " return map(ys, inc);\n};\n",
        "const beta = (items: number[]) => {\n const kept = items.filter(even);\n"
        " return map(kept, dec);\n};\n",
    ),
    "go": (
        "package demo\nfunc alpha(xs []int) int {\n fmt.Println(xs)\n return len(xs)\n}\n",
        "package demo\nfunc beta(items []int) int {\n log.Println(items)\n return len(items)\n}\n",
    ),
    "rs": (
        "fn alpha() {\n let ys = Vec::new();\n let zs = HashMap::<String, i32>::new();\n}\n",
        "fn beta() {\n let kept = Vec::new();\n let rows = HashMap::<Integer, i32>::new();\n}\n",
    ),
}


def read(root: Path, name: str, source: str) -> tuple[list[Form], Diagnostic | None]:
    path = root / name
    path.write_text(source)
    return read_forms(Repository("repo", root), path, 1, 1)


@pytest.mark.parametrize("suffix", SAMPLES)
def test_renaming_locals_preserves_structure(tmp_path: Path, suffix: str) -> None:
    left, diagnostic = read(tmp_path, f"a.{suffix}", SAMPLES[suffix][0])
    right, other = read(tmp_path, f"b.{suffix}", SAMPLES[suffix][1])
    assert diagnostic is other is None
    assert len(left) == len(right) == 1
    assert left[0].entry.fingerprints == right[0].entry.fingerprints
    assert left[0].symbol == "alpha"
    assert right[0].symbol == "beta"


def test_methods_operators_and_interpolation_stay_significant(tmp_path: Path) -> None:
    sources = [
        "return xs.filter(odd)",
        "return xs.select(odd)",
        "return xs + 1",
        "return xs - 1",
        'return "value"',
        'return f"{xs}"',
    ]
    prints = [
        read(tmp_path, f"{i}.py", f"def alpha(xs):\n    {source}\n")[0][0].entry.fingerprints
        for i, source in enumerate(sources)
    ]
    assert len(set(prints)) == len(sources)


def test_nested_python_and_named_method(tmp_path: Path) -> None:
    forms, diagnostic = read(
        tmp_path,
        "a.py",
        "class Box:\n    def outer(self):\n        def inner():\n"
        "            return 1\n        return inner()\n",
    )
    assert diagnostic is None
    assert [(f.symbol, f.entry.start_line, f.entry.end_line) for f in forms] == [
        ("Box.outer", 2, 5)
    ]


def test_ts_nested_callbacks_stay_in_outer(tmp_path: Path) -> None:
    forms, diagnostic = read(
        tmp_path,
        "a.ts",
        "function outer(xs: number[]) {\n const fn = (x: number) => x + 1;\n"
        " return xs.map(fn);\n}\n",
    )
    assert diagnostic is None
    assert [f.symbol for f in forms] == ["outer"]


def test_rust_tests_and_nested_functions_are_excluded(tmp_path: Path) -> None:
    forms, diagnostic = read(
        tmp_path,
        "a.rs",
        "fn alpha() { fn nested() {} }\nmod helpers { fn beta() {} }\n"
        "mod tests { fn gamma() {} }\n",
    )
    assert diagnostic is None
    assert [f.symbol for f in forms] == ["alpha", "beta"]


def test_parse_error_is_not_silently_recovered(tmp_path: Path) -> None:
    forms, diagnostic = read(tmp_path, "a.py", "def good():\n    return 1\ndef broken(\n")
    assert forms == []
    assert diagnostic is not None


def test_deep_expression_is_iterative(tmp_path: Path) -> None:
    forms, diagnostic = read(
        tmp_path, "a.py", "def deep():\n    return " + " + ".join(["1"] * 1200) + "\n"
    )
    assert diagnostic is None
    assert forms[0].entry.nodes > 1200


@pytest.mark.parametrize("suffix", ["clj", "cljc", "cljs", "java", "js"])
def test_unsupported_languages_are_cut(suffix: str) -> None:
    assert language_of(f"a.{suffix}") is None


def test_export_type_star_and_namespace_keep_source_spans(tmp_path: Path) -> None:
    baseline = "function alpha(xs: number[]) {\n return xs.filter(odd);\n}\n"
    expected, _ = read(tmp_path, "baseline.ts", baseline)
    for statement in ['export type * from "./a.js";', 'export type * as types from "./a.js";']:
        forms, diagnostic = read(tmp_path, "export.ts", statement + "\n" + baseline)
        assert diagnostic is None
        assert forms[0].entry.fingerprints == expected[0].entry.fingerprints
        assert (forms[0].entry.start_line, forms[0].entry.end_line) == (2, 4)


def test_invalid_type_export_still_reports_parse_failure(tmp_path: Path) -> None:
    forms, diagnostic = read(tmp_path, "a.ts", "export type * from ;\nfunction alpha() {}\n")
    assert forms == []
    assert diagnostic is not None


def test_python_type_parameter_defaults_preserve_methods_and_spans(tmp_path: Path) -> None:
    source = (
        "class Envelope[T, Ref = str]:\n"
        "    def read(self, item: T) -> T:\n        return item\n"
        "def helper[T = str](item: T) -> T:\n    return item\n"
    )
    forms, diagnostic = read(tmp_path, "a.py", source)
    assert diagnostic is None
    assert [(f.symbol, f.entry.start_line, f.entry.end_line) for f in forms] == [
        ("Envelope.read", 2, 3),
        ("helper", 4, 5),
    ]
