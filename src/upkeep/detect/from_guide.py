"""Read a provider's migration guide into a MigrationSpec.

Three providers in, the evidence says the prose migration guide is the better
input. Stripe's spec hides renames in coexistence; Twilio's is silent about the
SDK releases where it actually breaks people; Shopify publishes no spec at all.
All three publish a guide, and Shopify's carries before-and-after code in the
consumer's own language — better material for writing a patch than any schema
diff.

A guide is also the provider stating the change in their own words, which is
the definition of `declared`: renames from here are not upkeep guessing at type
signatures, so `declared=True` is the honest default for this path and lets
them reach Tier A.

The model's one job is transcription, not judgement. It may only report what the
text says, and anything that does not fit the vocabulary becomes a
`semantics_changed` escalation rather than being forced into a shape that would
license a patch.
"""

from __future__ import annotations

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - exercised by the message below
    anthropic = None

from upkeep.detect.grounding import GroundingReport, drop_ungrounded
from upkeep.models import MigrationSpec

MODEL = "claude-opus-5"

SYSTEM = """\
You convert API migration guides into a structured MigrationSpec.

Rules, in order of importance:

1. Report only changes the document actually states. Never infer a change from
   your own knowledge of the provider, and never complete a partial statement.
   A guide that is vague about a change is a guide that produces a vague record.

2. Use `semantics_changed` for anything that does not fit the other kinds
   exactly. This includes changes to SDK classes, methods, constructors, or call
   patterns, and changes to the runtime or language version. Forcing such a
   change into `field_renamed` or `endpoint_removed` would tell downstream
   tooling a mechanical fix exists when it does not, which is worse than saying
   nothing. Put the affected symbol in `op` and the document's own explanation
   in `note`.

3. `field_renamed` is only for a field, property, or parameter that the document
   says was renamed or replaced, where the document names both the old and the
   new one.

4. `endpoint_removed` is only for an HTTP endpoint. A removed SDK method is not
   an endpoint — use `symbol_removed` for that.

5. When the guide shows the old call and the shape that replaces it, use
   `call_pattern_changed` and copy both verbatim. Do not paraphrase code, do not
   tidy it, and do not write an `after` the document does not contain: an
   invented example is worse than no example, because something downstream will
   rewrite real code from it. If the guide shows only the old call, that is a
   `symbol_removed`, not a pattern change. Set `language` to the SDK's language,
   never the provider's API. A pattern may be drawn from a reference or appendix
   section as long as the document actually shows it; say so in `note`.

6. `param_required_added` needs the document to name the parameter. Set
   `safe_default` only if the document states a default; otherwise leave it null.

7. Set `severity` from the document's own framing. If it calls the release
   breaking, it is breaking.

Quote nothing you cannot point at in the text.\
"""


def spec_from_guide(
    guide_text: str,
    *,
    provider: str,
    from_version: str,
    to_version: str,
    vectors: list[str] | None = None,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
) -> tuple[MigrationSpec, GroundingReport]:
    """Extract a MigrationSpec from a written migration guide.

    The result is validated against the same schema a spec diff produces, so
    everything downstream — index, plan, patch, verify — is unchanged.

    Every change is then checked back against the guide text, and anything whose
    symbol or code cannot be found there is dropped before returning. An invented
    `after` is the one failure that would have something downstream rewrite real
    source from a document that never said it. The report comes back alongside
    the spec so a caller can see what was discarded.
    """
    if anthropic is None:
        raise ModuleNotFoundError(
            "Reading migration guides needs the Anthropic SDK. "
            "Install it with:  pip install 'upkeep[extract]'"
        )
    client = client or anthropic.Anthropic()

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Provider: {provider}\n"
                    f"Migrating from {from_version} to {to_version}.\n\n"
                    f"Migration guide:\n\n{guide_text}"
                ),
            }
        ],
        output_format=MigrationSpec,
    )

    spec = response.parsed_output
    # The caller owns identity and provenance; the model only reads the prose.
    spec = spec.model_copy(
        update={
            "id": f"{provider}-{from_version}-{to_version}",
            "provider": provider,
            "from_version": from_version,
            "to_version": to_version,
            "vectors": list(vectors or []),
            "changes": [
                c.model_copy(update={"inferred": False})
                if hasattr(c, "inferred")
                else c
                for c in spec.changes
            ],
        }
    )
    return drop_ungrounded(spec, guide_text)
