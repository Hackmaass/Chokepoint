from chokepoint.faithful import check

RESULTS = [({"account": "010:AAAAAAAAA"}, {"max_movable_usd_cents": 3292362, "top": [{"from": "011:BBBBBBBBB", "usd_cents": 5030728}]})]


def test_exact_whole_dollar_and_rounded_amounts_pass():
    text = "Up to $32,923.62 moves; about $32,923 or $33K or $7 million, from 011:BBBBBBBBB to 010:AAAAAAAAA"
    assert check(text, RESULTS)["unmatched_amounts"] == ["$7 million"]
    ok = check("Up to $32,923.62, say $32,923, or $50.3K, via 011:BBBBBBBBB.", RESULTS)
    assert ok["faithful"], ok


def test_invented_numbers_and_accounts_are_flagged():
    bad = check("It could move $919,425.02 through 012:CCCCCCCCC.", RESULTS)
    assert not bad["faithful"]
    assert bad["unmatched_amounts"] == ["$919,425.02"] and bad["unmatched_accounts"] == ["012:CCCCCCCCC"]


def test_known_accounts_and_zero_are_allowed():
    assert check("After freezing, $0 could move from 013:DDDDDDDDD.", RESULTS, {"013:DDDDDDDDD"})["faithful"]
