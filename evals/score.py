"""Score guide extractions on checks a machine can settle.

Every metric here is decided against the guide text itself, not against an
opinion about what the guide meant, because the extractions in `extractions/`
and any hand-written expectation would come from the same model in the same
session. A score that needs a judgement call would be that model grading its own
homework.

What this measures:

  grounded       every symbol and every line of before/after code appears in
                 the guide. This is the metric that matters: an invented
                 `after` is the one failure that corrupts real source.
  language       the language on a call pattern matches the guide's SDK.
  schema         the extraction validates as a MigrationSpec.
  patchable      how much of the result could ever be applied automatically.

What it cannot measure: recall. A guide whose changes were silently skipped
scores perfectly. That needs an independent grader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from upkeep.detect.grounding import check_grounding
from upkeep.models import MigrationSpec, Tier
from upkeep.router import plan

ROOT = Path(__file__).parent


def main() -> int:
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    rows, fabrications, wrong_language = [], [], []
    totals = {"changes": 0, "grounded": 0, "kinds": {}}

    for entry in manifest["guides"]:
        stem = entry["file"][:-3]
        guide = (ROOT / "corpus" / entry["file"]).read_text()
        extraction = ROOT / "extractions" / f"{stem}.json"
        if not extraction.exists():
            rows.append((stem, "-", "NO EXTRACTION", "", ""))
            continue

        spec = MigrationSpec.model_validate_json(extraction.read_text())
        report = check_grounding(spec, guide, guide=stem)

        for result in report.fabricated:
            fabrications.append((stem, result.label, result.missing))

        bad_language = [
            c for c in spec.changes
            if getattr(c, "language", None) not in (None, entry["language"])
        ]
        wrong_language += [(stem, c.symbol, c.language) for c in bad_language]

        for change in spec.changes:
            totals["kinds"][change.kind] = totals["kinds"].get(change.kind, 0) + 1
        totals["changes"] += report.total
        totals["grounded"] += report.grounded

        rows.append((
            stem,
            str(report.total),
            f"{report.grounded}/{report.total}",
            f"{report.rate:>6.0%}",
            "ok" if not bad_language else f"{len(bad_language)} WRONG",
        ))

    print(f"{'guide':<24}{'chg':>4}{'grounded':>11}{'rate':>8}   language")
    print("-" * 62)
    for row in rows:
        print(f"{row[0]:<24}{row[1]:>4}{row[2]:>11}{row[3]:>8}   {row[4]}")

    rate = totals["grounded"] / totals["changes"] if totals["changes"] else 1.0
    print("-" * 62)
    print(f"{'TOTAL':<24}{totals['changes']:>4}"
          f"{str(totals['grounded']) + '/' + str(totals['changes']):>11}{rate:>8.0%}")

    print("\nby kind:")
    for kind, n in sorted(totals["kinds"].items(), key=lambda x: -x[1]):
        print(f"   {kind:<24}{n:>4}")

    print(f"\nfabrications: {len(fabrications)}")
    for stem, label, missing in fabrications:
        print(f"   {stem}: {label}")
        for item in missing:
            print(f"       not in guide -> {item}")

    print(f"wrong language: {len(wrong_language)}")
    for stem, symbol, language in wrong_language:
        print(f"   {stem}: {symbol} declared {language}")

    # What would any of this actually let you do?
    tiers: dict[str, int] = {}
    for entry in manifest["guides"]:
        stem = entry["file"][:-3]
        extraction = ROOT / "extractions" / f"{stem}.json"
        if not extraction.exists():
            continue
        spec = MigrationSpec.model_validate_json(extraction.read_text())
        from upkeep.models import CallSite, SiteKind

        sites = []
        for change in spec.changes:
            leaf = getattr(change, "leaf", None) or getattr(change, "old_name", None)
            if leaf:
                sites.append(CallSite(
                    file="app", line=1, column=0, kind=SiteKind.attribute,
                    name=leaf, expression=leaf, rooted=True,
                    language=entry["language"],
                ))
        for item in plan(spec, sites, require_vectors=False):
            tiers[item.tier.value] = tiers.get(item.tier.value, 0) + 1

    print("\nif a consumer touched every extracted symbol, routing would be:")
    for tier in sorted(tiers):
        print(f"   Tier {tier}: {tiers[tier]}")
    print(f"   auto-merge patches: {tiers.get(Tier.A.value, 0)}")

    return 1 if fabrications or wrong_language else 0


if __name__ == "__main__":
    sys.exit(main())
