# hive-attention

The agent-facing name of [Hive Attention](https://github.com/Skrates/hive/blob/main/docs/adr/0004-human-attention-queue.md):
`hive-attention <args…>` is exactly `hive attention <args…>`.

The launcher replaces its process with the installed `hive` (no shell, no child), so
stdout/stderr, signals and exit codes are Hive's own. It owns no database, HTTP client,
command schema, ordering or retry semantics — every rule lives in Hive. A missing `hive`, or a
`HIVE_BIN` that resolves back to this launcher, exits 127 with a diagnostic; a `hive` that is
found but cannot be executed (broken interpreter line, bad format, no-exec mount) exits 126.

Tested against Hive attention schema **1** (`hive attention capabilities` reports the live
broker's). An agent-affordances version alone says nothing about which Hive a seat has; check
`hive-attention doctor`.

## Install

```sh
uv tool install --from ./attention hive-attention          # --force to upgrade in place
```

On a seat host without GitHub credentials, ship the directory and install from it:
`git archive <sha> attention | ssh <host> 'mkdir -p /tmp/ha && tar -x -C /tmp/ha'`, then as the seat
user `uv tool install --force --from /tmp/ha/attention hive-attention`. The launcher needs the
`hive` CLI of the same host on PATH (deploy-hive installs it as `~/.local/bin/hive`).

`HIVE_BIN` overrides which Hive executable is used (a path, or a name on PATH); otherwise the
`hive` on PATH.

## Use (seat side)

```sh
# Bind once per work context while the turn's dispatch token is valid…
hive-attention bind --project birthday-film --work parents-likeness-round-2 \
  --return actor-context --out ./attention-source.json
# …then ring the bell with a saved envelope (reuse the same file on retry: same command id).
hive-attention summon --source-file ./attention-source.json --file ./summon.json
hive-attention show 42 --json
```

`summon.json` is a complete envelope:

```json
{
  "schema_version": 1,
  "command_id": "8ba99410-c894-4632-aea6-b33a06a6b015",
  "action": "summon",
  "payload": {
    "human_id": "hakon",
    "project_id": "birthday-film",
    "work_id": "parents-likeness-round-2",
    "intervention_key": "candidate-selection",
    "kind": "selection",
    "title": "Choose the next birthday-film render",
    "requested_action": "Select candidate A, B, C or D for the next video batch.",
    "context": "Round 2. Same lighting and composition; compare likeness and expression.",
    "unblocks": "The next video-generation batch.",
    "artifacts": [],
    "estimate_minutes": 8,
    "deadline_at": null,
    "predecessor_item_id": null
  }
}
```

A summoning turn **ends normally** after the summons — it never waits for the answer. The
answer arrives later as an ordinary Hive wake (`Attention return ATN-<id> …`): context-restored
for headless seats, or into the exact session when a live surface can honour it.

Recovery, when a request's outcome is unknown (exit 6):

- `summon`: `hive-attention receipt <command_id>`; if absent, rerun the **same** envelope file.
- `bind`: rerun it with `--command-id <id>` from the same turn (same dispatch token) or the same
  live session; once neither is in reach, the operator recovers it with
  `hive attention bind --operator --command-id <id> …` on that edge.

When to summon, how to choose keys, and how to handle the response: the `hive-attention`
skill in weave-doctrine.

## Use (human side)

The human pulls from an operator shell — `HIVE_BROKER_URL` and `HIVE_ADMIN_TOKEN` set; the CLI
refuses the operator credential inside an agent's environment:

```sh
hive attention next                      # claims the next item (FIFO) and prints its brief
hive attention resolve <id> --expected-revision <n> --outcome selected --response "B." --next
hive attention queue                     # active, eligible, deferred, returns in flight
```

With the broker's Slack front end on (Hive ADR-0004 Amendment 1), each summons also arrives as one
DM card from the Hive app — **Take next** is the main button, **Answer this one** a rare override —
and the app's Home tab shows the queue.

## Develop

```sh
uv sync && ruff format --check . && ruff check . && ty check && uv run pytest -q
```
