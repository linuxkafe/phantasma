"""The root page on a desktop, where it was falling apart.

Owner report, 2026-09-30: "em modo desktop o / fica todo partido ... e o menu
hamburger vem parar a meio do ecrã em vez de permanecer em cima, em Desktop deve
dar maior privilégio à horizontalidade".

Both symptoms were one defect, and neither was visible in the test suite.

**Two `</div>` too many.** One sat between `#brand` and `#devices`, the other
after the nav. Together they closed `#header-strip` before `#devices` and the
nav bar existed, so the browser's parser repaired the document by re-parenting:
`#devices` and `.nav-bar` became siblings of the strip and `#main` and
`#chat-dock` ended up at depth -1. Measured, counting tags with comments
stripped:

                      before        after
    <div> / </div>    30 / 32       30 / 30
    #devices depth    1             2      (child of #header-strip)
    .nav-bar depth    1             2      (child)
    #main depth       -1            1      (child of body)
    #chat-dock depth  -1            1      (child of body)

And what that cost, at 1280x900:

                      before        after
    #header-strip h   69            455     (brand | devices, one row)
    .nav-bar y        368           0       (top-right)
    menu top y        412           44      (under the bar, not mid-screen)

**Why no test caught it.** A browser does not reject a malformed document; it
invents a well-formed one. Every Playwright test in the suite asks questions of
the DOM the *browser* built, and the repaired DOM answers questions perfectly --
the elements exist, they have boxes, `elementFromPoint` returns them. The
misplaced burger was a real, visible, on-screen defect that twenty-odd passing
layout tests could not see, because the only defect was in the text and the
repair happened before the first assertion ran.

So the first test here counts tags. That is the only instrument that can see it.

**The second bug** was separate and equally silent: `#nav-menu` carried
`height: 100%` from the era when that id *was* the device strip. On an
absolutely positioned element `height: 100%` resolves against the 45px
`.nav-bar`, so the menu was a 44px window onto 188px of links --
`clientHeight 42`, `scrollHeight 188` -- and `elementFromPoint` at the centre of
every link returned something else. All five links were unclickable while
looking, in the DOM, and having a background.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHONE = {"width": 375, "height": 667}
DESKTOP = {"width": 1280, "height": 900}
WIDE = {"width": 1920, "height": 1080}


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
    reason="no headless browser: this is the only place the desktop layout can be seen",
)


@pytest.fixture(scope="module")
def page_html(tmp_path_factory):
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "desktop.db")

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
    out = tmp_path_factory.mktemp("desktop") / "index.html"
    out.write_text(html, encoding="utf-8")
    return html, out.as_uri()


@pytest.fixture(scope="module")
def browser():
    """A real context, not `browser.new_page()`.

    `new_page()` on a Browser creates an IMPLICIT context, and Playwright then
    refuses `context.new_page()` -- so a second viewport for the same page was
    impossible. The first version of the phone-regression test tried, and the
    failure was a Playwright API error rather than anything about the layout,
    which is the worst kind: a test that cannot run looks like a test that
    passed nothing."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


def _open(browser, uri, viewport, block_navigation=False):
    """A page at one viewport, in its own context so it can be closed.

    `block_navigation` cancels link clicks. A link under file:// resolves
    against the FILESYSTEM root, so clicking one tears the page down and there
    is nothing left to assert on. The behaviour under test is that the menu
    dismisses itself on the way, which is observable with the navigation
    cancelled -- and a cancelled navigation is closer to what a user sees than
    a completed trip to a page that does not exist on disk.
    """
    ctx = browser.new_context(viewport=viewport)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if block_navigation:
        pg.add_init_script(
            "document.addEventListener('click', e => {"
            "  const a = e.target.closest && e.target.closest('a');"
            "  if (a) e.preventDefault();"
            "}, true);"
        )
    pg.goto(uri, wait_until="load")
    pg.wait_for_timeout(1500)
    return pg, ctx


@pytest.fixture
def desktop(browser, page_html):
    _html, uri = page_html
    pg, ctx = _open(browser, uri, DESKTOP)
    yield pg
    ctx.close()


@pytest.fixture
def phone(browser, page_html):
    _html, uri = page_html
    pg, ctx = _open(browser, uri, PHONE)
    yield pg
    ctx.close()


@pytest.fixture
def desktop_no_nav(browser, page_html):
    _html, uri = page_html
    pg, ctx = _open(browser, uri, DESKTOP, block_navigation=True)
    yield pg
    ctx.close()


# ------------------------------------------------------- the tag balance ----

def _body_without_prose(html: str) -> str:
    """The body with scripts, styles AND HTML comments removed.

    Comments are the reason the first version of this measurement was wrong in
    both directions. This file's own prose contains the literal string `</div>`
    -- the very defect it is describing -- and a counter that does not strip
    comments reports a balanced document as broken and a broken one as
    differently broken. Measuring the explanation of the bug instead of the
    bug is the failure mode of every source-scanning test.
    """
    body = html[html.index("<body>"):]
    for pattern in (r"<script\b.*?</script>", r"<style\b.*?</style>", r"<!--.*?-->"):
        body = re.sub(pattern, "", body, flags=re.S | re.I)
    return body


def test_the_body_has_balanced_divs(page_html):
    """The test that would have caught it.

    A browser repairs a malformed document rather than refusing it, so every
    behavioural test downstream sees a DOM that is well-formed by then. This one
    looks at the text.
    """
    html, _uri = page_html
    body = _body_without_prose(html)
    opens = len(re.findall(r"<div\b", body, re.I))
    closes = len(re.findall(r"</div\s*>", body, re.I))
    assert opens == closes, (
        f"{closes} closing tags for {opens} opening ones. The browser repairs "
        f"this by re-parenting, which is how #devices and .nav-bar ended up as "
        f"siblings of #header-strip and #main ended up at depth -1."
    )


def test_no_closing_tag_arrives_before_its_opening_tag(page_html):
    """Balance alone is not enough: `</div></div><div>` also nets to zero."""
    html, _uri = page_html
    body = _body_without_prose(html)
    depth = 0
    for match in re.finditer(r"<div\b[^>]*>|</div\s*>", body, re.I):
        depth += -1 if match.group(0).lower().startswith("</") else 1
        assert depth >= 0, (
            f"an orphan </div> at offset {match.start()}: the document closes a "
            f"container that was never opened, and the parser has to invent one"
        )
    assert depth == 0


def test_the_regions_are_nested_where_the_css_expects_them(page_html):
    """`#header-strip` is a ROW on desktop: brand on the left, devices filling
    the rest, the burger in the bar. That only holds if all three are its
    children, and it is what "on desktop, favour horizontality" means here."""
    html, _uri = page_html
    body = _body_without_prose(html)

    def depth_of(needle: str) -> int:
        i = body.index(needle)
        before = body[:i]
        return len(re.findall(r"<div\b", before, re.I)) - len(
            re.findall(r"</div\s*>", before, re.I)

        )

    assert depth_of('id="devices"') == 2, (
        "#devices is not a child of #header-strip, so the desktop row collapses "
        "into a column: the brand on one line and the tiles on the next"
    )
    assert depth_of('class="nav-bar"') == 2, (
        "the nav bar is not inside #header-strip, which is why the menu's "
        "absolute positioning lost the ancestor it was written against and "
        "opened halfway down the screen"
    )
    assert depth_of('id="main"') == 1, "#main escaped the body"
    assert depth_of('id="chat-dock"') == 1, "#chat-dock escaped the body"


# --------------------------------------------------------- the desktop row ----

def test_the_top_bar_is_a_row_on_desktop(desktop):
    """Measured, 1280x900, after the fix: brand 210px, devices 1026px, both
    starting at y=0 in the same 455px strip. Before it, the strip was 69px --
    the brand row alone -- with the tiles on a line of their own underneath."""
    boxes = desktop.evaluate(
        """() => ['#header-strip', '#brand', '#devices', '.nav-bar'].reduce((a, s) => {
             const e = document.querySelector(s);
             if (!e) { a[s] = null; return a; }
             const r = e.getBoundingClientRect();
             a[s] = {x: Math.round(r.x), y: Math.round(r.y),
                     w: Math.round(r.width), h: Math.round(r.height)};
             return a; }, {})"""
    )
    assert boxes["#brand"] and boxes["#devices"], boxes
    assert boxes["#brand"]["y"] == boxes["#devices"]["y"], (
        f"the brand is at y={boxes['#brand']['y']} and the tiles at "
        f"y={boxes['#devices']['y']}: the header is a column, not a row"
    )
    assert boxes["#devices"]["x"] >= boxes["#brand"]["x"] + boxes["#brand"]["w"] - 1, (
        "the tiles start before the brand ends: they are stacked, not side by side"
    )
    assert boxes["#devices"]["w"] > boxes["#brand"]["w"], (
        f"the tiles have {boxes['#devices']['w']}px and the brand "
        f"{boxes['#brand']['w']}px: the screen is not being used horizontally"
    )


def test_the_burger_is_at_the_top_on_desktop(desktop):
    box = desktop.evaluate(
        """() => {const e = document.querySelector('.nav-bar');
                  if (!e) return null; const r = e.getBoundingClientRect();
                  return {x: Math.round(r.x), y: Math.round(r.y)};}"""
    )
    assert box, "there is no nav bar"
    assert box["y"] <= 2, f"the burger is at y={box['y']}, not at the top"


def test_there_is_no_horizontal_overflow_on_desktop(desktop):
    over = desktop.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
    )
    assert over["sw"] <= over["iw"] + 1, (
        f"the page scrolls sideways: {over['sw']}px of content in {over['iw']}px"
    )


# --------------------------------------------------------------- the menu ----

def test_the_menu_opens_at_the_top_and_not_halfway_down(desktop):
    """The owner's words: "vem parar a meio do ecrã em vez de permanecer em
    cima". Before the fix the panel opened at y=412 on a 900px screen -- 46% of
    the way down -- because the bar it anchors to had been re-parented to the
    middle of the page. After: y=44.

    The threshold is 15% of the viewport, not "less than half". A menu that
    hangs off a bar pinned to the top of the screen can only be near the top, and
    the first version of this test asserted `y < height/2` -- which the broken
    layout passed at 412 < 450. A threshold chosen to be safe is a threshold
    that does not test.
    """
    desktop.locator(".nav-toggle").first.click()
    desktop.wait_for_timeout(500)
    menu = desktop.evaluate(
        """() => {const e = document.getElementById('nav-menu');
                  const r = e.getBoundingClientRect();
                  return {y: Math.round(r.y), h: Math.round(r.height)};}"""
    )
    ceiling = DESKTOP["height"] * 0.15
    assert menu["y"] <= ceiling, (
        f"the menu opens at y={menu['y']}, which is "
        f"{menu['y'] / DESKTOP['height']:.0%} down a {DESKTOP['height']}px "
        f"screen: it is not hanging off the top of the page"
    )


def test_the_menu_is_not_clipped_to_nothing(desktop):
    """`#nav-menu` carried `height: 100%` from when that id was the device
    strip. On an absolutely positioned element that resolves against the 45px
    `.nav-bar`, and the panel became a 44px window onto 188px of links."""
    desktop.locator(".nav-toggle").first.click()
    desktop.wait_for_timeout(500)
    m = desktop.evaluate(
        """() => {const e = document.getElementById('nav-menu');
                  return {client: e.clientHeight, scroll: e.scrollHeight};}"""
    )
    assert m["client"] >= m["scroll"] - 2, (
        f"the menu shows {m['client']}px of {m['scroll']}px of links: most of "
        f"the menu is somewhere it cannot be seen or reached"
    )


def test_every_link_in_the_menu_can_actually_be_tapped(desktop):
    """Not "the link exists" -- that was true while all five were unclickable.
    The panel was 44px tall over 188px of content, so `elementFromPoint` at the
    centre of every link returned the panel instead."""
    desktop.locator(".nav-toggle").first.click()
    desktop.wait_for_timeout(500)
    links = desktop.evaluate(
        """() => [...document.querySelectorAll('#nav-menu a.nav-link')].map(a => {
             const b = a.getBoundingClientRect();
             const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
             return {text: a.textContent.trim().slice(0, 20),
                     reach: !!(hit && (hit === a || a.contains(hit)))};})"""
    )
    assert links, "the menu has no links at all"
    unreachable = [x["text"] for x in links if not x["reach"]]
    assert not unreachable, f"these menu links cannot be tapped: {unreachable}"


def test_the_menu_closes_when_a_link_is_tapped(desktop_no_nav):
    """Because it covers the page it navigates away from."""
    desktop_no_nav.locator(".nav-toggle").first.click()
    desktop_no_nav.wait_for_timeout(400)
    desktop_no_nav.locator("#nav-menu a.nav-link").first.click()
    desktop_no_nav.wait_for_timeout(400)
    assert not desktop_no_nav.evaluate(
        "() => document.getElementById('nav-menu').classList.contains('open')"
    ), "the menu stayed open over the page it just navigated to"


# ------------------------------------------------------------ no regressions ----

def test_the_phone_layout_is_untouched_by_the_desktop_fixes(phone):
    """The `#nav-menu` block that was removed had a mobile override of its own,
    and the tag fix moved #devices back inside #header-strip, which is
    `display: contents` below 768px. Both could have broken the phone, and the
    phone is the layout with fourteen tiles in it."""
    box = phone.evaluate(
        """() => {const b = document.getElementById('brand').getBoundingClientRect();
                   const d = document.getElementById('devices').getBoundingClientRect();
                   return {brandBottom: Math.round(b.bottom), devicesTop: Math.round(d.top),
                           devicesLeft: Math.round(d.x),
                           sw: document.documentElement.scrollWidth,
                           iw: window.innerWidth};}"""
    )
    assert box["devicesLeft"] == 0, (
        f"#devices starts at x={box['devicesLeft']} on a phone: a column must not "
        f"be pushed sideways, which is what a flex-basis written for a row does"
    )
    assert box["sw"] <= box["iw"] + 1, f"sideways scroll on a phone: {box}"


def test_no_javascript_error_on_desktop(desktop):
    assert not desktop.errors, desktop.errors
