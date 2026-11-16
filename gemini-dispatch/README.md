# gemini-dispatch

Hand one self-contained task to Gemini through the Antigravity CLI (`agy`) and get back a
verdict you can trust. For an agent that wants a multimodal second opinion or a delegated
writing job without reimplementing the same checks every time.

`agy -p` reports `status: SUCCESS` when a turn ended, not when the work was done. This tool
turns that into an exit status that means something:

| Exit | Meaning |
| --- | --- |
| 0 | There is a reply to read, and every `--expect` file was written during this dispatch |
| 1 | `agy` is missing or could not start, printed no result object, or reported a status other than `SUCCESS` |
| 2 | Usage error (bad path, no prompt) |
| 3 | Time ran out: agy stopped at `--timeout` mid-turn, or printed nothing for 60 s more and was killed with its helpers |
| 4 | Gemini returned no text (typical after a denied tool call) |
| 5 | A file named with `--expect` is absent, empty, or older than the dispatch |

## Install

```sh
uv tool install ./gemini-dispatch   # from the repo root
# or, for development:
cd gemini-dispatch && uv sync
```

Requires `agy` on PATH, signed in. No API key is handled here.

## Use

```sh
gemini-dispatch --cwd JOB --prompt-file JOB/brief.txt
gemini-dispatch --cwd JOB --image before.png --image after.png --prompt "What changed between Image 1 and Image 2?"
gemini-dispatch --cwd JOB --prompt-file brief.txt --out JOB/draft.txt --strip-fence
gemini-dispatch --cwd JOB --prompt-file edit.txt --image plate.png --expect result.png --timeout 900
echo "Summarise notes.md in five lines." | gemini-dispatch --cwd JOB
```

- **stdout** carries Gemini's reply and nothing else. With `--out FILE` the reply goes to the file,
  only when the dispatch is ok, so a failure never overwrites the last good file.
- **stderr** carries one status line (`outcome`, agy `status`, seconds, tokens, log path), the
  reason for a failure, each denied action, and up to 20 lines of agy's own warnings.
- **`JOB/.gemini-dispatch/<time>-<label>.json`** keeps the whole exchange: prompt, result object,
  or raw output when there was none. Timeouts and unparseable output are logged too.

| Option | Effect |
| --- | --- |
| `--cwd DIR` | Required. Where Gemini works and may write. Use the job's own folder |
| `--prompt`, `--prompt-file`, stdin | The task text; exactly one source |
| `--image F` (repeatable) | Named "Image N" at the top of the prompt, in order |
| `--add-dir D` (repeatable) | Opens another directory to Gemini, with the mode's write rights |
| `--out FILE` | Reply to a file instead of stdout; its folder must exist |
| `--strip-fence` | Drop one Markdown fence when it wraps the whole reply |
| `--expect FILE` (repeatable) | Relative to `--cwd` and inside it; must be written during the dispatch |
| `--allow-empty` | Accept a dispatch with no reply text (file-only jobs) |
| `--model`, `--effort`, `--mode` | Passed to agy; agy's defaults when unset. `agy models` lists ids |
| `--timeout S` | Passed to agy as `--print-timeout` (default 600) |
| `--label NAME` | Names the log file |
| `--dangerously-skip-permissions` | Passes agy's flag of the same name; only with the human's explicit approval |

## What Gemini can see

Only `--cwd` and any `--add-dir`. An image outside `--cwd` is copied to
`.gemini-dispatch/inputs/NN-<name>` and named by that relative path, so neither the image's
folder nor your home path enters the prompt. In `accept-edits` mode Gemini may edit files in
every directory it can see: pass `--add-dir` only for folders it may change, and inline
reference text into the prompt instead when the source must stay untouched.

## Writing the task

Observed with agy 1.5.9; check again when agy changes.

- Make the prompt self-contained: facts, rules and file names inline. Gemini has no other context.
- For a pure answer say "Look at the images only. Do not run shell commands. Write no files."
  The default mode denies shell commands, and a denied command often ends the turn with an empty
  reply (exit 4).
- For a written artifact, give the exact filename inside `--cwd` and pass it to `--expect`.
- Text and vision replies took 15 to 60 s, an image generation about 3 min, a long structured
  writing task with a large inlined guide several minutes. Run long jobs in the background.
- When the account lacks `auto` mode agy warns and falls back to its default mode; the warning
  shows up on stderr as an `[agy]` line.
- The prompt travels as one command-line argument: on Linux that caps it near 128 KB.
- Read the reply before acting on it. Exit 0 says a reply exists, not that it is right.
