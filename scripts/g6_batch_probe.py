"""G6: load throughput for different batch sizes on a slice (throwaway graphs)."""

from __future__ import annotations

import time

from chokepoint.data_ibm import load_transactions
from chokepoint.db import connect
from chokepoint.ingest import load_tenant, prepare

db = connect()
df = prepare(load_transactions()).head(120_000)
print("slice transfers", df.height)
for bs in (1000, 5000, 10000):
    name = f"g6_{bs}"
    try:
        db.select_graph(f"tenant_{name}").delete()
    except Exception:
        pass
    t0 = time.time()
    st = load_tenant(db, df, name, batch_size=bs)
    total = time.time() - t0
    print(f"batch {bs:6d}: accounts {st.accounts}, edges {st.transfers}, nodes {st.seconds_nodes:5.1f}s, edges {st.seconds_edges:6.1f}s "
          f"({st.transfers / st.seconds_edges:,.0f} edges/s), total {total:.1f}s")
    db.select_graph(f"tenant_{name}").delete()
