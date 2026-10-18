"""Cut a small CSV (same format as HI-Small_Trans.csv) that holds every transfer touching an account in the region
of any example alert. Cases opened on the slice give the same figures as on the full file, because the tools only read
transfers of region accounts. Used for a small public host; needs the full database and the full CSV.

  uv run python scripts/make_slice.py            # writes examples/slice/HI-Small_slice.csv
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from chokepoint.casegraph import extract_region
from chokepoint.data_ibm import COLS, DEFAULT_CSV
from chokepoint.db import connect
from chokepoint.toolbox import DAY, DEFAULT_WINDOW_DAYS, MAX_CASE_NODES, parse_ts

OUT = Path("examples/slice/HI-Small_slice.csv")
g = connect().select_graph("tenant_ibm")
keep: set[str] = set()
for ex in json.load(open("examples/examples.json")):
    r = extract_region(g, ex["account"], parse_ts(ex["alert_ts"]), DEFAULT_WINDOW_DAYS * DAY, 3, 0, MAX_CASE_NODES)
    keep |= set(r.depth)
print(f"{len(keep)} accounts in the 8 example regions")

raw = pl.read_csv(DEFAULT_CSV, new_columns=COLS, infer_schema=False)
hit = ((pl.col("from_bank") + ":" + pl.col("src")).is_in(keep)) | ((pl.col("to_bank") + ":" + pl.col("dst")).is_in(keep))
part = raw.filter(hit)
OUT.parent.mkdir(parents=True, exist_ok=True)
part.write_csv(OUT)
print(f"{part.height} of {raw.height} transfers -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
