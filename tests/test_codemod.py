from upkeep.patch.codemods import rename_at_positions


def test_renames_only_the_recorded_position():
    source = (
        "import acme\n"
        "invoice = acme.Invoice.retrieve('in_1')\n"
        "a = invoice.amount\n"
        "b = payload.amount\n"
    )
    # Only line 3's `amount` was proven; line 4's was not.
    new_source, applied = rename_at_positions(source, {(3, 12): ("amount", "unit_amount")})

    assert applied == 1
    assert "invoice.unit_amount" in new_source
    assert "payload.amount" in new_source


def test_renames_dict_keys_and_preserves_quote_style():
    source = 'x = obj["amount"]\ny = obj[\'amount\']\n'
    new_source, applied = rename_at_positions(
        source,
        {(1, 8): ("amount", "unit_amount"), (2, 8): ("amount", "unit_amount")},
    )
    assert applied == 2
    assert '"unit_amount"' in new_source
    assert "'unit_amount'" in new_source


def test_a_position_holding_a_different_identifier_is_refused():
    source = "a = invoice.currency\n"
    new_source, applied = rename_at_positions(source, {(1, 12): ("amount", "unit_amount")})
    assert applied == 0
    assert new_source == source


def test_no_targets_is_a_no_op():
    source = "a = 1\n"
    assert rename_at_positions(source, {}) == (source, 0)


def test_formatting_elsewhere_is_untouched():
    source = "import acme\n\n\n# a comment\nx   =    invoice.amount  # trailing\n"
    column = source.splitlines()[4].index("amount")
    new_source, _ = rename_at_positions(source, {(5, column): ("amount", "unit_amount")})
    assert new_source == (
        "import acme\n\n\n# a comment\nx   =    invoice.unit_amount  # trailing\n"
    )
