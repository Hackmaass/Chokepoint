from chokepoint.casegraph import time_ordered_pairs


def test_a_link_that_only_works_out_of_order_is_not_a_route():
    # o->a at 10, a->x at 5 (before the money arrives), o->b at 3, b->x at 8
    transfers = sorted([("o", "a", 10), ("a", "x", 5), ("o", "b", 3), ("b", "x", 8)], key=lambda t: t[2])
    assert time_ordered_pairs(transfers, "o", 0) == {("o", "b"), ("b", "x"), ("o", "a")}


def test_same_minute_chains_count_and_removed_accounts_block():
    transfers = [("o", "a", 4), ("a", "x", 4)]
    assert ("a", "x") in time_ordered_pairs(transfers, "o", 0)
    assert time_ordered_pairs(transfers, "o", 0, frozenset({"a"})) == set()
