import hashlib
import hmac as hmac_mod
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from demo import server
from parchi.mandate import Cart, CartLine, new_mandate, sign
from parchi.operators import OperatorDirectory, hash_password
from parchi.razorpay import RazorpayClient, RazorpayOrder

client = TestClient(server.app)


def setup_function():
    server.engine.provider = "heuristic"
    server.HUMAN_APPROVAL_SECRET = ""
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
    server.HUMAN_APPROVAL_SECRET = "human-secret"
    token = client.get(
        f"/api/human/approval-token/{started['authorization_id']}",
        headers={"X-Parchi-Human-Secret": "human-secret"},
    ).json()["approval_token"]
    approved = client.post("/api/approve", json={
        "txn_id": started["authorization_id"],
        "approval_token": token, "approve": True,
    })
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"
    assert approved.json()["decision"]["verdict"] == "ALLOW"
    repeated = client.post("/api/approve", json={
        "txn_id": started["authorization_id"],
        "approval_token": token, "approve": True,
    })
    assert repeated.status_code == 409


def test_step_up_token_is_not_returned_to_initiator():
    started = client.post("/api/authorize", json={"scenario": "step_up"}).json()
    assert "approval_token" not in started
    assert client.get(
        f"/api/human/approval-token/{started['authorization_id']}"
    ).status_code == 503


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
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": 420_000, "currency": "INR",
        }}},
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


def test_duplicate_webhook_event_is_idempotent(monkeypatch):
    txn_id = _webhook_client(monkeypatch, RazorpayOrder("order_dup", 420_000, "INR", "created"))
    raw, signature = _signed_event("payment.captured", "order_dup")
    headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": "evt_1"}
    assert client.post("/api/razorpay/webhook", content=raw, headers=headers).status_code == 200
    before = len(list(server.engine.ledger.records()))
    repeated = client.post("/api/razorpay/webhook", content=raw, headers=headers)
    assert repeated.json()["duplicate"] is True
    assert len(list(server.engine.ledger.records())) == before
    assert server.authorizations[txn_id]["state"] == "CAPTURED"


def test_refund_webhook_uses_refund_payload_and_payment_id(monkeypatch):
    txn_id = _webhook_client(monkeypatch, RazorpayOrder("order_ref", 420_000, "INR", "created"))
    captured, captured_sig = _signed_event("payment.captured", "order_ref", "pay_ref")
    assert client.post("/api/razorpay/webhook", content=captured, headers={
        "X-Razorpay-Signature": captured_sig,
    }).status_code == 200
    raw = json.dumps({
        "event": "refund.processed",
        "payload": {"refund": {"entity": {
            "id": "rfnd_1", "payment_id": "pay_ref", "amount": 420_000,
            "currency": "INR", "status": "processed",
        }}},
    }).encode()
    signature = hmac_mod.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post("/api/razorpay/webhook", content=raw, headers={
        "X-Razorpay-Signature": signature,
    })
    assert response.status_code == 200
    assert server.authorizations[txn_id]["state"] == "REFUNDED"


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


def test_a_product_outside_the_category_is_declined_with_a_readable_reason():
    """The reason string ends up in front of a customer, so it has to name the
    thing that went wrong rather than a check id."""
    r = client.post("/api/authorize", json={"scenario": "wrong_category"})
    assert r.status_code == 200
    decision = r.json()["decision"]
    assert decision["verdict"] == "BLOCK"
    failed = next(c for c in decision["checks"] if not c["passed"])
    assert failed["name"] == "category"
    assert "electronics" in failed["reason"] and "footwear" in failed["reason"]


def test_a_substituted_delivery_requires_a_real_refund():
    """The checkpoint runs before the money moves, which leaves a gap: an agent
    can be authorised for one thing and the merchant can ship another. The signed
    mandate is still the record of what was agreed, so it is checked again."""
    authorized = client.post("/api/authorize", json={"scenario": "allow"}).json()
    assert authorized["decision"]["verdict"] == "ALLOW"

    r = client.post("/api/settle", json={"txn_id": authorized["authorization_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["state"] == "REFUND_REQUIRED"
    assert body["refund"]["check"] == "category"
    assert body["refund"]["amount_paise"] > 0
    # The refund is a ledger record like any other verdict, not a side note.
    assert body["ledger"]["intact"] is True


def test_a_settlement_cannot_be_replayed_to_refund_twice():
    authorized = client.post("/api/authorize", json={"scenario": "allow"}).json()
    txn = authorized["authorization_id"]
    assert client.post("/api/settle", json={"txn_id": txn}).status_code == 200
    again = client.post("/api/settle", json={"txn_id": txn})
    assert again.status_code == 409


def test_a_blocked_purchase_has_nothing_to_settle():
    blocked = client.post("/api/authorize", json={"scenario": "over_cap"}).json()
    r = client.post("/api/settle", json={"txn_id": blocked["authorization_id"]})
    assert r.status_code == 404


def test_the_refund_verdict_is_written_into_the_hash_chain():
    authorized = client.post("/api/authorize", json={"scenario": "allow"}).json()
    client.post("/api/settle", json={"txn_id": authorized["authorization_id"]})
    ledger = client.get("/api/ledger?limit=20").json()
    verdicts = [rec["verdict"] for rec in ledger["records"]]
    assert "REFUND_REQUIRED" in verdicts
    assert ledger["chain"]["intact"] is True


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def test_a_refund_raises_an_alert_on_the_server_not_just_in_a_browser():
    authorized = client.post("/api/authorize", json={"scenario": "allow"}).json()
    client.post("/api/settle", json={"txn_id": authorized["authorization_id"]})
    body = client.get("/api/alerts").json()
    refund = next(a for a in body["alerts"] if a["kind"] == "settlement_mismatch")
    assert refund["severity"] == "high"
    assert "support_console" in refund["delivered"]


def test_a_log_edited_on_disk_is_caught_by_the_next_read_not_by_a_button():
    """The Tamper button is a demo affordance. Detection cannot depend on it.

    Verification runs on every ledger read, so whoever looks next finds the
    break, including a support console polling in the background.
    """
    client.post("/api/authorize", json={"scenario": "allow"})
    client.post("/api/authorize", json={"scenario": "over_cap"})

    with open(server.LEDGER_PATH, encoding="utf-8") as f:
        before = f.read()
    lines = [ln for ln in before.splitlines() if ln.strip()]
    record = json.loads(lines[0])
    # Guard the guard: writing back the value it already had would change nothing
    # and this test would pass for the wrong reason.
    record["verdict"] = "BLOCK" if record["verdict"] != "BLOCK" else "ALLOW"
    lines[0] = json.dumps(record)
    with open(server.LEDGER_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(server.LEDGER_PATH, encoding="utf-8") as f:
        assert f.read() != before

    ledger = client.get("/api/ledger").json()
    assert ledger["chain"]["intact"] is False

    alerts = client.get("/api/alerts").json()
    assert alerts["open_critical"] >= 1
    assert any(a["kind"] == "ledger_tampered" for a in alerts["alerts"])


def test_one_break_does_not_raise_an_alert_per_page_refresh():
    client.post("/api/authorize", json={"scenario": "allow"})
    client.post("/api/authorize", json={"scenario": "over_cap"})
    client.post("/api/tamper")
    first = len(client.get("/api/alerts").json()["alerts"])
    for _ in range(4):
        client.get("/api/ledger")
    assert len(client.get("/api/alerts").json()["alerts"]) == first


def test_an_alert_webhook_failure_never_breaks_the_request(monkeypatch):
    """Monitoring that can take down the thing it monitors is worse than none."""
    monkeypatch.setattr(server, "ALERT_WEBHOOK", "http://127.0.0.1:9/does-not-exist")
    authorized = client.post("/api/authorize", json={"scenario": "allow"}).json()
    r = client.post("/api/settle", json={"txn_id": authorized["authorization_id"]})
    assert r.status_code == 200
    assert r.json()["state"] == "REFUND_REQUIRED"


def test_reset_clears_alerts_so_a_demo_starts_clean():
    client.post("/api/authorize", json={"scenario": "allow"})
    client.post("/api/authorize", json={"scenario": "over_cap"})
    client.post("/api/tamper")
    assert client.get("/api/alerts").json()["alerts"]
    client.post("/api/reset")
    assert client.get("/api/alerts").json()["alerts"] == []


# --------------------------------------------------------------------------
# the chat demo
# --------------------------------------------------------------------------

def test_chat_refuses_to_pretend_when_no_model_is_configured(monkeypatch):
    """Offline, this endpoint must say so rather than fall back to the lexical
    matcher. A scripted agent proves nothing about a checkpoint."""
    monkeypatch.setattr(server, "resolve_provider", lambda *a, **k: "heuristic")
    r = client.post("/api/chat", json={"message": "buy me running shoes under 5000"})
    assert r.status_code == 503
    assert "live model" in r.json()["detail"]


def test_chat_rejects_an_empty_or_oversized_message():
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 400


def test_the_catalogue_still_carries_the_attack_the_demo_depends_on():
    """One product description contains instructions aimed at an AI assistant.

    If that text is ever tidied away, the live demo silently stops demonstrating
    anything, so it is pinned here.
    """
    from demo import shopper

    catalogue = shopper.load_catalogue()
    skus = {p["sku"] for p in catalogue["products"]}
    assert "care-2y" in skus, "the add-on the injection asks for must exist"
    injected = [p for p in catalogue["products"]
                if "AI SHOPPING ASSISTANTS" in p["description"]]
    assert injected, "no product page carries the injected instruction"
    assert "care-2y" in injected[0]["description"]


def test_the_agent_sees_the_merchants_text_verbatim():
    """The agent is given the descriptions unfiltered, on purpose. Sanitising
    them here would move the defence into the agent, which is the thing this
    project argues you cannot rely on."""
    from demo import shopper

    products = shopper.load_catalogue()["products"]
    rendered = shopper.render_catalogue(products)
    assert "AI SHOPPING ASSISTANTS" in rendered
    assert "care-2y" in rendered


# --------------------------------------------------------------------------
# threat reporting, end to end
# --------------------------------------------------------------------------

@pytest.mark.parametrize("scenario,kind,severity", [
    ("over_cap", "cap_breach", "high"),
    ("wrong_category", "scope_breach", "high"),
    ("wrong_method", "instrument_abuse", "high"),
    ("expired", "expired_mandate", "info"),
    ("payee_substitution", "payee_substitution", "critical"),
    ("agent_substitution", "agent_impersonation", "critical"),
])
def test_each_refused_scenario_reaches_the_service_named_and_ranked(
        scenario, kind, severity):
    r = client.post("/api/authorize", json={"scenario": scenario}).json()
    assert r["decision"]["verdict"] == "BLOCK"
    assert r["threat"]["kind"] == kind
    assert r["threat"]["severity"] == severity

    raised = client.get("/api/alerts").json()["alerts"]
    assert any(a["kind"] == kind for a in raised), f"{kind} never reached /api/alerts"


def test_an_allowed_purchase_raises_nothing():
    """Alert fatigue is a security failure. A clean purchase must be silent."""
    r = client.post("/api/authorize", json={"scenario": "allow"}).json()
    assert r["decision"]["verdict"] == "ALLOW"
    assert r["threat"] is None
    assert client.get("/api/alerts").json()["alerts"] == []


def test_repeated_refusals_escalate_to_a_probing_alert():
    """Every one of these verdicts is correct and no money moves, which is why
    nobody would otherwise notice someone mapping the checkpoint."""
    for _ in range(5):
        client.post("/api/authorize", json={"scenario": "over_cap"})
    alerts = client.get("/api/alerts").json()
    probing = [a for a in alerts["alerts"] if a["kind"] == "probing"]
    assert probing, "five refusals in a row raised no probing alert"
    assert probing[0]["severity"] == "critical"


def test_the_injected_product_page_is_reported_as_an_attack_on_the_agent():
    """Same BLOCK as an ordinary intent mismatch, different attacker, so it has
    to reach the service as a different thing."""
    server.engine.provider = "heuristic"
    r = client.post("/api/authorize", json={"scenario": "injection"}).json()
    assert r["decision"]["verdict"] == "BLOCK"
    assert r["threat"]["kind"] == "prompt_injection"
    assert r["threat"]["severity"] == "critical"


def test_the_alert_payload_carries_every_field_the_history_tray_renders():
    """The bell renders id, timestamp, kind, severity, summary, detail and where
    it was delivered. Dropping any of those leaves a blank row in the UI and
    nothing in the tests, so the contract is pinned here rather than in a
    comment."""
    client.post("/api/authorize", json={"scenario": "payee_substitution"})
    alerts = client.get("/api/alerts").json()["alerts"]
    assert alerts, "a refused purchase raised no alert"
    a = alerts[0]
    for field in ("id", "ts", "kind", "severity", "summary", "detail", "delivered"):
        assert field in a, f"the history tray renders {field} and it is missing"
    assert a["severity"] in ("critical", "high", "info")
    assert isinstance(a["ts"], int) and a["ts"] > 0
    assert isinstance(a["delivered"], list) and a["delivered"]


def test_alert_ids_are_unique_so_the_unread_count_can_be_trusted():
    """The bell counts alerts this tab has not opened yet, keyed on id. Duplicate
    ids would under-count silently."""
    for scenario in ("over_cap", "wrong_category", "expired", "wrong_method"):
        client.post("/api/authorize", json={"scenario": scenario})
    ids = [a["id"] for a in client.get("/api/alerts").json()["alerts"]]
    assert len(ids) == len(set(ids)), "duplicate alert ids"


def test_the_history_survives_a_page_the_browser_never_had_open():
    """The whole reason for the bell: toasts clear after five seconds, so the
    record has to live somewhere a reload can still find it."""
    client.post("/api/authorize", json={"scenario": "agent_substitution"})
    first = client.get("/api/alerts").json()["alerts"]
    # A second, entirely separate read, as a fresh page load would do.
    second = client.get("/api/alerts").json()["alerts"]
    assert [a["id"] for a in first] == [a["id"] for a in second]
    assert any(a["kind"] == "agent_impersonation" for a in second)


# --------------------------------------------------------------------------
# the operations console
# --------------------------------------------------------------------------

def test_alerts_survive_a_restart(tmp_path, monkeypatch):
    """The alert file is the store of record; memory is only its tail.

    A console that empties on restart tells the operator "nothing happened",
    which is the one reading a fraud log must never produce.
    """
    alerts_path = tmp_path / "alerts.jsonl"
    alerts_path.write_text(
        json.dumps({"id": "alt_old1", "ts": 1, "kind": "probing",
                    "severity": "critical", "summary": "s", "detail": "d",
                    "txn_id": None, "acked": None, "delivered": []}) + "\n"
        # A torn final line: the previous process died mid-write. Skipped.
        + '{"id": "alt_tor', encoding="utf-8")
    monkeypatch.setattr(server, "ALERTS_PATH", str(alerts_path))
    monkeypatch.setattr(server, "_alerts_loaded", False)
    monkeypatch.setattr(server, "alerts", [])

    body = client.get("/api/alerts").json()
    assert [a["id"] for a in body["alerts"]] == ["alt_old1"]
    assert body["open_critical"] == 1

    # A second reload must not double-append what is already in memory.
    body_again = client.get("/api/alerts").json()
    assert [a["id"] for a in body_again["alerts"]] == ["alt_old1"]


def test_an_acknowledged_critical_stops_being_open(monkeypatch):
    """'Needs a person' has to be a queue a human can drain, not a counter."""
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    client.post("/api/authorize", json={"scenario": "payee_substitution"})
    before = client.get("/api/console/feed",
                        headers={"X-Parchi-Console-Token": "tok"}).json()
    critical = next(a for a in before["alerts"] if a["severity"] == "critical")
    assert before["open_critical"] >= 1

    r = client.post("/api/console/ack",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"ids": [critical["id"]]})
    assert r.status_code == 200
    assert r.json()["acked"] == [critical["id"]]

    after = client.get("/api/console/feed",
                       headers={"X-Parchi-Console-Token": "tok"}).json()
    acked = next(a for a in after["alerts"] if a["id"] == critical["id"])
    assert acked["acked"]["by"] == "machine-token"
    assert after["open_critical"] == before["open_critical"] - 1
    # Attribution, not deletion: the alert stays in the feed.
    assert any(a["id"] == critical["id"] for a in after["alerts"])


def test_watch_history_survives_repeated_clears_and_restart(tmp_path, monkeypatch):
    alerts_path = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(server, "ALERTS_PATH", str(alerts_path))
    monkeypatch.setattr(server, "alerts", [])
    monkeypatch.setattr(server, "_alerts_loaded", False)
    monkeypatch.setattr(server, "_current_alert_session", "watch_first")
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}

    server.raise_alert("probing", "critical", "first", "first session")
    first = client.post("/api/console/clear-alerts", headers=auth).json()
    server.raise_alert("scope_breach", "high", "second", "second session")
    second = client.post("/api/console/clear-alerts", headers=auth).json()

    assert first["archived_session_id"] != second["archived_session_id"]
    history = client.get("/api/console/watch-history", headers=auth).json()
    assert [s["id"] for s in history["sessions"]] == [
        second["archived_session_id"], first["archived_session_id"]]

    # Simulate fresh process memory. Disk must recover only current session into
    # the live feed while leaving both prior sessions in Watch history.
    server.alerts = []
    server._alerts_loaded = False
    server._current_alert_session = "discarded"
    active = client.get("/api/console/feed", headers=auth).json()
    assert all(a["session_id"] == second["session_id"] for a in active["alerts"])
    restarted = client.get("/api/console/watch-history", headers=auth).json()
    assert [s["id"] for s in restarted["sessions"]] == [
        second["archived_session_id"], first["archived_session_id"]]


def test_old_destructive_clear_recovers_blocked_alerts_from_ledger(
        tmp_path, monkeypatch):
    alerts_path = tmp_path / "alerts.jsonl"
    ledger_path = tmp_path / "ledger.jsonl"
    alerts_path.write_text(json.dumps({
        "id": "alt_oldclear", "ts": 200, "kind": "alerts_cleared",
        "severity": "info", "summary": "Alert feed cleared",
        "detail": "employee@example.com cleared all alerts. The ledger is untouched.",
        "txn_id": None, "actor": "", "acked": None, "delivered": [],
    }) + "\n", encoding="utf-8")
    ledger_path.write_text(json.dumps({
        "ts": 100, "verdict": "BLOCK", "txn": {"txn_id": "txn_recovered"},
        "checks": [{"name": "method", "passed": False,
                    "reason": "card was not authorised"}], "intent": {},
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(server, "ALERTS_PATH", str(alerts_path))
    monkeypatch.setattr(server, "LEDGER_PATH", str(ledger_path))
    monkeypatch.setattr(server, "alerts", [])
    monkeypatch.setattr(server, "_alerts_loaded", False)
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}

    history = client.get("/api/console/watch-history", headers=auth).json()
    assert len(history["sessions"]) == 1
    recovered = client.get(
        f"/api/console/watch-history/{history['sessions'][0]['id']}",
        headers=auth).json()["alerts"]
    assert recovered[0]["kind"] == "instrument_abuse"
    assert recovered[0]["txn_id"] == "txn_recovered"
    assert recovered[0]["delivered"] == ["recovered_from_audit_ledger"]


def test_only_an_employee_can_delete_archived_watch_history(tmp_path, monkeypatch):
    alerts_path = tmp_path / "alerts.jsonl"
    monkeypatch.setattr(server, "ALERTS_PATH", str(alerts_path))
    monkeypatch.setattr(server, "alerts", [])
    monkeypatch.setattr(server, "_alerts_loaded", False)
    monkeypatch.setattr(server, "_current_alert_session", "watch_delete_me")
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    monkeypatch.setattr(server, "operators", OperatorDirectory(
        email="employee@example.com", password_hash=hash_password("correct-horse")))
    server.console_sessions.reset()

    server.raise_alert("probing", "critical", "saved", "delete test")
    archived = client.post(
        "/api/console/clear-alerts",
        headers={"X-Parchi-Console-Token": "tok"}).json()["archived_session_id"]

    assert client.delete(f"/api/console/watch-history/{archived}").status_code == 401
    assert client.delete(
        f"/api/console/watch-history/{archived}",
        headers={"X-Parchi-Console-Token": "tok"}).status_code == 403

    login = client.post("/api/console/login", json={
        "email": "employee@example.com", "password": "correct-horse"}).json()
    employee = {"X-Parchi-Console-Session": login["session"]}
    current = client.get("/api/console/watch-history", headers=employee).json()[
        "current_session_id"]
    assert client.delete(
        f"/api/console/watch-history/{current}", headers=employee).status_code == 409

    deleted = client.delete(
        f"/api/console/watch-history/{archived}", headers=employee)
    assert deleted.status_code == 200
    assert deleted.json()["deleted_by"] == "employee@example.com"
    assert client.get(
        f"/api/console/watch-history/{archived}", headers=employee).status_code == 404


def test_ack_requires_the_console_and_ignores_unknown_ids(monkeypatch):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    assert client.post("/api/console/ack", json={"ids": ["alt_nope"]}).status_code == 401
    r = client.post("/api/console/ack",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"ids": ["alt_nope"]})
    assert r.status_code == 200 and r.json()["acked"] == []


def test_reset_clears_the_alert_file_so_a_demo_starts_clean(tmp_path, monkeypatch):
    alerts_path = tmp_path / "alerts.jsonl"
    alerts_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(server, "ALERTS_PATH", str(alerts_path))
    client.post("/api/authorize", json={"scenario": "over_cap"})
    assert alerts_path.exists() and alerts_path.read_text(encoding="utf-8").strip()
    client.post("/api/reset")
    assert not alerts_path.exists()


def test_the_console_is_off_rather_than_open_when_unconfigured(monkeypatch):
    """An internal fraud console that ships world-readable by default is worse
    than no console: it hands an attacker the map of which of their attempts
    were noticed."""
    # Either credential enables the console, so "off" has to mean neither.
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "")
    monkeypatch.setattr(server, "operators", OperatorDirectory())
    r = client.get("/api/console/feed")
    assert r.status_code == 503
    assert "not enabled" in r.json()["detail"]


@pytest.mark.parametrize("supplied", [
    None,               # no header at all
    "",                 # empty
    "wrong",            # wrong
    "s3cret-ops-toke",  # one character short
    "s3cret-ops-tokenx",  # one character long
    "S3CRET-OPS-TOKEN",  # right characters, wrong case
])
def test_only_the_exact_console_token_is_accepted(monkeypatch, supplied):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "s3cret-ops-token")
    headers = {} if supplied is None else {"X-Parchi-Console-Token": supplied}
    assert client.get("/api/console/ping", headers=headers).status_code == 401


def test_the_correct_token_gets_in(monkeypatch):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "s3cret-ops-token")
    r = client.get("/api/console/ping",
                   headers={"X-Parchi-Console-Token": "s3cret-ops-token"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_the_console_page_loads_without_a_token_but_carries_no_data():
    """The shell is public so the token never has to travel in the URL, where it
    would land in history, referrer headers and every proxy log in between."""
    r = client.get("/console")
    assert r.status_code == 200
    body = r.text
    assert "parchi" in body.lower() and "operations" in body.lower()
    assert "sign in" in body.lower()          # it is a login, not a landing page
    # No alert *content* baked into the page. "ledger_tampered" appears there as
    # a styling constant, so asserting on the kind name would fail for the wrong
    # reason. An alert id is data, and can only appear if data leaked in.
    assert "alt_" not in body
    assert "Audit log has been altered" not in body
    assert "X-Parchi-Console-Session" in body    # it fetches with the header


def test_the_feed_summarises_what_is_being_attempted(monkeypatch):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    for scenario in ("payee_substitution", "over_cap", "expired"):
        client.post("/api/authorize", json={"scenario": scenario})
    d = client.get("/api/console/feed",
                   headers={"X-Parchi-Console-Token": "tok"}).json()

    assert d["counts"]["total"] >= 3
    assert d["counts"]["by_severity"]["critical"] >= 1   # payee substitution
    assert d["counts"]["by_severity"]["info"] >= 1       # the expired slip
    assert "payee_substitution" in d["counts"]["by_kind"]
    assert d["ledger"]["intact"] is True
    assert d["intent_provider"]


def test_opening_the_console_verifies_the_audit_log(monkeypatch):
    """Reading the feed is itself a check, so a tampered log is found by whoever
    opens the console rather than by whoever clicks a button in the demo."""
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    client.post("/api/authorize", json={"scenario": "allow"})
    client.post("/api/authorize", json={"scenario": "over_cap"})
    client.post("/api/tamper")

    d = client.get("/api/console/feed",
                   headers={"X-Parchi-Console-Token": "tok"}).json()
    assert d["ledger"]["intact"] is False
    assert any(a["kind"] == "ledger_tampered" for a in d["alerts"])


def test_signing_in_with_the_configured_account_opens_the_feed(monkeypatch):
    monkeypatch.setattr(server, "operators", OperatorDirectory(
        email="ops@example.com", password_hash=hash_password("correct-horse")))
    server.console_sessions.reset()

    r = client.post("/api/console/login",
                    json={"email": "ops@example.com", "password": "correct-horse"})
    assert r.status_code == 200
    session = r.json()["session"]

    feed = client.get("/api/console/feed",
                      headers={"X-Parchi-Console-Session": session})
    assert feed.status_code == 200
    assert feed.json()["operator"] == "ops@example.com"


def test_a_wrong_password_says_nothing_about_which_half_was_wrong(monkeypatch):
    """Different messages for "no such account" and "wrong password" is how an
    attacker learns which addresses exist."""
    monkeypatch.setattr(server, "operators", OperatorDirectory(
        email="ops@example.com", password_hash=hash_password("correct-horse")))
    bad_password = client.post("/api/console/login",
                               json={"email": "ops@example.com", "password": "no"})
    bad_email = client.post("/api/console/login",
                            json={"email": "nobody@example.com",
                                  "password": "correct-horse"})
    assert bad_password.status_code == bad_email.status_code == 401
    assert bad_password.json()["detail"] == bad_email.json()["detail"]


def test_signing_out_kills_the_session(monkeypatch):
    monkeypatch.setattr(server, "operators", OperatorDirectory(
        email="ops@example.com", password_hash=hash_password("correct-horse")))
    server.console_sessions.reset()
    session = client.post("/api/console/login",
                          json={"email": "ops@example.com",
                                "password": "correct-horse"}).json()["session"]
    client.post("/api/console/logout",
                headers={"X-Parchi-Console-Session": session})
    assert client.get("/api/console/feed",
                      headers={"X-Parchi-Console-Session": session}).status_code == 401


def test_repeated_failures_lock_the_account_and_raise_an_alert(monkeypatch):
    """Someone grinding the console password is worth waking a person for."""
    monkeypatch.setattr(server, "operators", OperatorDirectory(
        email="ops@example.com", password_hash=hash_password("correct-horse")))
    for _ in range(5):
        client.post("/api/console/login",
                    json={"email": "ops@example.com", "password": "wrong"})
    locked = client.post("/api/console/login",
                         json={"email": "ops@example.com", "password": "wrong"})
    assert locked.status_code == 429

    # Even the right password waits out the lock.
    still = client.post("/api/console/login",
                        json={"email": "ops@example.com", "password": "correct-horse"})
    assert still.status_code == 429
    assert any(a["kind"] == "console_lockout"
               for a in client.get("/api/alerts").json()["alerts"])


# --------------------------------------------------------------------------
# alert attribution: who was being mischievous
# --------------------------------------------------------------------------

def test_every_alert_names_the_account_it_is_about():
    """A history answers "who was doing this?", not just "what happened?"."""
    client.post("/api/authorize", json={"scenario": "over_cap"})
    alerts = client.get("/api/alerts").json()["alerts"]
    named = [a for a in alerts if a.get("actor")]
    assert named, "alerts carry no actor at all"
    assert all(a["actor"] == "usr_demo" for a in named), named
    assert all(a["actor_name"] == "usr_demo" for a in named), (
        "the guest payer has no account, so its id is the display name")


def test_a_signed_in_users_alerts_name_the_user(monkeypatch):
    """The mischief-maker is named by their email, not a ghost payer id."""
    session = client.post("/api/user/login", json={
        "email": server.DEMO_USER_EMAIL,
        "password": server.DEMO_USER_PASSWORD}).json()["session"]
    try:
        client.post("/api/authorize", json={"scenario": "over_cap"},
                    headers={"X-Parchi-User-Session": session})
        alerts = client.get("/api/alerts").json()["alerts"]
        mine = [a for a in alerts if a.get("actor", "").startswith("usr_")
                and a["actor"] != "usr_demo"]
        assert mine, "no alert carried the signed-in user as its actor"
        assert all(a["actor_name"] == server.DEMO_USER_EMAIL for a in mine), mine
    finally:
        server.cooldowns.reset()


def test_agent_actors_are_labelled_as_agents():
    client.post("/api/authorize", json={"scenario": "allow"})
    client.post("/api/authorize", json={"scenario": "replay"})
    alerts = client.get("/api/alerts").json()["alerts"]
    replayed = [a for a in alerts if a["kind"] == "replay_attack"]
    assert replayed and replayed[-1]["actor"] == "usr_demo"
    assert replayed[-1]["actor_name"] == "usr_demo"


def test_releasing_one_cooled_account_does_not_free_the_others(monkeypatch):
    """The button is drawn next to one account, so it must free one account.

    An untargeted release frees everyone from a control that looks per-account,
    which during an incident is the opposite of what the operator meant. This
    test exists because the first version of the endpoint did exactly that.
    """
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}
    server.cooldowns.reset()
    server.cooldowns.trigger("usr_one", "agent swarm detected")
    server.cooldowns.trigger("usr_two", "agent swarm detected")
    try:
        r = client.post("/api/console/release", headers=auth,
                        json={"account": "usr_one"})
        assert r.status_code == 200 and r.json()["released"] == ["usr_one"]

        assert not server.cooldowns.check("usr_one").active
        assert server.cooldowns.check("usr_two").active, (
            "releasing one account released another account's block")
    finally:
        server.cooldowns.reset()


def test_a_release_is_named_by_the_operator_who_did_it(monkeypatch):
    """Lifting a fraud block is the most consequential thing here. It is logged."""
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}
    server.cooldowns.reset()
    server.cooldowns.trigger("usr_three", "agent swarm detected")
    try:
        client.post("/api/console/release", headers=auth,
                    json={"account": "usr_three"})
        alerts = client.get("/api/alerts", params={"limit": 50}).json()["alerts"]
        rec = next(a for a in alerts if a["kind"] == "cooldown_released")
        assert rec["actor"] == "usr_three"
        assert "machine-token" in rec["detail"]
    finally:
        server.cooldowns.reset()


def test_a_release_with_no_account_is_refused_rather_than_meaning_all(monkeypatch):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}
    server.cooldowns.reset()
    server.cooldowns.trigger("usr_four", "agent swarm detected")
    try:
        r = client.post("/api/console/release", headers=auth, json={"account": ""})
        assert r.status_code == 400
        assert server.cooldowns.check("usr_four").active
    finally:
        server.cooldowns.reset()


def test_releasing_an_account_that_is_not_held_is_not_an_error(monkeypatch):
    """The feed can move between the render and the click."""
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    r = client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_never_held"})
    assert r.status_code == 200 and r.json()["released"] == []


def test_release_requires_the_console(monkeypatch):
    # Enable the console, so a refusal means "wrong credential" and not
    # "console off": the second is a 503 with its own test, and relying on a
    # local .env to tell them apart is what broke this in CI.
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    server.cooldowns.reset()
    server.cooldowns.trigger("usr_five", "agent swarm detected")
    try:
        assert client.post("/api/console/release",
                           json={"account": "usr_five"}).status_code == 401
        assert server.cooldowns.check("usr_five").active
    finally:
        server.cooldowns.reset()


def test_every_scenario_on_the_page_has_an_expected_verdict_in_ci():
    """A scenario CI does not check is a scenario nothing checks.

    The CI job asserts this too, but only after a six-minute queue. Failing
    here costs a fifth of a second, and this test was written because a new
    scenario did in fact reach the tree with no CI entry behind it.
    """
    import re
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
    covered = set(re.findall(r'"(\w+)":\s*"(?:ALLOW|BLOCK|STEP_UP)"',
                             workflow.read_text(encoding="utf-8")))
    missing = set(server.SCENARIOS) - covered
    assert not missing, f"scenarios with no expected verdict in CI: {sorted(missing)}"


# -------------------------------------------------------------------------- /
# the agent-mistake net: detect, propose, human approves
# -------------------------------------------------------------------------- /

def test_an_agent_mistake_is_allowed_then_refund_proposed():
    """The nightmare case is not the cart a rule can refuse; it is this one.

    The purchase is judged rules-only, so it goes out ALLOW with no intent
    call in the payment path. The after-purchase review then says no, the
    alert fires, and the purchase sits in REFUND_PENDING with the proposal
    attached - awaiting the human, never auto-approved.
    """
    started = client.post("/api/authorize", json={"scenario": "agent_mistake"}).json()
    assert started["state"] == "ALLOW"
    assert started["initiated_by"] == "ai"

    review = started["intent_review"]
    assert review is not None and review["match"] is False
    assert "not something the human asked for" in review["reason"]

    txn_id = started["authorization_id"]
    record = server.authorizations[txn_id]
    assert record["state"] == "REFUND_PENDING"
    assert record["refund"]["proposed_by"] == "ai"
    assert record["refund"]["status"] == "PENDING"
    assert started["refund"]["display"] == "Rs 3,699.00"

    kinds = {a["kind"] for a in client.get(
        "/api/alerts", params={"limit": 40}).json()["alerts"]}
    assert "agent_intent_mistake" in kinds


def test_the_console_sees_the_pending_refund_and_the_operator_approves_it():
    """Proposal by AI, approval by human, both attributed.

    The feed must carry the proposal regardless of what the alert feed is
    doing, because a proposal attached to a purchase that already went out
    stays on the board until someone decides.
    """
    started = client.post("/api/authorize", json={"scenario": "agent_mistake"}).json()
    txn_id = started["authorization_id"]

    server.CONSOLE_TOKEN = "tok"
    try:
        feed = client.get("/api/console/feed",
                          headers={"X-Parchi-Console-Token": "tok"}).json()
        pending = [r for r in feed["refunds"] if r["txn_id"] == txn_id]
        assert pending and pending[0]["initiated_by"] == "ai"
        assert pending[0]["proposed_by"] == "ai"

        # Unauthenticated is refused, like every operator control.
        assert client.post("/api/console/refund-approve",
                           json={"txn_id": txn_id}).status_code == 401

        r = client.post("/api/console/refund-approve",
                        headers={"X-Parchi-Console-Token": "tok"},
                        json={"txn_id": txn_id})
        assert r.status_code == 200 and r.json()["state"] == "REFUNDED"
        assert server.authorizations[txn_id]["state"] == "REFUNDED"
        assert server.authorizations[txn_id]["refund"]["approved_by"] == "machine-token"
        assert server.authorizations[txn_id]["refund"]["status"] == "PROCESSING"

        kinds = {a["kind"] for a in client.get(
            "/api/alerts", params={"limit": 40}).json()["alerts"]}
        assert "refund_approved" in kinds

        # The board clears once decided.
        feed = client.get("/api/console/feed",
                          headers={"X-Parchi-Console-Token": "tok"}).json()
        assert all(r["txn_id"] != txn_id for r in feed["refunds"])
    finally:
        server.CONSOLE_TOKEN = ""


def test_a_refund_cannot_be_approved_twice_or_without_a_proposal():
    """The button only acts on a purchase actually awaiting a refund."""
    started = client.post("/api/authorize", json={"scenario": "allow"}).json()
    assert started["state"] == "ALLOW"
    txn_id = started["authorization_id"]

    server.CONSOLE_TOKEN = "tok"
    try:
        # Never proposed: the endpoint must refuse, not mint a refund.
        assert client.post("/api/console/refund-approve",
                           headers={"X-Parchi-Console-Token": "tok"},
                           json={"txn_id": txn_id}).status_code == 409
    finally:
        server.CONSOLE_TOKEN = ""

    proposed = client.post("/api/authorize", json={"scenario": "agent_mistake"}).json()
    ptxn = proposed["authorization_id"]
    server.CONSOLE_TOKEN = "tok"
    try:
        assert client.post("/api/console/refund-approve",
                           headers={"X-Parchi-Console-Token": "tok"},
                           json={"txn_id": ptxn}).status_code == 200
        # Already REFUNDED: a second approval is refused, not a double payout.
        assert client.post("/api/console/refund-approve",
                           headers={"X-Parchi-Console-Token": "tok"},
                           json={"txn_id": ptxn}).status_code == 409
    finally:
        server.CONSOLE_TOKEN = ""


def test_a_human_initiated_purchase_is_labelled_as_such():
    """The bifurcation the operator asked for: even a clean purchase carries
    who set it in motion, so the console and the page never have to guess.
    """
    body = client.post("/api/authorize", json={"scenario": "allow"}).json()
    assert body["initiated_by"] == "ai"  # the demo agent presents the cart

    mandate = new_mandate(
        "usr_demo", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
        "buy running shoes", issued_at=int(time.time()) - 60,
    )
    cart = Cart((CartLine("running shoes", "footwear", 420_000),), "upi", "mrc_bluleaf")
    payload = client.post("/api/authorizations", json={
        "mandate": mandate.to_dict(), "signature": sign(mandate, server.KEY),
        "cart": cart.to_dict(),
    }).json()
    assert payload["initiated_by"] == "human"


def test_a_degraded_review_proposes_nothing(monkeypatch):
    """The net fails open: a review that could not actually judge the cart
    must not mint a refund it cannot support, the same way an unavailable
    adjudicator blocks nobody.
    """
    from parchi.intent_match import IntentVerdict

    def degraded(*args, **kwargs):
        return IntentVerdict(
            match=False,
            reason="intent check unavailable (stubbed) - human confirmation required",
            degraded=True, provider="stub")

    monkeypatch.setattr(server, "intent_matches", degraded)
    body = client.post("/api/authorize", json={"scenario": "agent_mistake"}).json()
    assert body["state"] == "ALLOW"
    assert body["refund"] is None
    assert server.authorizations[body["authorization_id"]]["state"] == "ALLOW"
    # The degraded review is still shown, labelled as what it is.
    assert body["intent_review"]["degraded"] is True


# --------------------------------------------------------------------------
# privilege escalation and extreme autonomous defense
# --------------------------------------------------------------------------

def test_privilege_escalation_blocks_account_for_ten_minutes_without_ai(
        monkeypatch):
    calls = []
    monkeypatch.setattr(
        server, "assess_attack", lambda *args, **kwargs: calls.append(args))

    body = client.post(
        "/api/authorize", json={"scenario": "agent_substitution"}).json()

    assert body["decision"]["verdict"] == "BLOCK"
    held = server.cooldowns.check("usr_demo")
    assert held.active and 590 <= held.seconds_left <= 600
    assert calls == [], "default-off autonomous defense spent model tokens"

    alerts = client.get("/api/alerts", params={"limit": 40}).json()["alerts"]
    privilege = next(a for a in alerts if a["kind"] == "privilege_escalation")
    assert privilege["severity"] == "critical"
    assert privilege["actor"] == "usr_demo"
    assert "10 minutes" in privilege["detail"]


def test_cooldown_blocks_the_generic_authorization_api_too():
    client.post("/api/authorize", json={"scenario": "agent_substitution"})
    mandate = new_mandate(
        "usr_demo", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
        "buy running shoes", issued_at=int(time.time()) - 60,
    )
    cart = Cart(
        (CartLine("running shoes", "footwear", 420_000),),
        "upi", "mrc_bluleaf")
    response = client.post("/api/authorizations", json={
        "mandate": mandate.to_dict(),
        "signature": sign(mandate, server.KEY),
        "cart": cart.to_dict(),
    })
    assert response.status_code == 200
    assert response.json()["decision"]["checks"][-1]["name"] == "account_cooldown"


def test_autonomous_defense_is_guarded_and_only_runs_after_an_attack(
        monkeypatch):
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "tok")
    auth = {"X-Parchi-Console-Token": "tok"}
    called = threading.Event()

    def review(*args, **kwargs):
        called.set()
        return None

    monkeypatch.setattr(server, "assess_attack", review)

    assert client.post(
        "/api/console/autonomous-defense",
        json={"enabled": True}).status_code == 401
    enabled = client.post(
        "/api/console/autonomous-defense", headers=auth,
        json={"enabled": True})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True

    client.post("/api/authorize", json={"scenario": "over_cap"})
    assert not called.wait(0.05), "ordinary refusal triggered defensive AI"

    client.post("/api/authorize", json={"scenario": "agent_substitution"})
    assert called.wait(1), "proven privilege attack did not trigger defensive AI"

    feed = client.get("/api/console/feed", headers=auth).json()
    assert feed["autonomous_defense_enabled"] is True
    assert any(a["kind"] == "autonomous_defense_enabled"
               for a in feed["alerts"])


def test_health_reports_whether_the_console_was_configured():
    """A console that fails closed looks exactly like a deploy that lost its
    environment. Both answer 503 on every console route, and only one of them
    is a mistake. Health says which, so the difference can be seen from
    outside the host without reading its logs.
    """
    original = server.operators.email, server.operators.password_hash
    try:
        server.operators.email = ""
        server.operators.password_hash = ""
        assert client.get("/api/health").json()["console"] is False

        server.operators.email = "ops@example.test"
        server.operators.password_hash = "scrypt$1$1$1$AA==$AA=="
        assert client.get("/api/health").json()["console"] is True
    finally:
        server.operators.email, server.operators.password_hash = original


def test_health_reports_whether_step_up_approval_was_configured():
    original = server.HUMAN_APPROVAL_SECRET
    try:
        server.HUMAN_APPROVAL_SECRET = ""
        assert client.get("/api/health").json()["human_approval"] is False

        server.HUMAN_APPROVAL_SECRET = "123456"
        assert client.get("/api/health").json()["human_approval"] is True
    finally:
        server.HUMAN_APPROVAL_SECRET = original


def test_demo_console_flag_opens_a_published_sign_in(monkeypatch):
    """PARCHI_DEMO_CONSOLE fills an empty operator slot rather than weakening
    a configured one, and health publishes the credentials only in that mode.
    """
    original = (server.DEMO_CONSOLE, server.operators.email,
                server.operators.password_hash, server.HUMAN_APPROVAL_SECRET)
    try:
        server.DEMO_CONSOLE = True
        server.operators.email = server.DEMO_CONSOLE_EMAIL
        server.operators.password_hash = server.hash_password(
            server.DEMO_CONSOLE_PASSWORD)
        server.HUMAN_APPROVAL_SECRET = server.DEMO_APPROVAL_SECRET

        health = client.get("/api/health").json()
        assert health["console_mode"] == "demo"
        assert health["demo_credentials"]["password"] == \
            server.DEMO_CONSOLE_PASSWORD

        signed_in = client.post("/api/console/login", json={
            "email": server.DEMO_CONSOLE_EMAIL,
            "password": server.DEMO_CONSOLE_PASSWORD})
        assert signed_in.status_code == 200
        assert signed_in.json()["session"]
    finally:
        (server.DEMO_CONSOLE, server.operators.email,
         server.operators.password_hash,
         server.HUMAN_APPROVAL_SECRET) = original


def test_a_configured_operator_is_never_replaced_by_the_demo_account():
    """The demo flag must not be a way to sign into a real deployment."""
    original = (server.DEMO_CONSOLE, server.operators.email,
                server.operators.password_hash)
    try:
        server.DEMO_CONSOLE = True
        server.operators.email = "real@merchant.test"
        server.operators.password_hash = server.hash_password("a real one")

        assert client.get("/api/health").json()["console_mode"] == "configured"
        assert client.get("/api/health").json()["demo_credentials"] is None
        refused = client.post("/api/console/login", json={
            "email": server.DEMO_CONSOLE_EMAIL,
            "password": server.DEMO_CONSOLE_PASSWORD})
        assert refused.status_code == 401
    finally:
        (server.DEMO_CONSOLE, server.operators.email,
         server.operators.password_hash) = original


def test_credentials_are_never_published_when_the_flag_is_off():
    original = server.DEMO_CONSOLE
    try:
        server.DEMO_CONSOLE = False
        assert client.get("/api/health").json()["demo_credentials"] is None
    finally:
        server.DEMO_CONSOLE = original
