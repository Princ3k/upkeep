"""The test that matters: a broken repo goes in, a verified patch comes out.

It reproduces the failure first (the consumer's suite must actually be red
against the provider's new surface), then applies the migration, then proves the
same suite goes green without any test file being touched.
"""

import shutil
from pathlib import Path

from upkeep.delivery import render_pr_body
from upkeep.detect import diff_specs
from upkeep.indexer import index_repo
from upkeep.models import Confidence, Tier
from upkeep.patch import apply_plan
from upkeep.providers.base import load_json
from upkeep.router import plan
from upkeep.verify import run_gate
from upkeep.verify.gate import check_tests

FIXTURES = Path(__file__).parent / "fixtures"


def build_spec():
    return diff_specs(
        load_json(FIXTURES / "specs" / "acme_v1.json"),
        load_json(FIXTURES / "specs" / "acme_v2.json"),
        provider="acme",
        from_version="v1",
        to_version="v2",
        vectors=["test_billing.py"],
        declared=True,  # this fixture diff was checked by hand
    )


def stage_consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer"
    shutil.copytree(FIXTURES / "consumer", repo)
    return repo


def test_consumer_suite_is_red_before_migrating(tmp_path):
    repo = stage_consumer(tmp_path)
    assert not check_tests(repo).passed


def test_full_pipeline_migrates_and_verifies(tmp_path):
    repo = stage_consumer(tmp_path)
    spec = build_spec()

    sites = index_repo(repo, ("acme",))
    items = plan(spec, sites)

    tiers = {i.tier for i in items}
    assert Tier.A in tiers, "the rooted `line.amount` should be patchable"
    assert Tier.C in tiers, "the unrooted `payload['amount']` should escalate"

    patches = apply_plan(repo, items, write=True)
    assert [p.file for p in patches] == ["billing.py"]
    assert patches[0].sites_changed == 1

    gate = run_gate(repo, items, [p.file for p in patches])
    assert gate.passed, gate.checks

    # The repo also has a Tier B site (a new required parameter with no codemod
    # yet), so this cannot ship as a confident PR even though every check
    # passed — an unhandled site left in the tree is a reason for a human to
    # look, not a reason to raise confidence.
    assert Tier.B in tiers
    assert gate.confidence is Confidence.medium
    assert gate.delivery() == "draft_pull_request"


def test_the_unrooted_site_is_left_alone(tmp_path):
    repo = stage_consumer(tmp_path)
    spec = build_spec()

    items = plan(spec, index_repo(repo, ("acme",)))
    apply_plan(repo, items, write=True)

    source = (repo / "billing.py").read_text()
    assert "line.unit_amount" in source
    assert "payload['amount']" in source, "a plain dict key must survive the migration"


def test_pr_body_names_what_it_could_not_prove(tmp_path):
    repo = stage_consumer(tmp_path)
    spec = build_spec()

    items = plan(spec, index_repo(repo, ("acme",)))
    patches = apply_plan(repo, items, write=True)
    gate = run_gate(repo, items, [p.file for p in patches])

    body = render_pr_body(spec, items, patches, gate)
    assert "billing.py" in body
    assert "Not patched — needs a human" in body
    assert "confidence" in body.lower()


def test_a_clean_tier_a_only_migration_ships_as_a_confident_pr(tmp_path):
    """Strip the Tier B change and the same repo earns a real pull request."""
    repo = stage_consumer(tmp_path)
    spec = build_spec()
    spec = spec.model_copy(
        update={"changes": [c for c in spec.changes if c.kind == "field_renamed"]}
    )

    items = plan(spec, index_repo(repo, ("acme",)))
    # The unrooted dict access still escalates — a Tier C item a human will look
    # at does not, by itself, hold back a PR for the work that was proven.
    assert {i.tier for i in items} == {Tier.A, Tier.C}

    patches = apply_plan(repo, items, write=True)
    gate = run_gate(repo, items, [p.file for p in patches])

    assert gate.confidence is Confidence.high
    assert gate.delivery() == "pull_request"
