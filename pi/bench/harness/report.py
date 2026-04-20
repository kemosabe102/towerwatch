"""Pass/fail aggregation, local JSON report, and stdout table."""

import json
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .state import REPORTS_DIR


@dataclass
class TestResult:
    name: str
    status: str          # "pass", "fail", "expected_failure", "error", "skipped"
    expected_failure: bool = False
    duration_s: float = 0.0
    evidence: dict = field(default_factory=dict)
    error_msg: Optional[str] = None
    started_at: float = field(default_factory=time.time)


class Report:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._results: list[TestResult] = []
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = REPORTS_DIR / f"report_{run_id}.json"

    def add(self, result: TestResult) -> None:
        self._results.append(result)
        self._flush()

    def add_note(self, text: str) -> None:
        self._results.append(TestResult(
            name="__note__",
            status="note",
            evidence={"text": text},
        ))
        self._flush()

    def _flush(self) -> None:
        data = {
            "run_id": self.run_id,
            "generated_at": time.time(),
            "results": [asdict(r) for r in self._results],
        }
        self._path.write_text(json.dumps(data, indent=2))

    def print_table(self) -> None:
        _STATUS_ICON = {
            "pass": "PASS",
            "fail": "FAIL",
            "expected_failure": "XFAIL",
            "error": "ERROR",
            "skipped": "SKIP",
            "note": "NOTE",
        }
        print(f"\n{'Test':<40} {'Status':<8} {'Duration':>10}")
        print("-" * 62)
        for r in self._results:
            icon = _STATUS_ICON.get(r.status, r.status.upper())
            dur = f"{r.duration_s:.1f}s" if r.duration_s else ""
            print(f"{r.name:<40} {icon:<8} {dur:>10}")
        print()
        summary = self._summary()
        print(f"Summary: {summary['passed']} pass  {summary['xfail']} xfail  {summary['failed']} fail  {summary['skipped']} skip")
        print(f"Report:  {self._path}")

    def _summary(self) -> dict:
        """Compute pass/xfail/fail/skip counts."""
        return {
            'passed': sum(1 for r in self._results if r.status == "pass"),
            'xfail': sum(1 for r in self._results if r.status == "expected_failure"),
            'failed': sum(1 for r in self._results if r.status in ("fail", "error")),
            'skipped': sum(1 for r in self._results if r.status == "skipped"),
        }

    @property
    def path(self) -> Path:
        return self._path

    @property
    def has_unexpected_failures(self) -> bool:
        return any(r.status in ("fail", "error") for r in self._results)
