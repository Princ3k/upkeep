"""Verify that an extracted change is actually in the document it came from.

This is the one check that matters when a model reads a migration guide. A
missed change is a gap; an invented one is a corrupting patch written from a
document that never said what it claims. `before`/`after` code is the sharpest
case — something downstream rewrites real source from it.

So grounding runs at extraction time, not only in an eval: `spec_from_guide`
drops what it cannot find in the text. It is deliberately crude, checking that
the strings appear rather than judging whether they were understood, because a
crude check that always runs beats a clever one that runs at review time.

It cannot see the opposite failure. A guide whose changes were silently skipped
scores perfectly here, so recall needs a human or an independent grader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from upkeep.models import Change, MigrationSpec

# Lines too generic to prove anything by their presence.
_NOISE = {"{", "}", "(", ")", "end", "});", ")", "};", "]", "[", "", "..."}


def _normalise(text: str) -> str:
    """Collapse whitespace so indentation differences don't read as fabrication."""
    return " ".join(text.split())


def _squeeze(text: str) -> str:
    """Drop whitespace entirely, for comparing code.

    A model quoting a multi-line block often joins it onto one line — real
    Gemini output turned the guide's four-line `Session.new(...)` call into a
    single line. That is reformatting, not invention, and collapsing whitespace
    only between lines still reported it as fabricated. Comparing code with
    whitespace removed fixes that without weakening the check: every mutation in
    tests/test_grounding.py changes a character that is not whitespace.
    """
    return re.sub(r"\s+", "", text)


def _significant_lines(code: str) -> list[str]:
    out = []
    for raw in code.splitlines():
        line = raw.strip()
        if not line or line in _NOISE:
            continue
        if line.startswith(("#", "//", "*", "/*")):
            continue  # comments are commonly reworded; don't fail on them
        out.append(line)
    return out


@dataclass
class ChangeGrounding:
    change: Change
    kind: str
    label: str
    checks: dict[str, bool] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return all(self.checks.values())


@dataclass
class GroundingReport:
    guide: str
    results: list[ChangeGrounding] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def grounded(self) -> int:
        return sum(1 for r in self.results if r.grounded)

    @property
    def fabricated(self) -> list[ChangeGrounding]:
        return [r for r in self.results if not r.grounded]

    @property
    def rate(self) -> float:
        return self.grounded / self.total if self.total else 1.0


def _check_change(change: Change, haystack: str, raw: str, squeezed: str) -> ChangeGrounding:
    label = (
        getattr(change, "symbol", None)
        or getattr(change, "path", None)
        or getattr(change, "op", None)
        or getattr(change, "param", None)
        or "<unnamed>"
    )
    result = ChangeGrounding(change=change, kind=change.kind, label=str(label))

    symbol = getattr(change, "symbol", None)
    if symbol:
        # A guide may write `Session#serialize` or spell out the receiver; accept
        # the bare identifier so a correct extraction isn't failed on notation.
        leaf = symbol.replace("#", ".").replace("::", ".").split(".")[-1]
        found = _normalise(symbol) in haystack or leaf in raw
        result.checks["symbol_in_guide"] = found
        if not found:
            result.missing.append(f"symbol {symbol!r}")

    for attribute in ("before", "after"):
        code = getattr(change, attribute, None)
        if not code:
            continue
        lines = _significant_lines(code)
        absent = [ln for ln in lines if _squeeze(ln) not in squeezed]
        result.checks[f"{attribute}_in_guide"] = not absent
        result.missing += [f"{attribute}: {ln}" for ln in absent[:3]]

    if not result.checks:
        # Prose-only records (semantics_changed) carry nothing quotable.
        result.checks["nothing_to_verify"] = True
    return result


def check_grounding(spec: MigrationSpec, guide_text: str, *, guide: str = "") -> GroundingReport:
    haystack = _normalise(guide_text)
    squeezed = _squeeze(guide_text)
    return GroundingReport(
        guide=guide,
        results=[
            _check_change(c, haystack, guide_text, squeezed) for c in spec.changes
        ],
    )


def drop_ungrounded(spec: MigrationSpec, guide_text: str) -> tuple[MigrationSpec, GroundingReport]:
    """Return the spec with unverifiable changes removed, plus the report."""
    report = check_grounding(spec, guide_text)
    kept = [r.change for r in report.results if r.grounded]
    return spec.model_copy(update={"changes": kept}), report
