"""Check that every headline number in the README, docs/for-judges.md and the landing page equals claims.json, and that each
'recorded' claim is backed by its evidence file. Standard library only, no database: CI runs it. Exit 1 on any mismatch.

Markers: Markdown `<!--claim:KEY-->VALUE<!--/claim-->`; TSX a line comment `// claim:KEY=VALUE` on the line holding the number.
The 'measured' claims are re-derived against the database by `scripts/judge_check.py --claims`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD_FILES = ["README.md", "docs/for-judges.md"]
TSX_FILES = ["web/src/Landing.tsx"]
MD = re.compile(r"<!--claim:(\w+)-->(.*?)<!--/claim-->")
TS = re.compile(r"claim:(\w+)=(\S+)")
NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")


def load() -> dict:
    return {k: v for k, v in json.loads((ROOT / "claims.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}


def problems() -> list[str]:
    claims, out, used = load(), [], set()
    for k, c in claims.items():
        if c["kind"] != "recorded":
            continue
        text = (ROOT / c["source"]).read_text(encoding="utf-8")
        if c["evidence"] not in text:
            out.append(f"{k}: evidence {c['evidence']!r} not found in {c['source']}")
            continue
        first = NUM.search(c["evidence"].replace("**", ""))
        want = NUM.search(c["value"])
        if not first or not want:
            continue
        dec = len(want.group().split(".")[1]) if "." in want.group() else 0
        if round(float(first.group()), dec) != round(float(want.group()), dec) or float(first.group()) * float(want.group()) < 0:
            out.append(f"{k}: value {c['value']} does not match the evidence number {first.group()} after rounding")
    for f in MD_FILES:
        for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            for key, val in MD.findall(line):
                used.add(key)
                if key not in claims:
                    out.append(f"{f}:{i}: unknown claim {key}")
                elif val.strip() != claims[key]["value"]:
                    out.append(f"{f}:{i}: {key} says {val.strip()!r}, claims.json says {claims[key]['value']!r}")
    for f in TSX_FILES:
        for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            for key, val in TS.findall(line):
                used.add(key)
                if key not in claims:
                    out.append(f"{f}:{i}: unknown claim {key}")
                elif val != claims[key]["value"]:
                    out.append(f"{f}:{i}: {key} says {val!r}, claims.json says {claims[key]['value']!r}")
                elif claims[key]["value"] not in line.replace("−", "-"):
                    out.append(f"{f}:{i}: {key} is marked but {claims[key]['value']} is not on that line")
    out += [f"{k}: in claims.json but never marked in the README, for-judges or landing page" for k in claims if k not in used]
    return out


if __name__ == "__main__":
    bad = problems()
    print("\n".join(bad) if bad else f"claims ok: {len(load())} claims, all markers match claims.json")
    sys.exit(1 if bad else 0)
