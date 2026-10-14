"""USD value of one unit of each currency in the IBM data, derived from the dataset's own cross-currency
transfers (median implied rate; see scripts/derive_fx.py). The data uses fixed rates per currency, so the
spread is negligible except for Bitcoin (observed range 10,000 to 15,000 USD; the median is used)."""

from __future__ import annotations

USD_PER_UNIT: dict[str, float] = {
    'Australian Dollar': 0.7078142694295919,
    'Bitcoin': 11874.999999999998,
    'Brazil Real': 0.17710085849756796,
    'Canadian Dollar': 0.757977714866847,
    'Euro': 1.1717834539993446,
    'Mexican Peso': 0.04729675385557055,
    'Ruble': 0.01285280962330482,
    'Rupee': 0.013615816131492639,
    'Saudi Riyal': 0.26658846778824696,
    'Shekel': 0.2961208153743032,
    'Swiss Franc': 1.092896174427715,
    'UK Pound': 1.2916558984057387,
    'US Dollar': 1.0,
    'Yen': 0.009487666033186501,
    'Yuan': 0.14930721351280496,
}


def to_usd_cents(amount_cents: int, currency: str) -> int:
    """Convert integer cents of ``currency`` to integer USD cents (rounded)."""
    return round(amount_cents * USD_PER_UNIT[currency])
