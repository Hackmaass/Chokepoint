"""Check that the model's narrative only states dollar amounts and account ids that a tool returned.

Used by the agent (to warn the user in the interface) and by the evaluation. Amounts match a tool value exactly to the
cent, to the whole dollar (the model may drop the cents), or, when written with a suffix such as "$18M" or
"$33.2 million", within 5% (rounding).
"""

from __future__ import annotations

import re

AMOUNT = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|thousand|bn|m|k)?\b", re.I)
SCALE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "k": 1e3}
ACCOUNT = re.compile(r"\b[0-9]{2,7}:[0-9A-F]{9}\b")


def _numbers(obj, out: set[float]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "usd_cents" in k and isinstance(v, (int, float)):
                out.add(round(v / 100, 2))
            _numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, out)


def _accounts(obj, out: set[str]) -> None:
    if isinstance(obj, str):
        out.update(ACCOUNT.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _accounts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _accounts(v, out)


def check(text: str, tool_results: list[tuple[dict, dict]], known_accounts: set[str] | None = None) -> dict:
    """``tool_results`` is a list of (arguments, result). Returns the amounts and accounts in ``text`` that no tool returned."""
    amounts: set[float] = set()
    accounts: set[str] = set(known_accounts or ())
    for args, result in tool_results:
        _numbers(result, amounts)
        _accounts(result, accounts)
        _accounts(args, accounts)
    bad_amounts = []
    for mantissa, suffix in AMOUNT.findall(text or ""):
        value = float(mantissa.replace(",", "")) * SCALE.get(suffix.lower(), 1)
        if value == 0:
            continue
        if suffix:
            ok = any(abs(value - a) <= 0.05 * a for a in amounts)
        else:
            ok = any(abs(value - a) <= 0.011 or ("." not in mantissa and abs(value - a) < 1.0) for a in amounts)
        if not ok:
            bad_amounts.append(f"${mantissa}{' ' + suffix if suffix else ''}")
    bad_accounts = sorted(a for a in set(ACCOUNT.findall(text or "")) if a not in accounts)
    return {"unmatched_amounts": bad_amounts, "unmatched_accounts": bad_accounts,
            "faithful": not bad_amounts and not bad_accounts}
