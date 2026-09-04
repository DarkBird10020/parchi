"""The cooldown gate and the AI adjudicator above it.

Properties under test:
- a cooled account is refused before any engine work, payer-wide;
- release is the operator's, and works;
- the AI verdict gates the ratchet, and an unavailable AI fails open;
- the swarm threshold needs distinct registered agents on one payer.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from demo import server
from parchi.ai_guard import AttackAssessment, assess_attack
from parchi.cooldown import SWARM_AGENT_THRESHOLD, CooldownStore, detect_swarm
from parchi.mandate import Cart

client = TestClient(server.app)


def setup_function():
    server.engine.provider = "heuristic"
    client.post("/api/reset")


# -------------------------------------------------------------------------- /
# the store
# -------------------------------------------------------------------------- /

def test_a_triggered_cooldown_blocks_then_expires():
    store = CooldownStore(cooldown_seconds=600)
    t = 1000.0
    assert not store.check("usr_demo", now=t).active
    store.trigger("usr_demo", "test reason", now=t)
    held = store.check("usr_demo", now=t + 1)
    assert held.active and held.reason == "test reason" and held.seconds_left == 599
    assert not store.check("usr_other", now=t + 1).active  # payer-scoped
    # After the window passes, the account is free again.
    assert not store.check("usr_demo", now=t + 601).active


def test_release_removes_the_block():
    store = CooldownStore(cooldown_seconds=600)
    store.trigger("usr_demo", "reason")
    assert store.release("usr_demo") is True
    assert not store.check("usr_demo").active
    assert store.release("usr_demo") is False  # nothing left to release


def test_held_lists_only_live_entries():
    store = CooldownStore(cooldown_seconds=600)
    real_now = 50_000.0
    store.trigger("usr_a", "r", now=real_now)
    store.trigger("usr_b", "r", now=real_now - 10_000.0)  # already expired
    held = store.held(now=real_now)
    assert list(held) == ["usr_a"]


# -------------------------------------------------------------------------- /
# swarm detection
# -------------------------------------------------------------------------- /

def test_swarm_needs_distinct_agents_on_one_payer():
    seen: dict[str, set[str]] = {}
    for i in range(SWARM_AGENT_THRESHOLD - 1):
        assert not detect_swarm("usr_demo", f"agt_{i}", seen)
    # The third distinct face crosses the line.
    assert detect_swarm("usr_demo", "agt_new", seen)
    # The same agent repeating is a return, not news.
    assert not detect_swarm("usr_demo", "agt_new", seen)
    # A fourth face on an already-swarming payer is news again: extend.
    assert detect_swarm("usr_demo", "agt_fourth", seen)


def test_swarm_is_tracked_per_payer():
    seen: dict[str, set[str]] = {}
    assert not detect_swarm("usr_a", "agt_1", seen)
    assert not detect_swarm("usr_b", "agt_1", seen)


# -------------------------------------------------------------------------- /
# the adjudicator's gate
# -------------------------------------------------------------------------- /

def test_assess_attack_returns_none_when_no_provider_is_configured(monkeypatch):
    """Fail open: no model must never mean 'block because I could not think'."""
    monkeypatch.delenv("PARCHI_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PARCHI_GUARD_MODEL", raising=False)
    # Force an unresolvable endpoint so any accidental network attempt fails.
    monkeypatch.setattr(server.openai_provider, "resolve_model", lambda *a: "x")
    result = assess_attack("usr_demo", {"bursts": 9}, timeout=1.0)
    assert result is None


def test_an_attack_assessment_carries_its_model_and_confidence():
    a = AttackAssessment(True, 0.87, "bot farm", "z-ai/glm-4.6")
    d = a.to_dict()
    assert d["attack"] is True and d["confidence"] == 0.87 and "glm" in d["model"]


# -------------------------------------------------------------------------- /
# the live gate, end to end
# -------------------------------------------------------------------------- /

def _block_account_directly(payer: str = "usr_demo"):
    server.cooldowns.trigger(payer, "agent swarm detected")


def test_a_cooled_account_is_refused_before_the_engine_runs():
    _block_account_directly()
    r = client.post("/api/authorize", json={"scenario": "allow"}).json()
    assert r["decision"]["verdict"] == "BLOCK"
    names = [c["name"] for c in r["decision"]["checks"]]
    assert "account_cooldown" in names
    assert r["cooldown"]["active"] is True
    # No purchase was recorded while held.
    assert client.get("/api/ledger").json()["chain"]["intact"] is True


def test_the_console_can_release_a_cooled_account():
    _block_account_directly()
    blocked = client.post("/api/authorize",
                          json={"scenario": "allow"}).json()
    assert blocked["decision"]["verdict"] == "BLOCK"

    # The console has to be ENABLED for "refused" to mean "wrong credential".
    # With nothing configured the answer is 503, console off, which is a
    # different property with its own test. CI configures neither, so a test
    # that leans on a local .env for this passes here and fails there.
    server.CONSOLE_TOKEN = "the-real-token"
    r = client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_demo"})
    assert r.status_code == 401  # wrong token

    server.CONSOLE_TOKEN = "tok"
    try:
        # The release names the account. A release that took no account would
        # free every held account from a button drawn beside one of them.
        r = client.post("/api/console/release",
                        headers={"X-Parchi-Console-Token": "tok"},
                        json={"account": "usr_demo"})
        assert r.status_code == 200 and r.json()["released"] == ["usr_demo"]
        allowed = client.post("/api/authorize", json={"scenario": "allow"}).json()
        assert allowed["decision"]["verdict"] == "ALLOW"
    finally:
        server.CONSOLE_TOKEN = ""


def test_the_feed_carries_the_cooldown_panel_data():
    _block_account_directly()
    server.CONSOLE_TOKEN = "tok"
    try:
        d = client.get("/api/console/feed",
                       headers={"X-Parchi-Console-Token": "tok"}).json()
        assert "usr_demo" in d["cooldowns"]
        assert d["cooldowns"]["usr_demo"]["reason"] == "agent swarm detected"
    finally:
        server.CONSOLE_TOKEN = ""
        client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_demo"})


def test_reset_clears_cooldowns_and_swarm_state():
    _block_account_directly()
    detect_swarm("usr_demo", "agt_1", server.swarm_seen)
    detect_swarm("usr_demo", "agt_2", server.swarm_seen)
    client.post("/api/reset")
    assert not server.cooldowns.check("usr_demo").active
    assert server.swarm_seen == {}


def test_swarm_scenario_routes_the_pattern_through_the_adjudicator(monkeypatch):
    """Three registered agents, one payer: the pattern must reach the AI.

    The adjudicator is stubbed so the mechanism, not a live model's opinion,
    is what is under test: the pattern is always named on its own, the model
    is asked, and a confident "attack" verdict pulls the cooldown.
    """
    seen = {}

    def fake_assess(actor, signals, timeout=None):
        seen.update(signals)
        return AttackAssessment(attack=True, confidence=0.9,
                                reason="stubbed verdict", model="stub")

    monkeypatch.setattr(server, "assess_attack", fake_assess)
    client.post("/api/authorize", json={"scenario": "swarm"})
    alerts = client.get("/api/alerts").json()["alerts"]
    kinds = {a["kind"] for a in alerts}
    # The pattern is named by the detectors regardless of the model's opinion.
    assert "agent_swarm" in kinds
    assert seen.get("swarm_agents_on_this_payer"), (
        "the adjudicator was never shown the swarm roster")
    assert "ai_attack" in kinds and "account_cooled" in kinds
    assert server.cooldowns.check("usr_demo").active


def test_a_benign_verdict_is_recorded_not_hidden(monkeypatch):
    monkeypatch.setattr(server, "assess_attack", lambda *a, **k: AttackAssessment(
        attack=False, confidence=0.8, reason="looks like a shared kiosk",
        model="stub"))
    client.post("/api/authorize", json={"scenario": "swarm"})
    kinds = {a["kind"] for a in client.get("/api/alerts").json()["alerts"]}
    assert "agent_swarm" in kinds and "ai_cleared" in kinds
    assert "ai_attack" not in kinds and "account_cooled" not in kinds
    assert not server.cooldowns.check("usr_demo").active


# -------------------------------------------------------------------------- /
# operator controls: the AI gate, the clear-all, and the token bill
# -------------------------------------------------------------------------- /

def test_the_ai_gate_can_be_turned_off_and_blocks_are_skipped():
    """Gate off: no adjudicator call, no cooldown - but the alerts still stand."""
    server.CONSOLE_TOKEN = "tok"
    server.ai_gate_enabled = True
    try:
        r = client.post("/api/console/ai-gate", json={"enabled": False},
                        headers={"X-Parchi-Console-Token": "tok"})
        assert r.status_code == 200 and r.json()["enabled"] is False

        client.post("/api/authorize", json={"scenario": "swarm"})
        kinds = {a["kind"] for a in client.get("/api/alerts").json()["alerts"]}
        assert "ai_attack" not in kinds and "ai_cleared" not in kinds, (
            "the adjudicator ran with the gate off - tokens spent")
        assert "account_cooled" not in kinds
    finally:
        server.ai_gate_enabled = True
        server.CONSOLE_TOKEN = ""


def test_the_feed_reports_the_gate_state():
    server.CONSOLE_TOKEN = "tok"
    try:
        d = client.get("/api/console/feed",
                       headers={"X-Parchi-Console-Token": "tok"}).json()
        assert d["ai_gate_enabled"] is True
    finally:
        server.CONSOLE_TOKEN = ""


def test_clear_alerts_archives_feed_and_keeps_the_chain():
    server.CONSOLE_TOKEN = "tok"
    try:
        client.post("/api/authorize", json={"scenario": "over_cap"})
        before = client.get("/api/console/feed",
                            headers={"X-Parchi-Console-Token": "tok"}).json()
        assert before["counts"]["total"] > 0

        r = client.post("/api/console/clear-alerts",
                        headers={"X-Parchi-Console-Token": "tok"})
        assert r.status_code == 200 and r.json()["ok"] is True
        archived_session = r.json()["archived_session_id"]

        after = client.get("/api/console/feed",
                           headers={"X-Parchi-Console-Token": "tok"}).json()
        # The feed is empty except for the entry recording the clear itself.
        assert all(a["kind"] == "alerts_cleared" for a in after["alerts"])
        # The ledger is not housekeeping: its records must survive the clear.
        assert after["ledger"]["records"] == before["ledger"]["records"]
        assert after["ledger"]["intact"] is True
        # Clear starts a new live session without deleting the previous one.
        history = client.get(
            "/api/console/watch-history",
            headers={"X-Parchi-Console-Token": "tok"}).json()
        assert history["sessions"][0]["id"] == archived_session
        archived = client.get(
            f"/api/console/watch-history/{archived_session}",
            headers={"X-Parchi-Console-Token": "tok"}).json()
        assert any(a["kind"] == "cap_breach" for a in archived["alerts"])
        assert os.path.getsize(server.ALERTS_PATH) > 0
    finally:
        server.CONSOLE_TOKEN = ""
        client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_demo"})


def test_operator_controls_require_the_console():
    """Enabled but unauthenticated is 401. Not enabled at all is 503, which is
    a different property, tested elsewhere."""
    server.CONSOLE_TOKEN = "tok"
    try:
        assert client.post("/api/console/ai-gate",
                           json={"enabled": False}).status_code == 401
        assert client.post("/api/console/clear-alerts").status_code == 401
        assert client.get("/api/console/watch-history").status_code == 401
    finally:
        server.CONSOLE_TOKEN = ""


def test_one_swarm_incident_is_reviewed_once_not_once_per_attempt(monkeypatch):
    """A swarm arrives as several attempts in the same breath.

    The cooldown check alone cannot stop the second review, because the
    cooldown does not exist until the model has answered. Three attempts
    dispatched three reviews: three model calls, three identical alerts, one
    incident. Found by looking at the console feed in a browser.
    """
    calls = []

    def counting_assess(actor, signals, timeout=30.0, model=None):
        calls.append(actor)
        time.sleep(0.25)          # a real call is not instant; that is the race
        return AttackAssessment(True, 0.95, "credential farm", "test-model")

    monkeypatch.setattr(server, "assess_attack", counting_assess)
    server.cooldowns.reset()
    server.swarm_seen.clear()
    server.adjudicating.clear()
    client.post("/api/reset")
    try:
        for _ in range(3):
            client.post("/api/authorize", json={"scenario": "swarm"})
        # Let whichever threads were dispatched finish.
        for _ in range(40):
            if not server.adjudicating:
                break
            time.sleep(0.1)

        assert len(calls) == 1, (
            f"one swarm incident asked the model {len(calls)} times")
        alerts = client.get("/api/alerts", params={"limit": 60}).json()["alerts"]
        cooled = [a for a in alerts if a["kind"] == "account_cooled"]
        assert len(cooled) == 1, f"{len(cooled)} cooldown alerts for one incident"
    finally:
        server.cooldowns.reset()
        server.swarm_seen.clear()
        server.adjudicating.clear()


def test_the_claim_is_given_back_even_when_the_adjudicator_raises(monkeypatch):
    """A claim never released is an account that can never be reviewed again.

    And nothing escapes: this runs on its own thread, where an exception would
    surface as an unattributed traceback in the server log and block nobody.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("endpoint on fire")

    monkeypatch.setattr(server, "assess_attack", boom)
    server.adjudicating.clear()
    server.adjudicate("agt_1", "usr_stuck", {"x": 1}, "txn_1", "a reason")
    assert "usr_stuck" not in server.adjudicating
    assert not server.cooldowns.check("usr_stuck").active, (
        "a failed adjudication must never cool an account")


def test_turning_the_gate_off_does_not_strand_the_claim(monkeypatch):
    """The off path never starts a thread, so it has to release the claim itself."""
    server.adjudicating.clear()
    server.cooldowns.reset()
    server.swarm_seen.clear()
    monkeypatch.setattr(server, "ai_gate_enabled", False)
    client.post("/api/reset")
    try:
        for _ in range(3):
            client.post("/api/authorize", json={"scenario": "swarm"})
        assert not server.adjudicating, (
            f"claims left behind with the gate off: {server.adjudicating}")
    finally:
        server.cooldowns.reset()
        server.swarm_seen.clear()


def test_a_coupon_claimed_at_two_values_cools_the_account_without_a_model(monkeypatch):
    """The operator's case: the claimed value was raised, so block the account.

    Deliberately with the provider off and the adjudicator stubbed to explode.
    This decision is arithmetic, so it must hold with no key, no network and no
    model, which is also what makes it hold in CI and on a fresh clone.
    """
    def never(*args, **kwargs):
        raise AssertionError("a coupon value claim is arithmetic, not a judgement")

    monkeypatch.setattr(server, "assess_attack", never)
    server.cooldowns.reset()
    client.post("/api/reset")
    try:
        first = client.post("/api/authorize", json={"scenario": "coupon_drift"}).json()
        assert first["decision"]["verdict"] == "BLOCK"

        held = server.cooldowns.check("usr_demo")
        assert held.active, "the account was not cooled after the value was raised"
        assert "different values" in held.reason

        kinds = {a["kind"] for a in client.get(
            "/api/alerts", params={"limit": 40}).json()["alerts"]}
        assert "coupon_abuse_confirmed" in kinds
        assert "account_cooled" in kinds
    finally:
        server.cooldowns.reset()


def test_a_hot_code_claimed_at_two_values_still_cools_the_account(monkeypatch):
    """The drift cannot be allowed to hide behind the hot alert.

    Found in the suppression guard in `check_patterns`: when the drift fired on
    the very attempt that also crossed the hot threshold, the guard dropped it
    as a duplicate, the gate never saw a shape, and the account stayed free.
    The watcher marks the drift raised on that attempt regardless, so no later
    attempt can raise it either - one missed breath and the block never lands.

    An attacker warms the rail with true-value claims and inflates on the
    attempt that crosses the hot line, which is exactly the breath that was
    being eaten. The watcher is warmed to one below the threshold so the
    scenario's inflated claim is that attempt.
    """
    def never(*args, **kwargs):
        raise AssertionError("a coupon value claim is arithmetic, not a judgement")

    monkeypatch.setattr(server, "assess_attack", never)
    server.cooldowns.reset()
    client.post("/api/reset")
    try:
        mandate, base_cart = server.build_case("allow")
        warm_cart = Cart(base_cart.lines, base_cart.method, base_cart.payee_id,
                         base_cart.merchant_note, agent_id=base_cart.agent_id,
                         discount_code="SAVE10", discount_paise=10_000)
        for _ in range(4):
            server.coupon_watch.observe(warm_cart, mandate)

        # The scenario's main attempt is the fifth event - the hot threshold
        # and the first drift in the same breath, the breath the bug ate. The
        # replay claim lands afterwards, on an account that must already read
        # as cooled.
        client.post("/api/authorize", json={"scenario": "coupon_drift"})

        held = server.cooldowns.check("usr_demo")
        assert held.active and "different values" in held.reason, (
            "the drift was suppressed as a duplicate of the hot alert and the "
            "account was never blocked")
    finally:
        server.cooldowns.reset()


def test_repeated_payee_substitution_blocks_the_account_for_ten_minutes(monkeypatch):
    """A payee substitution is refused on every attempt, but the refusals alone
    only stop the carts. An AI agent trying the same trick again and again is
    attempting the attack, not shopping: at the probe threshold the account
    itself is held, the way the coupon shapes hold it, so the NEXT attempt is
    the one that stops.

    The warning to the user is the cooldown state the page already polls, and
    the operator sees it on the release panel like every other held account.
    """
    def never(*args, **kwargs):
        raise AssertionError("repetition is arithmetic, not a judgement")

    monkeypatch.setattr(server, "assess_attack", never)
    server.cooldowns.reset()
    client.post("/api/reset")
    try:
        for i in range(5):
            r = client.post("/api/authorize",
                            json={"scenario": "payee_substitution"}).json()
            assert r["decision"]["verdict"] == "BLOCK"
            if i < 4:
                assert r["cooldown"]["active"] is False, (
                    "one attempt is refused, not held - a mistake must not "
                    "lock the account")

        # The fifth refusal crossed the probe threshold: the account is held,
        # before any sixth attempt has to be refused on its own merits.
        held = server.cooldowns.check("usr_demo")
        assert held.active, "five payee substitutions in a minute held nobody"
        assert "payee substitution" in held.reason

        after = client.post("/api/authorize",
                            json={"scenario": "allow"}).json()
        assert after["decision"]["verdict"] == "BLOCK"
        assert "cooldown" in after["decision"]["reason"]
        assert after["cooldown"]["active"] is True

        kinds = {a["kind"] for a in client.get(
            "/api/alerts", params={"limit": 40}).json()["alerts"]}
        assert "payee_substitution_blocked" in kinds
        assert "payee_substitution" in kinds   # the per-attempt refusals stand
    finally:
        server.cooldowns.reset()


def test_the_cooldown_then_stops_the_next_purchase(monkeypatch):
    """Refusing an attempt and stopping an attacker are different things.

    This spillover is the feature, not a bug: an account that was caught
    working the coupon rail does not get to buy something else a second later.
    CI resets between scenarios for exactly this reason.
    """
    monkeypatch.setattr(server, "assess_attack",
                        lambda *a, **k: pytest.fail("no model should be asked"))
    server.cooldowns.reset()
    client.post("/api/reset")
    try:
        client.post("/api/authorize", json={"scenario": "coupon_drift"})
        after = client.post("/api/authorize", json={"scenario": "allow"}).json()
        assert after["decision"]["verdict"] == "BLOCK"
        assert "cooldown" in after["decision"]["reason"]
    finally:
        server.cooldowns.reset()
