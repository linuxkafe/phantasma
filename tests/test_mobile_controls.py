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
        # A FAKE microphone, not the real one counted. Headless Chromium has no
        # audio input, so the real getUserMedia REJECTS: the code took the
        # "Sem acesso ao microfone" path, never built a MediaRecorder, and every
        # assertion about recording failed for a reason that had nothing to do
        # with the recording. A headless browser is not a machine without a
        # microphone, it is a machine whose microphone nobody plugged in.
        pg.add_init_script(
            """
            window.__gum = 0;
            window.__rec = [];
            const track = { stop() {} };
            const fakeStream = { getTracks: () => [track] };
            navigator.mediaDevices.getUserMedia = async () => {
                window.__gum++;
                return fakeStream;
            };
            const Real = window.MediaRecorder;
            window.MediaRecorder = function () {
                const self = this;
                window.__rec.push('new');
                this.mimeType = 'audio/webm';
                this.ondataavailable = null;
                this.onstop = null;
                /* It has to EMIT DATA, or the page correctly refuses an empty
                   recording and nothing is ever sent -- which is what happened
                   with the first version of this fixture: every assertion about
                   the send path passed by never reaching it. */
                const chunk = (n) => new Blob([new Uint8Array(n).fill(9)],
                                              {type: 'audio/webm'});
                this.start = () => {
                    window.__rec.push('start');
                    setTimeout(() => {
                        if (self.ondataavailable) self.ondataavailable({data: chunk(4000)});
                    }, 40);
                };
                this.stop = () => {
                    window.__rec.push('stop');
                    if (self.ondataavailable) self.ondataavailable({data: chunk(2000)});
                    setTimeout(() => { if (self.onstop) self.onstop(); }, 20);
                };
            };
            if (Real && Real.isTypeSupported) {
                window.MediaRecorder.isTypeSupported = Real.isTypeSupported.bind(Real);
            } else {
                window.MediaRecorder.isTypeSupported = () => true;
            }
            """
        )
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        # Unhandled REJECTIONS too. An async function that throws and is not
        # awaited raises nothing on `pageerror`, which is how a ReferenceError
        # that broke the voice send path reported "no errors" for a whole run.
        pg.add_init_script(
            "window.__rejections = [];"
            "window.addEventListener('unhandledrejection', e =>"
            " window.__rejections.push(String(e.reason && (e.reason.stack || e.reason))));"
        )
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

    Unhandled rejections are included because `pageerror` alone is not enough:
    an async function that throws and is not awaited is silent, which is exactly
    how the voice send path failed while the page reported no errors at all.
    """
    assert not page.errors, f"the page threw: {page.errors}"
    rejections = page.evaluate("() => window.__rejections || []")
    assert not rejections, f"unhandled promise rejection(s): {rejections}"


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


def test_pressing_the_microphone_asks_for_the_microphone(page):
    """Wiring, not styling. A button that renders, is not disabled, and does
    nothing is exactly what shipped before."""
    _hold(page, "#voice-btn", 500)
    assert page.evaluate("() => window.__gum") >= 1, (
        "pressing the microphone never reached getUserMedia: the button has no "
        "listener, which is what a temporal-dead-zone error in initVoice does"
    )
    started = page.evaluate("() => window.__rec")
    assert "new" in started and "start" in started, (
        f"the recorder was never started: {started}"
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
    # Fills everything BELOW the top bar, which is what a sheet pulled up from
    # the dock is. This used to be `height >= 667`, i.e. the whole screen, and
    # that was the owner's complaint: the top bar has to stay visible.
    geom = page.evaluate(
        """() => {const m = document.getElementById('main').getBoundingClientRect();
                   const b = document.getElementById('brand').getBoundingClientRect();
                   return {h: m.height, y: m.y, bottom: m.bottom,
                           barBottom: b.bottom, vh: window.innerHeight};}"""
    )
    # `--brand-h` is set from a ROUNDED measurement (an integer px, so the sheet
    # does not jitter sub-pixel on scroll), while getBoundingClientRect reports
    # 63.984375 for the same bar. One pixel of tolerance is the disagreement
    # between those two, not a gap under the bar.
    assert abs(geom["y"] - geom["barBottom"]) <= 1, (
        f"the panel starts at y={geom['y']} under a top bar ending at "
        f"y={geom['barBottom']}"
    )
    assert geom["bottom"] >= geom["vh"] - 1, (
        f"the panel ends at y={geom['bottom']}, not at the bottom of the "
        f"{geom['vh']}px screen"
    )


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


def test_the_close_control_is_the_grip_and_not_the_dock(page):
    """Supersedes an earlier test that asserted the dock stayed above the open
    panel. The dock does not survive opening any more -- it is the thing that was
    covering the composer -- so the guarantee is now different and stronger:
    the panel is closed by a control that is part of the panel, at the top, not
    by a bar that has to dodge the keyboard.
    """
    page.evaluate("() => openChat()")
    page.wait_for_timeout(400)
    assert page.evaluate(
        "() => getComputedStyle(document.getElementById('chat-dock')).display"
    ) == "none", "the dock is still present over the open panel"
    grip = page.evaluate(
        """() => {const r=document.getElementById('chat-grip').getBoundingClientRect();
            return {y:Math.round(r.y), h:Math.round(r.height), w:Math.round(r.width)};}"""
    )
    assert grip["y"] < 80, f"the close handle is at y={grip['y']}, not at the top"
    assert grip["h"] >= 44 and grip["w"] >= 44, f"the handle is {grip['w']}x{grip['h']}"



# --- the close control must not sit on the composer -----------------------


def test_nothing_covers_the_input_when_the_chat_is_open(page):
    """Reported: the close button was on top of the field you type into.

    Measured: the tab was [0,607,315,60] over an input box of [0,574,375,93].
    The `top:10px` that was supposed to lift the close button did nothing,
    because the tab had become a static flex child of the dock, so `top` and
    `right` had no positioned element to apply to. Asserted on the element that
    is actually on top at the centre of the field, which is the only question
    that matters to somebody about to type.
    """
    page.evaluate("() => openChat()")
    page.wait_for_timeout(500)
    top = page.evaluate(
        """() => {const r=document.getElementById('chat-input').getBoundingClientRect();
            const e=document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
            return e ? (e.id || String(e.className)) : 'nada';}"""
    )
    assert top == "chat-input", (
        f"the element on top of the input field is {top!r}: you cannot type "
        f"through it"
    )


def test_the_dock_gets_out_of_the_way_when_the_chat_is_open(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(400)
    assert page.evaluate(
        "() => getComputedStyle(document.getElementById('chat-dock')).display"
    ) == "none", (
        "the dock is still on screen over the composer; the composer has its "
        "own microphone, so the dock only gets in the way"
    )


def test_there_is_a_visible_way_to_close(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(400)
    box = page.evaluate(
        """() => {const r=document.getElementById('chat-grip').getBoundingClientRect();
            return {w:Math.round(r.width), h:Math.round(r.height)};}"""
    )
    assert box["h"] >= 44, f"the close handle is {box['h']}px tall: under 44 is not a target"
    page.evaluate("() => document.getElementById('chat-grip').click()")
    page.wait_for_timeout(400)
    assert not page.evaluate("() => document.getElementById('main').classList.contains('open')"), (
        "tapping the handle did not close the chat"
    )


# --- the chat avatar -------------------------------------------------------


def test_the_chat_avatar_is_a_ghost_and_is_sized(page):
    """It was 183x150: 62% of a box with no size, in a flex row that grew to
    fit the ghost instead of the other way round."""
    page.evaluate("() => addToChatLog('olá', 'ia')")
    page.wait_for_timeout(200)
    box = page.evaluate(
        """() => {const a=document.querySelector('.ia-avatar'); if(!a) return null;
            const s=a.querySelector('svg');
            const r=a.getBoundingClientRect();
            return {w:Math.round(r.width), h:Math.round(r.height), svg:!!s};}"""
    )
    assert box is not None, "the chat has no avatar at all"
    assert box["svg"], "the avatar is not a ghost"
    assert box["w"] <= 40 and box["h"] <= 40, (
        f"the avatar is {box['w']}x{box['h']}: it is negotiating with the "
        f"container instead of being sized by it"
    )


# --- rooms are iconified ---------------------------------------------------


def test_every_room_header_has_an_icon(page):
    page.wait_for_timeout(400)
    rooms = page.evaluate(
        """() => [...document.querySelectorAll('.room-header')].map(h => ({
            name: h.textContent.trim(), svg: !!h.querySelector('svg'),
            h: Math.round(h.getBoundingClientRect().height) }))"""
    )
    assert rooms, "no room headers rendered"
    for r in rooms:
        assert r["svg"], f"the room {r['name']!r} has no icon"
        assert r["h"] >= 14, f"the room {r['name']!r} header is {r['h']}px: unreadable"


def test_the_robot_vacuum_does_not_look_like_the_sun(page):
    """It was a circle with four radial lines, which is the universal
    brightness glyph -- so the one device that is genuinely round was drawn as
    the one thing it is not. Asserted as 'not the same drawing as the sun'
    rather than as a path count, because that is the actual defect."""
    page.wait_for_timeout(400)
    result = page.evaluate(
        """() => {
            const t = [...document.querySelectorAll('.device-toggle')]
                .find(e => /aspir/i.test(e.title || ''));
            if (!t) return {found: false};
            const vac = t.querySelector('.device-icon svg');
            return {found: true, html: vac ? vac.innerHTML : ''};
        }"""
    )
    if not result["found"]:
        pytest.skip("this stub has no robot vacuum in it")
    # The sun is a small disc plus radial rays; the vacuum must not be that.
    rays_only = result["html"]
    assert rays_only.count("<circle") >= 2 or "bezier" in rays_only or "M18.6" in rays_only, (
        "the robot vacuum is still the circle-with-rays drawing, which reads as "
        "a sun or a brightness control"
    )


# --- the first thing the ghost says must be readable ----------------------


def test_the_first_message_is_not_underneath_the_close_handle(page):
    """It was 26px of padding against a 44px handle, and the first row started
    at y=26 -- `elementFromPoint` on it returned the handle. The number has to be
    read off the handle, not guessed smaller."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(500)
    result = page.evaluate(
        """() => {
            const g = document.getElementById('chat-grip').getBoundingClientRect();
            const log = document.getElementById('chat-log');
            const first = log.children[0];
            if (!first) return null;
            const r = first.getBoundingClientRect();
            const e = document.elementFromPoint(r.x + 20, r.y + 8);
            return {top: Math.round(r.y), handleBottom: Math.round(g.bottom),
                    coveredBy: e ? (e.id || e.className || e.tagName) : 'nada'};
        }"""
    )
    assert result is not None, "the chat is empty, so there is nothing to cover"
    assert result["top"] >= result["handleBottom"], (
        f"the first message starts at y={result['top']} and the handle ends at "
        f"{result['handleBottom']}: the ghost's first words are underneath it"
    )
    assert "chat-grip" not in str(result["coveredBy"]), (
        f"the first message is covered by {result['coveredBy']!r}"
    )


# --- the ghost is actually drawn --------------------------------------------


def test_the_chat_avatar_is_not_an_invisible_stroke(page):
    """It measured 26x26, visible, with a real ghost in it -- and stroke: none.

    The SVG was built as a JavaScript string with `&apos;` for its quotes. That
    is an HTML entity, and inside a JS string it is literal text, so the
    attribute became `stroke="&apos;currentColor&apos;"`, which is not a colour.
    The element was present, sized, and drew nothing at all: an invisible ghost
    is worse than no ghost, because the space is still reserved.
    """
    page.wait_for_timeout(400)
    info = page.evaluate(
        """() => {
            const a = document.querySelector('.ia-avatar');
            if (!a) return null;
            const svg = a.querySelector('svg');
            if (!svg) return {svg: false};
            const cs = getComputedStyle(svg);
            return {svg: true, stroke: cs.stroke,
                    w: Math.round(svg.getBoundingClientRect().width)};
        }"""
    )
    assert info and info["svg"], "the avatar has no SVG in it"
    assert info["stroke"] not in ("none", "", "rgba(0, 0, 0, 0)"), (
        f"the ghost's stroke is {info['stroke']!r}: the avatar occupies space "
        f"and draws nothing, which reads as a missing image"
    )
    assert info["w"] > 4, f"the ghost is {info['w']}px wide"


# --- press and hold, not tap to toggle ------------------------------------


def _centre(pg, selector):
    return pg.evaluate(
        """(s) => {const r=document.querySelector(s).getBoundingClientRect();
            return {x: r.x + r.width/2, y: r.y + r.height/2};}""",
        selector,
    )


def _pointer(pg, selector, kind, box):
    pg.evaluate(
        """([s, t, x, y]) => {const b=document.querySelector(s);
            b.dispatchEvent(new PointerEvent(t,
              {bubbles:true, cancelable:true, pointerId:1, pointerType:'touch',
               clientX:x, clientY:y, isPrimary:true}));}""",
        [selector, kind, box["x"], box["y"]],
    )


def _press(pg, selector, hold_ms=450):
    """pointerdown, then wait `hold_ms`. Returns the box, for the release.

    Split from the release so a test can look at the state WHILE the button is
    held, which is the only moment that proves hold-to-talk works. The wait is a
    parameter for a concrete reason: a helper that always waits 450ms cannot test
    a 200ms press, and a 450ms "short press" is not a short press -- the first
    version of this test asserted a 200ms brush and was really asserting 450ms,
    which is above the 350ms minimum, so it took the send path and failed for
    the wrong reason.
    """
    box = _centre(pg, selector)
    _pointer(pg, selector, "pointerdown", box)
    pg.wait_for_timeout(hold_ms)
    return box


def _release(pg, selector, box=None):
    _pointer(pg, selector, "pointerup", box or _centre(pg, selector))
    pg.wait_for_timeout(450)


def _hold(pg, selector, hold_ms):
    """A whole press-and-hold of exactly `hold_ms`, for tests that only care
    about the end state."""
    box = _press(pg, selector, hold_ms)
    _release(pg, selector, box)


def test_holding_the_microphone_records_and_releasing_stops(page):
    """It was a click handler that toggled. You pressed, held, and nothing
    happened, because nothing happens on a press.

    Observed WHILE held, which is why pressing and releasing are separate
    helpers here.
    """
    box = _press(page, "#voice-btn", 700)
    mid = page.evaluate(
        "() => document.getElementById('voice-btn').classList.contains('recording')"
    )
    assert mid, "holding the microphone did not put it into the recording state"
    _release(page, "#voice-btn", box)


def test_releasing_the_microphone_releases_the_button(page):
    """The send path threw a ReferenceError on its first line, so the button
    stayed red and disabled for ever -- and because it was an unhandled
    rejection, the page reported no error at all."""
    _hold(page, "#voice-btn", 700)
    assert not page.evaluate(
        "() => document.getElementById('voice-btn').classList.contains('recording')"
    ), "the button is still in the recording state after release"
    assert not page.evaluate("() => document.getElementById('voice-btn').disabled"), (
        "the button is still disabled after release: it can never be used again"
    )


def test_a_brush_of_the_thumb_does_not_send_a_command(page):
    """A 200ms press is a tap, not a word. It must be refused, with a reason."""
    _hold(page, "#voice-btn", 200)
    msg = page.evaluate(
        "() => {const e=document.querySelector('.voice-status');"
        " return e ? e.textContent : null;}"
    )
    assert msg, "a 200ms press gave the user no feedback at all"
    assert "premid" in msg.lower() or "curto" in msg.lower(), (
        f"a 200ms press produced {msg!r}, which does not say the press was too short"
    )


def test_the_microphone_works_from_the_keyboard(page):
    """Hold-to-talk that needs a finger is not an accessible control."""
    page.evaluate(
        """() => {const b=document.getElementById('voice-btn');
            b.dispatchEvent(new KeyboardEvent('keydown',
              {key:' ', bubbles:true, cancelable:true}));}"""
    )
    page.wait_for_timeout(500)
    assert page.evaluate(
        "() => document.getElementById('voice-btn').classList.contains('recording')"
    ), "Enter/Space did not start recording: the button is finger-only"
    page.evaluate(
        """() => {const b=document.getElementById('voice-btn');
            b.dispatchEvent(new KeyboardEvent('keyup',
              {key:' ', bubbles:true, cancelable:true}));}"""
    )
    page.wait_for_timeout(400)
    assert not page.evaluate(
        "() => document.getElementById('voice-btn').classList.contains('recording')"
    ), "releasing the key did not stop recording"


# --- the top bar ------------------------------------------------------------


def _bar(pg):
    return pg.evaluate(
        """() => {
            const box = (s) => {const e = document.querySelector(s);
                if (!e) return null;
                const r = e.getBoundingClientRect();
                return {x: Math.round(r.x), w: Math.round(r.width),
                        vis: getComputedStyle(e).display !== 'none'};};
            return {logo: box('#brand-logo'), weather: box('#main-weather-icon'),
                    moon: box('#moon-slot'), uv: box('#uv-slot'), aqi: box('#aqi-indicator'),
                    burger: box('.nav-toggle'),
                    name: getComputedStyle(document.getElementById('brand-name')).display};
        }"""
    )


def test_the_ghost_is_flush_left_and_the_burger_flush_right(page):
    page.wait_for_timeout(500)
    b = _bar(page)
    assert b["logo"]["x"] <= 16, f"the ghost starts at x={b['logo']['x']}"
    assert b["burger"]["x"] + b["burger"]["w"] >= PHONE["width"] - 16, (
        f"the burger ends at {b['burger']['x'] + b['burger']['w']} of "
        f"{PHONE['width']}: it is not at the right edge"
    )


def test_the_indicators_follow_the_ghost_in_reading_order(page):
    page.wait_for_timeout(500)
    b = _bar(page)
    logo_end = b["logo"]["x"] + b["logo"]["w"]
    assert b["weather"]["x"] >= logo_end, (
        "the weather is not next to the ghost"
    )
    shown = [(k, v) for k, v in (("moon", b["moon"]), ("uv", b["uv"]),
                                 ("aqi", b["aqi"])) if v["vis"]]
    xs = [v["x"] for _k, v in shown]
    assert xs == sorted(xs), f"the indicators are out of order: {shown}"
    # And they are a group, not a banner across the screen.
    assert b["weather"]["x"] < PHONE["width"] * 0.6, (
        f"the sky starts at x={b['weather']['x']}: it is centred, not following "
        f"the ghost"
    )


def test_the_word_mark_is_dropped_on_a_phone(page):
    """At 375px there is room for the ghost, four indicators and the burger, or
    for those and the name. The ghost is already the name."""
    page.wait_for_timeout(400)
    assert _bar(page)["name"] == "none", (
        "the word mark is still on the phone and competes with the indicators"
    )


# --- the voice send, end to end --------------------------------------------


def test_a_recording_actually_reaches_the_server(page):
    """This never worked, and every test above it passed.

    `sendRecording` read `_voiceMime`, which was declared inside `initVoice()`
    while sendRecording is a sibling: a ReferenceError on its very first line,
    before the network. The user saw "falha ao enviar o áudio" with the
    recording working. The send was never asserted by any test, so the whole
    feature could be broken from end to end and the suite stayed green.
    """
    page.evaluate(
        """() => {window.__sent = null;
            const prev = window.fetch;
            window.fetch = async (u, o) => {
                if (String(u).includes('/api/voz')) {
                    const b = JSON.parse(o.body);
                    window.__sent = {chars: b.audio_base64.length, ct: b.content_type};
                    return {status: 200, json: async () => ({
                        success: true, transcript: 'liga a luz', text: 'Ligado.'})};
                }
                return prev(u, o);
            };}"""
    )
    box = _press(page, "#voice-btn", 600)
    _release(page, "#voice-btn", box)
    page.wait_for_timeout(600)
    sent = page.evaluate("() => window.__sent")
    assert sent, "nothing was ever sent to /api/voz: the send path is dead"
    assert sent["chars"] > 100, f"only {sent['chars']} chars were sent"
    assert "webm" in sent["ct"] or "mp4" in sent["ct"] or "wav" in sent["ct"], (
        f"the recording's own container was not declared: {sent['ct']!r}. The page "
        f"used to rebuild a WAV in the browser; it now sends bytes and the server "
        f"decodes them with PyAV."
    )


def test_the_container_is_sent_untouched(page):
    """No browser-side decodeAudioData, no hand-written resampler, no
    hand-written WAV. The three steps that failed on a real phone are gone, and
    the payload is the recording as produced."""
    # Call sites, not prose. All three names survive in the COMMENTS that explain
    # why they are gone -- which is the point of those comments -- so asserting
    # on the raw text of the script fails on documentation and teaches everyone
    # to delete the explanation.
    called = page.evaluate(
        """() => {
            const src = Array.from(document.scripts).map(s => s.textContent).join('');
            // Strip block and line comments, then look for a CALL.
            const code = src.replace(/\\/\\*[\\s\\S]*?\\*\\//g, '').replace(/^\\s*\\/\\/.*$/gm, '');
            return {resample: /\btoMono16k\\s*\\(/.test(code),
                    wav: /\bpcmToWav\\s*\\(/.test(code),
                    decode: /\bdecodeAudioData\\s*\\(/.test(code)};
        }"""
    )
    assert not called["resample"], "the browser-side resampler is still called"
    assert not called["wav"], "the hand-written WAV encoder is still called"
    assert not called["decode"], (
        "the page still decodes its own recording. That is the step that failed "
        "on a real phone, and it has no reason to exist: the server carries PyAV "
        "and opens webm, opus, ogg, mp4 and wav alike"
    )

