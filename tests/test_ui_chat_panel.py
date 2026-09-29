
# The layout itself is measured, not grepped. `test_mobile_layout_browser.py`
# loads this page in a real headless browser at 375x667 and measures the tiles,
# the opener, the panel and the drag. These tests kept here are the ones a
# string can answer honestly: does the control exist, is it labelled, does the
# panel start closed. A CSS string being present proves nothing about a
# rectangle -- the release that hid the device tiles passed every string
# assertion in this file while the strip measured 45px of a 667px screen.

"""On a phone the device tiles own the screen and the chat is a panel.

Owner decision, 2026-09-29: "the interface should give priority to the device
icons; the chat should only overlay when you click on it".

What that changes structurally, and why it is worth a test at all. The tiles
used to share the viewport with the conversation, which forced the strip to be
capped -- 22vh on a phone -- so a home with fourteen devices showed three and
the rest were behind a scroll inside a 20vh band that read as a rendering bug.
Giving the tiles the height and taking the chat out of the flow removes the cap
and the nested scrollbar together.

The rules these tests pin, because each one was a way to get this wrong:

* the panel is CLOSED by default, and the only opener is the tab;
* the opener stays reachable while the panel is open, or there is no way out;
* a reply -- spoken, typed or from a tile -- opens the panel, because a reply
  delivered into a closed panel is a reply nobody reads;
* the desktop layout is untouched: the overlay is inside a max-width:768px media
  query, and the chat is an ordinary column above it.
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
    _app, client = make_app_with_user("b@t.test", "admin", monkeypatch=monkeypatch)
    return root_page_html(client)


def test_the_chat_tab_exists(ui_page):
    """A panel with no opener is not a panel."""
    assert 'id="chat-tab"' in ui_page


def test_the_tab_says_what_it_does(ui_page):
    assert 'aria-controls="main"' in ui_page, "the tab does not name what it controls"
    assert 'aria-expanded="false"' in ui_page, (
        "the tab starts expanded, which contradicts a panel that starts closed"
    )
    assert 'aria-label="Abrir conversa"' in ui_page


def test_the_panel_starts_closed(ui_page):
    """Asserted on the markup, not on a screenshot: no `open` class in the HTML.

    The class is only ever added by JS, so its absence in the served document is
    what makes the closed state the default rather than a race.
    """
    assert 'class="main open"' not in ui_page
    assert re.search(r'<div id="main"[^>]*\bopen\b', ui_page) is None, (
        "the chat panel is rendered open; the tiles would be covered on load"
    )


def test_the_open_and_close_functions_exist(ui_page):
    for name in ("function openChat", "function closeChat", "openChat()"):
        assert name in ui_page, f"{name} is missing"


def test_a_device_action_opens_the_panel(ui_page):
    """A reply from a tile is produced into the log. If the panel is closed, the
    owner sees the light change and no confirmation."""
    m = re.search(
        r"async function handleDeviceAction\(device, action\) \{(.*?)\n\s{12}\}",
        ui_page, re.S,
    )
    assert m, "handleDeviceAction not found"
    assert "openChat()" in m.group(1), (
        "switching a device from a tile does not open the panel, so the "
        "confirmation lands where nobody is looking"
    )


def test_a_spoken_command_opens_the_panel(ui_page):
    m = re.search(
        r"if \(data\.success === false\).*?playReply\(data\.audio_base64\);",
        ui_page, re.S,
    )
    assert m, "the voice response path not found"
    assert "openChat()" in m.group(0), (
        "a spoken command answers into a closed panel; the transcript is the "
        "only way to check the house heard the right thing"
    )


def test_escape_closes_the_panel(ui_page):
    assert "e.key === 'Escape'" in ui_page, (
        "no keyboard way out, which matters on a phone with a keyboard"
    )


def test_the_devices_strip_is_still_outside_the_panel(ui_page):
    """The invariant from 2026-09-27: the tiles are primary content and must not
    end up inside a collapsible panel. The chat became a panel; the tiles did not."""
    panel = re.search(r'<div id="main".*?</div>\s*</div>', ui_page, re.S)
    assert panel, "the chat panel not found"
    assert 'id="devices"' not in panel.group(0), (
        "the device strip ended up inside the chat panel"
    )
