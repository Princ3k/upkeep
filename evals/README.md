# Guide-extraction eval

Ten real, published migration guides — four Shopify Ruby, four Shopify JS, two
Twilio — across Ruby, TypeScript and Python. Copied verbatim into `corpus/` so
the grounding check has an exact source to match against, with provenance in
`corpus/manifest.json`.

```bash
pip install -e '.[extract]'
export ANTHROPIC_API_KEY=sk-ant-...      # or run: ant auth login
python evals/extract.py --estimate       # ~$0.84 for all ten
python evals/extract.py --yes            # real run, overwrites extractions/
python evals/score.py                    # exits non-zero on any fabrication
```

`score.py` needs no key — it only reads the corpus and `extractions/`. Only
`extract.py` calls the API, and only the guide reader needs a model at all: the
pipeline itself (diff, index, plan, patch, verify) has no model in it and should
stay that way, which is why `anthropic` is an optional extra rather than a
dependency.

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
| Changes extracted | 88 across 10 guides |
| **Grounded** — every symbol and every line of before/after code appears in the guide | **88 / 88** |
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

It caught one immediately. `shopify-ruby-v10` scored **0.29**, because that
extraction was made from `grep` output of the guide's headings rather than the
full 231-line document. Re-reading the guide took it from 2 changes to 13 — a
GraphQL client refactor with before/after blocks and a five-row REST migration
table, all of it missed the first time. It now scores 1.86.

Two things are worth keeping from that. The proxy is crude and it still worked,
which is the argument for having one at all. And the miss was 85% of that
guide's content: **an extractor that skips most of a document still scores a
perfect 100% on grounding**, which is precisely the limit of what grounding
tells you.

`shopify-js-v8` at 0.62 and `twilio-python-upgrade` at 0.72 have not been
re-checked and may be the same problem.

**Semantic precision.** A symbol that appears in the guide but was not actually
removed passes grounding. So does a correct quotation attached to the wrong
claim.

Both need an independent grader — a different model, or a human writing
expectations before seeing any extraction. That is the next thing to run, and
until it exists these numbers say only that nothing was invented.

## What the extractions add up to

| Kind | Count |
| --- | --- |
| `symbol_removed` | 49 |
| `call_pattern_changed` | 25 |
| `semantics_changed` | 12 |
| `field_renamed` | 2 |

Routing every extracted symbol as if a consumer touched it: **2 Tier A, 27
Tier B, 49 Tier C.** Two automatic patches out of eighty-eight changes.

That is the shape of the whole finding. Migration guides are dense with things
worth telling a developer and nearly empty of things safe to fix for them —
which is why the near-term product is impact analysis, and why the model's job
here is reading prose rather than writing patches.


## The A/B: two real runs

Both arms were produced by `evals/extract.py` against the API, so unlike
`extractions/` they are machine-made and reproducible. No Anthropic credential
was available, so this compares two tiers of one provider rather than two
providers. Both got the identical flat schema.

| | hand-made | gemini-3.1-pro | gemini-3-flash |
| --- | --- | --- | --- |
| Changes | 88 | 79 | 93 |
| Grounded | 88/88 | **79/79** | **93/93** |
| Fabrications | 0 | **0** | **0** |
| `call_pattern_changed` | 25 | 8 | 18 |
| Tier A (auto-patchable) | **2** | **2** | **2** |
| Wall clock | — | 4m34s | ~3m |
| Cost | — | ~$0.30 | ~$0.08 |

**Nothing was fabricated by either model.** Across 172 machine-extracted changes
from ten real documents, every symbol and every line of quoted code was present
in its source. That is the result the grounding check exists to produce, and it
held without a single drop.

**Tier A is 2 in all three runs.** The substantive finding of this whole
exercise — migration guides are dense with things worth reporting and nearly
empty of things safe to fix — did not move across three independent extractors.
That is the number worth trusting here.

**The flash model extracted more than the pro model**, 93 against 79, and more
than twice as many call patterns. Which is a caution about assuming the bigger
model is better at a transcription task, not a recommendation: see below.

### The number that actually matters: 60%

```
python evals/compare.py runs/google-pro runs/google-flash
```

The two runs agree on **60% of the bare identifiers** either one named (49% on
exact labels, but that penalises writing `Session#serialize` where the other
wrote `ShopifyAPI::Auth::Session#serialize`). Each run against the hand-made set
agrees at 56-57%.

So: three extractors, all perfectly grounded, all fabricating nothing — and any
two of them disagree about what a document says roughly 40% of the time. **They
are not clean because they are accurate; they are clean because grounding only
catches invention, and none of them invented anything.** Recall is the problem,
it is large, and it is invisible to every automatic check here.

That is also the strongest evidence yet for an independent grader. The 58
single-run findings printed by `compare.py` are exactly the set a human should
adjudicate first, because each one is a miss by one side or an over-read by the
other.

### Three bugs these runs found in this repo

Every one was mine, not a model's, and none was visible from the hand-made set:

- **Reformatting read as fabrication.** Gemini joined a four-line `Session.new(...)`
  call onto one line and grounding dropped it as invented. Code is now compared
  with whitespace removed. The hand-made extractions were copy-pasted, so their
  line breaks always matched and the bug never fired.
- **A required field threw away correct work.** 19 records carrying exactly the
  right `before` and `after` were binned for not setting `symbol`, which is
  derivable from the `before` code. `symbol` is now optional.
- **Language aliases counted as errors.** Models wrote `ts` and `js` where the
  corpus said `typescript`; an exact-match guard called that five errors per run.
  Aliases are now normalised.

Running a second extractor was worth it for these alone, independent of which
model scored better.

## Is this publishable?

The harness is. The number is not.

`grounding.py`, the corpus, the mutation tests and `score.py` are real and
reusable, and the finding they support — that guides are dense with things worth
reporting and nearly empty of things safe to fix — rests on counting change kinds
and routing them, which does not depend on who did the extracting.

The **88/88** did, and the machine-made runs only partly fix it. Extraction is
now reproducible, which removes one objection. But the same author still wrote
the check that grades it, and the 60% cross-run agreement shows how little a
perfect grounding score constrains accuracy. Grounding
is blind to recall, as the v10 miss demonstrated at full strength, and blind to
meaning: a symbol that appears in the guide but was never removed passes, and so
does a correct quotation attached to the wrong claim. n=10, from three providers,
chosen partly because their guides were easy to fetch.

To make the number publishable: run `spec_from_guide` against the API so the
extraction is machine-produced and reproducible, have someone who has not seen
the extractions write expectations for each guide, and score recall and semantic
precision against those. Until then this measures one thing, and should only
claim one thing — **nothing was invented.**
