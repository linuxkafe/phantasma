"""A real login that could not be completed, reproduced and fixed.

Owner report, 2026-09-30: a user with `elsavamp@gmail.com` "tentou login mas o
código temporário para adicionar o dispositivo é sempre classificado como
expirado". The password was supplied and verified against the production hash
(`bcrypt.checkpw(b'infortunium', ...)` -> True), so this is not a wrong
credential.

## What the production evidence actually said

`journalctl`, same morning:

    09:23:32  new_device code issued for elsavamp@gmail.com
    09:25:20  new_device code issued for elsavamp@gmail.com
    09:27:09  new_device code issued for elsavamp@gmail.com
    09:29:48  new_device code issued for elsavamp@gmail.com
    14:18:35  new_device code issued for mail@linuxkafe.com
    14:18:51  device verified for mail@linuxkafe.com (role=admin)
    14:49:21  new_device code issued for elsavamp@gmail.com

Four codes in six minutes, and not one "device verified" -- while another
account verified on its first attempt. The `auth_codes` rows for that address
were all `consumed=1`, most with `failures=0`, i.e. **nobody ever submitted a
code successfully, and mostly nobody submitted one at all**.

The mailer was not at fault: postfix was active, nothing matched "SMTP ERROR",
and every send logged "Sent to ... via SMTP". The codes went out. They just
could not be used.

## The cause

Issuing a code marks every earlier one `consumed=1`, so exactly one code per
address is ever redeemable. That is the right security property. What was wrong
is everything around it:

* `consume_code` looked at `ORDER BY id DESC LIMIT 1` -- the NEWEST unconsumed
  code -- and compared it against what was typed. Ask twice, then read the
  FIRST email, and the code in hand was already dead. The user did nothing
  wrong except ask twice.
* Every failure returned one sentence: "O código não é válido ou expirou". A
  wrong digit, a superseded code, an expired code and a locked-out code were
  indistinguishable, so the page could not tell her what to do.
* The failure counter moved on the newest row rather than on the code actually
  typed, so five mistypes spread over three emails could exhaust the live one.
* A suppressed re-request (the 60s cooldown) returned a bare `None` and the
  route redirected to the code page as if a code had gone out. It had not.

Together these read, from the user's side, as "always expired". The TTL, the
clock and the claim were all fine -- which is why the obvious suspects, and two
measurements that pointed at them, were all wrong.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import auth_store  # noqa: E402

EMAIL = "elsavamp@gmail.com"
T0 = 1_000_000.0  # a fixed clock, so "later" means later


@pytest.fixture
def conn():
    c = sqlite3.connect(os.path.join(tempfile.mkdtemp(), "code.db"))
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
            is_active BOOLEAN DEFAULT TRUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO users (email, password_hash, role, is_active)
             VALUES ('elsavamp@gmail.com', 'x', 'user', 1);
        """
    )
    auth_store.ensure_schema(c)
    return c


def _issue(c, at, purpose="new_device"):
    return auth_store.request_code(c, EMAIL, purpose, "10.0.0.1", now=at)


# -------------------------------------------------------- the real failure ----

def test_a_superseded_code_is_dead_and_the_page_says_why(conn):
    """The exact case: she asked twice and then read the first email.

    A re-request DOES retire the earlier code -- that is a security property
    (a leaked code must not survive a re-issue) and it is not being relaxed
    here. What was broken is that nothing told her so: the rejection read
    "não é válido ou expirou", identically to a wrong digit or a dead code, and
    the production log for this address is four superseded codes in six minutes
    with no verification at all.

    So the behaviour that matters is the SECOND half: the state the page reads
    must be able to say "a newer code replaced yours".
    """
    first = _issue(conn, T0)
    second = _issue(conn, T0 + 120)
    assert first != second
    # The older code is refused -- correctly, it was superseded.
    assert not auth_store.consume_code(conn, EMAIL, first, "new_device", now=T0 + 200)
    # And the page is told there is a newer one, so she uses the right code.
    state = auth_store.live_code_state(conn, EMAIL, "new_device", now=T0 + 200)
    assert state["state"] == "live"
    assert state["replaced"] is True, (
        "the page cannot tell her that a newer code exists, so it shows the "
        "generic 'invalid or expired' and the account looks broken"
    )
    # Which works, obviously.
    assert auth_store.consume_code(conn, EMAIL, second, "new_device", now=T0 + 260)


def test_a_superseded_code_still_dies_when_its_window_closed(conn):
    """It was already dead at redemption; this is belt and braces."""
    first = _issue(conn, T0)
    _issue(conn, T0 + 120)
    too_late = T0 + auth_store.CODE_TTL_SECONDS + 10
    assert not auth_store.consume_code(conn, EMAIL, first, "new_device", now=too_late)


def test_the_newest_code_always_works(conn):
    _issue(conn, T0)
    newest = _issue(conn, T0 + 120)
    assert auth_store.consume_code(conn, EMAIL, newest, "new_device", now=T0 + 200)


def test_a_code_is_still_single_use(conn):
    """Redeeming the newest must not leave the door open behind it."""
    code = _issue(conn, T0)
    assert auth_store.consume_code(conn, EMAIL, code, "new_device", now=T0 + 10)
    assert not auth_store.consume_code(conn, EMAIL, code, "new_device", now=T0 + 20)


def test_redeeming_one_code_kills_the_siblings(conn):
    """One live code per address, still. The moment one is redeemed every other
    row for that address and purpose is consumed with it."""
    first = _issue(conn, T0)
    second = _issue(conn, T0 + 120)
    # Force both to be unconsumed so this is testing the redemption, not the
    # re-request rule (which already retired the first one).
    conn.execute("UPDATE auth_codes SET consumed = 0")
    conn.commit()
    assert auth_store.consume_code(conn, EMAIL, first, "new_device", now=T0 + 200)
    assert not auth_store.consume_code(
        conn, EMAIL, second, "new_device", now=T0 + 260
    ), "two codes for one address were both redeemable"


def test_a_code_does_not_work_for_another_purpose(conn):
    """A recovery code must not open a device check."""
    recovery = _issue(conn, T0, purpose="recover")
    assert not auth_store.consume_code(conn, EMAIL, recovery, "new_device")


def test_a_leaked_code_dies_on_a_re_request(conn):
    """The security property, asserted here too because the whole point of the
    change was to keep it. A code that leaked before a re-request must not be
    redeemable afterwards."""
    first = _issue(conn, T0)
    conn.execute("UPDATE auth_codes SET created_at = 0")  # past the cooldown
    conn.commit()
    _issue(conn, T0 + 120)
    assert not auth_store.consume_code(conn, EMAIL, first, "new_device", now=T0 + 200)


# ---------------------------------------------- the failure counter is fair ----

def test_one_wrong_guess_costs_exactly_one_attempt(conn):
    """Not one per row scanned.

    The previous implementation counted a miss against the newest row while
    scanning, so a wrong keystroke against a superseded code could burn several
    of the five attempts at once -- and the user never learns which code the
    counter is about.
    """
    _issue(conn, T0)
    _issue(conn, T0 + 120)
    _issue(conn, T0 + 240)
    auth_store.consume_code(conn, EMAIL, "000000", "new_device", now=T0 + 300)
    row = conn.execute(
        "SELECT failures FROM auth_codes WHERE email = ? AND consumed = 0"
        " ORDER BY id DESC LIMIT 1", (EMAIL,),
    ).fetchone()
    assert row["failures"] == 1, (
        f"one wrong guess cost {row['failures']} attempts: the counter is being "
        f"charged once per row scanned, so a single mistype can lock her out"
    )


def test_five_wrong_guesses_still_lock_the_code_out(conn):
    """The cap is not weakened by any of this."""
    code = _issue(conn, T0)
    for _ in range(auth_store.CODE_MAX_FAILURES):
        auth_store.consume_code(conn, EMAIL, "000000", "new_device", now=T0 + 10)
    assert not auth_store.consume_code(conn, EMAIL, code, "new_device", now=T0 + 20), (
        "five wrong guesses and the real code is refused: the attempt cap is gone"
    )


# ------------------------------------------------ telling the user the truth ----

def test_the_state_says_a_newer_code_exists(conn):
    _issue(conn, T0)
    _issue(conn, T0 + 120)
    state = auth_store.live_code_state(conn, EMAIL, "new_device", now=T0 + 200)
    assert state["state"] == "live"
    assert state["replaced"] is True, (
        "the page is being told there is only one code, so it cannot explain "
        "that the one she holds was superseded"
    )


def test_the_state_is_calm_when_there_is_only_one_code(conn):
    _issue(conn, T0)
    state = auth_store.live_code_state(conn, EMAIL, "new_device", now=T0 + 30)
    assert state["state"] == "live"
    assert state["replaced"] is False
    assert 0 < state["seconds_left"] <= auth_store.CODE_TTL_SECONDS
    assert state["attempts_left"] == auth_store.CODE_MAX_FAILURES


def test_the_state_reports_nothing_live_once_the_code_expires(conn):
    _issue(conn, T0)
    state = auth_store.live_code_state(
        conn, EMAIL, "new_device", now=T0 + auth_store.CODE_TTL_SECONDS + 5
    )
    assert state["state"] == "none", (
        "an expired code is still reported as live, so the page would tell her "
        "to try a code that cannot work"
    )


def test_the_attempts_left_counts_down(conn):
    code = _issue(conn, T0)
    for _ in range(2):
        auth_store.consume_code(conn, EMAIL, "000000", "new_device", now=T0 + 10)
    state = auth_store.live_code_state(conn, EMAIL, "new_device", now=T0 + 20)
    assert state["attempts_left"] == auth_store.CODE_MAX_FAILURES - 2
    assert auth_store.consume_code(conn, EMAIL, code, "new_device", now=T0 + 30)


# ------------------------------------------------------------- the cooldown ----

def test_the_cooldown_suppresses_but_does_not_invalidate(conn):
    """The 60s window, and what it must NOT do.

    A suppressed re-request used to return a bare `None`, and the login route
    redirected to the code page as though a code had gone out. None had. The
    code already issued stays usable throughout, which is the whole point of
    suppressing rather than re-issuing.
    """
    code = _issue(conn, T0)
    assert _issue(conn, T0 + 5) is None, "the cooldown did not suppress"
    assert auth_store.consume_code(conn, EMAIL, code, "new_device", now=T0 + 40), (
        "the code issued before the cooldown was killed by a suppressed request"
    )


def test_the_cooldown_expires_on_the_clock(conn):
    _issue(conn, T0)
    assert _issue(conn, T0 + auth_store.CODE_REQUEST_COOLDOWN - 1) is None
    assert _issue(conn, T0 + auth_store.CODE_REQUEST_COOLDOWN) is not None


def test_the_cooldown_is_per_address(conn):
    """One user hammering the button must not lock out another."""
    _issue(conn, T0)
    conn.execute(
        "INSERT INTO users (email, password_hash, role, is_active)"
        " VALUES ('outro@t.test', 'x', 'user', 1)"
    )
    conn.commit()
    assert auth_store.request_code(
        conn, "outro@t.test", "new_device", "10.0.0.2", now=T0 + 5
    ) is not None
