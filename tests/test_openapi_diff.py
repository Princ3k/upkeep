from pathlib import Path

from upkeep.detect import diff_specs
from upkeep.models import (
    EndpointRemoved,
    FieldRenamed,
    ParamRequiredAdded,
    SemanticsChanged,
    Severity,
)
from upkeep.providers.base import load_json

SPECS = Path(__file__).parent / "fixtures" / "specs"


def build_spec():
    return diff_specs(
        load_json(SPECS / "acme_v1.json"),
        load_json(SPECS / "acme_v2.json"),
        provider="acme",
        from_version="v1",
        to_version="v2",
        vectors=["fixtures/consumer/test_billing.py"],
        declared=True,
    )


def test_unambiguous_rename_is_detected():
    renames = build_spec().field_renames()
    assert len(renames) == 1
    assert renames[0].old_name == "amount"
    assert renames[0].new_name == "unit_amount"


def test_ambiguous_removal_is_never_guessed():
    """Refund lost two string properties and gained one. That is not a rename."""
    spec = build_spec()
    escalated = [c for c in spec.changes if isinstance(c, SemanticsChanged)]
    ops = {c.op for c in escalated}
    assert ops == {"Refund.reason", "Refund.note"}
    assert not any(
        isinstance(c, FieldRenamed) and c.path.startswith("Refund") for c in spec.changes
    )


def test_removed_endpoint_is_detected():
    removed = [c for c in build_spec().changes if isinstance(c, EndpointRemoved)]
    assert [(c.method, c.path) for c in removed] == [("POST", "/v1/charges")]


def test_new_required_param_carries_its_default():
    added = [c for c in build_spec().changes if isinstance(c, ParamRequiredAdded)]
    assert len(added) == 1
    assert added[0].param == "page_size"
    assert added[0].safe_default == 100


def test_severity_is_breaking():
    assert build_spec().severity is Severity.breaking


def test_identical_specs_produce_no_changes():
    spec = load_json(SPECS / "acme_v1.json")
    assert diff_specs(
        spec, spec, provider="acme", from_version="v1", to_version="v1"
    ).changes == []


# ---------------------------------------------------------------------------
# Request bodies. Form-encoded APIs (Stripe among them) declare call parameters
# under requestBody, not `parameters` — in one real Stripe version window every
# single shared operation used requestBody and none used it for `parameters`,
# so reading only the latter made every POST invisible.
# ---------------------------------------------------------------------------


def spec_with_body(properties: dict, required: list[str] | None = None):
    return {
        "openapi": "3.0.3",
        "paths": {
            "/v1/charges": {
                "post": {
                    "operationId": "createCharge",
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": properties,
                                    "required": required or [],
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }


def diff_bodies(old_props, new_props, *, old_required=None, new_required=None):
    return diff_specs(
        spec_with_body(old_props, old_required),
        spec_with_body(new_props, new_required),
        provider="acme",
        from_version="v1",
        to_version="v2",
    ).changes


def test_renamed_request_parameter_is_detected():
    """A renamed request param is a keyword-argument rename in consumer code."""
    changes = diff_bodies(
        {"amount": {"type": "integer"}, "currency": {"type": "string"}},
        {"unit_amount": {"type": "integer"}, "currency": {"type": "string"}},
    )
    renames = [c for c in changes if isinstance(c, FieldRenamed)]
    assert len(renames) == 1
    assert renames[0].old_name == "amount"
    assert renames[0].new_name == "unit_amount"


def test_newly_required_request_field_is_detected():
    changes = diff_bodies(
        {"amount": {"type": "integer"}},
        {"amount": {"type": "integer"}, "idempotency_key": {"type": "string"}},
        new_required=["idempotency_key"],
    )
    added = [c for c in changes if isinstance(c, ParamRequiredAdded)]
    assert [(c.param, c.safe_default) for c in added] == [("idempotency_key", None)]


def test_ambiguous_request_body_removal_is_not_guessed():
    changes = diff_bodies(
        {"reason": {"type": "string"}, "note": {"type": "string"}},
        {"cause": {"type": "string"}},
    )
    assert not [c for c in changes if isinstance(c, FieldRenamed)]
    assert len([c for c in changes if isinstance(c, SemanticsChanged)]) == 2


def test_additive_request_body_change_is_not_breaking():
    changes = diff_bodies(
        {"amount": {"type": "integer"}},
        {"amount": {"type": "integer"}, "memo": {"type": "string"}},
    )
    assert changes == []


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


def test_a_bare_property_removal_is_breaking():
    """Real case from Stripe: `card.iin` vanished with nothing added alongside
    it, so no rename was inferable. Being unable to say what replaced it makes
    the change harder to handle, not gentler."""
    old = {"components": {"schemas": {"card": {"properties": {"iin": {"type": "string"}}}}}, "paths": {}}
    new = {"components": {"schemas": {"card": {"properties": {}}}}, "paths": {}}
    spec = diff_specs(old, new, provider="stripe", from_version="a", to_version="b")

    assert [c.kind for c in spec.changes] == ["semantics_changed"]
    assert spec.severity is Severity.breaking


def test_a_required_param_with_a_safe_default_is_only_a_deprecation():
    changes = diff_bodies(
        {"amount": {"type": "integer"}},
        {"amount": {"type": "integer"}, "page_size": {"type": "integer", "default": 100}},
        new_required=["page_size"],
    )
    spec_severity = diff_specs(
        spec_with_body({"amount": {"type": "integer"}}),
        spec_with_body(
            {"amount": {"type": "integer"}, "page_size": {"type": "integer", "default": 100}},
            ["page_size"],
        ),
        provider="acme",
        from_version="v1",
        to_version="v2",
    ).severity
    assert [c.safe_default for c in changes if isinstance(c, ParamRequiredAdded)] == [100]
    assert spec_severity is Severity.deprecation


def test_a_peer_set_membership_change_is_not_a_rename():
    """Taken from Stripe's terminal tipping config, a map keyed by ISO-4217
    currency. Bulgaria joined the euro so `bgn` left; Gibraltar's `gip` arrived
    independently. Exactly one out and one in, identical shapes — the old rule
    called that an unambiguous rename and would have silently repointed a
    merchant's tipping config at the wrong currency."""
    currencies = ["aed", "aud", "cad", "chf", "czk", "dkk", "eur", "gbp", "hkd", "jpy"]
    shape = {"type": "object"}
    old = {
        "paths": {},
        "components": {"schemas": {"tipping": {"properties": {
            **{c: dict(shape) for c in currencies}, "bgn": dict(shape)}}}},
    }
    new = {
        "paths": {},
        "components": {"schemas": {"tipping": {"properties": {
            **{c: dict(shape) for c in currencies}, "gip": dict(shape)}}}},
    }
    spec = diff_specs(old, new, provider="stripe", from_version="a", to_version="b",
                      declared=True)

    assert not [c for c in spec.changes if isinstance(c, FieldRenamed)]
    (escalation,) = [c for c in spec.changes if isinstance(c, SemanticsChanged)]
    assert escalation.op == "tipping.bgn"
    assert "set of peers" in escalation.note


def test_a_struct_with_few_same_shaped_fields_still_renames():
    """The guard must not swallow ordinary renames: three fields, only one of
    them an integer, is a struct — not a bag of peers."""
    old = {"paths": {}, "components": {"schemas": {"line": {"properties": {
        "amount": {"type": "integer"},
        "currency": {"type": "string"},
        "description": {"type": "string"}}}}}}
    new = {"paths": {}, "components": {"schemas": {"line": {"properties": {
        "unit_amount": {"type": "integer"},
        "currency": {"type": "string"},
        "description": {"type": "string"}}}}}}
    (rename,) = diff_specs(old, new, provider="acme", from_version="a",
                           to_version="b").field_renames()
    assert (rename.old_name, rename.new_name) == ("amount", "unit_amount")


def test_renames_are_marked_inferred_unless_vouched_for():
    assert build_spec().field_renames()[0].inferred is False  # built with declared=True

    undeclared = diff_specs(
        load_json(SPECS / "acme_v1.json"),
        load_json(SPECS / "acme_v2.json"),
        provider="acme", from_version="v1", to_version="v2",
    )
    assert undeclared.field_renames()[0].inferred is True
