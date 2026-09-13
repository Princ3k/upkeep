"""The grounding check must be able to fail.

A 100% grounding score across the eval corpus is only informative if the check
rejects fabrication, so these are the mutations that score ran against: invented
code, a single altered identifier, a plausible-but-absent symbol, and code
lifted from a different document. Each is the shape of a real failure — most of
all the second, which is what a model does when it half-remembers an API.
"""

import pytest

from upkeep.detect.grounding import check_grounding, drop_ungrounded
from upkeep.models import (
    CallPatternChanged,
    MigrationSpec,
    SemanticsChanged,
    Severity,
    SymbolRemoved,
)

GUIDE = """\
# Breaking change notice

## Removal of `Session#serialize`

```ruby
serialized_data = session.serialize
```

Use `Session.new()` instead:

```ruby
ShopifyAPI::Auth::Session.new(shop: shop.shopify_domain)
```
"""


def spec_with(*changes):
    return MigrationSpec(
        id="x", provider="p", **{"from": "1", "to": "2"},
        severity=Severity.breaking, changes=list(changes),
    )


def pattern(before="serialized_data = session.serialize",
            after="ShopifyAPI::Auth::Session.new(shop: shop.shopify_domain)"):
    return CallPatternChanged(symbol="Session#serialize", language="ruby",
                              before=before, after=after)


def test_a_faithful_extraction_is_grounded():
    report = check_grounding(spec_with(pattern()), GUIDE)
    assert report.grounded == 1
    assert report.fabricated == []


def test_invented_replacement_code_is_caught():
    """The corrupting failure: something downstream rewrites real source from
    an `after` the document never contained."""
    report = check_grounding(
        spec_with(pattern(after="Session.build(shop: shop.domain, token: tok)")), GUIDE
    )
    assert report.fabricated
    assert any("after:" in m for m in report.fabricated[0].missing)


def test_a_single_altered_identifier_is_caught():
    """What a model does when it half-remembers an API — the line looks right
    and one token is wrong."""
    report = check_grounding(
        spec_with(pattern(after="ShopifyAPI::Auth::Session.new(shop: shop.myshopify_domain)")),
        GUIDE,
    )
    assert report.fabricated


def test_a_plausible_but_absent_symbol_is_caught():
    report = check_grounding(
        spec_with(SymbolRemoved(symbol="Session#deserialize_all")), GUIDE
    )
    assert report.fabricated


def test_code_lifted_from_another_document_is_caught():
    report = check_grounding(
        spec_with(pattern(before="const scopes = shopify.config.scopes.toString();")), GUIDE
    )
    assert report.fabricated


def test_whitespace_and_indentation_are_not_treated_as_fabrication():
    """Re-indenting a quoted block is not inventing it."""
    report = check_grounding(
        spec_with(pattern(before="    serialized_data   =   session.serialize")), GUIDE
    )
    assert report.grounded == 1


def test_prose_only_records_have_nothing_to_verify():
    """A semantics_changed quotes no code, so grounding neither passes nor
    condemns it — and must not silently drop it."""
    report = check_grounding(spec_with(SemanticsChanged(op="ruby", note="3.0 to 3.2")), GUIDE)
    assert report.grounded == 1


def test_dropping_ungrounded_changes_keeps_the_rest():
    spec = spec_with(pattern(), pattern(after="Session.build(x)"))
    cleaned, report = drop_ungrounded(spec, GUIDE)
    assert len(cleaned.changes) == 1
    assert len(report.fabricated) == 1


@pytest.mark.parametrize("bad", ["Session.build(x)", "totally.invented(call)"])
def test_extraction_never_returns_what_it_cannot_find(bad):
    cleaned, _ = drop_ungrounded(spec_with(pattern(after=bad)), GUIDE)
    assert cleaned.changes == []
