"""Probe protocol and ProbeResult dataclass — shared contract for all probes."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProbeResult:
    fields: dict
    ok: bool
    error: str | None = None


class Probe(Protocol):
    name: str

    def run(self) -> ProbeResult: ...
