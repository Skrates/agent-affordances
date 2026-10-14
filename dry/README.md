# afford-dry

Find existing code before writing another copy. Search structural duplicates in
Python, TypeScript/TSX, Go, and Rust, within one Git repository or across several.

```sh
uv tool install ./dry
afford-dry scan /path/to/repo
afford-dry scan --changed /path/to/repo
afford-dry scan --base origin/main /path/to/repo
afford-dry match /path/to/repo/src/example.py:42 /path/to/repo
```

From this directory, `uv run afford-dry ...` runs without installing the command.
From the repository root, use `uv run --project dry afford-dry ...`.

`scan --changed` queries functions touched by staged, unstaged, or untracked
changes against **all** eligible source in the supplied repositories. `--base REF`
also includes committed changes since the merge base of REF and HEAD. Deletions
anchor on the surviving lines on either side. Untouched functions in a modified
file are not queries. Each supplied repository must have HEAD for change scans,
and must resolve REF for base scans.
Python decorator lines belong to the function's reported span and select it on
change; minimum line counts still measure the underlying definition. Symlink
aliases share the canonical file's identity, with its regular path preferred.

`match FILE:LINE` finds the narrowest extracted form containing that line and
searches for similar forms elsewhere. The target must belong to a supplied Git
repository. An explicitly named target can be a test or ignored file; it does
not make other tests or ignored files part of the corpus.
Size filters apply to the comparison corpus; the explicit target remains a
query even when it is smaller than those filters.

## Across the estate

Supply roots explicitly; optional labels distinguish repositories with the same
directory name. No estate paths are built into the tool.

```sh
afford-dry scan --cross-repo --threshold 0.9 --limit 20 \
  sokrates=/path/to/sokrates krepis=/path/to/Krepis hive=/path/to/hive

afford-dry match /path/to/sokrates/core/src/example.py:42 \
  sokrates=/path/to/sokrates krepis=/path/to/Krepis hive=/path/to/hive
```

Results include repository labels, paths, function names, line spans, node
counts, and structural similarity. The left side of a focused result is a query.
Compare the source and its callers before deciding to reuse or consolidate it:
similar shape does not establish equivalent behavior or the right ownership
boundary. In particular, literal values, field names, local names, and argument
type names normalize away. Callee names, operators, and tree shape stay.
Expressions inside TypeScript template substitutions and Rust macro names stay
significant. Python source encoding declarations are honored.

## Output and scope

- Compact JSON is the default. `--text` produces a short human-readable report.
- `--limit` bounds returned pairs (default 50 for scans, 20 for matches). The
  total `stats.matching_pairs` and `truncated` flag disclose omitted results.
  Pairs sort by descending score, then the smaller function's node count
  descending, then stable source order: substantial copies precede tiny helpers.
- Similarity below 1.0 is not transitive: A~B and B~C do not make A~C, so do
  not chain pairs into clusters. `groups` lists forms with identical normalized
  structure, where every pair scores 1.0. They are ordered by member count, then
  node count. `--limit` bounds them too; `stats.identical_groups` gives the total.
  A group's pairs are counted and listed like any other pair, under the same
  focus, `--cross-repo`, and `--limit` rules. A focused or cross-repo search only
  merges members through pairs it reports, so not every member pair need appear.
- `stats.comparisons` counts exact fingerprint intersections;
  `stats.possible_pairs` counts the eligible exhaustive search space.
- Exit 0 means a complete scan, including one that found candidates. Exit 1
  means a partial scan, with `complete: false` and file diagnostics. Exit 2
  means invalid input. Invalid syntax excludes the entire file, rather than
  letting a recovered parse look like complete evidence. `complete` means every
  eligible file was parsed, not that every copy was found: a small change high
  in a short function, such as a type assertion, can move a near-copy below
  the threshold.
- Git supplies tracked and non-ignored untracked files. Deleted tracked files,
  dependency/build directories, declarations (`.d.ts`, `.d.mts`, `.d.cts`), and tests are omitted.
  `--include-tests` includes tests; repeat `--exclude GLOB` to omit additional
  tracked/generated paths (repository-relative Python glob semantics, including
  recursive `**`). For example, `--exclude '.github/**'` omits shared CI helpers.
- Functions and methods are compared only within their language. Python nested
  functions, TypeScript callbacks inside functions, and Rust closures remain in
  their enclosing function. TypeScript top-level assigned arrows are candidates;
  Rust `mod tests` functions are excluded.
- Functions whose body executes nothing are omitted: only docstrings, comments,
  `pass`, `...`, or `raise NotImplementedError`, or an empty block. With names
  and types normalized away, such a form is just its parameter-list shape, and
  it would match unrelated protocol methods. `match` on one is an input error.
- Defaults are `--threshold 0.82 --min-lines 4 --min-nodes 20` for scans.
  `match` defaults to minimums of 1 to allow small reuse queries.
- Analysis reads local files and Git state. It writes no repository metrics,
  calls no models, and uses no network at runtime. Four parser grammars ship in
  the installed wheels. Clojure and Java are not supported or dependencies.

## How it improves on Dryer

The language extraction and normalization are adapted from
[Robert C. Martin's Dryer](https://github.com/unclebob/dryer), commit
`66ff6d21a42c04afcad89c78a80066176d1294b0`, under its 0BSD license (see
[NOTICE](NOTICE)). The structural score remains Jaccard similarity of subtree
fingerprint sets.

The matcher uses a shared rare-first token order and Jaccard prefix/size bounds
to avoid comparing unrelated forms. These are exact filters, not approximate
nearest-neighbor retrieval. Every returned score is computed from the full
fingerprint sets. Focused scans query only selected forms against the full
corpus. No persistent cache or daemon is needed.

The four grammar wheels require no runtime downloads. Two known gaps in their
published grammars are handled at parsing: TypeScript `export type *` modifiers
and Python 3.13 type-parameter defaults. Only that non-runtime type metadata is
erased in the parser's input; offsets stay intact, and source files are untouched.
Python's native parser validates defaults before this erasure. Other parse errors
are reported as incomplete coverage, not asserted to be defects in your source.

## Development

```sh
uv sync --locked
ruff format --check .
ruff check .
ty check
uv run pytest -q
```
