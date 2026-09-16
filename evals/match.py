"""Match extraction records across runs by what they claim, not what they are called.

`compare.py` and `build_review.py` originally matched records on the bare
identifier each run attached. Judging the first ten contested identifiers that
produced showed **nine of ten were the same change under a different label**:

    Minimum Ruby Version Requirement / Ruby version / ruby   one Ruby 3.0->3.2 bump
    Session                          / Session#serialize     class vs. its methods
    GraphQL client                   / graphql-client        same gem, one hyphen
    ActiveResource                                           a real miss

So the contested queue was mostly measuring spelling, and the agreement figure
it produced understated how consistently these models read the same document.
This module matches on the claim instead: the note text, the quoted code, and
the qualified name read as segments rather than as a string.

Four rules produce a match, each named in the result so a reviewer can see why
two records were joined:

`identical`   The qualified names agree segment for segment, case-insensitively.
              `Session#serialize` and `ShopifyAPI::Auth::Session#serialize` do
              not — that is `qualified` below — but `serialize` and `SERIALIZE`
              do.

`spelling`    The labels are equal once case and punctuation are stripped.
              `graphql-client` and `GraphQL client` both reduce to
              `graphqlclient`.

`code`        Both records quote the same `before` block, ignoring whitespace.
              Two records quoting the same lines of one guide are one change.

`qualified`   One name's segments are a *suffix* of the other's. Same symbol,
              different namespace depth: `Session#serialize` against
              `ShopifyAPI::Auth::Session#serialize`. This is the relation the
              old leaf matching approximated by keeping only the last segment.

`member`      One name's segments are a proper *prefix* of the other's, *and*
              the claims corroborate — the longer name's leaf is named in the
              shorter record's note, or the notes overlap. `Session` matches
              `Session#serialize` this way. A container and its member are only
              the same change if the record says so, which is why this rule
              alone carries a corroboration requirement and `qualified` does
              not.

`mention`     One record has no label at all, and the other's leaf is named in
              its note or its quoted code. 17 records in the corpus carry
              before/after code and no symbol — `symbol` is optional on
              `call_pattern_changed`, deliberately, because it is derivable from
              the code. Those records can otherwise only ever match an exact
              code quote, so they read as unique findings and inflate the
              contested queue.

`prose`       The notes overlap above a threshold *and* the labels' word sets
              relate. This is the rule that joins the three spellings of the
              Ruby bump, and `Node version` to `Node.js support`.

              How strictly the words must relate depends on what kind of label
              it is. A label containing whitespace is free text a model wrote to
              describe a change — `Minimum Ruby Version Requirement` — where
              word choice is arbitrary, so one shared word plus agreeing notes
              is enough. A label with no whitespace is a code identifier, where
              a differing segment is a real difference, so the stricter subset
              relation is required. That distinction is load-bearing: relaxing
              it for identifiers merges `Session#serialize` into
              `Session.deserialize`, whose label words differ by exactly one
              element and whose notes in one real run are near-identical prose.

The corroboration requirements on the last two rules are what keep the matcher
from collapsing real distinctions. `Session#serialize` and `Session.deserialize`
are separate removals whose notes in one run are nearly identical prose, and
whose names differ by a prefix that a naive substring test would happily merge
("serialize" is a substring of "deserialize"). Reading names as segment
sequences and requiring a subset relation on label words rejects both traps.
`tests/test_match.py` holds them as explicit non-matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words that carry no signal about *which* change is being described. Kept
# short on purpose: an aggressive stoplist would let unrelated notes match.
STOPWORDS = frozenset("""
a an the this that these those is are was were be been being has have had
and or but if then than of to in on at by for with from as it its their there
you your we our will would should must can may not no longer now been
change changed changes removed removal new old use used using instead
""".split())

SPLIT = re.compile(r"[.#]|::")
WORD = re.compile(r"[A-Za-z0-9_]+")


def segments(label: str | None) -> list[str]:
    """Split a qualified name into its parts, lowercased.

    `ShopifyAPI::Auth::Session#serialize` -> [shopifyapi, auth, session, serialize]

    Reading a name as a sequence is what separates `Session#serialize` from
    `Session.deserialize` while still relating both to `Session`. A substring
    test cannot: `serialize` is a substring of `deserialize`.
    """
    if not label:
        return []
    return [p.lower() for p in SPLIT.split(label) if p.strip()]


def norm_label(label: str | None) -> str:
    """Case and punctuation stripped, for spotting one name spelled two ways."""
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def label_words(label: str | None) -> frozenset[str]:
    """The word set of a label, split on punctuation and camelCase.

    Prose labels are the ones that need this: `Minimum Ruby Version Requirement`
    and `Ruby version` relate by word subset, not by any identifier rule.
    """
    words: set[str] = set()
    for raw in WORD.findall(label or ""):
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|[0-9]+", raw) or [raw]
        words.update(p.lower() for p in parts)
    return frozenset(w for w in words if w and w not in STOPWORDS)


def norm_code(code: str | None) -> str:
    """All whitespace removed, so reformatting is not read as a different quote.

    The same normalisation grounding uses, and for the same reason: a model that
    joins a four-line call onto one line has quoted the guide, not invented it.
    """
    return re.sub(r"\s+", "", code or "")


def note_tokens(note: str | None) -> frozenset[str]:
    return frozenset(
        w.lower() for w in WORD.findall(note or "")
        if w.lower() not in STOPWORDS and len(w) > 1
    )


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Symmetric overlap. Kept for reporting; matching uses `overlap` below."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


MIN_SHARED = 3
"""How many content words two notes must share before their ratio means anything.

Without a floor, containment reads 1.0 whenever one note is two words long,
which would let a terse record merge with anything that happened to repeat it.
"""


def overlap(left: frozenset[str], right: frozenset[str]) -> float:
    """Containment: the shorter note drives the score, not the union.

    This is the difference between matching the Ruby bump and missing it. One
    run wrote `The minimum required Ruby version has been updated from 3.0 to
    3.2.`; another wrote that plus clauses about End of Life and the runtime
    needing to be upgraded first. Four of the shorter note's five content words
    appear in the longer one — but Jaccard divides by the union and scores it
    **0.27**, so the run that wrote the more thorough note is penalised for
    being thorough, and the two records stay apart. Containment scores the same
    pair 0.80, which is what a reader would say.

    The asymmetry is the normal case here, not an edge case: these runs differ
    much more in how much they write about a change than in which change they
    are describing.
    """
    if not left or not right:
        return 0.0
    shared = len(left & right)
    if shared < MIN_SHARED:
        return 0.0
    return shared / min(len(left), len(right))


def _is_free_text(label: str | None) -> bool:
    """True when a label is a phrase a model wrote, not a name the SDK exposes.

    Whitespace is the whole test, and it is enough: no identifier in any of
    these guides contains a space, and every free-text label in the corpus does
    — `Minimum Ruby Version Requirement`, `Response properties`, `Module
    loading`. Getting this wrong in the permissive direction is what would merge
    two real removals, so the test errs toward calling a label code.
    """
    return bool(label and any(c.isspace() for c in label))


@dataclass(frozen=True)
class Record:
    """One extracted change, reduced to what matching needs."""

    run: str
    guide: str
    index: int
    kind: str
    label: str
    note: str = ""
    before: str | None = None

    @property
    def leaf(self) -> str:
        parts = segments(self.label)
        return parts[-1] if parts else ""

    @property
    def display(self) -> str:
        """Something to put on a card when the record carries no symbol."""
        if self.label:
            return self.label
        if self.note:
            first = self.note.strip().split("\n")[0]
            return first[:70] + ("..." if len(first) > 70 else "")
        line = (self.before or "").strip().split("\n")[0]
        return line[:70] + ("..." if len(line) > 70 else "") if line else "(unlabelled)"


def same_claim(a: Record, b: Record, threshold: float = 0.30) -> str | None:
    """Return the name of the rule joining these two records, or None.

    Returning *why* rather than a bool is deliberate: the review tool shows the
    reason on each merged card, so a person adjudicating can see whether the
    matcher joined two records for a good reason or a coincidence.
    """
    seg_a, seg_b = segments(a.label), segments(b.label)
    notes = overlap(note_tokens(a.note), note_tokens(b.note))

    if seg_a and seg_a == seg_b:
        return "identical"

    if a.label and norm_label(a.label) and norm_label(a.label) == norm_label(b.label):
        return "spelling"

    code_a, code_b = norm_code(a.before), norm_code(b.before)
    if code_a and code_a == code_b:
        return "code"

    if seg_a and seg_b and seg_a != seg_b:
        short, long_, short_rec, long_rec = (
            (seg_a, seg_b, a, b) if len(seg_a) < len(seg_b) else (seg_b, seg_a, b, a)
        )
        # Same trailing path: one run simply qualified the name further.
        if long_[-len(short):] == short:
            return "qualified"
        # Container and member: the same change only if a record says so.
        if long_[: len(short)] == short:
            mentioned = long_rec.leaf and long_rec.leaf in note_tokens(short_rec.note)
            if mentioned or notes >= threshold:
                return "member"

    # An unlabelled record, identified by the symbol its own note names.
    if bool(seg_a) != bool(seg_b):
        blank, named = (a, b) if not seg_a else (b, a)
        # The note is matched token-for-token; the quoted code can only be
        # matched as a substring, so a short leaf is not allowed to try. `id`
        # occurs inside half the code in this corpus and means nothing.
        in_note = named.leaf and named.leaf in note_tokens(blank.note)
        in_code = (len(named.leaf) > 3
                   and named.leaf in norm_code(blank.before).lower())
        if in_note or in_code:
            return "mention"

    # Prose labels: the notes agree and the labels' words relate.
    words_a, words_b = label_words(a.label), label_words(b.label)
    if words_a and words_b and notes >= threshold:
        free_text = _is_free_text(a.label) or _is_free_text(b.label)
        related = (words_a & words_b) if free_text else None
        if related or words_a <= words_b or words_b <= words_a:
            return "prose"

    return None


@dataclass
class Claim:
    """One change, as agreed across whichever runs found it."""

    members: list[Record] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)

    @property
    def runs(self) -> set[str]:
        return {m.run for m in self.members}

    @property
    def label(self) -> str:
        """The most qualified spelling any run used — the most informative one.

        Also the key a verdict is stored against, so it has to be stable for a
        given set of members rather than depending on their order.
        """
        named = sorted(m.label for m in self.members if m.label)
        if named:
            return max(named, key=lambda s: (len(segments(s)), len(s)))
        return sorted(self.members, key=lambda m: (m.run, m.index))[0].display


def cluster(records: list[Record], threshold: float = 0.30) -> list[Claim]:
    """Union records that make the same claim, within and across runs.

    Union-find rather than pairwise grouping, because matching is not
    transitive on its own: `Session` joins `Session#serialize` and
    `Session.deserialize` although those two do not join each other. Treating
    them as one claim is the right answer — the guide documents one change to
    session persistence — but it only falls out if merges compose.
    """
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reasons: dict[tuple[int, int], str] = {}
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            why = same_claim(records[i], records[j], threshold)
            if why:
                reasons[(i, j)] = why
                parent[find(i)] = find(j)

    groups: dict[int, Claim] = {}
    for i, record in enumerate(records):
        groups.setdefault(find(i), Claim()).members.append(record)
    for (i, j), why in reasons.items():
        groups[find(i)].reasons.add(why)
    return list(groups.values())
