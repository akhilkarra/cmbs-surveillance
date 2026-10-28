from cmbs.testing.bdd import changed_tables


def test_no_change_means_no_tables():
    assert changed_tables({"silver.deal": "a"}, {"silver.deal": "a"}) == []


def test_changed_added_and_dropped_tables_are_all_reported():
    before = {"silver.deal": "a", "silver.check_flag": "b", "raw.filing": "c"}
    after = {"silver.deal": "a", "silver.check_flag": "changed", "silver.new_table": "d"}
    assert changed_tables(before, after) == ["raw.filing", "silver.check_flag", "silver.new_table"]
