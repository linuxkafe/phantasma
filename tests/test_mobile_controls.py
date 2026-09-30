"""The phone controls, measured in a real browser.

Found by a mobile-UI audit on 2026-09-29, and the audit was right about all of
it. Every one of these was invisible to the string-based tests, and two of them
were invisible to me because I had claimed the feature worked:

* **Neither microphone did anything.** `initVoice()` was called near the top of
  the script while `const voiceBtn` was declared twenty lines below it. That is
  a temporal-dead-zone access: a `ReferenceError` thrown before any listener was
  attached. The page rendered two microphones, neither disabled, neither doing
  anything, and the status element never entered the DOM. `node --check` passes
  on it, because it is a runtime error, not a syntax error.
* **The chat pill sat on top of the microphone.** `elementFromPoint` at the
  mic's centre returned the pill, so the mic could not be tapped.
* **Seven of seventeen switches were off-screen.** The rooms were laid out in a
  row with `flex-shrink: 0`, the strip measured 857px of content inside a 375px
  box with `overflow-x: hidden`, and `scrollWidth == innerWidth` so the
  no-side-scroll test passed.
* **The device states arrived 5.29s late**, because they were only fetched by the
  first tick of a 5-second interval.

Each of these is a "the code looks right" failure. So these tests touch the page
and read numbers: does a tap reach the microphone, is a switch inside the
viewport, how long until the states arrive, is there an emoji left in the icon
slot.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_mobile_layout_browser import (  # noqa: E402
    PHONE,
    STUB_JS,
    _browser_available,
)

pytestmark = pytest.mark.skipif(
    not _browser_available(),
    reason="no headless browser: these are the only tests that can see whether the controls work",
)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    import sqlite3
    import tempfile

    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "ctrl.db")

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
def page(doc):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        pg = browser.new_page(
            viewport=PHONE, is_mobile=True, has_touch=True, device_scale_factor=3,
        )
        # getUserMedia is counted, not performed: a headless browser has no
        # microphone, and a fake one is enough to prove the button is wired.
        pg.add_init_script(
            "window.__gum = 0;"
            "const o = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);"
            "navigator.mediaDevices.getUserMedia = (c) => { window.__gum++; return o(c); };"
        )
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.set_default_timeout(8000)
        pg.goto(doc, wait_until="domcontentloaded")
        pg.wait_for_timeout(1500)
        pg.errors = errors
        yield pg
        browser.close()


# --- nothing throws ---------------------------------------------------------


def test_the_page_raises_no_javascript_error(page):
    """The gate for everything else in this file.

    A `ReferenceError` in a script does not stop the HTML rendering: the
    elements exist, the CSS applies, the screenshot looks right, and every
    listener after the throw is missing. This is how two microphones rendered
    and did nothing for a whole release.
    """
    assert not page.errors, f"the page threw: {page.errors}"


def test_the_voice_status_element_exists(page):
    assert page.evaluate("() => !!document.querySelector('.voice-status')"), (
        "the voice status element is not in the DOM, which means initVoice "
        "bailed before it was created"
    )


# --- the microphone ---------------------------------------------------------


def test_the_microphone_is_in_the_dock_and_always_visible(page):
    box = page.evaluate(
        """() => {const e=document.getElementById('voice-btn'); if(!e) return null;
            const r=e.getBoundingClientRect();
            return {x:Math.round(r.x), y:Math.round(r.y),
                    w:Math.round(r.width), h:Math.round(r.height),
                    vis:getComputedStyle(e).visibility};}"""
    )
    assert box is not None, "there is no microphone in the dock"
    assert box["vis"] == "visible"
    assert box["y"] + box["h"] <= PHONE["height"] + 1, (
        f"the microphone is at y={box['y']} in a {PHONE['height']}px screen: "
        f"it renders where a thumb cannot reach"
    )
    assert box["w"] >= 44 and box["h"] >= 44, (
        f"the microphone is {box['w']}x{box['h']}"
    )


def test_nothing_is_painted_over_the_microphone(page):
    """The measured failure: the chat pill was on top of it, so a tap at the
    centre delivered nothing."""
    hit = page.evaluate(
        """() => {const b=document.getElementById('voice-btn');
            const r=b.getBoundingClientRect();
            const e=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
            return e ? (b.contains(e) ? 'proprio' : (e.id || e.className)) : 'nada';}"""
    )
    # A CHILD of the button is fine -- the icon inside it is part of the target.
    # What is not fine is another element entirely: that was the chat pill, and
    # the assertion has to tell the two apart or it fails on its own markup.
    assert hit == "proprio", (
        f"the element at the centre of the microphone is {hit!r}, not the "
        f"microphone or something inside it: the control renders but cannot be "
        f"tapped"
    )


def test_tapping_the_microphone_asks_for_the_microphone(page):
    """Wiring, not styling. A button that renders, is not disabled, and does
    nothing is exactly what shipped before."""
    page.evaluate("() => document.getElementById('voice-btn').click()")
    page.wait_for_timeout(700)
    assert page.evaluate("() => window.__gum") >= 1, (
        "tapping the microphone never reached getUserMedia: the button has no "
        "listener, which is what a temporal-dead-zone error in initVoice does"
    )


def test_the_microphone_carries_an_icon_not_an_emoji(page):
    svg = page.evaluate(
        "() => !!document.querySelector('#voice-btn svg')"
    )
    assert svg, "the microphone has no SVG icon"


# --- every device is reachable ---------------------------------------------


def test_no_switch_is_painted_outside_the_strip(page):
    outside = page.evaluate(
        """() => {const d=document.getElementById('devices');
            const R=d.getBoundingClientRect();
            return [...d.querySelectorAll('.device-toggle')].filter(t => {
                const r=t.getBoundingClientRect();
                return r.right > R.right + 1 || r.left < R.left - 1;
            }).length;}"""
    )
    assert outside == 0, (
        f"{outside} switches are outside the strip: the rooms are laid out in a "
        f"row with overflow-x:hidden, so they are painted off the edge and "
        f"cannot be reached"
    )


def test_the_strip_does_not_scroll_sideways(page):
    w = page.evaluate(
        "() => [document.getElementById('devices').scrollWidth,"
        " document.getElementById('devices').clientWidth]"
    )
    assert w[0] <= w[1] + 1, f"the strip is {w[0]}px of content in {w[1]}px"


def test_the_tiles_have_real_size(page):
    box = page.evaluate(
        """() => {const t=document.querySelector('.device-toggle'); if(!t) return null;
            const r=t.getBoundingClientRect();
            return [Math.round(r.width), Math.round(r.height)];}"""
    )
    assert box and box[0] > 20 and box[1] > 20, f"a tile measured {box}"


def test_the_states_are_already_there_when_the_page_has_loaded(page):
    """They used to arrive 5.29s late, on the first tick of a 5-second
    interval. Every switch was inert for five seconds of every load, and a
    person cannot throw a switch they have not waited for."""
    disabled = page.evaluate(
        "() => [...document.querySelectorAll('.device-toggle')]"
        ".filter(e => e.classList.contains('disabled') || e.disabled).length"
    )
    total = page.evaluate("() => document.querySelectorAll('.device-toggle').length")
    assert disabled == 0, (
        f"{disabled} of {total} switches are still disabled 1.5s after load; the "
        f"first fetch is still waiting for the 5s poll"
    )


# --- icons ------------------------------------------------------------------


def test_the_device_icons_are_svg_and_not_emoji(page):
    svgs = page.evaluate("() => document.querySelectorAll('.device-icon svg').length")
    tiles = page.evaluate("() => document.querySelectorAll('.device-icon').length")
    assert svgs == tiles and tiles > 0, (
        f"{svgs} SVGs for {tiles} icon slots: the rest are text. `innerText` on "
        f"an SVG string renders the markup as visible code inside the tile"
    )


def test_an_svg_icon_is_actually_sized(page):
    box = page.evaluate(
        """() => {const s=document.querySelector('.device-icon svg'); if(!s) return null;
            const r=s.getBoundingClientRect();
            return [Math.round(r.width), Math.round(r.height)];}"""
    )
    assert box and box[0] > 4 and box[1] > 4, (
        f"an icon SVG measured {box}: 62% of a badge with no height is 0x0, "
        f"which is indistinguishable from a failed load"
    )


def test_the_brand_svg_is_not_zero(page):
    box = page.evaluate(
        """() => {const s=document.querySelector('#brand-logo svg'); if(!s) return null;
            const r=s.getBoundingClientRect();
            return [Math.round(r.width), Math.round(r.height)];}"""
    )
    assert box and box[0] > 4, f"the brand SVG measured {box}"


def test_no_emoji_glyph_is_left_in_the_rendered_page(page):
    """Scans the RENDERED page, skipping <script> and <style>.

    The skip is not cosmetic. A `⚡` survived in a JavaScript comment explaining
    why the catch-all ⚡ was removed -- code, not pixels -- and the test failed
    on it. Without the skip the assertion is about the source, not the
    interface, and the first thing anyone does to make it pass is delete the
    comment that documents the decision.
    """
    found = page.evaluate(
        """() => {
            const out = [];
            const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let n;
            while ((n = w.nextNode())) {
                const el = n.parentElement;
                if (!el) continue;
                if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
                const m = (n.nodeValue || '').match(/[\\u{1F300}-\\u{1FAFF}\\u2600-\\u27BF]/gu);
                if (m) out.push(m.join('') + ' @' + (el.id || el.className || el.tagName));
            }
            return out;
        }"""
    )
    assert not found, f"emoji still rendered: {found}"


# --- the burger is at the top ----------------------------------------------


def test_the_burger_sits_at_the_top_of_the_screen(page):
    box = page.evaluate(
        """() => {const e=document.querySelector('.nav-toggle'); if(!e) return null;
            const r=e.getBoundingClientRect();
            return {x:Math.round(r.x), y:Math.round(r.y),
                    w:Math.round(r.width), h:Math.round(r.height)};}"""
    )
    assert box is not None, "there is no burger"
    assert box["y"] < PHONE["height"] * 0.2, (
        f"the burger is at y={box['y']} of {PHONE['height']}: it did not move to "
        f"the top as asked"
    )
    assert box["x"] > PHONE["width"] / 2, (
        f"the burger is at x={box['x']}: top-RIGHT is the corner a right thumb "
        f"reaches, and the bottom-right corner now belongs to the dock"
    )
    assert box["w"] >= 44 and box["h"] >= 44, f"the burger is {box['w']}x{box['h']}"


def test_the_open_menu_does_not_hide_the_sign_out(page):
    """It was wrapped into a second flex column and pushed to x=365 of a 375px
    screen: sign-out was off-screen, on the page whose job includes signing
    out."""
    page.evaluate("() => document.querySelector('.nav-toggle').click()")
    page.wait_for_timeout(400)
    result = page.evaluate(
        """() => {const e=document.getElementById('nav-menu');
            const link=[...e.querySelectorAll('a')].find(a=>/sair|logout/i.test(a.textContent));
            if(!link) return 'sem link de sair';
            const r=link.getBoundingClientRect();
            return {x:Math.round(r.x), right:Math.round(r.right),
                    visible: r.width>0 && r.right<=innerWidth+1 && r.bottom<=innerHeight+1};}"""
    )
    assert result != "sem link de sair", result
    assert result["visible"], (
        f"the sign-out is at x={result['x']}..{result['right']}, off-screen"
    )


# --- the dock ---------------------------------------------------------------


def test_the_dock_is_a_full_width_bar_not_a_pill(page):
    box = page.evaluate(
        """() => {const e=document.getElementById('chat-dock'); if(!e) return null;
            const r=e.getBoundingClientRect();
            return {w:Math.round(r.width), h:Math.round(r.height),
                    y:Math.round(r.y)};}"""
    )
    assert box is not None, "there is no dock"
    assert box["w"] >= PHONE["width"] * 0.9, (
        f"the dock is {box['w']}px of a {PHONE['width']}px screen: a pill in the "
        f"corner is what a thumb misses, and a bar is what was asked for"
    )
    assert box["h"] >= 56, f"the dock is {box['h']}px tall; a bar needs presence"
    assert box["y"] + box["h"] <= PHONE["height"] + 1, "the dock is below the fold"


def test_the_dock_has_a_prominent_handle(page):
    box = page.evaluate(
        """() => {const e=document.getElementById('dock-grip'); if(!e) return null;
            const r=e.getBoundingClientRect();
            return {w:Math.round(r.width), h:Math.round(r.height)};}"""
    )
    assert box is not None, "the dock has no handle to grab"
    assert box["w"] >= 40 and box["h"] >= 4, (
        f"the grip is {box['w']}x{box['h']}: too small to read as a handle"
    )


def test_pulling_the_dock_up_opens_the_chat(page):
    """The gesture, not just the state: the panel follows the finger and opens."""
    page.evaluate(
        """() => {const d=document.getElementById('chat-dock');
            const T=(y)=>new Touch({identifier:1, target:d, clientY:y});
            const E=(t,y)=>new TouchEvent(t,{bubbles:true,cancelable:true,
                touches:t==='touchend'?[]:[T(y)], changedTouches:[T(y)]});
            d.dispatchEvent(E('touchstart',640));
            d.dispatchEvent(E('touchmove',560));
            d.dispatchEvent(E('touchend',560));}"""
    )
    page.wait_for_timeout(600)
    assert page.evaluate("() => document.getElementById('main').classList.contains('open')"), (
        "pulling the dock upwards did not open the conversation"
    )
    height = page.evaluate("() => document.getElementById('main').offsetHeight")
    assert height >= PHONE["height"] - 1, f"the panel is {height}px once open"


def test_opening_is_a_blind_not_a_slide(page):
    """The persiana: the panel is revealed by an edge travelling down, which is
    `clip-path`, not a translate of the whole rectangle."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(500)
    clip = page.evaluate("() => getComputedStyle(document.getElementById('main')).clipPath")
    assert clip and clip != "none", (
        f"clip-path is {clip}: the panel has no blind, it only slides"
    )
    assert "inset" in clip and clip.strip().endswith("0%)"), (
        f"the blind is not fully unrolled: {clip}"
    )


def test_the_dock_stays_above_the_open_panel(page):
    """The panel covers the screen when it opens; the dock has to stay on top of
    it or there is no way back that is not a gesture."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(400)
    z = page.evaluate(
        """() => {const d=document.getElementById('chat-dock');
            const hit=document.elementFromPoint(20, d.getBoundingClientRect().top + 10);
            return hit ? (d.contains(hit) ? 'doca' : (hit.id || hit.className)) : 'nada';}"""
    )
    assert z == "doca", (
        f"at the dock's own position the topmost element is {z!r}: the panel is "
        f"over the dock and the dock cannot be tapped to close"
    )
