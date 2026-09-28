"""
Admin blueprint for pHantasma.

Provides a protected /admin area where authenticated users can view and edit
the runtime ``.env`` configuration.  Authentication is performed via a one‑time
code sent to the administrator's e‑mail address (the e‑mail transport is
mocked – replace ``_send_mail`` with a real SMTP client in production).

Session lifetime is 30 days; after 30 days of inactivity the user must
re‑authenticate.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Optional

import bcrypt
from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

import config
from src.settings_store import (
    DEFAULT_REACTION_WEIGHTS,
    REACTION_WEIGHTS_KEY,
    clear_setting,
    get_persona,
    get_reaction_weights,
    persona_is_overridden,
    reset_persona,
    set_persona,
    set_reaction_weights,
)

logger = logging.getLogger("phantasma.api")

from . import ratelimit as _ratelimit
from .design import design_css, design_js
from .i18n import (
    COOKIE_NAME as LANG_COOKIE,
)
from .i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    normalize_language,
    parse_accept_language,
    t,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def get_language() -> str:
    """Resolve the active UI language.

    Order: explicit session choice -> cookie -> ``Accept-Language`` -> default.
    An explicit choice wins so the PT/EN switch is sticky regardless of what
    the browser asks for.
    """
    try:
        chosen = session.get("lang")
    except RuntimeError:
        # Outside a request context (e.g. unit tests importing the module).
        chosen = None
    if chosen:
        return normalize_language(chosen)
    try:
        cookie_lang = request.cookies.get(LANG_COOKIE)
    except RuntimeError:
        cookie_lang = None
    if cookie_lang:
        return normalize_language(cookie_lang)
    try:
        header = request.headers.get("Accept-Language")
    except RuntimeError:
        header = None
    return parse_accept_language(header) or DEFAULT_LANGUAGE


# ----------------------------------------------------------------------
# Database connection
# ----------------------------------------------------------------------
# Resolved through config so that a dev checkout uses ITS OWN databases. These
# used to be hardcoded to /opt/phantasma/data/..., which meant a dev process
# opened -- and wrote to -- the PRODUCTION database. In production the resolved
# paths are identical to the old literals, so behaviour is unchanged there.
CONFIG_DB_PATH = Path(config.CONFIG_DB_PATH)
# Kept on config.BRAIN_DB_PATH, which the .env points at data/brain.db -- the
# same file src/brain/memory_graph.py opens, and the one holding memory_graph,
# memories and graph_edit_audit. Reading it without the .env loaded resolves to
# data/flybrain.db, which has none of those tables, so the knowledge editor and
# the dangling-ref list found nothing to work on. The lesson is that this
# constant is only meaningful inside a process that has loaded the .env, which
# is why the helpers below take a path rather than trusting an import-time one.
BRAIN_DB_PATH = Path(config.BRAIN_DB_PATH)


def get_db_connection():
    conn = sqlite3.connect(CONFIG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ----------------------------------------------------------------------
# User authentication
# ----------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a password against EITHER supported hash format.

    Two formats exist in the wild and they are not interchangeable:

    * ``$2b$...``          -- bcrypt, what :func:`hash_password` writes.
    * ``scrypt:N:r:p$salt$key`` -- passlib-style scrypt.

    The production store held scrypt hashes, so ``bcrypt.checkpw`` on one did
    not return False -- it RAISED ``ValueError: Invalid salt``. Nothing caught
    that, so authenticating the pre-existing administrator produced a 500
    instead of a clean rejection. A verification helper that raises on a
    malformed or foreign hash is a denial-of-service on your own user store.

    A hash in an unrecognised format is a verification FAILURE, never an
    exception: an unknown encoding must not be able to take a request down.
    """
    if not stored_hash:
        return False
    encoded = password.encode("utf-8")
    try:
        if stored_hash.startswith("scrypt:"):
            return _verify_scrypt(stored_hash, encoded)
        return bcrypt.checkpw(encoded, stored_hash.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        # Unknown or corrupt encoding. Refuse the login; do not propagate.
        logger.warning("Unverifiable password hash format: %s", type(exc).__name__)
        return False


def _verify_scrypt(stored_hash: str, encoded: bytes) -> bool:
    """Check a passlib-style ``scrypt:N:r:p$salt$key`` hash."""
    import base64
    import hashlib
    import hmac

    try:
        params, salt_b64, key_b64 = stored_hash.split("$")
        n, r, p = (int(x) for x in params.split(":")[1:4])
    except (ValueError, IndexError):
        return False
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
        derived = hashlib.scrypt(
            encoded,
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=(128 * n * r * p) + (1 << 20),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


# ----------------------------------------------------------------------
# In‑memory OTP store (replace with Redis / DB in production)
# ----------------------------------------------------------------------
_OTP_STORE: dict[str, tuple[str, float]] = {}  # email -> (otp, expiry_ts)


# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def _send_mail(to: str, subject: str, body: str, otp: str = None) -> None:
    """
    Send e‑mail via SMTP using settings from the ``.env`` file.

    Reads:
        - SMTP_HOST (default: localhost - uses local postfix)
        - SMTP_PORT (default: 25)
        - SMTP_USER (default: empty - postfix handles auth)
        - SMTP_PASS (default: empty)
        - ALERT_EMAIL (from .env, used as from address)

    Falls back to a console print if any required setting is missing,
    so the admin login flow still works in development.
    """
    host = os.getenv("SMTP_HOST", "localhost")
    port = int(os.getenv("SMTP_PORT", "25"))
    user = os.getenv("SMTP_USER", "")
    pwd = os.getenv("SMTP_PASS", "")

    # Build HTML email using template
    from flask import render_template_string

    html_body = render_template_string(EMAIL_TEMPLATE, subject=subject, body=body, otp=otp)

    try:
        msg = EmailMessage()
        msg.set_content(body)  # Plain text fallback
        msg.add_alternative(html_body, subtype="html")
        msg["Subject"] = subject
        msg["From"] = os.getenv("ALERT_EMAIL", "phantasma@linuxkafe.com")
        msg["To"] = to

        with smtplib.SMTP(host, port) as smtp:
            if user and pwd:
                smtp.login(user, pwd)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[SMTP ERROR] Failed to send mail: {e}")
        print(f"[MOCK MAIL] To: {to}\nSubject: {subject}\n{body}\n---")
    finally:
        print(f"[MAIL] Sent to {to} via SMTP")


def _generate_otp() -> str:
    """Six‑digit numeric OTP."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _store_otp(email: str, otp: str, ttl: int = 300) -> None:
    """Store OTP with expiry (default 5 min)."""
    _OTP_STORE[email] = (otp, time.time() + ttl)


def _verify_otp(email: str, otp: str) -> bool:
    """Check OTP and remove it on success."""
    entry = _OTP_STORE.pop(email, None)
    if not entry:
        return False
    stored_otp, expiry = entry
    if time.time() > expiry:
        return False
    return secrets.compare_digest(stored_otp, otp)


# ----------------------------------------------------------------------
# Session helpers
# ----------------------------------------------------------------------
SESSION_KEY = "admin_user"
# Last sleep/dream cycle, shared between the trigger and the status
# route: the worker is a daemon thread and the request has
# already returned, so this is the only place they can meet.
_LAST_SLEEP_CYCLE: dict = {}
SESSION_EXPIRY_DAYS = 30


def _login_user(email: str) -> None:
    """Create a permanent session for the given e‑mail."""
    session.permanent = True
    session[SESSION_KEY] = email
    # Flask‑Session will handle the 30‑day expiry via PERMANENT_SESSION_LIFETIME


def _logout_user() -> None:
    session.clear()


def _current_user() -> Optional[str]:
    return session.get(SESSION_KEY)


def _bypass_or_none():
    """Loopback admin identity, when the bypass is enabled for this request.

    Logs every use at WARNING. An authentication exception that leaves no trace
    is indistinguishable from a compromise, which is the opposite of what an
    exception is for.
    """
    from . import localauth

    ident = localauth.bypass_identity(request.remote_addr)
    if ident is None:
        return None
    logger.warning(
        "LOCAL AUTH BYPASS used by %s for %s -- loopback admin granted",
        request.remote_addr,
        request.path,
    )
    return ident


def _current_user_data() -> Optional[dict]:
    email = _current_user()
    if not email:
        # The loopback bypass is a first-class identity here, not only a gate in
        # the decorators: templates render `user` and the nav reads the role, and
        # a bypassed request that reached the view with user=None and role="user"
        # would render a half-authenticated page.
        return _bypass_or_none()
    conn = get_db_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(user) if user else None
    finally:
        conn.close()


def login_required(view):
    """Decorator that protects admin routes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        # Validate the RESOLVED identity, not the cookie contents.
        #
        # _current_user() returns whatever string the session holds, so a stale
        # cookie naming an address that was deleted from the store used to pass
        # this gate and render the full admin area: /admin/brain returned 200 for
        # an address that no longer existed. An identity that cannot be resolved
        # is not an identity.
        if _current_user_data() is None and _bypass_or_none() is None:
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Decorator that requires admin role."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _current_user_data()
        if user is None:
            # Fall back to the loopback bypass rather than 403ing, so a bypassed
            # request passes both gates instead of failing the second one.
            user = _bypass_or_none()
        if user is not None and user.get("role") == "admin":
            return view(*args, **kwargs)

        # A session naming an address that is not in the store, or one that has
        # been deactivated or demoted, is not a permissions failure to show the
        # browser: it is an expired identity. login_required already sends a
        # visitor with no session to the login page, so a stale one deserves the
        # same treatment. A bare 403 here was reported by the owner as "Forbidden"
        # on /admin/users while /admin/brain rendered normally in the same
        # session -- the two routes disagreeing about the same identity.
        #
        # The test is on the RESOLVED user, not on the session cookie. A session
        # whose email is absent from the store still yields a non-empty
        # _current_user(), so checking that would send a genuinely-unknown
        # identity straight back to 403 -- the exact bug this fixes.
        if _current_user_data() is None and _current_user() is not None:
            return redirect(url_for("admin.login", next=request.path))
        abort(403)

    return wrapped


# ----------------------------------------------------------------------
# Template context processors
# ----------------------------------------------------------------------


def _build_nav_menu(current_endpoint: str, user_role: str = None) -> str:
    """Build the top navigation.

    Memória, RAG, FlyBrain and the 3D explorer are grouped under a single
    "Cérebro" entry, per the IA decision that all brain subsystems live in one
    section. The C páginas themselves render ``_build_subnav()`` as tabs.
    """
    lang = get_language()

    # (endpoint, i18n key, fallback label, url)
    # .env was removed from the nav on 2026-09-27 by owner decision: a raw
    # env-file editor is a maintenance tool, not primary navigation, and
    # /admin/config already covers configuration. The page stays reachable at
    # /admin/env by URL.
    links = [
        ("admin.config_manager", "nav.config", "Configuração", "/admin/config"),
        ("admin.user_manager", "nav.users", "Utilizadores", "/admin/users"),
    ]
    brain_endpoints = {
        "admin.brain_hub",
        "admin.memory_viewer",
        "rag_viewer",
        "admin.flybrain_manager",
        "admin.explorer_3d",
    }
    brain_active = current_endpoint in brain_endpoints

    parts = [
        # The bar and the toggle must be siblings: the toggle is a fixed-size
        # button that stays visible while .nav-menu becomes a full-screen
        # overlay below 900px, so it cannot live *inside* the collapsing menu.
        '<div class="nav-bar">',
        # data-label-* feed the shared design.js(), which would otherwise label
        # this Portuguese UI in English.
        '<button type="button" class="nav-toggle" aria-controls="nav-menu" '
        'aria-expanded="false" aria-label="Abrir menu" '
        'data-label-open="Abrir menu" data-label-close="Fechar menu">'
        "<span></span><span></span><span></span></button>",
        '<nav class="nav-menu" id="nav-menu">',
        # The brand is the way home, and home is the device UI at "/", not the
        # admin dashboard.
        '  <a class="nav-brand" href="/"><span class="mark">P</span><span>Phantasma</span></a>',
        '  <div class="nav-group">',
        f'    <a href="/admin/brain" class="nav-link{" active" if brain_active else ""}">'
        f"🧠 {t('nav.brain', lang)}</a>",
    ]
    for endpoint, key, fallback, url in links:
        active = " active" if endpoint == current_endpoint else ""
        parts.append(
            f'    <a href="{url}" class="nav-link{active}">{t(key, lang, _fallback=fallback)}</a>'
        )
    parts.append("  </div>")
    parts.append('  <div class="nav-spacer"></div>')
    parts.append('  <div class="nav-sep"></div>')
    parts.append(_build_language_switch(lang))
    parts.append(
        f'  <a href="/admin/logout" class="nav-link" '
        f'style="color:var(--destructive)">{t("nav.logout", lang)}</a>'
    )
    parts.append("</nav>")
    parts.append("</div>")
    return "\n".join(parts)


def _build_language_switch(lang: str) -> str:
    """Segmented PT/EN control, mirroring the reference SegmentedControl."""
    buttons = []
    for code, label in LANGUAGES.items():
        pressed = "true" if code == lang else "false"
        buttons.append(
            f'<button type="button" aria-pressed="{pressed}" '
            f"onclick=\"location.href='/admin/lang/{code}'\">{label}</button>"
        )
    return (
        f'<div class="segmented" role="group" '
        f'aria-label="{t("nav.language", lang)}">{"".join(buttons)}</div>'
    )


def _build_subnav(current_endpoint: str) -> str:
    """Tab bar shared by every Cérebro page.

    As requested, the single "Tudo" / "All" tab has been retired since the hub
    consolidates all subsystems on a single screen. Returns an empty string.
    """
    return ""


@admin_bp.context_processor
def inject_globals():
    """Inject common template variables, including the active language."""
    lang = get_language()
    return {
        "_current_user_data": _current_user_data,
        "lang": lang,
        "t": lambda key, **kw: t(key, lang, **kw),
    }


# ----------------------------------------------------------------------
# User management
# ----------------------------------------------------------------------


def create_user(email: str, password: str, role: str = "user") -> dict:
    conn = get_db_connection()
    try:
        password_hash = hash_password(password)
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO users (email, password_hash, role, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (email, password_hash, role, now, now),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(user) if user else None
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(email: str, password: str) -> Optional[dict]:
    conn = get_db_connection()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)
        ).fetchone()
        if user and verify_password(user["password_hash"], password):
            return dict(user)
        return None
    finally:
        conn.close()


def get_all_users() -> list[dict]:
    conn = get_db_connection()
    try:
        users = conn.execute(
            "SELECT id, email, role, is_active, created_at, updated_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(u) for u in users]
    finally:
        conn.close()


def update_user_role(user_id: int, role: str) -> bool:
    conn = get_db_connection()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
            (role, now, user_id),
        )
        conn.commit()
        return conn.rowcount > 0
    finally:
        conn.close()


def delete_user(user_id: int) -> bool:
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return conn.rowcount > 0
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Config management
# ----------------------------------------------------------------------


def get_configs_by_category() -> dict:
    conn = get_db_connection()
    try:
        configs = conn.execute("SELECT * FROM config ORDER BY category, key").fetchall()
        result = {}
        for c in configs:
            cat = c["category"]
            if cat not in result:
                result[cat] = []
            result[cat].append(dict(c))
        return result
    finally:
        conn.close()


def update_config(key: str, value: str) -> bool:
    conn = get_db_connection()
    try:
        conn.execute("UPDATE config SET value = ? WHERE key = ?", (value, key))
        conn.commit()
        return conn.rowcount > 0
    finally:
        conn.close()


def get_categories() -> list[dict]:
    conn = get_db_connection()
    try:
        cats = conn.execute("SELECT * FROM config_categories ORDER BY display_order").fetchall()
        return [dict(c) for c in cats]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# .env helpers
# ----------------------------------------------------------------------
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def _read_env() -> dict[str, str]:
    """Parse ``.env`` into a flat dict (no sections, no quoting rules)."""
    data: dict[str, str] = {}
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _write_env(data: dict[str, str]) -> None:
    """Write ``data`` back to ``.env`` (simple key=value lines)."""
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
BASE_STYLE = (
    "<style>"
    + design_css()
    + """
.auth-shell {
    display:flex; align-items:center; justify-content:center;
    min-height:100vh; padding:2rem;
}
.auth-card {
    background:var(--surface); border:1px solid var(--border);
    border-radius:var(--radius-lg); padding:var(--sp-6);
    width:100%; max-width:420px; box-shadow:var(--shadow-2);
}
.form-group { margin-bottom:var(--sp-4); }
label { display:block; font-size:var(--fs-small); font-weight:600;
       margin-bottom:var(--sp-1); color:var(--text-secondary); }
</style>
<script>"""
    + design_js()
    + """</script>"""
)
LOGIN_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Admin Login | pHantasma</title>
<div class="container">
    {{ nav_menu | safe }}
    <h1>Admin Login</h1>
    <p class="subtitle">Insira o seu e-mail para receber o código de acesso</p>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <ul class="flash-messages">
          {% for m in messages %}<li>{{ m }}</li>{% endfor %}
        </ul>
      {% endif %}
    {% endwith %}
    <form method="post">
      <div class="form-group">
        <label for="email">E-mail</label>
        <input type="email" id="email" name="email" required autocomplete="email" placeholder="admin@exemplo.com">
      </div>
      <button type="submit">Enviar código</button>
    </form>
</div>
</html>
"""
)

OTP_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Código de acesso | pHantasma</title>
<div class="container">
    <h1>Código enviado</h1>
    <p class="subtitle">Verifique o seu e-mail e insira o código de 6 dígitos abaixo</p>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <ul class="flash-messages">
          {% for m in messages %}<li>{{ m }}</li>{% endfor %}
        </ul>
      {% endif %}
    {% endwith %}
    <form method="post">
      <div class="form-group">
        <label for="otp">Código (6 dígitos)</label>
        <input type="text" id="otp" name="otp" maxlength="6" required autocomplete="one-time-code" inputmode="numeric" placeholder="000000">
      </div>
      <button type="submit">Entrar</button>
    </form>
</div>
</body>
</html>
"""
)

ADMIN_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Admin – Configuração | pHantasma</title>
<div class="container">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>Configuração (.env)</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong> —
            <a href="{{ url_for('admin.logout') }}" class="logout-link">Sair</a>
        </div>
    </div>
    <p class="subtitle">Edite as variáveis de ambiente. Uma por linha: CHAVE=VALOR</p>
    <form method="post">
      <div class="form-group">
        <label for="env">Variáveis</label>
        <textarea id="env" name="env" rows="30" spellcheck="false">{{ env }}</textarea>
      </div>
      <button type="submit">Guardar</button>
    </form>
</div>
</body>
</html>
"""
)

EMAIL_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>{{ subject }}</title>
<div class="container" style="max-width: 560px;">
    <h1 style="text-align: center; color: var(--accent);">pHantasma</h1>
    <div class="email-body">
        <p style="margin-top: 0; font-size: 1.1rem;">{{ body }}</p>
        {% if otp %}
        <div style="text-align: center; margin: 1rem 0;">
            <span class="otp-code">{{ otp }}</span>
        </div>
        {% endif %}
        <hr style="border-color: var(--border); margin: 1.5rem 0;">
        <p style="margin-bottom: 0; font-size: 0.75rem; color: var(--muted);">
            Este é um e-mail automático do sistema pHantasma. Não responda a esta mensagem.<br><br>
            <strong>Detalhes técnicos:</strong><br>
            - Código de acesso: <span class="otp-code" style="font-size: 1rem;">{{ otp }}</span><br>
            - Expira em: 5 minutos<br>
            - Para segurança, não partilhe este código com ninguém.<br>
        </p>
    </div>
</div>
</body>
</html>
"""
)

DASHBOARD_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Painel de Administração | pHantasma</title>
<div class="container" style="max-width: 1200px;">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>Painel de Administração</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong>
        </div>
    </div>
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.5rem; margin-bottom: 2rem;">
        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; text-align: center;">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">👥</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent);">{{ user_count }}</div>
            <div style="color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem;">Utilizadores Registados</div>
        </div>
        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; text-align: center;">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">⚙️</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent);">{{ config_count }}</div>
            <div style="color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem;">Chaves de Configuração</div>
        </div>
        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; text-align: center;">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🧠</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent);">{{ memory_count }}</div>
            <div style="color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem;">Memórias Guardadas</div>
        </div>
        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; text-align: center;">
            <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🧬</div>
            <div style="font-size: 1.5rem; font-weight: 700; color: var(--accent);">{{ graph_count }}</div>
            <div style="color: var(--muted); font-size: 0.875rem; margin-top: 0.25rem;">Nós do Grafo</div>
        </div>
    </div>

    <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem;">
        <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0;">FlyBrain - Estado de Reforço</h2>
        <p style="color: var(--muted); font-size: 0.875rem;">Última atualização: {{ flybrain_state.updated_at if flybrain_state else 'Sem registo' }}</p>
        {% if flybrain_state %}
        <pre style="background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; color: var(--text); overflow-x: auto; font-family: monospace;">{{ flybrain_state.data }}</pre>
        {% else %}
        <p style="color: var(--text); font-style: italic;">Sem estado de reforço do FlyBrain registado.</p>
        {% endif %}
    </div>

    <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem;">
        <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0;">Serviço</h2>
        <ul style="color: var(--text); font-size: 0.875rem; padding-left: 1.5rem; line-height: 1.6;">
            <li><strong>API:</strong> <code style="background: var(--bg-color); padding: 0.2rem 0.4rem; border-radius: 4px;">/api/health</code></li>
            <li><strong>Interface de voz:</strong> <a href="/" style="color: var(--accent);">/</a></li>
            <li><strong>Explorador 3D:</strong> <a href="/memory/3d" style="color: var(--accent);">/memory/3d</a></li>
        </ul>
    </div>
</div>
</body>
</html>
"""
)

CONFIG_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Gestão de Configurações | pHantasma</title>
<form method="post" class="container" style="max-width: 1200px;">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>Gestão de Configurações</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong>
        </div>
    </div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <ul class="flash-messages">
          {% for m in messages %}<li>{{ m }}</li>{% endfor %}
        </ul>
      {% endif %}
    {% endwith %}

    {% for category in categories %}
    <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem;">
        <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 0.5rem;">{{ category.name }}</h2>
        <p style="color: var(--muted); font-size: 0.875rem; margin-bottom: 1.5rem;">{{ category.description }}</p>

        <div class="cfg-grid">
            {% for config in configs.get(category.name, []) %}
            <div class="cfg-card">
                <div style="margin-bottom: 1rem;">
                    <label for="cfg-{{ config.key }}" style="font-weight: 600; font-size: 0.875rem; color: var(--text); word-break: break-all;">{{ config.key }}</label>
                    {% if config.description %}
                    <p id="cfg-desc-{{ config.key }}" style="color: var(--muted); font-size: 0.75rem; margin: 0.25rem 0 0;">{{ config.description }}</p>
                    {% endif %}
                </div>
                <div>
                    {% if config.is_sensitive %}
                    <input type="password" id="cfg-{{ config.key }}" name="config_{{ config.key }}" value="{{ config.value }}"{% if config.description %} aria-describedby="cfg-desc-{{ config.key }}"{% endif %} class="cfg-input" style="width: 100%; padding: 0.5rem; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-size: 0.875rem;">
                    {% else %}
                    <input type="text" id="cfg-{{ config.key }}" name="config_{{ config.key }}" value="{{ config.value }}"{% if config.description %} aria-describedby="cfg-desc-{{ config.key }}"{% endif %} class="cfg-input" style="width: 100%; padding: 0.5rem; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-size: 0.875rem;">
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endfor %}
    <button type="submit" name="config_submit">Atualizar Configurações</button>

    <section style="background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 1.5rem; margin-top: 2rem;">
      <h2 style="color: var(--accent); font-size: 1.25rem; margin: 0 0 .5rem;">
        Persona e reacções</h2>
      <p style="color: var(--muted); font-size: .875rem; margin-bottom: 1.25rem;">
        Como o Phantasma fala, e quanto cada reacção o reforça. Vale no chat e
        no Discord, e aplica-se à próxima mensagem sem reiniciar.
      </p>
      <label for="persona" style="font-weight: 600; font-size: .875rem;">Persona</label>
      <textarea name="persona" id="persona" rows="16"
        style="width: 100%; font-family: monospace;">{{ persona }}</textarea>
      <p style="margin-top: 1rem; display: flex; gap: .75rem; flex-wrap: wrap;">
        <button type="submit" name="action" value="save_persona">Guardar persona</button>
        <button type="submit" name="action" value="reset_persona">Voltar ao original</button>
      </p>
      <p style="color: var(--muted); font-size: .8rem;">
        {% if persona_overridden %}Personalizada.{% else %}Estás a usar o original.{% endif %}
      </p>
      <h3 style="font-size: 1rem; margin: 2rem 0 .5rem;">Peso das reacções</h3>
      <p style="color: var(--muted); font-size: .875rem; margin-bottom: .75rem;">
        Positivo ensina, negativo afasta.
      </p>
      <table style="width: 100%; border-collapse: collapse;">
        {% for emoji, weight in weights.items() if emoji in default_weights %}
        <tr>
          <td style="font-size: 1.5rem; width: 4rem;">{{ emoji }}</td>
          <td><input type="number" step="0.1" name="w_{{ loop.index0 }}"
                     value="{{ weight }}" style="width: 6rem;"></td>
          <td style="padding-left: 1rem; color: var(--muted);">
            {% if weight > 0 %}reforça{% elif weight < 0 %}enfraquece{% else %}neutro{% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
      <p style="margin-top: 1rem; display: flex; gap: .75rem;">
        <button type="submit" name="action" value="save_weights">Guardar pesos</button>
        <button type="submit" name="action" value="reset_weights">Voltar aos originais</button>
      </p>
    </section>

    </form>
"""
)

USERS_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Gestão de Utilizadores | pHantasma</title>
<div class="container" style="max-width: 1200px;">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>Gestão de Utilizadores</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong>
        </div>
    </div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <ul class="flash-messages">
          {% for m in messages %}<li>{{ m }}</li>{% endfor %}
        </ul>
      {% endif %}
    {% endwith %}

    <div style="display: grid; grid-template-columns: 1fr 2fr; gap: 2rem;">
        <form method="post" style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; height: fit-content;">
            <input type="hidden" name="action" value="create">
            <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1.5rem;">Adicionar Utilizador</h2>
            <div class="form-group">
                <label for="email">E-mail</label>
                <input type="email" id="email" name="email" required placeholder="user@exemplo.com">
            </div>
            <div class="form-group">
                <label for="password">Palavra-passe</label>
                <input type="password" id="password" name="password" required style="width: 100%; padding: 0.75rem 1rem; background: var(--bg-color); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; box-sizing: border-box;">
            </div>
            <div class="form-group">
                <label for="role">Perfil</label>
                <select id="role" name="role" style="width: 100%; padding: 0.75rem 1rem; background: var(--bg-color); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 1rem; box-sizing: border-box;">
                    <option value="user">Utilizador</option>
                    <option value="admin">Administrador</option>
                </select>
            </div>
            <button type="submit" style="margin-top: 1rem;">Criar Utilizador</button>
        </form>

        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; overflow-x: auto;">
            <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1.5rem;">Utilizadores Registados</h2>
            <table style="width: 100%; border-collapse: collapse; font-size: 0.875rem;">
                <thead>
                    <tr style="border-bottom: 1px solid var(--border); text-align: left;">
                        <th style="padding: 0.75rem 1rem; color: var(--muted);">E-mail</th>
                        <th style="padding: 0.75rem 1rem; color: var(--muted);">Perfil</th>
                        <th style="padding: 0.75rem 1rem; color: var(--muted);">Estado</th>
                        <th style="padding: 0.75rem 1rem; color: var(--muted); text-align: right;">Ações</th>
                    </tr>
                </thead>
                <tbody>
                    {% for u in users %}
                    <tr style="border-bottom: 1px solid var(--border);">
                        <td style="padding: 0.75rem 1rem; font-weight: 500;">{{ u.email }}</td>
                        <td style="padding: 0.75rem 1rem;">
                            <span style="background: rgba(34, 197, 94, 0.15); color: var(--accent); padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; text-transform: uppercase;">{{ u.role }}</span>
                        </td>
                        <td style="padding: 0.75rem 1rem;">
                            <span style="color: {{ 'var(--accent)' if u.is_active else 'var(--destructive)' }}">{{ 'Ativo' if u.is_active else 'Inativo' }}</span>
                        </td>
                        <td style="padding: 0.75rem 1rem; text-align: right; display: flex; gap: 0.5rem; justify-content: flex-end;">
                            <form method="post" style="display: inline-block;">
                                <input type="hidden" name="action" value="update">
                                <input type="hidden" name="user_id" value="{{ u.id }}">
                                <input type="hidden" name="role" value="{{ 'user' if u.role == 'admin' else 'admin' }}">
                                <button type="submit" style="background: none; border: 1px solid var(--border); color: var(--text); padding: 0.25rem 0.5rem; font-size: 0.75rem; border-radius: 4px; cursor: pointer; width: auto;">Alternar Perfil</button>
                            </form>
                            <form method="post" style="display: inline-block;">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="user_id" value="{{ u.id }}">
                                <button type="submit" style="background: var(--destructive); color: #000; padding: 0.25rem 0.5rem; font-size: 0.75rem; border-radius: 4px; cursor: pointer; width: auto;" onclick="return confirm('Tem a certeza?')">Remover</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
</body>
</html>
"""
)

BRAIN_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<html lang="{{ lang if lang is defined else 'pt' }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% if lang == 'en' %}Brain{% else %}Cérebro{% endif %} | pHantasma</title>
</head>
<body class="brain-fullscreen">
{{ nav_menu|safe }}
<main class="page">
  <div class="page-head">
    <h1 class="page-title">🧠 {% if lang == 'en' %}Brain{% else %}Cérebro{% endif %}</h1>
    <p class="page-sub">{% if lang == 'en' %}Memory, RAG, reinforcement and 3D graph on one screen.{% else %}Memória, RAG, reforço e grafo 3D num só ecrã.{% endif %}</p>
  </div>
  {{ subnav|safe }}


  <!-- HUB ------------------------------------------------------------------
       The 3D graph is the stage and it owns the full height; RAG, FlyBrain,
       Memory, pending issues and the summary are panels the submenu swaps
       underneath it. Previously the graph was a 560px iframe with a link to
       open it elsewhere, so the other views were siblings in a second column
       rather than destinations -- on a laptop they were squeezed. Nothing is
       dropped: every counter and list below is the same real payload. -->
  <div class="brain-hub">
    <style>
    /* Translucent rather than a solid slab: the bar reports on the brain,
       and the brain stays visible through it. Falls back to a plain
       translucent background where color-mix is unsupported. */
    .brain-item-link {
      display: flex; gap: var(--sp-2); flex: 1;
      text-decoration: none; color: inherit; cursor: pointer;
    }
    .brain-item-link:hover .muted { color: var(--accent); }
    .sleep-bar {
        display: flex; align-items: center; gap: var(--sp-3);
        margin: 0 0 var(--sp-4); padding: var(--sp-2) var(--sp-3);
        background: color-mix(in srgb, var(--surface) 55%, transparent);
        background: rgba(127,127,127,.14);
        -webkit-backdrop-filter: blur(8px);
        backdrop-filter: blur(8px);
        border: 1px solid var(--border); border-radius: 8px;
        position: sticky; top: 0; z-index: 5;
    }
    .sleep-bar strong { font-size: var(--fs-small); }
    /* The graph stays mounted; the unified panel slides over it, so the
       relationship between what is on screen and what the counters say is
       never broken. */
    #brain-inspect {
        position: absolute;
        inset: 0;
        z-index: 30;
        overflow-y: auto;
        background: color-mix(in srgb, var(--surface) 92%, transparent);
        background: rgba(20, 20, 24, 0.92);
        -webkit-backdrop-filter: blur(6px);
        backdrop-filter: blur(6px);
        display: none;
    }
    #brain-inspect.is-active { display: block; }
    .brain-sub {
        font-size: 1rem; margin: 1.5rem 0 .5rem; color: var(--accent);
    }
    .brain-note { color: var(--muted); font-size: .875rem; margin: 0 0 .75rem; }
    @media (prefers-reduced-transparency: reduce) {
        .sleep-bar { background: var(--surface);
                     -webkit-backdrop-filter: none; backdrop-filter: none; }
    }
    @media (max-width: 640px) {
        .sleep-bar { flex-wrap: wrap; position: static; }
    }
    /* Fullscreen is fixed inset:0 with the stage absolutely positioned over
       the whole hub, so a bar in normal flow would sit underneath the canvas.
       In that mode the control floats over the graph instead. */
    body.brain-fullscreen .sleep-bar {
        position: fixed;
        top: 12px;
        right: 12px;
        left: auto;
        margin: 0;
        max-width: min(28rem, calc(100vw - 24px));
        z-index: 40;
    }
    body.brain-fullscreen .sleep-bar > div:first-child { display: none; }
    </style>
    <nav class="brain-tabs" role="tablist">
      <span class="brain-titlebar">🧠 {% if lang == 'en' %}Brain{% else %}Cérebro{% endif %}</span>
      <button type="button" class="brain-tab is-active" data-tab="graph" role="tab" aria-selected="true">
        {% if lang == 'en' %}3D graph{% else %}Grafo 3D{% endif %}
      </button>
      <button type="button" class="brain-tab" id="brain-inspect-toggle"
              aria-expanded="false" aria-controls="brain-inspect"
              role="button">
        {% if lang == 'en' %}Everything{% else %}Tudo{% endif %}
        {% if stats.gmif_total_gaps or stats.unresolved_edges %}
        <span class="badge">{{ stats.gmif_total_gaps + stats.unresolved_edges }}</span>
        {% endif %}
      </button>
      <a class="brain-link brain-more" href="/memory/3d" target="_blank" rel="noopener">
        {% if lang == 'en' %}Full screen{% else %}Ecrã inteiro{% endif %} ↗
      </a>
    </nav>

    <div class="sleep-bar" id="sonhar">
    <div style="flex:1; min-width:200px;">
      <strong>{% if lang == 'en' %}Sleep &amp; Dream{% else %}Dormir e Sonhar{% endif %}</strong>{% if stats.gmif_total_gaps > 0 or stats.unresolved_edges > 0 %} &mdash; {% if lang == 'en' %}pending issues{% else %}problemas pendentes{% endif %}{% endif %}</strong>
      <span class="muted" style="margin-left:var(--sp-2); font-size:var(--fs-small);">
        {% if stats.gmif_weak_edges > 0 %}{{ stats.gmif_weak_edges }} {% if lang == 'en' %}weak edges (M1/M2){% else %}arestas fracas (M1/M2){% endif %}{% endif %}
        {% if stats.gmif_causal_gaps > 0 %}{% if stats.gmif_weak_edges > 0 %}, {% endif %}{{ stats.gmif_causal_gaps }} {% if lang == 'en' %}causal gaps{% else %}gaps causais{% endif %}{% endif %}
        {% if stats.unresolved_edges > 0 %}{% if stats.gmif_weak_edges > 0 or stats.gmif_causal_gaps > 0 %}, {% endif %}{{ stats.unresolved_edges }} {% if lang == 'en' %}unresolved refs{% else %}refs por resolver{% endif %}{% endif %}
      </span>
    </div>
    <div class="card stat">
      <span class="stat-label">{% if lang == 'en' %}Concepts{% else %}Conceitos{% endif %}</span>
      <span class="stat-value">{{ stats.concepts or 0 }}</span>
    </div>
  </div>
    <div class="brain-stage">
      <section class="brain-panel is-active" data-panel="graph" role="tabpanel">
        <iframe class="brain-frame" src="/memory/3d?embed=1"
                title="{% if lang == 'en' %}3D memory graph{% else %}Grafo 3D de memória{% endif %}"
                loading="lazy"></iframe>
      </section>

      <section class="brain-panel" id="brain-inspect" data-panel="inspect"
               aria-label="Tudo">
        <div class="brain-head">
          <h2 class="brain-title">{% if lang == 'en' %}Everything{% else %}Tudo{% endif %}</h2>
          <button data-sleep class="btn btn--primary" style="white-space:nowrap;"
                  onclick="triggerSleep()">
            🌙 {% if lang == 'en' %}Sleep &amp; Dream{% else %}Dormir e Sonhar{% endif %}
          </button>
        </div>

        <h3 class="brain-sub">{% if lang == 'en' %}Unresolved references{% else %}Referências por resolver{% endif %}</h3>
        <p class="brain-note">
          {% if lang == 'en' %}An edge pointing at a node that does not exist.
            Choose where each end belongs:{% else %}Uma aresta aponta para um nó que
            não existe. Diz para onde cada extremidade deve ligar:{% endif %}
        </p>
        {% if refs %}
        <table style="width:100%; border-collapse:collapse; margin-bottom:1.5rem;">
          {% for r in refs %}
          <tr>
            <td style="padding: .4rem; font-family: monospace; font-size: .8rem;">
              {{ r['label'] }} &mdash; {{ r['side'] }}
            </td>
            <td style="padding: .4rem;">
              <select data-ref="{{ r['edge_key'] }}"
                      data-side="{{ r['side'] }}"
                      style="width: 100%; min-width: 12rem;">
                <option value="">{% if lang == 'en' %}-- pick a node --{% else %}-- escolher nó --{% endif %}</option>
                {% for n in nodes %}
                <option value="{{ n.label }}">{{ n.label }}</option>
                {% endfor %}
              </select>
            </td>
            <td style="padding: .4rem;">
              <select data-ref-edge="{{ r['edge_key'] }}" data-ref-side="{{ r['side'] }}"
                      style="width: 7rem;">
                <option value="relink">{% if lang == 'en' %}relink{% else %}relacionar{% endif %}</option>
                <option value="promote">{% if lang == 'en' %}promote{% else %}promover{% endif %}</option>
              </select>
              <button class="btn" data-resolve-btn>{% if lang == 'en' %}Apply{% else %}Aplicar{% endif %}</button>
            </td>
          </tr>
          {% endfor %}
        </table>
        <p class="brain-note" id="ref-status" role="status"></p>
        {% else %}
        <p class="brain-note" style="color: var(--success);">
          {% if lang == 'en' %}No unresolved references.{% else %}Nenhuma referência por resolver.{% endif %}
        </p>
        {% endif %}

        <h3 class="brain-sub">{% if lang == 'en' %}Knowledge{% else %}Conhecimento{% endif %}</h3>
        <div class="brain-cards">
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}Concepts{% else %}Conceitos{% endif %}</span>
            <span class="stat-value">{{ stats.concepts or 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}Links{% else %}Ligações{% endif %}</span>
            <span class="stat-value">{{ stats.links or 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}RAG chunks{% else %}Chunks RAG{% endif %}</span>
            <span class="stat-value">{{ stats.chunks or 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}FlyBrain steps{% else %}Passos FlyBrain{% endif %}</span>
            <span class="stat-value">{{ flybrain.steps if flybrain else 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}GMIF gaps{% else %}Lacunas GMIF{% endif %}</span>
            <span class="stat-value">{{ stats.gmif_weak_edges or 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}Unresolved{% else %}Por resolver{% endif %}</span>
            <span class="stat-value">{{ stats.unresolved_edges or 0 }}</span>
          </div>
          <div class="card stat">
            <span class="stat-label">{% if lang == 'en' %}With Mermaid{% else %}Com Mermaid{% endif %}</span>
            <span class="stat-value">{{ stats.memories_with_mermaid or 0 }}</span>
          </div>
        </div>

        <h3 class="brain-sub">{% if lang == 'en' %}Recent memories{% else %}Memórias recentes{% endif %}</h3>
        <a class="brain-link brain-more" href="/admin/brain/knowledge">
          {% if lang == 'en' %}Edit or delete{% else %}Editar ou apagar{% endif %} ↗
        </a>
        <ul class="brain-list">
          {% for m in memories[:40] %}
          <li class="brain-item">
            <a class="brain-item-link" href="/admin/brain/knowledge?mem_id={{ m.id }}">
              <span>#{{ m.id }}</span>
              <span class="muted">{{ (m.text or '')[:150] }}</span>
            </a>
          </li>
          {% endfor %}
        </ul>
      </section>
    </div>
  </div>
</main>
<style>
/* ===========================================================================
   BRAIN HUB -- layout contract, measured rather than guessed.
     - The 3D graph is the page: mounted once, never unmounted, filling the
       whole stage. The other views are drawers that slide OVER it, so changing
       view costs nothing and the graph never refetches.
     - .brain-stage is an absolutely positioned layer covering the whole hub, so
       the submenu has to sit above it (z-index 6) or the iframe swallows every
       click on the menu.
     - The stage is `inset:0` inside a relatively positioned hub whose height
       comes from the viewport, so the graph is always exactly as tall as the
       space left over, whatever the drawer on top of it contains.
   =========================================================================== */
/* ===========================================================================
   FULLSCREEN. Scoped to .brain-fullscreen (set on <body> of this page only),
   so none of it leaks to the other admin pages.
   The graph is the whole viewport: the hub is a fixed layer at inset:0, and
   the page chrome (title, submenu) floats over the graph instead of pushing it
   down. Before this the header took 120px of a 900px window and the graph also
   produced a 110px document scroll -- on a laptop that is a graph in a panel.
   =========================================================================== */
html:has(body.brain-fullscreen), body.brain-fullscreen { overflow:hidden; height:100%; }
body.brain-fullscreen .page { padding:0; margin:0; height:100dvh; max-width:none; }
/* The title now lives in the floating bar; the static block would otherwise
   reserve 83px of flow above the graph. */
body.brain-fullscreen .page-head { display:none; }
body.brain-fullscreen .brain-hub { position:fixed; inset:0; height:100dvh; min-height:0; }
.brain-titlebar {
  font-weight:var(--fw-h2); font-size:var(--fs-h3); color:var(--text);
  padding:0 var(--sp-2) 0 var(--sp-1); white-space:nowrap; }
.brain-tabs {
  /* top:45px clears the admin nav-bar, which is 45px tall and z-index 50 --
     leaving the menu at top:0 put the two bars on top of each other and every
     click on the menu hit the nav instead. z-index 60 keeps the menu above it. */
  position:absolute; top:45px; left:0; right:0; z-index:60;
  display:flex; flex-wrap:wrap; align-items:center; gap:var(--sp-2);
  padding:var(--sp-2); border:1px solid var(--border); border-radius:var(--radius-md);
  background:color-mix(in srgb, var(--surface,#161616) 90%, transparent);
  backdrop-filter:blur(6px); }
.brain-stage { position:absolute; inset:0; }
.brain-panel {
  display:none; height:100%; min-height:0; overflow-y:auto; padding:var(--sp-3);
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-md); }
.brain-panel[data-panel="graph"] { display:block !important; padding:0; overflow:hidden; }
/* The submenu floats over the stage, so a drawer must leave room for it:
   52px measured nav height + the panel's own gap, otherwise the first row of
   content (the Sleep & Dream button, the panel titles) sits UNDER the menu and
   cannot be clicked. */
.brain-panel:not([data-panel="graph"]) {
  /* 45 nav-bar + 52 menu + gap: the drawer's first row must clear BOTH bars. */
  position:absolute; inset:0; z-index:5; padding-top:calc(45px + 52px + var(--sp-3)); }
.brain-panel:not([data-panel="graph"]).is-active { display:block; }
.brain-tab {
  padding:6px 12px; font-size:var(--fs-small); font-family:inherit; cursor:pointer;
  color:var(--text-secondary); background:transparent;
  border:1px solid transparent; border-radius:var(--radius-sm); }
.brain-tab:hover { color:var(--text); background:rgba(255,255,255,.04); }
.brain-tab.is-active { color:var(--brand-400); border-color:var(--brand-500); background:rgba(255,255,255,.05); }
.brain-jump { border-color:var(--border); }
.brain-more { margin-left:auto; white-space:nowrap; }
.brain-frame { display:block; width:100%; height:100%; border:0; background:var(--bg-color,#0b0b0b); }
.brain-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:var(--sp-3); }
.brain-cards .card { display:flex; flex-direction:column; gap:var(--sp-2); }
.brain-head { display:flex; align-items:center; justify-content:space-between; gap:var(--sp-2); margin-bottom:var(--sp-2); }
.brain-title { margin:0; font-size:var(--fs-h3); font-weight:var(--fw-h2); }
.brain-link { font-size:var(--fs-small); color:var(--brand-400); text-decoration:none; white-space:nowrap; }
.brain-link:hover { text-decoration:underline; }
.brain-note { margin:0 0 var(--sp-2); font-size:var(--fs-small); color:var(--muted); }
.brain-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:var(--sp-2); }
.brain-item { display:flex; flex-direction:column; gap:2px; padding:var(--sp-2); background:var(--bg-color,#0b0b0b); border-radius:var(--radius-sm); }
.brain-item-id { color:var(--brand-400); font-size:var(--fs-small); font-variant-numeric:tabular-nums; }
.brain-item-text { color:var(--text-secondary); }
.brain-empty { margin:0; font-size:var(--fs-small); color:var(--muted); font-style:italic; }
.brain-kv { display:flex; justify-content:space-between; gap:var(--sp-2); padding:6px 0; border-bottom:1px solid var(--border); }
.brain-kv b { color:var(--text); font-variant-numeric:tabular-nums; }
@media (max-width:1080px) { .brain-hub { height:calc(100dvh - 150px); } }
@media (max-width:640px)  { .brain-tabs { gap:4px; } .brain-tab { padding:6px 9px; } .brain-more { margin-left:0; } }
</style>
<script>
// Submenu for the brain hub. Panels are toggled by class, never removed, so the
// graph iframe is never torn down and refetched when the view changes.
(function () {
  const tabs = document.querySelectorAll('.brain-tab[data-tab]:not(.brain-jump)');
  const panels = document.querySelectorAll('.brain-panel');
  function select(name) {
    tabs.forEach(t => {
      const on = t.dataset.tab === name;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panels.forEach(p => p.classList.toggle('is-active', p.dataset.panel === name));
  }
  tabs.forEach(t => t.addEventListener('click', () => select(t.dataset.tab)));
  // Shortcut buttons inside a panel reuse the submenu without being tabs
  // themselves, so they must not light up when their target is selected.
  document.querySelectorAll('[data-jump]').forEach(b =>
    b.addEventListener('click', () => select(b.dataset.jump)));
  if (!document.querySelector('.brain-tab.is-active') && tabs.length) select(tabs[0].dataset.tab);
})();
    // One panel, opened from the subnav. The graph stays mounted underneath --
    // the five old tabs each replaced it, and the whole point of the hub is
    // that the graph is the page and the rest slides over it.
    (function () {
      const toggle = document.getElementById('brain-inspect-toggle');
      const panel = document.getElementById('brain-inspect');
      if (!toggle || !panel) return;
      const graph = document.querySelector('.brain-panel[data-panel="graph"]');
      function setOpen(open) {
        panel.classList.toggle('is-active', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (graph) graph.classList.toggle('is-under', open);
        if (open) toggle.classList.add('is-active');
        else toggle.classList.remove('is-active');
      }
      setOpen(false);
      toggle.addEventListener('click', () =>
        setOpen(!panel.classList.contains('is-active')));
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setOpen(false);
      });
    })();

    // Resolving a dangling endpoint. /api/graph/resolve takes side as
    // mandatory: an edge can dangle on both ends, and resolving the wrong one
    // is a silent no-op on the number the owner is watching.
    (function () {
      const status = document.getElementById('ref-status');
      document.querySelectorAll('[data-resolve-btn]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('tr');
          const target = row.querySelector('[data-ref]');
          const mode = row.querySelector('[data-ref-mode]');
          if (!target || !target.value) {
            if (status) status.textContent =
              'Escolhe um nó para esta extremidade.';
            return;
          }
          const edgeKey = mode.dataset.refEdge;
          const side = mode.dataset.refSide;
          btn.disabled = true;
          try {
            const r = await fetch('/api/graph/resolve', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                edge_key: edgeKey, side: side,
                action: mode.value,
                target_label: target.value,
                actor: 'admin',
              }),
            });
            const data = await r.json().catch(() => ({}));
            if (r.ok) {
              if (status) status.textContent =
                'Resolvido. A página vai recarregar.';
              setTimeout(() => location.reload(), 700);
            } else {
              if (status) status.textContent =
                (data && (data.error || data.message)) ||
                ('Falhou: HTTP ' + r.status);
              btn.disabled = false;
            }
          } catch (e) {
            if (status) status.textContent = 'Erro de rede: ' + e;
            btn.disabled = false;
          }
        });
      });
    })();

</script>

    <section id="corrigir" style="background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 1.5rem; margin-top: 2.5rem;">
      <h2 style="color: var(--accent); font-size: 1.25rem; margin: 0 0 .5rem;">
        Corrigir o que ele acredita</h2>
      <p style="color: var(--muted); font-size: .875rem; margin-bottom: 1.25rem;">
        Um nó com afinidade 0.00, ou que é apenas uma resposta do assistente,
        não é conhecimento. Apaga-o. Corrigir um facto errado é teu.
      </p>
      <h3 style="font-size: 1rem; margin: 1.5rem 0 .5rem;">Nós do grafo</h3>
      <form method="post" style="margin-bottom: 2rem;">
        <input type="hidden" name="op" value="delete_node">
        <table style="width: 100%; border-collapse: collapse;">
          <tr><th style="text-align:left;">id</th><th style="text-align:left;">rótulo</th>
              <th>origem</th><th>afinidade</th><th></th></tr>
          {% for n in nodes %}
          <tr>
            <td>{{ n.id }}</td>
            <td style="max-width: 400px;">{{ (n.label or '')[:150] }}</td>
            <td>{{ n.source or '?' }}</td>
            <td>{{ n.affinity if n.affinity is not none else 0 }}</td>
            <td><button type="submit" name="node_id" value="{{ n.id }}">Apagar</button></td>
          </tr>
          {% endfor %}
        </table>
      </form>
      <p style="color: var(--muted); font-size: .8rem; margin: 1.5rem 0 .5rem;">
        <a href="#corrigir">↓ Ir para a correcção do conhecimento</a>
        &nbsp;·&nbsp;
        <a href="#sonhar">↑ Dormir e Sonhar</a>
      </p>
      <h3 style="font-size: 1rem; margin: 1.5rem 0 .5rem;">Memórias</h3>
      <form method="post">
        <input type="hidden" name="op" value="save_memory">
        <select name="mem_id" style="width: 100%; margin-bottom: .75rem;">
          {% for m in memories %}
          <option value="{{ m.id }}">#{{ m.id }} — {{ (m.text or '')[:88] }}</option>
          {% endfor %}
        </select>
        <textarea name="text" rows="6"
          style="width: 100%; font-family: monospace;">{{ selected_text or '' }}</textarea>
        <p style="margin-top: .75rem;"><button type="submit">Guardar texto</button></p>
      </form>
      <form method="post" style="margin-top: 1.25rem;">
        <input type="hidden" name="op" value="delete_memory">
        <input type="number" name="mem_id" placeholder="id"
               style="width: 7rem; margin-right: .5rem;">
        <button type="submit">Apagar memória</button>
      </form>
    </section>
    </body>
</html>
"""
)

RAG_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<html lang="{{ lang }}">
<title>{{ t('rag.title') }} | pHantasma</title>
<div class="page">
  {{ nav_menu | safe }}
  <div class="page-header">
    <div>
      <h1 class="page-title">{{ t('brain.rag') }}</h1>
      <p class="page-subtitle">{{ t('rag.subtitle') }}</p>
    </div>
    <a class="btn btn--secondary" href="/memory/3d">{{ t('brain.explorer') }}</a>
  </div>

  {{ subnav | safe }}

  <div class="grid grid-stats" style="margin-bottom: 1.5rem;">
    <div class="card stat">
      <span class="stat-label">{{ t('dashboard.memories') }}</span>
      <span class="stat-value">{{ stats.memories }}</span>
    </div>
    <div class="card stat">
      <span class="stat-label">{{ t('dashboard.graph_nodes') }}</span>
      <span class="stat-value">{{ stats.nodes }}</span>
    </div>
    <div class="card stat stat--info">
      <span class="stat-label">{{ t('common.all') }}</span>
      <span class="stat-value">{{ stats.links }}</span>
    </div>
    <div class="card stat {% if stats.unresolved_edges %}stat--warning{% endif %}">
      <span class="stat-label">{{ t('memory.title') }} &middot; {{ t('common.filter') }}</span>
      <span class="stat-value">{{ stats.unresolved_edges }}</span>
    </div>
  </div>

  {% if chunks %}
  <div class="card">
    <h2 class="card-title">{{ t('rag.title') }}</h2>
    <div class="card-scroll">
      {% for c in chunks %}
      <article class="card" style="background: var(--bg-color);">
        <div style="display:flex; justify-content:space-between; align-items:center;
                    gap:.5rem; margin-bottom:.5rem;">
          <span class="badge badge--brand">#{{ c.id }}</span>
          <span class="muted" style="font-size:var(--fs-small);">{{ c.timestamp }}</span>
        </div>
        <p style="margin:0 0 .5rem; line-height:1.5;">{{ c.summary }}</p>
        {% if c.tags %}
        <div style="display:flex; gap:.25rem; flex-wrap:wrap; margin-bottom:.5rem;">
          {% for tag in c.tags %}<span class="badge">{{ tag }}</span>{% endfor %}
        </div>
        {% endif %}
        {% if c.facts %}
        <ul class="muted" style="margin:.25rem 0 0; padding-left:1.1rem; font-size:var(--fs-small);">
          {% for f in c.facts[:5] %}<li>{{ f }}</li>{% endfor %}
        </ul>
        {% endif %}
      </article>
      {% endfor %}
    </div>
  </div>
  {% else %}
  <div class="empty">{{ t('rag.empty') }}</div>
  {% endif %}
</div>
</html>
"""
)

MEMORY_TEMPLATE = (
    BASE_STYLE
    + """
<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>Memória do pHantasma | pHantasma</title>
<div class="container" style="max-width: 1200px;">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>Memória do pHantasma</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong>
        </div>
    </div>

    {{ subnav | safe }}

    <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem;">
        <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1rem;">Estado do Tópico de Conversação</h2>
        {% if topic %}
        <div style="background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; font-family: monospace;">
            <div><strong>Tópico Atual:</strong> {{ topic.current_key }}</div>
            <div style="margin-top: 0.5rem; font-size: 0.8rem; color: var(--muted);">Atualizado em: {{ topic.updated_at }}</div>
        </div>
        {% else %}
        <p style="color: var(--text); font-style: italic;">Sem estado de tópico de conversação registado.</p>
        {% endif %}
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr)); gap: 2rem; margin-bottom: 2rem;">
        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem;">
            <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1rem;">{{ t('rag.title') }} <a class="btn btn--ghost btn--sm" href="/admin/rag">{{ t('brain.rag') }} →</a></h2>
            <div style="max-height: 400px; overflow-y: auto; display: flex; flex-direction: column; gap: 1rem;">
                {% for m in memories %}
                <div style="background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px; padding: 1rem;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem;">
                        <span>Registo #{{ m.id }}</span>
                        <span>{{ m.timestamp }}</span>
                    </div>
                    <div style="font-size: 0.9rem; color: var(--text); line-height: 1.5;">{{ m.text }}</div>
                </div>
                {% endfor %}
            </div>
        </div>

        <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem;">
            <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1rem;">Grafo de Conhecimento</h2>
            <div style="max-height: 400px; overflow-y: auto; display: flex; flex-direction: column; gap: 1rem;">
                {% for g in graph %}
                <div style="background: var(--bg-color); border: 1px solid var(--border); border-radius: 6px; padding: 1rem;">
                    <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem;">
                        <span style="font-weight: 600; color: var(--accent);">{{ g.node_type }}</span>
                        <span>Touch: {{ g.touch_count }}</span>
                    </div>
                    <div style="font-size: 0.95rem; font-weight: 600; color: var(--text);">{{ g.label }}</div>
                    {% if g.source or g.target %}
                    <div style="font-size: 0.8rem; color: var(--muted); margin-top: 0.5rem;">
                        {{ g.source or '' }} ➔ {{ g.target or '' }}
                    </div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
</div>

    </div>
        </div>
    </div>
</div>

    <section style="background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 1.5rem; margin-top: 2.5rem;">
      <h2 style="color: var(--accent); font-size: 1.25rem; margin: 0 0 .5rem;">
        Corrigir o que ele acredita</h2>
      <p style="color: var(--muted); font-size: .875rem; margin-bottom: 1.25rem;">
        Um nó com afinidade 0.00, ou que é apenas uma resposta do assistente,
        não é conhecimento. Apaga-o. Corrigir um facto errado é teu.
      </p>
      <h3 style="font-size: 1rem; margin: 1.5rem 0 .5rem;">Nós do grafo</h3>
      <form method="post" style="margin-bottom: 2rem;">
        <input type="hidden" name="op" value="delete_node">
        <table style="width: 100%; border-collapse: collapse;">
          <tr><th style="text-align:left;">id</th><th style="text-align:left;">rótulo</th>
              <th>origem</th><th>afinidade</th><th></th></tr>
          {% for n in nodes %}
          <tr>
            <td>{{ n.id }}</td>
            <td style="max-width: 400px;">{{ (n.label or '')[:150] }}</td>
            <td>{{ n.source or '?' }}</td>
            <td>{{ n.affinity if n.affinity is not none else 0 }}</td>
            <td><button type="submit" name="node_id" value="{{ n.id }}">Apagar</button></td>
          </tr>
          {% endfor %}
        </table>
      </form>
      <h3 style="font-size: 1rem; margin: 1.5rem 0 .5rem;">Memórias</h3>
      <form method="post">
        <input type="hidden" name="op" value="save_memory">
        <select name="mem_id" style="width: 100%; margin-bottom: .75rem;">
          {% for m in memories %}
          <option value="{{ m.id }}">#{{ m.id }} — {{ (m.text or '')[:88] }}</option>
          {% endfor %}
        </select>
        <textarea name="text" rows="6"
          style="width: 100%; font-family: monospace;">{{ selected_text or '' }}</textarea>
        <p style="margin-top: .75rem;"><button type="submit">Guardar texto</button></p>
      </form>
      <form method="post" style="margin-top: 1.25rem;">
        <input type="hidden" name="op" value="delete_memory">
        <input type="number" name="mem_id" placeholder="id"
               style="width: 7rem; margin-right: .5rem;">
        <button type="submit">Apagar memória</button>
      </form>
    </section>

    </body>
</html>
"""
)

FLYBRAIN_TEMPLATE = (
    BASE_STYLE
    + """<!doctype html>
<!-- lang em <html> e WCAG 3.1.1 (nivel A). Sem isto o leitor de
     ecra nao sabe as regras de pronunciacao nem o idioma da pagina. -->
<html lang="{{ lang if lang is defined else 'pt' }}">
<head><meta charset="utf-8">
</head>
<body>
<title>FlyBrain - Aprendizagem por Reforço | pHantasma</title>
<div class="container" style="max-width: 1200px;">
    {{ nav_menu | safe }}
    <div class="header">
        <h1>{{ t('flybrain.title') }}</h1>
        <div class="user-info">
            Logado como <strong>{{ user }}</strong>
        </div>
    </div>

    {{ subnav | safe }}

    <!-- Brain Graph Section -->
    <div class="brain-graph">
        <div class="brain-graph-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 2L2 8h20L12 2ZM12 20h-6l-2-6h-4l-2 6H5v2h14v-2Z"/>
            </svg>
            Rede Neural - Conexões de Memória e Reforço
        </div>
        <svg class="brain-graph-svg" viewBox="0 0 200 200" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="brain-tooltip"></div>
    </div>

    <div style="background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem;">
        <h2 style="color: var(--accent); font-size: 1.25rem; margin-top: 0; margin-bottom: 1rem;">Definição de Parâmetros</h2>
        <p style="color: var(--muted); font-size: 0.875rem; margin-bottom: 1.5rem;">Ajuste os parâmetros de reforço cognitivo e as respetivas associações inteligentes:</p>

        <form id="flybrain-form" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1.5rem; margin-bottom: 1.5rem;">
            <div class="form-group">
                <label for="learning_rate">Taxa de Aprendizagem (Alpha)</label>
                <input type="text" id="learning_rate" value="0.1" style="text-align: left; letter-spacing: normal;">
            </div>
            <div class="form-group">
                <label for="discount_factor">Fator de Desconto (Gamma)</label>
                <input type="text" id="discount_factor" value="0.9" style="text-align: left; letter-spacing: normal;">
            </div>
            <div class="form-group">
                <label for="exploration_rate">Taxa de Exploração (Epsilon)</label>
                <input type="text" id="exploration_rate" value="0.2" style="text-align: left; letter-spacing: normal;">
            </div>
            <div class="form-group">
                <label for="reinforcement_steps">Passos de Reforço</label>
                <input type="text" id="reinforcement_steps" value="1000" style="text-align: left; letter-spacing: normal;">
            </div>
        </form>
        <button id="save-flybrain" style="width: auto; padding: 0.75rem 2rem;">Guardar Parâmetros</button>
    </div>

    <script>
        // Initialize mermaid brain graph
        document.addEventListener('DOMContentLoaded', () => {
            // Sample brain graph data - in production this would come from the FlyBrain state
            const nodes = [
                { id: 'cortex', label: 'Cortex Prefrontal', reinforcement: 0.8 },
                { id: 'hippocampus', label: 'Hipocampo', reinforcement: 0.6 },
                { id: 'amygdala', label: 'Amígdala', reinforcement: 0.4 },
                { id: 'nucleus_accumbens', label: 'Núcleo Accumbens', reinforcement: 0.9 },
                { id: 'cerebellum', label: 'Cérebro', reinforcement: 0.3 }
            ];

            const edges = [
                { source: 'cortex', target: 'hippocampus', strength: 0.7 },
                { source: 'cortex', target: 'amygdala', strength: 0.5 },
                { source: 'hippocampus', target: 'nucleus_accumbens', strength: 0.8 },
                { source: 'amygdala', target: 'nucleus_accumbens', strength: 0.6 },
                { source: 'nucleus_accumbens', target: 'cerebellum', strength: 0.4 }
            ];

            // Initialize Cytoscape.js or use mermaid
            // For now, render mermaid flowchart
            const mermaidContent = \\`graph TD
    cortex -->|reinforcement| hippocampus
    hippocampus -->|reinforcement| nucleus_accumbens
    amygdala -->|reinforcement| nucleus_accumbens
    nucleus_accumbens -->|reinforcement| cerebellum\\`;

            // Initialize Cytoscape for interactive graph
            const cy = cytoscape({
                container: document.querySelector('.brain-graph-svg'),
                elements: [
                    /* nodes */
                    ...nodes.map(n => ({ data: { id: n.id, label: n.label }, classes: n.reinforcement > 0.7 ? 'reinforced' : '' })),
                    /* edges */
                    ...edges.map(e => ({ data: { source: e.source, target: e.target }, classes: 'edge' }))
                ],
                style: [
                    {
                        selector: 'node',
                        style: {
                            'width': 30,
                            'height': 30,
                            'content': 'data(label)',
                            'font-size': '10px',
                            'text-wrap': 'wrap',
                            'text-align': 'center',
                            'border-radius': '15px',
                            'transition': 'all 0.3s',
                            'fill': function(e) { return e.data('classes') === 'reinforced' ? '#00d4aa' : '#2a2a2e'; },
                            'stroke': function(e) { return e.data('classes') === 'reinforced' ? '#00d4aa' : '#666666'; },
                            'stroke-width': function(e) { return e.data('classes') === 'reinforced' ? '3px' : '1.5px'; }
                        }
                    },
                    {
                        selector: 'edge',
                        style: {
                            'stroke': function(e) { return '#666666'; },
                            'stroke-width': function(e) { return '1.5px'; },
                            'line-opacity': 0.5
                        }
                    }
                ],
                layout: {
                    name: 'preset',
                    padding: 10
                }
            });

            // Tooltip functionality
            const tooltip = document.querySelector('.brain-tooltip');
            cy.nodes().forEach(node => {
                node.on('mouseenter', function(e) {
                    const nodeData = cy.collection(e.target).data();
                    tooltip.html(`<strong>${nodeData.id}</strong><br/>Fortalecimento: ${node.data('reinforcement') || 0}</h3>`);
                    tooltip.css({
                        left: e.pageX + 10,
                        top: e.pageY + 10,
                        display: 'block'
                    });
                });
                node.on('mouseleave', function() {
                    tooltip.css('display', 'none');
                });
            });
        });
    // Sleep & Dream trigger for admin brain page
    // Sleep & Dream trigger for the admin brain page.
    //
    // The endpoint answers 202 with status "started": every step calls the LLM,
    // so the cycle takes minutes and cannot be waited out in the request. The
    // old version treated anything that was not "ok" as an error and alerted
    // "Error: Sleep/dream cycle started in background" -- the user saw a failure
    // for a cycle that was in fact running. So: start, then follow
    // /admin/brain/sleep/status until the run actually reports a terminal state.
    const SLEEP_STEP_LABELS = {
      classify_edges: 'classificar arestas',
      classify_nodes: 'classificar nos',
      consolidate_memories: 'consolidar memorias',
      gmif_dream: 'sonhar (GMIF)',
      dream: 'sonhar (notícias e pesquisa)'
    };
    function sleepStatusLine() {
      let line = document.getElementById('sleep-status-line');
      if (!line) {
        line = document.createElement('div');
        line.id = 'sleep-status-line';
        line.className = 'brain-note';
        const host = document.querySelector('.brain-panel.is-active');
        (host || document.body).appendChild(line);
      }
      return line;
    }
    function renderSleepStatus(d) {
      const line = sleepStatusLine();
      const steps = (d && d.steps) || {};
      const names = Object.keys(steps);
      const done = names.length;
      const failed = names.filter(n => steps[n] && steps[n].ok === false);
      const parts = names.map(n => {
        const ok = steps[n] && steps[n].ok;
        const mark = ok === true ? 'ok' : (ok === false ? 'FALHOU' : 'a correr');
        return (SLEEP_STEP_LABELS[n] || n) + ': ' + mark;
      });
      line.textContent = 'Ciclo em curso - ' + done + ' passo(s): ' + (parts.join(' | ') || 'a iniciar');
      if (failed.length) line.style.color = 'var(--destructive)';
    }
    function triggerSleep() {
      if (!confirm('Iniciar o ciclo de sono/sonho? Cada passo chama o LLM e demora minutos.')) return;
      const btns = document.querySelectorAll('.brain-panel.is-active [data-sleep]');
      btns.forEach(b => { b.disabled = true; b.textContent = 'A dormir...'; });
      fetch('/admin/brain/sleep', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin'
      })
        .then(r => r.json().then(d => ({http: r.status, data: d})))
        .then(({http, data}) => {
          if (http !== 202 && data.status !== 'started') {
            // A real refusal: not allowlisted, already running, or a crash.
            sleepStatusLine().textContent = 'Erro: ' + (data.message || ('HTTP ' + http));
            btns.forEach(b => { b.disabled = false; b.textContent = 'Dormir e Sonhar'; });
            return;
          }
          sleepStatusLine().textContent = 'Ciclo iniciado. Acompanhando...';
          pollSleep();
        })
        .catch(err => {
          sleepStatusLine().textContent = 'Erro de rede: ' + err;
          btns.forEach(b => { b.disabled = false; b.textContent = 'Dormir e Sonhar'; });
        });
    }
    function pollSleep() {
      fetch('/admin/brain/sleep/status', {credentials: 'same-origin'})
        .then(r => r.json())
        .then(d => {
          if (d.status === 'running') { renderSleepStatus(d); setTimeout(pollSleep, 3000); return; }
          // Terminal: done, error, or idle. Done means the brain changed, so the
          // counters on the page are stale until it is reloaded.
          if (d.status === 'done') { location.reload(); return; }
          sleepStatusLine().textContent = 'Ciclo terminou com estado: ' + (d.status || 'desconhecido');
          document.querySelectorAll('.brain-panel.is-active [data-sleep]')
            .forEach(b => { b.disabled = false; b.textContent = 'Dormir e Sonhar'; });
        })
        .catch(err => {
          sleepStatusLine().textContent = 'Erro ao consultar o estado: ' + err;
        });
    }
            })
            .catch(error => alert('Network error: ' + error));
        }
    }
    </script>
</div>"""
)


def _email_is_allowed(email: str) -> bool:
    """Is this address permitted to request an admin OTP?

    Access is allowlist-only. Before this, `login()` accepted any address at
    all: it generated an OTP, stored it and mailed it, so a stranger could
    start an admin login. The OTP then landed in the journal (see below), which
    made the allowlist necessary but not sufficient.

    Two sources authorise an address:
      1. An ACTIVE row in the `users` table. These are the users configured
         through /admin/users. `is_active = 0` revokes access without deleting
         the row, so the check honours it.
      2. ADMIN_EMAILS in .env, for the principal admin to stay reachable if
         the users table is ever lost. Comma separated.

    Args:
        email: Candidate address, already lowercased.

    Returns:
        True if the address may receive an OTP.
    """
    extra = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if email in extra:
        return True

    conn = None
    try:
        # get_db_connection() itself can raise, so it must be inside the guard.
        # An allowlist that fails open on a database error is not an allowlist.
        conn = get_db_connection()
        row = conn.execute("SELECT is_active FROM users WHERE email = ?", (email,)).fetchone()
    except Exception:
        # A database error must fail closed, not open.
        logger.error(f"Allowlist lookup failed for {email!r}; denying")
        return False
    finally:
        if conn is not None:
            conn.close()

    if not row:
        return False
    # is_active is stored as an integer flag; treat anything falsy as revoked.
    return bool(row["is_active"])


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Step 1 – ask for e‑mail, send OTP.

    The address must be allowlisted before an OTP is generated. A rejection is
    deliberately indistinguishable in wording from a missing address, so this
    cannot be used to enumerate which addresses are administrators.
    """
    if request.method == "GET":
        nav_menu = _build_nav_menu("admin.login", "user")
        return render_template_string(LOGIN_TEMPLATE, nav_menu=nav_menu)

    # Rate limit BEFORE any work. Unthrottled, this endpoint mailed a fresh
    # six-digit OTP on every call: a loop both bombs the allowlisted mailbox
    # and hands the caller a new 1-in-1,000,000 surface each time, for free.
    if not _ratelimit.login_limiter.allow(_ratelimit.client_key()):
        retry = _ratelimit.login_limiter.retry_after(_ratelimit.client_key())
        logger.warning(
            "Rate limited admin login from %s (retry in %ss)",
            _ratelimit.client_key(),
            retry,
        )
        resp = make_response(
            render_template_string(LOGIN_TEMPLATE, nav_menu=_build_nav_menu("admin.login", "user")),
            429,
        )
        # Deliberately identical wording to the success path: a rate-limit
        # message that says "too many attempts" is still an oracle for how many
        # are left, and this endpoint already refuses to reveal which addresses
        # are administrators.
        resp.headers["Retry-After"] = str(retry)
        return resp

    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("E‑mail obrigatório")
        return redirect(url_for("admin.login"))

    if not _email_is_allowed(email):
        # Do not say "not allowed": that turns /admin/login into an oracle for
        # which addresses are administrators. Same message as a success, minus
        # the redirect, so a prober learns nothing from the response.
        logger.warning(f"Rejected admin login for non-allowlisted {email!r}")
        flash("Se o e‑mail estiver configurado, receberá um código.")
        return redirect(url_for("admin.login"))

    otp = _generate_otp()
    _store_otp(email, otp)
    # NOT logged. This line printed the OTP to the journal, where anything able
    # to read the service log could impersonate an administrator -- including
    # the allowlisted address itself. Kept out of the log deliberately.
    _send_mail(
        to=email,
        subject="pHantasma – Código de acesso administrativo",
        body=f"O seu código de acesso é: {otp}\nExpira em 5 minutos.",
        otp=otp,
    )
    flash("Código enviado para o e‑mail indicado.")
    # Store e‑mail in session temporarily for OTP verification
    session["_otp_email"] = email
    return redirect(url_for("admin.verify_otp"))


@admin_bp.route("/verify", methods=["GET", "POST"], endpoint="verify_otp")
def verify_otp():
    """Step 2 – verify OTP, create session."""
    email = session.get("_otp_email")
    if not email:
        flash("Sessão expirada, comece de novo.")
        return redirect(url_for("admin.login"))

    if request.method == "GET":
        nav_menu = _build_nav_menu("admin.verify_otp", "user")
        return render_template_string(OTP_TEMPLATE, nav_menu=nav_menu)

    otp = request.form.get("otp", "").strip()

    # Budget for guessing, counted separately from the "request a code" budget
    # so a user who mistypes their code can still ask for a fresh one without
    # being locked out by their own typo.
    if not _ratelimit.verify_limiter.allow(_ratelimit.client_key()):
        retry = _ratelimit.verify_limiter.retry_after(_ratelimit.client_key())
        logger.warning(
            "Rate limited OTP verification from %s (retry in %ss)",
            _ratelimit.client_key(),
            retry,
        )
        resp = make_response(
            render_template_string(
                OTP_TEMPLATE, nav_menu=_build_nav_menu("admin.verify_otp", "user")
            ),
            429,
        )
        resp.headers["Retry-After"] = str(retry)
        return resp

    if _verify_otp(email, otp):
        _login_user(email)
        session.pop("_otp_email", None)
        flash("Login bem‑sucedido.")
        nxt = request.args.get("next") or url_for("admin.brain_hub")
        return redirect(nxt)
    else:
        flash("Código inválido ou expirado.")
        return redirect(url_for("admin.verify_otp"))


@admin_bp.route("/logout")
def logout():
    _logout_user()
    flash("Sessão terminada.")
    return redirect(url_for("admin.login"))


@admin_bp.route("/")
@login_required
def index():
    """Admin root: land on the interactive menu, not the .env textarea."""
    return redirect(url_for("admin.brain_hub"))


@admin_bp.route("/env", methods=["GET", "POST"])
@login_required
def env_editor():
    """View / edit .env (raw editor, secondary to the menu)."""
    if request.method == "POST":
        raw = request.form.get("env", "")
        # Very naïve parsing – each non‑empty line "KEY=VALUE"
        new_data: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            new_data[k.strip()] = v.strip()
        _write_env(new_data)
        flash("Configuração guardada.")
        return redirect(url_for("admin.env_editor"))

    env_data = _read_env()
    env_text = "\n".join(f"{k}={v}" for k, v in sorted(env_data.items()))
    nav_menu = _build_nav_menu(
        "admin.env_editor",
        _current_user_data()["role"] if _current_user_data() else "user",
    )
    return render_template_string(
        ADMIN_TEMPLATE, user=_current_user(), env=env_text, nav_menu=nav_menu
    )


# --- Persona e reacções -------------------------------------------------
# One page for the two things the owner kept having to edit in code: the
# persona (which could not be changed at all) and the FlyBrain weight of each
# reaction emoji (a hardcoded dict). Plain language, because the alternative
# on /admin/config is an env-var editor grouped by technical category.

_PERSONA_TEMPLATE = (
    ADMIN_TEMPLATE
    + """
<form method="post" class="container" style="max-width: 760px;">
  {{ nav_menu | safe }}
  <div class="header"><h1>Persona e reacções</h1></div>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul class="flash-messages">
      {% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}

  <section style="margin-bottom: 2rem;">
    <h2>Persona</h2>
    <p style="color: var(--muted);">
      Como o Phantasma fala. Aplica-se à próxima mensagem, sem reiniciar.
    </p>
    <textarea name="persona" rows="18" style="width: 100%; font-family: monospace;">{{ persona }}</textarea>
    <p style="margin-top: 1rem;">
      <button type="submit" name="action" value="save_persona">Guardar persona</button>
      <button type="submit" name="action" value="reset_persona">Voltar ao original</button>
      <button type="submit" name="action" value="test_persona">Guardar e testar</button>
    </p>
    {% if persona_overridden %}
      <p style="color: var(--muted);">Personalizada. O original está em <code>prompts/system.txt</code>.</p>
    {% else %}
      <p style="color: var(--muted);">Estás a usar o original.</p>
    {% endif %}
  </section>

  <section>
    <h2>Peso das reacções</h2>
    <p style="color: var(--muted);">
      Quanto cada reacção reforça ou enfraquece o que o Phantasma aprendeu.
      Positivo ensina, negativo afasta. Vale no chat e no Discord.
    </p>
    <table style="width: 100%; border-collapse: collapse;">
      {% for emoji, weight in weights.items() if emoji in default_weights %}
      <tr>
        <td style="font-size: 1.5rem; width: 4rem;">{{ emoji }}</td>
        <td><input type="number" step="0.1" name="w_{{ loop.index0 }}"
                   value="{{ weight }}"></td>
        <td style="padding-left: 1rem; color: var(--muted);">
          {% if weight > 0 %}reforça{% elif weight < 0 %}enfraquece{% else %}neutro{% endif %}
        </td>
      </tr>
      {% endfor %}
    </table>
    <p style="margin-top: 1rem;">
      <button type="submit" name="action" value="save_weights">Guardar pesos</button>
      <button type="submit" name="action" value="reset_weights">Voltar aos originais</button>
    </p>
  </section>
</form>
"""
)


@admin_bp.route("/persona", methods=["GET", "POST"])
@login_required
def persona_editor():
    """View / edit the persona and the reaction weights."""
    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action in ("save_persona", "test_persona"):
                set_persona(request.form.get("persona", ""), updated_by=_current_user())
                flash("Persona guardada.")
            elif action == "reset_persona":
                reset_persona()
                flash("Persona reposta no original.")
            elif action == "save_weights":
                raw = {k[2:]: v for k, v in request.form.items() if k.startswith("w_")}
                set_reaction_weights(raw, updated_by=_current_user())
                flash("Pesos guardados.")
            elif action == "reset_weights":
                clear_setting(REACTION_WEIGHTS_KEY)
                flash("Pesos repostos nos originais.")
        except ValueError as e:
            flash(str(e))
        return redirect(url_for("admin.persona_editor"))

    nav_menu = _build_nav_menu(
        "admin.persona_editor",
        _current_user_data()["role"] if _current_user_data() else "user",
    )
    return render_template_string(
        _PERSONA_TEMPLATE,
        user=_current_user(),
        nav_menu=nav_menu,
        persona=get_persona(),
        persona_overridden=persona_is_overridden(),
        weights=get_reaction_weights(),
        default_weights=DEFAULT_REACTION_WEIGHTS,
    )


_MEMORY_EDIT_TEMPLATE = (
    ADMIN_TEMPLATE
    + """
<div class="container" style="max-width: 1000px;">
  {{ nav_menu | safe }}
  <div class="header"><h1>Editar conhecimento</h1></div>
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul class="flash-messages">
      {% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}

  <h2>Nos do grafo</h2>
  <p style="color: var(--muted);">
    Um no com afinidade 0.00 e que parece uma resposta do assistente
    ({"source": "assistant"}) nao e conhecimento: apaga-o.
  </p>
  <form method="post">
  <input type="hidden" name="op" value="delete_node">
  <table style="width:100%; border-collapse:collapse; margin-bottom:2rem;">
    <tr><th style="text-align:left;">id</th><th style="text-align:left;">rotulo</th>
        <th>origem</th><th>afinidade</th><th></th></tr>
    {% for n in nodes %}
    <tr>
      <td>{{ n.id }}</td>
      <td style="max-width:420px;">{{ (n.label or '')[:160] }}</td>
      <td>{{ n.source or '?' }}</td>
      <td>{{ n.affinity if n.affinity is not none else 0 }}</td>
      <td><button type="submit" name="node_id" value="{{ n.id }}">Apagar</button></td>
    </tr>
    {% endfor %}
  </table>
  </form>

  <h2>Memorias</h2>
  <p style="color: var(--muted);">
    Edita o texto ou apaga. A correccao de um facto errado e do dono, nao do sistema.
  </p>
  <form method="post">
    <input type="hidden" name="op" value="save_memory">
    <label for="mem_id">Memoria</label>
    <select name="mem_id" id="mem_id" style="width:100%; margin-bottom:0.75rem;">
      {% for m in memories %}
      <option value="{{ m.id }}">#{{ m.id }} — {{ (m.text or '')[:90] }}</option>
      {% endfor %}
    </select>
    <textarea name="text" rows="7" style="width:100%; font-family:monospace;">{{ selected_text or '' }}</textarea>
    <p style="margin-top:0.75rem;">
      <button type="submit" name="action" value="save">Guardar texto</button>
    </p>
  </form>

  <form method="post" style="margin-top:1.5rem;">
    <input type="hidden" name="op" value="delete_memory">
    <label for="del_id">Apagar memoria pelo id</label>
    <input type="number" name="mem_id" id="del_id" style="width:8rem;">
    <button type="submit" name="action" value="delete">Apagar</button>
  </form>
</div>
"""
)


@admin_bp.route("/brain/knowledge", methods=["GET", "POST"])
@login_required
def knowledge_editor():
    """Edit or delete what the assistant believes.

    The graph had no way to be corrected. Assistant replies were being stored
    as knowledge nodes at affinity 0, then retrieved for the next question,
    so one bad answer fed the next. Being able to see and delete a node is how
    the owner stops that; without it the only fix is a database edit by hand.
    """
    if request.method == "POST":
        op = request.form.get("op", "")
        actor = _current_user()
        conn = sqlite3.connect(BRAIN_DB_PATH)
        try:
            if op == "delete_node":
                nid = request.form.get("node_id", type=int)
                row = conn.execute("SELECT * FROM memory_graph WHERE id = ?", (nid,)).fetchone()
                if not row:
                    flash("No nao encontrado.")
                else:
                    before = dict(row)
                    conn.execute("DELETE FROM memory_graph WHERE id = ?", (nid,))
                    conn.execute(
                        "INSERT INTO graph_edit_audit "
                        "(at, actor, op, target, before_json, after_json) "
                        "VALUES (datetime('now'), ?, 'node.delete', ?, ?, NULL)",
                        (actor, f"node:{nid}", json.dumps(before, default=str)),
                    )
                    conn.commit()
                    flash(f"No {nid} apagado.")
            elif op == "save_memory":
                mid = request.form.get("mem_id", type=int)
                text = request.form.get("text", "")
                if not (text or "").strip():
                    flash("O texto da memoria nao pode ficar vazio.")
                else:
                    row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
                    if not row:
                        flash("Memoria nao encontrada.")
                    else:
                        before = dict(row)
                        conn.execute(
                            "UPDATE memories SET text = ? WHERE id = ?",
                            (text, mid),
                        )
                        conn.execute(
                            "INSERT INTO graph_edit_audit "
                            "(at, actor, op, target, before_json, after_json) "
                            "VALUES (datetime('now'), ?, 'memory.edit', ?, ?, ?)",
                            (
                                actor,
                                f"memory:{mid}",
                                json.dumps(before, default=str),
                                json.dumps({"text": text}, ensure_ascii=False),
                            ),
                        )
                        conn.commit()
                        flash(f"Memoria {mid} guardada.")
            elif op == "delete_memory":
                mid = request.form.get("mem_id", type=int)
                row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
                if not row:
                    flash("Memoria nao encontrada.")
                else:
                    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
                    conn.execute(
                        "INSERT INTO graph_edit_audit "
                        "(at, actor, op, target, before_json, after_json) "
                        "VALUES (datetime('now'), ?, 'memory.delete', ?, NULL)",
                        (actor, f"memory:{mid}", json.dumps(dict(row), default=str)),
                    )
                    conn.commit()
                    flash(f"Memoria {mid} apagada.")
        finally:
            conn.close()
        return redirect(url_for("admin.knowledge_editor"))

    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        nodes = [
            dict(r)
            for r in conn.execute(
                "SELECT id, label, source, affinity FROM memory_graph ORDER BY affinity ASC, id"
            )
        ]
        memories = [
            dict(r) for r in conn.execute("SELECT id, text FROM memories ORDER BY id DESC LIMIT 60")
        ]
        sel = request.args.get("mem_id", type=int)
        selected = ""
        if sel:
            row = conn.execute("SELECT text FROM memories WHERE id = ?", (sel,)).fetchone()
            selected = row["text"] if row else ""
    finally:
        conn.close()

    return render_template_string(
        _MEMORY_EDIT_TEMPLATE,
        nodes=nodes,
        memories=memories,
        selected_text=selected,
        user=_current_user(),
        nav_menu=_build_nav_menu(
            "admin.knowledge_editor",
            _current_user_data()["role"] if _current_user_data() else "user",
        ),
    )


def _knowledge_conn():
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _knowledge_nodes():
    conn = _knowledge_conn()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, label, source, affinity FROM memory_graph ORDER BY affinity ASC, id"
            )
        ]
    finally:
        conn.close()


def _knowledge_memories(limit=60):
    conn = _knowledge_conn()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, text FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
    finally:
        conn.close()


def _apply_knowledge_edit(op, req, actor):
    """Apply one knowledge edit and record it.

    A helper because the same three operations are reachable from two
    screens, and an audit trail only one of them writes is not an audit trail.
    """
    import json as _json

    conn = _knowledge_conn()
    try:
        if op == "delete_node":
            nid = req.form.get("node_id", type=int)
            row = conn.execute("SELECT * FROM memory_graph WHERE id = ?", (nid,)).fetchone()
            if not row:
                flash("Nó não encontrado.")
                return
            conn.execute("DELETE FROM memory_graph WHERE id = ?", (nid,))
            _audit(
                conn,
                actor,
                "node.delete",
                f"node:{nid}",
                _json.dumps(dict(row), default=str),
                None,
            )
            flash(f"Nó {nid} apagado.")
        elif op == "save_memory":
            mid = req.form.get("mem_id", type=int)
            text = req.form.get("text", "")
            if not (text or "").strip():
                flash("O texto da memória não pode ficar vazio.")
                return
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
            if not row:
                flash("Memória não encontrada.")
                return
            conn.execute("UPDATE memories SET text = ? WHERE id = ?", (text, mid))
            _audit(
                conn,
                actor,
                "memory.edit",
                f"memory:{mid}",
                _json.dumps(dict(row), default=str),
                _json.dumps({"text": text}, ensure_ascii=False),
            )
            flash(f"Memória {mid} guardada.")
        elif op == "delete_memory":
            mid = req.form.get("mem_id", type=int)
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
            if not row:
                flash("Memória não encontrada.")
                return
            conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            _audit(
                conn,
                actor,
                "memory.delete",
                f"memory:{mid}",
                _json.dumps(dict(row), default=str),
                None,
            )
            flash(f"Memória {mid} apagada.")
        conn.commit()
    finally:
        conn.close()


def _audit(conn, actor, op, target, before, after):
    conn.execute(
        "INSERT INTO graph_edit_audit "
        "(at, actor, op, target, before_json, after_json) "
        "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
        (actor, op, target, before, after),
    )


@admin_bp.route("/config", methods=["GET", "POST"])
@login_required
def config_manager():
    """View and edit categorized configuration."""
    if request.method == "POST":
        _act = request.form.get("action", "")
        if _act in ("save_persona", "reset_persona", "save_weights", "reset_weights"):
            if _act == "save_persona":
                set_persona(request.form.get("persona", ""), updated_by=_current_user())
                flash("Persona guardada.")
            elif _act == "reset_persona":
                reset_persona()
                flash("Persona reposta no original.")
            elif _act == "save_weights":
                set_reaction_weights(
                    {key[2:]: v for key, v in request.form.items() if key.startswith("w_")},
                    updated_by=_current_user(),
                )
                flash("Pesos guardados.")
            else:
                clear_setting(REACTION_WEIGHTS_KEY)
                flash("Pesos repostos nos originais.")
            return redirect(url_for("admin.config_manager"))
        for key in request.form:
            if key.startswith("config_") and key != "config_submit":
                update_config(key[7:], request.form[key])
        flash("Configurações atualizadas.")
        return redirect(url_for("admin.config_manager"))

    configs = get_configs_by_category()
    categories = get_categories()
    nav_menu = _build_nav_menu(
        "admin.config_manager",
        _current_user_data()["role"] if _current_user_data() else "user",
    )
    return render_template_string(
        CONFIG_TEMPLATE,
        persona=get_persona(),
        persona_overridden=persona_is_overridden(),
        weights=get_reaction_weights(),
        default_weights=DEFAULT_REACTION_WEIGHTS,
        configs=configs,
        categories=categories,
        user=_current_user(),
        nav_menu=nav_menu,
    )


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
@admin_required
def user_manager():
    """View and manage users."""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            email = request.form.get("email")
            password = request.form.get("password")
            role = request.form.get("role", "user")
            if create_user(email, password, role):
                flash(f"Utilizador {email} criado com sucesso.")
            else:
                flash("Erro ao criar utilizador.")
        elif action == "update":
            user_id = int(request.form.get("user_id"))
            role = request.form.get("role")
            if update_user_role(user_id, role):
                flash("Perfil atualizado com sucesso.")
            else:
                flash("Erro ao atualizar perfil.")
        elif action == "delete":
            user_id = int(request.form.get("user_id"))
            if delete_user(user_id):
                flash("Utilizador removido com sucesso.")
            else:
                flash("Erro ao remover utilizador.")
        return redirect(url_for("admin.user_manager"))

    users = get_all_users()
    nav_menu = _build_nav_menu(
        "admin.user_manager",
        _current_user_data()["role"] if _current_user_data() else "user",
    )
    return render_template_string(
        USERS_TEMPLATE, users=users, user=_current_user(), nav_menu=nav_menu
    )


@admin_bp.route("/lang/<code>")
def set_language(code: str):
    """Persist the UI language choice, then return the user where they were.

    Deliberately unauthenticated: the switch must work on the login screen
    too, and the only thing it sets is a display preference.
    """
    language = normalize_language(code)
    if code and code.lower().split("-")[0] not in LANGUAGES:
        # Unknown code must not silently resolve to PT for a real choice.
        return redirect(request.referrer or url_for("admin.brain_hub"))
    session["lang"] = language
    response = redirect(request.referrer or url_for("admin.brain_hub"))
    response.set_cookie(LANG_COOKIE, language, max_age=365 * 24 * 3600, samesite="Lax")
    return response


@admin_bp.route("/rag")
@login_required
def rag_viewer():
    """RAG view: the chunks retrieval actually returns, plus the real stats.

    Rendered from the same ``memories`` rows the API serves. No synthetic
    scores, no invented relevance ranking.
    """
    import json as _json

    from .memory_graph import build_graph_from_db

    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, timestamp, text FROM memories ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()

    chunks = []
    for row in rows:
        text = row["text"] or ""
        try:
            payload = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        chunks.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "summary": payload.get("summary") or payload.get("text") or "",
                "tags": payload.get("tags") or [],
                "facts": payload.get("facts") or [],
            }
        )

    stats = build_graph_from_db(BRAIN_DB_PATH)["stats"]
    nav_menu = _build_nav_menu("rag_viewer")
    return render_template_string(
        RAG_TEMPLATE,
        chunks=chunks,
        stats=stats,
        subnav=_build_subnav("rag_viewer"),
        nav_menu=nav_menu,
        user=_current_user(),
    )


@admin_bp.route("/brain/sleep", methods=["POST"])
@login_required
def brain_sleep():
    """Start the sleep/dream cycle in the background. Returns 202 immediately.

    What it does, precisely: classifies GMIF on edges and nodes, merges
    duplicate memories, runs the GMIF dream, and -- since T047 -- reconciles
    dangling references, but only where online evidence justifies it. Anything
    it cannot justify is deliberately left pending and reported in
    /admin/brain/sleep/status, so it can be resolved by hand.

    The previous wording here was "to resolve pending issues in the brain",
    which it structurally could not do: the four original steps only write
    `gmif_*` columns and merge memories, while `unresolved_edges` is
    recomputed at read time and could not move however often the cycle ran.
    The docstring was promising a capability the code did not have.
    """
    import sqlite3
    import threading

    # Outcome of the last run, so a failed cycle is visible instead of
    # silent. Module-level on purpose: the worker runs in a daemon thread and
    # the request has already returned, so this dict is the only place the
    # two halves can meet. Without it the endpoint answered "ok" before
    # knowing whether any step had succeeded -- which is how a cycle that
    # blocked on the LLM looked identical to one that had finished.
    state = {"status": "running", "started_at": time.time(), "steps": {}}

    def _step(name, fn, *a, **kw):
        """Run one step, recording success or failure instead of swallowing it."""
        started = time.time()
        try:
            result = fn(*a, **kw)
            state["steps"][name] = {
                "ok": True,
                "seconds": round(time.time() - started, 1),
            }
            return result
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            state["steps"][name] = {
                "ok": False,
                "seconds": round(time.time() - started, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
            raise

    def _reconcile_refs(conn):
        """T047: resolve what can be justified, leave the rest, report which.

        Only ever WRITES when it has online evidence. Without it a pending ref
        stays pending for a human, which is visible and recoverable; a guessed
        relink deletes a relationship silently, which is neither.
        """
        from src.brain.reconcile import reconcile_refs

        report = reconcile_refs(BRAIN_DB_PATH)
        logger.info(
            "reconcile_refs: pending=%s relinked=%s promoted=%s ambiguous=%s failed=%s skipped=%s",
            report.get("pending"),
            report.get("relinked"),
            report.get("promoted"),
            report.get("ambiguous"),
            report.get("failed"),
            report.get("skipped"),
        )
        state.setdefault("reports", {})["reconcile_refs"] = report
        if report.get("error"):
            raise RuntimeError(report["error"])
        return report

    def _run_sleep_cycle():
        """Background worker for sleep/dream cycle."""
        conn = sqlite3.connect(BRAIN_DB_PATH)
        try:
            from skills.skill_dream import (
                _consolidate_memories,
            )
            from skills.skill_dream import (
                perform_dreaming as _perform_dreaming,
            )
            from skills.skill_gmif_dream import _gmif_dream_cycle
            from src.pipeline.gmif_classifier import (
                classify_all_edges,
                classify_all_nodes,
            )

            _step("classify_edges", classify_all_edges, conn)
            _step("classify_nodes", classify_all_nodes, conn)
            _step("reconcile_refs", _reconcile_refs, conn)
            _step("consolidate_memories", _consolidate_memories)
            # A GMIF failure must not erase the classification work above, so
            # it is recorded and swallowed rather than aborting the cycle.
            try:
                _step("gmif_dream", _gmif_dream_cycle)
            except Exception as exc:  # noqa: BLE001
                logger.warning("GMIF dream cycle step failed: %s", exc)
            # The dreaming half of skill_dream: news and web research, stored
            # with the keyword structure the RAG matches on. Consolidation
            # above only merges what is already stored.
            try:
                _step("dream", _perform_dreaming)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Dream step failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            state["status"] = "failed"
            state["error"] = f"{type(exc).__name__}: {exc}"
            logger.error("Sleep/dream cycle failed at the top level: %s", exc)
        else:
            state["status"] = "done"
        finally:
            state["finished_at"] = time.time()
            conn.close()

    # Start background thread and return immediately
    thread = threading.Thread(target=_run_sleep_cycle, daemon=True)
    thread.start()
    _LAST_SLEEP_CYCLE["state"] = state

    return jsonify(
        {
            "status": "started",
            "message": "Sleep/dream cycle started in background",
            "note": (
                "Each step calls the LLM, so the cycle takes minutes. "
                "GET /admin/brain/sleep/status reports what actually happened."
            ),
        }
    ), 202


@admin_bp.route("/brain/sleep/status", methods=["GET"])
@login_required
def brain_sleep_status():
    """What the last sleep/dream cycle actually did.

    Added 2026-09-27 because the trigger returned "ok" the instant its
    thread started. A cycle that blocked on the LLM, or died on a missing
    symbol, was indistinguishable from one that had finished -- which is
    why Sleep & Dream read as "not working" while the request had
    succeeded and returned 200.
    """
    state = _LAST_SLEEP_CYCLE.get("state")
    if not state:
        return jsonify({"status": "never_run"})
    payload = dict(state)
    if state.get("started_at") and state.get("finished_at"):
        payload["duration_seconds"] = round(state["finished_at"] - state["started_at"], 1)
    return jsonify(payload)


def _dangling_refs() -> list[dict]:
    """Every edge endpoint that points at a node which does not exist.

    /api/graph/resolve exists and is deliberately not wired into the sleep
    cycle: relinking versus promoting is a judgement only the owner can make.
    It had no interface at all, so the count was visible on /admin/brain and
    the fix was not. These rows are what the unified panel offers to resolve.
    """
    from src.brain import reconcile

    try:
        found = reconcile.find_dangling(str(BRAIN_DB_PATH))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not list dangling refs: %s", exc)
        return []
    out = []
    for f in found:
        out.append({
            "edge_key": f.get("edge_key", ""),
            "side": f.get("side", ""),
            "label": str(f.get("label") or f.get("edge_key", ""))[:120],
        })
    return out


def _resolve_candidates() -> list[dict]:
    """Existing nodes, as targets for a relink or a promote."""
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT node_key, label FROM memory_graph WHERE node_type = 'node'"
            " ORDER BY affinity DESC LIMIT 200")]
    finally:
        conn.close()



@admin_bp.route("/brain", methods=["GET", "POST"])
@login_required
def brain_hub():
    """All four brain subsystems on one screen.

    Memory, RAG, FlyBrain and the 3D explorer were four separate pages, which
    meant four page loads and no way to see them side by side. This route
    renders all four regions together, from exactly the same real data the
    individual pages use -- no duplicated queries, no invented numbers.

    The 3D explorer is embedded rather than inlined: it is a full-viewport
    WebGL app with its own 36-test browser suite, and mounting Three.js twice on
    one document would mean two canvases fighting over the same window and a
    rewrite of tested rendering code. An iframe keeps the two concerns separate
    while still putting everything on a single screen.
    """
    selected_text_brain = ""
    _sel = request.args.get("mem_id", type=int)
    if _sel:
        _c = _knowledge_conn()
        try:
            _r = _c.execute("SELECT text FROM memories WHERE id = ?", (_sel,)).fetchone()
            selected_text_brain = _r["text"] if _r else ""
        finally:
            _c.close()

    if request.method == "POST":
        # /admin/brain is where the owner corrects what the assistant believes.
        # A form that posts to a GET-only view does nothing and looks fine.
        _op = request.form.get("op", "")
        if _op in ("delete_node", "save_memory", "delete_memory"):
            _apply_knowledge_edit(_op, request, _current_user())
        else:
            flash("Acção desconhecida.")
        return redirect(url_for("admin.brain_hub"))

    import json as _json

    from .memory_graph import build_graph_from_db

    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, timestamp, text FROM memories ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()
        state_row = conn.execute("SELECT * FROM flybrain_state WHERE id = 1").fetchone()
        topic_row = conn.execute("SELECT * FROM topic_state").fetchone()

        # Add GMIF gap analysis for the sleep button (inside try, before conn.close())
        from src.pipeline.gmif_classifier import get_gmif_stats

        gmif_stats = get_gmif_stats(conn)

    finally:
        conn.close()

    # Reuse the RAG parser verbatim so both pages agree on what a "chunk" is.
    chunks = []
    for row in rows:
        text = row["text"] or ""
        try:
            payload = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        chunks.append(
            {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "summary": payload.get("summary") or payload.get("text") or "",
                "tags": payload.get("tags") or [],
                "facts": payload.get("facts") or [],
            }
        )

    graph = build_graph_from_db(BRAIN_DB_PATH)
    stats = graph["stats"]
    # build_graph_from_db already attaches the real reinforcement state here;
    # do not query it a second time.
    flybrain = stats.get("flybrain")
    stats["gmif_weak_edges"] = sum(
        v for k, v in gmif_stats.get("level_distribution", {}).items() if k in ("M1", "M2")
    )
    stats["gmif_causal_gaps"] = max(
        0, gmif_stats.get("logical_edges", 0) - gmif_stats.get("classified_edges", 0)
    )
    stats["gmif_total_gaps"] = stats.get("gmif_weak_edges", 0) + stats.get("gmif_causal_gaps", 0)

    role = _current_user_data()["role"] if _current_user_data() else "user"
    return render_template_string(
        BRAIN_TEMPLATE,
        refs=_dangling_refs(),
        nodes=_resolve_candidates(),
        memories=_knowledge_memories(60),
        selected_text=selected_text_brain,
        chunks=chunks,
        stats=stats,
        memories_total=len(rows),
        topic=dict(topic_row) if topic_row else None,
        flybrain_state=dict(state_row) if state_row else None,
        flybrain=flybrain,
        subnav=_build_subnav("admin.brain_hub"),
        nav_menu=_build_nav_menu("admin.brain_hub", role),
        user=_current_user(),
    )


@admin_bp.route("/memory", methods=["GET", "POST"])
@login_required
def memory_viewer():
    """View pHantasma memory."""

    if request.method == "POST":
        _op = request.form.get("op", "")
        if _op in ("delete_node", "save_memory", "delete_memory"):
            _apply_knowledge_edit(_op, request, _current_user())
            return redirect(url_for("admin.memory_viewer"))

    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _memories = conn.execute(
            "SELECT * FROM memories ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        graph = conn.execute("SELECT * FROM memory_graph ORDER BY weight DESC").fetchall()
        topic = conn.execute("SELECT * FROM topic_state").fetchone()
        sel = request.args.get("mem_id", type=int)
        selected_text = ""
        if sel:
            _r = conn.execute("SELECT text FROM memories WHERE id = ?", (sel,)).fetchone()
            selected_text = _r["text"] if _r else ""
        nav_menu = _build_nav_menu(
            "admin.memory_viewer",
            _current_user_data()["role"] if _current_user_data() else "user",
        )
        return render_template_string(
            MEMORY_TEMPLATE,
            graph=[dict(g) for g in graph],
        nodes=_knowledge_nodes(),
        selected_id=sel,
        selected_text=selected_text,
            topic=dict(topic) if topic else None,
            user=_current_user(),
            subnav=_build_subnav("admin.memory_viewer"),
            nav_menu=nav_menu,
        )
    finally:
        conn.close()


@admin_bp.route("/flybrain")
@login_required
def flybrain_manager():
    """View and manage FlyBrain parameters."""
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if request.method == "POST":
            data = request.get_json()
            if data:
                conn.execute(
                    "INSERT OR REPLACE INTO flybrain_state (id, schema_version, data, updated_at) VALUES (1, 1, ?, ?)",
                    (json.dumps(data), datetime.now().isoformat()),
                )
                conn.commit()
                return jsonify({"status": "ok"})

        state = conn.execute("SELECT * FROM flybrain_state WHERE id = 1").fetchone()
        nav_menu = _build_nav_menu(
            "admin.flybrain_manager",
            _current_user_data()["role"] if _current_user_data() else "user",
        )
        return render_template_string(
            FLYBRAIN_TEMPLATE,
            state=dict(state) if state else None,
            user=_current_user(),
            subnav=_build_subnav("admin.flybrain_manager"),
            nav_menu=nav_menu,
        )
    finally:
        conn.close()


@admin_bp.route("/dashboard")
@login_required
def dashboard():
    """Permanent redirect to /admin/brain.

    The dashboard used to be a separate page holding counts of memories, graph
    nodes, FlyBrain, users and config -- all of which are state of the brain. The
    brain hub already renders every one of those subsystems side by side from the
    same tables, so the dashboard was a second page summarising the first: two
    entry points to one subject, free to diverge.

    A redirect rather than a removal: the dashboard is linked from bookmarks and
    from any older cached nav, and 404ing those is worse than landing somewhere
    correct. 301 so it is cached as permanent.
    """
    return redirect(url_for("admin.brain_hub"), code=301)


@admin_bp.route("/api/stats")
@login_required
def api_stats():
    """API endpoint for dashboard statistics."""
    # Query users and config from config.db
    conn = sqlite3.connect(CONFIG_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        configs = conn.execute("SELECT COUNT(*) as c FROM config").fetchone()["c"]
    finally:
        conn.close()

    # Query memories, graph, and flybrain from unified brain.db
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        memories = conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
        graph_nodes = conn.execute("SELECT COUNT(*) as c FROM memory_graph").fetchone()["c"]
        flybrain_row = conn.execute("SELECT data FROM flybrain_state WHERE id = 1").fetchone()
        flybrain_data = flybrain_row["data"] if flybrain_row else None
    finally:
        conn.close()

    stats = {
        "users": users,
        "configs": configs,
        "memories": memories,
        "graph_nodes": graph_nodes,
        "flybrain": flybrain_data,
    }
    return jsonify(stats)


@admin_bp.route("/api/memory")
@login_required
def api_memory():
    """API endpoint for memory data."""
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        memories = conn.execute(
            "SELECT * FROM memories ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        graph = conn.execute("SELECT * FROM memory_graph ORDER BY weight DESC").fetchall()
        topic = conn.execute("SELECT * FROM topic_state").fetchone()
        return jsonify(
            {
                "memories": [dict(m) for m in memories],
                "graph": [dict(g) for g in graph],
                "topic": dict(topic) if topic else None,
            }
        )
    finally:
        conn.close()


@admin_bp.route("/api/flybrain")
@login_required
def api_flybrain():
    """API endpoint for FlyBrain data."""
    conn = sqlite3.connect(BRAIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        state = conn.execute("SELECT * FROM flybrain_state WHERE id = 1").fetchone()
        if state:
            state_dict = dict(state)
            if state_dict["data"]:
                try:
                    state_dict["data"] = json.loads(state_dict["data"])
                except json.JSONDecodeError:
                    state_dict["data"] = state_dict["data"]
        return jsonify({"state": state_dict if state else None})
    finally:
        conn.close()


@admin_bp.route("/api/email", methods=["POST"])
@login_required
def api_send_email():
    """API endpoint to send email."""
    data = request.get_json()
    if not data or "to" not in data or "subject" not in data or "body" not in data:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        _send_mail(
            to=data["to"],
            subject=data["subject"],
            body=data["body"],
            otp=data.get("otp"),
        )
        return jsonify({"status": "Email sent successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
