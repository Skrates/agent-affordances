"""Compare the exact index with exhaustive matching on the same sampled forms.

uv run python scripts/benchmark.py /path/to/repo [...] --sample 1500
Full scan measurements include source parsing; the speedup compares matchers only.
No source code or credentials are included in the JSON measurement output.
"""

import argparse
import json
import random
from itertools import combinations
from time import perf_counter

from afford_dry.cli import collect
from afford_dry.matcher import match_forms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--sample", type=int, default=1500)
    parser.add_argument("--threshold", type=float, default=0.82)
    args = parser.parse_args()
    start = perf_counter()
    _, forms, report = collect(args.roots, False, None, 4, 20, "scan", args.threshold)
    parsed = perf_counter() - start
    start = perf_counter()
    _, full = match_forms(forms, args.threshold, 50)
    indexed_full = perf_counter() - start
    sample = random.Random(0).sample(forms, min(args.sample, len(forms)))
    start = perf_counter()
    expected = {}
    comparisons = 0
    for i, j in combinations(range(len(sample)), 2):
        a, b = sample[i].entry, sample[j].entry
        if a.language != b.language:
            continue
        comparisons += 1
        x, y = a.fingerprints, b.fingerprints
        score = len(x & y) / len(x | y) if x or y else 0.0
        if score >= args.threshold:
            expected[
                (sample[i].repo, a.file, a.start_line, sample[j].repo, b.file, b.start_line)
            ] = score
    exhaustive = perf_counter() - start
    start = perf_counter()
    matches, stats = match_forms(sample, args.threshold, max(1, len(expected)))
    indexed_sample = perf_counter() - start
    actual = {
        (
            m.left.repo,
            m.left.file,
            m.left.start_line,
            m.right.repo,
            m.right.file,
            m.right.start_line,
        ): m.score
        for m in matches
    }
    if actual != expected:
        raise AssertionError("Indexed results differ from exhaustive results")
    output = {
        "complete": report.complete,
        "files": report.stats.files,
        "forms": len(forms),
        "parse_seconds": round(parsed, 6),
        "full_match_seconds": round(indexed_full, 6),
        "full_stats": full.model_dump(),
        "sample_forms": len(sample),
        "sample_exhaustive_comparisons": comparisons,
        "sample_indexed_comparisons": stats.comparisons,
        "sample_matches": len(expected),
        "sample_results_identical": True,
        "sample_exhaustive_seconds": round(exhaustive, 6),
        "sample_indexed_seconds": round(indexed_sample, 6),
        "sample_match_speedup": round(exhaustive / indexed_sample, 2),
    }
    print(json.dumps(output, indent=2))
    if not report.complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
