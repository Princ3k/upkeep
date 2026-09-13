"""The pull request body is the product surface.

Whoever reviews this has thirty seconds and no context. Say what changed, what
was proven, what was *not* proven, and how to undo it. The escalation list at
the bottom matters as much as the diff: it is upkeep telling a human exactly
where it declined to guess.
"""

from __future__ import annotations

from upkeep.models import FilePatch, GateResult, MigrationSpec, Tier, WorkItem


def _tier_counts(items: list[WorkItem]) -> dict[str, int]:
    counts = {"A": 0, "B": 0, "C": 0}
    for item in items:
        counts[item.tier.value] += 1
    return counts


def render_pr_body(
    spec: MigrationSpec,
    items: list[WorkItem],
    patches: list[FilePatch],
    gate: GateResult,
) -> str:
    counts = _tier_counts(items)
    changed = sum(p.sites_changed for p in patches)

    lines = [
        f"## {spec.provider}: migrate `{spec.from_version}` → `{spec.to_version}`",
        "",
        f"**Severity:** {spec.severity.value}"
        + (f" · **Effective:** {spec.effective}" if spec.effective else ""),
        "",
        f"Updated **{changed}** call site(s) across **{len(patches)}** file(s).",
        "",
        "### What changed",
    ]

    for patch in patches:
        lines.append(f"- `{patch.file}` — {patch.sites_changed} site(s)")

    lines += ["", "### What was verified"]
    for check in gate.checks:
        mark = "x" if check.passed else " "
        detail = f" — {check.detail.splitlines()[0]}" if check.detail else ""
        lines.append(f"- [{mark}] `{check.name}`{detail}")

    lines += [
        "",
        f"Confidence: **{gate.confidence.value}** → ships as `{gate.delivery()}`.",
    ]

    escalations = [i for i in items if i.tier is Tier.C]
    if escalations:
        lines += [
            "",
            "### Not patched — needs a human",
            "",
            "upkeep found these but could not prove a safe transformation:",
            "",
        ]
        for item in escalations:
            site = item.site
            lines.append(
                f"- `{site.file}:{site.line}` — `{site.expression}` — {item.reason}"
            )

    if counts["B"]:
        lines += [
            "",
            f"_{counts['B']} site(s) routed to Tier B (model-assisted), "
            "which is not enabled in this version._",
        ]

    lines += [
        "",
        "---",
        "",
        "To undo: revert this commit. No dependency versions were changed and no "
        "test files were modified.",
    ]
    return "\n".join(lines)


def render_issue(spec: MigrationSpec, items: list[WorkItem]) -> str:
    escalations = [i for i in items if i.tier is Tier.C]
    lines = [
        f"## {spec.provider} `{spec.from_version}` → `{spec.to_version}`: "
        f"{len(escalations)} site(s) need review",
        "",
        "upkeep did not open a pull request for these. Each one is a place where "
        "an automatic patch could not be proven correct.",
        "",
    ]
    for item in escalations:
        site = item.site
        lines += [
            f"### `{site.file}:{site.line}`",
            "",
            f"```python\n{site.expression}\n```",
            "",
            f"- **Change:** `{item.change.kind}`",
            f"- **Why not patched:** {item.reason}",
            "",
        ]
    return "\n".join(lines)
