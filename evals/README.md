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
Tier B, 49 Tier C** — 78 work items, which is what `score.py` prints. Two
automatic patches out of eighty-eight changes.

Only 76 of the 88 changes name a symbol a consumer could touch; the 12
`semantics_changed` records — a Ruby version bump, a dropped platform — name
nothing to index, so they produce no call site and no work item. That is why the
review tool reports **61** Tier C for the same corpus and is also right:
`build_review.py` falls back to any label a record carries, so those 12 route as
well, and 49 + 12 = 61. Both numbers are correct for what they count; neither is
quotable without saying which.

(The count is 78 rather than 76 because one guide, `shopify-ruby-v10`, has a
change that matches two sites.)

That is the shape of the whole finding. Migration guides are dense with things
worth telling a developer and nearly empty of things safe to fix for them —
which is why the near-term product is impact analysis, and why the model's job
here is reading prose rather than writing patches.


## The A/B: two real runs

Both arms were produced by `evals/extract.py` against the API. No Anthropic
credential was available, so this compares two tiers of one provider rather than
two providers. Both got the identical flat schema.

| | hand-made | gemini-3.1-pro | gemini-3-flash |
| --- | --- | --- | --- |
| Changes | 88 | 91 | 93 |
| Grounded | 88/88 | **91/91** | **93/93** |
| Fabrications | 0 | **0** | **0** |
| `call_pattern_changed` | 25 | 23 | 18 |
| Tier A (auto-patchable) | **2** | **2** | **2** |

**Nothing was fabricated by either model.** Across 184 machine-extracted changes
from ten real documents, every symbol and every line of quoted code was present
in its source, and nothing was dropped.

**Tier A is 2 in all three runs.** The substantive finding of this whole
exercise — migration guides are dense with things worth reporting and nearly
empty of things safe to fix — did not move across three independent extractors.

**The two models are indistinguishable here.** 91 against 93 is inside the
measured run-to-run spread (below), and pro produces slightly *more* call
patterns than flash. An earlier version of this file claimed flash extracted
substantially more; that was a measurement error, described next.

### The error that produced the first comparison

The first scored pro run was written at 22:55. The fix that made `symbol`
optional on `call_pattern_changed` landed at 22:59. The flash run was written at
23:00.

So pro was scored on pre-fix data and flash on post-fix data. Pro's call
patterns were being discarded as malformed for an unset field; flash's survived.
That single confound produced "8 against 18 call patterns", which then produced
a false model comparison, which then produced a false variance explanation when
an ablation failed to reproduce it.

The re-run meant to fix this was issued and silently never executed. It was
"verified" by counting ten files in the output directory — which is exactly what
a stale directory also looks like. **Counting outputs is not verifying them;
compare timestamps against the code that produced them.** `evals/score.py` reads
whatever is on disk and cannot know it is old.

### Variance, measured

Three passes of gemini-3.1-pro over the three most code-dense guides, all
post-fix:

| guide | totals | call patterns | stdev |
| --- | --- | --- | --- |
| shopify-ruby-older | 14, 14, 15 | 4, 4, 4 | 0.58 |
| shopify-ruby-v10 | 12, 13, 12 | 7, 6, 7 | 0.58 |
| twilio-python-upgrade | 19, 18, 16 | 3, 3, 3 | 1.53 |

**Variance is small**: at most 3 changes on any guide, and call-pattern counts
are identical across passes. An earlier claim here that variance swallowed the
pro/flash gap was wrong — the gap was the confound above. Repeated passes remain
worth running, because two disagreeing passes would have exposed that confound
immediately.

### The number that actually matters, and what it was measuring

```
python evals/compare.py runs/google-pro runs/google-flash
```

An earlier version of this section put that number at **60%** and concluded that
any two runs disagree about a document "roughly 40% of the time". Neither figure
came out of `compare.py`. What it actually prints, for every pairing:

| | exact label | bare identifier | claim |
| --- | --- | --- | --- |
| hand vs pro | 32% | 43% | **68%** |
| hand vs flash | 52% | 56% | **61%** |
| pro vs flash | 41% | 48% | **78%** |

The first two columns were the ones being quoted, and both are mostly measuring
spelling. The third matches on the *claim* — `match.py`, described below — and
is the figure worth carrying.

Across all three runs at once the same correction applies: **91 contested
identifiers become 31 contested claims**, and three-way agreement goes from 33%
to 60%.

So: three extractors, all perfectly grounded, all fabricating nothing — and any
two of them still disagree about a fifth to two-fifths of what a document says.
**They are not clean because they are accurate; they are clean because grounding
only catches invention, and none of them invented anything.** Recall is the
problem, it is large, and it is invisible to every automatic check here.

That is still the strongest evidence for an independent grader. The 31 contested
claims are the set a human should adjudicate first, because each is a miss by one
side or an over-read by the other — and now each of them is plausibly one or the
other, rather than nine times in ten a disagreement about hyphenation.

### Matching on the claim, not the label

The first ten cards judged in the review tool were contested *identifiers*, and
nine of the ten were the same change under a different label. That queue was
measuring spelling. `evals/match.py` groups records by what they claim, under six
rules, each named in the output so a reviewer can check the merge as well as the
claim:

| rule | joins |
| --- | --- |
| `identical` | the same name, segment for segment |
| `spelling` | the same name once case and punctuation go — `graphql-client` / `GraphQL client` |
| `code` | the same `before` block quoted, whitespace ignored |
| `qualified` | one name a suffix of the other — `Session#serialize` / `ShopifyAPI::Auth::Session#serialize` |
| `member` | one name a proper prefix of the other, with corroboration — `Session` / `Session#serialize` |
| `mention` | an unlabelled record, matched by the symbol its own note names |

`shopify-ruby-v16` now collapses to exactly two claims, both found by all three
runs, which is what that guide documents: one Ruby version bump and one removal
of session serialization. The queue's first card is `ActiveResource` — the one
real miss among the adjudicated ten — instead of nine spelling duplicates.

**The number is not tuned.** Agreement sits at 60% for every note-similarity
threshold from 0.20 to 0.75; the rules, not the ratio, do the discriminating.

Two things the corpus forced, and both are in `tests/test_match.py`:

- **Note similarity is containment, not Jaccard.** One run wrote the Ruby bump in
  a sentence; another added why it happened and what it meant. Four of the
  shorter note's five content words appear in the longer one — but dividing by
  the union scores that **0.27**, which penalises the run that wrote the more
  thorough note, and the two records stay apart. Containment scores the same pair
  0.80. The asymmetry is the normal case here, not an edge case. A floor of three
  shared words stops a two-word note containing its way into anything.
- **The permissive rules apply to free text only.** `Session#serialize` and
  `Session.deserialize` share three of four label words and, in one real run,
  near-identical notes. Merging them would erase a real removal, and a naive
  substring test merges them outright, since `serialize` is a substring of
  `deserialize`. A label containing whitespace is prose a model wrote, where word
  choice is arbitrary; a label without one is a name, where a differing segment
  is a real difference.

Most of the 31 cases in `tests/test_match.py` are non-matches, for the same
reason `test_grounding.py` is mostly mutations: a matcher that merges everything
reports perfect agreement and measures nothing. **The separations are what make
the number mean anything.**

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
the check that grades it, and even at the corrected 60% claim agreement, two
runs that invented nothing still disagree about two-fifths of what they read —
which is how little a perfect grounding score constrains accuracy. Grounding
is blind to recall, as the v10 miss demonstrated at full strength, and blind to
meaning: a symbol that appears in the guide but was never removed passes, and so
does a correct quotation attached to the wrong claim. n=10, from three providers,
chosen partly because their guides were easy to fetch.

To make the number publishable: run `spec_from_guide` against the API so the
extraction is machine-produced and reproducible, have someone who has not seen
the extractions write expectations for each guide, and score recall and semantic
precision against those. Until then this measures one thing, and should only
claim one thing — **nothing was invented.**

## Does the prompt explain pro's low call-pattern count?

No. It is run-to-run variance, and the check that showed it also undermines the
A/B comparison above.

The pro run produced 8 `call_pattern_changed` against flash's 18, and **zero** on
the three most code-dense guides. The obvious suspect was rule 2 of the prompt —
"use `semantics_changed` for anything that does not fit" — being followed more
conservatively. So the same model was re-run on those same three guides, once
with the current prompt and once with a rule 3 rewritten to insist that a change
the document demonstrates with code is never `semantics_changed`:

| guide | prompt | call patterns | total |
| --- | --- | --- | --- |
| shopify-ruby-older | baseline | 4 | 14 |
| shopify-ruby-older | stronger | 10 | 14 |
| shopify-ruby-v10 | baseline | 6 | 12 |
| shopify-ruby-v10 | stronger | 6 | 13 |
| twilio-python-upgrade | baseline | 3 | 16 |
| twilio-python-upgrade | stronger | 1 | 6 |

The baseline column is the finding. **Those three guides produced 0 call patterns
in the scored run and 13 here — same model, same prompt, same documents.** Whatever
suppressed them was not the wording.

Two consequences:

- **The pro-versus-flash gap is inside the noise.** 79 against 93 on single runs
  cannot support "flash extracted more", and this README previously said so.
  Ranking two extractors needs repeated runs and a variance estimate, not one
  pass each.
- **The stronger prompt is not simply better.** It doubled call patterns on one
  guide, changed nothing on another, and on the third cut total extraction from
  16 changes to 6. A rule that makes one record type more attractive can suppress
  everything else.

Reproduce with `evals/ablate.py`.

## Watching a run happen

```bash
pip install -e '.[extract]'
export GEMINI_API_KEY=...            # or ANTHROPIC_API_KEY
python evals/serve.py --open         # http://127.0.0.1:8765
```

Standard library only — no web framework — so watching a run costs this project
no dependency it would not otherwise carry. Pick a backend, a model and which
guides to run; the page streams each guide as it lands with its kind breakdown,
and shows **anything dropped as ungrounded right where it happens**, quoting the
text that was not in the source. That is the moment worth seeing live: a model
inventing code, caught at the point it does it.

The **Repeats** field is the reason this exists rather than a progress bar.
Running a configuration several times and reporting the spread is the
measurement that was missing when a single pass per extractor produced a
pro-versus-flash gap that turned out to be noise. The page prints the widest
spread on any one guide, which is the number a comparison has to beat before it
means anything.

## The review tool

`evals/build_review.py` bundles the corpus, every run and the contested claims
into `review-data.json` (and `review-data.js`, which is what the page actually
loads — the artifact CSP blocks fetch, and for a while the builder wrote only the
`.json`, so the page had nothing to read). `evals/review.html` is a published
artifact over it: the runs side by side, each guide's source next to what every
run extracted, and an adjudication queue for the claims only some runs made.

That queue is the point. Grounding proves nothing was invented and says nothing
about what was missed, so recall has to come from a person reading the source.
Each verdict is stored per claim and the page computes per-run recall from the
confirmed set as judging proceeds. Every card lists what each run called the
change and which rule merged them, so the merge is checkable too.

### What the first ten cards showed

Nine of ten were **the same change under a different label**, and one was a
genuine miss. Nobody over-read anything.

| | |
| --- | --- |
| `Minimum Ruby Version Requirement` / `Ruby version` / `ruby` | one Ruby 3.0→3.2 bump, three labels |
| `Session` | flash named the class, hand named its two removed methods |
| `WebhookHandler` | flash named the replacement, hand the removed class |
| `GraphQL client` / `graphql-client` | the same gem deprecation, hyphenated differently |
| **`ActiveResource`** | **real** — pro and flash recorded no equivalent under any label |

So the contested set was mostly an artifact of matching on bare identifiers, and
the agreement figures understated how consistently these models read the same
document. **That is now fixed** — `match.py` above merges those cases before a
reviewer sees them, and the queue went from 91 cards to 31. Judging ten cards to
discover the queue was measuring the wrong thing was worth more than judging
ninety of them would have been.

Three fixes came out of judging them. The verdict vocabulary had no way to say
"same change, different name" — the most common answer — so the binary forced a
wrong answer either way; it exists now and duplicates leave the truth set rather
than penalising whichever run spelled it differently. The page's own verdict
writes were failing silently for any label containing a space, because document
ids reject them and the failure is swallowed to keep the page quiet: a verdict
on `Minimum Ruby Version Requirement` looked saved and vanished on reload. And
the queue itself was rebuilt on claims, which is what the rest of this section
is about.

The `duplicate` verdict stays, for the merges the matcher does not make. Each one
it is used on now is a case worth teaching `match.py`, rather than the routine
answer.
