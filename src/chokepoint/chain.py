"""Hash-chained receipts. Every ToolCall, Finding, FreezePlan, FreezeOrder and Dispatch stores
``hash = sha256(canon + "|" + parent_hash)``, where ``canon`` is a text built once from the record's immutable stored fields
(mutable ones such as status or verified are left out). The server sends each record's ``canon`` as a string, so the browser
only hashes what it receives (WebCrypto) and never has to re-serialise JSON; a changed stored value shows up as the first
record whose recomputed hash differs from the stored one.
"""

from __future__ import annotations

import hashlib
import json


def canon(*fields) -> str:
    return json.dumps(list(fields), ensure_ascii=False, separators=(",", ":"))


def chain_hash(text: str, parent: str) -> str:
    return hashlib.sha256((text + "|" + parent).encode("utf-8")).hexdigest()


def verify_chain(records: list[dict]) -> dict:
    """Recompute the chain from the records exactly as the browser does. ``records`` are in parent-before-child order, each
    {type, id, canon, parent_hash, hash, parent_id}. Returns {ok, checked, first_broken}; records written before chaining
    was added have no hash and are counted as ``unchained``, not as broken."""
    stored: dict[str, str] = {}
    checked = unchained = 0
    for r in records:
        if not r.get("hash"):
            unchained += 1
            continue
        parent_ok = (r["parent_id"] is None and r["parent_hash"] == "") or stored.get(r["parent_id"]) == r["parent_hash"]
        if not parent_ok or chain_hash(r["canon"], r["parent_hash"]) != r["hash"]:
            return {"ok": False, "checked": checked, "unchained": unchained, "first_broken": {"type": r["type"], "id": r["id"]}}
        stored[r["id"]] = r["hash"]
        checked += 1
    return {"ok": True, "checked": checked, "unchained": unchained, "first_broken": None}
