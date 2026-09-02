"""Minimal Razorpay test-mode Orders and Checkout verification adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class RazorpayError(RuntimeError):
    pass


@dataclass(frozen=True)
class RazorpayOrder:
    id: str
    amount: int
    currency: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "amount": self.amount,
            "currency": self.currency,
            "status": self.status,
        }


class RazorpayClient:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str | None = None,
    ) -> None:
        if not key_id.startswith("rzp_test_"):
            raise RazorpayError("only Razorpay test-mode keys are accepted")
        if not key_secret:
            raise RazorpayError("Razorpay key secret is empty")
        self.key_id = key_id
        self._secret = key_secret
        self._webhook_secret = webhook_secret or ""

    @classmethod
    def from_env(cls) -> RazorpayClient | None:
        key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        if not key_id and not key_secret:
            return None
        return cls(key_id, key_secret, os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip())

    @property
    def webhooks_configured(self) -> bool:
        return bool(self._webhook_secret)

    def create_order(
        self, authorization_id: str, mandate_id: str, amount_paise: int
    ) -> RazorpayOrder:
        payload = json.dumps({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": authorization_id[:40],
            "notes": {
                "authorization_id": authorization_id,
                "mandate_id": mandate_id,
            },
        }).encode()
        token = base64.b64encode(f"{self.key_id}:{self._secret}".encode()).decode()
        request = urllib.request.Request(
            "https://api.razorpay.com/v1/orders",
            data=payload,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RazorpayError(f"Razorpay Orders API returned HTTP {exc.code}") from None
        except Exception as exc:
            raise RazorpayError(f"Razorpay Orders API unavailable ({type(exc).__name__})") from None
        if result.get("amount") != amount_paise or result.get("currency") != "INR":
            raise RazorpayError("Razorpay order amount or currency mismatch")
        if not str(result.get("id", "")).startswith("order_"):
            raise RazorpayError("Razorpay returned an invalid order id")
        return RazorpayOrder(result["id"], result["amount"], result["currency"], result["status"])

    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        expected = hmac.new(
            self._secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, raw_body: bytes | str, signature: str) -> bool:
        """HMAC-SHA256 over the exact raw request body, per Razorpay webhooks docs.

        The body must be the bytes Razorpay sent, before any JSON re-serialisation:
        a re-serialised body changes whitespace or key order and the signature
        stops matching. An empty webhook secret means webhooks were never
        configured, which must not silently verify.
        """
        if not self._webhook_secret:
            return False
        if isinstance(raw_body, str):
            raw_body = raw_body.encode()
        expected = hmac.new(self._webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
