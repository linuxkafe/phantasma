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

import secrets
import time
from functools import wraps
from pathlib import Path
from typing import Optional

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# ----------------------------------------------------------------------
# In‑memory OTP store (replace with Redis / DB in production)
# ----------------------------------------------------------------------
_OTP_STORE: dict[str, tuple[str, float]] = {}  # email -> (otp, expiry_ts)

# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def _send_mail(to: str, subject: str, body: str) -> None:
    """
    Mock e‑mail sender.  Replace with a real SMTP client (aiosmtplib,
    smtplib, etc.) in production.
    """
    print(f"[MOCK MAIL] To: {to}\nSubject: {subject}\n{body}\n---")


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


def login_required(view):
    """Decorator that protects admin routes."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _current_user():
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


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
LOGIN_TEMPLATE = """
<!doctype html>
<title>Admin Login</title>
<h1>Admin Login</h1>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    <ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>
  {% endif %}
{% endwith %}
<form method="post">
  <label>E‑mail: <input type="email" name="email" required></label><br>
  <button type="submit">Enviar código</button>
</form>
"""

OTP_TEMPLATE = """
<!doctype html>
<title>Código de acesso</title>
<h1>Código enviado</h1>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    <ul>{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>
  {% endif %}
{% endwith %}
<form method="post">
  <label>
    Código (6 dígitos):
    <input type="text" name="otp" maxlength="6" required>
  </label><br>
  <button type="submit">Entrar</button>
</form>
"""

ADMIN_TEMPLATE = """
<!doctype html>
<title>Admin – Configuração</title>
<h1>Configuração (.env)</h1>
<p>
    Logado como <strong>{{ user }}</strong> —
    <a href="{{ url_for('admin.logout') }}">Sair</a>
  </p>
<form method="post">
  <textarea name="env" rows="30" cols="100">{{ env }}</textarea><br>
  <button type="submit">Guardar</button>
</form>
"""


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    """Step 1 – ask for e‑mail, send OTP."""
    if request.method == "GET":
        return render_template_string(LOGIN_TEMPLATE)

    email = request.form.get("email", "").strip().lower()
    if not email:
        flash("E‑mail obrigatório")
        return redirect(url_for("admin.login"))

    otp = _generate_otp()
    _store_otp(email, otp)
    _send_mail(
        to=email,
        subject="pHantasma – Código de acesso administrativo",
        body=f"O seu código de acesso é: {otp}\nExpira em 5 minutos.",
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
        return render_template_string(OTP_TEMPLATE)

    otp = request.form.get("otp", "").strip()
    if _verify_otp(email, otp):
        _login_user(email)
        session.pop("_otp_email", None)
        flash("Login bem‑sucedido.")
        nxt = request.args.get("next") or url_for("admin.index")
        return redirect(nxt)
    else:
        flash("Código inválido ou expirado.")
        return redirect(url_for("admin.verify_otp"))


@admin_bp.route("/logout")
def logout():
    _logout_user()
    flash("Sessão terminada.")
    return redirect(url_for("admin.login"))


@admin_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """View / edit .env."""
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
        return redirect(url_for("admin.index"))

    env_data = _read_env()
    env_text = "\n".join(f"{k}={v}" for k, v in sorted(env_data.items()))
    return render_template_string(ADMIN_TEMPLATE, user=_current_user(), env=env_text)
