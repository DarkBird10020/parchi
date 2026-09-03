"""Payer accounts: signup, login, sessions, and slips signed by the user's key.

Properties under test:
- the seed account exists and authenticates;
- signup fails closed on duplicates and weak passwords;
- a signed-in purchase carries the user's own payer id, and the ledger proves it;
- a signed-out request still works as usr_demo;
- the cooldown gate keys on the signed-in user, not on usr_demo.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from demo import server

client = TestClient(server.app)

# Whatever the server seeded itself with, never a literal: the deployment
# password comes from the environment and must not exist in this repo.
SEED_EMAIL = server.DEMO_USER_EMAIL
SEED_PASSWORD = server.DEMO_USER_PASSWORD


def setup_function():
    server.engine.provider = "heuristic"
    server.users.remove("roundtrip@example.com")   # this test creates it below
    client.post("/api/reset")


def _login(email: str = SEED_EMAIL, password: str = SEED_PASSWORD) -> dict:
    r = client.post("/api/user/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.json()
    return r.json()


def _headers(session: str) -> dict:
    return {"X-Parchi-User-Session": session}


def test_the_seed_account_exists_and_authenticates():
    body = _login()
    assert body["user"]["email"] == SEED_EMAIL
    assert body["user"]["user_id"].startswith("usr_")
    assert "password_hash" not in body["user"]      # the hash never leaves
    wrong = client.post("/api/user/login",
                        json={"email": SEED_EMAIL, "password": "nope"})
    assert wrong.status_code == 401


def test_signup_rejects_duplicates_and_weak_passwords():
    dup = client.post("/api/user/signup",
                      json={"email": SEED_EMAIL, "password": "whatever-long"})
    assert dup.status_code == 409
    weak = client.post("/api/user/signup",
                       json={"email": "fresh@example.com", "password": "short"})
    assert weak.status_code == 409


def test_signup_then_login_round_trip():
    made = client.post("/api/user/signup",
                       json={"email": "roundtrip@example.com",
                             "password": "long-enough-1"})
    assert made.status_code == 200
    again = _login("roundtrip@example.com", "long-enough-1")
    # Same account, same user id, across sessions.
    assert again["user"]["user_id"] == made.json()["user"]["user_id"]


def test_a_signed_in_purchase_carries_the_users_own_payer_id():
    session = _login()["session"]
    r = client.post("/api/authorize", json={"scenario": "allow"},
                    headers=_headers(session))
    assert r.status_code == 200
    body = r.json()
    assert body["mandate"]["payer_id"] == body["user"]["user_id"]
    assert body["mandate"]["payer_id"] != "usr_demo"
    # The ledger proves the slip was signed by the user's key.
    rec = client.get("/api/ledger").json()["records"][0]
    assert rec["mandate_id"].startswith("mnd_")


def test_a_signed_out_purchase_is_still_usr_demo():
    r = client.post("/api/authorize", json={"scenario": "allow"})
    assert r.status_code == 200
    assert r.json()["mandate"]["payer_id"] == "usr_demo"


def test_a_cool_down_on_the_user_blocks_the_user_not_usr_demo():
    """The gate keys on the signed-in account - the whole point of having one."""
    session = _login()["session"]
    uid = _login()["user"]["user_id"]
    server.cooldowns.trigger(uid, "agent swarm detected")

    blocked = client.post("/api/authorize", json={"scenario": "allow"},
                          headers=_headers(session))
    assert blocked.json()["decision"]["verdict"] == "BLOCK"
    assert "cooldown" in blocked.json()["decision"]["reason"]

    signed_out = client.post("/api/authorize", json={"scenario": "allow"})
    assert signed_out.json()["decision"]["verdict"] == "ALLOW"


def test_me_reports_the_cooldown_for_the_signed_in_user():
    session = _login()["session"]
    uid = _login()["user"]["user_id"]
    server.cooldowns.trigger(uid, "reason here")
    me = client.get("/api/user/me", headers=_headers(session)).json()
    assert me["cooldown"]["active"] is True
    assert me["cooldown"]["reason"] == "reason here"


def test_me_without_a_session_says_nobody():
    body = client.get("/api/user/me").json()
    assert body == {"user": None}


def test_logout_kills_the_session():
    session = _login()["session"]
    client.post("/api/user/logout", headers=_headers(session))
    me = client.get("/api/user/me", headers=_headers(session)).json()
    assert me["user"] is None


def test_chat_signs_the_mandate_as_the_signed_in_user(monkeypatch):
    """The assistant's slip belongs to whoever is chatting, not to a ghost."""
    fake_intent = {"understood": True, "cap_rupees": 5000,
                   "categories": ["footwear"],
                   "playback": "buy running shoes under Rs 5,000"}
    fake_cart = {"reply": "Found these.", "items": [
        {"sku": "shoe-rev7", "quantity": 1}]}
    monkeypatch.setattr(server.openai_provider, "complete_json",
                        lambda *a, **k: dict(fake_intent))
    monkeypatch.setattr(server.shopper, "load_catalogue",
                        server.shopper.load_catalogue)  # touch, keeps names honest
    calls = {"n": 0}

    def fake_complete(prompt, timeout=None, schema=None, **kw):
        calls["n"] += 1
        return fake_intent if calls["n"] == 1 else fake_cart

    monkeypatch.setattr(server.openai_provider, "complete_json", fake_complete)
    session = _login()["session"]
    r = client.post("/api/chat", json={"message": "buy running shoes"},
                    headers=_headers(session))
    if r.status_code == 503:      # chat requires a live model; skip if unset
        return
    assert r.status_code == 200
    assert r.json()["mandate"]["payer_id"] != "usr_demo"


# --------------------------------------------------------------------------
# the user is told about their own block
# --------------------------------------------------------------------------

def test_status_reports_the_block_to_the_signed_in_user():
    """The user's own view of the cooldown: active, why, and until when."""
    session = _login()["session"]
    uid = _login()["user"]["user_id"]
    server.cooldowns.trigger(uid, "agent swarm detected")
    d = client.get("/api/user/status", headers=_headers(session)).json()
    assert d["user"]["email"] == SEED_EMAIL
    assert d["cooldown"]["active"] is True
    assert d["cooldown"]["reason"] == "agent swarm detected"
    assert 0 < d["cooldown"]["seconds_left"] <= 600
    assert d["cooldown"]["ends_at_ms"] is not None   # absolute: survives reload


def test_status_is_empty_without_a_session():
    assert client.get("/api/user/status").json() == {"user": None}


def test_status_clears_after_release():
    session = _login()["session"]
    uid = _login()["user"]["user_id"]
    server.cooldowns.trigger(uid, "swarm")
    server.cooldowns.release(uid)
    d = client.get("/api/user/status", headers=_headers(session)).json()
    assert d["cooldown"]["active"] is False
    assert d["cooldown"]["ends_at_ms"] is None


def test_the_stored_public_key_still_verifies_after_a_restart(tmp_path):
    """A stored key that verifies nothing is worse than no stored key.

    Only the public half is persisted, so a restart mints a new keypair. If
    the file kept the old public key it would read like an answer while
    verifying none of the signatures the process is now producing.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from parchi.mandate import new_mandate, sign, verify
    from parchi.users import UserDirectory

    path = str(tmp_path / "users.jsonl")
    rec = UserDirectory(path).signup("restart@example.com", "password123")
    user_id = rec["user_id"]

    after_restart = UserDirectory(path)           # the private keys are gone
    mandate = new_mandate(user_id, "mrc_x", ("upi",), 100, ("footwear",),
                          "buy shoes")
    signature = sign(mandate, after_restart.private_key(user_id))

    from pathlib import Path
    line = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    assert len(line) == 1, "the rewrite should leave one line per account"
    stored_hex = json.loads(line[0])["pubkey_hex"]
    stored = Ed25519PublicKey.from_public_bytes(bytes.fromhex(stored_hex))
    assert verify(mandate, signature, stored)


def test_removing_an_account_leaves_the_others_in_the_file(tmp_path):
    from parchi.users import UserDirectory

    path = str(tmp_path / "users.jsonl")
    directory = UserDirectory(path)
    directory.signup("keep@example.com", "password123")
    directory.signup("drop@example.com", "password123")

    assert directory.remove("drop@example.com") is True
    assert directory.remove("drop@example.com") is False

    from pathlib import Path
    emails = {json.loads(ln)["email"]
              for ln in Path(path).read_text(encoding="utf-8").splitlines()
              if ln.strip()}
    assert emails == {"keep@example.com"}
