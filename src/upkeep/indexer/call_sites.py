"""Find every place consumer code touches a provider's surface.

This is a SAST scanner wearing a different hat: parse, resolve the provider's
imports, follow symbol flow, and tag each expression with what it touches.

The critical output is not the list of sites — it is the `rooted` flag on each
one. A rooted site is an expression upkeep can *prove* descends from a provider
call, by following one of three bindings:

    invoice = acme.Invoice.retrieve(id)     # assignment from a provider call
    for line in invoice.lines:              # iteration over a rooted expression
    [x for x in invoice.lines]              # the same, in a comprehension

Anything else is unrooted. `payload["amount"]` in a file that imports the SDK
may well be the provider's object, but upkeep cannot demonstrate it, so it never
patches it — it escalates instead. Guessing here is how you break production.

Known v0 limitations, deliberately: bindings are tracked in one flat module
scope with no shadowing analysis, and flow does not cross function boundaries.
Both widen the unrooted set, which fails toward escalation rather than toward a
bad patch.
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from upkeep.models import CallSite, SiteKind

MAX_BINDING_PASSES = 5


def _dotted(node: cst.BaseExpression) -> str | None:
    """Render a Name/Attribute chain as `a.b.c`, or None if it isn't one."""
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else None
    return None


def _string_value(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.SimpleString):
        return node.raw_value
    return None


class _ImportCollector(cst.CSTVisitor):
    """Collect local names bound to the provider's SDK."""

    def __init__(self, sdk_roots: tuple[str, ...]) -> None:
        self.sdk_roots = sdk_roots
        self.aliases: set[str] = set()

    def _is_provider_module(self, dotted: str | None) -> bool:
        if not dotted:
            return False
        head = dotted.split(".", 1)[0]
        return head in self.sdk_roots

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            dotted = _dotted(alias.name)
            if not self._is_provider_module(dotted):
                continue
            if alias.asname and isinstance(alias.asname.name, cst.Name):
                self.aliases.add(alias.asname.name.value)
            elif dotted:
                self.aliases.add(dotted.split(".", 1)[0])

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if node.module is None or not self._is_provider_module(_dotted(node.module)):
            return
        if isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            if alias.asname and isinstance(alias.asname.name, cst.Name):
                self.aliases.add(alias.asname.name.value)
            elif isinstance(alias.name, cst.Name):
                self.aliases.add(alias.name.value)


class _BindingCollector(cst.CSTVisitor):
    """Grow the set of local names proven to hold provider-derived values.

    Run repeatedly until the set stops growing: libcst visits a comprehension's
    element before its `for` clause, so a single ordered pass would miss
    `line.amount` in `[line.amount for line in invoice.lines]`.
    """

    def __init__(self, rooted: set[str]) -> None:
        self.rooted = set(rooted)

    def is_rooted(self, node: cst.BaseExpression) -> bool:
        if isinstance(node, cst.Name):
            return node.value in self.rooted
        if isinstance(node, cst.Attribute):
            return self.is_rooted(node.value)
        if isinstance(node, cst.Subscript):
            return self.is_rooted(node.value)
        if isinstance(node, cst.Call):
            return self.is_rooted(node.func)
        return False

    def _bind(self, target: cst.BaseExpression) -> None:
        if isinstance(target, cst.Name):
            self.rooted.add(target.value)
        elif isinstance(target, (cst.Tuple, cst.List)):
            for element in target.elements:
                self._bind(element.value)

    def visit_Assign(self, node: cst.Assign) -> None:
        if self.is_rooted(node.value):
            for target in node.targets:
                self._bind(target.target)

    def visit_For(self, node: cst.For) -> None:
        if self.is_rooted(node.iter):
            self._bind(node.target)

    def visit_CompFor(self, node: cst.CompFor) -> None:
        if self.is_rooted(node.iter):
            self._bind(node.target)


class _SiteCollector(cst.CSTVisitor):
    """Record every provider-relevant expression, rooted or not."""

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, path: str, rooted: set[str], aliases: set[str]) -> None:
        self.path = path
        self.rooted = rooted
        self.aliases = aliases
        self.sites: list[CallSite] = []

    def is_rooted(self, node: cst.BaseExpression) -> bool:
        if isinstance(node, cst.Name):
            return node.value in self.rooted
        if isinstance(node, cst.Attribute):
            return self.is_rooted(node.value)
        if isinstance(node, cst.Subscript):
            return self.is_rooted(node.value)
        if isinstance(node, cst.Call):
            return self.is_rooted(node.func)
        return False

    def _record(
        self, node: cst.CSTNode, kind: SiteKind, name: str, expression: str, rooted: bool
    ) -> None:
        position = self.get_metadata(PositionProvider, node)
        self.sites.append(
            CallSite(
                file=self.path,
                line=position.start.line,
                column=position.start.column,
                kind=kind,
                name=name,
                expression=expression,
                rooted=rooted,
            )
        )

    def visit_Attribute(self, node: cst.Attribute) -> None:
        # `acme.Invoice` is the SDK itself, not a value read off a response.
        if _dotted(node.value) in self.aliases:
            return
        self._record(
            node.attr,
            SiteKind.attribute,
            node.attr.value,
            _dotted(node) or "<expr>",
            self.is_rooted(node.value),
        )

    def visit_Subscript(self, node: cst.Subscript) -> None:
        if len(node.slice) != 1:
            return
        index = node.slice[0].slice
        if not isinstance(index, cst.Index):
            return
        key = _string_value(index.value)
        if key is None:
            return
        base = _dotted(node.value) or "<expr>"
        self._record(
            index.value,
            SiteKind.subscript,
            key,
            f"{base}[{key!r}]",
            self.is_rooted(node.value),
        )

    def visit_Call(self, node: cst.Call) -> None:
        target = _dotted(node.func)
        head = target.split(".", 1)[0] if target else None
        is_provider_call = head in self.aliases or self.is_rooted(node.func)
        if not is_provider_call:
            return

        self._record(node, SiteKind.call, target or "<call>", target or "<call>", True)
        for arg in node.args:
            if arg.keyword is not None:
                self._record(
                    arg.keyword,
                    SiteKind.kwarg,
                    arg.keyword.value,
                    f"{target}({arg.keyword.value}=...)",
                    True,
                )


def touches_provider(source: str, sdk_roots: tuple[str, ...]) -> bool:
    """Cheap pre-filter: does this file import the provider at all?"""
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return False
    collector = _ImportCollector(sdk_roots)
    module.visit(collector)
    return bool(collector.aliases)


def index_file(path: Path | str, source: str, sdk_roots: tuple[str, ...]) -> list[CallSite]:
    """Index one file. Returns [] for files that never import the provider."""
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return []

    imports = _ImportCollector(sdk_roots)
    module.visit(imports)
    if not imports.aliases:
        return []

    rooted = set(imports.aliases)
    for _ in range(MAX_BINDING_PASSES):
        bindings = _BindingCollector(rooted)
        module.visit(bindings)
        if bindings.rooted == rooted:
            break
        rooted = bindings.rooted

    # The SDK's own names are entry points, not values read off a response.
    value_rooted = rooted - imports.aliases

    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    collector = _SiteCollector(str(path), value_rooted, imports.aliases)
    wrapper.visit(collector)
    return collector.sites


def index_repo(root: Path | str, sdk_roots: tuple[str, ...]) -> list[CallSite]:
    """Index every Python file under `root` that imports the provider."""
    root = Path(root)
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".upkeep-cache"}
    sites: list[CallSite] = []

    for path in sorted(root.rglob("*.py")):
        if skip & set(path.relative_to(root).parts):
            continue
        source = path.read_text(encoding="utf-8")
        sites.extend(
            index_file(path.relative_to(root), source, sdk_roots)
        )
    return sites
