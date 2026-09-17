"""The adjudication is a test set, and the matcher has to keep passing it.

`evals/verdicts.json` records a human judgement of every contested claim against
the guide that produced it: 16 genuine misses, 3 naming duplicates, no
over-reads. That makes it the only ground truth this eval has, and it is worth
more as a regression test than as a table in a README.

The check that matters is the one that failed. Teaching `match.py` to compare
every name a record carries merged `ShopifyAPI::Session.new` with
`ShopifyAPI::Session.temp` — both `param_required_added`, both naming
`api_version` — and silently erased a real miss, because `hand` recorded `.new`
and not `.temp`. Nothing in the unit tests could have caught that: the records
come from three real runs over a real document, and the damage only shows when
you ask whether an adjudicated miss is still visible as a miss.

So: every claim judged a genuine miss must still be one claim, still contested,
and still found by exactly the runs that found it. A matcher change that merges
one of them away fails here, whatever it does to the agreement figure.
"""

import json
from pathlib import Path

import pytest

from match import Record, cluster

EVALS = Path(__file__).resolve().parent.parent / "evals"
RUNS = [("hand", "extractions"), ("pro", "runs/google-pro"), ("flash", "runs/google-flash")]
NAME_FIELDS = ("symbol", "path", "op", "param")

pytestmark = pytest.mark.skipif(
    not (EVALS / "verdicts.json").exists() or not (EVALS / "corpus" / "manifest.json").exists(),
    reason="eval corpus or verdicts not present",
)


def records_for(stem):
    out = []
    for run, directory in RUNS:
        path = EVALS / directory / f"{stem}.json"
        if not path.exists():
            continue
        for i, change in enumerate(json.loads(path.read_text())["changes"]):
            names = [str(change[f]) for f in NAME_FIELDS if change.get(f)]
            out.append(Record(run=run, guide=stem, index=i, kind=change["kind"],
                              label=names[0] if names else "",
                              note=change.get("note") or "",
                              before=change.get("before"),
                              aliases=tuple(names)))
    return out


@pytest.fixture(scope="module")
def clustered():
    manifest = json.loads((EVALS / "corpus" / "manifest.json").read_text())
    return {entry["file"][:-3]: cluster(records_for(entry["file"][:-3]))
            for entry in manifest["guides"]}


@pytest.fixture(scope="module")
def judged():
    return json.loads((EVALS / "verdicts.json").read_text())["verdicts"]


def test_every_adjudicated_miss_is_still_a_contested_claim(clustered, judged):
    """A merge that swallows a judged miss reports better agreement and is wrong."""
    runs = {name for name, _ in RUNS}
    for verdict in judged:
        if verdict["verdict"] != "real":
            continue
        claims = [c for c in clustered[verdict["guide"]] if c.label == verdict["label"]]
        assert len(claims) == 1, (
            f"{verdict['guide']}/{verdict['label']} judged a real miss, but the "
            f"matcher now produces {len(claims)} claims with that label")
        assert claims[0].runs < runs, (
            f"{verdict['guide']}/{verdict['label']} was judged a change some runs "
            f"missed, but the matcher now has every run finding it")


def test_no_two_adjudicated_misses_share_a_claim(clustered, judged):
    """Distinct judged changes must stay distinct, however alike their prose."""
    by_guide = {}
    for verdict in judged:
        if verdict["verdict"] == "real":
            by_guide.setdefault(verdict["guide"], []).append(verdict["label"])
    for guide, labels in by_guide.items():
        for claim in clustered[guide]:
            members = [l for l in labels if l == claim.label]
            assert len(members) <= 1


def test_the_two_sibling_session_constructors_stay_apart(clustered):
    """The regression the adjudication caught, pinned at corpus scale.

    Session.new and Session.temp each get their own before/after block in the
    guide. hand recorded the first and missed the second, and an alias-to-alias
    match on their shared `api_version` parameter merged them.
    """
    claims = {c.label: c for c in clustered["shopify-ruby-older"]}
    temp = claims.get("ShopifyAPI::Session.temp")
    assert temp is not None, "Session.temp merged away into another claim"
    assert "hand" not in temp.runs, "hand missed Session.temp; a merge hid that"


def test_the_corpus_still_produces_the_recorded_shape(clustered, judged):
    """The headline numbers, so a change that moves them has to say so."""
    claims = [c for cs in clustered.values() for c in cs]
    contested = [c for c in claims if len(c.runs) < 3]
    assert len(claims) == 70, f"claim count moved to {len(claims)}"
    assert len(contested) == 19, f"contested count moved to {len(contested)}"
    assert len(judged) == 19
    assert sum(1 for v in judged if v["verdict"] == "real") == 16
    assert sum(1 for v in judged if v["verdict"] == "overread") == 0
