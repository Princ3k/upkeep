# Guide-extraction eval

Ten real, published migration guides — four Shopify Ruby, four Shopify JS, two
Twilio — across Ruby, TypeScript and Python. Copied verbatim into `corpus/` so
the grounding check has an exact source to match against, with provenance in
`corpus/manifest.json`.

```bash
python evals/score.py     # exits non-zero on any fabrication
```

## What is measured, and why only this

**The extractions in `extractions/` were produced by Claude Opus 5 reading each
guide in a session, not through an automated API run** (this environment has no
key; `upkeep.detect.from_guide.spec_from_guide` is the code path that reproduces
them). That makes one whole class of metric worthless here: if the same model
writes both the extraction and the expected answer, any score needing a
judgement call is that model grading its own homework, and it would come back
near 100% while telling you nothing.

So every metric below is settled against the guide text itself:

| Metric | Result |
| --- | --- |
| Changes extracted | 77 across 10 guides |
| **Grounded** — every symbol and every line of before/after code appears in the guide | **77 / 77** |
| Fabrications | 0 |
| Language correctly matched to the guide's SDK | 10 / 10 |
| Schema-valid | 10 / 10 |

Grounding is the metric that matters. A missed change is a gap; an invented
`after` is a corrupting patch written from a document that never said it. It now
runs at extraction time too, not just here — `spec_from_guide` drops what it
cannot find in the source.

### The score is only worth what the check can reject

A clean 100% on a check its own author designed deserves suspicion, so
`tests/test_grounding.py` fires four real failure shapes at it: invented code, a
single altered identifier inside an otherwise-real line, a plausible symbol the
guide never mentions, and code lifted from a different document. All four are
caught, and re-indented quotations are correctly *not* caught. The metric can
fail, so the 100% carries information.

## What is not measured

**Recall.** A guide whose changes were silently skipped scores a perfect 100%
here. The crude proxy in the scorer counts lines containing removal/rename
language and compares that to records produced — enough to catch gross misses,
nothing more.

It caught one immediately. `shopify-ruby-v10` scores **0.29**, and the cause is
known: that extraction was made from `grep` output of the guide's headings rather
than from the full 231-line document. It is left in the corpus unfixed, because
a shortcut that produces low recall is exactly what an eval is for, and quietly
patching it would make the headline number prettier and the corpus less honest.

**Semantic precision.** A symbol that appears in the guide but was not actually
removed passes grounding. So does a correct quotation attached to the wrong
claim.

Both need an independent grader — a different model, or a human writing
expectations before seeing any extraction. That is the next thing to run, and
until it exists these numbers say only that nothing was invented.

## What the extractions add up to

| Kind | Count |
| --- | --- |
| `symbol_removed` | 47 |
| `call_pattern_changed` | 19 |
| `semantics_changed` | 9 |
| `field_renamed` | 2 |

Routing every extracted symbol as if a consumer touched it: **2 Tier A, 19
Tier B, 47 Tier C.** Two automatic patches out of seventy-seven changes.

That is the shape of the whole finding. Migration guides are dense with things
worth telling a developer and nearly empty of things safe to fix for them —
which is why the near-term product is impact analysis, and why the model's job
here is reading prose rather than writing patches.
