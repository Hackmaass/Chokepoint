"""Loader for the IBM synthetic AML transactions (HI-Small). Data is never committed.

Source: IBM "Realistic Synthetic Financial Transactions for Anti-Money Laundering Models"
(Altman et al., NeurIPS 2023 Datasets and Benchmarks, arXiv:2306.16424). All data is synthetic.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

DEFAULT_CSV = Path("data/raw/ibm_hi_small/extracted/HI-Small_Trans.csv")
COLS = ["ts", "from_bank", "src", "to_bank", "dst", "amt_recv", "cur_recv", "amt_paid",
        "cur_paid", "fmt", "laundering"]


def load_transactions(path: Path | str = DEFAULT_CSV) -> pl.DataFrame:
    """One row per transfer, with a stable ``tid`` (row number in the file) and ``ts`` as epoch
    minutes (int) plus a datetime column ``dt``. The file's header has two columns named
    ``Account``, so columns are renamed positionally."""
    df = pl.read_csv(
        path, has_header=True, new_columns=COLS,
        schema_overrides={"from_bank": pl.Utf8, "to_bank": pl.Utf8, "src": pl.Utf8, "dst": pl.Utf8},
    )
    return df.with_row_index("tid").with_columns(
        pl.col("ts").str.strptime(pl.Datetime, "%Y/%m/%d %H:%M").alias("dt"),
    ).with_columns(
        (pl.col("dt").dt.epoch("s") // 60).alias("ts_min"),
        # account identity is (bank, account): the same account string at two banks is distinct
        (pl.col("from_bank") + ":" + pl.col("src")).alias("a_src"),
        (pl.col("to_bank") + ":" + pl.col("dst")).alias("a_dst"),
    ).drop("ts")
