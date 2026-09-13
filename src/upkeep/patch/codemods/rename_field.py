"""Rename an identifier — but only at positions the indexer proved.

A blanket find-and-replace of `amount` across a codebase is exactly the kind of
patch that passes review and breaks production. This transform is keyed on
(line, column) pairs carried over from the index, so an occurrence the indexer
could not root is left untouched even when it is spelled identically.
"""

from __future__ import annotations

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

Position = tuple[int, int]


class _RenameAtPositions(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, targets: dict[Position, tuple[str, str]]) -> None:
        self.targets = targets
        self.applied = 0

    def _lookup(self, node: cst.CSTNode, current: str) -> str | None:
        position = self.get_metadata(PositionProvider, node)
        entry = self.targets.get((position.start.line, position.start.column))
        if entry is None:
            return None
        old, new = entry
        # Belt and braces: the position must also still hold the old identifier.
        return new if old == current else None

    def leave_Name(
        self, original_node: cst.Name, updated_node: cst.Name
    ) -> cst.BaseExpression:
        new = self._lookup(original_node, original_node.value)
        if new is None:
            return updated_node
        self.applied += 1
        return updated_node.with_changes(value=new)

    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.BaseExpression:
        new = self._lookup(original_node, original_node.raw_value)
        if new is None:
            return updated_node
        self.applied += 1
        quote = original_node.quote
        return updated_node.with_changes(value=f"{original_node.prefix}{quote}{new}{quote}")


def rename_at_positions(
    source: str, targets: dict[Position, tuple[str, str]]
) -> tuple[str, int]:
    """Return (new_source, sites_changed)."""
    if not targets:
        return source, 0

    module = cst.parse_module(source)
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    transformer = _RenameAtPositions(targets)
    new_module = wrapper.visit(transformer)
    return new_module.code, transformer.applied
