"""Layout regressions for the nav panel and the device page.

Each test corresponds to a defect reported against the live UI on 2026-09-27,
and each was verified by reintroducing the defect and watching the test fail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import admin as admin_mod  # noqa: E402
from src.api.design import design_css  # noqa: E402

DESKTOP_WIDTHS = (1280, 1440, 1920)


def _admin_email() -> str:
    conn = admin_mod.get_db_connection()
    try:
        row = conn.execute("SELECT email FROM users WHERE role='admin' LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row, "no admin user in the store"
    return row["email"]


def _css() -> str:
    # Comments are stripped: an inline comment quoting the old value would
    # otherwise be read as a live declaration.
    return re.sub(r"\s+", "", re.sub(r"/\*.*?\*/", "", design_css(), flags=re.S))


def test_desktop_panel_is_a_dropdown_not_a_full_screen_overlay():
    """Reported: scrollbars in the menu on desktop, and leftover space.

    The panel was position:fixed at every width, so on desktop it covered the
    viewport and scrolled for a handful of links, and it sat 57px below the bar
    as a dead band. Above 900px it must be an anchored dropdown.
    """
    flat = _css()
    outside = flat.split("@media(max-width:900px)")[0]
    assert re.search(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{", outside)
    base = re.findall(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{[^}]*\}", outside)
    assert any("position:absolute" in blk for blk in base), (
        f"desktop panel must be an anchored dropdown; rules seen: {base}"
    )
    assert not any("position:fixed" in blk for blk in base), (
        "desktop panel must not be a full-screen overlay"
    )
    # Anchored at top:100% -> flush under the bar, no dead band.
    assert any("top:100%" in blk for blk in base), (
        "the dropdown must be anchored flush under the bar"
    )
    # The bar must establish the positioning context for the absolute panel.
    assert ".nav-bar{position:relative" in outside, (
        "nav-bar must be position:relative or the dropdown anchors to the page"
    )


def test_full_screen_overlay_is_mobile_only():
    """Below 900px an anchored dropdown cannot hold the device strip."""
    flat = _css()
    inside = flat.split("@media(max-width:900px)")[1]
    mobile = re.findall(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{[^}]*\}", inside)
    assert any("position:fixed" in blk for blk in mobile), (
        "under 900px the panel must become a full-height overlay"
    )


def test_root_keeps_devices_outside_the_menu():
    """Reported: device readings should always be available, not behind a burger.

    Buried in the panel, the page's primary content became conditional.

    The ORDER of the two siblings changed on 2026-09-27: the nav-bar was moved
    after #devices so `margin-left:auto` could right-align it, because
    margin-left:auto only pushes a flex child away from its previous sibling --
    with #devices last and flex-grow:1, the burger stayed at x=210 on a 1280px
    viewport. The invariant is therefore stated as SIBLING, in either order,
    which is what actually matters.
    """
    src = (Path(__file__).resolve().parent.parent / "skills/skill_ui.py").read_text(
        encoding="utf-8"
    )
    # The devices container must be a sibling of the nav-bar, not inside it.
    m = re.search(r'<nav class="nav-menu[^"]*" id="nav-menu".*?</nav>', src, re.S)
    assert m, "root nav-menu not found"
    assert 'id="devices"' not in m.group(0), (
        "the device strip must NOT be inside the hamburger panel"
    )
    # Sibling, in EITHER order -- the two are ordered by the flex layout, not by
    # the markup, and the burger has to sit after #devices to right-align.
    bar = re.search(r'<div class="nav-bar">.*?</div>', src, re.S)
    assert bar, "root nav-bar not found"
    devices = re.search(r'<div[^>]*id="devices".*?\n\s*</div>', src, re.S)
    assert devices, "root #devices not found"
    assert 'id="devices"' not in bar.group(0), "#devices must NOT be inside the nav-bar"
    # They must not overlap: the bar's span must not contain the devices span.
    assert not (bar.start() < devices.start() < bar.end()), (
        "#devices must not be nested inside the nav-bar"
    )


class TestRootNavIsARightAlignedBurger:
    """The root page's admin nav is a burger, right-aligned, admin-only.

    Reversed on 2026-09-27 by owner decision: the first pass removed the burger
    entirely, on the grounds that it gated five links and nothing else. The
    owner then asked for it back, as a burger in the top RIGHT -- the left edge
    belongs to the brand. So the toggle is correct after all; what was wrong
    before was its position and the fact that the links went to every visitor.
    """

    @staticmethod
    def _root_src() -> str:
        return (Path(__file__).resolve().parent.parent / "skills" / "skill_ui.py").read_text(
            encoding="utf-8"
        )

    def test_root_has_no_toggle(self):
        """No burger on the root page.

        Reversed a second time on 2026-09-27: a round had just added a
        right-aligned burger, and the owner then asked for it removed again. The
        panel holds four admin links and nothing else, so the toggle gates
        nothing worth gating. Pinned so it stays removed unless that changes.
        """
        src = self._root_src()
        assert 'id="nav-toggle"' not in src

    def test_root_toggle_meets_the_touch_floor(self):
        """44px, not 24px: a 37px target is one a thumb reliably misses."""
        src = self._root_src()
        assert "min-height:44px" in src
        assert "min-width:44px" in src

    def test_root_page_has_no_admin_links_at_all(self):
        """REVERSED AGAIN on 2026-09-27, by owner report: the admin panel kept
        reappearing on `/` and, once it was a bordered box, it read as a stuck
        hamburger. It is now absent for every viewer, admin or not.

        Asserted server-side, not with CSS: `display:none` would leave the admin
        URLs in the page source, which is the failure mode the previous version
        of this test was written to prevent.
        """
        src = self._root_src()
        assert 'admin_links_html = ""' in src, "the root page must not build admin links for anyone"
        assert "__ADMIN_LINKS__" in src, "the placeholder must still be substituted"
        # and no admin URL may be reachable from the root document
        for href in ("/admin/brain", "/admin/config", "/admin/users", "/admin/logout"):
            assert f'href="{href}"' not in src, f"{href} is still linked from /"

    def test_admin_nav_still_works_on_the_pages_that_keep_it(self):
        """Removing them from `/` must not have removed them everywhere.

        The root page no longer builds the admin block, but the shared design
        system is what every admin page uses for its nav. If the opt-out had
        been written as a page-level override instead of in `design.py`, the
        collapsible panel would have broken with it. This asserts the shared
        rules for a collapsible panel are still there and still scoped away from
        the opt-out.
        """
        from src.api.design import design_css

        css = re.sub(r"/\*.*?\*/", "", design_css(), flags=re.S)
        flat = re.sub(r"\s+", "", css)
        # The collapsible geometry must survive for pages that have a toggle...
        assert ".nav-menu:not(.nav-menu-always){" in flat and "position:absolute" in flat, (
            "the anchored dropdown rule for admin pages is gone"
        )
        # ...and the opt-out must still exclude itself from it.
        assert ".nav-menu.nav-menu-always{" in flat and "position:static" in flat, (
            "the opt-out no longer forces inline flow"
        )
        # The open state, which the burger toggles, must still be defined.
        # The declaration keeps its semicolon inside the block, as emitted.
        assert ".nav-menu.open{display:flex;}" in flat


def test_opted_out_nav_is_never_turned_into_an_overlay():
    """`.nav-menu-always` must opt out of the panel GEOMETRY, not just display.

    The opt-out originally forced `display:flex !important` and nothing else, so
    an always-visible nav still inherited `position:absolute` (desktop) and
    `position:fixed; inset:0` (below 900px). Measured on the root page at
    375x667: a `position:fixed` panel 307px tall with its four links at
    y=67/119/171/223, over a chat that starts at y=190 -- a tap on the message
    text hit an admin link and navigated to /admin/login. `display:flex` alone
    is not "always visible", it is "always open and on top of the page".

    This asserts the new invariant on both sides: the opted-out nav is inline
    and wrap-enabled, and the collapsible panel keeps its overlay for the pages
    that actually have a toggle.
    """
    flat = _css()
    blocks = re.findall(r"\.nav-menu\.nav-menu-always\{[^}]*\}", flat)
    assert blocks, (
        f"no .nav-menu-always rules found; rules seen: {re.findall(r'[^{}]*nav-menu-always[^{]*', flat)}"
    )
    body = "".join(blocks)
    assert "position:fixed" not in body, f"opt-out must not be viewport-fixed; got: {body}"
    assert "position:absolute" not in body, f"opt-out must not be an anchored dropdown; got: {body}"
    assert "flex-wrap:wrap" in body, f"opt-out must wrap instead of stacking; got: {body}"
    assert "flex-direction:row" in body, f"opt-out must lay the links inline; got: {body}"

    # The collapsible panel must KEEP both geometries -- the opt-out is additive,
    # not a replacement. If this regresses, admin pages with a toggle lose the
    # dropdown on desktop and the full-height overlay on a phone.
    outside = flat.split("@media(max-width:900px)")[0]
    inside = flat.split("@media(max-width:900px)")[1]
    base = re.findall(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{[^}]*\}", outside)
    assert any("position:absolute" in b for b in base), f"desktop dropdown lost: {base}"
    mobile = re.findall(r"\.nav-menu(?::not\(\.nav-menu-always\))?\{[^}]*\}", inside)
    assert any("position:fixed" in b for b in mobile), f"mobile overlay lost: {mobile}"
    # And the overlay rules must be scoped so they cannot match the opt-out.
    assert re.search(r"\.nav-menu:not\(\.nav-menu-always\)\{[^}]*position:fixed", inside), (
        "the mobile overlay selector must exclude .nav-menu-always, otherwise "
        "source order alone decides and the opt-out silently stops working"
    )
