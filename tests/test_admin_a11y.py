"""Measured accessibility and layout regressions for the admin UI.

Every assertion here came from a defect found by rendering the pages in a real
browser (Playwright/Chromium), not from reading the CSS. They assert on the
RENDERED DOM because the defects were invisible in the templates: templates
received a `nav_menu` they never printed, a `<label>` with no `for`, and grid
columns that only overflowed once laid out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import admin as admin_mod  # noqa: E402
from src.api.routes import create_app  # noqa: E402

ROUTES = [
    "/admin/memory",
    "/admin/brain",
    "/admin/config",
    "/admin/users",
    "/admin/rag",
    "/admin/env",
    "/admin/flybrain",
    # /admin/dashboard is intentionally absent: it is a 301 to /admin/brain
    # as of 2026-09-27, so asserting on its body would assert on a redirect.
    "/admin/login",
]


@pytest.fixture(scope="module")
def pages() -> dict:
    """Render every admin page as a logged-in admin, once per module."""
    app = create_app()
    app.config.update(TESTING=True)
    out = {}
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess[admin_mod.SESSION_KEY] = "test-admin@example.invalid"
        for route in ROUTES:
            resp = client.get(route)
            assert resp.status_code == 200, f"{route} -> {resp.status_code}"
            out[route] = resp.get_data(as_text=True)
    return out


def test_every_page_declares_a_language(pages):
    """WCAG 3.1.1 Language of Page (Level A).

    Seven of nine templates shipped without any lang attribute, so screen
    readers had no pronunciation rules for Portuguese. brain and rag already
    had it, which is why the gap was easy to miss.
    """
    missing = [r for r, html in pages.items() if 'lang="pt"' not in html]
    assert not missing, f"pages without <html lang>: {missing}"


def test_every_page_renders_the_nav_toggle(pages):
    """Includes /admin/login: a visitor who is not authenticated yet has
    nothing else to navigate with, and login() was passing a nav_menu that
    LOGIN_TEMPLATE silently dropped."""
    missing = [r for r, html in pages.items() if 'class="nav-toggle"' not in html]
    assert not missing, f"pages with no hamburger toggle: {missing}"


def test_config_form_controls_have_accessible_names(pages):
    """WCAG 4.1.2 Name, Role, Value (Level A).

    The template had a <label> with the config key as its text but no `for`,
    and the inputs had no id, so 58 controls had no accessible name at all.
    """
    import re

    html = pages["/admin/config"]
    inputs = re.findall(r'<input\s[^>]*name="config_[^"]*"[^>]*>', html)
    assert inputs, "expected config inputs to be present"
    unlabelled = [
        tag for tag in inputs if "id=" not in tag or not re.search(r'for="cfg-[^"]+"', html)
    ]
    assert not unlabelled, f"{len(unlabelled)} inputs without an id/label pair"


def test_memory_graph_grid_is_responsive(pages):
    """A fixed `1.5fr 1fr` grid overflowed the viewport by 242px at 375px wide.

    Regression guard on the intrinsic, not on the declaration: a
    `repeat(auto-fit, minmax(min(100%, ...), 1fr))` track cannot overflow,
    whereas any fixed two-column track can.
    """
    html = pages["/admin/memory"]
    assert "grid-template-columns: repeat(auto-fit, minmax(min(100%" in html, (
        "memory template must use a self-collapsing grid track; "
        "a fixed track overflowed the viewport at 375px"
    )
