"""Compare two extraction runs over the same corpus.

Grounding alone cannot rank two backends: both can score 100% while one of them
read half the document. What separates them is how much they found, how much
survived grounding, how much came back malformed, and — most telling — whether
they agree on which symbols the guide names.

Symbol agreement is the useful column. Where both runs name the same symbol,
they corroborate each other. Where only one does, it is either a miss by the
other or an over-read by that one, and those are the records worth a human's
attention. Neither run is treated as the answer key.

    python evals/compare.py runs/google-pro runs/google-flash
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from upkeep.detect.grounding import check_grounding
from upkeep.models import MigrationSpec

ROOT = Path(__file__).parent


def label_of(change) -> str | None:
    for attribute in ("symbol", "path", "op", "param"):
        value = getattr(change, attribute, None)
        if value:
            return str(value)
    return None


def leaf_of(change) -> str | None:
    """The bare identifier, ignoring how far each run qualified the name.

    Exact labels understate agreement badly: one run wrote `Session#serialize`
    where another wrote `ShopifyAPI::Auth::Session#serialize`. Same symbol,
    different qualification. Comparing leaves separates real disagreement about
    what the document says from disagreement about how to spell it.
    """
    leaf = getattr(change, "leaf", None)
    if leaf:
        return leaf
    label = label_of(change)
    if not label:
        return None
    return label.replace("#", ".").replace("::", ".").split(".")[-1]


def load(directory: Path, stem: str) -> MigrationSpec | None:
    path = directory / f"{stem}.json"
    if not path.exists():
        return None
    return MigrationSpec.model_validate_json(path.read_text())


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    left_dir, right_dir = (ROOT / a for a in sys.argv[1:3])
    left_name, right_name = (a.split("/")[-1] for a in sys.argv[1:3])

    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    totals = {left_name: 0, right_name: 0, "both": 0,
              f"{left_name} only": 0, f"{right_name} only": 0}

    print(f"{'guide':<24}{left_name:>12}{right_name:>12}{'shared':>8}"
          f"{'L-only':>8}{'R-only':>8}")
    print("-" * 72)

    disagreements: list[tuple[str, str, str]] = []
    for entry in manifest["guides"]:
        stem = entry["file"][:-3]
        left, right = load(left_dir, stem), load(right_dir, stem)
        if left is None or right is None:
            print(f"{stem:<24}{'missing run':>40}")
            continue

        left_labels = {label_of(c) for c in left.changes} - {None}
        right_labels = {label_of(c) for c in right.changes} - {None}
        shared = left_labels & right_labels

        totals[left_name] += len(left.changes)
        totals[right_name] += len(right.changes)
        totals["both"] += len(shared)
        totals[f"{left_name} only"] += len(left_labels - right_labels)
        totals[f"{right_name} only"] += len(right_labels - left_labels)

        for label in sorted(left_labels - right_labels):
            disagreements.append((stem, left_name, label))
        for label in sorted(right_labels - left_labels):
            disagreements.append((stem, right_name, label))

        print(f"{stem:<24}{len(left.changes):>12}{len(right.changes):>12}"
              f"{len(shared):>8}{len(left_labels - right_labels):>8}"
              f"{len(right_labels - left_labels):>8}")

    print("-" * 72)
    print(f"{'TOTAL':<24}{totals[left_name]:>12}{totals[right_name]:>12}"
          f"{totals['both']:>8}{totals[f'{left_name} only']:>8}"
          f"{totals[f'{right_name} only']:>8}")

    union = totals["both"] + totals[f"{left_name} only"] + totals[f"{right_name} only"]
    if union:
        print(f"\nagreement (exact label): {totals['both'] / union:.0%}")

    # Again, ignoring how far each run qualified each name.
    shared_leaf = only_left = only_right = 0
    for entry in manifest["guides"]:
        stem = entry["file"][:-3]
        left, right = load(left_dir, stem), load(right_dir, stem)
        if left is None or right is None:
            continue
        l = {leaf_of(c) for c in left.changes} - {None}
        r = {leaf_of(c) for c in right.changes} - {None}
        shared_leaf += len(l & r)
        only_left += len(l - r)
        only_right += len(r - l)
    union_leaf = shared_leaf + only_left + only_right
    if union_leaf:
        print(f"agreement (bare identifier): {shared_leaf / union_leaf:.0%}"
              f"   shared={shared_leaf} left-only={only_left} right-only={only_right}")

    print(f"\nfound by only one run ({len(disagreements)}) — each is a miss by one "
          "side or an over-read by the other:")
    for stem, who, label in disagreements[:40]:
        print(f"   {stem:<24} {who:<14} {label}")
    if len(disagreements) > 40:
        print(f"   ... and {len(disagreements) - 40} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
