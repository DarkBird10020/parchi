import hashlib
import hmac as hmac_mod
import json
import time

from fastapi.testclient import TestClient

from demo import server
from parchi.mandate import Cart, CartLine, new_mandate, sign
from parchi.razorpay import RazorpayClient, RazorpayOrder

client = TestClient(server.app)


def setup_function():
    server.engine.provider = "heuristic"
    client.post("/api/reset")


def test_generic_api_resolves_a_trusted_server_key():
    mandate = new_mandate(
        "usr_demo", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
        "buy running shoes", issued_at=int(time.time()) - 60,
    )
    cart = Cart((CartLine("running shoes", "footwear", 420_000),), "upi", "mrc_bluleaf")
    response = client.post("/api/authorizations", json={
        "mandate": mandate.to_dict(), "signature": sign(mandate, server.KEY),
        "cart": cart.to_dict(),
    })
    assert response.status_code == 200
    assert response.json()["state"] == "ALLOW"


def test_generic_api_rejects_an_unregistered_payer():
    mandate = new_mandate(
        "attacker", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
        "buy running shoes", issued_at=int(time.time()) - 60,
    )
    cart = Cart((CartLine("running shoes", "footwear", 420_000),), "upi", "mrc_bluleaf")
    response = client.post("/api/authorizations", json={
        "mandate": mandate.to_dict(), "signature": sign(mandate, server.KEY),
        "cart": cart.to_dict(),
    })
    assert response.status_code == 403


def test_step_up_can_be_approved_once():
    started = client.post("/api/authorize", json={"scenario": "step_up"}).json()
    assert started["state"] == "PENDING"
    approved = client.post("/api/approve", json={
        "txn_id": started["authorization_id"],
        "approval_token": started["approval_token"], "approve": True,
    })
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"
    assert approved.json()["decision"]["verdict"] == "ALLOW"
    repeated = client.post("/api/approve", json={
        "txn_id": started["authorization_id"],
        "approval_token": started["approval_token"], "approve": True,
    })
    assert repeated.status_code == 409


def test_step_up_rejects_a_guessed_approval_token():
    started = client.post("/api/authorize", json={"scenario": "step_up"}).json()
    response = client.post("/api/approve", json={
        "txn_id": started["authorization_id"],
        "approval_token": "guessed", "approve": True,
    })
    assert response.status_code == 403


def test_blocked_authorization_cannot_create_an_order(monkeypatch):
    monkeypatch.setattr(server, "razorpay", type("Gateway", (), {"key_id": "rzp_test_x"})())
    started = client.post("/api/authorize", json={"scenario": "over_cap"}).json()
    response = client.post("/api/razorpay/order", json={
        "txn_id": started["authorization_id"],
    })
    assert response.status_code == 404


def test_generic_api_rejects_non_integer_money():
    mandate = new_mandate(
        "usr_demo", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
        "buy running shoes", issued_at=int(time.time()) - 60,
    )
    cart = Cart((CartLine("running shoes", "footwear", 420_000),), "upi", "mrc_bluleaf")
    body = {
        "mandate": mandate.to_dict(), "signature": sign(mandate, server.KEY),
        "cart": cart.to_dict(),
    }
    body["cart"]["lines"][0]["amount_paise"] = "420000"
    response = client.post("/api/authorizations", json=body)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# webhooks: the other half of the payment loop
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "whsec_test"


def _webhook_client(monkeypatch, order):
    """Server with a configured webhook secret and one authorization holding an order."""
    monkeypatch.setattr(
        server, "razorpay",
        RazorpayClient("rzp_test_123", "secret", webhook_secret=WEBHOOK_SECRET),
    )
    monkeypatch.setattr(server.RazorpayClient, "create_order", lambda *a, **k: order)
    started = client.post("/api/authorize", json={"scenario": "allow"}).json()
    ordered = client.post("/api/razorpay/order", json={"txn_id": started["authorization_id"]})
    assert ordered.status_code == 200
    return started["authorization_id"]


def _signed_event(event, order_id, payment_id="pay_1"):
    raw = json.dumps({
        "event": event,
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id}}},
    }).encode()
    return raw, hmac_mod.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def test_captured_webhook_closes_the_loop_and_ledgers_it(monkeypatch):
    txn_id = _webhook_client(monkeypatch, RazorpayOrder("order_1", 420_000, "INR", "created"))
    raw, signature = _signed_event("payment.captured", "order_1")

    response = client.post(
        "/api/razorpay/webhook", content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True and body["authorization_id"] == txn_id
    record = server.authorizations[txn_id]
    assert record["state"] == "CAPTURED" and record["payment_id"] == "pay_1"
    # The outcome is in the hash-chained ledger, not just in memory.
    import os
    with open(server.LEDGER_PATH, encoding="utf-8") as f:
        last = f.read().splitlines()[-1]
    entry = json.loads(last)
    assert entry["txn"]["event"] == "payment_captured"
    assert entry["txn"]["txn_id"] == txn_id and entry["txn"]["razorpay_payment_id"] == "pay_1"
    assert os.path.exists(server.LEDGER_PATH)


def test_failed_webhook_marks_the_authorization_failed(monkeypatch):
    txn_id = _webhook_client(monkeypatch, RazorpayOrder("order_2", 420_000, "INR", "created"))
    raw, signature = _signed_event("payment.failed", "order_2", payment_id="pay_2")

    response = client.post(
        "/api/razorpay/webhook", content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
    )

    assert response.status_code == 200
    assert server.authorizations[txn_id]["state"] == "FAILED"


def test_webhook_with_a_forged_signature_is_rejected(monkeypatch):
    _webhook_client(monkeypatch, RazorpayOrder("order_3", 420_000, "INR", "created"))
    raw, _ = _signed_event("payment.captured", "order_3")
    response = client.post(
        "/api/razorpay/webhook", content=raw,
        headers={"Content-Type": "application/json",
                 "X-Razorpay-Signature": "0" * 64},
    )
    assert response.status_code == 400
    assert "invalid webhook signature" in response.json()["detail"]


def test_webhook_for_an_unknown_order_is_acknowledged_not_matched(monkeypatch):
    _webhook_client(monkeypatch, RazorpayOrder("order_real", 420_000, "INR", "created"))
    raw, signature = _signed_event("payment.captured", "order_someone_elses")
    response = client.post(
        "/api/razorpay/webhook", content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": signature},
    )
    assert response.status_code == 200
    assert response.json()["matched"] is False


def test_webhook_without_configuration_returns_503(monkeypatch):
    monkeypatch.setattr(server, "razorpay", None)
    response = client.post(
        "/api/razorpay/webhook", content=b"{}",
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": "x"},
    )
    assert response.status_code == 503
