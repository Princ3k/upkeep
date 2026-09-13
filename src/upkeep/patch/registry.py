"""Which codemods actually exist.

The router decides a tier; the runner writes the patch. If those two disagree —
the router promising a deterministic fix for a rule nobody implemented — the
work item silently vanishes: no patch, no escalation, no trace in the PR body.
That is the worst possible failure mode, because it looks like success.

So the router consults this set and refuses to promise Tier A for a rule that
cannot be executed, demoting it instead. Adding a codemod means adding its name
here, and the mismatch stops being expressible.
"""

from __future__ import annotations

IMPLEMENTED_RULES: frozenset[str] = frozenset({"rename_field"})

KNOWN_RULES: frozenset[str] = frozenset({"rename_field", "add_required_param"})
