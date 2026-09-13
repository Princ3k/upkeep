from upkeep.indexer import index_file, touches_provider
from upkeep.models import SiteKind

ROOTS = ("acme",)


def sites_for(source: str):
    return index_file("sample.py", source, ROOTS)


def find(sites, name, kind):
    return [s for s in sites if s.name == name and s.kind is kind]


def test_file_without_the_sdk_is_skipped():
    assert sites_for("import json\nx = {'amount': 1}\n") == []
    assert not touches_provider("import json\n", ROOTS)


def test_assignment_from_a_provider_call_roots_the_value():
    sites = sites_for(
        "import acme\n"
        "invoice = acme.Invoice.retrieve('in_1')\n"
        "total = invoice.amount\n"
    )
    (site,) = find(sites, "amount", SiteKind.attribute)
    assert site.rooted


def test_comprehension_target_inherits_rooting():
    """libcst visits the element before the `for` clause; the binding pass must
    still reach a fixed point or this site comes back unrooted."""
    sites = sites_for(
        "import acme\n"
        "invoice = acme.Invoice.retrieve('in_1')\n"
        "total = sum(line.amount for line in invoice.lines)\n"
    )
    (site,) = find(sites, "amount", SiteKind.attribute)
    assert site.rooted


def test_for_loop_target_inherits_rooting():
    sites = sites_for(
        "import acme\n"
        "invoice = acme.Invoice.retrieve('in_1')\n"
        "for line in invoice.lines:\n"
        "    print(line.amount)\n"
    )
    (site,) = find(sites, "amount", SiteKind.attribute)
    assert site.rooted


def test_unrelated_dict_access_is_not_rooted():
    sites = sites_for("import acme\ndef f(payload):\n    return payload['amount']\n")
    (site,) = find(sites, "amount", SiteKind.subscript)
    assert not site.rooted


def test_import_alias_is_followed():
    sites = sites_for(
        "import acme as billing_sdk\n"
        "invoice = billing_sdk.Invoice.retrieve('in_1')\n"
        "total = invoice.amount\n"
    )
    (site,) = find(sites, "amount", SiteKind.attribute)
    assert site.rooted


def test_from_import_is_followed():
    sites = sites_for(
        "from acme import Invoice\n"
        "invoice = Invoice.retrieve('in_1')\n"
        "total = invoice.amount\n"
    )
    (site,) = find(sites, "amount", SiteKind.attribute)
    assert site.rooted


def test_keyword_arguments_are_recorded():
    sites = sites_for("import acme\nacme.Invoice.create(amount=100)\n")
    assert find(sites, "amount", SiteKind.kwarg)


def test_the_sdk_namespace_itself_is_not_a_site():
    sites = sites_for("import acme\ninvoice = acme.Invoice.retrieve('in_1')\n")
    assert not find(sites, "Invoice", SiteKind.attribute)


def test_syntax_errors_do_not_crash_the_indexer():
    assert sites_for("import acme\ndef broken(:\n") == []
