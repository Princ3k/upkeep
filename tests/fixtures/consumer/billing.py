import acme


def invoice_total(invoice_id: str) -> int:
    invoice = acme.Invoice.retrieve(invoice_id)
    return sum(line.amount for line in invoice.lines)


def line_currencies(invoice_id: str) -> list[str]:
    invoice = acme.Invoice.retrieve(invoice_id)
    return [line.currency for line in invoice.lines]


def describe(payload: dict) -> str:
    # Spelled the same as the renamed field, but this is a plain dict the caller
    # passed in — not the provider's object. upkeep must leave it alone.
    return f"{payload['amount']} {payload['currency']}"
