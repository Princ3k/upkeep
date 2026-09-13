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


def test_a_schema_removed_entirely_is_reported():
    """Twilio dropped ten `usage_record_*_enum_category` enums in one release.
    A diff that only walks schemas present in both versions reports nothing at
    all, and grades the release `additive`."""
    old = {"paths": {}, "components": {"schemas": {
        "usage_record_enum_category": {"type": "string", "enum": ["a", "b"]},
        "account": {"properties": {"sid": {"type": "string"}}}}}}
    new = {"paths": {}, "components": {"schemas": {
        "realtime_transcription_enum_track": {"type": "string", "enum": ["x"]},
        "account": {"properties": {"sid": {"type": "string"}}}}}}
    spec = diff_specs(old, new, provider="twilio", from_version="a", to_version="b")

    (gone,) = [c for c in spec.changes if isinstance(c, SemanticsChanged)]
    assert gone.op == "usage_record_enum_category"
    assert "removed entirely" in gone.note
    assert spec.severity is Severity.breaking


def test_a_removed_schema_is_never_paired_with_an_added_one():
    """The added schema has the same shape, which under the property rule would
    have looked like a rename. Schemas are not paired at all."""
    shape = {"type": "string", "enum": ["a", "b"]}
    old = {"paths": {}, "components": {"schemas": {"old_enum": dict(shape)}}}
    new = {"paths": {}, "components": {"schemas": {"new_enum": dict(shape)}}}
    spec = diff_specs(old, new, provider="twilio", from_version="a", to_version="b",
                      declared=True)
    assert not spec.field_renames()


def test_ref_style_parameters_do_not_crash_the_differ():
    """Twilio declares a shared API-version header as a `$ref`, so the parameter
    entry carries no `name` of its own. Indexing it blind raised KeyError and
    took down a sweep of 60 product specs."""
    def spec(required):
        return {
            "paths": {"/v1/Services": {"post": {"parameters": [
                {"$ref": "#/components/parameters/XTwilioApiVersion"},
                {"name": "PageSize", "in": "query", "required": required,
                 "schema": {"type": "integer", "default": 50}},
            ]}}},
            "components": {
                "schemas": {},
                "parameters": {"XTwilioApiVersion": {
                    "name": "X-Twilio-Api-Version", "in": "header",
                    "schema": {"type": "string"}}},
            },
        }
    changes = diff_specs(spec(False), spec(True), provider="twilio",
                         from_version="a", to_version="b").changes
    added = [c for c in changes if isinstance(c, ParamRequiredAdded)]
    assert [(c.param, c.safe_default) for c in added] == [("PageSize", 50)]


def test_path_level_parameters_are_seen_by_operations():
    """A parameter declared on the path item applies to every operation under
    it; reading only the operation's own list misses it."""
    def spec(required):
        return {
            "paths": {"/v1/Messages": {
                "parameters": [{"name": "AccountSid", "in": "path",
                                "required": required, "schema": {"type": "string"}}],
                "get": {"operationId": "listMessages"},
            }},
            "components": {"schemas": {}},
        }
    changes = diff_specs(spec(False), spec(True), provider="twilio",
                         from_version="a", to_version="b").changes
    assert [c.param for c in changes if isinstance(c, ParamRequiredAdded)] == ["AccountSid"]


# ---------------------------------------------------------------------------
# Pending renames. Real providers rename by adding the successor and keeping
# the old field, announcing it in prose. Nothing is removed, so a key-set diff
# reports pure addition — which is why upkeep's first measurement of how often
# providers rename came back zero across two providers.
# ---------------------------------------------------------------------------


def pending_spec(desc_old: str, desc_new: str | None):
    props = {"quantity": {"type": "integer", "description": desc_old}}
    if desc_new is not None:
        props["quantity_decimal"] = {"type": "string", "description": desc_new}
    return {"paths": {}, "components": {"schemas": {"invoiceitem": {"properties": props}}}}


def test_a_prose_announced_rename_is_found_while_both_fields_exist():
    """Verbatim from Stripe's `invoiceitem.quantity`."""
    before = pending_spec("Quantity of units for the invoice item.", None)
    after = pending_spec(
        "Quantity of units for the invoice item in integer format. This field "
        "will be deprecated in favor of `quantity_decimal` in a future version.",
        "Full-precision decimal quantity.",
    )
    spec = diff_specs(before, after, provider="stripe", from_version="a", to_version="b")
    (rename,) = spec.field_renames()
    assert (rename.old_name, rename.new_name) == ("quantity", "quantity_decimal")
    assert rename.pending is True


def test_a_pending_rename_is_a_deprecation_not_yet_a_break():
    """The old field still works. Grading this `breaking` would cry wolf."""
    before = pending_spec("Quantity of units.", None)
    after = pending_spec("Use `quantity_decimal` instead.", "Decimal quantity.")
    assert diff_specs(before, after, provider="stripe", from_version="a",
                      to_version="b").severity is Severity.deprecation


def test_a_successor_that_is_not_a_sibling_field_is_not_claimed():
    """Prose can name anything — another object, a guide, a concept. Only a
    successor that actually exists alongside the field is believed."""
    before = pending_spec("Quantity of units.", None)
    after = {"paths": {}, "components": {"schemas": {"invoiceitem": {"properties": {
        "quantity": {"type": "integer",
                     "description": "Deprecated in favor of `some_other_object`."}}}}}}
    assert not diff_specs(before, after, provider="stripe", from_version="a",
                          to_version="b").field_renames()


def test_an_already_announced_deprecation_is_not_re_reported():
    """It was already true at the start of the window; it is not news."""
    text = "Use `quantity_decimal` instead."
    same = pending_spec(text, "Decimal quantity.")
    assert not diff_specs(same, same, provider="stripe", from_version="a",
                          to_version="b").field_renames()
