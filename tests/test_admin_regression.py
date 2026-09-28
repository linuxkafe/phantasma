"""Regression tests for the admin blueprint.

The corruption repaired earlier (a duplicated module head) was invisible to
`py_compile`: the file compiled while registering two Blueprint objects and
duplicating every helper. These tests assert the *shape* of the module, not
just that it imports.

Run directly (the venv has flask but no pytest):

    pytest tests/test_admin_regression.py

They are also collectable by pytest if it is available in the interpreter.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def test_module_never_hardcodes_a_production_db_path():
    """A dev process must not reach production data. See test_db_path_isolation."""
    assert '"/opt/phantasma/data/' not in SOURCE, (
        "admin.py hardcodes a production database path; a dev process would "
        "open and write to the PRODUCTION database."
    )


# Safe at module level: conftest.py (imported first by pytest) has already
# redirected config at the databases, so this opens the isolated store, not
# production. That ordering is the whole point -- see conftest.py.
from src.api import admin as admin_mod  # noqa: E402

TEST_ADMIN_EMAIL = "test-admin@example.invalid"

# The shared synthetic admin email lives in conftest.py, which pytest loads
# automatically. Fixtures there inject `admin_mod` with isolated databases,
# so nothing here touches production.

SOURCE = (Path(__file__).resolve().parent.parent / "src/api/admin.py").read_text(encoding="utf-8")


def test_module_head_is_not_duplicated():
    assert SOURCE.count("Admin blueprint for pHantasma") == 1
    assert SOURCE.count("admin_bp = Blueprint(") == 1
    assert len(re.findall(r"^from __future__ import annotations$", SOURCE, re.M)) == 1


def test_no_function_is_defined_twice():
    """`wrapped` appears twice by design (inner fn of two decorators)."""
    names = re.findall(r"^\s*def (\w+)", SOURCE, re.M)
    duplicates = {n for n in names if names.count(n) > 1} - {"wrapped"}
    assert not duplicates, f"duplicated defs: {duplicates}"


def test_expected_routes_are_registered_once():
    source_routes = re.findall(r'@admin_bp\.route\(\s*"([^"]+)"', SOURCE)
    for expected in [
        "/",
        "/login",
        "/verify",
        "/logout",
        "/dashboard",
        "/config",
        "/users",
        "/memory",
        "/flybrain",
        "/env",
    ]:
        assert source_routes.count(expected) == 1, (
            f"route {expected} declared {source_routes.count(expected)}x"
        )


def test_index_redirects_to_dashboard_not_the_env_textarea():
    """The reported bug: /admin served the raw .env textarea instead of a menu."""
    index_src = re.search(r"def index\(\):.*?(?=\n@admin_bp\.|\ndef )", SOURCE, re.S)
    assert index_src, "index() not found"
    body = index_src.group(0)
    assert 'redirect(url_for("admin.brain_hub"))' in body
    assert "ADMIN_TEMPLATE" not in body, "/admin/ must not render the env editor"
    assert "_read_env" not in body


def test_env_editor_is_the_secondary_route():
    assert re.search(r'@admin_bp\.route\("/env", methods=\["GET", "POST"\]\)', SOURCE)
    env_fn = re.search(r"def env_editor\(\):.*?(?=\n@admin_bp\.|\ndef )", SOURCE, re.S)
    assert env_fn and "ADMIN_TEMPLATE" in env_fn.group(0)


def test_nav_menu_emits_a_single_style_attribute_per_link():
    nav = admin_mod._build_nav_menu("admin.dashboard", "admin")
    assert 'style="' in nav
    # The original bug: <a ... class="nav-link" style="a" style="b">
    for tag in re.findall(r"<a\b[^>]*>", nav):
        assert tag.count("style=") <= 1, f"duplicate style attribute: {tag}"
    assert "nav-link" in nav


def test_nav_menu_marks_exactly_one_active_link():
    """The active marker moved from an inline style to a CSS class when the
    nav was rebuilt on the design system, so the assertion follows the class."""
    nav = admin_mod._build_nav_menu("admin.memory_viewer", "admin")
    active = [t for t in re.findall(r"<a\b[^>]*>", nav) if "nav-link active" in t]
    assert len(active) == 1, active
    assert "/admin/brain" in active[0]


def test_brain_endpoints_highlight_the_single_brain_entry():
    """Every Cérebro page must light up the one Cérebro top-level link."""
    for endpoint in (
        "admin.memory_viewer",
        "rag_viewer",
        "admin.flybrain_manager",
        "admin.explorer_3d",
    ):
        nav = admin_mod._build_nav_menu(endpoint, "admin")
        active = [t for t in re.findall(r"<a\b[^>]*>", nav) if "nav-link active" in t]
        assert len(active) == 1, f"{endpoint}: {active}"
        assert "/admin/brain" in active[0], f"{endpoint} highlights {active[0]}"


def test_nav_menu_contains_every_destination():
    """Top level: brand->/, the Cérebro hub, Config, Users, Logout.

    Dashboard left the nav on 2026-09-27: it was folded into /admin/brain,
    which already renders every subsystem the dashboard summarised, so the nav
    had two entries pointing at one subject. /admin/dashboard still answers 301,
    so this asserts absence from the MENU, not that the route is gone.

    Memória/RAG/FlyBrain/Explorador 3D are deliberately NOT top-level items any
    more; the single Cérebro entry points at the hub, which shows all four.

    /admin/env left the nav on 2026-09-27 by owner decision -- a raw env-file
    editor is a maintenance tool, not primary navigation, and /admin/config
    already covers configuration. The page is still reachable by URL, so this
    asserts absence from the MENU, not that the route is gone.
    """
    nav = admin_mod._build_nav_menu("admin.dashboard", "admin")
    for href in [
        "/admin/brain",
        "/admin/config",
        "/admin/users",
        "/admin/logout",
    ]:
        assert f'href="{href}"' in nav, f"missing nav link {href}"
    assert 'href="/admin/dashboard"' not in nav, (
        "Dashboard was folded into /admin/brain; two nav entries for one subject"
    )
    assert 'href="/admin/env"' not in nav, "/admin/env must not be in the primary nav"
    # The brand is "home", and home is the device UI.
    assert 'class="nav-brand" href="/"' in nav, (
        "the brand must point at / , not at the admin dashboard"
    )
    for href in ("/admin/flybrain", "/memory/3d", "/admin/rag"):
        assert f'href="{href}"' not in nav, f"{href} escaped the Cerebro section into the top nav"


def test_subnav_is_retired():
    """The Cérebro tab bar has been retired entirely.

    The hub consolidates all subsystems on a single screen, so the tab bar
    was removed by decision. This test guards against its accidental return.
    """
    for endpoint in (
        "admin.brain_hub",
        "admin.memory_viewer",
        "rag_viewer",
        "admin.flybrain_manager",
        "admin.explorer_3d",
    ):
        subnav = admin_mod._build_subnav(endpoint)
        assert subnav == "", f"{endpoint} subnav should be empty, got: {subnav}"


def test_logout_link_keeps_its_destructive_colour():
    nav = admin_mod._build_nav_menu("admin.dashboard", "admin")
    logout = [t for t in re.findall(r"<a\b[^>]*>", nav) if "/admin/logout" in t]
    assert logout and "destructive" in logout[0]
    assert "var(--accent)" not in logout[0], "logout must not look active"


def test_nav_is_sticky_and_wraps():
    """position:fixed used to overlap page content on small screens.

    Sticky now lives on .nav-bar (which wraps the toggle and the menu); the
    wrap behaviour stays on .nav-menu. The stylesheet is minified, so compare
    whitespace-insensitively rather than coupling the test to formatting.
    """
    from src.api.design import design_css

    css = design_css()
    flat = re.sub(r"\s+", "", css)

    bar = flat.split(".nav-bar")[1].split("}")[0]
    assert "position:sticky" in bar, f".nav-bar must stay pinned: {bar}"
    assert "top:0" in bar

    menu = flat.split(".nav-menu")[1].split("}")[0]
    assert "flex-wrap:wrap" in menu, f".nav-menu must wrap, not overflow: {menu}"


def test_hamburger_is_visible_at_every_width():
    """The toggle is permanent, so its panel is dismissible at every width.

    Contract CHANGED on 2026-09-27 by owner decision. It used to assert the
    opposite -- `display:none` by default, `display:flex` only under
    max-width:900px -- i.e. no menu control on desktop. The toggle is now shown
    everywhere, which obliges the panel to be collapsible everywhere; a toggle
    that only worked below 900px would be a dead control on desktop.

    The old test is not deleted, it is inverted: both the "always visible" and
    the "collapsible everywhere" halves are load-bearing.
    """
    from src.api.design import design_css

    # Comments are stripped before asserting: a comment that explains the old
    # `display:none` lives inside the rule and would otherwise be read as a
    # declaration.
    raw = design_css()
    raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    flat = re.sub(r"\s+", "", raw)

    base = flat.split(".nav-toggle")[1].split("}")[0]
    assert "display:flex" in base, "toggle must be visible by default at every width"
    assert "display:none" not in base, "toggle must not be hidden by default"

    # The panel is collapsed by default and opened by a class, and those rules
    # must NOT live inside the breakpoint any more. Asserted on the exact
    # declarations rather than on "the first .nav-menu rule", because the
    # inline horizontal bar rule legitimately comes first.
    outside = flat.split("@media(max-width:900px)")[0]
    # ALL .nav-menu blocks before the breakpoint, not just the first: the
    # inline horizontal bar rule legitimately comes first and is not the
    # collapsible one.
    menu_blocks = re.findall(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{[^}]*\}", outside)
    assert any("display:none" in blk for blk in menu_blocks), (
        f"panel must be closed by default at every width; blocks seen: {menu_blocks}"
    )
    assert ".nav-menu.open{display:flex;}" in outside, (
        "the open state must be defined outside the breakpoint"
    )
    # The toggle is NOT positioned. Modelled on pdftools/components/Navbar.tsx,
    # where the button is a plain flex child at the end of the bar and
    # `margin-left:auto` puts it on the right. Both positioning attempts were
    # wrong: absolute landed on the container's right edge (976px from the
    # screen edge at 1920px), and fixed only escaped it because .nav-bar had no
    # filtered ancestor -- adding backdrop-filter to the bar silently trapped it
    # again. Layout is the fix.
    assert "position:fixed" not in base, "the toggle must not be viewport-positioned; use flex flow"
    assert "position:absolute" not in base, "the toggle must not be absolutely positioned either"
    assert "margin-left:auto" in base, (
        "the toggle must be pushed to the right end of the bar by layout"
    )
    # And the content column must be centred, or "right" is only relative to an
    # off-centre container (the bar used to sit flush against the left edge).
    assert ".container{margin-left:auto;margin-right:auto;}" in outside, (
        "the content column must be centred or the nav lands off-centre"
    )


def test_nav_js_no_longer_gates_on_the_breakpoint():
    """Dismissing on link click is not breakpoint-gated any more.

    With the panel collapsible at every width, keeping `&& mq.matches` would
    leave the overlay covering the page the user just navigated to.
    """
    from src.api.design import design_js

    flat = re.sub(r"\s+", "", design_js())
    assert "closest('a')&&mq.matches" not in flat.replace(" ", ""), (
        "link-click dismissal must not be gated on the breakpoint"
    )


def test_every_admin_page_ships_the_hamburger_toggle():
    """A toggle without the shared script, or a script without a toggle, is a
    dead control. The markup half comes from _build_nav_menu, the behaviour
    half from BASE_STYLE; both must be present on every rendered page."""
    from src.api.admin import BASE_STYLE

    nav = admin_mod._build_nav_menu("admin.dashboard", "admin")

    assert 'class="nav-toggle"' in nav
    assert 'aria-controls="nav-menu"' in nav
    assert 'aria-expanded="false"' in nav
    assert 'id="nav-menu"' in nav
    # The toggle must be a sibling of the menu, not a child: the menu becomes
    # display:none when collapsed, which would hide the only control that
    # reopens it.
    assert nav.index('class="nav-toggle"') < nav.index('class="nav-menu"')
    assert nav.index('class="nav-menu"') < nav.index("</nav>")

    assert "matchMedia" in BASE_STYLE
    assert "aria-expanded" in BASE_STYLE
    assert "Escape" in BASE_STYLE


def _run() -> int:
    """Minimal runner so this file needs no pytest in the venv."""
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as exc:
            failed.append(name)
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # pragma: no cover
            failed.append(name)
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())


# --- Every admin page must actually render the nav ----------------------------
#
# The tests above exercise _build_nav_menu directly, so they all passed while
# /admin/env rendered no menu at all: ADMIN_TEMPLATE was the only template
# missing `{{ nav_menu | safe }}`, and the route dutifully passed a nav_menu
# the template silently dropped. Asserting on the function cannot catch a
# template that never uses it -- so these assert on the RENDERED pages.

# A synthetic admin from the isolated store created by the `admin_store`
# fixture. This used to be fetched at MODULE level from the production
# database, so the suite could not run in a fresh checkout and any test run
# mutated live data. The session value is set per-test from the fixture now.
ADMIN_PAGE_ROUTES = [
    "/admin/memory",
    "/admin/brain",
    "/admin/flybrain",
    "/admin/rag",
    "/admin/config",
    "/admin/users",
    "/admin/env",
]


def _render_admin_page(path: str) -> str:
    """Render an admin page as a logged-in admin and return its HTML."""
    from src.api.routes import create_app

    app = create_app()
    app.config.update(TESTING=True)
    with app.test_request_context():
        # Satisfy @login_required without going through the OTP email flow.
        with app.test_client() as client:
            with client.session_transaction() as sess:
                # admin.SESSION_KEY is the only key @login_required reads;
                # _current_user_data() resolves the role from the users store.
                sess[admin_mod.SESSION_KEY] = TEST_ADMIN_EMAIL
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
            return resp.get_data(as_text=True)


def test_every_admin_page_renders_the_hamburger():
    """Each admin page must carry the same toggle, or the menu is unreachable."""
    missing = []
    for path in ADMIN_PAGE_ROUTES:
        html = _render_admin_page(path)
        if 'class="nav-toggle"' not in html:
            missing.append(path)
    assert not missing, f"pages with no hamburger toggle: {missing}"


def test_every_admin_page_toggle_controls_the_same_nav():
    """The toggle's aria-controls target must be the menu that is rendered.

    A mismatch here is silent: the button appears to do nothing.
    """
    for path in ADMIN_PAGE_ROUTES:
        html = _render_admin_page(path)
        controls = re.search(r'aria-controls="([^"]+)"', html)
        assert controls, f"{path}: toggle has no aria-controls"
        assert controls.group(1) == "nav-menu", (
            f"{path}: aria-controls={controls.group(1)!r}, expected 'nav-menu'"
        )
        assert 'id="nav-menu"' in html, f"{path}: aria-controls target not rendered"


def test_every_admin_page_has_exactly_one_toggle():
    """A duplicated toggle means two buttons fighting over one aria-expanded."""
    for path in ADMIN_PAGE_ROUTES:
        html = _render_admin_page(path)
        n = html.count('class="nav-toggle"')
        assert n == 1, f"{path}: expected 1 nav-toggle, found {n}"
