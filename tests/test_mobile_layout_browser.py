"""The phone layout, measured in a real browser.

Every other UI test in this repository asserts that a CSS *string* is present.
That is theatre for layout, and it is theatre that cost the owner a broken
release: the phone layout shipped with `#main` computing as `position: relative`
instead of absolute, so the chat panel stayed in the flow, took 459px of a 667px
viewport, and the device strip collapsed to 40px. The tab that opens the chat
was inside the hidden panel, at y=1076 in a 667px screen. The string-based
tests were green the whole time.

So the invariants here are measured on the rendered page at 375x667, which is
what a phone is. A string can be present and mean nothing; a rectangle is the
layout.

The fixtures stub `fetch` so the device tiles exist. That is not a shortcut: the
tiles come from `/get_devices`, which is now behind the session gate, and a
`file://` page carries no session, so a real page would render zero tiles and
the test would measure an empty strip. What is under test is the layout of the
tiles, not the HTTP that fetches them.

Skipped, loudly, when no browser is installed. A layout test that quietly
reports nothing is worse than no layout test, because it reads as coverage.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHONE = {"width": 375, "height": 667}  # the smallest phone worth designing for

STUB_JS = """
window.fetch = async function (url) {
  const u = String(url);
  if (u.includes('get_devices')) return {json: async () => ({devices: {
      status: ['Sensor da Sala', 'Sensor do Quarto'],
      toggles: ['Luz do Quarto', 'Luz da Sala', 'Desumidificador', 'Exaustor',
                'Aspirador', 'carregador do carro', 'forno', 'luz do balcão']}})};
  if (u.includes('device_status')) return {json: async () => ({
      state: 'on', power_w: 42.0, reading_suspect: false, series: [1,2,3],
      state_source: 'api'})};
  if (u.includes('weather')) return {json: async () => ({temp: 18})};
  if (u.includes('/help')) return {json: async () => ({comandos: ['liga a luz']})};
  if (u.includes('reactions')) return {json: async () => ({reactions: []})};
  return {json: async () => ({}), ok: true};
};
"""


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(args=["--no-sandbox"]).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _browser_available(),
    reason=(
        "no headless browser: this is the only test that can see the phone "
        "layout, and skipping it silently is how the last one shipped broken"
    ),
)


@pytest.fixture(scope="module")
def page_html(tmp_path_factory):
    """`/` rendered for a signed-in admin, with fetch stubbed, written to disk."""
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "layout.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    seed.execute(
        "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
        " role TEXT, is_active INTEGER, updated_at TEXT)"
    )
    seed.execute("INSERT INTO users VALUES ('b@t.test','x','admin',1,'now')")
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()

    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = _fresh
    try:
        app = create_app()
        skill_ui.register_routes(app)
        app.config["TESTING"] = True
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[ui_auth.SESSION_KEY] = "b@t.test"
            sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
        html = client.get("/").get_data(as_text=True)
    finally:
        admin_mod.get_db_connection = original

    html = html.replace("<body", f"<script>{STUB_JS}</script><body", 1)
    out = tmp_path_factory.mktemp("ui") / "index.html"
    out.write_text(html, encoding="utf-8")
    return out.as_uri()


@pytest.fixture
def phone(page_html):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        pg = browser.new_page(
            viewport=PHONE, is_mobile=True, has_touch=True,
            device_scale_factor=3, user_agent=(
                "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"
            ),
        )
        pg.set_default_timeout(8000)
        pg.goto(page_html, wait_until="domcontentloaded")
        pg.wait_for_timeout(1200)  # the tiles are built by JS after load
        yield pg
        browser.close()


def _box(pg, selector):
    return pg.evaluate(
        """(sel) => {
            const e = document.querySelector(sel);
            if (!e) return null;
            const r = e.getBoundingClientRect();
            const s = getComputedStyle(e);
            return {x: Math.round(r.x), y: Math.round(r.y),
                    w: Math.round(r.width), h: Math.round(r.height),
                    vis: s.visibility, pos: s.position, display: s.display,
                    tiles: e.querySelectorAll('.device-toggle,.device-sensor').length};
        }""",
        selector,
    )


# --- by default: the tiles own the screen ----------------------------------


def test_the_tiles_take_the_screen_by_default(phone):
    """The owner's instruction, measured.

    Measured 45px before the fix -- the strip was 208px of a 667px screen and
    the tiles were an afterthought inside a wrapper meant for the brand. The
    tiles are the primary content; they get the height.
    """
    devices = _box(phone, "#devices")
    assert devices is not None
    assert devices["h"] >= PHONE["height"] * 0.6, (
        f"the device strip is {devices['h']}px of a {PHONE['height']}px screen "
        f"({devices['h'] / PHONE['height']:.0%}); the tiles are being squeezed"
    )
    assert devices["tiles"] >= 8, (
        f"only {devices['tiles']} tiles rendered; the layout is being measured "
        f"on an empty strip, which would pass every other assertion here"
    )


def test_the_page_does_not_scroll_sideways(phone):
    scroll_w = phone.evaluate("() => document.documentElement.scrollWidth")
    assert scroll_w <= PHONE["width"] + 1, (
        f"scrollWidth {scroll_w} exceeds the viewport {PHONE['width']}: something "
        f"is pushing the layout sideways, which on a phone is a horizontal drag "
        f"that reveals nothing"
    )


def test_the_chat_is_closed_by_default(phone):
    panel = _box(phone, "#main")
    assert panel["y"] >= PHONE["height"] - 1, (
        f"the chat panel is on screen at rest (y={panel['y']}); it covers the "
        f"tiles the moment the page loads"
    )
    assert panel["vis"] == "hidden"


# --- the opener is reachable ------------------------------------------------


def test_the_chat_tab_is_inside_the_viewport(phone):
    """The failure the owner reported: the chat would not open.

    The tab was a child of `#main`, and `visibility` is inherited, so hiding the
    panel hid the only control that could open it. It measured at y=1076 in a
    667px screen.
    """
    tab = _box(phone, "#chat-tab")
    assert tab is not None, "there is no tab to open the chat"
    assert tab["vis"] == "visible", "the opener is invisible"
    assert tab["y"] + tab["h"] <= PHONE["height"], (
        f"the opener sits at y={tab['y']}..{tab['y'] + tab['h']} in a "
        f"{PHONE['height']}px viewport: it cannot be tapped"
    )
    # And it must not be inside the panel it opens.
    inside = phone.evaluate(
        "() => document.getElementById('main')"
        ".contains(document.getElementById('chat-tab'))"
    )
    assert inside is False, (
        "the opener is inside the panel it opens; hiding the panel hides it, "
        "which is exactly the bug that shipped"
    )


# --- opening covers the screen ---------------------------------------------


def test_opening_the_chat_covers_the_screen(phone):
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(400)
    panel = _box(phone, "#main")
    assert panel["y"] == 0, f"the panel starts at y={panel['y']}, not at the top"
    assert panel["h"] >= PHONE["height"] - 1, (
        f"the panel is {panel['h']}px of {PHONE['height']}px: it is a panel, not "
        f"a full-screen sheet"
    )
    assert panel["vis"] == "visible"


def test_there_is_still_a_visible_way_out(phone):
    """A full-screen sheet whose only exit is a gesture is a trap."""
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(400)
    tab = _box(phone, "#chat-tab")
    assert tab["vis"] == "visible"
    assert tab["y"] >= 0 and tab["y"] < PHONE["height"], "the close button is off screen"
    assert phone.evaluate("() => document.getElementById('chat-grip').offsetHeight > 0"), (
        "no drag handle while the sheet is open, so there is no way to dismiss "
        "it with a thumb"
    )


def test_the_microphone_is_in_the_conversation(phone):
    """Voice MESSAGES belong in the chat, next to the other way of talking."""
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(400)
    mic = _box(phone, "#voice-btn-chat")
    assert mic is not None, "no microphone in the composer"
    assert mic["vis"] == "visible"
    assert mic["y"] + mic["h"] <= PHONE["height"], (
        f"the microphone is at y={mic['y']}, below the fold: it is rendered and "
        f"unreachable, which is the same failure as the tab"
    )
    assert mic["w"] >= 40 and mic["h"] >= 40, (
        f"the microphone is {mic['w']}x{mic['h']}; under 44px it is not a touch "
        f"target"
    )
    # A touch target, not decoration.
    in_composer = phone.evaluate(
        "() => !!document.getElementById('voice-btn-chat').closest('#chat-input-box')"
    )
    assert in_composer, "the microphone is not in the composer row"


# --- dragging it away ------------------------------------------------------


def _drag_grip(pg, distance):
    pg.evaluate(
        """(d) => {
            const g = document.getElementById('chat-grip');
            const touch = (y) => new Touch({identifier: 1, target: g, clientY: y});
            const ev = (type, y) => new TouchEvent(type, {
                bubbles: true, cancelable: true,
                touches: type === 'touchend' ? [] : [touch(y)],
                changedTouches: [touch(y)],
            });
            g.dispatchEvent(ev('touchstart', 8));
            g.dispatchEvent(ev('touchmove', 8 + d));
            g.dispatchEvent(ev('touchend', 8 + d));
        }""",
        distance,
    )
    pg.wait_for_timeout(400)


def test_dragging_the_sheet_down_dismisses_it(phone):
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(300)
    _drag_grip(phone, int(PHONE["height"] * 0.6))
    assert not phone.evaluate("() => document.getElementById('main').classList.contains('open')"), (
        "dragging the sheet down did not close it"
    )
    assert not phone.evaluate("() => document.body.classList.contains('chat-open')")


def test_a_short_drag_springs_back(phone):
    """A slip while reading must not throw away the conversation."""
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(300)
    _drag_grip(phone, 30)
    assert phone.evaluate("() => document.getElementById('main').classList.contains('open')"), (
        "a 30px drag closed the chat; the threshold is too eager, and an "
        "ordinary scroll gesture would dismiss the conversation"
    )


def test_dragging_up_does_nothing(phone):
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(300)
    _drag_grip(phone, -200)
    assert phone.evaluate("() => document.getElementById('main').classList.contains('open')"), (
        "dragging upwards closed the sheet; a flick up is not a dismissal"
    )


def test_closing_gives_the_screen_back_to_the_tiles(phone):
    phone.evaluate("() => openChat()")
    phone.wait_for_timeout(300)
    phone.evaluate("() => closeChat()")
    phone.wait_for_timeout(400)
    devices = _box(phone, "#devices")
    assert devices["h"] >= PHONE["height"] * 0.6, (
        f"after closing, the tiles are down to {devices['h']}px: the panel is "
        f"still taking space from them"
    )


# --- desktop is untouched ---------------------------------------------------


def test_the_desktop_layout_is_not_the_phone_layout(phone):
    """The overlay is a phone layout; above the breakpoint the chat is a column
    in the flow, and this must not change."""
    pg = phone.context.browser  # not used; keep the failure message clear
    assert pg is not None
    wide = phone.viewport_size
    phone.set_viewport_size({"width": 1280, "height": 800})
    phone.wait_for_timeout(300)
    panel = _box(phone, "#main")
    assert panel["pos"] == "relative", (
        f"on desktop #main computes as {panel['pos']}; the chat would be a "
        f"full-screen overlay on a wide screen too"
    )
    assert panel["y"] < PHONE["height"], "the desktop chat is not in the flow"
    assert phone.evaluate(
        "() => getComputedStyle(document.getElementById('chat-tab')).display"
    ) == "none", "the chat tab is showing on desktop"
    phone.set_viewport_size(wide)
