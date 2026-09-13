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


def _diff_schema_properties(name: str, old: dict, new: dict) -> list[Change]:
    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}

    removed = sorted(set(old_props) - set(new_props))
    added = sorted(set(new_props) - set(old_props))
    if not removed:
        return []

    # Only a signature that appears exactly once on each side is a safe pairing.
    removed_sigs = Counter(_type_signature(old_props[p]) for p in removed)
    added_sigs = Counter(_type_signature(new_props[p]) for p in added)

    changes: list[Change] = []
    for prop in removed:
        signature = _type_signature(old_props[prop])
        unambiguous = removed_sigs[signature] == 1 and added_sigs[signature] == 1
        if unambiguous:
            match = next(
                p for p in added if _type_signature(new_props[p]) == signature
            )
            changes.append(FieldRenamed(path=f"{name}.{prop}", to=f"{name}.{match}"))
        else:
            changes.append(
                SemanticsChanged(
                    op=f"{name}.{prop}",
                    note=(
                        f"property removed from {name}; "
                        f"{len(added)} added properties, none uniquely matching its "
                        "type signature — a human must decide what replaced it"
                    ),
                )
            )
    return changes


def _diff_operations(old: dict, new: dict) -> list[Change]:
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
):
    """Produce a MigrationSpec describing how `old` became `new`."""
    from upkeep.models import MigrationSpec

    changes: list[Change] = []

    old_schemas, new_schemas = _schemas(old), _schemas(new)
    for name in sorted(set(old_schemas) & set(new_schemas)):
        changes.extend(
            _diff_schema_properties(name, old_schemas[name], new_schemas[name])
        )

    changes.extend(_diff_operations(old, new))

    if any(isinstance(c, (FieldRenamed, EndpointRemoved)) for c in changes):
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
