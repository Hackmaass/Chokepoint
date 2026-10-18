"""Load the IBM HI-Small transactions into FalkorDB (tenant_ibm + eval_ibm).

  uv run python scripts/load_ibm.py            # load if absent
  uv run python scripts/load_ibm.py --force    # delete and reload

Needs the dataset in data/raw/ibm_hi_small/extracted/HI-Small_Trans.csv (see docs/g7-data-and-pilot.md).
Takes about 2 minutes and about 2 GiB of database memory for 4.49 million transfers.
"""

from __future__ import annotations

import argparse
import time

from chokepoint.db import connect
from chokepoint.ingest import load_all

ap = argparse.ArgumentParser()
ap.add_argument("--force", action="store_true")
ap.add_argument("--limit", type=int, default=0, help="load only the first N rows (tenant graph only)")
ap.add_argument("--name", default="ibm")
ap.add_argument("--csv", default="", help="load this file instead of the full dataset (see make_slice.py)")
args = ap.parse_args()

db = connect()
existing = set(db.list_graphs())
for g in (f"tenant_{args.name}", f"eval_{args.name}"):
    if g in existing:
        if not args.force:
            print(f"{g} already exists; use --force to reload")
            raise SystemExit(0)
        db.select_graph(g).delete()
t0 = time.time()
stats, n_labels = load_all(db, args.name, limit=args.limit or None, batch_size=10_000, csv=args.csv or None)
print(f"accounts {stats.accounts:,}, transfers {stats.transfers:,}, labels {n_labels:,}, {time.time() - t0:.0f}s")
