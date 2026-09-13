"""A flat change record, and the mapping back to the real union.

`MigrationSpec.changes` is a six-way discriminated union with `$ref`
indirection and const-valued literals. Anthropic's structured output takes that
pydantic model directly; other backends constrain output with an OpenAPI subset
where `oneOf` plus a discriminator is the thinnest part.

Rather than give each backend whatever it handles best, both get this one flat
object and the union is rebuilt here. That keeps a provider comparison honest —
otherwise the stronger schema support would be measured as better extraction —
and it means adding a backend is a request-shaping problem, not a schema one.

Rebuilding is also a measurement. A record naming `call_pattern_changed` with no
`after` is a malformed extraction, and `to_change` returns None rather than
inventing a default, so the caller can count them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from upkeep.models import (
    CallPatternChanged,
    Change,
    EndpointRemoved,
    FieldRenamed,
    ParamRequiredAdded,
    SemanticsChanged,
    Severity,
    SymbolRemoved,
)

KIND = Literal[
    "field_renamed",
    "endpoint_removed",
    "param_required_added",
    "symbol_removed",
    "call_pattern_changed",
    "semantics_changed",
]


class FlatChange(BaseModel):
    """Every field any change kind might need, all optional but `kind`."""

    kind: KIND
    path: str | None = Field(None, description="field_renamed: the old field path")
    to: str | None = Field(None, description="field_renamed: the new field path")
    method: str | None = Field(None, description="endpoint_removed: HTTP method")
    endpoint: str | None = Field(None, description="endpoint_removed: URL path")
    op: str | None = Field(None, description="param_required_added / semantics_changed: the operation or subject")
    param: str | None = Field(None, description="param_required_added: parameter name")
    safe_default: str | None = Field(None, description="param_required_added: default the document states, if any")
    symbol: str | None = Field(None, description="symbol_removed / call_pattern_changed: fully qualified symbol")
    replacement: str | None = Field(None, description="symbol_removed: replacement the document names, if any")
    language: str | None = Field(None, description="call_pattern_changed: the SDK's language")
    before: str | None = Field(None, description="call_pattern_changed: the old call, verbatim")
    after: str | None = Field(None, description="call_pattern_changed: the new call, verbatim")
    note: str | None = Field(None, description="the document's own explanation")


class FlatSpec(BaseModel):
    severity: Literal["breaking", "deprecation", "additive"]
    changes: list[FlatChange] = Field(default_factory=list)


def _coerce_default(raw: str | None):
    if raw is None:
        return None
    text = raw.strip()
    if text.lower() in {"none", "null", ""}:
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text


def to_change(flat: FlatChange) -> Change | None:
    """Rebuild the discriminated union, or None if the record is unusable."""
    note = flat.note or ""

    if flat.kind == "field_renamed":
        if not (flat.path and flat.to):
            return None
        return FieldRenamed(path=flat.path, to=flat.to, inferred=False)

    if flat.kind == "endpoint_removed":
        if not (flat.method and flat.endpoint):
            return None
        return EndpointRemoved(
            method=flat.method.upper(), path=flat.endpoint, replacement=flat.replacement
        )

    if flat.kind == "param_required_added":
        if not (flat.op and flat.param):
            return None
        return ParamRequiredAdded(
            op=flat.op, param=flat.param, safe_default=_coerce_default(flat.safe_default)
        )

    if flat.kind == "symbol_removed":
        if not flat.symbol:
            return None
        return SymbolRemoved(
            symbol=flat.symbol, replacement=flat.replacement, note=note
        )

    if flat.kind == "call_pattern_changed":
        # The validator rejects an empty or identical example; a backend that
        # produced one made a malformed record, not a usable change.
        # `symbol` is optional: the pattern is the before/after pair. Demanding
        # it here discarded correct extractions whose only fault was an unset
        # field the model had no reason to fill.
        if not (flat.language and flat.before and flat.after):
            return None
        try:
            return CallPatternChanged(
                symbol=flat.symbol, language=flat.language,
                before=flat.before, after=flat.after, note=note,
            )
        except ValueError:
            return None

    if flat.kind == "semantics_changed":
        if not flat.op:
            return None
        return SemanticsChanged(op=flat.op, note=note)

    return None


def rebuild(flat: FlatSpec) -> tuple[list[Change], int]:
    """Return (changes, malformed_count)."""
    changes, malformed = [], 0
    for record in flat.changes:
        change = to_change(record)
        if change is None:
            malformed += 1
        else:
            changes.append(change)
    return changes, malformed


def severity_of(flat: FlatSpec) -> Severity:
    return Severity(flat.severity)
