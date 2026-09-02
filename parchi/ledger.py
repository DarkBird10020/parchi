"""Hash-chained audit log - why anyone should believe the record.

Every record carries the hash of the one before it. Change an old line and
every hash after it stops matching, which `verify_chain` will tell you and
`tests/test_ledger.py` proves.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator
from typing import Any

GENESIS = "0" * 64


def _record_hash(rec: dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Ledger:
    def __init__(self, path: str = "ledger.jsonl") -> None:
        self.path = path
        self._lock = threading.Lock()
        self.prev = self._last_hash()

    def _last_hash(self) -> str:
        if not os.path.exists(self.path):
            return GENESIS
        last = GENESIS
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = json.loads(line)["hash"]
        return last

    def append(
        self,
        mandate_id: str,
        txn: dict[str, Any],
        checks: list[dict[str, Any]],
        verdict: str,
        degraded: bool = False,
        intent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._append(mandate_id, txn, checks, verdict, degraded, intent)

    def _append(
        self,
        mandate_id: str,
        txn: dict[str, Any],
        checks: list[dict[str, Any]],
        verdict: str,
        degraded: bool,
        intent: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # If the file went away under us (rotated, deleted, wiped by a demo
        # reset), the in-memory `prev` now points at a hash no reader can find.
        # A chain that starts mid-air is worse than no chain: re-anchor.
        if not os.path.exists(self.path):
            self.prev = GENESIS

        rec = {
            "ts": int(time.time() * 1000),
            "mandate_id": mandate_id,
            "txn": txn,
            "checks": checks,          # every check and its reason
            "intent": intent or {},    # the one model decision, if it ran
            "verdict": verdict,
            "degraded": degraded,      # true when the llm was skipped
            "prev": self.prev,
        }
        rec["hash"] = _record_hash(rec)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self.prev = rec["hash"]
        return rec

    def records(self) -> Iterator[dict[str, Any]]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def find(self, txn_id: str) -> dict[str, Any] | None:
        for rec in self.records():
            if rec["txn"].get("txn_id") == txn_id:
                return rec
        return None


def verify_chain(path: str = "ledger.jsonl") -> tuple[bool, str, int]:
    """Walk the log. Return (ok, message, records_checked)."""
    prev = GENESIS
    n = 0
    if not os.path.exists(path):
        return True, "no ledger yet - nothing to verify", 0
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            n += 1
            rec = json.loads(line)
            if rec.get("prev") != prev:
                return False, f"record {i} does not link to record {i - 1}", n
            if rec.get("hash") != _record_hash(rec):
                return False, f"record {i} has been altered - hash does not match its body", n
            prev = rec["hash"]
    return True, f"chain intact across {n} records", n
