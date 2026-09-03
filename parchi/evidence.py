"""Dispute evidence pack builder.

Prototype evidence JSON carrying the signed slip, every check with its reason,
the verdict, and the ledger hash that ties the record to the local chain.
"""

from __future__ import annotations

import json
from typing import Any

from .engine import Decision
from .ledger import Ledger, verify_chain
from .mandate import Cart, IntentMandate, rupees

SCHEMA_VERSION = "parchi-evidence/1"


def build_pack(
    mandate: IntentMandate,
    signature: str,
    cart: Cart,
    decision: Decision,
    public_key_hex: str,
    ledger_path: str | None = None,
) -> dict[str, Any]:
    pack: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "txn_id": decision.txn_id,
        "verdict": decision.verdict,
        "reason": decision.reason,
        "amount": {
            "total_paise": cart.total_paise,
            "display": rupees(cart.total_paise),
            "currency": "INR",
        },
        "mandate": mandate.to_dict(),
        "mandate_canonical_sha256": _sha256(mandate.canonical()),
        "signature": signature,
        "payer_public_key": public_key_hex,
        "cart": cart.to_dict(),
        "checks": [c.to_dict() for c in decision.checks],
        "intent_check": decision.intent.to_dict() if decision.intent else None,
        "degraded": decision.degraded,
        "ledger_hash": decision.ledger_hash,
    }
    if ledger_path:
        ok, msg, n = verify_chain(ledger_path)
        bound = False
        if ok and decision.ledger_hash:
            try:
                bound = any(
                    rec.get("hash") == decision.ledger_hash
                    and rec.get("txn", {}).get("txn_id") == decision.txn_id
                    and rec.get("verdict") == decision.verdict
                    for rec in Ledger(ledger_path).records()
                )
            except (KeyError, TypeError, ValueError):
                ok = False
                msg = "ledger contains an unreadable record"
        pack["ledger_chain"] = {
            "intact": ok,
            "decision_bound": bound,
            "detail": msg if bound or not ok else "decision is absent from the supplied ledger",
            "records": n,
        }
    return pack


def _sha256(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def dump(pack: dict[str, Any], path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2)
    return path
