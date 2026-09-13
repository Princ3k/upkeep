"""A local, file-backed provider used by the test fixtures.

Real providers fetch specs over the network; this one reads them off disk so the
whole pipeline is testable without egress.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from upkeep.providers.base import load_json, register


class AcmeProvider:
    name = "acme"
    sdk_roots = ("acme",)

    def __init__(self, spec_dir: Path | str | None = None) -> None:
        self.spec_dir = Path(spec_dir) if spec_dir else None

    def load_spec(self, version: str) -> dict[str, Any]:
        if self.spec_dir is None:
            raise RuntimeError("AcmeProvider needs a spec_dir to load specs from")
        return load_json(self.spec_dir / f"acme_{version}.json")


register(AcmeProvider())
