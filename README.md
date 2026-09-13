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

Sweeping all 60 surfaces across that window (17 didn't exist at 2.0.0) found
**122 breaking changes**, spot-checked exactly against independently computed
ground truth:

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
Accounts v2 migration added `customer_account` beside `customer` in 26 schemas
with no marker on the old field. Only the changelog says it is a migration.

So the honest recall on renames is somewhere near a fifth, and the honest
conclusion is narrower than either of the two this README has carried:

- **Deterministic codemods are not obsolete.** Real renames are frequent. The
  first measurement that said otherwise was an artifact.
- **A spec diff alone is not enough to find them.** Prose helps; the changelog
  is where the rest live; provider-declared Migration Specs are the only way to
  get them reliably. That is the argument for the provider side of this product,
  and it is now an argument from evidence.
- **Impact analysis stands on its own** regardless. Naming the call sites a
  release touches, and being explicit about what could not be worked out, is
  useful today and needs no write access to anyone's repository.

Anything measured here is reproducible from the commands above. The lesson
worth keeping is that a metric which returns a clean zero deserves more
suspicion than one that returns a mess.

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

Sweeping all 60 surfaces across that window (17 didn't exist at 2.0.0) found
**122 breaking changes**, spot-checked exactly against independently computed
ground truth:

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

## What two real providers say about the thesis

The design this repo started from assumed deterministic codemods would cover
most real API churn, which is why Tier A was built first.

Measured, that is wrong. Across a year of Stripe and 60 Twilio surfaces, the
`rename_field` codemod — the only Tier A rule — had **essentially nothing to
do**: zero renames in three months of Stripe, zero across all of Twilio, and of
the five Stripe eventually produced, two were wrong. What actually breaks
consumers is endpoints disappearing and fields vanishing with no stated
successor. Both need judgment. Neither is a mechanical rename.

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

1. Point it at **Stripe**. `providers/stripe.py` loads any two versions of
   `stripe/openapi` by git ref, so historical diffs are replayable — check
   upkeep's output against what Stripe's own migration guides said to do. That's
   free evaluation data.
2. Open real PRs on ten OSS repos that use the SDK.
3. Count what merges, and read what maintainers changed before merging. Each
   edit is a missing deterministic rule.

Merged PRs from strangers are the only validation that counts.
