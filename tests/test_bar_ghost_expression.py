"""The bar ghost answers the conversation, and the chat stops accumulating motion.

Two changes on owner instruction 2026-10-07, and both are asserted HERE, on a
rendered page, because both failure modes are invisible to a string check:

  * `.ia-avatar` lost its `floatGhost`. Every delivered message used to leave
    one PERMANENT animation behind -- the browser's own census reads exactly
    one infinite animation per avatar, linear in the length of the
    conversation. The test below counts the browser's animations rather than
    searching the CSS for a string, because the whole point is what the
    browser is actually RUNNING, and a stylesheet can declare an animation
    that never starts.

  * the top-bar ghost became the <ghost-avatar> component and its face now
    follows the last thing pHantasma said, while the weather keeps its tint.

A note on what is NOT claimed here, because it would be easy to imply it:
these tests do not prove the change makes the phone faster. It could not be
reproduced -- freezing the float moved the p95 frame gap by -0.1 ms at n=2 on
every Chromium available here, so the symptom the instruction came from is
still unexplained and may well live somewhere else. What these tests hold is
the mechanism: motion that does not accumulate, and a bar that reacts.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The fixtures come from the module, not from its names. Importing `page_html`
# directly and then naming a fixture parameter `page_html` reads as a
# redefinition of the import -- and the lint is not wrong, it just cannot see a
# pytest convention. Importing the module and referring to
# `layout.page_html` says plainly that the fixture is borrowed rather than
# shadowed, and it is what the sibling module already does.
# Only plain CONSTANTS are imported from the sibling module -- `STUB_JS`,
# which is a string used as a value. Its FIXTURES are not: a fixture defined in
# another module is only visible to modules that import it by name, and doing
# that while naming a fixture parameter the same thing trips the linter as a
# redefinition. `tests/test_mobile_controls.py` already solved this by
# rendering its own page, and this follows that convention rather than
# inventing a second way to do it.
from tests.test_mobile_layout_browser import (  # noqa: E402
    PHONE,
    STUB_JS,
    _browser_available,
)

pytestmark = pytest.mark.skipif(
    not _browser_available(),
    reason="no headless browser: only a rendered page shows what is running",
)

DOC = "document.querySelector('#brand-logo ghost-avatar')"


@pytest.fixture(scope="module")
def page_html(tmp_path_factory):
    """`/` rendered for a signed-in admin, with fetch stubbed, written to disk.

    The same shape as the layout module's fixture and for the same reason: the
    device tiles come from `/get_devices`, which sits behind the session gate,
    and a `file://` page carries no session, so a real page would render no
    tiles and the weather would never arrive.
    """
    import sqlite3
    import tempfile

    from skills import skill_ui
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    path = os.path.join(tempfile.mkdtemp(), "bar.db")

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
    """The rendered page on a phone, with the chat open.

    The chat is opened because the bar ghost is inside the header, which is
    always present, but the assertions that involve a delivered message need
    the log to exist and the sheet to have a real box -- the avatar measures
    0x0 while the drawer is closed, which is a layout fact and not a bug.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        pg = browser.new_page(
            viewport=PHONE, is_mobile=True, has_touch=True, device_scale_factor=3,
        )
        pg.goto(page_html)
        pg.wait_for_selector("ghost-avatar", state="attached")
        pg.evaluate("() => openChat()")
        pg.wait_for_selector("ghost-avatar", state="attached")
        pg.evaluate("() => openChat()")
        pg.wait_for_timeout(500)
        yield pg
        browser.close()


# --- the chat must not accumulate motion -------------------------------------

# The browser's own list of running animations. `getAnimations()` is the only
# honest source here: an `animation` declaration that never starts, or one on a
# display:none element, does not appear, and neither does a count that misses
# an element the compositor is genuinely driving.
_CHAT_ANIMATIONS = """
() => {
  const out = [];
  for (const a of document.getAnimations()) {
    const el = a.effect && a.effect.target;
    if (!el || !el.closest) continue;
    const row = el.closest('.ia-avatar');
    if (!row) continue;
    const timing = a.effect.getTiming ? a.effect.getTiming() : {};
    out.push({name: a.animationName || '?', infinite: timing.iterations === Infinity});
  }
  return out;
}
"""


def test_a_delivered_message_leaves_no_animation_behind(phone):
    """The leak itself: N messages must not mean N permanent animations.

    This is the assertion the change exists for. Before it, the count grew by
    one per message and never came down, because the row stays in the log for
    the whole session.
    """
    phone.evaluate("() => { document.getElementById('chat-log').innerHTML = ''; }")
    phone.evaluate("() => addToChatLog('Bem-vindo.', 'ia')")
    before = phone.evaluate(_CHAT_ANIMATIONS)

    for i in range(6):
        phone.evaluate(f"() => addToChatLog('Resposta {i}', 'ia')")
        phone.wait_for_timeout(40)

    after = phone.evaluate(_CHAT_ANIMATIONS)
    avatars = phone.evaluate(
        "document.querySelectorAll('.ia-avatar').length"
    )
    assert avatars >= 7, f"the messages did not land: {avatars} avatars"
    assert after == before == [], (
        f"the chat is running {len(after)} animation(s) after "
        f"{avatars} messages: {after}"
    )


def test_the_float_rule_is_gone_from_the_stylesheet(phone):
    """A direct check on the declaration, as well as on the runtime.

    The runtime assertion above would also pass if the rule had been neutered
    by something else. This one pins the cause: the rule that used to animate
    every avatar no longer declares a float. It is here to fail loudly if
    someone reintroduces the motion without noticing what it costs.
    """
    rules = phone.evaluate(
        """() => {
            const out = [];
            for (const sheet of document.styleSheets) {
                let rules;
                try { rules = sheet.cssRules; } catch (e) { continue; }
                for (const r of rules) {
                    if (r.selectorText && r.selectorText.includes('.ia-avatar')) {
                        out.push({selector: r.selectorText,
                                  animation: r.style.animation || '',
                                  name: r.style.animationName || ''});
                    }
                }
            }
            return out;
        }"""
    )
    assert rules, "no .ia-avatar rule found at all: the avatar is unstyled"
    offenders = [r for r in rules
                 if r["name"] and r["name"] != "none"
                 and "floatGhost" in r["name"]]
    assert offenders == [], (
        f".ia-avatar animates again: {offenders}"
    )


def test_no_rule_silently_lost_its_word_boundaries(phone):
    """The bug this file was written after, guarded directly.

    The first version of these rules was written with single-backslash word
    boundaries inside a Python triple-quoted string. Python decoded them to
    U+0008 and the boundaries vanished, so every rule matched almost nothing
    and the bar sat on `normal` for every reply. Nothing crashed, nothing
    warned, and a single happy-path test would have passed.

    So the guards are asserted on the COMPILED patterns rather than only on
    their behaviour: a rule must contain a word boundary, and a rule must not
    be able to match its own stem inside an unrelated longer word. `boa` is the
    sharpest probe -- it is a prefix of `boas`, `boato` and `bombordo`, and
    an unanchored `boa` matches all three.
    """
    patterns = phone.evaluate(
        "() => BAR_FACE_RULES.map(([name, re]) => [name, re.source])"
    )
    assert patterns, "BAR_FACE_RULES is empty: nothing can ever change the face"
    for name, source in patterns:
        assert "\\\\b" in source or source.startswith("\\b"), (
            f"the {name!r} rule has no word boundary left: {source!r} -- a "
            f"single-backslash boundary inside the Python string literal is "
            f"decoded to a backspace before the browser sees it"
        )
        assert "\x08" not in source, (
            f"the {name!r} rule contains U+0008 BACKSPACE: {source!r}"
        )

    # Behavioural half: the boundary has to do real work, not merely be
    # present in the source. Each haystack embeds a rule's stem inside a
    # LONGER word -- `erro` inside `erros`, `falh` inside `falhou` -- so an
    # unanchored match would light the face up on ordinary bad news.
    for stem, haystack in (("erro", "houve erros no sistema"),
                           ("falh", "a luz falhou")):
        got = phone.evaluate(f"() => faceForText({haystack!r})")
        assert got == "normal", (
            f"{haystack!r} put {got!r} on the bar: the {stem!r} rule matched "
            f"inside a longer word"
        )


def test_the_typing_row_still_moves(phone):
    """The other half of the instruction: delivered messages freeze, the
    typing row keeps breathing.

    Removing ALL motion would have been simpler, and it would have quietly
    reversed the owner's own decision from 2026-10-01 that the avatar should
    show it is working. The thinking row is created on demand and removed the
    moment the reply lands, so it cannot accumulate -- which is exactly why it
    is the one place motion is allowed to stay.
    """
    phone.evaluate("() => showTypingIndicator()")
    phone.wait_for_timeout(200)
    moving = phone.evaluate(
        """() => {
            const row = document.getElementById('typing-indicator-row');
            if (!row) return null;
            const el = row.querySelector('ghost-avatar');
            if (!el || !el.shadowRoot) return null;
            /* getComputedStyle inside the shadow root, not getAnimations():
               CSS animations declared in a shadow root are not reported by
               Document.getAnimations(), and an element's own getAnimations()
               did not surface them here either. This is the same detector
               test_ghost_avatar_browser.py already uses, so there is one
               method in the suite rather than two that can disagree. */
            const out = [];
            for (const n of el.shadowRoot.querySelectorAll('*')) {
                const s = getComputedStyle(n);
                if (s.animationName && s.animationName !== 'none') {
                    out.push(n.className.baseVal || n.tagName);
                }
            }
            return out;
        }"""
    )
    phone.evaluate("() => removeTypingIndicator()")
    assert moving, (
        "the typing row does not animate: the avatar can no longer show that "
        "pHantasma is working, which is what the owner asked for on 2026-10-01"
    )


def test_removing_the_typing_row_removes_its_motion(phone):
    """It has to be transient, not merely few. An animation left running after
    the row is removed would be the same leak this change exists to close."""
    phone.evaluate("() => showTypingIndicator()")
    phone.wait_for_timeout(150)
    phone.evaluate("() => removeTypingIndicator()")
    phone.wait_for_timeout(150)
    assert phone.evaluate(_CHAT_ANIMATIONS) == [], (
        "the typing row left an animation running after it was removed"
    )


# --- the bar ghost answers the last reply -------------------------------------


def test_the_bar_ghost_is_the_component_and_paints_a_face(phone):
    box = phone.evaluate(
        f"""() => {{
            const el = {DOC};
            if (!el || !el.shadowRoot) return null;
            const svg = el.shadowRoot.querySelector('svg');
            const r = el.getBoundingClientRect();
            return {{
                expr: el.getAttribute('expression'),
                w: Math.round(r.width), h: Math.round(r.height),
                svg: !!svg,
                painted: [...el.shadowRoot.querySelectorAll('.face')]
                    .filter(f => [...f.children].some(c => {{
                        const b = c.getBoundingClientRect();
                        return b.width + b.height > 0;
                    }})).map(f => f.dataset.f),
            }};
        }}"""
    )
    assert box is not None, "the bar ghost is not the <ghost-avatar> component"
    assert box["svg"], "the bar ghost has no SVG in its shadow root"
    assert box["w"] > 4 and box["h"] > 4, (
        f"the bar ghost is {box['w']}x{box['h']}px: it is invisible"
    )
    assert box["painted"] == ["normal"], (
        f"the bar ghost paints {box['painted']}, expected exactly one face"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Não foi possível desligar a luz: timeout", "error"),
        ("Não consegui falar com o dispositivo", "error"),
        ("Como assim? Já está ligado.", "confused"),
        ("Estou cansado, vou dormir", "sleepy"),
        ("Não acredito, que surpresa!", "surprised"),
        ("Obrigado, ficou perfeito!", "excited"),
        ("A luz do balcão está apagada.", "normal"),
        ("A temperatura na sala é 21 graus.", "normal"),
    ],
)
def test_the_bar_face_follows_the_tone_of_the_reply(phone, text, expected):
    """The mapping the owner asked for, asserted per case rather than once.

    Parametrised deliberately: a single happy-path assertion would pass for a
    classifier that always returns 'normal', which is the failure mode a
    lookup-style implementation has. Each row states what it expects, so a
    regression names the phrase that broke.
    """
    got = phone.evaluate(
        f"""() => {{
            addToChatLog({text!r}, 'ia');
            return {DOC}.getAttribute('expression');
        }}"""
    )
    assert got == expected, (
        f"{text!r} put {got!r} on the bar, expected {expected!r}"
    )


def test_the_weather_still_tints_the_bar_ghost(phone):
    """The part that would have been easy to break.

    The weather tint is a CSS filter on the HOST element, and the face is drawn
    in a shadow root. If those had stopped composing -- if the filter stopped
    reaching the shadow content, or the class were being reset somewhere --
    the bar would silently lose the sky, which it has always reported.
    """
    phone.evaluate("() => addToChatLog('Está bom o tempo.', 'ia')")
    state = phone.evaluate(
        """() => {
            const el = document.getElementById('brand-logo');
            const cs = getComputedStyle(el);
            return {cls: el.className, filter: cs.filter,
                    animation: cs.animationName};
        }"""
    )
    phone.evaluate("() => { const g = document.getElementById('brand-logo');"
                  " g.className = ''; g.classList.add('ghost-rain'); }")
    rain = phone.evaluate(
        "() => getComputedStyle(document.getElementById('brand-logo')).filter"
    )
    phone.evaluate("() => { const g = document.getElementById('brand-logo');"
                  " g.className = ''; g.classList.add('ghost-sun'); }")
    sun = phone.evaluate(
        "() => getComputedStyle(document.getElementById('brand-logo')).filter"
    )
    assert rain != sun, (
        "ghost-rain and ghost-sun resolve to the same filter on the bar: the "
        "weather no longer distinguishes them"
    )
    assert rain != "none" and sun != "none", (
        f"the weather tint is gone: rain={rain!r} sun={sun!r}"
    )
    # And it must not be a leftover from the fetch: put the neutral class back
    # so a later assertion is not reading state this test left behind.
    phone.evaluate("() => { const g = document.getElementById('brand-logo');"
                  " g.className = ''; g.classList.add('ghost-normal'); }")
    assert state["cls"], "the bar ghost has no class at all"


def test_the_weather_refresh_does_not_wipe_the_face(phone):
    """`updateWeather()` reassigns `className` outright. The face lives on an
    ATTRIBUTE of a child, so it must survive that reassignment -- if the face
    were ever stored in a class on `#brand-logo`, this poll would erase it and
    the brand would go back to neutral every ten minutes, which is a bug you
    would only notice if you happened to be looking."""
    phone.evaluate("() => addToChatLog('Não foi possível: timeout', 'ia')")
    before = phone.evaluate(f"() => {DOC}.getAttribute('expression')")
    phone.evaluate("""() => {
        const g = document.getElementById('brand-logo');
        g.className = '';
        g.classList.add('ghost-sun');
    }""")
    after = phone.evaluate(f"() => {DOC}.getAttribute('expression')")
    assert before == "error" and after == "error", (
        f"the weather poll changed the bar's face: {before!r} -> {after!r}"
    )


def test_a_user_message_does_not_move_the_bar(phone):
    """Only pHantasma's own words should move its face. The brand reacting to
    being TYPED AT would be a ghost answering itself."""
    phone.evaluate("() => addToChatLog('A luz está apagada.', 'ia')")
    settled = phone.evaluate(f"() => {DOC}.getAttribute('expression')")
    phone.evaluate("() => addToChatLog('não foi possível', 'user')")
    after = phone.evaluate(f"() => {DOC}.getAttribute('expression')")
    assert after == settled == "normal", (
        f"a user row moved the bar: {settled!r} -> {after!r}"
    )


def test_an_explicit_error_beats_the_text(phone):
    """A caller that knows the request failed passes 'error', and that must
    win over the classifier.

    The reason it has to win: `addToChatLog('Erro rede.', 'ia', 'error')` and
    a cheerful sentence are told apart by the CALLER, not by the words. If the
    classifier decided, a reply that happened to contain "obrigado" could put
    a smile on the face of a command that did not happen.
    """
    got = phone.evaluate(
        f"""() => {{
            addToChatLog('Não foi possível ler o sensor.', 'ia', 'error');
            return {DOC}.getAttribute('expression');
        }}"""
    )
    assert got == "error", f"the bar shows {got!r}, expected 'error'"


def test_the_chat_row_and_the_bar_can_disagree(phone):
    """They are two different contracts and are allowed to differ.

    The chat row shows the STATE of the request. The bar shows the TONE of the
    words. A failed command that apologised is `error` in the row and may be
    something else on the bar; a plain successful answer is `normal` in both.
    Collapsing them would have meant picking one meaning for two surfaces.
    """
    phone.evaluate("() => addToChatLog('Obrigado!', 'ia')")
    got = phone.evaluate(
        """() => {
            const rows = [...document.querySelectorAll('.msg-row.ia')];
            const row = rows[rows.length - 1].querySelector('ghost-avatar');
            return {row: row.getAttribute('expression'),
                    bar: document.querySelector('#brand-logo ghost-avatar')
                            .getAttribute('expression')};
        }"""
    )
    assert got["row"] == "normal", f"the row shows {got['row']!r}"
    assert got["bar"] == "excited", f"the bar shows {got['bar']!r}"
