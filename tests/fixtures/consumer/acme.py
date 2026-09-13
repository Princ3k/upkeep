"""Stand-in for the Acme SDK, pinned to the 2026-09-01 surface.

Real runs install the provider's actual package. This exists so the whole
pipeline — including the gate, which really does execute the consumer's tests —
can run with no network and no third-party install.
"""


class InvoiceLine:
    def __init__(self, unit_amount: int, currency: str) -> None:
        self.unit_amount = unit_amount
        self.currency = currency


class Invoice:
    def __init__(self, id: str, lines: list[InvoiceLine]) -> None:
        self.id = id
        self.lines = lines

    @classmethod
    def retrieve(cls, invoice_id: str) -> "Invoice":
        return cls(invoice_id, [InvoiceLine(1200, "cad"), InvoiceLine(350, "cad")])
