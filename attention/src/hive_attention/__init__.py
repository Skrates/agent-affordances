"""`hive-attention`: the agent-facing name of Hive Attention, forwarding to `hive attention`."""

# The Hive attention contract this launcher was tested against (Hive ADR-0004,
# `hive attention capabilities` reports the live value). The launcher itself is
# version-agnostic: it forwards bytes and never interprets them.
TESTED_HIVE_ATTENTION_SCHEMA = 1
