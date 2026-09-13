"""Decide, per call site, how a change gets handled.

Routing is per site, not per repo or per change: one MigrationSpec can produce
three Tier A edits and a Tier C escalation inside the same file.

The bias is deliberate. Every uncertainty routes *down* — toward a human, never
toward a speculative patch. Losing volume is survivable; losing trust isn't.
"""

from __future__ import annotations

import re

from upkeep.models import (
    CallSite,
    Change,
    EndpointRemoved,
    FieldRenamed,
    MigrationSpec,
    ParamRequiredAdded,
    SemanticsChanged,
    SiteKind,
    Tier,
    WorkItem,
)
from upkeep.patch.registry import IMPLEMENTED_RULES

RENAMEABLE = {SiteKind.attribute, SiteKind.subscript, SiteKind.kwarg}


def _route(change: Change, site: CallSite) -> tuple[Tier, str | None, str]:
    if isinstance(change, SemanticsChanged):
        return Tier.C, None, "behavioural change with no mechanical equivalent"

    if isinstance(change, FieldRenamed):
        if not site.rooted:
            return (
                Tier.C,
                None,
                "cannot prove this expression is the provider's object",
            )
        if site.kind not in RENAMEABLE:
            return Tier.C, None, f"unsupported site kind {site.kind.value}"
        return Tier.A, "rename_field", "unambiguous rename at a proven call site"

    if isinstance(change, ParamRequiredAdded):
        if change.safe_default is None:
            return Tier.B, None, "new required parameter has no safe default"
        return Tier.A, "add_required_param", "new required parameter has a safe default"

    if isinstance(change, EndpointRemoved):
        if change.replacement is None:
            return Tier.C, None, "endpoint removed with no documented replacement"
        return Tier.B, None, "endpoint replaced; call shape must be rewritten"

    return Tier.C, None, "unrecognised change kind"


VERSION_SEGMENT = re.compile(r"v\d+|\d{4}-\d{2}-\d{2}")


def _resource_names(path: str) -> set[str]:
    """Resource tokens for an API path, e.g. `/v1/payment_intents` ->
    {payment_intents, payment_intent, paymentintents, paymentintent}."""
    segments = [
        s.lower()
        for s in path.strip("/").split("/")
        if s and not s.startswith("{") and not VERSION_SEGMENT.fullmatch(s)
    ]
    names: set[str] = set()
    for segment in segments:
        for candidate in (segment, segment.rstrip("s")):
            if candidate:
                names.add(candidate)
                names.add(candidate.replace("_", ""))
    return names


def _call_targets_path(symbol: str, path: str) -> bool:
    """Does this call plausibly hit `path`?

    A removed endpoint affects the calls that reach it, not every call in the
    repository. Matching on the resource name keeps `acme.Invoice.retrieve` out
    of an escalation about `/v1/charges` — a false escalation costs exactly as
    much reviewer trust as a false patch.
    """
    names = _resource_names(path)
    if not names:
        return False
    tokens = {t.lower() for t in re.split(r"[._]", symbol) if t}
    if tokens & names:
        return True
    compact = symbol.lower().replace("_", "").replace(".", "")
    return any(len(name) > 3 and name in compact for name in names)


def _affects(change: Change, site: CallSite) -> bool:
    if isinstance(change, FieldRenamed):
        return site.name == change.old_name
    if isinstance(change, SemanticsChanged):
        return site.name == change.op.rsplit(".", 1)[-1]
    if site.kind is not SiteKind.call:
        return False
    if isinstance(change, ParamRequiredAdded):
        return _call_targets_path(site.name, change.op.split(" ", 1)[-1])
    if isinstance(change, EndpointRemoved):
        return _call_targets_path(site.name, change.path)
    return False


def plan(
    spec: MigrationSpec,
    sites: list[CallSite],
    *,
    require_vectors: bool = True,
) -> list[WorkItem]:
    """Join a spec against an index and assign a tier to each affected site.

    `require_vectors` enforces the policy that an unverifiable change is never
    patched automatically. A spec shipping no test material can still tell a
    human what to look at — it just cannot open a pull request.
    """
    unverifiable = require_vectors and not spec.vectors
    items: list[WorkItem] = []

    for change in spec.changes:
        for site in sites:
            if not _affects(change, site):
                continue
            tier, rule, reason = _route(change, site)
            if tier is Tier.A and rule not in IMPLEMENTED_RULES:
                tier, reason = Tier.B, f"{reason}, but no codemod implements it yet"
                rule = None
            if unverifiable and tier is not Tier.C:
                tier, rule = Tier.C, None
                reason = "spec ships no test vectors, so no patch can be proven"
            items.append(
                WorkItem(site=site, change=change, tier=tier, rule=rule, reason=reason)
            )

    items.sort(key=lambda i: (i.site.file, i.site.line, i.site.column))
    return items
