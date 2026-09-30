"""The desktop bar, measured against a screenshot the owner took.

Owner report, 2026-09-30, with a screenshot at 1920x880:

* "os svg das divisões estão enormes"
* "a barra com o svg do phantasma, e estado do tempo deveria estar na mesma
  no topo mas está ao lado"
* "a barra de chat nem aparece"

The screenshot is the reason this file exists rather than a description of it.
Three of these are invisible to a DOM assertion in the way they were written
before, because the thing that was wrong was a SIZE, an ALIGNMENT and a
CONTEXT -- not a missing element. `getBoundingClientRect().width > 0` passed
on a 247px house drawing.

Measured before the fix, at 1920x880:

    .room-header svg      247 x 247px     (a house the height of three tiles)
    #brand                 210px wide, centre-y=227
    .nav-bar                44px,  centre-y=22
    #chat-dock            1920 x  44px, display:block
    #chat-tab                0 x  0,  display:none
    #header-strip          455px tall
    #chat-log              550px, one message
    #main-weather-temp       clipped: "21" with a cut-off degree

And after:

    .room-header svg       16 x  16px
    #sky-stage            centre-y=28, .nav-bar centre-y=32   (one bar)
    #chat-dock              0 x  0,   display:none
    #header-strip          187px tall (was 455 -- 268px returned to the chat)
    #chat-input            72px tall (was 24px with a clipped placeholder)

The two structural causes, which are the reusable part:

* **`width: 15px` on the room icon lived only inside the phone media query.**
  On a desktop the inline SVG had no size of its own and took what the flex row
  gave it. A phone-sized fix that was never mirrored to the width it was
  written for.
* **`#chat-dock` was missing from the `display: none` list** that hides the
  phone-only affordances. Its only styling is inside the phone block, so on a
  desktop it fell back to `display: block` and sat under the composer as a 44px
  band with a microphone stranded in the corner -- "a barra de chat nem
  aparece" was "a barra de chat aparece e nao serve para nada".
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIDE = {"width": 1920, "height": 880}


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
    reason="no headless browser: every one of these is a size or an alignment",
)


@pytest.fixture(scope="module")
def page_html(tmp_path_factory):
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "widebar.db")

    def _fresh():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    seed = _fresh()
    seed.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,"
        " password_hash TEXT, role TEXT, is_active INTEGER, created_at TEXT,"
        " updated_at TEXT)"
    )
    seed.execute(
        "INSERT INTO users (email, password_hash, role, is_active, created_at,"
        " updated_at) VALUES ('u@t.test','x','user',1,'now','now')"
    )
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
            sess[ui_auth.SESSION_KEY] = "u@t.test"
            sess[ui_auth.ADMIN_SESSION_KEY] = "u@t.test"
        html = client.get("/").get_data(as_text=True)
    finally:
        admin_mod.get_db_connection = original
    out = tmp_path_factory.mktemp("widebar") / "index.html"
    out.write_text(html, encoding="utf-8")
    return out.as_uri()


# The device list has to be faked. Under file:// the page's fetch to
# /get_devices resolves against the FILESYSTEM, fails, and the room headers --
# with the icons under test -- are never built at all. A test that then reports
# "no room icon on the page" is measuring its own harness, which is the failure
# mode SD-phantasma-OPS-035 is about.
STUB_JS = """
window.fetch = async function (url) {
  const u = String(url);
  const raw = (u.split('nickname=')[1] || '').split('&')[0];
  let nick = raw;
  try { nick = decodeURIComponent(raw); } catch (e) {}
  const json = (o) => ({json: async () => o, ok: true, status: 200});
  if (u.includes('get_devices')) {
    return json({devices: {
      status: ['Sensor da Sala', 'Sensor do Quarto', 'Sensor do WC'],
      toggles: ['Luz da Sala', 'Luz do Quarto', 'Exaustor da Sala',
                'Exaustor do WC', 'Candeeiro do Quarto', 'Aspirador',
                'Desumidificador do Armario', 'forno']}});
  }
  if (u.includes('device_status')) {
    return json({state: 'on', temperature: 21.4, humidity: 48, power_w: 0,
                 age_s: 120});
  }
  if (u.includes('weather')) return json({today: {tMax: 21}, aqi: 40});
  if (u.includes('/help')) return json({commands: {liga: 'liga a luz'}});
  if (u.includes('reactions')) return json({emojis: []});
  return json({});
};
"""


@pytest.fixture
def wide(browser, page_html):
    ctx = browser.new_context(viewport=WIDE)
    ctx.add_init_script(STUB_JS)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(page_html, wait_until="load")
    pg.wait_for_function(
        "() => document.querySelectorAll('.room-header').length > 0", timeout=10000
    )
    pg.wait_for_timeout(400)
    yield pg
    ctx.close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


def _boxes(pg, selectors):
    return pg.evaluate(
        """(sels) => sels.reduce((a, s) => {
             const e = document.querySelector(s);
             if (!e) { a[s] = null; return a; }
             const r = e.getBoundingClientRect();
             const cs = getComputedStyle(e);
             a[s] = {x: Math.round(r.x), y: Math.round(r.y),
                     w: Math.round(r.width), h: Math.round(r.height),
                     display: cs.display};
             return a; }, {})""",
        selectors,
    )


# ------------------------------------------------------- the room icons ----

def test_the_room_icons_are_icon_sized(wide):
    """"os svg das divisões estão enormes". 247x247 in the screenshot."""
    assert wide.locator(".room-header svg").count() > 0, "no room icon on the page at all"
    sizes = wide.evaluate(
        "() => [...document.querySelectorAll('.room-header svg')]"
        ".map(e => Math.round(e.getBoundingClientRect().width))"
    )
    assert sizes, "no room icons rendered"
    assert max(sizes) <= 24, (
        f"a room icon is {max(sizes)}px wide: a house drawing the height of "
        f"three device tiles. Every one: {sizes}"
    )
    assert max(sizes) >= 8, f"the room icon is {max(sizes)}px: unreadable the other way"


def test_the_room_icon_rule_is_not_phone_only(wide):
    """The structural cause: `width: 15px` existed only under max-width:768px.

    Asserted on the stylesheet, because the browser test can only see the
    consequence. A rule that fixes a phone and is never mirrored to the width
    it was written for is invisible from the phone and enormous on a desktop.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "skills", "skill_ui.py",
    )
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    # Find the `.room-header svg` rule and check whether it sits inside a
    # media query by counting the braces opened before it.
    i = src.index(".room-header svg {")
    before = src[:i]
    opens = before.count("{") - before.count("}")
    assert opens <= 2, (
        "`.room-header svg` is nested {0} levels deep, so its width only "
        "applies inside a conditional block -- which is how the phone got 15px "
        "and the desktop got whatever the flex row felt like".format(opens)
    )


# ---------------------------------------------------------- the top bar ----

def test_the_sky_sits_with_the_brand_in_one_bar(wide):
    """"o estado do tempo deveria estar na mesma no topo mas está ao lado".

    It was not beside, it was 200px LOWER: #brand was a column with
    `justify-content: center` inside a 454px header, so the weather sat at
    centre-y=227 while the burger sat at centre-y=22.
    """
    b = _boxes(wide, ["#brand", "#brand-logo", "#sky-stage", ".nav-bar",
                      "#main-weather-icon", "#main-weather-temp"])
    assert b["#sky-stage"], "there is no sky stage at all"
    # Same line: the vertical centres are within a row's worth of each other.
    centres = {k: v["y"] + v["h"] / 2 for k, v in b.items()
               if k in ("#brand-logo", "#sky-stage", ".nav-bar")}
    spread = max(centres.values()) - min(centres.values())
    assert spread < 40, (
        f"the brand, the weather and the burger are spread over {spread:.0f}px "
        f"of vertical space: {centres}. They are meant to be one bar."
    )


def test_the_weather_is_not_clipped(wide):
    """In the screenshot the temperature read "21" with the degree cut off.

    `.sky-element` was a column, so the icon and the number stacked inside a
    narrow stage, and `#brand { overflow: hidden }` cut the bottom of the
    number. A number with half a degree sign is a wrong reading, not a
    cosmetic one.
    """
    fits = wide.evaluate(
        """() => {
             const t = document.getElementById('main-weather-temp');
             if (!t) return {ok: false, why: 'no element'};
             const stage = document.getElementById('sky-stage');
             const tr = t.getBoundingClientRect(), sr = stage.getBoundingClientRect();
             return {ok: tr.bottom <= sr.bottom + 1 && tr.top >= sr.top - 1,
                     temp: [Math.round(tr.y), Math.round(tr.height)],
                     stage: [Math.round(sr.y), Math.round(sr.height)]};
           }"""
    )
    assert fits["ok"], (
        f"the temperature does not fit inside the sky stage: temp {fits.get('temp')} "
        f"vs stage {fits.get('stage')}"
    )


def test_the_header_does_not_eat_the_screen(wide):
    """455px of 880 was the header. With the icon bug fixed it is 187.

    Not a fixed number to defend for its own sake -- the point is that the
    chat gets the rest, and that the strip is one bar's height rather than
    three tiles'.
    """
    b = _boxes(wide, ["#header-strip", "#chat-log"])
    assert b["#header-strip"]["h"] < 300, (
        f"the header is {b['#header-strip']['h']}px of {WIDE['height']}px: the "
        f"device strip is eating the page"
    )
    assert b["#chat-log"]["h"] > b["#header-strip"]["h"], (
        "the chat is smaller than the header it is the point of"
    )


# ---------------------------------------------------------- the chat bar ----

def test_the_phone_dock_does_not_exist_on_a_desktop(wide):
    """"a barra de chat nem aparece".

    It DID appear: 1920x44px, `display: block`, with a microphone stranded in
    the corner. `#chat-dock` was missing from the list that hides the
    phone-only affordances, and with no styling outside the phone block it
    fell back to the browser default.
    """
    b = _boxes(wide, ["#chat-dock", "#chat-tab", "#chat-grip"])
    for sel in ("#chat-dock", "#chat-tab", "#chat-grip"):
        assert b[sel]["display"] == "none", (
            f"{sel} is {b[sel]['display']} on a desktop: it is a phone "
            f"affordance and there is no rule to hide it here"
        )
        assert b[sel]["h"] == 0, f"{sel} still occupies {b[sel]['h']}px"


def test_the_composer_field_is_a_whole_line_tall(wide):
    """The placeholder was drawn through the middle of a 24px field.

    `height: 24px` with `padding: 12px` and `box-sizing: border-box` leaves no
    content box at all. It is visible in the screenshot as "Mensagem" cut in
    half, and `height > 0` was true.
    """
    box = _boxes(wide, ["#chat-input", "#chat-input-box"])
    assert box["#chat-input"]["h"] >= 40, (
        f"the message field is {box['#chat-input']['h']}px tall: the text is "
        f"drawn through the middle of it"
    )
    assert box["#chat-input"]["h"] <= box["#chat-input-box"]["h"], (
        "the field is taller than the row that holds it"
    )


def test_clearing_the_field_leaves_it_a_whole_line_tall(wide):
    """The JS pinned the reset height to '24px' too, so the field shrank after
    the first message and stayed that way for the rest of the session."""
    wide.fill("#chat-input", "liga a luz")
    wide.evaluate("() => { document.getElementById('chat-input').style.height = '60px'; }")
    wide.evaluate("() => { document.getElementById('chat-send').click(); }")
    wide.wait_for_timeout(400)
    h = wide.evaluate("() => document.getElementById('chat-input').getBoundingClientRect().height")
    assert h >= 40, (
        f"after sending, the field is {h}px tall: the reset height was pinned in "
        f"JavaScript as well as in the CSS"
    )


def test_no_javascript_error(wide):
    assert not wide.errors, wide.errors
