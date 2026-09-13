"""Stripe — the first real target.

Stripe publishes its OpenAPI document in a public git repo with one file per
dated API version, which means historical diffs are replayable: you can point
upkeep at any two past versions and check its output against what Stripe's own
migration guide said to do. That is the cheapest possible source of evaluation
data, and it is why Stripe is the right provider to start with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from upkeep.providers.base import load_json, register

SPEC_URL = "https://raw.githubusercontent.com/stripe/openapi/{ref}/openapi/spec3.json"


class StripeProvider:
    name = "stripe"
    sdk_roots = ("stripe",)

    def __init__(self, cache_dir: Path | str = ".upkeep-cache") -> None:
        self.cache_dir = Path(cache_dir)

    def load_spec(self, version: str) -> dict[str, Any]:
        """Load a spec by git ref (a tag, branch, or commit in stripe/openapi)."""
        cached = self.cache_dir / f"stripe_{version}.json"
        if cached.exists():
            return load_json(cached)

        url = SPEC_URL.format(ref=version)
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return load_json(cached)


register(StripeProvider())
