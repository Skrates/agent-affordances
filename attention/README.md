# hive-attention

The agent-facing name of [Hive Attention](https://github.com/Skrates/hive/blob/main/docs/adr/0004-human-attention-queue.md):
`hive-attention <args…>` is exactly `hive attention <args…>`.

The launcher replaces its process with the installed `hive` (no shell, no child), so
stdout/stderr, signals and exit codes are Hive's own. It owns no database, HTTP client,
command schema, ordering or retry semantics — every rule lives in Hive. A missing `hive`, or a
`HIVE_BIN` that resolves back to this launcher, exits 127 with a diagnostic.

Tested against Hive attention schema **1** (`hive attention capabilities` reports the live
broker's). An agent-affordances version alone says nothing about which Hive a seat has; check
`hive-attention doctor`.

## Install

```sh
uv tool install --from ./attention hive-attention
```

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

When to summon, how to choose keys, and how to handle the response: the `hive-attention`
skill in weave-doctrine.

## Develop

```sh
uv sync && ruff format --check . && ruff check . && ty check && uv run pytest -q
```
