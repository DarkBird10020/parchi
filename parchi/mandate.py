"""AP2-inspired signed intent record: the slip.

The mandate is the object every other module operates on. It is signed by the
human's key at approval time and never edited afterwards. Canonical bytes are
the whole game: if signing and verification do not serialise to byte-identical
JSON, every signature check fails.
"""

from __future__ import annotations

import json
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# A cart at or above this value is never waved through on rules alone.
STEP_UP_PAISE = 1_000_000  # Rs 10,000

MANDATE_TTL_SECONDS = 24 * 60 * 60  # Demo policy, not a protocol requirement.

# A cart with more lines than this is not something a human approved in one
# sentence; it is either a bug or someone probing the checkpoint.
MAX_CART_LINES = 50

# Same for quantity: a human buying 100 of one personal item is either a bug or
# an attempt to exhaust the cap through one line.
MAX_LINE_QUANTITY = 50

# Tolerance for honest clock drift between the signing device and the merchant.
CLOCK_SKEW_SECONDS = 300


def norm(value: Any) -> str:
    """Normalise a token before comparing it to another token.

    NFKC folds the compatibility forms, casefold handles 'UPI' vs 'upi', strip
    handles ' footwear '. It deliberately does NOT touch confusables: a Cyrillic
    'о' stays a Cyrillic 'о' and fails the comparison, which is the safe
    direction for a check that decides whether to spend money.
    """
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


@dataclass(frozen=True)
class IntentMandate:
    """The permission slip a human signs before an agent is allowed to spend.

    Fields apply AP2-style signed intent constraints. This custom JSON record
    does not claim AP2 wire-format conformance.
    """

    mandate_id: str
    payer_id: str
    payee_id: str
    allowed_methods: tuple            # ("upi", "card")
    max_amount_paise: int             # paise, never floats. money is integers
    allowed_categories: tuple         # ("footwear",)
    prompt_playback: str              # "buy running shoes under Rs 5000"
    issued_at: int                    # unix seconds
    expires_at: int                   # unix seconds. TTL, ~24h
    nonce: str                        # one-time use, so it cannot be replayed
    allowed_agent_id: str = ""        # optional: which agent may present this slip

    def canonical(self) -> bytes:
        """Sorted keys + no whitespace = the same bytes every time.

        Tuples serialise as JSON arrays, so a mandate rebuilt from JSON (lists)
        must be normalised through `from_dict` before it will verify.

        Empty optional fields are omitted so adding them later does not break
        signatures of older mandates that were issued without them.
        """
        d = {k: v for k, v in asdict(self).items() if v not in (None, "")}
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["allowed_methods"] = list(self.allowed_methods)
        d["allowed_categories"] = list(self.allowed_categories)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> IntentMandate:
        return IntentMandate(
            mandate_id=d["mandate_id"],
            payer_id=d["payer_id"],
            payee_id=d["payee_id"],
            allowed_methods=tuple(d["allowed_methods"]),
            max_amount_paise=int(d["max_amount_paise"]),
            allowed_categories=tuple(d["allowed_categories"]),
            prompt_playback=d["prompt_playback"],
            issued_at=int(d["issued_at"]),
            expires_at=int(d["expires_at"]),
            nonce=d["nonce"],
            allowed_agent_id=str(d.get("allowed_agent_id", "")),
        )


@dataclass(frozen=True)
class CartLine:
    """One line the agent put in the cart."""

    description: str
    category: str
    amount_paise: int
    quantity: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Cart:
    """What the agent actually wants to buy, and how."""

    lines: tuple
    method: str                       # "upi" | "card"
    payee_id: str
    merchant_note: str = ""           # product-page text; injection lives here
    agent_id: str = ""                # optional: which agent is presenting the cart
    agent_signature: str = ""         # optional: agent's signature over cart canonical bytes

    @property
    def total_paise(self) -> int:
        return sum(line.amount_paise * line.quantity for line in self.lines)

    @property
    def categories(self) -> tuple:
        return tuple(dict.fromkeys(line.category for line in self.lines))

    def canonical(self) -> bytes:
        """The bytes the agent signs."""
        d = {k: v for k, v in asdict(self).items() if k != "agent_signature"}
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    def to_dict(self) -> dict[str, Any]:
        return {
            "lines": [line.to_dict() for line in self.lines],
            "method": self.method,
            "payee_id": self.payee_id,
            "merchant_note": self.merchant_note,
            "agent_id": self.agent_id,
            "agent_signature": self.agent_signature,
            "total_paise": self.total_paise,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Cart:
        return Cart(
            lines=tuple(
                CartLine(
                    description=line["description"],
                    category=line["category"],
                    amount_paise=int(line["amount_paise"]),
                    quantity=int(line.get("quantity", 1)),
                )
                for line in d["lines"]
            ),
            method=d["method"],
            payee_id=d["payee_id"],
            merchant_note=d.get("merchant_note", ""),
            agent_id=d.get("agent_id", ""),
            agent_signature=d.get("agent_signature", ""),
        )


def sign(m: IntentMandate, key: Ed25519PrivateKey) -> str:
    return key.sign(m.canonical()).hex()


def verify(m: IntentMandate, sig_hex: str, pub: Ed25519PublicKey) -> bool:
    try:
        pub.verify(bytes.fromhex(sig_hex), m.canonical())
        return True
    except Exception:
        return False


def sign_cart(cart: Cart, key: Ed25519PrivateKey) -> str:
    return key.sign(cart.canonical()).hex()


def verify_cart(cart: Cart, sig_hex: str, pub: Ed25519PublicKey) -> bool:
    try:
        pub.verify(bytes.fromhex(sig_hex), cart.canonical())
        return True
    except Exception:
        return False


def new_mandate(
    payer_id: str,
    payee_id: str,
    allowed_methods: tuple,
    max_amount_paise: int,
    allowed_categories: tuple,
    prompt_playback: str,
    issued_at: int | None = None,
    ttl_seconds: int = MANDATE_TTL_SECONDS,
    mandate_id: str | None = None,
    nonce: str | None = None,
    allowed_agent_id: str = "",
) -> IntentMandate:
    """Mint a mandate.

    `mandate_id` and `nonce` default to uuid4 and that default must stay: a
    predictable nonce is a replay vulnerability, not a convenience. They are
    injectable only so a *fixed-seed dataset generator* can produce byte-identical
    output across runs - without that, `data/generate.py` reseeds nothing here and
    every run of the batch differs, which quietly breaks both the reproducibility
    claim in the README and the CI check that the dataset has not moved.
    """
    issued = int(issued_at if issued_at is not None else time.time())
    return IntentMandate(
        mandate_id=mandate_id or ("mnd_" + uuid.uuid4().hex[:16]),
        payer_id=payer_id,
        payee_id=payee_id,
        allowed_methods=tuple(allowed_methods),
        max_amount_paise=int(max_amount_paise),
        allowed_categories=tuple(allowed_categories),
        prompt_playback=prompt_playback,
        issued_at=issued,
        expires_at=issued + ttl_seconds,
        nonce=nonce or ("nc_" + uuid.uuid4().hex),
        allowed_agent_id=allowed_agent_id or "",
    )


def rupees(paise: int) -> str:
    """Money is integers everywhere; this is the only place it becomes prose."""
    return f"Rs {paise / 100:,.2f}"
