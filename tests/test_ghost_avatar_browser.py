"""The ghost avatar, measured on the rendered page.

A string check cannot see this component. "The template is in the HTML" proves
nothing about whether a face paints, and the failure mode is silent and ugly: an
expression that matches no CSS rule leaves every face hidden and the chat shows
a blank space where the ghost was. So every assertion here asks the browser
whether geometry was actually painted -- `getBoundingClientRect` on the children
of each face group, which is zero for anything under `display: none`.

What is being held fixed:

  * ten expressions, each painting exactly one face, and no two at once;
  * an unknown, empty or missing expression painting `normal` -- never nothing;
  * the silhouette being the brand ghost's own `d`, so the character in the
    chat is the same ghost as the one on the bar and not a lookalike;
  * nothing inside the shadow root animating;
  * page CSS being unable to reach the face.

The last two are the non-negotiables of the system this was modelled on, and
both are silent regressions: an `animation` on a face would be invisible in a
screenshot diff, and a page rule that leaked in would look like a styling
accident rather than a broken boundary.

Skipped, loudly, when no browser is installed -- a render test that quietly
reports nothing reads as coverage.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PHONE = {"width": 375, "height": 667}

STUB_JS = """
window.fetch = async function (url) {
  const u = String(url);
  if (u.includes('get_devices')) return {json: async () => ({devices: {
      status: ['Sensor da Sala'],
      toggles: ['Luz do Quarto', 'luz do balcão']}})};
  if (u.includes('device_status')) return {json: async () => ({
      state: 'on', power_w: 42.0, reading_suspect: false, series: [1,2,3],
      state_source: 'api'})};
  if (u.includes('weather')) return {json: async () => ({temp: 18})};
  if (u.includes('/help')) return {json: async () => ({comandos: ['liga a luz']})};
  if (u.includes('reactions')) return {json: async () => ({reactions: []})};
  return {json: async () => ({}), ok: true};
};
"""

# Paint, not string-match: a face counts as drawn when at least one of its
# shapes has a non-zero box. `width + height`, not `width AND height`:
# `display: none` zeroes both dimensions, so the sum still detects it, while
# a deliberately flat face -- `loading` is three horizontal lines -- has a real
# width and no height at all and would be misread as missing.
_FACES = """
() => {
  const el = document.querySelector('ghost-avatar');
  if (!el || !el.shadowRoot) return null;
  return [...el.shadowRoot.querySelectorAll('.face')].map(f => {
    const painted = [...f.children].some(c => {
      const r = c.getBoundingClientRect();
      return r.width + r.height > 0;
    });
    return {name: f.dataset.f, painted};
  });
}
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
    reason="no headless browser: only a rendered page can show whether a face paints",
)


@pytest.fixture(scope="module")
def page_html(tmp_path_factory):
    """`/` rendered for a signed-in admin, with fetch stubbed, written to disk."""
    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "avatar.db")

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
def page(page_html):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        pg = browser.new_page(viewport=PHONE, device_scale_factor=3)
        pg.goto(page_html)
        # The chat is a closed drawer at rest, so a `visible` wait here would
        # block forever on a ghost that is present but folded away. Attach only,
        # then open the sheet so there is a real box to measure.
        pg.wait_for_selector("ghost-avatar", state="attached")
        pg.evaluate("() => openChat()")
        pg.wait_for_timeout(400)
        # The component upgrades synchronously on insertion, but the shadow root
        # is only there once connectedCallback has run.
        pg.wait_for_function(
            "!!document.querySelector('ghost-avatar')?.shadowRoot?.querySelector('.face')"
        )
        yield pg
        browser.close()


def _set_expression(page, value):
    """Set (or clear) the attribute on the first avatar and read what painted."""
    return page.evaluate(
        """(v) => {
            const el = document.querySelector('ghost-avatar');
            if (v === null) el.removeAttribute('expression');
            else el.setAttribute('expression', v);
            const faces = [...el.shadowRoot.querySelectorAll('.face')];
            return faces.map(f => {
                const painted = [...f.children].some(c => {
                    const r = c.getBoundingClientRect();
      return r.width + r.height > 0;
                });
                return {name: f.dataset.f, painted};
            });
        }""",
        value,
    )


def test_the_chat_avatar_is_the_component(page):
    """The greeting already put one in the chat, so the page proves itself."""
    count = page.evaluate("document.querySelectorAll('ghost-avatar').length")
    assert count >= 1, "no <ghost-avatar> in the chat: the ghost was not swapped in"


def test_there_are_exactly_ten_expressions(page):
    faces = page.evaluate(
        "[...document.querySelector('ghost-avatar').shadowRoot"
        ".querySelectorAll('.face')].map(f => f.dataset.f)"
    )
    assert len(faces) == 10, f"expected ten faces, found {len(faces)}: {faces}"
    assert len(set(faces)) == 10, f"duplicate expression names: {faces}"


def test_the_css_and_the_javascript_agree_on_the_names(page):
    """Two lists is two sources of truth. The CSS is the authority for what a
    name means; the array exists only for the tests, so a face added to one and
    not the other is a bug worth failing on."""
    in_markup = page.evaluate(
        "window.__GHOST_EXPRESSIONS__.slice().sort()"
    )
    in_dom = page.evaluate(
        "JSON.stringify([...document.querySelector('ghost-avatar').shadowRoot"
        ".querySelectorAll('.face')].map(f => f.dataset.f).sort())"
    )
    import json

    assert in_markup == sorted(json.loads(in_dom)), (
        "the expression list in JS and the faces in the template disagree"
    )


@pytest.mark.parametrize(
    "name",
    ["normal", "wink", "happy", "thinking", "surprised",
     "confused", "sleepy", "excited", "error", "loading"],
)
def test_each_expression_paints_exactly_its_own_face(page, name):
    faces = _set_expression(page, name)
    painted = [f["name"] for f in faces if f["painted"]]
    assert painted == [name], f"{name!r} painted {painted}, expected exactly [{name!r}]"


@pytest.mark.parametrize("value", [None, "", "   ", "foobar", "NORMAL", "winking"])
def test_an_unusable_expression_falls_back_to_normal_and_never_blanks(page, value):
    """The contract, and the reason it is worth a test: a malformed attribute
    must not produce an empty space where the ghost was. Case matters, so
    `NORMAL` is an unknown name rather than a synonym."""
    faces = _set_expression(page, value)
    painted = [f["name"] for f in faces if f["painted"]]
    assert painted == ["normal"], f"{value!r} painted {painted}, expected ['normal']"


def test_surrounding_whitespace_is_tolerated(page):
    """Padding is trimmed rather than treated as an unknown name. An
    expression arriving from a template or a query string with a stray space
    should still be the expression that was asked for, and not a silent
    downgrade to normal."""
    faces = _set_expression(page, "  wink  ")
    painted = [f["name"] for f in faces if f["painted"]]
    assert painted == ["wink"], f"'  wink  ' painted {painted}, expected ['wink']"


def test_the_paint_detector_can_actually_detect_a_blank_ghost(page):
    """A test that passes for the wrong reason is worse than no test. This hides
    every face and insists the detector reports nothing drawn, which is the
    state the whole component exists to prevent. If this ever goes green while
    the faces are hidden, the predicate above has rotted into always-true and
    every other assertion in this file is vacuous."""
    hidden = page.evaluate(
        """() => {
            const el = document.querySelector('ghost-avatar');
            const style = document.createElement('style');
            style.textContent = '.face { display: none !important; }';
            el.shadowRoot.appendChild(style);
            const faces = [...el.shadowRoot.querySelectorAll('.face')];
            return faces.map(f => {
                const painted = [...f.children].some(c => {
                    const r = c.getBoundingClientRect();
                    return r.width + r.height > 0;
                });
                return painted;
            });
        }"""
    )
    assert hidden and not any(hidden), (
        f"the detector claims a face is drawn while every face is display:none: {hidden}"
    )


def test_the_ten_faces_are_visually_distinct(page):
    """Distinct markup, because two identical faces would be two names for one
    drawing and the difference would only be visible in the source."""
    sigs = page.evaluate(
        "[...document.querySelector('ghost-avatar').shadowRoot"
        ".querySelectorAll('.face')].map(f => f.innerHTML)"
    )
    assert len(set(sigs)) == 10, "two or more expressions draw the same face"


def test_the_silhouette_is_the_brand_ghosts_own_path(page):
    """The same character. The component's body `d` is compared against the
    brand ghost's, so 'the ghost in the chat' cannot quietly become a lookalike."""
    brand_d = page.evaluate(
        "(() => { const m = GHOST_SVG.match(/<path d=\"([^\"]+)\"/); return m && m[1]; })()"
    )
    body_d = page.evaluate(
        "document.querySelector('ghost-avatar').shadowRoot"
        ".querySelector('.ghost-body').getAttribute('d')"
    )
    assert brand_d, "could not read the brand ghost's path out of the page"
    assert body_d == brand_d, (
        "the chat avatar's body is not the brand ghost's path:\n"
        f"  brand:   {brand_d}\n"
        f"  avatar:  {body_d}"
    )


def _animating_nodes(page, expression):
    """Every element inside the shadow root that CSS is animating right now."""
    page.evaluate(
        "(v) => { document.querySelector('ghost-avatar').setAttribute('expression', v); }",
        expression,
    )
    return page.evaluate(
        """() => {
            const el = document.querySelector('ghost-avatar');
            const out = [];
            for (const n of el.shadowRoot.querySelectorAll('*')) {
                const s = getComputedStyle(n);
                if (s.animationName && s.animationName !== 'none') {
                    const label = n.className.baseVal || n.className || n.tagName;
                    out.push(label + ' -> ' + s.animationName);
                }
            }
            return out;
        }"""
    )


def test_only_the_thinking_face_may_move(page):
    """Reversed on owner instruction, 2026-10-01, and deliberately narrowed.

    This used to assert that NOTHING inside the shadow root animates, borrowed
    as a non-negotiable from another system. The owner asked for the avatar to
    show that it is composing a reply, so motion is now allowed -- but only on
    the `thinking` face, which only the typing row ever sets.

    Every other expression is still frozen, and that is the part worth keeping:
    the rule is no longer "no motion anywhere", it is "motion must name the
    state that causes it". An animation on `.face`, on the host, or on any other
    expression would run forever on every delivered message, and this fails.
    """
    for expression in ("normal", "wink", "happy", "surprised", "confused",
                       "sleepy", "excited", "error", "loading"):
        offenders = _animating_nodes(page, expression)
        assert offenders == [], (
            f"expression {expression!r} is animated: {offenders}"
        )

    assert _animating_nodes(page, "thinking"), (
        "the thinking face does not animate: the avatar cannot show that it is "
        "working, which is what the owner asked for"
    )


def test_the_thinking_motion_is_a_transform_and_not_geometry(page):
    """A still screenshot must be indistinguishable from the frozen ghost.

    Animating width/height/top would move the painted box, so the avatar would
    breathe in screenshots, in print, and for anyone reading the DOM's
    geometry. Transform keeps every box exactly where the frozen artwork put it.
    """
    page.evaluate(
        "() => document.querySelector('ghost-avatar').setAttribute('expression', 'thinking')"
    )
    offenders = page.evaluate(
        """() => {
            const el = document.querySelector('ghost-avatar');
            const props = new Set();
            for (const sheet of el.shadowRoot.styleSheets) {
                for (const rule of sheet.cssRules) {
                    if (!rule.style || !rule.style.animationName ||
                        rule.style.animationName === 'none') continue;
                    for (const p of rule.style) {
                        if (p.startsWith('--')) continue;
                        // Any animation-* longhand is how the shorthand
                        // expands; it says nothing about geometry.
                        if (p.startsWith('animation')) continue;
                        if (p === 'transform' || p === 'transform-box' ||
                            p === 'transform-origin') continue;
                        props.add(p);
                    }
                }
            }
            return [...props];
        }"""
    )
    assert offenders == [], f"the thinking animation moves geometry: {offenders}"


def test_reduced_motion_still_shows_a_thinking_face(page):
    """The state stays legible without the movement.

    With motion off the expression still changes, so the owner can still tell
    the assistant is working -- it is the same avatar in the same row.
    """
    page.emulate_media(reduced_motion="reduce")
    try:
        assert _animating_nodes(page, "thinking") == [], (
            "prefers-reduced-motion did not stop the thinking animation"
        )
        faces = _set_expression(page, "thinking")
        painted = [f["name"] for f in faces if f["painted"]]
        assert painted == ["thinking"], (
            f"with reduced motion the thinking face is not painted: {painted}"
        )
    finally:
        page.emulate_media(reduced_motion="no-preference")


def test_page_css_cannot_reach_the_face(page):
    """The shadow root is the boundary. This proves it against a hostile rule
    rather than trusting that shadow DOM works."""
    page.add_style_tag(
        content=".face { display: none !important; } .ghost-body { display: none !important; }"
    )
    faces = _set_expression(page, "wink")
    painted = [f["name"] for f in faces if f["painted"]]
    assert painted == ["wink"], (
        f"page CSS leaked into the shadow root: painted {painted}"
    )


def test_the_theming_variables_are_reachable_and_nine(page):
    """CSS custom properties pierce shadow DOM, which is what makes per-theme
    colour possible without touching the markup. Counted, because a tenth
    variable appearing later should be a decision and not a drift."""
    names = page.evaluate(
        """() => {
            const el = document.querySelector('ghost-avatar');
            el.style.setProperty('--avatar-body', 'rgb(1, 2, 3)');
            const cs = getComputedStyle(el);
            return {
                declared: el.shadowRoot.querySelector('style').textContent
                            .match(/--avatar-[a-z-]+(?=\\s*:)/g) || [],
                reachesSvg: cs.getPropertyValue('--avatar-body').trim(),
            };
        }"""
    )
    declared = names["declared"]
    assert len(set(declared)) == 9, f"expected nine --avatar-* variables, got {declared}"
    assert names["reachesSvg"] == "rgb(1, 2, 3)", (
        "setting --avatar-body on the host did not reach the component"
    )


def test_the_thinking_row_appears_while_the_house_works(page):
    """`thinking` has to be a state the chat can actually be in, not a face
    nobody ever shows."""
    page.evaluate("showTypingIndicator()")
    got = page.evaluate(
        """() => {
            const row = document.getElementById('typing-indicator-row');
            const el = row && row.querySelector('ghost-avatar');
            return el ? el.getAttribute('expression') : null;
        }"""
    )
    assert got == "thinking", f"the typing row shows {got!r}, expected 'thinking'"
    page.evaluate("removeTypingIndicator()")


def test_a_failed_command_shows_the_error_face(page):
    page.evaluate("addToChatLog('Não foi possível desligar a luz: timeout', 'ia', 'error')")
    got = page.evaluate(
        """() => {
            const rows = [...document.querySelectorAll('.msg-row.ia')];
            const el = rows[rows.length - 1].querySelector('ghost-avatar');
            return el ? el.getAttribute('expression') : null;
        }"""
    )
    assert got == "error", f"a failed command shows {got!r}, expected 'error'"


def test_a_normal_answer_gets_the_normal_face(page):
    page.evaluate("addToChatLog('A luz do balcão está apagada.', 'ia')")
    got = page.evaluate(
        """() => {
            const rows = [...document.querySelectorAll('.msg-row.ia')];
            const el = rows[rows.length - 1].querySelector('ghost-avatar');
            return el ? el.getAttribute('expression') : null;
        }"""
    )
    assert got == "normal", f"a normal answer shows {got!r}, expected 'normal'"


def _box_once(page):
    return page.evaluate(
        """() => {
            const el = document.querySelector('ghost-avatar');
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {w: r.width, h: r.height};
        }"""
    )


def test_the_avatar_is_legible_at_the_size_the_chat_uses(page):
    """22px is what `.ia-avatar` renders. Below that the face stops being a
    face, and that is a measured fact rather than an opinion."""
    # The fixture waits 400 ms after `openChat()` and that is a guess about how
    # long the sheet takes to become visible. It is a guess that fails on its
    # own terms: the chat is a drawer, so before it is laid out the avatar is
    # `display:none` and `getBoundingClientRect()` returns 0x0 -- and this test
    # then blames the design for a timing problem. That is not rare, it is
    # intermittent, and it has failed the deploy gate twice on 2026-10-05 while
    # passing in isolation, which is the signature of a race rather than a
    # regression.
    #
    # So wait for the thing being measured instead of a duration: a real box.
    # The 22 px assertion is untouched, and a genuinely too-small avatar still
    # fails -- it just fails with a number that means something.
    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector('ghost-avatar');
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }""",
            timeout=5000,
        )
    except Exception:
        pass  # fall through: the assertion below reports the real measurement

    box = _box_once(page)
    assert box is not None and box["w"] > 0 and box["h"] > 0, (
        f"the avatar has no box to measure: {box}"
    )
    assert box["w"] >= 22 and box["h"] >= 22, (
        f"the avatar renders at {box['w']}x{box['h']}px, which is below the 22px "
        "the design calls legible"
    )
