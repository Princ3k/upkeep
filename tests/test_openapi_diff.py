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
