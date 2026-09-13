import billing


def test_invoice_total():
    assert billing.invoice_total("in_123") == 1550


def test_line_currencies():
    assert billing.line_currencies("in_123") == ["cad", "cad"]


def test_describe_uses_the_callers_own_dict():
    assert billing.describe({"amount": 999, "currency": "cad"}) == "999 cad"
