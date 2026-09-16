# upkeep

**Watch a provider's API surface, find the call sites it breaks, write the migration, and prove it works before a human ever looks at it.**

Dependabot bumps `^2.4.0` to `^3.0.0` and leaves your code broken. The gap between "the version changed" and "the code is migrated" is the whole product.

Status: **v0**. One deterministic codemod, two verification checks, no model in the patch path. It runs end to end.

---

## How it works

```
detect ─→ normalize ─→ index ─→ plan ─→ patch ─→ verify ─→ deliver
```

Two halves that only ever talk through a **Migration Spec**, which means either
side can be sold or run independently:

| Stage | What it does |
| --- | --- |
| `detect` | Diff two versions of a provider's OpenAPI document into typed change records. |
| `index` | Parse consumer code, resolve the SDK's imports, follow symbol flow, tag every provider-touching expression. |
| `plan` | Join the spec against the index. Assign a tier **per call site**, not per repo. |
| `patch` | Run a deterministic AST transform, scoped to positions the indexer proved. |
| `verify` | Compile, run the existing suite unchanged, score confidence. |
| `deliver` | Render a PR body — or an issue, when nothing could be proven. |

### The three tiers

| Tier | Meaning | Ships as |
| --- | --- | --- |
| **A** | Deterministic codemod. No model involved. | Pull request |
| **B** | Needs a model, or needs a codemod that doesn't exist yet. | Draft PR |
| **C** | Analysis only — upkeep writes no code. | Issue |

Every uncertainty routes *down*, toward a human. Losing volume is survivable;
losing trust isn't.

---

## Three design rules

**1. Never patch what you cannot root.**

The indexer marks each site `rooted` only when it can prove the expression
descends from a provider call, by following an assignment, a `for` target, or a
comprehension target. Everything else is unrooted and escalates:

```python
invoice = acme.Invoice.retrieve(id)
sum(line.amount for line in invoice.lines)   # rooted   → patched
payload["amount"]                            # unrooted → escalated, never touched
```

Both are spelled `amount`. A blanket find-and-replace gets one of them wrong,
passes review, and breaks production.

**2. Never guess a rename — and never trust your own guess.**

A property vanished and another appeared. That *might* be a rename. upkeep only
says so when one removed and one added property uniquely share a type signature
*and* the container isn't just a bag of same-shaped peers.

That rule was written against a fixture and then measured against a year of
Stripe's real spec, where **2 of 5 inferred renames were wrong**:

| Claimed | Reality |
| --- | --- |
| `tipping.bgn` → `tipping.gip` | A map of ISO-4217 currencies. Bulgaria joined the euro; Gibraltar arrived separately. Renaming would silently repoint a merchant's tipping config at the wrong currency. |
| `promotion_codes.coupon` → `customer_account` | Paired on type `string` and picked the wrong partner; the true successor is `promotion`, an object. |

The first is now caught by the peer-set guard. **The second is not, and probably
never will be by static means** — which is the real lesson. So a rename upkeep
worked out for itself is marked `inferred` and can never reach Tier A. It is
evidence for a reviewer, not an instruction for a codemod.

A human who has checked a diff against the provider's migration guide promotes
it with `--declared`. Against unvouched real Stripe data upkeep produces **zero**
auto-merge PRs, which is the honest answer — and it is why a provider-published
Migration Spec is the product, not a nice-to-have.

**3. Never promise a patch you cannot write.**

The router decides a tier; the runner writes the patch. If the router promised
Tier A for a rule nobody implemented, the work item would vanish silently — no
patch, no escalation, nothing in the PR body. That looks like success, which
makes it the worst failure mode available. So the router consults
`patch/registry.py` and demotes instead. A test enforces it.

---

## Try it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# Stage a consumer repo that is broken against the provider's new surface
cp -r tests/fixtures/consumer /tmp/demo

# 1. Diff two API versions into a Migration Spec
upkeep detect -p acme --from v1 --to v2 \
  --spec-dir tests/fixtures/specs --vector test_billing.py --out /tmp/spec.json

# 2. See what the consumer touches, and what can be proven
upkeep index /tmp/demo -p acme

# 3. Migrate and verify
upkeep run /tmp/demo --spec /tmp/spec.json --write
```

The fixture consumer's test suite is **red** before the migration and **green**
after, with no test file modified. That round trip is `test_end_to_end.py`.

---

## What's deliberately missing

| Not built | Why |
| --- | --- |
| Tier B model-assisted patching | Pointless before contract replay exists to verify it. |
| Contract replay | The next thing to build. Record real request/response pairs, assert outcomes match across versions. |
| Provider console + burndown | The revenue side. Needs consumer-side usage data first. |
| GitHub App delivery | `delivery/body.py` renders the body; nothing opens the PR yet. |
| Multi-language | libcst is Python-only. A second language means a second indexer behind the same `CallSite` interface. |

### Measured against Stripe

`providers/stripe.py` loads any two versions of `stripe/openapi` by git ref, so
these are reproducible:

```bash
upkeep detect -p stripe --from v2300 --to v2484   # 2026-05-27 -> 2026-08-26
upkeep detect -p stripe --from v2000 --to v2484   # 2025-08-27 -> 2026-08-26
```

Over three months (1,422 → 1,454 schemas): 6 breaking property removals, caught
6/6 with no false positives, against independently computed ground truth. Over a
year: 4 renames and 6 escalations — one rename still wrong, all of them
correctly held at Tier B.

Two coverage bugs came out of that exercise and are fixed: every one of Stripe's
587 shared operations declares its parameters under `requestBody`, which the
differ originally ignored entirely; and a bare property removal was being graded
`deprecation` when being unable to say what replaced it makes it worse, not
milder.

### Measured against Twilio

Twilio publishes one spec per product surface, so `--domain` selects one:

```bash
upkeep detect -p twilio --domain api_v2010 --from 2.0.0 --to 2.8.2
upkeep detect -p twilio --domain preview   --from 2.0.0 --to 2.8.2
```

Sweeping all 60 surfaces across that window — 2024-06-18 to 2026-09-09, just
over two years, of which 17 surfaces didn't exist at the start — found **122
breaking changes**, spot-checked exactly against independently computed ground
truth:

| | |
| --- | --- |
| `endpoint_removed` | 71 |
| `semantics_changed` | 51 |
| `field_renamed` | **0** |

Three more defects came out of it. Twilio declares a shared API-version header
as a `$ref`, so that parameter entry has no `name` of its own — indexing it
blind raised `KeyError` and took down the whole sweep. Parameters declared on a
path item, which apply to every operation beneath it, were not being read. And a
schema removed *outright* was invisible to a diff that only walks keys present
in both versions: Twilio dropped ten `usage_record_*_enum_category` enums in one
release and the differ graded that release `additive`.

### Shopify: no run, and that is the finding

Shopify was measured third and the sweep could not be run at all. There is no
public machine-readable versioned schema to diff:

- No OpenAPI document for the REST Admin API.
- The GraphQL Admin schema is only reachable by authenticated introspection
  against a real shop, so there is nothing to fetch by version.
- Their own release-notes page offers documentation links and no schema
  download.

Their breaking changes are real and field-level — "`discountedUnitPrice` on
`DraftOrderLineItem` ... deprecation", "Storefront MCP cart tools are being
deprecated in favour of UCP Cart MCP", "Script tags are deprecated and will
stop running on March 1, 2027" — but they live in prose on a changelog and in
`BREAKING_CHANGES_FOR_V*.md` files inside the SDK repos, complete with
before-and-after code samples.

**No `ShopifyProvider` was written.** An adapter that cannot load a spec is a
stub that makes the roster look better than it is.

### Feeding Shopify's guide to a model

Shopify's `BREAKING_CHANGES_FOR_V16.md` was run through Claude Opus 5 under the
prompt in `detect/from_guide.py`, which asks only for transcription: report what
the text says, and put anything that does not fit the vocabulary into the escape
hatch rather than dressing it up as patchable.

The extraction was clean. Three changes, correct severity, faithful notes, valid
against the same `MigrationSpec` a spec diff produces — so everything downstream
is unchanged. And because a guide is the provider stating the change in their own
words, renames from this path are `declared`, not inferred.

Then the result:

| | |
| --- | --- |
| `field_renamed` | 0 |
| `endpoint_removed` | 0 |
| `param_required_added` | 0 |
| escalations | 3 of 3 |

**Nothing was patchable, and the model was not the reason.** `Session#serialize`
and `Session.deserialize` are removed *SDK methods*, and the schema — designed
from OpenAPI diffs, where the nouns are fields, endpoints and parameters — had no
word for one. Both landed in `semantics_changed` next to a Ruby version bump,
which loses the only thing that makes them actionable: a name the indexer can
search for. So `symbol_removed` now exists, and those two are findable.

That gap is now closed too. `call_pattern_changed` carries the guide's own
before-and-after code verbatim, which is the thing no schema diff can express:
Shopify does not merely say `Session.deserialize` is gone, it shows the call it
replaces and the shape that replaces it. Three rules keep it honest:

- **It is an illustration, not a rule**, so it is Tier B and can never reach a
  deterministic codemod. One example cannot tell you how the change applies to a
  call site with different names, different surroundings, or arguments the sample
  never shows. Rewriting a call shape from an example is the one job here that
  genuinely needs a model.
- **`language` is required and enforced.** `serialize` is a common method name;
  applying Shopify's Ruby rewrite to a Python call site that merely shares it
  would be a corrupting patch generated from an unrelated document. The planner
  refuses to match across languages. Changes carrying no language describe the
  wire format and still apply everywhere.
- **An unusable example is rejected at the schema.** No `before` to match, no
  `after` to write, or the two identical — each would send a model off to rewrite
  real code on no information. A guide that shows only the old call is a
  `symbol_removed`, not a pattern change.

The report a reviewer receives now carries that example beside their own call
site, which is most of the value even before anything writes a patch.

### Three providers, three different shapes

| | Machine-readable versioned spec | Where the breakage actually lives |
| --- | --- | --- |
| Stripe | Yes | Spec, partially — renames hide in coexistence and the changelog |
| Twilio | Yes | Spec for endpoint removals; the rest in SDK major versions |
| Shopify | **No** | Prose changelog and SDK migration guides only |

The input upkeep was built on — a public, versioned, diffable spec — exists for
two of three, and under-reports for both of those. The input that exists for all
three, and is the most complete for each, is the prose migration guide. Shopify
even ships before-and-after code samples in the consumer's own language, which
is *better* material for writing a patch than any schema diff.

That argues for turning the pipeline around. `detect` was built spec-first, with
prose as a late addition; the evidence says the changelog should be the primary
input and the spec diff the corroboration. Nothing in the stages after `detect`
changes — `MigrationSpec` is already the interface, and `upkeep run --spec` will
consume a hand-authored or guide-derived one today.

### Ten real guides, three extractors — [`evals/`](evals/README.md)

That argument is now measured rather than asserted. Ten published migration
guides — four Shopify Ruby, four Shopify JS, two Twilio, across Ruby, TypeScript
and Python — are copied verbatim into `evals/corpus/` with provenance, and
`spec_from_guide` was run over all of them three times: once by hand and twice
against an API.

```bash
pip install -e '.[extract]'
python evals/score.py                    # no API key needed; reads the corpus
python evals/compare.py runs/google-pro runs/google-flash
python evals/serve.py --open             # watch a run land, guide by guide
```

| | hand | gemini-3.1-pro | gemini-3-flash |
| --- | --- | --- | --- |
| Changes | 88 | 91 | 93 |
| Grounded — every symbol and every quoted line present in the source | 88/88 | 91/91 | 93/93 |
| Fabrications | 0 | 0 | 0 |
| Tier A (auto-patchable) | **2** | **2** | **2** |

Two findings survive the scrutiny in [`evals/README.md`](evals/README.md), which
also records what does not:

- **Nothing was invented**, across 184 machine-extracted changes from ten real
  documents. `detect/grounding.py` drops anything it cannot find in the source,
  and `tests/test_grounding.py` fires four real failure shapes at the check so
  the 100% carries information.
- **Tier A is 2 in all three runs.** The ratio that argues for impact analysis
  over automatic patching did not move across three independent extractors.

And the limit, stated plainly there: grounding catches invention and is blind to
recall. Matching each run's records by the *claim* they make rather than the name
they happened to attach, any two of these runs still disagree about a fifth to
two-fifths of what a document says — and no automatic check in this repo can tell
a miss from an over-read. That is what the review tool and the next step below
are for.

Getting that figure right took two passes. Matched on bare identifiers the same
runs agree only 33% of the time, but nine of the first ten disagreements turned
out to be one change under two names. `evals/match.py` merges those, which took
the adjudication queue from 91 cards to 31 and left the first card as the one
genuine miss.

## How often do providers actually rename? Measured wrong, twice.

The first sweep reported **zero renames** across three months of Stripe and all
60 Twilio surfaces, and this README briefly concluded the deterministic codemod
tier had nothing to do. That conclusion was wrong, and the way it was wrong is
the most useful thing in this repo.

Stripe's own changelog for the window says, in their words and all marked
**Breaking**:

> "**Renames** the parameter for custom settlement timing on the Balance Settings API"
> "**Renames** Payment Intent field for linking tax calculation"
> "**Replaces** top-level price fields with improved price modeling on Invoice Items and Invoice Line Items"

Stripe renames constantly. The differ scored zero because it was measuring
something else. Renames reach consumers three ways, and a key-set diff sees only
the first:

| How a rename ships | Example | Visible to a key-set diff? |
| --- | --- | --- |
| Old field removed, new one added | `discount.coupon` → `source` | Yes |
| Both coexist, successor named in prose | `quantity` → `quantity_decimal` | **No** — nothing was removed |
| Both coexist, announced only in the changelog | `customer` → `customer_account`, 26 schemas | **No** — not in the spec at all |

The second is now handled: when a field's description names a successor that
exists beside it, that is the provider telling you outright, which beats any
inference from type signatures. It found both `quantity_decimal` pairs, and
grades them `deprecation` rather than `breaking`, because the old field still
works — the best possible moment to migrate, while it is still a no-op.

The third is not handled and may not be handleable from the spec. Stripe's
Accounts v2 migration added `customer_account` beside `customer` in 27 schemas,
and in **zero** of them does the old field's description mention the successor.
Only the changelog says it is a migration.

### Twilio's zero, re-checked

Twilio's sweep also returned zero renames, so it got the same scrutiny that
broke the Stripe result. It survived:

| Check | Result |
| --- | --- |
| Fields that became deprecated during the window | 0 (the 28 marked `DEPRECATED.` were already so at the start) |
| Coexisting near-name pairs, the Stripe shape | 0 |
| Deprecation prose naming a successor | 0 of 28 — Twilio writes a bare `DEPRECATED.` and points nowhere |

So the two zeros differ. Stripe's was an artifact hiding frequent renames.
Twilio's holds: across two years and 60 surfaces its field names really are
stable, and what breaks consumers is endpoints disappearing — 71 of the 122.

One caveat the specs cannot show. Twilio's own changelog puts breaking changes
in the *helper libraries*: "Java Helper Library 13.0.0 ... contains breaking
changes requiring migration." The API surface holds still while the SDK moves
underneath it, and a spec diff cannot see that at all.

So the honest recall on renames is somewhere near a fifth, and the honest
conclusion is narrower than either of the two this README has carried:

- **Deterministic codemods are not obsolete.** Real renames are frequent. The
  first measurement that said otherwise was an artifact.
- **A spec diff alone is not enough to find them.** Prose helps; the changelog
  is where the rest live; provider-declared Migration Specs are the only way to
  get them reliably. That is the argument for the provider side of this product,
  and it is now an argument from evidence.
- **Providers differ enough that one measurement generalises badly.** Stripe
  renames constantly and hides it in coexistence. Twilio barely renames and
  removes endpoints instead, then breaks people in its SDK releases. A third
  provider is worth measuring before trusting any pattern here.
- **Impact analysis stands on its own** regardless. Naming the call sites a
  release touches, and being explicit about what could not be worked out, is
  useful today and needs no write access to anyone's repository.

Anything measured here is reproducible from the commands above. The lesson
worth keeping is that a metric which returns a clean zero deserves more
suspicion than one that returns a mess.

## What three real providers say about the thesis

The design this repo started from assumed deterministic codemods would cover
most real API churn, which is why Tier A was built first.

Measured, that is half wrong — and the half that is wrong took two attempts to
see, which is the section above. Renames are **frequent**: Stripe ships them
constantly and says so in its own changelog. But they reach consumers mostly by
coexistence and prose, where a key-set diff cannot see them, so the codemod tier
starves for evidence it can act on rather than for work to do. What the diff
*does* catch reliably is endpoints disappearing — 71 of Twilio's 122 — and
fields vanishing with no stated successor. Both need judgment. Neither is a
mechanical rename.

Reading the providers' own guides sharpened it rather than reversing it. Across
ten real migration guides, 88 extracted changes route to 90 work items — **2
Tier A, 27 Tier B, 61 Tier C** — and Tier A stayed at 2 across all three
extractors. Guides are dense with things worth telling a developer and nearly
empty of things safe to fix for them.

So the near-term value is not the patch. It is the sentence *"this release
breaks these 14 call sites in your code, here they are, and here is what upkeep
could not work out"* — impact analysis nobody currently sells, which needs no
write access to anyone's repository. The automatic fix is the second act, and it
needs contract replay and provider-declared specs before it is worth trusting.
That reorders what to build next.

### Known limits of the v0 indexer

Bindings live in one flat module scope, with no shadowing analysis, and flow
does not cross function boundaries. Both widen the *unrooted* set — they fail
toward escalation, never toward a bad patch. That's the correct direction to be
wrong in.

---

## Next

Pointing it at Stripe was the first item here, and it has been done — three
months, a year, and 60 Twilio surfaces, all reproducible from the commands
above. What that bought was mostly a corrected question. The remaining four:

1. **An independent grader for the guide extractor.** Grounding proves nothing
   was invented and says nothing about what was missed, and three perfectly
   grounded runs still make the same claim only 60% of the time. Someone who has
   not seen the extractions has to write expectations per guide, so recall and
   semantic precision can be scored against something other than the extractor's
   own author. `evals/review.html` is the tool and its queue is down to 31 real
   disagreements; judging them is the work.
2. **Contract replay.** Record real request/response pairs, assert outcomes
   match across versions. Tier B model-assisted patching stays unbuilt until
   something can verify it, and that order is deliberate.
3. **Turn `detect` around.** Changelog as the primary input, spec diff as the
   corroboration. `MigrationSpec` is already the interface, so nothing after
   `detect` changes.
4. **Open real PRs on OSS repos that use the SDK**, and read what maintainers
   changed before merging. Each edit is a missing rule.

Merged PRs from strangers are the only validation that counts.
