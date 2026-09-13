"""Turn a plan into file patches."""

from __future__ import annotations

import difflib
from collections import defaultdict
from pathlib import Path

from upkeep.models import FieldRenamed, FilePatch, Tier, WorkItem
from upkeep.patch.codemods import rename_at_positions


def apply_plan(
    repo: Path | str, items: list[WorkItem], *, write: bool = False
) -> list[FilePatch]:
    """Generate patches for the Tier A items in `items`.

    Tier B is model-assisted and not implemented in v0 — see README. Tier C is
    analysis only and never produces a patch by definition.
    """
    repo = Path(repo)
    by_file: dict[str, dict[tuple[int, int], tuple[str, str]]] = defaultdict(dict)

    for item in items:
        if item.tier is not Tier.A or item.rule != "rename_field":
            continue
        if not isinstance(item.change, FieldRenamed):
            continue
        key = (item.site.line, item.site.column)
        by_file[item.site.file][key] = (item.change.old_name, item.change.new_name)

    patches: list[FilePatch] = []
    for relative_path, targets in sorted(by_file.items()):
        path = repo / relative_path
        original = path.read_text(encoding="utf-8")
        patched, applied = rename_at_positions(original, targets)
        if applied == 0 or patched == original:
            continue

        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                patched.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )
        if write:
            path.write_text(patched, encoding="utf-8")

        patches.append(
            FilePatch(
                file=relative_path,
                diff=diff,
                new_source=patched,
                sites_changed=applied,
            )
        )
    return patches
