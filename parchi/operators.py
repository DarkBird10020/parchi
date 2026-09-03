"""Who is allowed into the operations console.

A single operator account, configured by environment, with the password stored
as a scrypt hash rather than as itself. That is the smallest thing that is
honestly a login: the repository is public, and a password in source is a
password published.

What this is not
----------------
It is not an identity system. There is one account, so an alert is attributable
to "someone who had the password" and no further. A real deployment puts the
console behind the company IdP so the answer to "who acted on this" is a person,
and the console page says so on its face rather than leaving it to be discovered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

SCHEME = "scrypt"
# Interactive-login parameters. n=2**15 costs roughly 100ms and 32MB, which is
# unnoticeable once per session and expensive across a dictionary.
_N, _R, _P = 2 ** 15, 8, 1
_DKLEN = 32

# scrypt needs 128 * n * r bytes, which is exactly 32MB here, and OpenSSL's
# default ceiling is also 32MB. Leaving it implicit fails with "memory limit
# exceeded" on the first real password, which is a confusing way to find out.
_MAXMEM = 128 * _N * _R * 2


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Encode a password as `scrypt$n$r$p$salt$key`, all base64."""
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P,
                         dklen=_DKLEN, maxmem=_MAXMEM)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"{SCHEME}${_N}${_R}${_P}${b64(salt)}${b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against a stored hash.

    Every failure path returns False rather than raising: a malformed hash in
    configuration must read as "wrong password", not as a stack trace that tells
    the caller which part of the string it choked on.
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if scheme != SCHEME:
            return False
        expected = base64.b64decode(key_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=128 * int(n) * int(r) * 2,
        )
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


class OperatorDirectory:
    """One account, and a lockout so the password cannot be guessed at leisure.

    The lockout counts failures per email rather than per connection, because an
    attacker picks the connection and does not pick the account they want.
    """

    def __init__(self, email: str = "", password_hash: str = "",
                 max_failures: int = 5, lockout_seconds: int = 300) -> None:
        self.email = email.strip().lower()
        self.password_hash = password_hash.strip()
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}

    @classmethod
    def from_env(cls) -> OperatorDirectory:
        return cls(
            email=os.environ.get("PARCHI_CONSOLE_EMAIL", ""),
            password_hash=os.environ.get("PARCHI_CONSOLE_PASSWORD_HASH", ""),
        )

    @property
    def configured(self) -> bool:
        return bool(self.email and self.password_hash)

    def locked_out(self, email: str, now: float | None = None) -> int:
        """Seconds remaining on a lockout, or 0."""
        now = time.time() if now is None else now
        recent = [t for t in self._failures.get(email.strip().lower(), [])
                  if now - t < self.lockout_seconds]
        self._failures[email.strip().lower()] = recent
        if len(recent) < self.max_failures:
            return 0
        return int(self.lockout_seconds - (now - recent[0])) + 1

    def authenticate(self, email: str, password: str,
                     now: float | None = None) -> bool:
        now = time.time() if now is None else now
        email = (email or "").strip().lower()
        if not self.configured or self.locked_out(email, now):
            return False

        # Compare the email in constant time too. It is not a secret, but a fast
        # rejection on a wrong address turns "is this the right password" into
        # two separate, cheaper questions.
        email_ok = hmac.compare_digest(self.email, email)
        password_ok = verify_password(password or "", self.password_hash)
        if email_ok and password_ok:
            self._failures.pop(email, None)
            return True
        self._failures.setdefault(email, []).append(now)
        return False

    def reset(self) -> None:
        self._failures.clear()


class SessionStore:
    """Console sessions. In memory, and gone when the process restarts.

    A signed-out operator is the same as a restarted server here, which is the
    right trade for a console that holds no state worth resuming.
    """

    def __init__(self, ttl_seconds: int = 8 * 3600) -> None:
        self.ttl = ttl_seconds
        self._sessions: dict[str, tuple[str, float]] = {}

    def create(self, email: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (email, now + self.ttl)
        return token

    def email_for(self, token: str, now: float | None = None) -> str | None:
        now = time.time() if now is None else now
        entry = self._sessions.get(token or "")
        if entry is None:
            return None
        email, expires = entry
        if now >= expires:
            del self._sessions[token]
            return None
        return email

    def destroy(self, token: str) -> None:
        self._sessions.pop(token or "", None)

    def reset(self) -> None:
        self._sessions.clear()
