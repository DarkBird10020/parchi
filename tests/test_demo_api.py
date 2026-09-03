import hashlib
import hmac as hmac_mod
import json
import time

import pytest
from fastapi.testclient import TestClient

from demo import server
from parchi.mandate import Cart, CartLine, new_mandate, sign
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

def test_the_console_is_off_rather_than_open_when_unconfigured(monkeypatch):
    """An internal fraud console that ships world-readable by default is worse
    than no console: it hands an attacker the map of which of their attempts
    were noticed."""
    monkeypatch.setattr(server, "CONSOLE_TOKEN", "")
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
    assert "operations console" in body.lower()
    # No alert *content* baked into the page. "ledger_tampered" appears there as
    # a styling constant, so asserting on the kind name would fail for the wrong
    # reason. An alert id is data, and can only appear if data leaked in.
    assert "alt_" not in body
    assert "Audit log has been altered" not in body
    assert "X-Parchi-Console-Token" in body      # it fetches with the header


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
