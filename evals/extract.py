"""Run the guide extractor over the corpus, for real, against the API.

This is what turns evals/ from a worked example into a measurement: the
extractions become machine-produced and reproducible instead of something a
model wrote in a session. Everything in `extractions/` is overwritten.

    pip install -e '.[extract]'
    export ANTHROPIC_API_KEY=sk-ant-...        # or: ant auth login
    python evals/extract.py --estimate          # what it will cost
    python evals/extract.py --yes               # actually run it
    python evals/score.py                       # grade the result
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from upkeep.detect.from_guide import spec_from_guide

ROOT = Path(__file__).parent
# claude-opus-5, $5 per Mtok in / $25 per Mtok out.
IN_PER_MTOK, OUT_PER_MTOK = 5.0, 25.0


def estimate(guides: list[dict]) -> None:
    chars = sum(len((ROOT / "corpus" / g["file"]).read_text()) for g in guides)
    in_tokens = chars // 4 + 600 * len(guides)      # guides + system prompt each
    out_tokens = 3000 * len(guides)                 # extraction + adaptive thinking
    cost = in_tokens / 1e6 * IN_PER_MTOK + out_tokens / 1e6 * OUT_PER_MTOK
    print(f"  {len(guides)} guides, ~{in_tokens:,} input + ~{out_tokens:,} output tokens")
    print(f"  rough cost: ${cost:.2f}   (a real run will differ; thinking is variable)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Run a single guide by file stem.")
    parser.add_argument("--estimate", action="store_true", help="Price it and stop.")
    parser.add_argument("--yes", action="store_true", help="Confirm spending money.")
    parser.add_argument("--model", default=None, help="Override the model.")
    args = parser.parse_args()

    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    guides = manifest["guides"]
    if args.only:
        guides = [g for g in guides if g["file"][:-3] == args.only]
        if not guides:
            print(f"no guide with stem {args.only!r}", file=sys.stderr)
            return 2

    if args.estimate:
        estimate(guides)
        return 0
    if not args.yes:
        estimate(guides)
        print("\n  This calls the API and costs real money. Re-run with --yes.")
        return 1

    out_dir = ROOT / "extractions"
    out_dir.mkdir(exist_ok=True)
    dropped_total = 0

    for entry in guides:
        stem = entry["file"][:-3]
        text = (ROOT / "corpus" / entry["file"]).read_text()
        kwargs = {"model": args.model} if args.model else {}

        spec, report = spec_from_guide(
            text,
            provider=entry["provider"],
            from_version=entry["from"],
            to_version=entry["to"],
            **kwargs,
        )
        (out_dir / f"{stem}.json").write_text(
            json.dumps(spec.model_dump(by_alias=True, mode="json"), indent=2) + "\n"
        )

        dropped = len(report.fabricated)
        dropped_total += dropped
        flag = f"   {dropped} dropped as ungrounded" if dropped else ""
        print(f"  {stem:<26} {len(spec.changes):>3} changes{flag}")
        for result in report.fabricated:
            print(f"      dropped {result.label}: {result.missing[:1]}")

    print(f"\n  wrote {len(guides)} extractions; {dropped_total} change(s) dropped.")
    print("  now run: python evals/score.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
