"""Hand-editing a headline number turns the claims check red."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_claims  # noqa: E402


def test_every_marked_number_matches_claims_json():
    assert check_claims.problems() == []


def test_changing_a_claim_is_caught(monkeypatch):
    claims = check_claims.load()
    claims["precision_gain"] = {**claims["precision_gain"], "value": "+0.25"}
    monkeypatch.setattr(check_claims, "load", lambda: claims)
    bad = check_claims.problems()
    assert any("precision_gain" in b for b in bad)
