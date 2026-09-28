"""UI regression gate for the bilingual Cérebro section.

Covers the three things that were asked for and are easy to break silently:

1. ``/admin/lang/<code>`` switches the whole UI and survives a redirect.
2. Memória, RAG, FlyBrain and the 3D explorer are reachable from ONE "Cérebro"
   section (single top-level entry + tab bar on every Cérebro page).
3. The design language is present and carries no Universidade do Porto branding.

The authenticated pages are exercised through Flask's test client with a
forged session, so no real login or e-mail is needed.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api import design, i18n  # noqa: E402
from src.api.admin import admin_bp  # noqa: E402

ADMIN_EMAIL = "ui-test@phantasma.local"


@pytest.fixture(scope="module", autouse=True)
def loopback_bypass_for_admin_pages():
    """Enable the loopback auth bypass for this module.

    The admin routes now require a RESOLVED identity rather than merely a
    session cookie: a session naming an address absent from the users table is
    refused (that was the 403 on /admin/users, fixed 2026-09-27). This module
    renders admin pages, so it needs a real way in -- the bypass, which is the
    supported mechanism, rather than a fabricated session.

    Restored on teardown so it cannot leak into other modules. autouse and
    module-scoped so it is active before the `client` fixture builds the app.
    """
    import os as _os

    from src.api import localauth

    previous = _os.environ.get(localauth.ENV_FLAG)
    _os.environ[localauth.ENV_FLAG] = "1"
    yield
    if previous is None:
        _os.environ.pop(localauth.ENV_FLAG, None)
    else:
        _os.environ[localauth.ENV_FLAG] = previous


@pytest.fixture(scope="module")
def client():
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    app.secret_key = "test-secret-not-production"
    app.config["TESTING"] = True
    app.register_blueprint(admin_bp)

    @app.context_processor
    def _inject():
        from src.api.admin import get_language

        lang = get_language()
        return {"lang": lang, "t": lambda k, **kw: i18n.t(k, lang, **kw)}

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["admin_user"] = ADMIN_EMAIL
        yield c


# --- language switching ------------------------------------------------------


def test_every_string_has_both_languages():
    gaps = i18n.missing_translations()
    assert not gaps, f"strings missing a language: {gaps}"


@pytest.mark.parametrize("code", ["pt", "en"])
def test_language_switch_sets_session_and_cookie(client, code):
    resp = client.get(f"/admin/lang/{code}", follow_redirects=False)
    assert resp.status_code == 302
    cookie = resp.headers.get("Set-Cookie", "")
    assert f"phantasma_lang={code}" in cookie, cookie
    with client.session_transaction() as sess:
        assert sess["lang"] == code


def test_unknown_language_does_not_change_the_choice(client):
    with client.session_transaction() as sess:
        sess["lang"] = "en"
    client.get("/admin/lang/xx", follow_redirects=False)
    with client.session_transaction() as sess:
        assert sess["lang"] == "en", "an unknown code must not clobber the choice"


def test_known_variant_of_supported_language_is_accepted(client):
    client.get("/admin/lang/pt-BR", follow_redirects=False)
    with client.session_transaction() as sess:
        assert sess["lang"] == "pt"


@pytest.mark.parametrize(
    "key,lang,expected",
    [
        ("nav.brain", "pt", "Cérebro"),
        ("nav.brain", "en", "Brain"),
    ],
)
def test_brain_section_label_follows_the_language(key, lang, expected):
    assert i18n.t(key, lang) == expected


def _visible_html(body: str) -> str:
    """Strip <style>/<script> so assertions look at rendered labels only.

    Without this, a Portuguese word inside a CSS *comment* is indistinguishable
    from a Portuguese word shown to an English user.
    """
    body = re.sub(r"<style\b.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.S | re.I)
    return body


def test_pages_render_the_requested_language(client, use_local_bypass):
    client.get("/admin/lang/pt", follow_redirects=False)
    pt = _visible_html(client.get("/admin/rag").get_data(as_text=True))
    assert "Cérebro" in pt, "PT page is missing the Cérebro section"
    assert "Explorador 3D" in pt

    client.get("/admin/lang/en", follow_redirects=False)
    en = _visible_html(client.get("/admin/rag").get_data(as_text=True))
    assert "Brain" in en, "EN page is missing the Brain section"
    assert "3D Explorer" in en
    assert "Cérebro" not in en, "PT label leaked into the EN page"
    assert "Explorador 3D" not in en, "PT label leaked into the EN page"
    assert "Memória" not in en, "PT label leaked into the EN page"


def test_no_raw_i18n_keys_leak_into_html(client):
    """An unresolved key would render as 'nav.dashboard' - obvious, so assert it."""
    for path in ("/admin/rag", "/admin/memory", "/admin/flybrain"):
        body = _visible_html(client.get(path).get_data(as_text=True))
        leaked = set(
            re.findall(
                r">\s*((?:nav|brain|common|memory|rag|flybrain|explorer|auth|env|dashboard|users|config)\.[a-z_.]+)\s*<",
                body,
            )
        )
        assert not leaked, f"{path} leaked untranslated keys: {leaked}"


# --- the single Cérebro section ---------------------------------------------


def test_top_nav_has_exactly_one_brain_entry(client, use_local_bypass):
    body = client.get("/admin/rag").get_data(as_text=True)
    top_nav = body.split("</nav>")[0]
    hits = [
        m
        for m in re.findall(r'<a href="([^"]+)"[^>]*class="nav-link[^"]*"', top_nav)
        if m == "/admin/brain"
    ]
    assert len(hits) == 1, f"expected one Cérebro top-level entry, got {hits}"


@pytest.mark.parametrize("path", ["/admin/memory", "/admin/rag", "/admin/flybrain"])
def test_every_brain_page_shows_the_same_tab_bar(client, path):
    body = client.get(path).get_data(as_text=True)
    assert 'class="subnav"' not in body, f"{path} still contains a subnav"


def test_tab_bar_is_retired(client):
    for path in ("/admin/brain", "/admin/memory", "/admin/rag", "/admin/flybrain"):
        body = client.get(path).get_data(as_text=True)
        assert 'class="subnav"' not in body, f"{path} still contains a subnav"


def test_memory_rag_flybrain_and_explorer_all_live_under_the_brain_section():
    """Structural guard: the four subsystems must stay grouped."""
    source = (ROOT / "src/api/admin.py").read_text(encoding="utf-8")
    match = re.search(r"brain_endpoints = \{(.*?)\}", source, re.S)
    assert match, "brain_endpoints set is missing from the nav builder"
    grouped = match.group(1)
    for endpoint in (
        "admin.memory_viewer",
        "rag_viewer",
        "admin.flybrain_manager",
        "admin.explorer_3d",
    ):
        assert endpoint in grouped, f"{endpoint} escaped the Cérebro section"


# --- design language ---------------------------------------------------------


def test_design_css_defines_the_reference_tokens():
    css = design.design_css()
    for token in (
        "--brand-500:#009FDF",
        "--fs-display:32px",
        "--radius:8px",
        "--accent:#22c55e",
        "--destructive:#ef4444",
    ):
        assert token in css, f"missing design token {token}"
    assert "Inter" in css
    for scale in ("--sp-1:4px", "--sp-2:8px", "--sp-4:16px", "--sp-6:24px"):
        assert scale in css, f"missing spacing step {scale}"


def test_design_has_no_universidade_do_porto_branding():
    css = design.design_css().lower()
    for brand in design.FORBIDDEN_BRANDS:
        assert brand not in css, f"U.Porto branding leaked into the stylesheet: {brand}"


def test_admin_templates_have_no_uporto_strings():
    source = (ROOT / "src/api/admin.py").read_text(encoding="utf-8").lower()
    for brand in design.FORBIDDEN_BRANDS:
        assert brand not in source, f"U.Porto branding present in admin.py: {brand}"


def test_design_components_used_by_the_new_templates_exist():
    css = design.design_css()
    for cls in (
        ".card",
        ".stat",
        ".btn",
        ".btn--primary",
        ".segmented",
        ".subnav",
        ".nav-link",
        ".empty",
        ".badge",
    ):
        assert cls in css, f"missing component class {cls}"
