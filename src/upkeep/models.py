"""The vocabulary every stage shares.

The MigrationSpec is the primitive: a normalized, machine-actionable description
of what changed between two versions of a provider's surface. Detection produces
one; everything downstream consumes one.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


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


class SemanticsChanged(_Change):
    """The catch-all for anything upkeep found but refuses to interpret.

    Emitting this rather than guessing is the point. Every SemanticsChanged is a
    Tier C escalation by construction.
    """

    kind: Literal["semantics_changed"] = "semantics_changed"
    op: str
    note: str


Change = Annotated[
    Union[FieldRenamed, EndpointRemoved, ParamRequiredAdded, SemanticsChanged],
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
