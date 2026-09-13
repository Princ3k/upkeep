"""Turn two OpenAPI documents into typed change records.

The hard part is not finding differences — it is refusing to over-interpret
them. A property that vanished and a property that appeared in the same schema
*might* be a rename, or might be an unrelated removal and addition. upkeep only
calls it a rename when the mapping is unambiguous: exactly one removed property
and exactly one added property share a type signature. Everything else becomes a
SemanticsChanged, which routes to a human.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from upkeep.models import (
    Change,
    EndpointRemoved,
    FieldRenamed,
    ParamRequiredAdded,
    SemanticsChanged,
    Severity,
)

PEER_SET_THRESHOLD = 5
"""Above this many properties sharing one type signature, a container is a
collection of peers (currency codes, locale keys, feature flags) rather than a
struct. A removal and an addition among peers is a membership change, never a
rename — Stripe dropping `bgn` and adding `gip` from a map of 20-odd currencies
is the case that proved it."""

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def _type_signature(schema: dict[str, Any]) -> tuple:
    """A coarse fingerprint used to pair a removed property with an added one."""
    return (
        schema.get("type"),
        schema.get("format"),
        (schema.get("items") or {}).get("type"),
        tuple(sorted(schema.get("enum", []))) if "enum" in schema else None,
    )


def _schemas(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return (spec.get("components") or {}).get("schemas") or {}


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                out[(method.upper(), path)] = operation
    return out


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Stripe and most form-encoded APIs put call parameters here, not in
    `parameters`. Reading only `parameters` leaves every POST invisible."""
    content = (operation.get("requestBody") or {}).get("content") or {}
    for media in content.values():
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            return media["schema"]
    return {}


def _diff_property_sets(
    container: str, old_props: dict, new_props: dict, *, declared: bool = False
) -> list[Change]:
    """The rename rule, shared by response schemas and request bodies."""
    removed = sorted(set(old_props) - set(new_props))
    added = sorted(set(new_props) - set(old_props))
    if not removed:
        return []
    name = container

    # Only a signature that appears exactly once on each side is a safe pairing.
    removed_sigs = Counter(_type_signature(old_props[p]) for p in removed)
    added_sigs = Counter(_type_signature(new_props[p]) for p in added)
    # ...and only when the container isn't just a bag of same-shaped peers.
    peer_sigs = Counter(_type_signature(s) for s in old_props.values())

    changes: list[Change] = []
    for prop in removed:
        signature = _type_signature(old_props[prop])
        among_peers = peer_sigs[signature] >= PEER_SET_THRESHOLD
        unambiguous = (
            not among_peers
            and removed_sigs[signature] == 1
            and added_sigs[signature] == 1
        )
        if unambiguous:
            match = next(
                p for p in added if _type_signature(new_props[p]) == signature
            )
            changes.append(
                FieldRenamed(
                    path=f"{name}.{prop}",
                    to=f"{name}.{match}",
                    inferred=not declared,
                )
            )
        else:
            changes.append(
                SemanticsChanged(
                    op=f"{name}.{prop}",
                    note=(
                        f"property removed from {name}; "
                        + (
                            f"{peer_sigs[signature]} sibling properties share its "
                            "shape, so this container is a set of peers and a "
                            "removal here is a membership change, not a rename"
                            if among_peers
                            else f"{len(added)} added properties, none uniquely "
                            "matching its type signature"
                        )
                        + " — a human must decide what replaced it"
                    ),
                )
            )
    return changes


def _diff_schema_properties(
    name: str, old: dict, new: dict, *, declared: bool = False
) -> list[Change]:
    return _diff_property_sets(
        name,
        old.get("properties") or {},
        new.get("properties") or {},
        declared=declared,
    )


def _diff_operations(old: dict, new: dict, *, declared: bool = False) -> list[Change]:
    old_ops = _operations(old)
    new_ops = _operations(new)
    changes: list[Change] = []

    for (method, path) in sorted(set(old_ops) - set(new_ops)):
        changes.append(EndpointRemoved(method=method, path=path, replacement=None))

    for key in sorted(set(old_ops) & set(new_ops)):
        method, path = key
        old_required = {
            p["name"]
            for p in old_ops[key].get("parameters", [])
            if isinstance(p, dict) and p.get("required")
        }
        new_params = {
            p["name"]: p
            for p in new_ops[key].get("parameters", [])
            if isinstance(p, dict)
        }
        for param_name, param in new_params.items():
            if param.get("required") and param_name not in old_required:
                schema = param.get("schema") or {}
                changes.append(
                    ParamRequiredAdded(
                        op=f"{method} {path}",
                        param=param_name,
                        safe_default=schema.get("default"),
                    )
                )

        old_body = _request_body_schema(old_ops[key])
        new_body = _request_body_schema(new_ops[key])
        old_body_props = old_body.get("properties") or {}
        new_body_props = new_body.get("properties") or {}

        # A renamed request parameter is a keyword-argument rename in consumer
        # code — the same Tier A codemod that handles response fields.
        changes.extend(
            _diff_property_sets(
                f"{method} {path}",
                old_body_props,
                new_body_props,
                declared=declared,
            )
        )

        newly_required = set(new_body.get("required") or []) - set(
            old_body.get("required") or []
        )
        for param_name in sorted(newly_required):
            prop = new_body_props.get(param_name) or {}
            changes.append(
                ParamRequiredAdded(
                    op=f"{method} {path}",
                    param=param_name,
                    safe_default=prop.get("default"),
                )
            )
    return changes


def diff_specs(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    provider: str,
    from_version: str,
    to_version: str,
    vectors: list[str] | None = None,
    effective: str | None = None,
    declared: bool = False,
):
    """Produce a MigrationSpec describing how `old` became `new`.

    Pass `declared=True` only when a human has checked the diff against the
    provider's own migration guide. That promotion is what lets an inferred
    rename reach Tier A; without it, renames are evidence for a reviewer rather
    than instructions for a codemod.
    """
    from upkeep.models import MigrationSpec

    changes: list[Change] = []

    old_schemas, new_schemas = _schemas(old), _schemas(new)
    for name in sorted(set(old_schemas) & set(new_schemas)):
        changes.extend(
            _diff_schema_properties(
                name, old_schemas[name], new_schemas[name], declared=declared
            )
        )

    changes.extend(_diff_operations(old, new, declared=declared))

    # Something the consumer relied on is gone. That upkeep could not work out
    # *what* replaced it makes the change harder to handle, not gentler.
    breaking = (FieldRenamed, EndpointRemoved, SemanticsChanged)
    if any(isinstance(c, breaking) for c in changes) or any(
        isinstance(c, ParamRequiredAdded) and c.safe_default is None for c in changes
    ):
        severity = Severity.breaking
    elif changes:
        severity = Severity.deprecation
    else:
        severity = Severity.additive

    return MigrationSpec(
        id=f"{provider}-{from_version}-{to_version}",
        provider=provider,
        **{"from": from_version, "to": to_version},
        severity=severity,
        effective=effective,
        changes=changes,
        vectors=vectors or [],
    )
