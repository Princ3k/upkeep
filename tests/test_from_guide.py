"""The migration guide as an input.

Three providers in, the guide is the better source: Stripe's spec hides renames
in coexistence, Twilio's is silent about the SDK releases where it actually
breaks people, and Shopify publishes no spec at all. All three publish a guide,
and a guide carries the one thing no schema diff does — the shape of the call,
before and after.

The fixture is a real extraction from Shopify's published
BREAKING_CHANGES_FOR_V16.md, produced by Claude Opus 5 under the prompt in
`upkeep.detect.from_guide`. It was produced in a session rather than through an
automated API call, so treat it as a worked example rather than a benchmark
figure; `spec_from_guide` is the code path that reproduces it.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from upkeep.models import (
    CallPatternChanged,
    CallSite,
    MigrationSpec,
    SemanticsChanged,
    Severity,
    SiteKind,
    SymbolRemoved,
    Tier,
)
from upkeep.router import plan

GUIDES = Path(__file__).parent / "fixtures" / "guides"


def load() -> MigrationSpec:
    return MigrationSpec.model_validate_json(
        (GUIDES / "shopify_v16.expected.json").read_text()
    )


def ruby_site(name: str, language: str = "ruby") -> CallSite:
    return CallSite(
        file="app.rb", line=7, column=0, kind=SiteKind.attribute,
        name=name, expression=f"session.{name}", rooted=True, language=language,
    )


def test_an_extracted_guide_validates_as_a_migration_spec():
    spec = load()
    assert spec.provider == "shopify-ruby"
    assert spec.severity is Severity.breaking
    assert len(spec.changes) == 3


def test_the_guides_before_and_after_code_survives_verbatim():
    """The reason a guide beats a spec diff: it shows the call, both ways."""
    spec = load()
    patterns = [c for c in spec.changes if isinstance(c, CallPatternChanged)]
    assert [c.leaf for c in patterns] == ["serialize", "deserialize"]

    deserialize = patterns[1]
    assert "Session.deserialize(serialized_data)" in deserialize.before
    assert "Session.new(" in deserialize.after
    assert deserialize.language == "ruby"


def test_a_runtime_requirement_is_not_forced_into_a_code_shape():
    """"Ruby 3.0 -> 3.2" has no before and no after. It stays in the escape
    hatch rather than being dressed up as something rewritable."""
    (other,) = [c for c in load().changes if isinstance(c, SemanticsChanged)]
    assert other.op == "ruby"


def test_a_pattern_rewrite_is_tier_b_never_tier_a():
    """One example cannot tell you how the change applies to a call site with
    different names and surroundings. That is a model's job, not a codemod's."""
    items = plan(load(), [ruby_site("deserialize")], require_vectors=False)
    (item,) = items
    assert item.tier is Tier.B
    assert item.rule is None
    assert "needs a model" in item.reason


def test_a_ruby_pattern_never_touches_a_python_call_site():
    """The safety property. `serialize` is a common method name; applying
    Shopify's Ruby rewrite to a Python call site that merely shares it would be
    a corrupting patch generated from an unrelated document."""
    spec = load()
    assert plan(spec, [ruby_site("deserialize", language="python")],
                require_vectors=False) == []
    assert plan(spec, [ruby_site("deserialize", language="ruby")],
                require_vectors=False) != []


def test_api_surface_changes_still_apply_to_every_language():
    """A change with no language describes the wire format, not an SDK, so it
    must not be filtered out by the guard above."""
    from upkeep.models import FieldRenamed

    spec = MigrationSpec(
        id="x", provider="p", **{"from": "1", "to": "2"},
        severity=Severity.breaking, vectors=["v"],
        changes=[FieldRenamed(path="line.amount", to="line.unit_amount", inferred=False)],
    )
    site = CallSite(file="a.go", line=1, column=0, kind=SiteKind.attribute,
                    name="amount", expression="line.amount", rooted=True, language="go")
    assert len(plan(spec, [site])) == 1


def test_a_symbol_with_no_replacement_shown_is_not_a_pattern_change():
    """Guides often say only "this is gone". That is a removal to report, not
    an example to rewrite from."""
    removed = SymbolRemoved(symbol="Session#serialize", note="removed")
    (item,) = plan(
        MigrationSpec(id="x", provider="p", **{"from": "1", "to": "2"},
                      severity=Severity.breaking, vectors=["v"], changes=[removed]),
        [ruby_site("serialize")],
    )
    assert item.tier is Tier.C


@pytest.mark.parametrize(
    "before, after, complaint",
    [
        ("session.serialize", "", "symbol_removed"),
        ("", "Session.new", "match against"),
        ("Session.new", "Session.new", "identical"),
    ],
)
def test_an_unusable_example_is_rejected_at_the_schema(before, after, complaint):
    """A pattern with nothing to match, nothing to write, or no difference
    between them would send a model off to rewrite code on no information."""
    with pytest.raises(ValidationError, match=complaint):
        CallPatternChanged(symbol="Session.deserialize", language="ruby",
                           before=before, after=after)


def test_the_issue_reports_pattern_rewrites_not_just_escalations():
    """A guide-derived spec is all Tier B. Reporting only Tier C rendered
    "0 site(s) need review" while real call sites went unmentioned."""
    from upkeep.delivery import render_issue

    spec = load()
    items = plan(spec, [ruby_site("deserialize")], require_vectors=False)
    body = render_issue(spec, items)

    assert "1 site(s) need review" in body
    assert "app.rb:7" in body
    assert "Session.deserialize(serialized_data)" in body   # the guide's before
    assert "Session.new(" in body                           # and its after
    assert "```ruby" in body
