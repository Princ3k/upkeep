"""upkeep command line.

Each stage is runnable on its own so you can inspect what it produced before
handing it to the next one; `upkeep run` chains all of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from upkeep.delivery import render_issue, render_pr_body
from upkeep.detect import diff_specs
from upkeep.indexer import index_repo
from upkeep.models import MigrationSpec, Tier
from upkeep.patch import apply_plan
from upkeep.providers import get_provider
from upkeep.providers.acme import AcmeProvider
from upkeep.router import plan as build_plan
from upkeep.verify import run_gate

app = typer.Typer(add_completion=False, help=__doc__)
console = Console()

TIER_STYLE = {"A": "green", "B": "yellow", "C": "red"}


def _resolve_provider(name: str, spec_dir: Path | None):
    if name == "acme" and spec_dir is not None:
        return AcmeProvider(spec_dir)
    return get_provider(name)


@app.command()
def detect(
    provider: str = typer.Option(..., "--provider", "-p"),
    from_version: str = typer.Option(..., "--from"),
    to_version: str = typer.Option(..., "--to"),
    spec_dir: Path = typer.Option(None, "--spec-dir", help="Load specs from disk."),
    vectors: list[str] = typer.Option([], "--vector", help="Test material paths."),
    out: Path = typer.Option(None, "--out", help="Write the MigrationSpec here."),
) -> None:
    """Diff two versions of a provider's surface into a MigrationSpec."""
    adapter = _resolve_provider(provider, spec_dir)
    spec = diff_specs(
        adapter.load_spec(from_version),
        adapter.load_spec(to_version),
        provider=provider,
        from_version=from_version,
        to_version=to_version,
        vectors=list(vectors),
    )

    table = Table("kind", "detail", title=f"{spec.id} · {spec.severity.value}")
    for change in spec.changes:
        detail = getattr(change, "path", None) or getattr(change, "op", "")
        extra = getattr(change, "to", None)
        table.add_row(change.kind, f"{detail}{f' → {extra}' if extra else ''}")
    console.print(table)

    payload = spec.model_dump(by_alias=True, mode="json")
    if out:
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[dim]wrote {out}[/dim]")


@app.command()
def index(
    repo: Path = typer.Argument(..., help="Consumer repository to scan."),
    provider: str = typer.Option(..., "--provider", "-p"),
) -> None:
    """List every provider-touching call site in a repository."""
    adapter = get_provider(provider)
    sites = index_repo(repo, adapter.sdk_roots)

    table = Table("file:line", "kind", "name", "rooted", title=f"{len(sites)} site(s)")
    for site in sites:
        table.add_row(
            f"{site.file}:{site.line}",
            site.kind.value,
            site.name,
            "[green]yes[/green]" if site.rooted else "[red]no[/red]",
        )
    console.print(table)


@app.command()
def run(
    repo: Path = typer.Argument(..., help="Consumer repository to migrate."),
    spec_path: Path = typer.Option(..., "--spec", help="A MigrationSpec JSON file."),
    write: bool = typer.Option(False, "--write", help="Apply patches to disk."),
    require_vectors: bool = typer.Option(True, "--require-vectors/--no-require-vectors"),
) -> None:
    """Index, plan, patch, and verify — the whole chain."""
    spec = MigrationSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    adapter = get_provider(spec.provider)

    sites = index_repo(repo, adapter.sdk_roots)
    items = build_plan(spec, sites, require_vectors=require_vectors)

    table = Table("file:line", "expression", "tier", "why")
    for item in items:
        table.add_row(
            f"{item.site.file}:{item.site.line}",
            item.site.expression,
            f"[{TIER_STYLE[item.tier.value]}]{item.tier.value}[/]",
            item.reason,
        )
    console.print(table)

    patches = apply_plan(repo, items, write=write)
    for patch in patches:
        console.print(f"[bold]{patch.file}[/bold]")
        if patch.diff:
            console.print(patch.diff, markup=False, highlight=False)
        else:
            console.print("[dim](no textual change)[/dim]")

    if not write:
        console.print(
            "[yellow]Dry run — pass --write to apply, then re-run to verify.[/yellow]"
        )
        raise typer.Exit(0)

    gate = run_gate(repo, items, [p.file for p in patches])
    for check in gate.checks:
        mark = "[green]pass[/green]" if check.passed else "[red]fail[/red]"
        console.print(f"  {mark}  {check.name}")

    console.print(f"\nconfidence: [bold]{gate.confidence.value}[/bold]")
    console.print(f"delivers as: [bold]{gate.delivery()}[/bold]\n")

    if gate.delivery() == "issue":
        console.print(render_issue(spec, items), markup=False, highlight=False)
    else:
        console.print(render_pr_body(spec, items, patches, gate), markup=False, highlight=False)

    if any(item.tier is Tier.C for item in items):
        console.print(
            "\n[dim]Tier C sites were left alone on purpose — see the body above.[/dim]"
        )


if __name__ == "__main__":
    app()
