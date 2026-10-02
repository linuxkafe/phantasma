"""The top line of /admin/brain is ONE line, and nothing paints over it.

T058. The page used to stack four translucent bars at the top and let z-index
decide who was visible. Measured on production at 1440x900:

    .nav-bar      y=0..45    z50    the admin nav, collapsed to a burger
    #topbar       y=9..48    --     the 3D view's OWN bar, inside the iframe
    .brain-tabs   y=45..97   z60    "C POLICY | Grafo 3D | Tudo | Ecra inteiro"
    .sleep-bar    y=12..129  z40    <- declared top:12 and still underneath both

The sleep bar is the one the owner called "a barra de topo", and it lost: 32 of
its 117 pixels were visible, and `document.elementFromPoint` over the one number
it carried ("Conceitos") returned the "Ecra inteiro" link of the tab row above
it. A control can be visible, enabled, and belong to something else.

So these tests assert the replacement, not the fix. The contract is now
structural rather than numeric: one flex row on top of the hub, in normal flow,
with the graph taking the space below it. A bar that is `position:fixed` over a
full-bleed hub cannot be asserted by z-index without re-introducing the exact
race that broke -- so the tests here measure geometry and hit-test, and the one
thing they forbid is an overlap.

Server: its own, built like tests/test_brain_hub_clickables.py:100. Never :5000.
That is /opt/phantasma -- production, a separate copy -- so a test pointed there
would be testing last night's deploy and would keep passing after a regression
landed here.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import admin as admin_mod  # noqa: E402

# 375 is here on purpose: it is the width at which the top line wrapped to two
# rows, which is the failure this file exists to prevent. 1916x895 is the
# owner's reported screen.
SIZES = ((375, 667), (900, 700), (1440, 900), (1916, 895))

# One row, whatever the content: the burger is a 44px touch target and the tabs
# are 28px, so 56 is the tallest honest single row. Two rows measured 66px.
MAX_TOP_LINE_H = 56


@pytest.fixture
def brain_db(tmp_path, monkeypatch):
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    db = tmp_path / "brain.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.execute(
        "INSERT INTO memories (id, timestamp, text) VALUES"
        " (1, '2026-01-01', '{\"summary\": \"uma memoria\", \"tags\": []}')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (1, 'node:alfa', 'node', 'alfa', 'memory', 3.0, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.execute(
        "INSERT INTO memory_graph"
        " (id, node_key, node_type, label, source, target, affinity, weight,"
        "  touch_count, created_at, updated_at)"
        " VALUES (2, 'edge:1->alfa', 'edge', 'alfa', 'memory', 'alfa', 1.0, 1.0, 1,"
        " '2026-01-01', '2026-01-01')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(admin_mod, "BRAIN_DB_PATH", Path(db))
    from src.brain import graph_edit

    graph_edit.ensure_schema(str(db))
    return db


@pytest.fixture
def brain_server(brain_db, monkeypatch):
    """A real HTTP server on an ephemeral port, for the same reason as the
    sibling file: the click has to travel over a real origin."""
    from werkzeug.serving import make_server

    from src.api.routes import create_app

    ident = {"role": "admin", "email": "owner@example.invalid"}
    monkeypatch.setattr(admin_mod, "_current_user_data", lambda: ident)
    monkeypatch.setattr(admin_mod, "_current_user", lambda: "owner@example.invalid")
    monkeypatch.setattr(admin_mod, "_bypass_or_none", lambda: ident)

    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def _pw():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch(args=["--no-sandbox"]).close()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"chromium unavailable: {exc}")
    return sync_playwright


@pytest.fixture(scope="module")
def browser(_pw):
    with _pw() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


def _open(browser, server, width, height, open_drawer=False):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{server}/admin/brain", wait_until="domcontentloaded")
    page.wait_for_selector("#brain-inspect-toggle")
    if open_drawer:
        page.click("#brain-inspect-toggle")
        page.wait_for_selector("#brain-inspect.is-active")
    page.wait_for_timeout(700)
    return page


# --------------------------------------------------------------------------
# The measurement. One evaluate, one round trip, all the boxes we reason about.
# --------------------------------------------------------------------------
GEOMETRY = """() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            top: Math.round(r.top), bottom: Math.round(r.bottom)};
  };
  const q = (s) => document.querySelector(s);
  const owner = (el) => el ? (el.parentElement.className || el.parentElement.tagName) : null;

  const topbar = q('.brain-topbar');
  const bar  = q('.sleep-bar');
  const stat = q('.sleep-stat');
  const tabs = q('.brain-tabs');
  const nav  = q('.nav-bar');

  // What the browser would actually click, at the centre of each box.
  const hit = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return e ? {tag: e.tagName, id: e.id, cls: String(e.className || ''),
                inSubject: !!e.closest('.sleep-bar') || !!e.closest('.brain-topbar')} : null;
  };

  const frame = q('.brain-frame');
  const inner = frame && frame.contentDocument;
  const innerBar = inner && inner.querySelector('#topbar');
  let innerBarPageY = null;
  if (innerBar && frame) {
    const a = innerBar.getBoundingClientRect();
    innerBarPageY = Math.round(a.top + frame.getBoundingClientRect().top);
  }

  return {
    viewport: {w: window.innerWidth, h: window.innerHeight},
    topbar: box(topbar), hub: box(q('.brain-hub')),
    stage: box(q('.brain-stage')), panel: box(q('.brain-panel.is-active')),
    frame: box(frame),
    nav: box(nav), tabs: box(tabs), bar: box(bar), stat: box(stat),
    drawer: box(q('#brain-inspect')),
    innerTopbarY: innerBarPageY,
    parents: {bar: owner(bar), tabs: owner(tabs), nav: owner(nav),
              stage: owner(q('.brain-stage'))},
    hitStat: hit(stat), hitBar: hit(bar),
    sleepBtnHit: (() => {
      const b = q('.brain-panel.is-active [data-sleep]');
      return hit(b);
    })(),
    docScrollH: document.documentElement.scrollHeight,
  };
}"""


def _overlap(a, b):
    """True when two boxes share any area. Touching edges do not count."""
    if not a or not b:
        return False
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
@pytest.mark.parametrize("width,height", SIZES)
def test_the_top_line_is_one_row_at_every_size(browser, brain_server, width, height):
    """The regression. Measured 66px at 375px -- "Ecra inteiro" wrapped the tab
    row onto a second line, which is the two-line top line T058 removed."""
    page = _open(browser, brain_server, width, height)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    assert d["topbar"], "there is no .brain-topbar at all"
    assert d["topbar"]["h"] <= MAX_TOP_LINE_H, (
        f"the top line is {d['topbar']['h']}px tall at {width}x{height}; it "
        f"wrapped to a second row (limit {MAX_TOP_LINE_H})"
    )
    assert d["topbar"]["y"] == 0, f"the top line starts at y={d['topbar']['y']}, not 0"


@pytest.mark.parametrize("width,height", SIZES)
def test_nothing_in_the_top_line_paints_over_anything_else(browser, brain_server, width, height):
    """The original bug, stated as the invariant that replaces it.

    Nav (z50), tab row (z60) and sleep bar (z40) all claimed the same corner and
    z-index picked a winner; the loser was invisible. As siblings in one flex row
    they cannot overlap at all, so this asserts geometry instead of stacking --
    a stronger claim than any z-index number, because it survives a re-order.
    """
    page = _open(browser, brain_server, width, height)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    for a, b, label in (
        (d["nav"], d["tabs"], "nav-bar vs brain-tabs"),
        (d["nav"], d["bar"], "nav-bar vs sleep-bar"),
        (d["tabs"], d["bar"], "brain-tabs vs sleep-bar"),
    ):
        assert not _overlap(a, b), (
            f"{label} overlap at {width}x{height}: {a} and {b}"
        )


@pytest.mark.parametrize("width,height", SIZES)
def test_the_concepts_counter_is_the_thing_you_click(browser, brain_server, width, height):
    """The exact failure, as a hit test.

    Before: elementFromPoint over the Concepts card returned the "Ecra inteiro"
    link from the row above. The card reported itself visible and enabled the
    whole time; it was simply not what a click would land on.
    """
    page = _open(browser, brain_server, width, height)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    assert d["stat"], "the Concepts counter is not on the page"
    hit = d["hitStat"]
    assert hit and hit["inSubject"], (
        f"elementFromPoint over the Concepts card at {width}x{height} returned "
        f"{hit}; something else is painted on top of it"
    )


@pytest.mark.parametrize("width,height", SIZES)
def test_the_graph_takes_everything_below_the_top_line(browser, brain_server, width, height):
    """The graph is the page -- but "the page" now means the space under the top
    line, not the whole viewport. Measured, not assumed: the top line's height
    is read from the DOM and subtracted, so this cannot drift from the layout.

    This replaces the old `>= viewport - 4` assertion, which is why it is worth
    stating that the replacement is STRICTLY STRONGER: it additionally requires
    the top line to be a bounded row, which nothing measured before. It still
    catches the 2026-10-01 regression (hub 244x670), because 670 < 900-45-4.
    """
    page = _open(browser, brain_server, width, height)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    avail_h = d["viewport"]["h"] - d["topbar"]["h"]
    avail_w = d["viewport"]["w"]
    for key in ("hub", "stage", "panel", "frame"):
        box = d[key]
        assert box is not None, f".{key} is missing from /admin/brain"
        assert box["w"] >= avail_w - 4 and box["h"] >= avail_h - 6, (
            f".{key} is {box} under a {avail_w}x{avail_h} usable area "
            f"({width}x{height} viewport, {d['topbar']['h']}px top line): the "
            f"graph is no longer the page"
        )
    assert d["docScrollH"] <= d["viewport"]["h"] + 1, (
        f"the document scrolls ({d['docScrollH']} > {d['viewport']['h']}): "
        f"something is laid out below the fold again"
    )


@pytest.mark.parametrize("width,height", SIZES)
def test_the_3d_views_own_bar_is_not_covered(browser, brain_server, width, height):
    """Inside the iframe, #topbar sits at top:8px. The page's nav used to occupy
    exactly that band and hide it -- measured at y=9..48 against a 0..45 nav."""
    page = _open(browser, brain_server, width, height)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    assert d["innerTopbarY"] is not None, "the 3D view's #topbar is missing"
    assert d["innerTopbarY"] >= d["topbar"]["bottom"], (
        f"the 3D view's own bar starts at y={d['innerTopbarY']}, under the page "
        f"top line which ends at {d['topbar']['bottom']}"
    )


def test_the_three_bars_are_siblings_in_one_row(browser, brain_server):
    """The structural claim the whole thing rests on. If one of them is moved
    back inside .brain-hub or .brain-stage, the stage stops filling the hub and
    the page goes back to overlapping bars."""
    page = _open(browser, brain_server, 1440, 900)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    for key in ("bar", "tabs", "nav"):
        assert d["parents"][key] == "brain-topbar", (
            f"the {key} is a child of {d['parents'][key]!r}, not of "
            f".brain-topbar; it is back to floating over the graph"
        )
    assert "brain-hub" in (d["parents"]["stage"] or ""), (
        f".brain-stage parent is {d['parents']['stage']!r}, not the hub"
    )


def test_the_drawer_cannot_reach_the_top_line(browser, brain_server):
    """The relation test_brain_hub_clickables.py asserted by z-index no longer
    exists -- the bar and the drawer are in different containers and do not
    intersect -- so it is asserted as geometry now. The button is the thing that
    matters: it must be the topmost thing where it is drawn."""
    page = _open(browser, brain_server, 1916, 895, open_drawer=True)
    try:
        d = page.evaluate(GEOMETRY)
    finally:
        page.close()
    assert not _overlap(d["drawer"], d["bar"]), (
        f"the drawer overlaps the sleep bar: {d['drawer']} vs {d['bar']}"
    )
    hit = d["sleepBtnHit"]
    assert hit and hit["tag"] in ("BUTTON", "SPAN"), (
        f"the Sleep & Dream button's centre is not the button: {hit}"
    )
    assert hit["id"] != "sonhar", (
        "the sleep bar is painted over the Sleep & Dream button again"
    )


def test_the_drawer_starts_under_the_top_line_with_no_magic_padding(browser, brain_server):
    """The drawer's first row used to need padding-top:calc(45px + 52px + gap) --
    two measured constants for two bars that no longer float over it. A stale
    value there pushed the first row under the menu with no other symptom."""
    page = _open(browser, brain_server, 1440, 900, open_drawer=True)
    try:
        d = page.evaluate(GEOMETRY)
        pad = page.evaluate(
            "() => getComputedStyle(document.getElementById('brain-inspect')).paddingTop"
        )
    finally:
        page.close()
    assert d["drawer"]["y"] >= d["topbar"]["bottom"], (
        f"the drawer starts at y={d['drawer']['y']}, overlapping the top line "
        f"which ends at {d['topbar']['bottom']}"
    )
    assert pad in ("0px", "8px", "12px", "16px"), (
        f"the drawer still reserves {pad} of top padding for bars that are no "
        f"longer painted over it"
    )


def test_the_burger_still_opens_the_menu_inside_the_viewport(browser, brain_server):
    """The nav shrank from a full-width band to the burger's own width when it
    became a row member, and .nav-menu is positioned relative to it. If the
    panel anchored off-screen the menu would be unreachable again."""
    for width, height in ((1440, 900), (375, 667)):
        page = _open(browser, brain_server, width, height)
        try:
            page.click(".nav-toggle")
            page.wait_for_timeout(250)
            m = page.evaluate(
                """() => {
                    const el = document.getElementById('nav-menu');
                    const r = el.getBoundingClientRect();
                    return {x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height),
                            open: el.classList.contains('open')};
                }"""
            )
        finally:
            page.close()
        assert m["open"], f"the burger did not open the menu at {width}x{height}"
        assert m["w"] > 0 and m["h"] > 0, f"the menu opened with no size: {m}"
        assert m["x"] >= 0 and m["y"] >= 0, (
            f"the menu opened off-screen at {width}x{height}: {m}"
        )
        assert m["x"] + m["w"] <= width + 1, (
            f"the menu runs past the right edge at {width}x{height}: {m}"
        )


def test_no_javascript_error_on_load(browser, brain_server):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)[:160]))
    page.on(
        "console",
        lambda m: errs.append(m.text[:160]) if m.type == "error" else None,
    )
    try:
        page.goto(f"{brain_server}/admin/brain", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
    finally:
        page.close()
    # The 3D view builds a second WebGL context inside the iframe and logs when
    # it cannot; that is redundancy, not failure.
    real = [e for e in errs if "WebGL" not in e and "THREE.WebGLRenderer" not in e]
    assert not real, f"page errors: {real[:3]}"
