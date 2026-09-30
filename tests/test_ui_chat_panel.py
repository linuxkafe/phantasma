
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


def _code_only(text: str) -> str:
    """The block with its comments removed.

    A shape assertion that matches prose is a shape assertion that lies. These
    functions are heavily commented -- which is good -- and the first version of
    `test_a_device_action_does_not_open_the_panel` failed because the COMMENT
    explaining that `openChat()` was removed still contained the characters
    "openChat()". The code was right and the test was reading the essay next to
    it. Strip `/* ... */` and `// ...` and assert on what runs.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _handle_device_action(ui_page):
    m = re.search(
        r"async function handleDeviceAction\(device, action, tile\) \{(.*?)\n            \}",
        ui_page, re.S,
    )
    assert m, "handleDeviceAction not found"
    return _code_only(m.group(1))


def test_a_device_action_does_not_open_the_panel(ui_page):
    """Owner instruction, 2026-09-30: switching a device must not open the
    conversation. It writes the reply to the log in the background and leaves the
    conversation closed.

    This test used to assert the OPPOSITE -- that a device action DOES open the
    panel -- with a comment saying a reply into a closed panel is a reply nobody
    reads. It was pinned here since 2026-09-2x and every deploy enforced it, so
    the behaviour was not an accident anyone had to look for: it was a rule.

    The behaviour it was protecting did not exist. The front end only ever read
    `data.response`, and every error from /device_action answers with `message`
    and no `response`, so a failed action opened the panel onto nothing. Measured
    on the old code, both ways: success wrote the reply, 502 left the log
    byte-for-byte unchanged. The panel opening was not delivering the failure.

    The failure now has somewhere to go that is not the panel: the log, and the
    tile that was touched. Covered behaviourally, with a real browser and a
    stubbed endpoint, in tests/test_mobile_owner_complaints.py -- these are the
    code-shape guards, and those are the ones that would catch it.
    """
    assert "openChat()" not in _handle_device_action(ui_page), (
        "switching a device still opens the conversation: the owner asked for it "
        "to be written in the background instead"
    )


def test_a_device_action_does_not_drop_a_failure(ui_page):
    """The hole the old rule was covering, closed in the place that matters.

    Every non-2xx from /device_action carries `message` and no `response`. The
    old front end branched on `data.response` alone, so the backend's own
    explanation of why it could not switch the light was discarded on the floor
    -- the switch stayed in the state the page had optimistically claimed for it,
    and nothing said otherwise.
    """
    body = _handle_device_action(ui_page)
    assert "data.message" in body, (
        "the backend's error message is not read: a failed action is still "
        "discarded, and now it is discarded silently because the panel no "
        "longer opens to cover for it"
    )
    assert "markTileResult" in body, (
        "the tile is not told how the action went: the eye is on the tile that "
        "was touched, not on a log nobody has opened"
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
