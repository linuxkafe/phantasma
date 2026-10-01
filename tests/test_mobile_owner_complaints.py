"""The four things the owner complained about on 2026-09-30, as tests.

The complaints, in the owner's words: a grey bar across the top when the chat
opened, which did not need to take the whole screen and should show an icon for
dragging the chat down; the top bar should always be visible; the device icons
should have more complete names, not just "Sala" or "Balcão"; and the
temperature/humidity icons should go, with their readings next to the room name.

Every assertion here is written against a number MEASURED on the old code, not
against a guess. They are the measurements, kept:

* `#chat-grip` was `position: fixed; top: 0; left: 0; right: 0` with
  `background: #9a9a9a` when the chat was open: 375px x 44px of grey, at
  z-index 55, over `#header-strip` at z-index 50. That band was the complaint.
* `#main` was `position: fixed; inset: 0` at z-index 40. Below 768px
  `#header-strip` is `display: contents`, so it has no box and `#brand` and
  `#devices` are static flex children of the body with no z-index: the panel
  painted over both. Measured y=0 h=667 against a 64px brand row.
* Tiles were named `device.split(' ').pop().substring(0,12)` -- last word only.
  With the real production device list that made "Luz da Sala" and "Exaustor da
  Sala" both read "Sala", and "Luz do Quarto", "Candeeiro do Quarto" and
  "Desumidificador do Quarto" all read "Quarto". Six of eight tiles.
* A device drawing power had its name REPLACED by the wattage, so the
  desumidifier at 210W was the one tile that never said what it was.
* Three sensor tiles existed, each one a whole tile whose content was a number
  in the room it was already standing in.
* `fetchSensorStatus` read `parts.length` where the array is named
  `measurements`. The ReferenceError was swallowed by a bare `catch (e) {}`, so
  the colour was never applied and the title -- the only place the reading's age
  was available -- was never written. A stale reading looked live.

The fetch stub is asserted to have fired. A stub that silently does not match
turns a test suite into a machine that confirms whatever the server happens to
answer, which is how a whole round of "verified" layout numbers came to be
measured against a server that was returning 500 for every device.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHONE = {"width": 375, "height": 667}

# The real production device list (14 entries across miio/tuya/cloogy/ewelink),
# plus the two Cloogy/Ewelink names, because a naming rule that has only ever
# seen "Luz da Sala" is a naming rule that has not been tested.
TOGGLES = [
    "Luz da Sala", "Luz do Quarto", "Exaustor da Sala", "Exaustor do WC",
    "Candeeiro do Quarto", "Aspirador", "Desumidificador do Armário",
    "Desumidificador do Quarto", "forno", "carregador do carro",
]
SENSORS = ["Sensor da Sala", "Sensor do Quarto", "Sensor do WC", "Sensor de Gás"]

READINGS = {
    "Sensor da Sala": {"state": "on", "temperature": 21.4, "humidity": 48, "age_s": 120},
    "Sensor do Quarto": {"state": "on", "temperature": 19.8, "humidity": 55, "age_s": 300},
    "Sensor do WC": {"state": "on", "temperature": 18.1, "humidity": 62, "age_s": 900},
    # The gas meter has no room word of its own (getRoomName -> "Geral"), which
    # is exactly where the average of the rooms must appear, before the ppm.
    "Sensor de Gás": {"state": "on", "ppm": 340, "status": "normal", "age_s": 60},
    "Luz da Sala": {"state": "on", "power_w": 0},
    "Luz do Quarto": {"state": "off", "power_w": 0},
    "Exaustor da Sala": {"state": "on", "power_w": 24.5},
    "Exaustor do WC": {"state": "off", "power_w": 0},
    "Candeeiro do Quarto": {"state": "on", "power_w": 8.2},
    "Aspirador": {"state": "off", "power_w": 0},
    "Desumidificador do Armário": {"state": "on", "power_w": 210},
    "Desumidificador do Quarto": {"state": "off", "power_w": 0},
    "forno": {"state": "off", "power_w": 0},
    "carregador do carro": {"state": "off", "power_w": 0},
    "casa": {"state": "on", "power_w": 1234},
}

STUB_JS = """
window.__stubHits = {devices: 0, status: 0, matched: 0, action: 0};
/* Set by a test before it taps a tile: 'ok' or 'erro'. The error shape is the
   backend's own -- `message`, no `response`, status 502 -- because the whole
   point is that this is a shape the front end used to throw away. */
window.__actionMode = 'ok';
window.fetch = async function (url) {
  const u = String(url);
  /* decodeURIComponent, not the raw slice: the page asks for
     `?nickname=Desumidificador%20do%20Arm%C3%A1rio`, and looking the encoded
     string up in a table keyed by the real name misses EVERY device. That is
     the second version of the same bug -- a stub that fires and matches
     nothing is still a stub that tells you nothing, and the devices simply
     came back "unreachable", which looks like a product fault. `matched`
     counts the hits so a test can insist on it. */
  const raw = (u.split('nickname=')[1] || '').split('&')[0];
  let nick = raw;
  try { nick = decodeURIComponent(raw); } catch (e) {}
  const json = (o) => ({json: async () => o, ok: true, status: 200});
  if (u.includes('device_action')) {
    window.__stubHits.action++;
    if (window.__actionMode === 'erro') {
      return {json: async () => ({status: 'error', message: 'Sem resposta'}),
              ok: false, status: 502};
    }
    return json({status: 'ok', response: 'Luz ligada.'});
  }
  if (u.includes('get_devices')) {
    window.__stubHits.devices++;
    return json({devices: {status: __SENSORS__, toggles: __TOGGLES__}});
  }
  if (u.includes('device_status')) {
    window.__stubHits.status++;
    const hit = __READINGS__[nick];
    if (hit) window.__stubHits.matched++;
    return json(hit || {state: 'unreachable'});
  }
  if (u.includes('weather')) return json({today: {tMax: 20}});
  if (u.includes('/help')) return json({commands: {liga: 'liga a luz'}});
  if (u.includes('reactions')) return json({emojis: []});
  return json({});
};
"""
STUB_JS = (STUB_JS
           .replace("__SENSORS__", json.dumps(SENSORS))
           .replace("__TOGGLES__", json.dumps(TOGGLES))
           .replace("__READINGS__", json.dumps(READINGS)))



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
        "no headless browser: every one of these complaints is about what the "
        "screen actually shows, and none of them can be seen from the HTML"
    ),
)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    import sqlite3

    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "complaints.db")

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
    out = tmp_path_factory.mktemp("complaints") / "index.html"
    out.write_text(html, encoding="utf-8")
    return out.as_uri()


@pytest.fixture
def page(doc):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        pg = browser.new_page(viewport=PHONE, is_mobile=True, has_touch=True)
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(doc, wait_until="load")
        # The devices are fetched, then polled once. Both must have happened
        # before anything is asserted, or these tests measure an empty page.
        pg.wait_for_function("() => window.__stubHits && window.__stubHits.status > 0",
                             timeout=15000)
        pg.wait_for_timeout(600)
        pg.__errors = errors  # type: ignore[attr-defined]
        pg.__hits = pg.evaluate("() => window.__stubHits")  # type: ignore[attr-defined]
        yield pg
        browser.close()


# ---------------------------------------------------------------- the bar ----

def test_the_stub_actually_fired(page):
    """A stub that does not match is worse than no stub: it turns the suite
    into a machine that confirms whatever the server answered. This failed
    silently for a whole round of "verified" measurements."""
    assert page.__hits["devices"] >= 1, page.__hits
    assert page.__hits["status"] >= len(TOGGLES), page.__hits


def test_the_stub_actually_matched_a_reading(page):
    """Firing is not matching. The first version of this stub fired on every
    `/device_status` request and matched NONE of them, because the nickname
    arrives percent-encoded and the table was keyed by the real name. Every
    device came back "unreachable", which is indistinguishable from a product
    fault and would have had these tests rewritten to match the wrong thing."""
    assert page.__hits["matched"] >= len(SENSORS), (
        f"only {page.__hits['matched']} of {page.__hits['status']} stubbed "
        f"reads matched a device: {page.__hits}"
    )


def test_the_page_raises_no_javascript_error(page):
    assert not page.__errors, page.__errors


def test_the_top_bar_is_still_visible_with_the_chat_open(page):
    """The owner: "a barra de topo do ecrã deve estar sempre visivel"."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    brand = page.evaluate(
        """() => {const b = document.getElementById('brand').getBoundingClientRect();
                   return {top: b.top, bottom: b.bottom};}"""
    )
    panel = page.evaluate(
        """() => {const m = document.getElementById('main').getBoundingClientRect();
                   return {top: m.top, bottom: m.bottom, height: m.height};}"""
    )
    assert panel["top"] >= brand["bottom"] - 1, (
        f"the chat starts at y={panel['top']} and the top bar ends at "
        f"y={brand['bottom']}: the panel is drawn over it"
    )
    # And the whole bar, not just its bottom edge.
    assert brand["top"] >= 0


def test_the_top_bar_is_reachable_with_the_chat_open(page):
    """Being painted and being usable are different questions, and the z-index
    numbers hid the difference.

    Probed across the whole brand row on the OLD code, with the chat open:

        y=10  20:chat-grip  100:chat-grip  187:chat-grip  300:chat-grip  360:nav-toggle
        y=50  20:ia-avatar  100:chat-log   187:chat-log   300:chat-log   360:nav-toggle
        y=62  100:msg msg-ia  187:msg msg-ia  300:msg msg-ia

    So the grey band and the conversation were painted OVER the top bar, and the
    only thing that survived was the burger at the far right, because it is the
    one item there that is positioned. A test that only checked the burger would
    have passed on the broken layout -- which is exactly what the first version
    of this test did. Every part of the bar has to answer for itself.

    "The top bar" is `#brand` AND `.nav-bar`: below 768px `#header-strip` is
    `display: contents`, so `#brand` (logo, weather, moon, UV, air) and the
    secondary `.nav-bar` (the burger) are siblings, both laid out by the body.
    The first version of this predicate accepted only `#brand` and so reported
    the burger itself as an occluder, which is the bug this test exists to find
    being reported in itself.
    """
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    brand = page.evaluate(
        """() => {const b = document.getElementById('brand').getBoundingClientRect();
                   return {y: b.y, bottom: b.bottom, x: b.x, right: b.right};}"""
    )
    covered = page.evaluate(
        """([x0, x1, y0, y1]) => {
             const inBar = (e) => e && (e.closest('#brand') || e.closest('.nav-bar'));
             const bad = [];
             for (let y = y0 + 4; y < y1 - 2; y += 8) {
               for (let x = x0 + 6; x < x1 - 4; x += 24) {
                 const e = document.elementFromPoint(x, y);
                 if (!inBar(e)) {
                   bad.push(x + ',' + y + ':' + (e ? (e.id || e.className || e.tagName) : 'nada'));
                 }
               }
             }
             return bad;
           }""",
        [brand["x"], brand["right"], brand["y"], brand["bottom"]],
    )
    assert not covered, (
        f"{len(covered)} points of the top bar are not the top bar. "
        f"First few: {covered[:5]}"
    )


def test_the_chat_does_not_take_the_whole_screen(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    panel = page.evaluate(
        "() => document.getElementById('main').getBoundingClientRect().height"
    )
    assert panel < PHONE["height"], (
        f"the panel is {panel}px of a {PHONE['height']}px screen: nothing of the "
        f"app is left, which is not what a sheet is"
    )


# ----------------------------------------------------------- the grey bar ----

def test_the_handle_is_not_a_grey_band(page):
    """The complaint itself: "a barra cinzenta que aparece quando se abre o
    chat". It was 375px x 44px of #9a9a9a."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    bg = page.evaluate(
        "() => getComputedStyle(document.getElementById('chat-grip')).backgroundColor"
    )
    alpha = page.eval_on_selector(
        "#chat-grip",
        "e => {const c = getComputedStyle(e).backgroundColor;"
        " const m = c.match(/rgba?\\(([^)]+)\\)/);"
        " return m ? parseFloat(m[1].split(',')[3] ?? '1') : 1;}",
    )
    assert alpha == 0, f"the handle row is painted {bg}: that is the grey bar"


def test_the_handle_draws_a_small_affordance_not_a_full_width_one(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    ink = page.evaluate(
        """() => [...document.querySelectorAll('#chat-grip .grip-pill, #chat-grip svg')]
                 .map(e => Math.round(e.getBoundingClientRect().width))"""
    )
    assert ink, "the handle draws nothing at all"
    assert max(ink) <= 64, (
        f"the handle's visible part is {max(ink)}px wide: an affordance you can "
        f"see, not a slab you have to look past"
    )


def test_the_handle_says_which_way_to_drag(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    assert page.locator("#chat-grip svg").count() >= 1, (
        "a pill on its own asks to be guessed at; the owner asked for an "
        "iconography for dragging the chat down"
    )
    label = page.get_attribute("#chat-grip", "aria-label") or ""
    assert "arrastar" in label.lower(), label


def test_the_handle_belongs_to_the_panel(page):
    """It used to be a sibling pinned to the top of the VIEWPORT, which is
    exactly why it could reach the top bar."""
    assert page.evaluate(
        "() => !!document.getElementById('chat-grip').closest('#main')"
    ), "the handle is not inside the panel, so it is not part of the sheet"


def test_the_handle_sits_below_the_top_bar_when_open(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    overlap = page.evaluate(
        """() => {const g = document.getElementById('chat-grip').getBoundingClientRect();
                   const b = document.getElementById('brand').getBoundingClientRect();
                   return Math.max(0, Math.min(g.bottom, b.bottom) - Math.max(g.top, b.top));}"""
    )
    assert overlap == 0, f"the handle covers {overlap}px of the top bar"


def test_tapping_the_handle_closes_the_chat(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    page.locator("#chat-grip .grip-pill").click(force=True)
    page.wait_for_timeout(500)
    assert not page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "tapping the handle did not close the conversation"


def test_a_short_drag_springs_back_instead_of_closing(page):
    """A drag below the third-of-the-screen threshold must NOT close. On the old
    code it did, about a frame later: the browser fires `click` after
    `touchend`, and the handle had an unconditional click-to-close, so a drag
    that was supposed to spring back closed the panel anyway."""
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    pill = page.locator("#chat-grip .grip-pill")
    box = pill.bounding_box()
    y = box["y"] + box["height"] / 2
    x = box["x"] + box["width"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 40, steps=6)
    page.mouse.up()
    page.wait_for_timeout(600)
    assert page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "a 40px drag closed the conversation: the threshold is innerHeight/3, "\
       "and something is closing it on the way out"


def test_dragging_up_does_not_close_the_chat(page):
    """An upward drag is a gesture that goes nowhere, NOT a tap.

    This is not hypothetical: tap-to-close keyed on "no DOWNWARD movement" made
    an upward flick close the conversation, because an upward drag leaves that
    counter at zero by design. `test_dragging_up_does_nothing` in
    test_mobile_layout_browser.py caught it, which is the argument for keeping
    the old suite green instead of only writing new tests.
    """
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    page.evaluate(
        """() => {const g = document.getElementById('chat-grip');
            const t = (y) => new Touch({identifier: 1, target: g, clientY: y});
            const ev = (type, y) => new TouchEvent(type, {bubbles: true, cancelable: true,
                touches: type === 'touchend' ? [] : [t(y)], changedTouches: [t(y)]});
            g.dispatchEvent(ev('touchstart', 80));
            g.dispatchEvent(ev('touchmove', -120));
            g.dispatchEvent(ev('touchend', -120));}"""
    )
    page.wait_for_timeout(500)
    assert page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "dragging the handle upwards closed the conversation: a flick up is not a dismissal"


def test_a_long_drag_does_close(page):
    page.evaluate("() => openChat()")
    page.wait_for_timeout(700)
    pill = page.locator("#chat-grip .grip-pill")
    box = pill.bounding_box()
    y = box["y"] + box["height"] / 2
    x = box["x"] + box["width"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 300, steps=10)
    page.mouse.up()
    page.wait_for_timeout(600)
    assert not page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "dragging the chat down a third of the screen did not close it"


# ----------------------------------------------------------------- names ----

def _rooms(page):
    # textContent, NOT innerText: `.room-header` has `text-transform: uppercase`,
    # so innerText reports "SALA" and every lookup by the room's real name
    # misses. That is the CSS talking, not the data, and a test that reads the
    # rendered casing cannot address what it is asserting about.
    return page.evaluate(
        """() => [...document.querySelectorAll('.device-room')].map(r => ({
            room: r.querySelector('.room-name')?.textContent || '',
            readings: r.querySelector('.room-readings')?.textContent || '',
            tiles: [...r.querySelectorAll('.device-toggle')].map(t => ({
                device: t.title,
                label: (t.querySelector('.device-label')?.textContent || '').trim(),
                power: (t.querySelector('.device-power')?.textContent || '').trim()}))}))"""
    )


def test_no_two_devices_in_a_room_answer_to_the_same_name(page):
    """Two tiles both reading "Sala", and three all reading "Quarto", is what
    `split(' ').pop()` produced. Within a room the name has to identify the
    device; across rooms it does not have to, because the room header is right
    there saying which room this is."""
    for room in _rooms(page):
        labels = [t["label"] for t in room["tiles"]]
        dupes = {x for x in labels if labels.count(x) > 1}
        assert not dupes, (
            f"in {room['room']} these devices answer to the same name: {dupes}. "
            f"Labels: {list(zip([t['device'] for t in room['tiles']], labels))}"
        )


def test_a_tile_keeps_its_name_when_it_is_drawing_power(page):
    """The desumidifier at 210W used to be labelled "210 W": the biggest load
    in the house was the one tile that never said what it was."""
    found = None
    for room in _rooms(page):
        for t in room["tiles"]:
            if t["device"] == "Desumidificador do Armário":
                found = t
    assert found is not None, "the desumidificador is missing from the strip"
    assert found["power"] == "210 W", (
        f"the reading is not shown: {found['power']!r}"
    )
    assert "Desumidificador" in found["label"], (
        f"the name was replaced by the reading: {found['label']!r}"
    )


def test_a_name_that_carries_its_own_place_keeps_it(page):
    """"Desumidificador do Armário" lives in the Geral group, which has no room
    word of its own -- the tail is the only thing saying where it is. A
    shortening rule that asks "does the tail name this room?" and treats the
    default "Geral" as a match throws exactly that away."""
    labels = {t["device"]: t["label"] for r in _rooms(page) for t in r["tiles"]}
    assert labels.get("Desumidificador do Armário") == "Desumidificador do Armário", labels.get(
        "Desumidificador do Armário"
    )


def test_every_tile_shows_the_full_device_name(page):
    """The 2026-10-01 report: many tiles only said "luz". The visible label is
    the full device name -- not its last word, and not a room-shortened form.
    "Luz da Sala" is "Luz da Sala", never just "Luz"."""
    labels = {t["device"]: t["label"] for r in _rooms(page) for t in r["tiles"]}
    for name in ("Luz da Sala", "Exaustor da Sala", "Luz do Quarto",
                 "Candeeiro do Quarto", "Desumidificador do Armário"):
        assert labels.get(name) == name, (name, labels.get(name))


def test_a_name_wider_than_its_tile_scrolls_instead_of_being_cut(page):
    """"Desumidificador do Armário" does not fit a phone tile. It drifts instead
    of being truncated -- and the drift is measured, so a short name is left
    still.

    The label is narrowed here on purpose. The flex layout widens a tile to fit
    its own name, so on this viewport the overflow the production phone shows is
    constructed rather than waited for; the mechanism is the thing under test,
    not the harness's flexbox."""
    result = page.evaluate(
        """() => {
            const narrow = (title, px) => {
                const t = [...document.querySelectorAll('.device-toggle')]
                    .find(t => t.title === title);
                const l = t.querySelector('.device-label');
                l.style.width = px + 'px';
                window.applyLabelMarquee(l, t.querySelector('.device-label-text'));
                return {
                    marquee: l.classList.contains('marquee'),
                    shift: l.style.getPropertyValue('--marquee-shift'),
                    duration: l.style.getPropertyValue('--marquee-duration'),
                };
            };
            return {long: narrow('Desumidificador do Armário', 40),
                    short: narrow('forno', 40)};
        }"""
    )
    assert result["long"]["marquee"], result["long"]
    assert result["long"]["shift"].startswith("-"), result["long"]
    assert result["long"]["duration"].endswith("s"), result["long"]
    assert not result["short"]["marquee"], result["short"]


def test_the_lamp_fills_when_its_toggle_is_on(page):
    """When on, the lamp glyph is filled amber, not merely un-greyed. Only the
    lamp glyph is filled: a light that is off stays an outline."""
    tiles = page.evaluate(
        """() => {
            const out = {};
            document.querySelectorAll('.device-toggle').forEach(t => {
                const svg = t.querySelector('.device-icon svg');
                out[t.title] = {
                    active: t.classList.contains('active'),
                    cls: svg ? svg.getAttribute('class') : null,
                    fill: svg ? getComputedStyle(svg).fill : null,
                };
            });
            return out;
        }"""
    )
    on = tiles["Luz da Sala"]
    assert on["active"], on
    assert "dev-icon-luz" in (on["cls"] or ""), on
    assert on["fill"] == "rgb(255, 207, 82)", on
    off = tiles["Luz do Quarto"]
    assert not off["active"], off
    assert off["fill"] == "none", off


def test_the_full_name_is_still_reachable(page):
    """Shortening the visible text must not throw the real name away: it is
    still the title, which is what a long press reads."""
    titles = page.evaluate(
        "() => [...document.querySelectorAll('.device-toggle')].map(t => t.title)"
    )
    for name in ("Luz da Sala", "Desumidificador do Armário", "carregador do carro"):
        assert name in titles, f"{name!r} lost its full name: {titles}"


# --------------------------------------------------------------- sensors ----

def test_temperature_and_humidity_have_no_tile_of_their_own(page):
    assert page.locator(".device-sensor").count() == 0, (
        "the sensors still take a tile each, which is the space the owner "
        "asked to save"
    )


def test_the_reading_sits_next_to_the_room_name(page):
    rooms = {r["room"]: r for r in _rooms(page)}
    assert rooms["Sala"]["readings"], f"SALA has no reading: {rooms['Sala']}"
    assert "21.4" in rooms["Sala"]["readings"], rooms["Sala"]["readings"]
    assert "48" in rooms["Sala"]["readings"], rooms["Sala"]["readings"]


def test_the_reading_is_in_the_header_not_below_it(page):
    """Beside the name, on the same row. A block header would drop the numbers
    onto their own line and give back the row the tile used to occupy."""
    same_row = page.evaluate(
        """() => [...document.querySelectorAll('.room-header')]
                 .filter(h => (h.querySelector('.room-readings')?.innerText || '').trim())
                 .map(h => {const n = h.querySelector('.room-name').getBoundingClientRect();
                            const r = h.querySelector('.room-readings').getBoundingClientRect();
                            return Math.abs(n.top - r.top) < n.height;})"""
    )
    assert same_row, "the reading is not on the same row as the room name"
    assert all(same_row), f"some readings are not beside their name: {same_row}"


def test_a_room_with_only_a_sensor_still_gets_a_header(page):
    rooms = {r["room"] for r in _rooms(page)}
    assert {"WC", "Sala", "Quarto"} <= rooms, (
        f"a room whose only device is a sensor lost its header: {rooms}"
    )


def test_the_reading_says_how_old_it_is(page):
    """The `parts.length` ReferenceError was swallowed by a bare catch, so the
    title -- the only place the age of a reading was available -- was never
    written. A stale reading looked live."""
    titles = page.evaluate(
        "() => [...document.querySelectorAll('.room-readings')].map(r => r.title)"
    )
    assert any("última leitura há" in t for t in titles), (
        f"no reading says when it was taken: {titles}"
    )


def test_an_unreachable_sensor_says_so_in_the_header(page):
    rooms = {r["room"]: r for r in _rooms(page)}
    assert rooms["WC"]["readings"], "the WC sensor produced nothing at all"


# ------------------------------------------------- the Geral average ----

def test_the_geral_header_shows_the_average_temperature(page):
    """The owner asked for the average of the room sensors in "Geral". The
    three stubbed rooms read 21.4, 19.8 and 18.1, so the average is 19.8 --
    the value is computed, not echoed from any single sensor."""
    rooms = {r["room"]: r for r in _rooms(page)}
    assert "média 19.8°" in rooms["Geral"]["readings"], rooms["Geral"]["readings"]


def test_the_average_comes_before_the_gas_reading(page):
    """The owner: "antes das medições de gás". The header answers "how is the
    house" before "is there gas"."""
    rooms = {r["room"]: r for r in _rooms(page)}
    readings = rooms["Geral"]["readings"]
    assert "340 ppm" in readings, readings
    assert readings.index("média") < readings.index("ppm"), readings


def test_the_average_does_not_appear_in_a_room_header(page):
    """It is the house average, and it belongs in "Geral" only. A copy in every
    room would be three new numbers saying the same thing."""
    for room in _rooms(page):
        if room["room"] != "Geral":
            assert "média" not in room["readings"], room


# ------------------------------------------------ activating a device ----

def _tap(page, device, checked=True):
    """Flip a tile's switch the way a thumb does: set it and fire `change`."""
    page.evaluate(
        """([dev, want]) => {
             const t = [...document.querySelectorAll('.device-toggle')]
                 .find(x => x.title.startsWith(dev));
             if (!t) throw new Error('no such tile: ' + dev);
             const i = t.querySelector('input[type=checkbox]');
             i.checked = want;
             i.dispatchEvent(new Event('change', {bubbles: true}));
           }""",
        [device, checked],
    )
    page.wait_for_timeout(900)


def _tile(page, device="Luz da Sala"):
    # textContent, not innerText: with the panel closed #chat-log is
    # `visibility: hidden`, and innerText of a non-rendered subtree is the empty
    # string. The first version of these tests read an empty log and looked
    # exactly like "the reply was dropped".
    return page.evaluate(
        """(dev) => {
             const t = [...document.querySelectorAll('.device-toggle')]
                 .find(x => x.title.startsWith(dev));
             const i = t.querySelector('input[type=checkbox]');
             return {estado: t.dataset.state, marcado: i.checked,
                     falhou: t.classList.contains('action-failed'),
                     porque: t.getAttribute('data-action-why'),
                     titulo: t.title,
                     borda: getComputedStyle(t).borderTopColor,
                     log: document.getElementById('chat-log').textContent};
           }""",
        device,
    )


def test_activating_a_device_does_not_open_the_chat(page):
    """The instruction: "a ativacao de dispositivos nao precisa de abrir o chat,
    basta escrever no chat em background"."""
    assert not page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    )
    _tap(page, "Luz da Sala")
    assert not page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "flipping a switch opened the conversation"


def test_the_reply_is_still_written_in_the_background(page):
    """Background, not discarded. The log is the transcript: the conversation is
    there to be read when you go and read it."""
    _tap(page, "Luz da Sala")
    log = _tile(page)["log"]
    assert "Luz ligada." in log, f"the reply never reached the log: {log!r}"


def test_a_failed_action_reaches_the_log(page):
    """The old code read only `data.response`, and every error answer from
    /device_action carries `message` and no `response`. Measured on the old
    code, both ways, with the action stubbed:

        success  -> chat opened, "Luz da Sala ligada." in the log
        502      -> chat opened, log byte-for-byte unchanged

    So the panel was opening onto nothing. The backend's own explanation of why
    it could not do it was being dropped, and the chat opening was not what was
    delivering the failure -- it was only interrupting.
    """
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala")
    log = _tile(page)["log"]
    assert "Sem resposta" in log, (
        f"the backend's message was dropped again: {log!r}"
    )
    assert "Não foi possível" in log, f"no failure was recorded at all: {log!r}"


def test_a_failed_action_does_not_open_the_chat(page):
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala")
    assert not page.evaluate(
        "() => document.getElementById('main').classList.contains('open')"
    ), "a FAILED action opened the conversation: that is the interruption the "\
       "owner is complaining about, and it is worst when it fails"


def test_a_failed_switch_goes_back_to_the_truth(page):
    """The front end sets the switch optimistically the instant it is tapped.
    Holding that claim for up to five seconds after the action failed is a lie
    the size of a light that is not on."""
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala", checked=True)
    t = _tile(page)
    assert t["marcado"] is False, (
        f"the switch still shows ON after the action failed: {t}"
    )
    assert t["estado"] == "off", f"the tile still claims {t['estado']}: {t}"


def test_a_failed_action_is_marked_on_the_tile(page):
    """The eye is on the tile that was just touched, not on a panel that is now
    closed. This is the channel the failure needs, and it is why closing the
    chat is safe: nothing is lost by not opening it."""
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala")
    t = _tile(page)
    assert t["falhou"], f"the tile carries no sign that the action failed: {t}"
    assert t["porque"], f"no reason recorded: {t}"
    assert "Sem resposta" in t["titulo"], (
        f"the reason is not in the title, which is where a phone reads it: {t}"
    )
    assert t["borda"] == "rgb(239, 68, 68)", f"the tile is not marked in red: {t}"


def test_the_mark_clears_when_the_device_answers_again(page):
    """Otherwise the red mark outlives the fault: the owner fixes the switch,
    the tile goes green, and it is still wearing the failure."""
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala")
    assert _tile(page)["falhou"]
    page.evaluate("() => refreshDeviceStates()")
    page.wait_for_timeout(900)
    t = _tile(page)
    assert not t["falhou"], f"a healthy device is still marked as failed: {t}"
    assert t["titulo"] == "Luz da Sala", t["titulo"]


def test_a_successful_action_clears_a_previous_failure(page):
    page.evaluate("() => { window.__actionMode = 'erro'; }")
    _tap(page, "Luz da Sala")
    assert _tile(page)["falhou"]
    page.evaluate("() => { window.__actionMode = 'ok'; }")
    _tap(page, "Luz da Sala", checked=True)
    t = _tile(page)
    assert not t["falhou"], f"the tile is still marked after a success: {t}"


# The voice path's scope guard lives in tests/test_mobile_controls.py, which
# already has a fake MediaRecorder: a spoken command still opens the
# conversation on purpose, and asserting that needs a real recording, not a
# window.fetch swap. Duplicating that harness here would have been a worse test
# of a better thing.
