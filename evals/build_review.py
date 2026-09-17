"""Bundle the corpus, every run, and the disagreements into one payload.

The review page is static except for the verdicts a human records, so all the
reading happens here rather than in the browser.

The adjudication queue is built from *claims*, not from bare identifiers. The
first ten cards judged under identifier matching showed nine of ten were one
change under two names, which is a queue measuring spelling. `match.py` does
that grouping; the old identifier set is still emitted alongside it so the page
can show what the change bought.
"""

from __future__ import annotations

import json
from pathlib import Path

from match import Record, cluster
from upkeep.detect.grounding import check_grounding
from upkeep.models import MigrationSpec, Tier
from upkeep.router import plan

ROOT = Path(__file__).parent
RUNS = [("hand", "extractions"), ("pro", "runs/google-pro"), ("flash", "runs/google-flash")]


def leaf_of(change):
    leaf = getattr(change, "leaf", None)
    if leaf:
        return leaf
    for attribute in ("symbol", "path", "op", "param"):
        value = getattr(change, attribute, None)
        if value:
            return str(value).replace("#", ".").replace("::", ".").split(".")[-1]
    return None


def change_json(change, guide_text):
    report = check_grounding(
        MigrationSpec(id="x", provider="p", **{"from": "1", "to": "2"},
                      severity="breaking", changes=[change]),
        guide_text,
    )
    return {
        "kind": change.kind,
        "leaf": leaf_of(change),
        "label": (getattr(change, "symbol", None) or getattr(change, "path", None)
                  or getattr(change, "op", None) or getattr(change, "param", None) or ""),
        # Every other name the record carries. `ParamRequiredAdded` has an `op`
        # and a `param`, and runs disagree about which one names the change.
        "aliases": [str(v) for v in (getattr(change, "symbol", None),
                                     getattr(change, "path", None),
                                     getattr(change, "op", None),
                                     getattr(change, "param", None)) if v],
        "language": getattr(change, "language", None),
        "before": getattr(change, "before", None),
        "after": getattr(change, "after", None),
        "replacement": getattr(change, "replacement", None),
        "note": getattr(change, "note", "") or "",
        "grounded": report.results[0].grounded,
    }


def apply_verdicts(guides, runs):
    """Fold `verdicts.json` into the payload, resolving duplicate groups.

    A card judged `duplicate` names the card it should have merged with. Those
    links have to be followed before recall means anything: `pro` recorded the
    js-v8 version removal under `Supported API versions`, and `flash` recorded
    the same change as `2022-04` and `2022-07`. Counting the `2022-04` card's
    own found_by credits flash and silently marks pro as having missed a change
    it did record.

    So each contested card is tagged with what it contributes:

      truth_real   a genuine change, credited to every run in the merged group
      truth_agreed every run found it; the matcher just failed to merge the
                   cards, so it belongs with the agreed claims, not the queue
      skip         folds into a claim that is already counted

    Returns the seed verdict map for the page.
    """
    path = ROOT / "verdicts.json"
    if not path.exists():
        return {}
    recorded = {(v["guide"], v["label"]): v
                for v in json.loads(path.read_text())["verdicts"]}

    seed = {}
    for guide in guides:
        stem = guide["stem"]
        contested = {c["label"]: c for c in guide["claims"] if c["contested"]}

        parent = {label: label for label in contested}

        def find(label):
            while parent[label] != label:
                parent[label] = parent[parent[label]]
                label = parent[label]
            return label

        for label in contested:
            verdict = recorded.get((stem, label))
            if verdict and verdict["verdict"] == "duplicate":
                other = verdict.get("merges_with")
                if other in parent:
                    parent[find(label)] = find(other)

        members = {}
        for label in contested:
            members.setdefault(find(label), []).append(label)

        for root, group in members.items():
            found = set()
            for label in group:
                found |= set(contested[label]["found_by"])
            verdicts = {recorded.get((stem, m), {}).get("verdict") for m in group}
            for label in group:
                card = contested[label]
                card["verdict"] = recorded.get((stem, label), {}).get("verdict")
                card["merged_found_by"] = sorted(found)
                if "real" in verdicts:
                    card["counts_as"] = "truth_real" if label == root else "skip"
                elif len(found) >= len(runs):
                    card["counts_as"] = "truth_agreed" if label == root else "skip"
                else:
                    card["counts_as"] = "skip"

        for label, card in contested.items():
            if card.get("verdict"):
                seed[f"{stem}__{label}"] = card["verdict"]
    return seed


def main() -> None:
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    guides = []

    for entry in manifest["guides"]:
        stem = entry["file"][:-3]
        text = (ROOT / "corpus" / entry["file"]).read_text()
        runs, leaves, records = {}, {}, []

        for name, directory in RUNS:
            path = ROOT / directory / f"{stem}.json"
            if not path.exists():
                continue
            spec = MigrationSpec.model_validate_json(path.read_text())
            changes = [change_json(c, text) for c in spec.changes]

            sites = []
            from upkeep.models import CallSite, SiteKind
            for change in spec.changes:
                leaf = leaf_of(change)
                if leaf:
                    sites.append(CallSite(file="app", line=1, column=0,
                                          kind=SiteKind.attribute, name=leaf,
                                          expression=leaf, rooted=True,
                                          language=entry["language"]))
            tiers = {"A": 0, "B": 0, "C": 0}
            for item in plan(spec, sites, require_vectors=False):
                tiers[item.tier.value] += 1

            runs[name] = {"changes": changes, "severity": spec.severity.value,
                          "tiers": tiers}
            leaves[name] = {c["leaf"] for c in changes if c["leaf"]}
            records.extend(
                Record(run=name, guide=stem, index=i, kind=c["kind"],
                       label=c["label"], note=c["note"], before=c["before"],
                       aliases=tuple(c["aliases"]))
                for i, c in enumerate(changes)
            )

        agreement = []
        every = set().union(*leaves.values()) if leaves else set()
        for leaf in sorted(every):
            found_by = sorted(n for n, s in leaves.items() if leaf in s)
            agreement.append({"leaf": leaf, "found_by": found_by,
                              "contested": len(found_by) < len(leaves)})

        claims = []
        for claim in cluster(records):
            found_by = sorted(claim.runs)
            claims.append({
                "label": claim.label,
                "found_by": found_by,
                "contested": len(found_by) < len(runs),
                "reasons": sorted(claim.reasons),
                "members": [
                    {"run": m.run, "kind": m.kind, "label": m.label,
                     "note": m.note, "display": m.display}
                    for m in sorted(claim.members, key=lambda m: (m.run, m.index))
                ],
            })
        claims.sort(key=lambda c: (not c["contested"], c["label"].lower()))

        guides.append({
            "stem": stem, "text": text, "provider": entry["provider"],
            "language": entry["language"], "from": entry["from"], "to": entry["to"],
            "source": entry["source"], "runs": runs, "agreement": agreement,
            "claims": claims,
        })

    seed = apply_verdicts(guides, [n for n, _ in RUNS])
    payload = {"runs": [n for n, _ in RUNS], "guides": guides, "seed_verdicts": seed}
    body = json.dumps(payload, separators=(",", ":"))
    out = ROOT / "review-data.json"
    out.write_text(body)
    # review.html reads the payload from a script tag, because the artifact CSP
    # blocks fetch and XHR. Writing only the .json left the page with nothing to
    # load and no way to say so beyond its own empty state.
    (ROOT / "review-data.js").write_text("window.__REVIEW = " + body + ";\n")
    identifiers = sum(1 for g in guides for a in g["agreement"] if a["contested"])
    contested = sum(1 for g in guides for c in g["claims"] if c["contested"])
    claims = sum(len(g["claims"]) for g in guides)
    print(f"wrote {out} and review-data.js — {len(guides)} guides, {claims} claims, {contested} of them "
          f"contested ({identifiers} under the old identifier matching), "
          f"{len(seed)} seeded verdicts, {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
