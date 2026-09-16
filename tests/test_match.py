"""The claim matcher must be able to refuse.

Matching records across runs by claim rather than by label exists to stop
spelling differences counting as disagreement. That only improves the number if
it still separates changes that genuinely differ — a matcher that merges
everything reports perfect agreement and measures nothing.

So the cases below are mostly non-matches, and they are the real shapes from
the corpus: two removals from one class whose notes are near-identical prose,
one name that is a substring of another, and two unrelated changes that happen
to share boilerplate wording.
"""

import pytest

from match import (Record, cluster, jaccard, label_words, note_tokens, overlap,
                   same_claim, segments)


def rec(run, label, note="", before=None, kind="symbol_removed", guide="g", index=0):
    return Record(run=run, guide=guide, index=index, kind=kind,
                  label=label, note=note, before=before)


# --------------------------------------------------------------------------
# Must match — the same change, spelled differently
# --------------------------------------------------------------------------


def test_the_same_name_spelled_the_same_way():
    a = rec("hand", "Session#serialize")
    b = rec("pro", "session#SERIALIZE")
    assert same_claim(a, b) == "identical"


def test_qualification_depth_does_not_matter():
    """One run writes the bare method, another the fully qualified path.

    This is the case `compare.py` was written around, and the one a prefix rule
    gets backwards: the shared part is the *end* of the name, not the start.
    """
    a = rec("hand", "Session#serialize")
    b = rec("pro", "ShopifyAPI::Auth::Session#serialize")
    assert same_claim(a, b) == "qualified"


def test_a_bare_leaf_matches_its_qualified_form():
    assert same_claim(rec("a", "serialize"),
                      rec("b", "ShopifyAPI::Auth::Session#serialize")) == "qualified"


def test_punctuation_spelling_of_one_name():
    """`graphql-client` and `GraphQL client` were adjudicated as one change."""
    a = rec("hand", "graphql-client", note="The GraphQL client gem is deprecated.")
    b = rec("flash", "GraphQL client", note="Deprecating the graphql-client gem.")
    assert same_claim(a, b) == "spelling"


def test_same_quoted_code_is_the_same_change():
    """Two records quoting the same lines of one guide are one change.

    Whitespace is ignored, because a model that reflows a call has still quoted
    the guide — the same reason grounding normalises it.
    """
    a = rec("pro", "Session.deserialize", before="Session.deserialize(session_json)")
    b = rec("flash", "deserialize", before="Session.deserialize(\n  session_json\n)")
    assert same_claim(a, b) == "code"


def test_class_matches_its_own_removed_method_when_the_note_names_it():
    """flash named the class; hand named the two methods removed from it."""
    a = rec("flash", "ShopifyAPI::Auth::Session",
            note="Applications using Session#serialize and Session.deserialize "
                 "for persistence must store attributes individually.",
            kind="call_pattern_changed")
    b = rec("hand", "ShopifyAPI::Auth::Session#serialize",
            note="Session#serialize was removed.")
    assert same_claim(a, b) == "member"


def test_prose_labels_for_one_version_bump():
    """Three runs, three labels, one Ruby 3.0 -> 3.2 bump."""
    a = rec("hand", "ruby", kind="semantics_changed",
            note="Minimum required Ruby version raised from 3.0 to 3.2 because "
                 "Ruby 3.0 and 3.1 reached End of Life.")
    b = rec("flash", "Minimum Ruby Version Requirement", kind="semantics_changed",
            note="The minimum required Ruby version has been updated from 3.0 to 3.2.")
    assert same_claim(a, b) == "prose"


def test_free_text_labels_need_only_one_shared_word():
    """`Node version` and `Node.js support` are one bump under two phrasings.

    Neither word set contains the other, so the subset rule that handles the
    Ruby case cannot fire. Both labels are free text, where the words a model
    picks are arbitrary, so a shared word plus agreeing notes is enough.
    """
    a = rec("pro", "Node version", kind="semantics_changed",
            note="The minimum supported Node.js version is now 18.")
    b = rec("flash", "Node.js support", kind="semantics_changed",
            note="Node.js 14 is no longer supported; the minimum is now 18.")
    assert same_claim(a, b) == "prose"


def test_an_unlabelled_record_matches_the_symbol_its_note_names():
    """17 records in the corpus carry code and no symbol, which is allowed."""
    a = rec("pro", "", kind="call_pattern_changed",
            note="Say.ssmlEmphasis() replaced by Say.emphasis()",
            before="const say = response.say();")
    b = rec("hand", "Say.ssmlEmphasis", note="Removed in favour of Say.emphasis.")
    assert same_claim(a, b) == "mention"


def test_an_unlabelled_record_matches_a_symbol_in_its_quoted_code():
    a = rec("pro", "", kind="call_pattern_changed", note="Scopes are now optional",
            before="const scopes = shopify.config.scopes.toString();")
    b = rec("flash", "shopify.config.scopes", note="This is now optional.")
    assert same_claim(a, b) == "mention"


# --------------------------------------------------------------------------
# Must NOT match — genuinely different changes
# --------------------------------------------------------------------------


def test_sibling_methods_of_one_class_stay_apart():
    """`serialize` and `deserialize` are two removals, not one.

    This is the case that kills a naive matcher twice over: one name is a
    substring of the other, and in one real run their notes open with the same
    sentence.
    """
    a = rec("pro", "ShopifyAPI::Auth::Session#serialize",
            note="Removed due to a security vulnerability. Applications should "
                 "refactor to store individual session attributes.")
    b = rec("pro", "ShopifyAPI::Auth::Session.deserialize",
            note="Removed due to a security vulnerability. Applications should "
                 "reconstruct sessions using Session.new().")
    assert same_claim(a, b) is None


def test_substring_alone_never_matches():
    """`serialize` is a substring of `deserialize`; they are not one change."""
    assert same_claim(rec("a", "serialize"), rec("b", "deserialize")) is None
    assert segments("deserialize") != segments("serialize")


def test_shared_boilerplate_does_not_join_unrelated_changes():
    """Identical stock wording on two different symbols is not agreement."""
    a = rec("pro", "WebhookHandler", note="This has been removed in this version.")
    b = rec("pro", "ActiveResource", note="This has been removed in this version.")
    assert same_claim(a, b) is None


def test_a_namespace_does_not_swallow_everything_beneath_it():
    """A bare top-level name needs corroboration before it absorbs a member."""
    a = rec("flash", "ShopifyAPI", note="The library has a new major version.")
    b = rec("hand", "ShopifyAPI::Auth::Session#serialize",
            note="Session#serialize was removed for a security vulnerability.")
    assert same_claim(a, b) is None


def test_empty_labels_do_not_match_each_other():
    assert same_claim(rec("a", ""), rec("b", "")) is None


def test_the_free_text_relaxation_does_not_reach_code_identifiers():
    """The whitespace test is what keeps two real removals apart.

    `Session#serialize` and `Session.deserialize` share three of four label
    words. If the one-shared-word rule applied to identifiers they would merge,
    so this asserts the distinction directly rather than trusting it.
    """
    a = rec("pro", "ShopifyAPI::Auth::Session#serialize",
            note="Removed due to a security vulnerability in session handling.")
    b = rec("pro", "ShopifyAPI::Auth::Session.deserialize",
            note="Removed due to a security vulnerability in session handling.")
    assert same_claim(a, b) is None
    # Same words, but written as a phrase, and now they may relate.
    assert same_claim(rec("pro", "Session serialize", note=a.note),
                      rec("pro", "Session deserialize", note=b.note)) == "prose"


def test_a_short_leaf_cannot_match_by_appearing_inside_code():
    """`id` occurs inside half the code in this corpus and means nothing.

    The note is matched token-for-token so it is safe; the quoted code can only
    be matched as a substring, so short leaves are barred from that path.
    """
    a = rec("pro", "", kind="call_pattern_changed", note="Scopes are now optional",
            before="const scopes = shopify.config.scopes.toString();")
    assert same_claim(a, rec("hand", "id", note="Unrelated.")) is None
    # A leaf long enough to mean something still matches.
    assert same_claim(a, rec("hand", "scopes", note="Unrelated.")) == "mention"


def test_an_unlabelled_record_does_not_match_a_symbol_it_never_names():
    a = rec("pro", "", kind="call_pattern_changed",
            note="Scopes on the config object are now optional",
            before="const scopes = shopify.config.scopes.toString();")
    b = rec("hand", "WebhookHandler", note="The WebhookHandler class was removed.")
    assert same_claim(a, b) is None


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def test_merges_compose_through_a_shared_member():
    """`Session` joins both methods although the methods do not join directly.

    The guide documents one change to session persistence, so one claim is the
    right answer — but it only falls out because union-find lets merges compose.
    """
    records = [
        rec("hand", "Session#serialize", note="Session#serialize was removed."),
        rec("hand", "Session.deserialize", note="Session.deserialize was removed."),
        rec("flash", "Session", kind="call_pattern_changed",
            note="Code using Session#serialize and Session.deserialize must "
                 "store attributes individually."),
    ]
    claims = cluster(records)
    assert len(claims) == 1
    assert claims[0].runs == {"hand", "flash"}


def test_unrelated_records_stay_in_their_own_claims():
    records = [
        rec("hand", "WebhookHandler", note="The WebhookHandler class was removed."),
        rec("pro", "ActiveResource", note="ActiveResource support was dropped."),
        rec("flash", "Session#serialize", note="Session#serialize was removed."),
    ]
    assert len(cluster(records)) == 3


def test_a_claim_reports_the_most_qualified_spelling():
    records = [
        rec("hand", "Session#serialize", note="removed"),
        rec("pro", "ShopifyAPI::Auth::Session#serialize", note="removed"),
    ]
    claim = cluster(records)[0]
    assert claim.label == "ShopifyAPI::Auth::Session#serialize"


def test_a_single_run_finding_something_alone_is_still_one_claim():
    """The real miss from the adjudicated ten: only hand recorded ActiveResource."""
    records = [rec("hand", "ActiveResource", note="ActiveResource support dropped.")]
    claims = cluster(records)
    assert len(claims) == 1 and claims[0].runs == {"hand"}


@pytest.mark.parametrize("threshold", [0.2, 0.3, 0.4, 0.5])
def test_the_hard_non_match_holds_at_every_threshold(threshold):
    """serialize/deserialize must stay apart however the prose bar is set.

    They are separated by the label-subset and segment rules, not by the note
    threshold, so tuning it cannot merge them by accident.
    """
    a = rec("pro", "Session#serialize",
            note="Removed due to a security vulnerability. Applications should "
                 "refactor to store individual session attributes.")
    b = rec("pro", "Session.deserialize",
            note="Removed due to a security vulnerability. Applications should "
                 "reconstruct sessions using Session.new().")
    assert same_claim(a, b, threshold=threshold) is None


def test_jaccard_and_label_words_behave():
    assert jaccard(frozenset(), frozenset({"a"})) == 0.0
    assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert label_words("HTTPClient") == frozenset({"http", "client"})


def test_a_thorough_note_is_not_penalised_for_being_thorough():
    """The case that made containment necessary instead of Jaccard.

    Both runs describe one Ruby 3.0 -> 3.2 bump. One adds why it happened and
    what it means. Four of the shorter note's five content words are in the
    longer one, which Jaccard scores 0.27 — below any usable threshold — and
    containment scores 0.80.
    """
    terse = note_tokens("The minimum required Ruby version has been updated "
                        "from 3.0 to 3.2.")
    full = note_tokens("Minimum required Ruby version raised from 3.0 to 3.2 "
                       "because Ruby 3.0 and 3.1 reached End of Life. Not a code "
                       "change: the runtime must be upgraded before the library can be.")
    assert jaccard(terse, full) < 0.30
    assert overlap(terse, full) > 0.75
    assert same_claim(rec("pro", "Ruby version", kind="semantics_changed",
                          note="The minimum required Ruby version has been "
                               "updated from 3.0 to 3.2."),
                      rec("hand", "ruby", kind="semantics_changed",
                          note="Minimum required Ruby version raised from 3.0 to "
                               "3.2 because Ruby 3.0 and 3.1 reached End of Life.")
                      ) == "prose"


def test_a_short_note_cannot_contain_its_way_into_a_match():
    """Containment reads 1.0 for a two-word note, so a floor guards it."""
    assert overlap(note_tokens("Scopes optional"),
                   note_tokens("Scopes optional everywhere now")) == 0.0
    assert overlap(frozenset({"a", "b"}), frozenset({"a", "b", "c"})) == 0.0
    assert overlap(frozenset({"a", "b", "c"}), frozenset({"a", "b", "c", "d"})) == 1.0


@pytest.mark.parametrize("threshold", [0.2, 0.3, 0.4, 0.5, 0.75])
def test_the_ruby_bump_merges_at_every_usable_threshold(threshold):
    """Agreement over the corpus is flat across this range, not tuned to a value."""
    a = rec("pro", "Ruby version", kind="semantics_changed",
            note="The minimum required Ruby version has been updated from 3.0 to 3.2.")
    b = rec("flash", "Minimum Ruby Version Requirement", kind="semantics_changed",
            note="The minimum required Ruby version has been updated from 3.0 to 3.2.")
    assert same_claim(a, b, threshold=threshold) == "prose"
