import pytest

from upkeep.models import (
    CallSite,
    EndpointRemoved,
    FieldRenamed,
    MigrationSpec,
    ParamRequiredAdded,
    SemanticsChanged,
    Severity,
    SiteKind,
    Tier,
)
from upkeep.router import plan


def spec_with(*changes, vectors=("fixtures/vectors.json",)):
    return MigrationSpec(
        id="acme-v1-v2",
        provider="acme",
        **{"from": "v1", "to": "v2"},
        severity=Severity.breaking,
        changes=list(changes),
        vectors=list(vectors),
    )


def site(name="amount", *, rooted=True, kind=SiteKind.attribute):
    return CallSite(
        file="billing.py",
        line=3,
        column=10,
        kind=kind,
        name=name,
        expression=f"invoice.{name}",
        rooted=rooted,
    )


RENAME = FieldRenamed(
    path="InvoiceLine.amount", to="InvoiceLine.unit_amount", inferred=False
)
INFERRED_RENAME = RENAME.model_copy(update={"inferred": True})


def test_rooted_rename_is_tier_a():
    (item,) = plan(spec_with(RENAME), [site()])
    assert item.tier is Tier.A
    assert item.rule == "rename_field"


def test_unrooted_rename_escalates():
    (item,) = plan(spec_with(RENAME), [site(rooted=False, kind=SiteKind.subscript)])
    assert item.tier is Tier.C
    assert item.rule is None
    assert "prove" in item.reason


def test_sites_with_other_names_are_not_affected():
    assert plan(spec_with(RENAME), [site("currency")]) == []


def test_semantics_change_always_escalates():
    change = SemanticsChanged(op="Refund.reason", note="ambiguous")
    (item,) = plan(spec_with(change), [site("reason")])
    assert item.tier is Tier.C


def test_required_param_with_default_awaits_its_codemod():
    """Routing says Tier A is appropriate; the registry says nothing implements
    it, so it demotes rather than promising a patch that never arrives."""
    change = ParamRequiredAdded(op="GET /v1/invoices", param="page_size", safe_default=100)
    (item,) = plan(spec_with(change), [site("acme.Invoice.retrieve", kind=SiteKind.call)])
    assert item.tier is Tier.B
    assert item.rule is None
    assert "no codemod implements it yet" in item.reason


def test_required_param_without_default_is_tier_b():
    change = ParamRequiredAdded(op="GET /v1/invoices", param="page_size")
    (item,) = plan(spec_with(change), [site("acme.Invoice.retrieve", kind=SiteKind.call)])
    assert item.tier is Tier.B


def test_endpoint_removed_without_replacement_escalates():
    change = EndpointRemoved(method="POST", path="/v1/charges")
    (item,) = plan(spec_with(change), [site("acme.Charge.create", kind=SiteKind.call)])
    assert item.tier is Tier.C


@pytest.mark.parametrize("require", [True, False])
def test_a_spec_without_vectors_can_never_patch(require):
    """The policy from the design: unverifiable means un-patchable."""
    items = plan(spec_with(RENAME, vectors=()), [site()], require_vectors=require)
    expected = Tier.C if require else Tier.A
    assert items[0].tier is expected


def test_removed_endpoint_does_not_escalate_unrelated_calls():
    """A removal of /v1/charges must not flag every call in the repository."""
    change = EndpointRemoved(method="POST", path="/v1/charges")
    assert plan(spec_with(change), [site("acme.Invoice.retrieve", kind=SiteKind.call)]) == []


def test_removed_endpoint_escalates_the_calls_that_reach_it():
    change = EndpointRemoved(method="POST", path="/v1/charges")
    (item,) = plan(spec_with(change), [site("acme.Charge.create", kind=SiteKind.call)])
    assert item.tier is Tier.C


def test_required_param_matches_its_own_resource():
    change = ParamRequiredAdded(op="GET /v1/invoices", param="page_size", safe_default=100)
    items = plan(spec_with(change), [site("acme.Invoice.retrieve", kind=SiteKind.call)])
    assert len(items) == 1, "the call for this resource should be matched"


def test_snake_case_resources_are_matched():
    change = EndpointRemoved(method="POST", path="/v1/payment_intents")
    (item,) = plan(spec_with(change), [site("acme.PaymentIntent.create", kind=SiteKind.call)])
    assert item.tier is Tier.C


def test_every_tier_a_item_names_an_implemented_codemod():
    """A standing guard: Tier A must never promise a patch the runner cannot write."""
    from upkeep.patch.registry import IMPLEMENTED_RULES

    changes = [
        RENAME,
        ParamRequiredAdded(op="GET /v1/invoices", param="page_size", safe_default=100),
        EndpointRemoved(method="POST", path="/v1/charges", replacement="POST /v1/pay"),
        SemanticsChanged(op="Refund.reason", note="ambiguous"),
    ]
    sites = [
        site(),
        site(rooted=False, kind=SiteKind.subscript),
        site("acme.Invoice.retrieve", kind=SiteKind.call),
        site("acme.Charge.create", kind=SiteKind.call),
        site("reason"),
    ]
    for item in plan(spec_with(*changes), sites):
        if item.tier is Tier.A:
            assert item.rule in IMPLEMENTED_RULES, item


def test_an_inferred_rename_never_reaches_tier_a():
    """Two of five renames inferred from a year of Stripe's spec were wrong.
    A guess is evidence for a reviewer, not an instruction for a codemod."""
    (item,) = plan(spec_with(INFERRED_RENAME), [site()])
    assert item.tier is Tier.B
    assert item.rule is None
    assert "not declared by the provider" in item.reason
