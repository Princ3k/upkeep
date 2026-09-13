"""The vocabulary every stage shares.

The MigrationSpec is the primitive: a normalized, machine-actionable description
of what changed between two versions of a provider's surface. Detection produces
one; everything downstream consumes one.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    breaking = "breaking"
    deprecation = "deprecation"
    additive = "additive"


class Tier(str, Enum):
    """How a single affected call site gets handled.

    A — deterministic AST transform, no model involved.
    B — model-assisted patch, ships as a draft PR. Not implemented in v0.
    C — analysis only; upkeep opens an issue and writes no code.
    """

    A = "A"
    B = "B"
    C = "C"


# --------------------------------------------------------------------------
# Change records
# --------------------------------------------------------------------------


class _Change(BaseModel):
    model_config = ConfigDict(frozen=True)


class FieldRenamed(_Change):
    kind: Literal["field_renamed"] = "field_renamed"
    path: str
    to: str
    pending: bool = False
    """True when the old field still exists alongside the new one.

    This is how real providers rename. Stripe's Accounts v2 migration added
    `customer_account` beside `customer` in 26 schemas, and `quantity_decimal`
    beside `quantity`, announcing both in prose. Nothing was removed, so a diff
    that only compares key sets sees pure addition and reports nothing — which
    is exactly what upkeep did, and why its first measurement of "how often do
    providers rename" came back zero.
    """

    inferred: bool = True
    """True when upkeep guessed this pairing from a spec diff rather than being
    told it by the provider.

    Measured against a year of Stripe's real spec, 2 of 5 inferred renames were
    wrong — a currency map losing `bgn` and gaining `gip`, and a `coupon` field
    paired to `customer_account` when the true successor was `promotion`. Both
    would have shipped as ready-to-merge patches. So an inferred rename is
    evidence, not instruction: it never reaches Tier A on its own.
    """

    @property
    def old_name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    @property
    def new_name(self) -> str:
        return self.to.rsplit(".", 1)[-1]


class EndpointRemoved(_Change):
    kind: Literal["endpoint_removed"] = "endpoint_removed"
    method: str
    path: str
    replacement: str | None = None


class ParamRequiredAdded(_Change):
    kind: Literal["param_required_added"] = "param_required_added"
    op: str
    param: str
    safe_default: Any | None = None


class SymbolRemoved(_Change):
    """A class, method, or constant the SDK no longer exposes.

    Migration guides are mostly made of these, and the schema had no word for
    one: Shopify's v16 notice removes `Session#serialize` and
    `Session.deserialize`, and both landed in `semantics_changed` alongside a
    Ruby version bump, which loses the one thing that makes them actionable —
    a name the indexer can search for.

    No codemod writes these. The value is impact analysis: telling someone the
    fourteen places they call a method that is about to stop existing.
    """

    kind: Literal["symbol_removed"] = "symbol_removed"
    symbol: str
    """Fully qualified as the guide writes it, e.g. `Session#serialize`."""
    replacement: str | None = None
    note: str = ""

    @property
    def leaf(self) -> str:
        """The bare identifier, for matching against indexed call sites."""
        parts = self.symbol.replace("#", ".").replace("::", ".").split(".")
        return parts[-1]


LANGUAGE_ALIASES = {
    "ts": "typescript", "tsx": "typescript", "js": "typescript",
    "jsx": "typescript", "javascript": "typescript", "node": "typescript",
    "py": "python", "rb": "ruby", "golang": "go", "cs": "csharp",
}
"""Spellings that mean the same thing to a call-site index.

Real model output used `ts` and `js` where the corpus said `typescript`, and an
exact-match guard read that as five wrong-language errors across two runs. They
were not errors. JavaScript folds into TypeScript here because one indexer
matches call sites in both.
"""


def normalise_language(language: str | None) -> str | None:
    if language is None:
        return None
    key = language.strip().lower()
    return LANGUAGE_ALIASES.get(key, key)


class CallPatternChanged(_Change):
    """The call shape changed, shown as before-and-after code.

    This is the richest thing a migration guide carries and the thing no schema
    diff can express. Shopify's v16 notice does not just say `Session.deserialize`
    is gone — it shows the old call and the shape that replaces it.

    It is an *illustration*, not a rule. One example cannot tell you how the
    change applies to a call site with different variable names, different
    surrounding structure, or arguments the sample never shows, which is exactly
    why this routes to Tier B and never to a deterministic codemod. Rewriting a
    call shape from an example is the one job in this pipeline that genuinely
    needs a model.

    `language` is required and load-bearing. A Ruby sample must never be applied
    to a Python call site that happens to share a method name, so the planner
    refuses to match across languages.
    """

    kind: Literal["call_pattern_changed"] = "call_pattern_changed"
    symbol: str | None = None
    """The symbol whose call shape changed, when the guide names one.

    Optional on purpose. A call pattern is defined by its before and after, and
    requiring this separately threw away 19 correct extractions in one eval run
    — records carrying exactly the right `before` and `after` and no `symbol`.
    When it is absent, `leaf` falls back to the last dotted identifier in the
    `before` code, which is what a call-site index can actually match on.
    """

    language: str
    before: str
    after: str
    note: str = ""

    @model_validator(mode="after")
    def _usable_as_an_example(self) -> "CallPatternChanged":
        object.__setattr__(self, "language", normalise_language(self.language))
        if not self.before.strip():
            raise ValueError("a call pattern needs a `before` to match against")
        if not self.after.strip():
            raise ValueError(
                "a call pattern needs an `after`; a guide that shows only the old "
                "call describes a removal — use symbol_removed instead"
            )
        if self.before.strip() == self.after.strip():
            raise ValueError("`before` and `after` are identical, so nothing changed")
        return self

    @property
    def leaf(self) -> str | None:
        """The bare identifier a call-site index matches on.

        Derived from `before` when no symbol was given. Best effort: the last
        dotted identifier in the old call is usually the thing that changed.
        """
        source = self.symbol
        if not source:
            matches = re.findall(r"[.\#]\s*([A-Za-z_][A-Za-z0-9_]*)", self.before)
            if not matches:
                return None
            return matches[-1]
        parts = source.replace("#", ".").replace("::", ".").split(".")
        return parts[-1]


class SemanticsChanged(_Change):
    """The catch-all for anything upkeep found but refuses to interpret.

    Emitting this rather than guessing is the point. Every SemanticsChanged is a
    Tier C escalation by construction.
    """

    kind: Literal["semantics_changed"] = "semantics_changed"
    op: str
    note: str


Change = Annotated[
    Union[
        FieldRenamed,
        EndpointRemoved,
        ParamRequiredAdded,
        SymbolRemoved,
        CallPatternChanged,
        SemanticsChanged,
    ],
    Field(discriminator="kind"),
]


class MigrationSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    provider: str
    from_version: str = Field(alias="from")
    to_version: str = Field(alias="to")
    severity: Severity
    effective: str | None = None
    changes: list[Change] = Field(default_factory=list)
    vectors: list[str] = Field(default_factory=list)
    """Test material proving a patch is correct. No vectors, no automatic patch."""

    def field_renames(self) -> list[FieldRenamed]:
        return [c for c in self.changes if isinstance(c, FieldRenamed)]


# --------------------------------------------------------------------------
# Index + plan
# --------------------------------------------------------------------------


class SiteKind(str, Enum):
    attribute = "attribute"
    subscript = "subscript"
    kwarg = "kwarg"
    call = "call"


class CallSite(BaseModel):
    """One place in consumer code that touches the provider's surface."""

    model_config = ConfigDict(frozen=True)

    file: str
    line: int
    column: int
    kind: SiteKind
    name: str
    """The identifier touched — an attribute name, dict key, or keyword arg."""
    expression: str
    language: str = "python"
    """Which language this site is written in, in canonical form.

    The indexer is libcst-only today so this is always Python, but a migration
    guide is written against one SDK in one language: without this, a Ruby
    before/after sample would happily match a Python call site that shares a
    method name.
    """

    rooted: bool
    """True when upkeep can prove this expression descends from a provider call.

    An unrooted site is never patched automatically: `payload["amount"]` in a
    file that imports the SDK might be the provider's object or might be
    anything at all, and guessing wrong is how you break production.
    """


class WorkItem(BaseModel):
    """A call site joined to the change that affects it, with a routing decision."""

    site: CallSite
    change: Change
    tier: Tier
    rule: str | None = None
    reason: str = ""


# --------------------------------------------------------------------------
# Patch + gate
# --------------------------------------------------------------------------


class FilePatch(BaseModel):
    file: str
    diff: str
    new_source: str
    sites_changed: int


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class GateResult(BaseModel):
    checks: list[CheckResult] = Field(default_factory=list)
    confidence: Confidence = Confidence.low

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def delivery(self) -> str:
        """What this result is allowed to ship as. Never 'merge'."""
        if not self.passed:
            return "issue"
        return {"high": "pull_request", "medium": "draft_pull_request"}.get(
            self.confidence.value, "issue"
        )
