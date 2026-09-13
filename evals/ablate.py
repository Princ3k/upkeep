"""Hold the model fixed, change one prompt rule, measure the difference."""
import collections
from pathlib import Path
from upkeep.detect.from_guide import GoogleBackend, SYSTEM
from upkeep.detect.flat import rebuild

STRONGER = SYSTEM.replace(
"""3. `call_pattern_changed` is for when the document shows the old call AND the
   shape that replaces it. Copy both verbatim — do not paraphrase, tidy,
   reformat, or complete them. If the document shows only the old call, that is
   `symbol_removed`. Set `language` to the SDK's language, not the provider's
   API. A pattern may come from a reference or appendix section as long as the
   document shows it; say so in `note`.""",
"""3. Whenever the document shows the old call AND the shape that replaces it,
   emit `call_pattern_changed` with both copied verbatim. This applies wherever
   the pair appears: paired code blocks, before/after columns in a table,
   Old:/New: lines, or inline in prose. Do not paraphrase, tidy, reformat or
   complete them.

   This is the most valuable record you can produce and the one most often
   missed. A change the document demonstrates with code is never
   `semantics_changed` — reach for that only when no replacement code is shown
   anywhere in the document. If the document shows only the old call, that is
   `symbol_removed`. Set `language` to the SDK's language, not the provider's
   API. A pattern may come from a reference or appendix section as long as the
   document shows it; say so in `note`.""")
assert STRONGER != SYSTEM, "prompt edit did not apply"

GUIDES = [("shopify-ruby-older", "shopify-ruby", "7", "8"),
          ("shopify-ruby-v10", "shopify-ruby", "9", "10"),
          ("twilio-python-upgrade", "twilio-python", "7", "8")]

backend = GoogleBackend(model="gemini-3.1-pro-preview")
print(f"{'guide':<24}{'prompt':<12}{'call_pat':>9}{'semantics':>11}{'total':>7}")
totals = collections.Counter()
for stem, provider, frm, to in GUIDES:
    text = Path(f"evals/corpus/{stem}.md").read_text()
    prompt = f"Provider: {provider}\nMigrating from {frm} to {to}.\n\nMigration guide:\n\n{text}"
    for label, system in (("baseline", SYSTEM), ("stronger", STRONGER)):
        flat = backend.run(system, prompt)
        changes, _ = rebuild(flat)
        cp = sum(1 for c in changes if c.kind == "call_pattern_changed")
        sem = sum(1 for c in changes if c.kind == "semantics_changed")
        totals[f"{label}_cp"] += cp; totals[f"{label}_sem"] += sem
        totals[f"{label}_tot"] += len(changes)
        print(f"{stem:<24}{label:<12}{cp:>9}{sem:>11}{len(changes):>7}", flush=True)
print(f"\n{'TOTAL':<24}{'baseline':<12}{totals['baseline_cp']:>9}"
      f"{totals['baseline_sem']:>11}{totals['baseline_tot']:>7}")
print(f"{'':<24}{'stronger':<12}{totals['stronger_cp']:>9}"
      f"{totals['stronger_sem']:>11}{totals['stronger_tot']:>7}")
