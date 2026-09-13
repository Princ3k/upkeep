"""Twilio.

Two things make Twilio a useful second target, and both exercise parts of the
pipeline Stripe never touched.

First, there is no single spec: Twilio publishes one document per product domain
(`twilio_api_v2010`, `twilio_messaging_v1`, ...), so a provider is a *set* of
surfaces and a migration may span several. Second, its operations are
form-encoded, so call parameters live in `requestBody` rather than `parameters`.

Versions are release tags on twilio/twilio-oai, e.g. `2.8.2`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from upkeep.providers.base import load_json, register

SPEC_URL = (
    "https://raw.githubusercontent.com/twilio/twilio-oai/{ref}/spec/json/twilio_{domain}.json"
)

DEFAULT_DOMAIN = "api_v2010"


class TwilioProvider:
    name = "twilio"
    sdk_roots = ("twilio",)

    def __init__(
        self, domain: str = DEFAULT_DOMAIN, cache_dir: Path | str = ".upkeep-cache"
    ) -> None:
        self.domain = domain
        self.cache_dir = Path(cache_dir)

    def load_spec(self, version: str) -> dict[str, Any]:
        cached = self.cache_dir / f"twilio_{self.domain}_{version}.json"
        if cached.exists():
            return load_json(cached)

        response = httpx.get(
            SPEC_URL.format(ref=version, domain=self.domain),
            timeout=60.0,
            follow_redirects=True,
        )
        response.raise_for_status()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return load_json(cached)


register(TwilioProvider())
