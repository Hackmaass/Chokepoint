"""Download the IBM HI-Small transactions from the Hugging Face mirror, verify the checksum, and extract.

  uv run python scripts/download_data.py

The data is synthetic (IBM, CDLA-Sharing-1.0; see docs/g7-data-and-pilot.md). It is never committed.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import httpx

URL = ("https://huggingface.co/datasets/eexzzm/IBM-Transactions-for-Anti-Money-Laundering-HI-Small-Trans"
       "/resolve/main/HI-Small_Trans.csv.zip")
SHA256 = "4407435ec99e37262294b3d4e317f3adcb8a89caa0df66927d1c6ba4f46e8080"
ROOT = Path("data/raw/ibm_hi_small")
ZIP = ROOT / "HI-Small_Trans.csv.zip"
OUT = ROOT / "extracted"

if (OUT / "HI-Small_Trans.csv").exists():
    print("already present:", OUT / "HI-Small_Trans.csv")
    sys.exit(0)
ROOT.mkdir(parents=True, exist_ok=True)
h = hashlib.sha256()
with httpx.stream("GET", URL, follow_redirects=True, timeout=300) as r:
    r.raise_for_status()
    with open(ZIP, "wb") as f:
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)
            h.update(chunk)
if h.hexdigest() != SHA256:
    ZIP.unlink()
    sys.exit(f"checksum mismatch: got {h.hexdigest()}, expected {SHA256}")
OUT.mkdir(exist_ok=True)
with zipfile.ZipFile(ZIP) as z:
    for info in z.infolist():
        if info.filename.startswith(("/", "..")) or ".." in info.filename:
            sys.exit(f"unsafe path in archive: {info.filename}")
    z.extractall(OUT)
print("downloaded and verified:", OUT / "HI-Small_Trans.csv")
