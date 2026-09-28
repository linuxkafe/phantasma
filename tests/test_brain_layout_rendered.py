"""The 3D graph is the page. Measured, not assumed.

This file exists because I broke that twice in one session. Moving the sleep
bar put it inside .brain-stage, which is position:absolute inset:0, so it
took the canvas's height and left the graph in a corner. Then moving the
stage out landed it in CONFIG_TEMPLATE, and .brain-stage vanished from
/admin/brain entirely. Both passed `ast.parse`, both compiled as Jinja, and
both looked fine in the template source. Only a browser knew.

So: measure the rendered geometry. These assert the graph fills the viewport
at three sizes and that the stage's parent is the hub, which is the specific
relationship that broke twice.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Playwright is installed in both venvs. It was only in the production one,
# so the pre-commit gate saw one skipped test and blocked the commit --
# correctly, because a layout regression that is never checked is a
# regression nobody is looking for. `pip install playwright` in venv/ is
# now part of the setup, same as ruff.
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = os.environ.get("PHANTASMA_URL", "http://127.0.0.1:5000")
SIZES = ((1280, 800), (1440, 900), (1920, 1080))

# Inside the graph iframe three.js builds a second WebGL context and logs a
# console error when it cannot; that is a warning about redundancy, not a
# failure, and treating it as one makes this file useless.
BENIGN = ("WebGL context could not be created", "THREE.WebGLRenderer")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--no-sandbox"])
        yield b
        b.close()


def _measure(browser, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    try:
        page.goto(f"{BASE}/admin/brain", wait_until="networkidle")
        page.wait_for_timeout(4000)
        return page.evaluate(
            """() => {
                const box = (s) => {
                    const el = document.querySelector(s);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height)};
                };
                const stage = document.querySelector('.brain-stage');
                return {
                    hub: box('.brain-hub'),
                    stage: box('.brain-stage'),
                    panel: box('.brain-panel.is-active'),
                    frame: box('.brain-frame'),
                    bar: box('.sleep-bar'),
                    stageParent: stage
                        ? (stage.parentElement.className || stage.parentElement.tagName)
                        : null,
                };
            }"""
        )
    finally:
        page.close()


@pytest.mark.parametrize("width,height", SIZES)
def test_the_graph_fills_the_viewport(browser, width, height):
    """The regression: 244x670 in a 1440x900 window, because the sleep bar
    had swallowed .brain-stage."""
    d = _measure(browser, width, height)
    for key in ("hub", "stage", "panel"):
        assert d[key] is not None, f".{key} is missing from /admin/brain"
        assert d[key]["w"] >= width - 4 and d[key]["h"] >= height - 4, (
            f"{key} is {d[key]} in a {width}x{height} viewport: the graph is "
            f"no longer the page"
        )


@pytest.mark.parametrize("width,height", SIZES)
def test_stage_is_a_child_of_the_hub(browser, width, height):
    """.brain-stage is position:absolute inset:0, so it only covers the
    screen while it is a child of the fixed, full-height hub. Nested inside
    anything else it collapses to that element's content box."""
    d = _measure(browser, width, height)
    assert "brain-hub" in (d["stageParent"] or ""), (
        f".brain-stage parent is {d['stageParent']!r}, not the hub; the stage "
        f"sizes itself to its parent instead of the viewport"
    )


def test_the_graph_iframe_is_mounted_and_live(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(f"{BASE}/admin/brain", wait_until="networkidle")
        page.wait_for_timeout(5000)
        d = page.evaluate(
            """() => {
                const f = document.querySelector('.brain-frame');
                const doc = f && f.contentDocument;
                const c = doc && doc.querySelector('canvas');
                return {
                    mounted: !!f,
                    canvas: c ? c.width + 'x' + c.height : null,
                    loadingHidden: doc
                        ? getComputedStyle(doc.getElementById('loading')).display
                        : null,
                };
            }"""
        )
        assert d["mounted"], "the 3D view is not mounted"
        assert d["canvas"], "the 3D view has no canvas"
        assert d["loadingHidden"] == "none", (
            "the loading overlay is still up: the graph never built"
        )
    finally:
        page.close()


def test_the_sleep_button_is_reachable(browser):
    """The button is now unconditional; it used to render only when the
    graph had pending issues, so on a healthy graph there was no button."""
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        page.goto(f"{BASE}/admin/brain", wait_until="networkidle")
        page.wait_for_timeout(2000)
        count = page.evaluate(
            "() => document.querySelectorAll('[data-sleep]').length")
        assert count, "no Dormir e Sonhar button is rendered"
        assert page.evaluate(
            "() => !!document.querySelector('.sleep-bar')"
        ), "the sleep bar is missing"
    finally:
        page.close()


def test_no_page_errors_on_load(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:120]))
    page.on("console",
            lambda m: errs.append(m.text[:120]) if m.type == "error" else None)
    try:
        page.goto(f"{BASE}/admin/brain", wait_until="networkidle")
        page.wait_for_timeout(4000)
        real = [e for e in errs if not any(b in e for b in BENIGN)]
        assert not real, f"page errors: {real[:3]}"
    finally:
        page.close()
