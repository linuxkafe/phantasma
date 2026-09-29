"""The navigation on `/` must be the same navigation as on `/admin`.

Reported on 2026-09-29: "the burger in /admin does not appear in /". It was not
a missing button, it was a button in the wrong place.

`skills/skill_ui.py` wrapped the output of `admin._build_nav_menu()` in its own
`<div class="nav-bar">` and its own `<nav id="nav-menu">`. The builder already
emits both. The result was two elements carrying `id="nav-menu"` (invalid HTML,
and `getElementById` returns whichever came first) and, worse, the real
`.nav-toggle` burger ended up nested INSIDE the menu copy that the under-900px
CSS hides. On a phone the menu was therefore unopenable: the button that opens
it was not on the screen.

These tests count real elements rather than substrings, after stripping HTML
comments -- a count that a code comment mentioning `id="nav-menu"` can inflate
is a test that will be "fixed" by deleting the comment.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers_ui_auth import make_app_with_user, root_page_html  # noqa: E402


@pytest.fixture
def ui_page(monkeypatch):
    """Render `/` as a signed-in admin and return the HTML.

    A real request through the test client, not a template string: the bug being
    guarded against was only visible in the assembled document (two elements, one
    id), and building the markup by hand in a test would have reproduced the
    single-element shape and passed.

    The session is established through the shared helper rather than by POSTing
    the login form. The first version of this fixture logged in per test and the
    file passed one test and failed the next six with a 302 to /login -- because
    the login endpoint is RATE LIMITED, correctly, to stop a password oracle. A
    test of page structure was being failed by an auth control it does not
    exercise and should not depend on. The login flow has its own tests.
    """
    _app, client = make_app_with_user("b@t.test", "admin", monkeypatch=monkeypatch)
    return root_page_html(client)


def test_there_is_exactly_one_nav_menu(ui_page):
    assert ui_page.count('id="nav-menu"') == 1, (
        "more than one element carries id=\"nav-menu\": the page is nesting the "
        "shared nav inside its own wrapper, which is what hid the burger"
    )


def test_there_is_exactly_one_nav_bar(ui_page):
    assert ui_page.count('class="nav-bar"') == 1, (
        "the page wraps the shared nav in a second .nav-bar"
    )


def test_the_burger_is_a_sibling_of_the_menu_not_a_child(ui_page):
    """The under-900px CSS hides `.nav-menu`. A toggle inside it is unreachable.

    This is the mechanism of the reported bug, asserted directly: the toggle
    must be closed before the menu opens, or the phone can never open it.

    The slice runs to the opening `<nav` tag itself, not to the `id` inside it --
    measuring to the attribute put the `<nav` opening in the window under test,
    and the assertion then failed on the very markup it was checking.
    """
    toggle = ui_page.find('class="nav-toggle"')
    assert toggle != -1, "no burger on / at all"
    nav_open = ui_page.find('<nav class="nav-menu"')
    assert nav_open != -1, "no menu on / at all"
    assert toggle < nav_open, (
        "the burger is rendered after the menu, i.e. inside or after the element "
        "the mobile CSS hides"
    )
    between = ui_page[toggle:nav_open]
    assert 'id="voice-btn"' in between, (
        "the voice button must sit in the bar, outside the collapsible menu, or "
        "it is unreachable on a phone"
    )


def test_the_menu_is_not_wrapped_in_the_ui_page_markup(ui_page):
    """The page must not ship its own nav-menu wrapper around the shared nav."""
    assert 'class="nav-menu nav-menu-always"' not in ui_page, (
        "the ui page still renders its own nav-menu wrapper; the shared builder "
        "already emits one and two of them is the original bug"
    )


def test_the_admin_links_are_present_for_an_admin(ui_page):
    hrefs = re.findall(r'<a[^>]+href="([^"]+)"[^>]*class="nav-link', ui_page)
    for expected in ("/admin/brain", "/admin/config", "/admin/users", "/perfil"):
        assert expected in hrefs, (
            f"{expected} missing from the menu on / for an admin; got {hrefs}"
        )


def test_the_voice_button_is_present(ui_page):
    assert 'id="voice-btn"' in ui_page, "no voice button on /"
    assert 'class="nav-voice"' in ui_page, "the voice button is not styled as a bar control"


def test_the_compact_view_toggle_is_gone(ui_page):
    """Removed on owner decision: the vertical phone layout already shows every
    device, and a persistent second layout mode was a way to make the page worse.
    The CSS went with it -- dead rules for a class nothing sets."""
    assert "dev-view-toggle" not in ui_page, "the compact-view button is still rendered"
    assert "dev-compact" not in ui_page, "the compact-view class is still applied"

