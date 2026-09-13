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

**2. Never guess a rename.**

A property vanished and another appeared. That *might* be a rename. upkeep only
says so when exactly one removed and one added property share a type signature —
otherwise it emits a `semantics_changed` and asks a human. See
`test_ambiguous_removal_is_never_guessed`.

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
