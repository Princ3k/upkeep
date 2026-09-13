"""Read a provider's migration guide into a MigrationSpec.

Three providers in, the evidence says the prose migration guide is the better
input. Stripe's spec hides renames in coexistence; Twilio's is silent about the
SDK releases where it actually breaks people; Shopify publishes no spec at all.
All three publish a guide, and Shopify's carries before-and-after code in the
consumer's own language — better material for writing a patch than any schema
diff.

A guide is also the provider stating the change in their own words, which is the
definition of `declared`: renames from here are not upkeep guessing at type
signatures, so they can reach Tier A where an inferred one cannot.

Backends are pluggable and all get the identical flat schema from `flat.py`, so
a comparison between them measures extraction rather than whose structured-output
implementation handles discriminated unions better.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from upkeep.detect.flat import FlatSpec, rebuild, severity_of
from upkeep.detect.grounding import GroundingReport, check_grounding
from upkeep.models import MigrationSpec

SYSTEM = """\
You convert API migration guides into a structured list of changes.

Rules, in order of importance:

1. Report only changes the document actually states. Never infer a change from
   your own knowledge of the provider, and never complete a partial statement.
   Every symbol you name and every line of code you quote must appear in the
   document; anything else is discarded before it is used, and worse, could have
   something downstream rewrite real source from a claim the document never made.

2. Use `semantics_changed` for anything that does not fit the other kinds
   exactly, including changes to runtime or language versions, build output, and
   behaviour with no mechanical equivalent. Forcing such a change into
   `field_renamed` or `call_pattern_changed` tells downstream tooling a
   mechanical fix exists when it does not, which is worse than saying nothing.

3. `call_pattern_changed` is for when the document shows the old call AND the
   shape that replaces it. Copy both verbatim — do not paraphrase, tidy,
   reformat, or complete them. If the document shows only the old call, that is
   `symbol_removed`. Set `language` to the SDK's language, not the provider's
   API. A pattern may come from a reference or appendix section as long as the
   document shows it; say so in `note`.

4. `symbol_removed` is for a class, method, constant or field the SDK no longer
   exposes. Set `replacement` only if the document names one.

5. `endpoint_removed` is only for an HTTP endpoint. A removed SDK method is not
   an endpoint.

6. `field_renamed` needs the document to name both the old and the new field.

7. `param_required_added` needs the parameter named; set `safe_default` only if
   the document states a default.

8. Set `severity` from the document's own framing. If it calls the release
   breaking, it is breaking.

Read the whole document before answering. Guides bury changes in tables,
appendices and reference sections, not only under headings.\
"""


@dataclass
class Extraction:
    spec: MigrationSpec
    grounding: GroundingReport
    malformed: int
    """Records the backend returned that could not be rebuilt into a change."""
    dropped: int
    """Grounded-check failures removed before returning."""


class Backend(Protocol):
    name: str
    model: str

    def run(self, system: str, prompt: str) -> FlatSpec: ...


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5", client=None) -> None:
        self.model = model
        self._client = client

    def run(self, system: str, prompt: str) -> FlatSpec:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "The anthropic backend needs:  pip install 'upkeep[anthropic]'"
            ) from exc
        client = self._client or anthropic.Anthropic()
        response = client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=system,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
            output_format=FlatSpec,
        )
        return response.parsed_output


class GoogleBackend:
    name = "google"

    def __init__(self, model: str = "gemini-3.1-pro-preview", client=None) -> None:
        self.model = model
        self._client = client

    def run(self, system: str, prompt: str) -> FlatSpec:
        try:
            from google import genai
            from google.genai import types
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "The google backend needs:  pip install 'upkeep[google]'"
            ) from exc
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        client = self._client or genai.Client(api_key=key)
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=FlatSpec,
            ),
        )
        return response.parsed


BACKENDS = {"anthropic": AnthropicBackend, "google": GoogleBackend}


def spec_from_guide(
    guide_text: str,
    *,
    provider: str,
    from_version: str,
    to_version: str,
    backend: Backend | None = None,
    vectors: list[str] | None = None,
) -> Extraction:
    """Extract a MigrationSpec from a written migration guide.

    Every change is checked back against the guide text, and anything whose
    symbol or code cannot be found there is dropped before returning. An invented
    `after` is the one failure that would have something downstream rewrite real
    source from a document that never said it.
    """
    backend = backend or AnthropicBackend()
    prompt = (
        f"Provider: {provider}\n"
        f"Migrating from {from_version} to {to_version}.\n\n"
        f"Migration guide:\n\n{guide_text}"
    )

    flat = backend.run(SYSTEM, prompt)
    changes, malformed = rebuild(flat)

    spec = MigrationSpec(
        id=f"{provider}-{from_version}-{to_version}",
        provider=provider,
        **{"from": from_version, "to": to_version},
        severity=severity_of(flat),
        changes=changes,
        vectors=list(vectors or []),
    )

    report = check_grounding(spec, guide_text)
    kept = [r.change for r in report.results if r.grounded]
    return Extraction(
        spec=spec.model_copy(update={"changes": kept}),
        grounding=report,
        malformed=malformed,
        dropped=len(report.fabricated),
    )
