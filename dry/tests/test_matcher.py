"""An exhaustive oracle proves that indexing cannot hide qualifying pairs or groups."""

import random
from itertools import combinations
from pathlib import Path

import pytest

from afford_dry.matcher import match_forms
from afford_dry.models import Entry, Form


def form(index: int, tokens: set[str], language: str = "python", repo: str = "repo") -> Form:
    return Form(
        repo,
        Path(f"/repo/{index}.py"),
        str(index),
        Entry(language, f"{index}.py", 1, 4, 20, frozenset(tokens)),
    )


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("threshold", [0.01, 0.25, 1 / 3, 0.5, 0.82, 0.9, 1.0])
def test_index_is_exact(seed: int, threshold: float) -> None:
    rng = random.Random(seed)
    forms = [
        form(
            i,
            {str(n) for n in rng.sample(range(30), rng.randrange(1, 24))},
            "python" if i % 3 else "rust",
            "a" if i % 2 else "b",
        )
        for i in range(40)
    ]
    forms += [form(40, set(forms[0].entry.fingerprints), forms[0].entry.language)]
    for queries in [None, set(), {0}, {0, 3, 8, 11, 40}, set(range(len(forms)))]:
        for cross_repo in [False, True]:
            expected = []
            identical: dict[int, set[int]] = {}
            possible = 0
            for i, j in combinations(range(len(forms)), 2):
                a, b = forms[i], forms[j]
                if a.entry.language != b.entry.language:
                    continue
                if queries is not None and i not in queries and j not in queries:
                    continue
                if cross_repo and a.repo == b.repo:
                    continue
                possible += 1
                x, y = a.entry.fingerprints, b.entry.fingerprints
                score = len(x & y) / len(x | y)
                if score >= threshold:
                    left, right = (
                        (j, i)
                        if queries is not None and j in queries and i not in queries
                        else (i, j)
                    )
                    expected.append((-score, left, right))
                if score == 1.0:
                    merged = identical.get(i, {i}) | identical.get(j, {j})
                    for member in merged:
                        identical[member] = merged
            matches, groups, stats = match_forms(forms, threshold, 7, queries, cross_repo)
            actual = [(-m.score, int(m.left.symbol), int(m.right.symbol)) for m in matches]
            assert actual == sorted(expected)[:7]
            assert stats.matching_pairs == len(expected)
            classes = sorted(
                {tuple(sorted(c)) for c in identical.values()}, key=lambda c: (-len(c), c)
            )
            assert [[int(m.symbol) for m in g.members] for g in groups] == [
                list(c) for c in classes[:7]
            ]
            assert stats.identical_groups == len(classes)
            assert stats.possible_pairs == possible
            assert stats.comparisons <= possible


@pytest.mark.parametrize("threshold", [0, -1, 1.01, float("nan"), float("inf")])
def test_bad_threshold_is_an_error(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        match_forms([], threshold, 1)


def test_empty_fingerprints_and_size_bound() -> None:
    matches, _, stats = match_forms([form(0, set()), form(1, {"x"}), form(2, {"x", "y"})], 1.0, 5)
    assert matches == []
    assert stats.comparisons == 0


def test_many_unrelated_forms_need_no_intersections() -> None:
    forms = [form(i, {f"{i}:{j}" for j in range(30)}) for i in range(1000)]
    matches, _, stats = match_forms(forms, 0.82, 5)
    assert matches == []
    assert stats.possible_pairs == 499500
    assert stats.comparisons == 0
