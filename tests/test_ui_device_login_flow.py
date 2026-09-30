"""Authenticating and then entering the code has to work.

Reported on 2026-09-29: the address got "Sessão expirada" between the password
and the code, with most of the window unused. Two separate causes, and neither
of them was the one the message names.

1. The claim lived 600s while the CODE it authorises lived 900s
   (auth_store.CODE_TTL_SECONDS). The password step expired five minutes before
   the code could be redeemed, so a code that was still valid could no longer be
   used, and the user was looking at a countdown that had not run out.
2. The claim rode on a BROWSER-SESSION cookie. `session.permanent` is only set
   when a login completes, so between the two steps the cookie had no expiry and
   died whenever the browser reclaimed the tab -- which is what happens when you
   authenticate, switch to the mail app to read the code, and come back.

The test for the ordering is a derived test on purpose: two constants describe
one window, and the next person to change either should not have to know the
other exists.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import auth_store, ui_auth  # noqa: E402


@pytest.fixture
def app():
    """A request context is all this needs: the claim lives in the session and
    nothing here touches the store or the routes."""
    from flask import Flask

    application = Flask(__name__)
    application.secret_key = "k"
    application.config["TESTING"] = True
    return application


def test_the_claim_outlives_the_code_it_authorises():
    """The structural guard, so this cannot drift again.

    If the claim is shorter, the last few minutes of every code are unusable
    and the failure is reported as an expired session rather than as a design
    mistake.
    """
    assert ui_auth.PENDING_TTL_SECONDS >= auth_store.CODE_TTL_SECONDS, (
        f"the pending claim lives {ui_auth.PENDING_TTL_SECONDS}s but the code it "
        f"authorises lives {auth_store.CODE_TTL_SECONDS}s: a valid code cannot "
        f"be redeemed in its last "
        f"{auth_store.CODE_TTL_SECONDS - ui_auth.PENDING_TTL_SECONDS}s"
    )


def test_starting_a_pending_login_makes_the_cookie_persistent(app):
    """Otherwise the claim is gone the moment the user reads their email."""
    with app.test_request_context("/login"):
        ui_auth.start_pending_login("someone@example.invalid")
        from flask import session

        assert session.permanent is True, (
            "the pending claim rides on a browser-session cookie: switching to "
            "the mail app and back can lose it, and the user is told the session "
            "expired"
        )


def test_a_claim_survives_within_its_window(app):
    with app.test_request_context("/login"):
        ui_auth.start_pending_login("someone@example.invalid")
        claim = ui_auth.peek_pending()
        assert claim is not None
        assert claim["email"] == "someone@example.invalid"


def test_a_claim_expires_when_its_window_is_gone(app):
    """The window is still enforced, whatever the cookie's lifetime is.

    A persistent cookie holding a claim that never expires would be a backdoor:
    the cookie outliving the claim is the point, not a weakening of it.
    """
    import time as _time

    with app.test_request_context("/login"):
        ui_auth.start_pending_login(
            "someone@example.invalid", now=_time.time() - (ui_auth.PENDING_TTL_SECONDS + 5)
        )
        assert ui_auth.take_pending() is None, (
            "an expired claim was still redeemable"
        )


def test_a_claim_is_single_use(app):
    with app.test_request_context("/login"):
        ui_auth.start_pending_login("someone@example.invalid")
        assert ui_auth.take_pending() is not None
        assert ui_auth.take_pending() is None, "a claim can be redeemed twice"
