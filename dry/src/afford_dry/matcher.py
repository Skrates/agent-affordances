"""Exact Jaccard join with rare-first prefixes; no approximate index or score change.

If J(A,B) >= t, the sorted prefixes of lengths |S|-ceil(t*|S|)+1
must share a token under one common token order. Otherwise their shared suffix
cannot supply the overlap required by Jaccard. Prefix hits only nominate pairs;
the original fingerprint sets decide the score.

Similarity below 1.0 is not transitive, so pairs are never chained into clusters.
Identity is transitive: pairs scoring exactly 1.0 merge into groups whose members
all share one fingerprint set.
"""

from collections import Counter, defaultdict
from heapq import nsmallest
from math import ceil, inf, isfinite, nextafter

from .models import Form, Group, Match, Stats


def eligible_pairs(indices: list[int], forms: list[Form], queries: set[int] | None) -> int:
    size = len(indices)
    unselected = 0 if queries is None else sum(i not in queries for i in indices)
    return size * (size - 1) // 2 - unselected * (unselected - 1) // 2


def match_forms(
    forms: list[Form],
    threshold: float,
    limit: int,
    queries: set[int] | None = None,
    cross_repo: bool = False,
) -> tuple[list[Match], list[Group], Stats]:
    if not isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("threshold must be finite and greater than 0, at most 1")
    if limit < 1:
        raise ValueError("limit must be positive")
    stats = Stats(forms=len(forms), queries=len(forms) if queries is None else len(queries))
    if queries == set():
        return [], [], stats
    groups: dict[str, list[int]] = defaultdict(list)
    for index, form in enumerate(forms):
        groups[form.entry.language].append(index)
    best: list[tuple[float, int, int, int]] = []
    identical: dict[int, int] = {}

    def root(i: int) -> int:
        while identical.setdefault(i, i) != i:
            identical[i] = identical[identical[i]]
            i = identical[i]
        return i

    for indices in groups.values():
        stats.possible_pairs += eligible_pairs(indices, forms, queries)
        if cross_repo:
            by_repo: dict[str, list[int]] = defaultdict(list)
            for i in indices:
                by_repo[forms[i].repo].append(i)
            stats.possible_pairs -= sum(
                eligible_pairs(group, forms, queries) for group in by_repo.values()
            )
        if queries is not None and not queries.intersection(indices):
            continue
        frequencies = Counter(token for i in indices for token in forms[i].entry.fingerprints)
        order = {
            token: rank
            for rank, token in enumerate(
                sorted(frequencies, key=lambda token: (frequencies[token], token))
            )
        }
        prefixes: dict[int, list[str]] = {}
        for i in indices:
            tokens = forms[i].entry.fingerprints
            size = len(tokens)
            length = size - ceil(nextafter(threshold * size, -inf)) + 1
            prefixes[i] = sorted(tokens, key=order.__getitem__)[:length] if size else []
        postings: dict[str, list[int]] = defaultdict(list)
        if queries is not None:
            for i in indices:
                for token in prefixes[i]:
                    postings[token].append(i)
            probes = sorted(queries.intersection(indices))
        else:
            probes = sorted(indices, key=lambda i: (len(forms[i].entry.fingerprints), i))
        for i in probes:
            left = forms[i]
            tokens = left.entry.fingerprints
            candidates = {j for token in prefixes[i] for j in postings[token]}
            for j in candidates:
                if i == j or (queries is not None and j in queries and j < i):
                    continue
                right = forms[j]
                if cross_repo and left.repo == right.repo:
                    continue
                other = right.entry.fingerprints
                if min(len(tokens), len(other)) / max(len(tokens), len(other)) < threshold:
                    continue
                stats.comparisons += 1
                common = len(tokens & other)
                score = common / (len(tokens) + len(other) - common)
                if score < threshold:
                    continue
                stats.matching_pairs += 1
                if common == len(tokens) == len(other):
                    identical[root(i)] = root(j)
                a, b = (i, j) if queries is not None and j not in queries else sorted((i, j))
                best.append((-score, -min(left.entry.nodes, right.entry.nodes), a, b))
                if len(best) > 2 * limit:
                    best = nsmallest(limit, best)
            if queries is None:
                for token in prefixes[i]:
                    postings[token].append(i)
    members: dict[int, list[int]] = defaultdict(list)
    for i in sorted(identical):
        members[root(i)].append(i)
    stats.identical_groups = len(members)
    identical_groups = [
        Group(
            language=forms[group[0]].entry.language,
            nodes=forms[group[0]].entry.nodes,
            members=[forms[i].location() for i in group],
        )
        for group in nsmallest(
            limit, members.values(), key=lambda g: (-len(g), -forms[g[0]].entry.nodes, g[0])
        )
    ]
    matches = [
        Match(
            score=-score,
            language=forms[a].entry.language,
            left=forms[a].location(),
            right=forms[b].location(),
        )
        for score, _size, a, b in nsmallest(limit, best)
    ]
    return matches, identical_groups, stats
