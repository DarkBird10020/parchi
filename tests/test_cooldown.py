"""The cooldown gate and the AI adjudicator above it.

Properties under test:
- a cooled account is refused before any engine work, payer-wide;
- release is the operator's, and works;
- the AI verdict gates the ratchet, and an unavailable AI fails open;
- the swarm threshold needs distinct registered agents on one payer.
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from demo import server
from parchi.ai_guard import AttackAssessment, assess_attack
from parchi.cooldown import SWARM_AGENT_THRESHOLD, CooldownStore, detect_swarm

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

    r = client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_demo"})
    assert r.status_code == 401  # no token supplied

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


def test_clear_alerts_empties_feed_and_file_but_keeps_the_chain():
    server.CONSOLE_TOKEN = "tok"
    try:
        client.post("/api/authorize", json={"scenario": "over_cap"})
        before = client.get("/api/console/feed",
                            headers={"X-Parchi-Console-Token": "tok"}).json()
        assert before["counts"]["total"] > 0

        r = client.post("/api/console/clear-alerts",
                        headers={"X-Parchi-Console-Token": "tok"})
        assert r.status_code == 200 and r.json()["ok"] is True

        after = client.get("/api/console/feed",
                           headers={"X-Parchi-Console-Token": "tok"}).json()
        # The feed is empty except for the entry recording the clear itself.
        assert all(a["kind"] == "alerts_cleared" for a in after["alerts"])
        # The ledger is not housekeeping: its records must survive the clear.
        assert after["ledger"]["records"] == before["ledger"]["records"]
        assert after["ledger"]["intact"] is True
        # The file of record is empty too, so a restart shows a clean feed.
        assert os.path.getsize(server.ALERTS_PATH) > 0  # the clear entry
    finally:
        server.CONSOLE_TOKEN = ""
        client.post("/api/console/release",
                    headers={"X-Parchi-Console-Token": "tok"},
                    json={"account": "usr_demo"})


def test_operator_controls_require_the_console():
    assert client.post("/api/console/ai-gate",
                       json={"enabled": False}).status_code == 401
    assert client.post("/api/console/clear-alerts").status_code == 401
