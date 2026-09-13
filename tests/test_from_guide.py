"""The migration guide as an input.

Three providers in, the guide is the better source: Stripe's spec hides renames
in coexistence, Twilio's is silent about the SDK releases where it actually
breaks people, and Shopify publishes no spec at all. All three publish a guide.

The fixture here is a real extraction from Shopify's published
BREAKING_CHANGES_FOR_V16.md, produced by Claude Opus 5 under the prompt in
`upkeep.detect.from_guide`. It was produced in a session rather than through an
automated API call, so treat it as a worked example rather than a benchmark
figure; `spec_from_guide` is the code path that reproduces it.
"""

import json
from pathlib import Path

from upkeep.models import (
    CallSite,
    MigrationSpec,
    SemanticsChanged,
    SiteKind,
    SymbolRemoved,
    Severity,
    Tier,
)
from upkeep.router import plan

GUIDES = Path(__file__).parent / "fixtures" / "guides"


def load() -> MigrationSpec:
    return MigrationSpec.model_validate_json(
        (GUIDES / "shopify_v16.expected.json").read_text()
    )


def test_an_extracted_guide_validates_as_a_migration_spec():
    """Whatever the source, everything downstream sees the same object."""
    spec = load()
    assert spec.provider == "shopify-ruby"
    assert spec.severity is Severity.breaking
    assert len(spec.changes) == 3


def test_removed_sdk_methods_are_recorded_as_symbols():
    spec = load()
    removed = [c for c in spec.changes if isinstance(c, SymbolRemoved)]
    assert [c.symbol for c in removed] == [
        "ShopifyAPI::Auth::Session#serialize",
        "ShopifyAPI::Auth::Session.deserialize",
    ]
    assert [c.leaf for c in removed] == ["serialize", "deserialize"]


def test_a_runtime_requirement_is_not_forced_into_an_api_shape():
    """"Ruby 3.0 -> 3.2" is not a field, an endpoint, or a symbol. It stays in
    the escape hatch rather than being dressed up as something patchable."""
    spec = load()
    (other,) = [c for c in spec.changes if isinstance(c, SemanticsChanged)]
    assert other.op == "ruby"


def test_the_guide_yields_impact_analysis_but_no_patch():
    """A removed symbol is findable, and that is the whole point of recording
    it. Nothing here is mechanically fixable, and nothing claims to be."""
    spec = load()
    sites = [
        CallSite(file="app.rb", line=n, column=0, kind=SiteKind.attribute,
                 name=name, expression=f"session.{name}", rooted=True)
        for n, name in enumerate(["serialize", "deserialize", "access_token"], start=1)
    ]
    items = plan(spec, sites, require_vectors=False)

    assert [i.site.name for i in items] == ["serialize", "deserialize"]
    assert {i.tier for i in items} == {Tier.C}
    assert any("Session.new" in i.reason for i in items)


def test_no_change_kind_in_this_spec_can_produce_a_patch():
    spec = load()
    assert not spec.field_renames()
    sites = [CallSite(file="app.rb", line=1, column=0, kind=SiteKind.attribute,
                      name="serialize", expression="session.serialize", rooted=True)]
    assert all(i.tier is Tier.C for i in plan(spec, sites, require_vectors=False))
