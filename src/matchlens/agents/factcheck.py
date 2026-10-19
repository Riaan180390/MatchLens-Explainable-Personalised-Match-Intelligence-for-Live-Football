"""The fact-checker: deterministic verification of agent text against the match data.

Every number an agent writes must trace back to a fact or a value in the
context it was given; no player outside that context may be named; and claims
that reach beyond this match (seasons, careers, history) are rejected because
the engine has no data to back them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
UNVERIFIABLE = re.compile(
    r"\b(season|seasons|career|all-time|history|historic|league table|title race|last week|last year)\b",
    re.IGNORECASE)
ALWAYS_ALLOWED = {0.0, 1.0}


@dataclass
class CheckResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    numbers_checked: int = 0


def _collect(value: Any, numbers: set[float], strings: list[str]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        numbers.add(float(value))
    elif isinstance(value, str):
        strings.append(value)
        for m in NUMBER.findall(value):
            numbers.add(float(m.replace(",", ".")))
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, numbers, strings)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, numbers, strings)


class FactChecker:
    def __init__(self, context: dict, roster: dict[str, str]):
        self.numbers: set[float] = set(ALWAYS_ALLOWED)
        strings: list[str] = []
        _collect(context, self.numbers, strings)
        for n in list(self.numbers):
            if 0 < n <= 1:
                self.numbers.add(round(n * 100, 1))  # 0.57 may be written as 57%
            if n >= 100:
                self.numbers.add(round(n / 1000, 1))  # metres may be written as km
        self.context_text = " ".join(strings)
        self.roster = roster

    def supported(self, n: float) -> bool:
        return any(abs(n - a) <= max(0.051, 0.01 * abs(a)) for a in self.numbers)

    def check(self, *texts: str) -> CheckResult:
        text = " ".join(t for t in texts if t)
        issues: list[str] = []
        found = [float(m.replace(",", ".")) for m in NUMBER.findall(text)]
        for n in found:
            if not self.supported(n):
                issues.append(f"The number {n:g} is not in the facts or match context.")
        for m in UNVERIFIABLE.finditer(text):
            issues.append(f"'{m.group(0)}' is a claim beyond this match; only this match's data is available.")
        for surname, full in self.roster.items():
            if re.search(rf"\b{re.escape(surname)}\b", text) and surname not in self.context_text:
                issues.append(f"{full} is not involved in this moment's context.")
        return CheckResult(passed=not issues, issues=issues, numbers_checked=len(found))
