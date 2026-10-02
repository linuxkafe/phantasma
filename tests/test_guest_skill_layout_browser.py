"""The guest skill picker, measured in a real browser.

The 23 checkboxes shipped twice before being looked at. First they rendered one
per row, a column so tall it pushed the save button off the screen. Then the
grid that replaced it was declared "compact" because a `grep` found
`display:grid` in the served HTML.

That grep was theatre, and this repository already knows the vocabulary for it:
`test_mobile_layout_browser.py` opens with "Every other UI test in this
repository asserts that a CSS *string* is present. That is theatre for layout."
This block is the guest half of that same argument. A string can be present and
mean nothing; a rectangle is the layout.

So the invariants here are measured on the rendered page, at desktop and at
phone width:

- the checkboxes form more than one column, so 23 skills do not become a
  23-row wall;
- the block is not taller than the viewport it was measured in, so the save
  button is reachable without hunting;
- every box is big enough to hit, which is the one thing a phone cares about
  and which no string assertion has ever checked for anything;
- no box overflows its column horizontally.

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

DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 375, "height": 667}  # the same phone the other layout test uses

# 44px, not a number I picked. `design.py` already states 44px as the practical
# touch floor (calling WCAG 2.2 AA the minimum) and enforces it on nav and form
# controls elsewhere in this same page. Picking 28 because "it looked fine" is
# how a 13px checkbox got shipped in the first place: nothing in the assertion
# disagreed with a screenshot.
#
# The 44px lands on the LABEL, and the box is measured through its parent, so
# this is "can you hit the row with your thumb", which is the real question.
MIN_TAP_PX = 44


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
        "no headless browser: this is the only test that can see the guest "
        "skill layout, and skipping it silently is how two bad versions of it "
        "shipped in a row"
    ),
)


def _screenshot(page, path):
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def picker_page(tmp_path_factory):
    """`/admin/config` with a real pipeline, so the 23 boxes actually render."""
    from src.api import admin as admin_mod
    from src.api import auth_store, ui_auth
    from src.api.routes import create_app

    # A pipeline stub, because the list under test comes from
    # `pipeline._skill_loader.skills`. Without one the page renders its
    # "no pipeline loaded" branch and there is nothing to measure -- which is
    # why passing pipeline=None was not a way to test this.
    class _StubLoader:
        def __init__(self, names):
            # NAME/TRIGGERS uppercase, as the real BaseSkill declares them.
            # A stub with lowercase `name`/`triggers` makes `_installed_skills`
            # return [] and the page renders its "no pipeline" branch -- the
            # stub then silently measures the wrong page. Same swallowed
            # exception, this time mine.
            # A LIST, like the real `SkillLoader.skills`. My first stub used a
            # dict, and `_installed_skills` iterated its KEYS -- strings, with
            # no .NAME -- so it returned [] and the page rendered "no pipeline
            # loaded". The stub measured the wrong page, silently.
            self.skills = [
                type("S", (), {"NAME": n, "TRIGGERS": [f"{n} um", f"{n} dois"]})()
                for n in names
            ]

    class _StubPipeline:
        def __init__(self, names):
            self._skill_loader = _StubLoader(names)

    names = [
        "skill_weather", "skill_calculator", "skill_chacon", "skill_tu Ya",
        "skill_dream", "skill_music", "skill_memory", "skill_cloogy",
        "skill_tuya", "skill_tasmota", "skill_shellygas", "skill_ewelink",
        "skill_feedback", "skill_system_stats", "skill_tapo", "skill_bareos",
        "skill_xiaomi", "skill_tts", "skill_gemini", "skill_discord",
        "skill_what_you_hear", "skill_ui", "skill_brennenstuhl",
    ]
    names[3] = "skill_tu ya"  # a name with a space, to catch naive splitting
    assert len(_StubPipeline(names)._skill_loader.skills) == 23

    db_path = os.path.join(tempfile.mkdtemp(), "layout.db")

    def _fresh():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # One script for the whole schema, so nothing races `CREATE TABLE` against
    # a table the schema file already declares.
    schema = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "schema.sql"
    )
    if os.path.exists(schema):
        ddl = open(schema, encoding="utf-8").read()
    else:
        ddl = (
            "CREATE TABLE users (email TEXT PRIMARY KEY, password_hash TEXT,"
            " role TEXT, is_active INTEGER, updated_at TEXT);"
            "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT,"
            " category TEXT, label TEXT, type TEXT);"
        )
    seed = _fresh()
    seed.executescript(ddl)
    seed.execute(
        "INSERT OR REPLACE INTO users (email, password_hash, role, is_active)"
        " VALUES ('b@t.test','x','admin',1)"
    )
    seed.commit()
    auth_store.ensure_schema(seed)
    seed.close()

    original = admin_mod.get_db_connection
    admin_mod.get_db_connection = _fresh
    try:
        app = create_app(pipeline=_StubPipeline(names))
        app.config["TESTING"] = True
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[ui_auth.SESSION_KEY] = "b@t.test"
            sess[ui_auth.ADMIN_SESSION_KEY] = "b@t.test"
        resp = client.get("/admin/config")
        assert resp.status_code == 200, resp.status_code
        html = resp.get_data(as_text=True)
    finally:
        admin_mod.get_db_connection = original

    assert 'name="guest_skill"' in html, "a página não renderizou nenhuma skill"

    out = tmp_path_factory.mktemp("guest") / "config.html"
    out.write_text(html, encoding="utf-8")
    return out.as_uri()


@pytest.fixture(scope="module")
def pw_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        yield browser
        browser.close()


def _boxes(page):
    return page.eval_on_selector_all(
        'input[name="guest_skill"]',
        """els => els.map(e => {
            const r = e.getBoundingClientRect();
            return {w: r.width, h: r.height, x: r.x, y: r.y,
                    value: e.value, checked: e.checked};
        })""",
    )


def test_the_boxes_spread_across_columns_and_stay_inside_the_screen(
    pw_browser, picker_page, tmp_path
):
    """23 boxes in more than one column, and not one box escaping its column.

    A single-column renderer also passes "fits horizontally", so the column count
    is the assertion that carries the weight here.
    """
    page = pw_browser.new_page(viewport=DESKTOP)
    page.goto(picker_page)

    boxes = _boxes(page)
    assert len(boxes) >= 20, f"esperava ~23 skills, veio {len(boxes)}"

    xs = sorted({round(b["x"]) for b in boxes})
    assert len(xs) >= 2, (
        f"as {len(boxes)} skills continuam numa coluna (x={xs}). Uma coluna "
        "com 23 caixas é o que foi enviado duas vezes."
    )

    _screenshot(page, tmp_path / "guest_desktop.png")

    overflow = [b["value"] for b in boxes if b["x"] < 0 or b["x"] + b["w"] > DESKTOP["width"]]
    assert not overflow, f"caixas fora do ecrã: {overflow}"
    page.close()


def test_every_box_is_big_enough_to_hit(pw_browser, picker_page, tmp_path):
    """A 28px floor on the rendered rect, not on the CSS.

    `width: 1em` and a 12px font produce a valid, present, green-by-string
    checkbox that a thumb will miss. This is the invariant no string assertion
    has ever covered.
    """
    page = pw_browser.new_page(viewport=DESKTOP)
    page.goto(picker_page)

    rows = page.eval_on_selector_all(
        'input[name="guest_skill"]',
        """els => els.map(e => {
            const row = e.closest('label') || e;
            const r = row.getBoundingClientRect();
            return {value: e.value, w: r.width, h: r.height};
        })""",
    )
    tiny = [(r["value"], round(r["h"], 1)) for r in rows if r["h"] < MIN_TAP_PX]
    assert not tiny, (
        f"{len(tiny)} linhas com menos de {MIN_TAP_PX}px de altura: {tiny}. "
        f"O `{MIN_TAP_PX}px` que o design system exige esta na mesma pagina e "
        f"nao chegou aqui -- e um clique de 13px numa captura parece-se com um "
        f"de 44px. So um rect medido diz a diferenca."
    )

    _screenshot(page, tmp_path / "guest_tap.png")
    page.close()


def test_the_block_does_not_grow_a_wall_on_a_phone(pw_browser, picker_page, tmp_path):
    """On a phone the boxes stack, but the save button stays on the page.

    Stacking to one column is correct at 375px. What is not correct is the
    checkbox column becoming so tall that the owner cannot see that they have to
    scroll to save. So the assertion is about the button being reachable, not
    about the block being short -- a 23-row phone column is fine as long as the
    page scrolls and the button is down there.
    """
    page = pw_browser.new_page(viewport=PHONE)
    page.goto(picker_page)

    boxes = _boxes(page)
    assert len(boxes) >= 20

    # No horizontal scrolling: the grid must not force the page wider than a phone.
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    assert scroll_w <= PHONE["width"] + 1, (
        f"a página tem {scroll_w}px de largura num ecrã de {PHONE['width']}px: "
        "algo não cabe e o dono teria de arrastar a página de lado."
    )

    # The boxes are in one column at this width, which is the intended reading.
    xs = sorted({round(b["x"]) for b in boxes})
    assert len(xs) == 1, f"em {PHONE['width']}px as skills deviam estar em coluna; x={xs}"

    # And the submit button is actually reachable by scrolling to it.
    submit = page.query_selector('button[type="submit"], input[type="submit"]')
    assert submit is not None, "não há botão de guardar para encontrar"
    submit.scroll_into_view_if_needed()
    in_view = page.evaluate(
        """() => { const r = document.querySelector(
                'button[type="submit"], input[type="submit"]').getBoundingClientRect();
            return r.top < innerHeight && r.bottom > 0; }"""
    )
    assert in_view, "o botão de guardar não aparece nem depois de scroll"

    _screenshot(page, tmp_path / "guest_phone.png")
    page.close()
