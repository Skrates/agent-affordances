"""Dryer-compatible internal forms and compact reports carrying source evidence."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, computed_field


@dataclass(frozen=True)
class Entry:
    language: str
    file: str
    start_line: int
    end_line: int
    nodes: int
    fingerprints: frozenset[str]


class Location(BaseModel):
    repo: str
    file: str
    symbol: str
    start_line: int
    end_line: int
    nodes: int


@dataclass(frozen=True)
class Form:
    repo: str
    path: Path
    symbol: str
    entry: Entry

    def location(self) -> Location:
        return Location(
            repo=self.repo,
            file=self.entry.file,
            symbol=self.symbol,
            start_line=self.entry.start_line,
            end_line=self.entry.end_line,
            nodes=self.entry.nodes,
        )


class Match(BaseModel):
    score: float
    language: str
    left: Location
    right: Location


class Group(BaseModel):
    """Forms with identical normalized structure: every pair among them scores 1.0."""

    language: str
    nodes: int
    members: list[Location]


class Diagnostic(BaseModel):
    repo: str
    file: str
    message: str


class Stats(BaseModel):
    files: int = 0
    forms: int = 0
    queries: int = 0
    possible_pairs: int = 0
    comparisons: int = 0
    matching_pairs: int = 0
    identical_groups: int = 0


class Report(BaseModel):
    scope: str
    roots: dict[str, str]
    threshold: float
    complete: bool
    stats: Stats = Field(default_factory=Stats)
    matches: list[Match] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @computed_field
    @property
    def truncated(self) -> bool:
        stats = self.stats
        return stats.matching_pairs > len(self.matches) or stats.identical_groups > len(self.groups)
