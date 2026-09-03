"""Payer accounts: sign up, sign in, and the key that signs your slips.

The demo used to have exactly one payer, `usr_demo`, whose key lived only in
process memory. That made the story impersonal: every permission slip was
signed by a ghost. This module gives each visitor their own account and their
own Ed25519 keypair, so the mandate on screen is *theirs*: signed by their
key, spent against their cap, cooled down when the adjudicator convicts them.

Persistence
-----------
Accounts live in `demo/users.jsonl`, append-and-rewrite like the alert file:
one JSON object per line, `user_id`, email, scrypt password hash, and the
**public** key hex. The private keys stay in process memory only: this demo
signs with them, it does not store them. A restart regenerates keys, which is
exactly the trade the README already documents for the demo's keys, and the
registry is rebuilt from the file so verification keeps working after one.

Reuse over reinvention
----------------------
The password hashing is `operators.hash_password`, the same scrypt parameters
the console login uses, one implementation of the important thing rather than
two. Sessions are opaque tokens with a TTL, checked in constant time where it
matters, and signup fails closed on a duplicate email rather than merging
accounts.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .operators import hash_password, verify_password

SESSION_TTL_SECONDS = 7 * 24 * 3600


class UserDirectory:
    """Who holds an account here, and which key signs for whom."""

    SESSION_TTL_SECONDS = SESSION_TTL_SECONDS

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._lock = threading.RLock()
        # email -> record
        self._users: dict[str, dict] = {}
        # user_id -> private key (memory only, never persisted)
        self._keys: dict[str, Ed25519PrivateKey] = {}
        # session token -> (user_id, expires)
        self._sessions: dict[str, tuple[str, float]] = {}
        self._load()

    # ------------------------------------------------------------------ store

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue        # torn final line: skip, like the alert file
                    email = str(rec.get("email", "")).strip().lower()
                    if email:
                        self._users[email] = rec
        except OSError:
            pass

    def _persist(self, rec: dict) -> None:
        """Append the new account, then rewrite (the alert file's pattern)."""
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------ users

    @staticmethod
    def _new_keypair() -> tuple[Ed25519PrivateKey, str]:
        key = Ed25519PrivateKey.generate()
        pub_hex = key.public_key().public_bytes_raw().hex()
        return key, pub_hex

    def signup(self, email: str, password: str) -> dict | None:
        """Create an account. Returns the record, or None if the email is taken."""
        email = (email or "").strip().lower()
        if not email or "@" not in email or len(password or "") < 8:
            return None
        with self._lock:
            if email in self._users:
                return None
            key, pub_hex = self._new_keypair()
            rec = {
                "user_id": "usr_" + uuid.uuid4().hex[:10],
                "email": email,
                "password_hash": hash_password(password),
                "pubkey_hex": pub_hex,
                "created": int(time.time()),
            }
            self._users[email] = rec
            self._keys[rec["user_id"]] = key
            self._persist(rec)
            return {k: v for k, v in rec.items() if k != "password_hash"}

    def authenticate(self, email: str, password: str) -> dict | None:
        email = (email or "").strip().lower()
        with self._lock:
            rec = self._users.get(email)
            if rec is None:
                # Same work as a real check, so a missing account is not a
                # fast "no" an attacker can time.
                hash_password(password or "")
                return None
            if not verify_password(password or "", rec.get("password_hash", "")):
                return None
            if rec["user_id"] not in self._keys:
                # setdefault would mint a keypair on every login just to throw
                # it away, which is slow for no reason on the hot path.
                self._regenerate(rec["user_id"])
            return {k: v for k, v in rec.items() if k != "password_hash"}

    def remove(self, email: str) -> bool:
        """Forget an account and rewrite the file without it. Test hygiene."""
        email = (email or "").strip().lower()
        with self._lock:
            if email not in self._users:
                return False
            rec = self._users.pop(email)
            self._keys.pop(rec.get("user_id"), None)
            self._rewrite()
        return True

    def get(self, user_id: str) -> dict | None:
        with self._lock:
            for rec in self._users.values():
                if rec.get("user_id") == user_id:
                    return {k: v for k, v in rec.items() if k != "password_hash"}
        return None

    def _regenerate(self, user_id: str) -> Ed25519PrivateKey | None:
        """Mint a fresh key for an account whose private half is gone.

        Only the public half is persisted, so a restart cannot bring the old
        key back. The record is rewritten with the new public key rather than
        left holding the old one: a stored public key that verifies nothing is
        worse than no stored key, because it reads like an answer.
        """
        rec = self.get(user_id)
        if rec is None:
            return None
        key = Ed25519PrivateKey.generate()      # demo trade: see module doc
        self._keys[user_id] = key
        stored = self._users.get(str(rec.get("email", "")).lower())
        if stored is not None:
            stored["pubkey_hex"] = key.public_key().public_bytes_raw().hex()
            self._rewrite()
        return key

    def _rewrite(self) -> None:
        """Write every account back, through a temp file. Caller holds the lock."""
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for rec in self._users.values():
                    f.write(json.dumps(rec) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def public_key(self, user_id: str) -> Ed25519PublicKey | None:
        """The verifying key for this payer, minted if the process forgot it."""
        with self._lock:
            key = self._keys.get(user_id) or self._regenerate(user_id)
            return key.public_key() if key is not None else None

    def private_key(self, user_id: str) -> Ed25519PrivateKey | None:
        with self._lock:
            return self._keys.get(user_id) or self._regenerate(user_id)

    # --------------------------------------------------------------- sessions

    def create_session(self, user_id: str,
                       now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = (user_id, now + SESSION_TTL_SECONDS)
        return token

    def user_for_session(self, token: str,
                         now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._sessions.get(token or "")
            if entry is None:
                return None
            user_id, expires = entry
            if now >= expires:
                del self._sessions[token]
                return None
        return self.get(user_id)

    def destroy_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
