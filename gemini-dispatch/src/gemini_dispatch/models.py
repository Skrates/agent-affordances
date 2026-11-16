"""What agy prints in `--output-format json`, and what a dispatch came to.

The agy fields are the ones observed from agy 1.5.9; unknown keys are kept so the saved log
stays a faithful copy of what agy said.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Mode(StrEnum):
    accept_edits = "accept-edits"
    plan = "plan"
    auto = "auto"


class Effort(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


class Usage(BaseModel):
    model_config = ConfigDict(extra="allow")

    total_tokens: int | None = None


class DeniedAction(BaseModel):
    """A tool call agy refused under the session's mode, e.g. a shell command."""

    model_config = ConfigDict(extra="allow")

    action: str | None = None
    display_name: str | None = None

    def label(self) -> str:
        name = self.display_name or "unnamed tool"
        return f"{name} ({self.action})" if self.action else name


class AgyResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str | None = None
    response: str = ""
    conversation_id: str | None = None
    duration_seconds: float | None = None
    error: str | None = None
    finished_reason: str | None = None
    denied_actions: list[DeniedAction] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)

    @field_validator("response", mode="before")
    @classmethod
    def _null_response_is_empty(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("denied_actions", mode="before")
    @classmethod
    def _null_denials_are_none(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("usage", mode="before")
    @classmethod
    def _null_usage_is_empty(cls, value: object) -> object:
        return {} if value is None else value


class Outcome(StrEnum):
    ok = "ok"
    agy_failed = "agy-failed"
    timeout = "timeout"
    empty_reply = "empty-reply"
    missing_file = "missing-file"


#: The process exit status for each outcome. 2 is left to usage errors (Typer's own).
EXIT_CODES: dict[Outcome, int] = {
    Outcome.ok: 0,
    Outcome.agy_failed: 1,
    Outcome.timeout: 3,
    Outcome.empty_reply: 4,
    Outcome.missing_file: 5,
}


class Request(BaseModel):
    cwd: Path
    prompt: str
    images: list[Path] = Field(default_factory=list)
    add_dirs: list[Path] = Field(default_factory=list)
    model: str | None = None
    effort: Effort | None = None
    mode: Mode = Mode.accept_edits
    timeout: int = 600
    label: str = "task"
    expect: list[str] = Field(default_factory=list)
    allow_empty: bool = False
    strip_fence: bool = False
    skip_permissions: bool = False


class Dispatch(BaseModel):
    """One finished dispatch: the verdict, Gemini's reply, and where the evidence is."""

    outcome: Outcome
    detail: str = ""
    reply: str = ""
    result: AgyResult | None = None
    agy_stderr: str = ""
    seconds: float = 0.0
    log: Path
