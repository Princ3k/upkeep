"""Provider adapters.

A provider tells upkeep two things: which import roots identify its SDK in
consumer code, and where to get its spec for a given version. Everything else in
the pipeline is provider-agnostic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Provider(Protocol):
    name: str
    sdk_roots: tuple[str, ...]
    """Module names whose imports mark a file as provider-touching."""

    def load_spec(self, version: str) -> dict[str, Any]:
        """Return the OpenAPI document for `version`."""
        ...


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    _REGISTRY[provider.name] = provider
    return provider


def get_provider(name: str) -> Provider:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise KeyError(f"unknown provider {name!r} (known: {known})") from None


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
