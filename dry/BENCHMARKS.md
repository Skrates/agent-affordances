# Measured behavior

Local run on 2026-10-11, macOS arm64, CPython 3.13.12, Tree-sitter 0.25.2.
Threshold 0.82, minimum 4 lines / 20 nodes. Corpus: the local Sokrates, Krepis,
and Hive Git checkouts; tracked plus non-ignored untracked production source.
These are observations on those checkouts, not guarantees for other machines.

| Corpus | Files | Forms | Eligible exhaustive pairs | Exact indexed comparisons |
| --- | ---: | ---: | ---: | ---: |
| Sokrates | 872 | 5,811 | 14,404,831 | 10,733 |
| Krepis | 701 | 3,893 | 7,025,116 | 4,920 |
| Hive | 58 | 873 | 215,218 | 258 |
| Combined | 1,631 | 10,577 | 44,637,916 | 23,793 |

The combined benchmark parsed the corpus in **5.12 s** and matched it in
**0.39 s**, finding **3,499 candidate pairs involving 2,388 functions**, with
complete coverage and no parser diagnostics. Those comprise 1,318 pairs within
Sokrates, 1,045 within Krepis, 28 within Hive, and 1,108 across repositories.
The exact filters avoided **99.9467%** of eligible intersections.
Candidate count is not a count of defects or justified refactors.

On a deterministic 1,500-form sample of that same corpus, exhaustive matching
took **3.16 s** (875,350 intersections). Indexed matching took **0.046 s**
(498 intersections), a **69.5x matcher speedup**. Both returned the same 76
pairs with exactly the same scores. This speedup excludes parsing; it is not
a claim of 69.5x faster end-to-end scans.

Reproduce the measurement on current checkouts:

```sh
uv run python scripts/benchmark.py /path/to/sokrates /path/to/Krepis /path/to/hive --sample 1500
```

The benchmark fails if its sampled indexed/exhaustive results differ. It prints
measurements only and writes nothing to the analyzed repositories.

Before the final review repairs, at commit `7124c08`, all **10,577** extracted
functions were compared to Dryer's original
normalization and fingerprint implementation at
`66ff6d21a42c04afcad89c78a80066176d1294b0`, with both implementations using the
same grammar wheels. Every compared source span's node count and fingerprint
set agreed. This establishes parity on the measured corpus, not universal
language correctness. The final version intentionally improves on upstream by
retaining TypeScript template expressions and Rust macro callee names; its
fingerprints and scores therefore differ for those constructs. The original
3,558-pair result is superseded by the refreshed figures above. Dedicated tests
cover these distinctions, decorated Python spans, canonical symlink identity,
Python source encodings, and the two newer type-syntax parser adaptations.

The test suite uses exhaustive pair enumeration as an oracle across seeded
synthetic corpora, seven thresholds (including exact boundaries), full/focused
queries, repository filtering, and result limits. Real Git fixtures cover
staged/unstaged/untracked changes, merge-base scans, deletion anchors, ignore
rules, Unicode paths, and partial parse failures.
