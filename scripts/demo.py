"""Walk one alert through the tools in the terminal. No model, no API key; the figures come from tool results.

  uv run python scripts/demo.py                              # the example alert from docs/demo-script.md
  uv run python scripts/demo.py --account 024515:80EBB2100 --alert-ts 2022-09-04T11:36

Needs the FalkorDB container running with the transfer graph loaded (docker compose up, then wait for /api/ready).
"""

from __future__ import annotations

import argparse
import sys

from chokepoint.db import connect
from chokepoint.toolbox import Toolbox
from chokepoint.walkthrough import default_alert, run_walkthrough

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--account", help="alerted account, bank:account")
ap.add_argument("--alert-ts", help="alert time, e.g. 2022-09-10T10:17")
args = ap.parse_args()

account, alert_ts = default_alert()
account, alert_ts = args.account or account, args.alert_ts or (alert_ts if not args.account else None)
if not alert_ts:
    ap.error("--alert-ts is required with --account")

db = connect(socket_timeout=120, socket_connect_timeout=5)
try:
    if "tenant_ibm" not in db.list_graphs():
        sys.exit("The transfer graph is not loaded yet. Start the stack and wait until /api/ready says ready.")
except Exception as exc:  # connection refused, DB still loading, wrong password
    sys.exit(f"Cannot reach FalkorDB ({type(exc).__name__}). Is the container running? See docs/deploy.md.")

result = run_walkthrough(Toolbox(db), account, alert_ts)
no_route = "error" not in result.get("open_case", {"error": 1}) and not result["open_case"]["has_route_to_perimeter"]
sys.exit(0 if "explain_finding" in result or no_route else 1)
