"""The console login: password storage, lockout, and sessions.

The console shows which attacks were noticed, which makes it the page an
attacker would most like to read. These tests are about the door.
"""

import pytest

from parchi.operators import (
    OperatorDirectory,
    SessionStore,
    hash_password,
    verify_password,
)

# --------------------------------------------------------------------------
# password storage
# --------------------------------------------------------------------------

def test_the_password_is_not_recoverable_from_what_is_stored():
    encoded = hash_password("Tr0ub4dor-and-3")
    assert "Tr0ub4dor-and-3" not in encoded
    assert encoded.startswith("scrypt$")
    assert verify_password("Tr0ub4dor-and-3", encoded)


def test_the_same_password_hashes_differently_every_time():
    """A shared salt turns one cracked password into every cracked password,
    and makes identical passwords visible as identical hashes."""
    a, b = hash_password("same-password"), hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a)
    assert verify_password("same-password", b)


@pytest.mark.parametrize("wrong", [
    "Tr0ub4dor-and-4",      # one digit out
    "tr0ub4dor-and-3",      # wrong case
    "Tr0ub4dor-and",       # truncated
    "Tr0ub4dor-and-31",     # extended
    "",                 # empty
    " Tr0ub4dor-and-3",     # leading space, which is a different password
])
def test_near_misses_are_still_misses(wrong):
    encoded = hash_password("Tr0ub4dor-and-3")
    assert not verify_password(wrong, encoded)


@pytest.mark.parametrize("broken", [
    "", "not-a-hash", "scrypt$bad", "bcrypt$1$2$3$4$5",
    "scrypt$notanumber$8$1$c2FsdA==$a2V5",
])
def test_a_malformed_stored_hash_reads_as_wrong_password(broken):
    """Configuration can be wrong. It must fail shut and quiet, not raise a
    traceback that tells the caller which part of the string it choked on."""
    assert verify_password("anything", broken) is False


# --------------------------------------------------------------------------
# the account
# --------------------------------------------------------------------------

def directory(**over):
    kw = dict(email="ops@example.com", password_hash=hash_password("correct-horse"))
    kw.update(over)
    return OperatorDirectory(**kw)


def test_only_the_configured_account_gets_in():
    d = directory()
    assert d.authenticate("ops@example.com", "correct-horse")
    assert not d.authenticate("someone@example.com", "correct-horse")
    assert not d.authenticate("ops@example.com", "wrong")


def test_the_email_is_matched_case_and_space_insensitively():
    """A person typing their own address with a capital is not an attacker."""
    d = directory()
    assert d.authenticate("  OPS@Example.com ", "correct-horse")


def test_an_unconfigured_directory_lets_nobody_in():
    """Empty configuration must not mean empty password."""
    d = OperatorDirectory()
    assert not d.configured
    assert not d.authenticate("", "")
    assert not d.authenticate("ops@example.com", "correct-horse")


# --------------------------------------------------------------------------
# lockout
# --------------------------------------------------------------------------

def test_five_wrong_tries_lock_the_account():
    d = directory(max_failures=5, lockout_seconds=300)
    for i in range(5):
        assert not d.authenticate("ops@example.com", "wrong", now=1000 + i)
    assert d.locked_out("ops@example.com", now=1005) > 0
    # And the correct password does not open it while it is locked.
    assert not d.authenticate("ops@example.com", "correct-horse", now=1006)


def test_the_lock_expires():
    d = directory(max_failures=3, lockout_seconds=60)
    for i in range(3):
        d.authenticate("ops@example.com", "wrong", now=1000 + i)
    assert d.locked_out("ops@example.com", now=1010) > 0
    assert d.locked_out("ops@example.com", now=1100) == 0
    assert d.authenticate("ops@example.com", "correct-horse", now=1100)


def test_a_successful_sign_in_clears_the_failures():
    d = directory(max_failures=3)
    d.authenticate("ops@example.com", "wrong", now=1000)
    d.authenticate("ops@example.com", "wrong", now=1001)
    assert d.authenticate("ops@example.com", "correct-horse", now=1002)
    # Two earlier failures must not combine with two later ones to lock a
    # legitimate operator out mid-shift.
    d.authenticate("ops@example.com", "wrong", now=1003)
    d.authenticate("ops@example.com", "wrong", now=1004)
    assert d.locked_out("ops@example.com", now=1005) == 0


def test_the_lockout_counts_the_account_not_the_connection():
    """An attacker chooses their connection. They do not choose which account
    they want, so counting per account is the count that constrains them."""
    d = directory(max_failures=3)
    for i in range(3):
        d.authenticate("ops@example.com", "wrong", now=1000 + i)
    assert d.locked_out("ops@example.com", now=1005) > 0
    assert d.locked_out("other@example.com", now=1005) == 0


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

def test_a_session_identifies_its_operator_until_it_expires():
    s = SessionStore(ttl_seconds=100)
    token = s.create("ops@example.com", now=1000)
    assert s.email_for(token, now=1050) == "ops@example.com"
    assert s.email_for(token, now=1101) is None


def test_signing_out_invalidates_the_session_immediately():
    s = SessionStore()
    token = s.create("ops@example.com")
    assert s.email_for(token)
    s.destroy(token)
    assert s.email_for(token) is None


def test_session_tokens_are_unguessable_and_unique():
    s = SessionStore()
    tokens = {s.create("ops@example.com") for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)


def test_an_unknown_or_empty_token_is_nobody():
    s = SessionStore()
    assert s.email_for("") is None
    assert s.email_for("made-up") is None
