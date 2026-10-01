import json
import logging
import os
import time
from pathlib import Path

from flask import jsonify, make_response, redirect, request, session, url_for

import config

logger = logging.getLogger(__name__)

TRIGGER_TYPE = "none"
TRIGGERS = []
WEATHER_CACHE_FILE = str(Path(config.CACHE_DIR) / "weather_cache.json")

# The chat page is a *skill*: it is loaded by the dynamic skill loader and must
# keep working even if the admin package is not importable. So the shared design
# system is loaded by absolute path with a self-contained fallback, rather than a
# hard `from src.api.design import ...` that would raise at skill-load time.
_DESIGN_CSS_FALLBACK = """
:root{--bg-color:#0a0a0a;--surface:#171717;--surface-2:#262626;--border:#262626;
 --border-strong:#404040;--text:#fafafa;--text-secondary:#d4d4d4;--muted:#a3a3a3;
 --brand-500:#009FDF;--brand-400:#38bdf8;--accent:#22c55e;--destructive:#ef4444;
 --radius:8px;--radius-sm:6px;--radius-lg:12px;--sp-1:4px;--sp-2:8px;--sp-3:12px;
 --sp-4:16px;--sp-6:24px;--fs-small:13px;--fs-tiny:11px;--fs-body:15px;
 --fw-h2:600;--fw-display:700;}
@media (max-width:900px){.nav-menu{display:none;}.nav-menu.open{display:flex;}}
"""

# Nav fallback: the same contract as design.js() -- discover every .nav-toggle,
# resolve its panel via aria-controls, then drive the "open" class and
# aria-expanded. Replaced 2026-09-27: this used to hardcode #topbar, so the
# device UI could never share toggle behaviour with the admin pages.
_DESIGN_JS_FALLBACK = """
(function(){document.querySelectorAll('.nav-toggle').forEach(function(t){
var m=document.getElementById(t.getAttribute('aria-controls'))||document.querySelector('.nav-menu');
if(!m||t.dataset.wired==='1')return;t.dataset.wired='1';
function s(o){m.classList.toggle('open',o);t.setAttribute('aria-expanded',o?'true':'false');
t.setAttribute('aria-label',o?'Fechar menu':'Abrir menu');}
s(false);t.addEventListener('click',function(){s(!m.classList.contains('open'));});
document.addEventListener('keydown',function(e){if(e.key==='Escape')s(false);});});})();
"""


def login_page():
    """Password login for the voice UI.

    Rate limited BEFORE any credential work, for the same reason the admin
    login is: an unthrottled password endpoint is a free oracle and a free
    spam vector. The failure message is identical for an unknown address and a
    wrong password, so the form cannot be used to enumerate the user store.
    """
    from src.api import ratelimit, ui_auth

    error = None
    if request.method == "POST":
        if not ratelimit.login_limiter.allow(ratelimit.client_key()):
            retry = ratelimit.login_limiter.retry_after(ratelimit.client_key())
            return (
                render_login_page(
                    error=f"Demasiadas tentativas. Tenta de novo em {retry}s."
                ),
                429,
            )
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        # `next` from the query string, validated as a local path by the module:
        # an absolute URL on a login form is an open redirect, i.e. phishing.
        ui_auth.remember_next(request.args.get("next"))
        if not ui_auth.authenticate(email, password):
            logger.warning("ui login failed from %s", request.remote_addr)
            return render_login_page(error="Email ou password inválidos.")
        # authenticate() answers a boolean on purpose: the same branch for an
        # unknown address and a wrong password. The identity is resolved
        # separately, AFTER the password passed.
        user = ui_auth.find_user(email)
        if user is None:
            return render_login_page(error="Email ou password inválidos.")

        # Second factor for a device this account has not trusted before. A
        # password is a bearer secret and it gets typed into phones and
        # laptops; once one is compromised every login from it is. This only
        # costs a mail for a device the owner has not used before, and the
        # device then stops costing anything.
        from src.api import auth_store

        conn = _auth_store()[1]
        if auth_store.is_device_trusted(
            conn, user["email"], request.cookies.get(auth_store.DEVICE_COOKIE, "")
        ):
            ui_auth.login(email, password)
            return redirect(ui_auth.take_next())

        device_code = auth_store.request_code(
            conn, user["email"], "new_device", request.remote_addr
        )
        if device_code:
            _send_ui_mail(
                user["email"],
                "pHantasma — novo dispositivo",
                "Entrou-se na tua conta a partir de um dispositivo novo.\n\n"
                f"Código: {device_code}\n\n"
                f"Válido durante {auth_store.CODE_TTL_SECONDS // 60} minutos.\n"
                "Se não foste tu, ignora este email e muda a password.",
                otp=device_code,
            )
        else:
            # The cooldown suppressed a second code -- one went out moments ago
            # and is still perfectly valid. Saying nothing here and redirecting
            # to the code page anyway is what made a working account look
            # broken: the owner was told to enter a code that had just been
            # reissued, while the code she actually had had been superseded.
            # The page now says "we already sent one", which is true, and the
            # pending claim is still parked so the code she has can still be
            # redeemed.
            logger.info(
                "ui auth: new_device code suppressed for %s (cooldown); "
                "a previously issued code remains valid",
                user["email"],
            )
        ui_auth.start_pending_login(user["email"])
        return redirect(url_for("ui.verify_new_device"))

        ui_auth.login(email, password)
        return redirect(ui_auth.take_next())

    return render_login_page(error=error)


def logout_page():
    from src.api import ui_auth

    ui_auth.logout()
    return redirect(url_for(ui_auth.UI_LOGIN_ROUTE))


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<!-- This template, not the app page, is what the installed app shows on the
     home screen: start_url is "/", and "/" redirects here for anyone signed
     out. Without the manifest link the launcher gets no icon and no theme
     colour, and the standalone window has black bars over the notch. -->
<link rel="manifest" href="/public/manifest.json">
<meta name="theme-color" content="#0a0a0a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Phantasma">
<link rel="apple-touch-icon" href="/public/icons/icon-192.png">
<title>pHantasma</title>
<style>
    /* The login page shares the app's palette on purpose: a login that looks
       like a different product is how phishing gets believed. */
    :root { --bg: #0a0a0a; --surface: #171717; --border: #262626;
            --text: #fafafa; --muted: #737373; --accent: #22c55e; }
    * { box-sizing: border-box; }
    body {
        margin: 0; min-height: 100dvh; display: flex; align-items: center;
        justify-content: center; padding: 1.5rem;
        background: var(--bg); color: var(--text);
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    form {
        width: 100%; max-width: 22rem; background: var(--surface);
        border: 1px solid var(--border); border-radius: 12px; padding: 2rem 1.5rem;
    }
    h1 { margin: 0 0 .25rem; font-size: 1.5rem; }
    p.sub { margin: 0 0 1.5rem; color: var(--muted); font-size: .85rem; }
    label { display: block; font-size: .75rem; color: var(--muted);
            margin: 0 0 .35rem; text-transform: uppercase; letter-spacing: .04em; }
    input[type=email], input[type=password] {
        width: 100%; padding: .7rem; margin-bottom: 1rem;
        background: var(--bg); color: var(--text);
        border: 1px solid var(--border); border-radius: 8px; font-size: 1rem;
    }
    /* 44px is the touch-target floor; below it the field is hard to hit on a
       phone, and a login you cannot tap is a login that fails. */
    input[type=email], input[type=password], button { min-height: 44px; }
    input:focus-visible, button:focus-visible {
        outline: 2px solid var(--accent); outline-offset: 2px;
    }
    button {
        width: 100%; padding: .7rem; border: none; border-radius: 8px;
        background: var(--accent); color: #05240f; font-size: 1rem;
        font-weight: 600; cursor: pointer;
    }
    .error {
        background: #2a1416; border: 1px solid #7f1d1d; color: #fca5a5;
        padding: .6rem .75rem; border-radius: 8px; font-size: .85rem;
        margin-bottom: 1rem;
    }


                </style>
</head>
<body>
  <form method="post" autocomplete="on">
    <h1>👻 pHantasma</h1>
    <p class="sub">Entrar para controlar a casa.</p>
    __ERROR__
    <label for="email">Email</label>
    <input id="email" name="email" type="email" autocomplete="username"
           required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password"
           autocomplete="current-password" required>
    <button type="submit">Entrar</button>
    <!-- Inside the form's box. It used to sit after the closing form tag, so it
         inherited neither the card's width nor its padding and rendered flush
         against the viewport, left of the panel it belongs to.

         The comment deliberately spells out "the closing form tag" rather than
         writing the tag: a literal closing tag inside an HTML comment still
         reads as a tag to any naive string search, and one did exactly that
         when verifying this fix. -->
    <p class="sub" style="margin:1.25rem 0 0;text-align:center">
      <a href="/recuperar" style="color:var(--muted)">Esqueci-me a password</a>
    </p>
  </form>
<script>
/* Registered on the login page too, so the worker is already installed by the
   time someone signs in. Registering it afterwards would mean the first launch
   of the installed app has no worker at all. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .catch(function (e) { console.warn('sw: não registado', e); });
  });
}
</script>
</body>
</html>
"""


def render_login_page(error=None):
    block = ""
    if error:
        block = f'<p class="error" role="alert">{error}</p>'
    return make_response(LOGIN_PAGE.replace("__ERROR__", block))


def _send_ui_mail(to, subject, body, otp=None):
    """Send through the admin mailer, which knows the SMTP settings.

    Delegating rather than re-implementing: a second SMTP path is a second set
    of credentials to get wrong, and the admin one already falls back to a
    console print so development still works.
    """
    try:
        from src.api import admin as admin_mod

        admin_mod._send_mail(to, subject, body, otp)
        return True
    except Exception:
        logger.exception("ui auth: could not send mail")
        return False


def _auth_store():
    from src.api import admin as admin_mod
    from src.api import auth_store

    return auth_store, admin_mod.get_db_connection()


def forgot_password():
    """Step 1: ask for the code.

    The response is identical whether or not the address is in the store. This
    form is reachable by anyone, so a different answer for "no such user" is a
    free oracle for the user list -- and a password-recovery flow is exactly
    where someone goes looking for one.
    """
    if request.method == "GET":
        return render_recover_page()

    from src.api import ratelimit

    if not ratelimit.login_limiter.allow(ratelimit.client_key()):
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código."
        )
    email = (request.form.get("email") or "").strip()
    auth_store, conn = _auth_store()
    code = auth_store.request_code(conn, email, "recover", request.remote_addr)
    if code:
        _send_ui_mail(
            email,
            "pHantasma — recuperação de password",
            "Recebeste um pedido para redefinir a tua password.\n\n"
            f"Código: {code}\n\n"
            f"Válido durante {auth_store.CODE_TTL_SECONDS // 60} minutos.\n"
            "Se não foste tu, ignora este email: nada muda.",
            otp=code,
        )
    # Same page either way, including for an unknown address.
    return _render_recover(
        neutral="Se o endereço existir, enviámos um código.",
        email=email,
    )


def reset_password():
    """Step 2: code + new password.

    Consumes the code first, then applies the policy. A code that passes and a
    password that fails must not leave the code alive to be retried: it was
    already spent, and keeping it alive would let a second guess at the policy
    reuse a captured code.
    """
    if request.method == "GET":
        return redirect(url_for("ui.forgot"))

    from src.api import ratelimit

    if not ratelimit.login_limiter.allow(ratelimit.client_key()):
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código.",
            error="Demasiadas tentativas. Tenta mais tarde.",
        )
    email = (request.form.get("email") or "").strip()
    code = (request.form.get("code") or "").strip()
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm") or ""
    auth_store, conn = _auth_store()

    if not auth_store.consume_code(conn, email, code, "recover"):
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código.",
            email=email,
            error="O código não é válido ou expirou. Pede outro.",
        )
    if password != confirm:
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código.",
            email=email,
            error="As passwords não coincidem.",
        )
    problem = auth_store.password_problem(password)
    if problem:
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código.",
            email=email,
            error=problem,
        )
    if not auth_store.set_password(conn, email, password):
        return _render_recover(
            neutral="Se o endereço existir, enviámos um código.",
            email=email,
            error="Não foi possível alterar a password.",
        )
    # A password change that leaves the old devices trusted is a change the
    # thief rides straight through.
    auth_store.revoke_devices(conn, email)
    logger.info("ui auth: password reset completed for %s", email)
    return render_recover_page(
        done="Password alterada. Entra com a nova password."
    )


def verify_new_device():
    """Second factor for a device this account has not trusted before.

    The password was already verified by the login step; what is missing is the
    second factor. So the login step parks a SHORT-LIVED, already-verified
    pending claim in the session -- never the password, and never a full
    session -- and this endpoint turns that claim into a real session once the
    code is redeemed.

    Parking the password would be the obvious shortcut and it is wrong: the
    password would sit in the cookie (signed, not encrypted) for the whole
    window. The pending claim carries only the address and an expiry, and it is
    destroyed on use.
    """
    if request.method == "GET":
        return render_verify_page()

    from src.api import ratelimit, ui_auth

    pending = session.get(ui_auth.PENDING_KEY)
    if not pending or time.time() > pending.get("expires", 0):
        # No live claim: the browser did not come through the login step, or
        # the claim expired. Either way, start over rather than ask for a code
        # that would be redeemed for an identity this browser never proved.
        return render_verify_page(
            error="A sessão expirou. Entra novamente.", restart=True
        )
    if not ratelimit.login_limiter.allow(ratelimit.client_key()):
        return render_verify_page(error="Demasiadas tentativas. Tenta mais tarde.")

    email = pending["email"]
    code = (request.form.get("code") or "").strip()
    auth_store, conn = _auth_store()
    if not auth_store.consume_code(conn, email, code, "new_device"):
        # WHY it failed, in words that tell her what to do. Every branch used to
        # return the same sentence, "O código não é válido ou expirou", for a
        # wrong digit, a superseded code, a dead code and a locked-out one. An
        # account whose codes were being superseded read as permanently broken:
        # the real production log for elsavamp@gmail.com is four codes issued in
        # six minutes, every one superseded, never one verified.
        state = auth_store.live_code_state(conn, email, "new_device")
        if state["state"] == "none":
            hint = (
                "O código expirou. Entra outra vez para receber um novo — "
                f"cada código vale {auth_store.CODE_TTL_SECONDS // 60} minutos."
            )
        elif state["replaced"]:
            hint = (
                "Esse código foi substituído por um mais recente. Usa o código "
                "do email mais recente (o que chegou depois)."
            )
        elif state["attempts_left"] <= 2:
            hint = (
                f"Faltam {state['attempts_left']} tentativa(s) antes de o "
                f"código deixar de valer. Confere os algarismos."
            )
        else:
            hint = (
                f"O código não confere. Tens {state['attempts_left']} "
                f"tentativa(s) e {state['seconds_left'] // 60} minuto(s) de "
                f"validade."
            )
        return render_verify_page(error=hint, email=email)

    ui_auth.complete_pending_login(email)
    raw = auth_store.trust_device(conn, email, request.form.get("label") or None)
    response = make_response(redirect("/"))
    response.set_cookie(
        auth_store.DEVICE_COOKIE,
        raw,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        max_age=60 * 60 * 24 * auth_store.TOKEN_SKEW_DAYS,
    )
    return response


_AUTH_CSS = """
    :root { --bg:#0a0a0a; --surface:#171717; --border:#262626; --text:#fafafa;
            --muted:#737373; --accent:#22c55e; --danger:#ef4444; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100dvh; display:flex; align-items:center;
           justify-content:center; padding:1.5rem; background:var(--bg);
           color:var(--text); font-family:Inter,-apple-system,BlinkMacSystemFont,
           "Segoe UI",Roboto,sans-serif; }
    .card { width:100%; max-width:24rem; background:var(--surface);
            border:1px solid var(--border); border-radius:12px; padding:2rem 1.5rem; }
    h1 { margin:0 0 .25rem; font-size:1.35rem; }
    p.sub { margin:0 0 1.5rem; color:var(--muted); font-size:.85rem; }
    label { display:block; font-size:.72rem; color:var(--muted); margin:0 0 .35rem;
            text-transform:uppercase; letter-spacing:.04em; }
    input { width:100%; padding:.7rem; margin-bottom:1rem; background:var(--bg);
            color:var(--text); border:1px solid var(--border); border-radius:8px;
            font-size:1rem; min-height:44px; }
    input:focus-visible, button:focus-visible { outline:2px solid var(--accent);
        outline-offset:2px; }
    button { width:100%; padding:.7rem; border:none; border-radius:8px;
             background:var(--accent); color:#05240f; font-size:1rem;
             font-weight:600; cursor:pointer; min-height:44px; }
    button.secondary { background:transparent; color:var(--muted);
                       border:1px solid var(--border); margin-top:.5rem; }
    .msg { padding:.6rem .75rem; border-radius:8px; font-size:.85rem;
           margin-bottom:1rem; }
    .msg.err { background:#2a1416; border:1px solid #7f1d1d; color:#fca5a5; }
    .msg.ok { background:#0f2417; border:1px solid #166534; color:#86efac; }
    .msg.neutral { background:#151515; border:1px solid var(--border);
                   color:var(--muted); }
    code, .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
                  font-size:.85em; word-break:break-all; }
    ul.plain { list-style:none; padding:0; margin:0 0 1rem; }
    ul.plain li { padding:.5rem 0; border-bottom:1px solid var(--border);
                 font-size:.85rem; display:flex; justify-content:space-between;
                 gap:.5rem; align-items:center; }
    .muted { color:var(--muted); font-size:.78rem; }
"""


def _auth_page(title, body_html, sub=""):
    sub_html = f'<p class="sub">{sub}</p>' if sub else ""
    return make_response(
        f"""<!DOCTYPE html>
<html lang="pt"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<!-- The installed app OPENS here: start_url is "/", and "/" redirects to
     /login for anyone not signed in. So the page that decides what a PWA looks
     like on the home screen is this one, and without the manifest link the
     app launches to a bare form with no icon, no theme colour and no offline
     shell. Same tags as the main page, kept in step deliberately. -->
<link rel="manifest" href="/public/manifest.json">
<meta name="theme-color" content="#0a0a0a">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Phantasma">
<link rel="apple-touch-icon" href="/public/icons/icon-192.png">
<title>pHantasma</title><style>{_AUTH_CSS}</style></head>
<body><div class="card"><h1>{title}</h1>{sub_html}{body_html}</div>
<script>
if ('serviceWorker' in navigator) {{
  window.addEventListener('load', function () {{
    navigator.serviceWorker.register('/sw.js', {{ scope: '/' }})
        .catch(function (e) {{ console.warn('sw: não registado', e); }});
  }});
}}
</script>
</body></html>"""
    )


def _render_recover(neutral=None, error=None, email=""):
    """The recovery form. `neutral` is the answer for every outcome."""
    block = ""
    if error:
        block += f'<p class="msg err" role="alert">{error}</p>'
    if neutral:
        block += f'<p class="msg neutral" role="status">{neutral}</p>'
    return _auth_page(
        "Recuperar password",
        f"""{block}
<form method="post" action="{url_for('ui.reset')}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="username"
         value="{email}" required>
  <label for="code">Código recebido</label>
  <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" required>
  <label for="password">Nova password</label>
  <input id="password" name="password" type="password"
         autocomplete="new-password" required>
  <label for="confirm">Repetir a password</label>
  <input id="confirm" name="confirm" type="password"
         autocomplete="new-password" required>
  <button type="submit">Definir nova password</button>
</form>
<form method="post" action="{url_for('ui.forgot')}">
  <button class="secondary" type="submit">Pedir outro código</button>
</form>
<a class="muted" href="{url_for('ui_login')}">Voltar ao início de sessão</a>""",
        sub="Enviamos um código por email se o endereço existir.",
    )


def render_recover_page(error=None, done=None, email=""):
    block = ""
    if error:
        block += f'<p class="msg err" role="alert">{error}</p>'
    if done:
        block += f'<p class="msg ok" role="status">{done}</p>'
    return _auth_page(
        "Recuperar password",
        f"""{block}
<form method="post" action="{url_for('ui.forgot')}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="username" required>
  <button type="submit">Enviar código</button>
</form>
<a class="muted" href="{url_for('ui_login')}">Voltar ao início de sessão</a>""",
        sub="Enviamos um código por email se o endereço existir.",
    )


def render_verify_page(error=None, email="", restart=False):
    """Second factor for a new device."""
    if restart:
        return _auth_page(
            "Sessão expirada",
            f"""<p class="msg err" role="alert">{error or "A sessão expirou."}</p>
<form method="post" action="{url_for('ui_login')}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password"
         autocomplete="current-password" required>
  <button type="submit">Entrar</button>
</form>""",
        )
    block = f'<p class="msg err" role="alert">{error}</p>' if error else ""
    return _auth_page(
        "Novo dispositivo",
        f"""{block}
<p class="sub">Enviámos um código para o email desta conta.</p>
<form method="post" action="{url_for('ui.verify_new_device')}">
  <label for="code">Código</label>
  <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" required>
  <label for="label">Nome do dispositivo (opcional)</label>
  <input id="label" name="label" placeholder="ex.: telemóvel da cozinha">
  <button type="submit">Confirmar</button>
</form>""",
    )


def profile_page():
    """The user's own page: tokens, trusted devices, and Discord identity.

    A token is shown exactly once, at creation, and cannot be recovered
    afterwards -- a list that re-displays secrets is a password list with a
    nicer header. The profile therefore shows prefixes and timestamps, and the
    secret lives in the response to the creation POST alone.
    """
    from src.api import ui_auth

    user = ui_auth.current_user()
    if user is None:
        return redirect(url_for(ui_auth.UI_LOGIN_ROUTE))
    email = user["email"]
    auth_store, conn = _auth_store()

    minted = None
    discord_note = None
    discord_note_ok = False
    if request.method == "POST":
        op = request.form.get("op")
        if op == "create_token":
            name = (request.form.get("name") or "").strip() or "token"
            secret, row = auth_store.create_token(conn, email, name)
            minted = (secret, row)
        elif op == "revoke_token":
            auth_store.revoke_token(conn, email, int(request.form.get("id") or 0))
        elif op == "revoke_device":
            auth_store.revoke_devices(conn, email)
        elif op in ("set_discord_id", "clear_discord_id"):
            # An authorisation change, so the outcome is always said out loud.
            # A form that saves silently is a form whose failure the user only
            # discovers from Discord saying "Acesso negado." an hour later.
            raw = "" if op == "clear_discord_id" else request.form.get("discord_id")
            ok, problem = auth_store.set_discord_id(email, raw, conn)
            discord_note = problem or (
                "ID do Discord removido." if op == "clear_discord_id"
                else "ID do Discord guardado."
            )
            discord_note_ok = ok

    discord_id = auth_store.get_discord_id(email, conn) or ""
    tokens = auth_store.list_tokens(conn, email)
    devices = auth_store.list_devices(conn, email)

    def _when(ts):
        if not ts:
            return "—"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

    token_rows = "".join(
        f"<li><span><strong>{t['name']}</strong> "
        f"<span class='mono muted'>{t['prefix']}…</span><br>"
        f"<span class='muted'>criado {_when(t['created_at'])}"
        + (f" · usado {_when(t['last_used_at'])}" if t["last_used_at"] else " · nunca usado")
        + (" · <strong>revogado</strong>" if t["revoked_at"] else "")
        + "</span></span>"
        + ("" if t["revoked_at"] else
           f"<form method='post' style='margin:0'>"
           f"<input type='hidden' name='op' value='revoke_token'>"
           f"<input type='hidden' name='id' value='{t['id']}'>"
           f"<button class='secondary' type='submit' "
           f"style='padding:.3rem .6rem;min-height:0'>Revogar</button></form>")
        + "</li>"
        for t in tokens
    ) or "<li class='muted'>Sem tokens.</li>"

    device_rows = "".join(
        f"<li><span>{d.get('label') or 'dispositivo'}<br>"
        f"<span class='muted'>visto {_when(d['last_seen_at'])}"
        + (" · <strong>revogado</strong>" if d["revoked_at"] else "")
        + "</span></span></li>"
        for d in devices
    ) or "<li class='muted'>Sem dispositivos.</li>"

    minted_block = ""
    if minted:
        secret, row = minted
        # A token nobody knows how to use is a token nobody uses, so the exact
        # call is shown with the real secret in it. The command is derived from
        # the request rather than hardcoded, so it names the host the owner is
        # actually on instead of a localhost that is only true for the server.
        host = request.host_url.rstrip("/")
        example = (
            f"curl -X POST {host}/comando \\\n"
            f"  -H 'Authorization: Bearer {secret}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"prompt\": \"liga a luz do balcão\"}}'"
        )
        minted_block = f"""
<p class="msg ok" role="alert"><strong>Token criado — copia-o agora,
não voltarás a vê-lo.</strong><br><span class="mono">{secret}</span></p>
<div style="background:#0d0d0d;border:1px solid var(--border);border-radius:8px;
            padding:.75rem;margin-bottom:1rem">
  <p class="muted" style="margin:0 0 .4rem">
    Exemplo — envia um comando com este token:</p>
  <pre class="mono" style="margin:0;white-space:pre-wrap">{example}</pre>
</div>"""

    discord_note_block = ""
    if discord_note:
        # Said either way, success or refusal. `role=status` so a screen reader
        # announces it: the note appears after a POST, with no navigation and no
        # focus move, which is exactly the case assistive tech is worst at.
        colour = "#4ade80" if discord_note_ok else "#f87171"
        discord_note_block = (
            f"<p role='status' style='color:{colour};margin:.5rem 0 0'>{discord_note}</p>"
        )
    discord_clear_block = (
        "<form method=\"post\" style=\"margin-top:.5rem\">"
        "<input type=\"hidden\" name=\"op\" value=\"clear_discord_id\">"
        "<button class=\"secondary\" type=\"submit\">Desligar o Discord</button>"
        "</form>" if discord_id else ""
    )
    return _auth_page(
        "O meu perfil",
        f"""{minted_block}
<p class="sub">{email} · {user.get('role', 'user')}</p>
<h2 style="font-size:1rem;margin:1.5rem 0 .5rem">Tokens de automação</h2>
<p class="muted">Um token permite a outras aplicações enviar comandos.
Não dá acesso a esta página, a memórias nem à administração.</p>
<ul class="plain">{token_rows}</ul>
<form method="post">
  <input type="hidden" name="op" value="create_token">
  <label for="name">Nome do token</label>
  <input id="name" name="name" placeholder="ex.: Home Assistant">
  <button type="submit">Criar token</button>
</form>
<h2 style="font-size:1rem;margin:1.5rem 0 .5rem">Discord</h2>
<p class="muted">Escreve aqui o teu ID numérico de Discord (o do perfil, em
Definições de utilizador &rarr; Copiar ID, com o Discord Developer Mode ligado)
para poderes mandar comandos pelo bot. O que este ID autoriza é o teu papel
aqui: administrador dá acesso total, utilizador dá o acesso normal com a
mesma quota de sempre. Um ID só pode estar associado a uma conta.</p>
{discord_note_block}
<form method="post">
  <input type="hidden" name="op" value="set_discord_id">
  <label for="discord_id">ID do Discord</label>
  <input id="discord_id" name="discord_id" inputmode="numeric" pattern="[0-9]*"
         autocomplete="off" spellcheck="false" placeholder="ex.: 123456789012345678"
         value="{discord_id}">
  <button type="submit">Guardar</button>
</form>
{discord_clear_block}
<h2 style="font-size:1rem;margin:1.5rem 0 .5rem">Dispositivos</h2>
<ul class="plain">{device_rows}</ul>
<form method="post">
  <input type="hidden" name="op" value="revoke_device">
  <button class="secondary" type="submit">Revogar todos os dispositivos</button>
</form>
<form method="post" action="{url_for('ui_logout')}">
  <button class="secondary" type="submit">Sair</button>
</form>
<a class="muted" href="/">Voltar ao painel</a>""",
    )


def _shared_design():
    """Return ``(css, js)`` from the admin design system, or the fallback.

    Importing by absolute file path keeps this skill independent of the caller's
    ``sys.path`` and of package layout.
    """
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_phantasma_design",
            str(Path(__file__).resolve().parent.parent / "src" / "api" / "design.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.design_css(), module.design_js()
    except Exception:
        # A skill must never take the whole UI down over cosmetics.
        return _DESIGN_CSS_FALLBACK, _DESIGN_JS_FALLBACK


def register_routes(app):


    # The chat page is ONE html document carrying both the css and the js for
    # every reaction button. With no cache headers the browser is free to reuse
    # the document it already has, and a stale copy reproduces exactly the two
    # symptoms of a broken feature: the buttons render with the default UA
    # background (the white block) because the css that made them transparent
    # is not in that copy, and the click does nothing because the delegated
    # listener is not in that copy either. Pinning the document keeps the css,
    # the js and the markup in the same version.
    def _ui_page():
        # `/` is the house-control surface: the device tiles, the chat, and the
        # buttons that switch the lights. It was open to anything that could
        # reach port 5000, which made the admin login decorative -- the same
        # box served the house to anyone who asked for it.
        #
        # The gate is a session, and the loopback bypass is deliberately NOT
        # honoured here: that bypass grants ADMIN to any local process, and
        # extending an admin bypass to the light switches would let anything
        # running on this host, and anything that could be induced to make a
        # request (an SSRF in a dependency), turn the lights on. A program
        # integrates with the command token instead, which carries exactly one
        # capability and cannot read this page.
        from src.api import ui_auth

        if not ui_auth.is_authenticated():
            try:
                return redirect(url_for(ui_auth.UI_LOGIN_ROUTE, next=request.path))
            except Exception:
                # A missing route must not turn "please log in" into a 500.
                return ("401 Unauthorized", 401)
        resp = make_response(handle_request())
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    app.add_url_rule('/', 'ui', _ui_page)
    app.add_url_rule('/api/voz', 'ui.voice', voice_endpoint, methods=["POST"])
    app.add_url_rule('/api/weather', 'weather_api', handle_weather_api)
    app.add_url_rule('/login', 'ui_login', login_page, methods=["GET", "POST"])
    app.add_url_rule('/logout', 'ui_logout', logout_page, methods=["GET", "POST"])
    app.add_url_rule('/recuperar', 'ui.forgot', forgot_password, methods=["GET", "POST"])
    app.add_url_rule('/recuperar/codigo', 'ui.reset', reset_password, methods=["GET", "POST"])
    app.add_url_rule('/verificar-dispositivo', 'ui.verify_new_device',
                     verify_new_device, methods=["GET", "POST"])
    app.add_url_rule('/perfil', 'ui.profile', profile_page, methods=["GET", "POST"])

def handle_weather_api():
    if not os.path.exists(WEATHER_CACHE_FILE): return jsonify({"error": "No cache data"})
    try:
        with open(WEATHER_CACHE_FILE, 'r') as f: return jsonify(json.load(f))
    except Exception as e: return jsonify({"error": str(e)})

def _viewer_is_admin() -> bool:
    """Is the current requester an admin?

    Read from EITHER session key, not just the UI one. The admin area and the
    voice UI each wrote their own key, and a browser that signed in before the
    two were unified still carries only the old one: reading `ui_user` alone
    left that session rendering a non-admin menu on a page the owner could
    plainly administer, and following an admin link from there asked for the
    password again -- the exact symptom that prompted the unification.

    The role is resolved from the store on every request in both cases, so
    neither key is trusted for what it says: a cookie claiming admin for a
    non-admin resolves to no admin, because the store says so.
    """
    try:
        from src.api import ui_auth

        if ui_auth.current_user() is not None:
            return ui_auth.is_admin()
        # A session from before the unification, or an admin-OTP login that
        # never touched the voice UI.
        return bool(ui_auth.admin_session_email())
    except Exception:
        # Never let an auth probe break the device page.
        return False


# Controls this page contributes to the shared navigation bar, rather than
# wrapping the bar in its own markup.
#
# They live INSIDE .nav-bar but OUTSIDE .nav-menu, because below 900px the menu
# becomes a full-screen overlay: anything placed inside it is hidden until the
# burger is opened, and the burger is the thing you need to reach the voice
# button in the first place. The mic belongs where a thumb can find it on the
# home screen, not two taps deep.
#
# The voice button is one control, not two. The compact-view toggle that used to
# The voice button used to be injected into the navigation bar here. It is now
# in the dock, at the bottom of the phone, for two measured reasons: the bar is
# a menu that closes, and the chat pill that sits bottom-right was painted over
# the microphone -- `elementFromPoint` at the mic's centre returned the pill, so
# the mic could not be tapped at all.
#
# Two microphones would also be a duplicated element id, which is invalid HTML
# and made `getElementById` return whichever came first.
#
# The compact-view toggle that also used to sit here was removed on owner
# decision (2026-09-29): on a phone the vertical layout already shows every
# device without a second layout mode, and a persistent toggle only offered a
# way to make the page worse.
_VOICE_BAR_CONTROLS = ""


def voice_endpoint():
    """One spoken command: audio in, text out, spoken answer back.

    Added 2026-09-29 for the phone. The browser records, decodes to 16 kHz mono
    WAV, and posts it here; this transcribes, executes the command, and returns
    the answer as both text and audio, so the whole loop is one round trip over
    whatever connection the phone happens to have.

    The audio is decoded IN THE BROWSER, not here. `soundfile` cannot read the
    webm/opus that MediaRecorder produces on Android Chrome, and the fallback in
    `_decode_base64_audio` -- "assume raw PCM" -- would have read the opus
    container as 16-bit samples and handed the recogniser noise, which transcribes
    to plausible garbage without ever reporting an error. Letting the browser
    call `AudioContext.decodeAudioData` avoids that failure mode and avoids
    adding ffmpeg as a production dependency for the sake of one format.

    Session-gated, like the page itself. This endpoint turns light switches and
    memory edits into a POST body, so it must not be reachable by anything that
    cannot load `/`. The command-token gate in routes.py does not cover it: that
    gate is opt-in and the token is not configured, so `/api/command` itself is
    currently reachable by anything on the LAN. That hole is real, predates
    this, and is NOT closed here -- an unknown mobile client may depend on it --
    but this endpoint is not adding to it.
    """
    from src.api import ui_auth

    if not ui_auth.is_authenticated():
        return jsonify({"success": False, "error": "Sessão necessária."}), 401

    started = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    audio_b64 = payload.get("audio_base64")
    if not audio_b64:
        return jsonify({"success": False, "error": "Sem áudio."}), 400

    # A phone on a bad connection will happily upload a very large blob. The
    # recogniser caps its own input (stt_max_audio_seconds), so anything past
    # this is wasted upload and wasted seconds, and the user waits either way.
    # 12 MB of 16 kHz mono WAV is roughly three minutes -- far beyond the cap,
    # and far below what would exhaust memory on this box.
    if len(audio_b64) > 12 * 1024 * 1024:
        return jsonify(
            {"success": False, "error": "Gravação demasiado longa."}
        ), 413

    try:
        import base64 as _b64

        from src.pipeline.stt import decode_bytes
        from src.pipeline.stt import transcribe as stt_transcribe

        # Whatever the browser recorded, sent as-is. The page used to decode it
        # with AudioContext, resample by hand and write a WAV, and that failed on
        # a real Android phone -- the user saw "falha ao enviar o áudio" with
        # recording, transcription and everything else working fine. PyAV, which
        # is already here as a dependency of faster-whisper, opens webm, opus,
        # ogg, mp4/aac and wav alike. One step instead of three, and the step
        # that cannot fail lives on the server where it can be measured.
        raw = _b64.b64decode(audio_b64)
        audio = decode_bytes(raw)
    except Exception as exc:  # noqa: BLE001 - report, never 500 a tap on a phone
        # The reason goes in the log AND in the response, so a failure that
        # cannot be reproduced in a headless browser can still be told apart by
        # the person who has the phone.
        logger.warning("voice: audio decode failed: %s: %s", type(exc).__name__, exc)
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Não consegui ler o áudio.",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ),
            400,
        )

    # Silence is the common case for a mis-tap, and the recogniser will happily
    # return a confident transcription of room noise. Rejecting it here means the
    # user is told nothing was heard instead of being shown a sentence nobody
    # said -- which they would then read aloud to their house.
    try:
        peak = float(abs(audio).max()) if len(audio) else 0.0
    except (TypeError, ValueError):
        peak = 0.0
    if peak < 0.01:
        return jsonify(
            {"success": False, "error": "Não ouvi nada.", "reason": "silence"}
        ), 200

    try:
        result = stt_transcribe(audio, language=payload.get("language") or "pt")
    except Exception as exc:  # noqa: BLE001
        logger.error("voice: transcription failed: %s", exc)
        return jsonify({"success": False, "error": "Falha ao transcrever."}), 500

    if not result.success:
        return jsonify(
            {"success": False, "error": result.error or "Não transcrevi."}
        ), 500

    spoken = (result.data or "").strip()
    if not spoken:
        return jsonify(
            {"success": False, "error": "Não percebi.", "reason": "empty"}
        ), 200

    # One dispatch, and it is the pipeline's: `respond_to_text` runs the skills
    # first and falls through to FlyBrain + SearXNG + the LLM. It is the same
    # method the local voice loop calls (assistant.py `_process_speech`) and the
    # same one `/comando` calls, which is what "the house understands the same
    # things however you ask" actually means.
    #
    # This used to branch to `_is_device_command` / `_handle_device_command`, and
    # failing that to `_execute_llm_tts`. Both halves were wrong:
    #
    # * `_handle_device_command` (routes.py) is a stub -- it returns the literal
    #   "Comando de dispositivo executado." without touching a device. A spoken
    #   "desliga a luz do balcão" was therefore answered with a confident lie,
    #   and skill_chacon_udp, whose TRIGGERS_NICKNAMES contain "luz do balcão"
    #   and which really does send the UDP frame, was never consulted.
    # * `_execute_llm_tts` calls the LLM directly. "hoje vai chover" never
    #   reached skill_weather, so it came back as the model asking to be
    #   told the weather again instead of the forecast.
    #
    # The same lie is still reachable through `/api/command`. Its only in-repo
    # caller was the Android app, which has since been removed, so the reason it
    # was left alone no longer holds. Not changed here; see docs/ROADMAP.md.
    from flask import current_app

    from src.api.routes import _speak_to_wav

    pipeline = getattr(current_app, "pipeline", None)
    if pipeline is None:
        # In every deployment where this route exists the app was built with a
        # pipeline (routes.py only registers skill routes when it has one), so
        # this is the test/unwired case. Answering anyway would mean answering
        # with the stub, which is the bug being fixed.
        logger.error("voice: no pipeline on the app, cannot answer")
        return jsonify({"success": False, "error": "Serviço indisponível."}), 503

    try:
        answer = pipeline.respond_to_text(spoken)
    except Exception as exc:  # noqa: BLE001 - a tap on a phone must not 500
        logger.error("voice: dispatch failed: %s: %s", type(exc).__name__, exc)
        return (
            jsonify({"success": False, "error": "Falha ao executar o comando."}),
            502,
        )

    if answer is None:
        # The same shape `/comando` uses for a dead LLM: the command was heard
        # and understood, and the house could not answer.
        return jsonify({"success": False, "error": "Sem resposta."}), 502

    audio_out = _speak_to_wav(answer)

    return jsonify(
        {
            "success": True,
            "transcript": spoken,
            "text": answer,
            "audio_base64": audio_out,
            "audio_format": "wav" if audio_out else None,
            "processing_time_ms": (time.perf_counter() - started) * 1000,
        }
    )


def _admin_nav_menu() -> str:
    """The shared navigation, built by the admin's own builder.

    This used to be a hand-rolled list of `<a class="nav-link">` elements. Same
    class names, none of the structure the design system actually drives: the
    nav-toggle button, the collapse below 900px, the grouped Cérebro entry. The
    result was a burger on `/` that did not look like the burger on `/admin`,
    which is the complaint that prompted this.

    Delegating gives one menu, one look, and the links stay in step with the
    admin pages automatically. `_build_nav_menu` takes a current endpoint;
    "ui" is not an admin endpoint, so nothing renders as `.active` -- correct,
    since this is not an admin page.

    The fallback exists because the voice UI is a skill: it is loaded by the
    dynamic loader and must keep working even if the admin package cannot be
    imported. Losing navigation because a menu helper is unavailable is a worse
    failure than a plainer menu, and the two links that matter on this page --
    profile and sign out -- are not admin's responsibility.
    """
    try:
        from src.api import admin as admin_mod

        return admin_mod._build_nav_menu(
            "ui",
            "admin" if _viewer_is_admin() else "user",
            extra_in_bar=_VOICE_BAR_CONTROLS,
        )
    except Exception:
        logger.warning("ui: shared nav unavailable, falling back to a plain menu")
        return (
            '<a class="nav-link" href="/perfil">'
            '<span class="lbl">Perfil</span></a>'
            '<a class="nav-link" href="/logout">'
            '<span class="lbl">Sair</span></a>'
        )


def handle_request():
        _css, _js = _shared_design()
        # The menu is the admin's own builder, so the burger here is the burger
        # on /admin rather than a lookalike built from the same class names.
        admin_links_html = _admin_nav_menu()
        return (
        """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
        <!-- PWA. viewport-fit=cover so the standalone app can use the notch
             area and the dock pads out to the home indicator. Without it the
             installed app has black bars and the bottom of the chat is a guess. -->
        <link rel="manifest" href="/public/manifest.json">
        <meta name="theme-color" content="#0a0a0a">
        <meta name="apple-mobile-web-app-capable" content="yes">
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
        <meta name="apple-mobile-web-app-title" content="Phantasma">
        <link rel="apple-touch-icon" href="/public/icons/icon-192.png">
        <title>Phantasma UI</title>
        <style>
            __SHARED_CSS__
            /* Root page chrome. Declared at the top, outside any @media, with a
               child selector: Playwright reported ZERO matching rules for
               .nav-bar when this lived further down the sheet, so the
               right-alignment silently did nothing on desktop. */
            #header-strip > .nav-bar { margin-left:auto; }
            #header-strip > .nav-bar .nav-toggle { min-width:44px; min-height:44px; }
            :root { --bg-color: #0a0a0a; --surface: #171717; --border: #262626; --text: #fafafa; --muted: #737373; --accent: #22c55e; --destructive: #ef4444; --chat-bg: var(--surface); --user-msg: #2d2d2d; --ia-msg: var(--accent); }
            body { 
                font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
                background: var(--bg-color); color: var(--text); 
                display: flex; flex-direction: column; 
                height: 100vh; height: 100dvh; margin: 0; overflow: hidden;
            }

            /* --- SIDEBAR UNIFICADA (DASHBOARD) --- */
            #header-strip {
                display: flex; align-items: flex-start; 
                background: var(--surface); 
                border-bottom: 1px solid var(--border); 
                box-shadow: 0 4px 15px rgba(0,0,0,0.3);
                /* The strip is context, not content. A fixed 240px with
                   flex-shrink:0 meant it claimed the same space whether or
                   not there was anything to show, and on a 375x667 phone
                   the chat was left 244px. Capped and shrinkable: #devices
                   scrolls, #chat-log takes the rest. */
                /* 240px on desktop; on a phone the strip has to hold the brand,
                   the device readings and the admin links, which measured 394px
                   at 375 wide. A flat 190px cap clipped the admin links
                   outright -- invisible and unclickable -- which an adversarial
                   falsifier caught after the hover work had already shipped.
                   So the cap is proportional and the device strip is the part
                   that yields: it already has its own scroll region. */
                /* 34dvh, not 46: measured floor is brand 78px + nav 67px = 145px,
                   plus a 64px peek of the device strip = ~210px. At 46dvh the
                   header took 300px of a 667px phone and the chat lost ~100px for
                   no gain -- the extra space all went to the device strip, which
                   scrolls anyway. 34dvh fits the floor and leaves the rest to the
                   chat. */
                height: 240px; max-height: 34dvh; flex: 0 1 auto; z-index: 50; 
            }

            /* Desktop: the header may take the space it needs, and the chat
               does not need to be a fixed share.

               The phone rules above exist for a 375x667 screen where the header
               and the chat compete for the same 667px, and there the cap is
               load-bearing: without it the chat loses ~100px. On a desktop the
               same cap just wastes the middle of the screen -- there is room
               for all fourteen devices AND a full chat, and forcing a
               percentage split means one of the two is always short.

               So above 900px the header grows to fit its content (the device
               strip stops being a scroller there and simply wraps), the chat
               takes whatever is left, and neither is a fixed percentage of the
               other. Below 900px everything above applies unchanged, because
               the phone is the case where the trade is real. */
            @media (min-width: 901px) {
                #header-strip {
                    height: auto;
                    max-height: none;
                    flex: 0 0 auto;
                }
                #header-strip #devices {
                    overflow-y: visible;
                    max-height: none;
                    flex: 1 1 auto;
                }
            }
                /* THE TOP BAR. A row: the ghost flush left, the sky in reading
                   order, the burger at the far right. The indicators used to sit
                   in two stacked groups either side of the logo, so telling the
                   air from the weather meant reading two places at once. */
                #brand-row {
                    display: flex; align-items: center; gap: 10px;
                    width: 100%; min-width: 0;
                }
                #sky-stage { display: flex; align-items: center; gap: 10px; min-width: 0; }
                /* Night and UV only when they mean something. Hidden by default
                   so a stale value from an hour ago does not sit there claiming
                   the moon is up at noon. */
                #moon-slot, #uv-slot { display: none; }
                #moon-slot.night, #uv-slot.day { display: flex; }
                #moon-slot svg, #uv-slot svg, #aqi-indicator svg,
                #main-weather-icon svg, #main-moon-icon svg {
                    width: 1.1em; height: 1.1em; display: block;
                }
                #uv-indicator, #aqi-indicator { display: flex; align-items: center; }
            #brand {
                /* A ROW on a desktop, a column on a phone. It was
                   `flex-direction: column` at every width, so the ghost and the
                   sky were stacked, and `justify-content: center` then centred
                   the pair INSIDE a 454px-tall header: measured, the brand and
                   the weather sat at centre-y=227 of an 880px screen while the
                   burger sat at centre-y=22. That is the owner's "o tempo
                   devia estar na mesma barra no topo mas esta ao lado" -- not
                   beside, but 200px lower, in a column where they wanted a
                   bar. The row and the top alignment go here, in the shared
                   rule, and the phone keeps its column below 768px where the
                   indicators genuinely have no room. */
                display: flex; flex-direction: row; align-items: center;
                justify-content: flex-start;
                /* The brand is the identity block and nothing else. It was
                   width:210px with a right border, which on a wide desktop
                   spent fixed space on a logo while the device strip -- the
                   part that runs out of room -- was capped at 34dvh and
                   scrolled. Below 900px it is still width:100% and still
                   carries the border, because there the rule is what separates
                   the header from the list. */
                width: auto; min-width: 0; height: 100%;
                border-right: 1px solid #333; background: #151515;
                cursor: pointer; user-select: none; z-index: 10;
                padding: 10px; box-sizing: border-box;
                /* Top-aligned, so the ghost, the weather and the burger share
                   one line. `align-items: center` centred the whole block at
                   y=69 in a 186px header while the burger sat at y=0, which is
                   the owner's "o tempo devia estar na barra no topo mas esta ao
                   lado": not beside it, just lower. The row is now the bar, and
                   its contents sit on the first line of it. */
                align-items: flex-start; padding-top: 12px;
                position: relative; overflow: hidden;
                flex: 0 0 auto;
            }
            #brand:active { background: #222; }

            /* ZONA DO CÉU (Tempo + Lua) */
            #sky-stage {
                /* A row of indicators that follows the ghost, so `width: 100%`
                   and `justify-content: center` are both wrong now: they made it
                   a full-width centred strip, which put the weather at x=170 of a
                   375px screen with nothing between it and the logo. It is a
                   group of readings that belong to the mark, not a banner. */
                display: flex; align-items: center; justify-content: flex-start;
                gap: 10px; margin-bottom: 0; width: auto;
            }
            .sky-element {
                /* A ROW. It is a column, so the icon and the temperature went
                   one under the other: measured, #main-weather-icon (29px) and
                   #main-weather-temp (29px) were stacked inside a 39px-wide
                   stage, and with #brand overflow:hidden the temperature's
                   bottom was cut off -- the owner saw "21" with a clipped
                   degree. The icon and the number belong side by side in a
                   horizontal bar. */
                display: flex; flex-direction: row; align-items: center;
                gap: 4px; position: relative;
            }
            #main-weather-icon, #main-moon-icon {
                font-size: 1.8rem; 
                filter: drop-shadow(0 0 5px rgba(0,0,0,0.5));
                transition: all 0.5s ease;
            }
            #main-weather-temp {
                font-size: 0.8rem; font-weight: bold; color: #bbb;
                margin-top: -2px; background: rgba(0,0,0,0.4); padding: 1px 6px; border-radius: 10px;
            }
            
            /* ZONA DO FANTASMA + AR */
            #ghost-stage {
                position: relative;
                display: flex; justify-content: center; align-items: center;
                margin-bottom: 5px;
            }
            #brand-logo { 
                font-size: 2rem; 
                transition: all 1s ease; z-index: 10;
            }
            
            /* Indicador de Ar (Flutuante ao lado do fantasma) */
            #aqi-indicator {
                position: absolute;
                right: -15px; bottom: 5px;
                font-size: 1.2rem;
                filter: drop-shadow(0 0 5px rgba(0,0,0,0.8));
                animation: floatWeather 6s infinite ease-in-out;
                opacity: 0.8;
            }

            /* Nome e Power */
            #brand-name { 
                font-size: 0.7rem; font-weight: bold; color: #555; 
                letter-spacing: 2px; text-transform: uppercase; margin-bottom: 5px;
            }
            #power-display {
                font-size: 1.3rem; font-weight: bold; color: #ffb74d;
                text-shadow: 0 0 10px rgba(255, 183, 77, 0.2);
                letter-spacing: 0.5px;
            }

            /* ANIMAÇÕES */
            .ghost-normal { animation: floatGhost 3s ease-in-out infinite; }
            .ghost-rain { filter: drop-shadow(0 0 10px #4db6ac) grayscale(0.6); animation: shakeGhost 5s infinite; }
            .ghost-sun  { filter: drop-shadow(0 0 15px #ffb74d) brightness(1.1); animation: floatGhost 3s infinite; }
            .ghost-storm { filter: drop-shadow(0 0 10px #7e57c2) contrast(1.2); animation: shakeGhost 0.5s infinite; }

            /* --- TOPBAR (DEVICES) --- */
            /* Always visible, outside the burger: the device readings are the
               primary content of this page and must never be behind a menu. */
            #devices {
                display:flex; flex:1; min-width:0; box-sizing:border-box;
                align-items:flex-start; align-content:flex-start;
                flex-wrap:wrap; overflow-y:auto; overflow-x:hidden;
                height:100%; padding:20px 20px 20px 0;
            }
            @media (max-width:768px) {
                /* The brand is width:100% below 768px and #header-strip is a
                   flex ROW, so the device strip was pushed past the viewport
                   (382px inside 375px). Give the devices their own row. */
                #devices { flex:1 1 100%; min-width:0; padding-right:0; }
                /* flex-wrap is what actually gives #devices its own row: a
                   100% flex-basis on a non-wrapping row just overflows. */
                #header-strip { flex-wrap:wrap; }
            }
            #devices::-webkit-scrollbar { width:4px; }
            #devices::-webkit-scrollbar-thumb { background:#333; border-radius:2px; }

            /* The menu collapses behind the burger at EVERY width, because the
               toggle is visible at every width -- the shared design system made
               the toggle permanent, so a panel that stayed pinned open on
               desktop would leave the button doing nothing there. Closed by
               default; design.js() adds .open.

               WHAT THIS BLOCK USED TO SAY, and why it is gone:

                   flex: 1; height: 100%; padding: 20px 0 20px 20px;
                   align-items: flex-start; flex-wrap: wrap;

               Those are DEVICE-STRIP rules, from the era when `#nav-menu` WAS
               the strip that held the device tiles. `#devices` is its own
               element now, and this id carries the admin menu -- so the rules
               were not merely dead, they were actively harmful:

               `height: 100%` on an absolutely positioned element resolves
               against its containing block, which is the 45px-tall `.nav-bar`.
               Measured at 1280x900 with the menu open: clientHeight 42px,
               scrollHeight 188px. A 44px window onto 188px of links, and
               `elementFromPoint` at the centre of every one of them returned
               something else -- Cérebro, Configuração, Utilizadores, Perfil and
               Sair were ALL unclickable. A menu you can open and not use is
               worse than no menu, and it looked plausible because the links were
               in the DOM and the panel had a background.

               The collapsible geometry now comes solely from the design system
               (`position:absolute; top:100%; right:0; max-height:70vh`), which
               is the same contract the admin pages get. The one thing kept here
               is `display:none` / `.open`, because the root page's menu is
               collapsible rather than always-on and the design system leaves the
               default to the page. */
            #nav-menu:not(.nav-menu-always) { display: none; }
            #nav-menu.open { display: flex; }
            #nav-menu::-webkit-scrollbar { width: 4px; }
            #nav-menu::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

              .device-room {
                  /* A BLOCK, not `inline-flex`. The rooms were inline-level and
                     wrapped, which is fine until one room's content is tall:
                     the wrap is computed against the INLINE box, and a room that
                     ran past the strip's height pushed the next one to a line of
                     its own, below the header it is supposed to live in. Measured
                     at 1920x880: the strip is 454px tall and the GERAL group was
                     at y=655, on screen but outside the header. A block-level
                     room with an internal flex column cannot do that. */
                  display: flex; flex-direction: column;
                  margin-right: 15px; margin-bottom: 15px;
                  /* No divider between rooms. The rooms are already separated
                     by their own headers and by 15px of space; the rule made a
                     row of devices read as a table with columns, which it is
                     not -- the tiles under "Sala" and under "WC" share nothing
                     and switching off one does not affect the other. The only
                     line left on the page is the one above the chat input,
                     which is a real boundary: that is where the user types. */
                  padding-right: 0; border-right: none;
                  vertical-align: top;
              }
            /* The room header, and the readings that now live in it.
               A ROW, because the whole point of moving the sensors here is that
               "SALA 21.4° · 48%" reads as one fact: the numbers belong to the
               name beside them. It was a block, so the readings dropped to their
               own line and the header grew by a row -- the same space the tiles
               used to take, just relocated. The readings are NOT uppercase and
               not bold: the name is the label, the numbers are data, and styling
               them identically is how a reading gets read as part of a name. */
            .room-header {
                font-size: 0.75rem; font-weight: bold; color: #666; margin-bottom: 8px;
                text-transform: uppercase;
                display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
            }
            /* The room icon, SIZED, at every width. `width: 15px` existed only
               inside the max-width:768px block, so on a desktop the SVG had no
               size of its own and took whatever the flex row gave it:
               measured 247x247px at 1920 wide -- a house drawing the height of
               three device tiles, and the reason a phone-sized fix looked fine
               and a desktop looked broken. An inline SVG with no intrinsic
               size in a flex row stretches to the row, and `align-items:
               baseline` (which this rule used to carry) does not constrain the
               cross axis, only the baseline. Both are fixed here. */
            .room-header svg {
                width: 16px; height: 16px; flex: 0 0 auto; opacity: .85;
            }
            .room-name { flex: 0 0 auto; }
            .room-readings {
                font-weight: normal; text-transform: none; letter-spacing: 0;
                font-size: 0.7rem; color: #4db6ac; font-variant-numeric: tabular-nums;
            }
            /* A header with no reading yet must not reserve a gap for one. */
            .room-readings[data-empty] { display: none; }
            .room-content { display: flex; gap: 8px; flex-wrap: wrap; }

            /* WIDGETS */
                /* Vertical stack, cross-axis centred, text centred -- the
                   `flex flex-col items-center text-center` behaviour. It was
                   already a column with align-items:center, but the tile was
                   pinned to height:44px, which squeezed the label into a 2-line
                   clamp at 0.6rem and made the centring invisible. 44px is kept
                   as a MINIMUM (the touch-target floor), never as a cap. */
                .device-toggle {
                    flex: 0 0 auto; display: flex; flex-direction: column;
                    align-items: center; justify-content: center; text-align: center;
                    background: #222; opacity: 0.5; transition: all 0.3s;
                    min-width: 60px; min-height: 44px;
                    border-radius: 8px; padding: 8px 10px;
                    flex-grow: 1; min-width: 0;
                }

            @media (min-width: 768px) {
                /* Was a FIXED height:56px, which cannot hold the 40px badge +
                   its 8px margin + a 3-line label + 16px padding -- the badge
                   measured 40x30 because flex-shrink ate the difference. The
                   tile now grows to its content, with 88px as the floor. */
                .device-toggle {
                    min-width: 60px;
                    min-height: 88px;
                    height: auto;
                    flex-grow: 0;
                }
            }
                .device-toggle.loaded { opacity: 1; border: 1px solid #333; }
                  .device-toggle.active .device-icon { filter: grayscale(0%); }
                  /* The last action on this tile did not happen. Red EDGES, not
                     a red fill: the switch has to stay readable, because the
                     honest thing it now shows is the state the device is really
                     in, and covering it in colour would hide the correction the
                     user most needs to see. The reason is in the title. */
                  .device-toggle.action-failed {
                      border-color: #ef4444;
                      box-shadow: 0 0 0 1px rgba(239, 68, 68, 0.55);
                  }
                  .device-toggle.action-failed .device-label { color: #f87171; }
                  .device-toggle.action-failed .device-icon { opacity: .55; }


                /* ---- Voice control in the bar ----
                   44x44 is the touch-target floor, matched to the nav burger
                   beside it: a control you have to aim at is not a control you
                   use with a phone in one hand. */
                /* ---- Ambient layer ----
                   `pointer-events: none` so it can never eat a tap, and z-index
                   below the chrome: the weather is the room the devices are in,
                   not an overlay on top of the controls. Rain sits UNDER the
                   page content; the tint sits under that. */
                #ambient, #ambient-tint {
                    position: fixed; inset: 0; pointer-events: none;
                }
                #ambient { z-index: 1; }
                #ambient-tint {
                    z-index: 0; opacity: 0; transition: opacity 1.2s ease;
                    background: radial-gradient(circle at 50% 0%, rgba(255,214,140,0.16), transparent 62%);
                }
                /* Night, and bad air: a cold wash from the top. Two layers so
                   each can come and go on its own strength. */
                #ambient-tint.night {
                    background: linear-gradient(180deg, rgba(20,30,64,0.42), transparent 55%);
                }
                #ambient-tint.hazy {
                    background: linear-gradient(180deg, rgba(150,140,120,0.20), transparent 60%);
                }
                #ambient.hidden, #ambient-tint.hidden { display: none; }
                .nav-voice {
                    background: transparent; border: 1px solid #333; color: #ccc;
                    border-radius: 8px; width: 44px; height: 44px; cursor: pointer;
                    display: inline-flex; align-items: center; justify-content: center;
                    flex: 0 0 auto; margin-left: 6px; padding: 0;
                }
                .nav-voice:hover { border-color: #555; }
                .nav-voice[disabled] { opacity: 0.4; cursor: not-allowed; }
                /* A drawn microphone rather than an emoji, so it matches the
                   rest of the bar and does not inherit the device tiles'
                   grayscale filter. */
                /* Recording: the border and the glyph go red, because the
                   microphone is HOT and the user must be able to tell at a
                   glance whether the house is listening. A colour change alone
                   is not enough for every user, so the button also carries an
                   aria-live status line (see .voice-status). */
                .nav-voice.recording { border-color: #ef4444; color: #ef4444; }
                .nav-voice.busy { border-color: var(--accent, #22c55e); color: var(--accent, #22c55e); }

                /* The chat tab, the dock and the drag grip are PHONE
                   affordances. Hidden by default and switched on inside the
                   max-width:768px block: on desktop the conversation is already
                   a column in the flow, so a "Chat" bar to open it is a control
                   that opens something already open.

                   `#chat-dock` was MISSING from this list, and it is the outer
                   element of the pair: its only styling is inside the phone
                   block, so on a desktop it fell back to the browser default
                   (`display: block`, 1920px wide) and sat under the composer as
                   a 44px band with a microphone stranded in the corner. That
                   is the "a barra de chat nem aparece" of the owner's report --
                   it was not absent, it was present and meaningless, and the
                   thing it was supposed to contain (`#chat-tab`) was 0x0 inside
                   it. Hiding the container hides the microphone with it, which
                   is correct: the composer has its own, and the voice one in
                   the nav bar is the desktop shortcut. */
                #chat-tab, #chat-grip, #chat-dock { display: none; }
                .voice-status {
                    position: fixed; left: 50%; bottom: 76px; transform: translateX(-50%);
                    background: #1e1e1e; border: 1px solid #333; color: #eee;
                    padding: 8px 14px; border-radius: 8px; font-size: 0.8rem;
                    max-width: 90vw; text-align: center; z-index: 60; display: none;
                }
                .voice-status.show { display: block; }




            
                /* The icon is the tile's badge, matching pdftools ToolCard.tsx:52-53
                   -- `flex h-10 w-10 items-center justify-center rounded-lg` with
                   `mb-2` and a hover scale. The tile itself was already
                   `flex flex-direction:column; align-items:center` (215-217);
                   only the badge box was missing, so the glyph sat loose on the
                   tile background. Styled here rather than wrapped in the JS
                   builder (line 540) so the DOM contract is untouched. */
                /* The SVG fills the badge and inherits the tile's colour, so the
                   grayscale filter for "off" applies to the glyph exactly as it
                   applied to the emoji. Sized in the same units as the badge so
                   it cannot drift from it, and `display:block` because an
                   inline SVG leaves a baseline gap inside a flex box. */
                .device-icon svg { width: 62%; height: 62%; display: block; }
                /* The microphone, drawn. The old version was a CSS capsule plus
                   a `::after` stand, which read as a blob at 16px and was not an
                   icon at all -- the owner asked for an icon and a glyph. */
                .mic-svg { width: 22px; height: 22px; display: block; }
                #voice-btn-chat .mic-svg { width: 19px; height: 19px; }
                /* The chat avatar gets its OWN size. 62% of a box with no size
                   is 0x0 -- and worse, in a flex row the box grew to fit the
                   ghost instead: measured 183x150 for a 150px tall glyph beside
                   a 12px line of text. Sized here, once, so it cannot be
                   negotiated with its container. */
                .ia-avatar {
                    width: 26px; height: 26px; flex: 0 0 auto; align-self: flex-start;
                    display: flex; align-items: center; justify-content: center;
                }
                .ia-avatar svg { width: 22px; height: 22px; display: block; }
                /* Explicit, not a percentage. `#brand-logo` is sized by its
                   font-size and has no width of its own, so 62% of nothing is
                   0x0 -- measured: the brand rendered at 0x0, which looks exactly
                   like "the SVG failed to load". */
                #brand-logo svg { width: 1em; height: 1em; display: block; }
                #main-weather-icon svg, #main-moon-icon svg {
                    width: 1em; height: 1em; display: block;
                }
                #big-ghost svg { width: 1em; height: 1em; display: block; }
                .device-icon {
                    display: flex; align-items: center; justify-content: center;
                    flex: 0 0 auto;
                    width: 2.5rem; height: 2.5rem;
                    border-radius: 0.5rem;
                    background: var(--surface-2, #252525);
                    font-size: 1.25rem;
                    filter: grayscale(100%);
                    transition: filter 0.3s, transform 0.3s;
                    margin-bottom: 0.5rem;
                }
                .device-toggle:hover .device-icon { transform: scale(1.05); }
                  /* One line, the full name, and a marquee only when it does
                     not fit. The old rule wrapped to 3 lines and shortened the
                     text, which is why the owner saw tiles that only said
                     "luz". A window that clips to one line cannot show a name
                     wider than itself, so an overflowing name is moved instead
                     of cut (applyLabelMarquee sets the shift and duration). */
                  .device-label {
                      font-size: 0.65rem; color: #aaa; width: 100%; text-align: center;
                      line-height: 1.15; white-space: nowrap; overflow: hidden;
                      position: relative;
                  }
                  /* The mover. Separate from the window, because a box cannot
                     clip itself and `text-overflow` needs a block to act on. */
                  .device-label .device-label-text { display: inline-block; }
                  /* Left-aligned while moving, so the drift starts at the first
                     word instead of at the middle of the text. */
                  .device-label.marquee { text-align: left; }
                  .device-label.marquee .device-label-text {
                      animation: device-marquee var(--marquee-duration, 10s) linear infinite;
                      will-change: transform;
                  }
                  /* A pause at each end: the first word must be readable before
                     the line moves. */
                  @keyframes device-marquee {
                      0%, 12% { transform: translateX(0); }
                      88%, 100% { transform: translateX(var(--marquee-shift, 0px)); }
                  }
                  /* The device that is doing something looks like it. An
                     extractor with its blades turning and a dehumidifier with
                     the drop running down reads as running from across the
                     room, at a glance, without reading a single word.

                     Scoped to `[data-state="on"]`, which is the tile's real
                     device state and not a guess: an extractor that is off has
                     blades that do not move. Transform-only, centred on the
                     fan hub, so the icon's box never changes -- the tile grid
                     does not reflow, and the rest of the icon does not drift
                     with the blades.

                     The blade path is a single path holding all four blades,
                     so it is one element rotating; the droplet falls as a
                     short translate, looping, so it reads as running rather
                     than as a bounce. */
                  @keyframes fanSpin { to { transform: rotate(360deg); } }
                  @keyframes dropFall {
                      0%   { transform: translateY(0);    opacity: 1; }
                      55%  { transform: translateY(2.5px); opacity: .45; }
                      70%  { transform: translateY(0);    opacity: 0; }
                      71%  { transform: translateY(0);    opacity: 0; }
                      100% { transform: translateY(0);    opacity: 1; }
                  }
                  .tile-icon-fan, .tile-icon-drop {
                      transform-box: fill-box;
                      transform-origin: center;
                  }
                  [data-state="on"] .tile-icon-fan { animation: fanSpin 1.6s linear infinite; }
                  [data-state="on"] .tile-icon-drop { animation: dropFall 2.4s ease-in infinite; }
                  @media (prefers-reduced-motion: reduce) {
                      [data-state="on"] .tile-icon-fan,
                      [data-state="on"] .tile-icon-drop { animation: none; }
                  }
                  /* Reduced motion: wrap to two lines instead of moving. The
                     full name is still readable; only the motion is gone. */
                  .device-label.wrap {
                      white-space: normal; overflow-wrap: break-word; hyphens: auto;
                  }
                  /* A lamp that is on is filled, not just un-greyed. The wash is
                     amber to match .device-power, and only the `luz` glyph (luz,
                     candeeiro) is filled -- the class comes from icon(kind). */
                  .device-toggle.active .device-icon svg.dev-icon-luz { fill: #ffcf52; }


                              /* The watts, on their own line under the name. Amber because
                     that is the colour the label used to take, so "this thing is
                     drawing power" is still a glance rather than a reading of
                     small text. */
                  .device-power {
                      font-size: 0.6rem; color: #ffb74d; width: 100%;
                      text-align: center; line-height: 1.1;
                      font-variant-numeric: tabular-nums;
                  }
                  /* No reading yet means no reserved row, or a wall of tiles
                     with a blank line under each one. */
                  .device-power[data-empty] { display: none; }
                  .switch { position: relative; display: inline-block; width: 28px; height: 14px; margin-bottom
: 2px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #444; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 10px; width: 10px; left: 3px; bottom: 2px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: var(--ia-msg); }
            input:checked + .slider:before { transform: translateX(12px); }

            /* CHAT */
            #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }
            #chat-log { 
                flex: 1; padding: 15px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px;
                padding-top: 25px; 
            }
            .msg-row { display: flex; width: 100%; align-items: flex-end; }
            .msg-row.user { justify-content: flex-end; }
            .msg-row.ia { justify-content: flex-start; }
            .ia-avatar { font-size: 1.5rem; margin-right: 8px; margin-bottom: 5px; animation: floatGhost 4s ease-in-out infinite; }
            .msg { max-width: 80%; padding: 10px 14px; border-radius: 18px; font-size: 1rem; line-height: 1.4; white-space: pre-wrap; }
            .msg-user { background: var(--user-msg); color: #fff; border-bottom-right-radius: 2px; }
            .msg-ia { background: var(--chat-bg); color: #ddd; border-bottom-left-radius: 2px; border: 1px solid #333; }
            
            .typing-indicator { display: inline-flex; align-items: center; padding: 12px 16px; background: var(--chat-bg); border-radius: 18px; border-bottom-left-radius: 2px; border: 1px solid #333; }
            .dot { width: 6px; height: 6px; margin: 0 2px; background: #888; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
            .dot:nth-child(1) { animation-delay: -0.32s; } .dot:nth-child(2) { animation-delay: -0.16s; }
            @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

            #chat-input-box { padding: 10px; background: #181818; border-top: 1px solid #333; display: flex; gap: 10px; flex-shrink: 0; padding-bottom: max(10px, env(safe-area-inset-bottom)); align-items: flex-end; }
            /* `height: 24px` with `padding: 12px` and `box-sizing: border-box`
               leaves ZERO content box, so the placeholder was drawn through the
               middle of the field and clipped: the composer read as "Mensagem"
               cut in half. It was a copy of a phone rule -- the mobile block
               overrode it with `min-height: 44px; height: auto` and nobody
               looked at the desktop, where the field sits in a 69px row and has
               room for a line. `min-height` is what both widths want: one line
               to start, growing to `max-height` as the owner types.

               The JS autosize in `sendChatCommand`/the keydown handler sets an
               explicit inline height, so this is the resting size only. */
            #chat-input { flex: 1; background: #2a2a2a; color: #fff; border: none; padding: 12px; border-radius: 20px; font-size: 16px; outline: none; resize: none; min-height: 44px; max-height: 100px; font-family: inherit; overflow-y: hidden; }
            #chat-send { 
    background: var(--ia-msg); color: white; border: none; padding: 0 12px; border-radius: 25px; 
    font-weight: bold; cursor: pointer; 
    height: 48px; flex: 0 0 auto; 
}

            @keyframes floatGhost { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-5px); } }
            @keyframes floatWeather { 0%, 100% { transform: translateY(0px) scale(1); } 50% { transform: translateY(-3px) scale(1.05); } }
            @keyframes shakeGhost { 0% { transform: translate(1px, 1px) rotate(0deg); } 10% { transform: translate(-1px, -2px) rotate(-1deg); } 20% { transform: translate(-3px, 0px) rotate(1deg); } 30% { transform: translate(3px, 2px) rotate(0deg); } 40% { transform: translate(1px, -1px) rotate(1deg); } 50% { transform: translate(-1px, 2px) rotate(-1deg); } 60% { transform: translate(-3px, 1px) rotate(0deg); } 70% { transform: translate(3px, 1px) rotate(-1deg); } 80% { transform: translate(-1px, -1px) rotate(1deg); } 90% { transform: translate(1px, 2px) rotate(0deg); } 100% { transform: translate(1px, -2px) rotate(-1deg); } }

            #easter-egg-layer { position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 9999; display: flex; align-items: center; justify-content: center; visibility: hidden; }
            #big-ghost { font-size: 15rem; opacity: 0; transform: scale(0.5); transition: all 0.3s; }
            .boo #easter-egg-layer { visibility: visible; }
            .boo #big-ghost { opacity: 1; transform: scale(1.2); }
            #cli-help { background: #111; border-top: 1px solid #333; max-height: 0; overflow: hidden; transition: max-height 0.3s; }
            #cli-help.open { max-height: 200px; overflow-y: auto; padding: 10px; }
            #help-toggle { text-align: center; font-size: 0.8rem; color: #666; padding: 5px; cursor: pointer; }

            #header-strip #devices { min-height: 0; overflow-y: auto; }
            #main { flex: 1 1 auto; min-height: 0; }
            #chat-log { flex: 1 1 auto; min-height: 0; }

            /* --- MOBILE (≤ 768px) --- */
            /* --- admin links on a phone: one 44px row ---------------------
               Measured at 375 wide: the links wrapped onto two rows of 48px
               (127px of nav-bar) and, stacked under the brand and the device
               strip, pushed past the header's cap -- so they were rendered
               outside it and clipped. Four icon-only targets in one row are
               4x44 = 176px, which fits. The accessible name comes from
               aria-label, so hiding the text costs nothing for a screen
               reader, and the 44px floor is preserved. */
            @media (max-width: 768px) {
                .nav-menu.nav-menu-always { flex-wrap:nowrap; }
                .nav-menu.nav-menu-always .nav-link {
                    min-width:44px; min-height:44px; width:44px;
                    padding:0; justify-content:center; font-size:0; gap:0;
                }
                .nav-menu.nav-menu-always .nav-link span.lbl { display:none; }
                .nav-menu.nav-menu-always .nav-link .ico { font-size:1.15rem; }
            }
            @media (max-width: 768px) {
                #header-strip { flex-direction: column; height: auto; min-height: 0; }
                /* The strip became a COLUMN above, but the max-width:768px block
                   still leaves `flex-wrap: wrap` on it (that rule was written for
                   a ROW). Wrapping a column whose height is auto wraps on the
                   CROSS axis -- i.e. horizontally -- so #brand filled 0-375px and
                   #devices was pushed onto a second line at left:375px:
                   documentElement.scrollWidth 750 inside a 375px viewport. A
                   column must not wrap; the items already stack vertically. */
                #header-strip { flex-wrap: nowrap; }
                /* THE mobile layout bug, and it was one declaration.
                   The base rule is `height:240px; flex-shrink:0` (line ~94).
                   Mobile set `height:auto` but never reset `flex-shrink`, so
                   inside `body{height:100dvh; overflow:hidden}` the strip
                   claimed the FULL content height of all 14 tiles at 84px each
                   and pushed the composer out of the viewport, where
                   overflow:hidden clipped it. Measured: document scrollHeight
                   was exactly the viewport height, so there was nothing to
                   scroll back to -- the composer was unreachable. The strip
                   has to be allowed to shrink and cap itself, with #devices as
                   the scrolling region. */
                #header-strip {
                    flex: 0 1 auto;
                    min-height: 0;
                    /* Kept in step with the base rule (34dvh). These two caps
                       drifted apart and the mobile one won, so the header
                       stayed at 300px of a 667px phone and the chat lost
                       ~100px for nothing. */
                    max-height: 34dvh;
                    overflow: hidden;
                }
                #devices {
                    flex: 1 1 auto;
                    /* min-height, not 0: with the brand now refusing to shrink,
                       devices is the only thing that yields, and at 320x568
                       yielding to zero would hide every device. 120px shows
                       roughly one full row plus the start of the next. */
                    min-height: 120px;
                    width: 100%;
                    overflow-y: auto;
                    -webkit-overflow-scrolling: touch;
                }
                /* #chat-log must take what is left and scroll internally, or the
                   14 tiles starve it. min-height:0 is what actually allows a
                   flex child to shrink below its content size. */
                #chat-log {
                    flex: 1 1 auto;
                    min-height: 0;
                    overflow-y: auto;
                    -webkit-overflow-scrolling: touch;
                }
                /* #brand is `height:100%` + `justify-content:center` +
                   `overflow:hidden`. In the column strip its content is taller
                   than its box, so centring pushed the top out of view and
                   overflow:hidden cut it: measured, #sky-stage and
                   #main-weather-icon sat at top:-23px on EVERY mobile size --
                   the weather icon was half off-screen. Let the brand take its
                   natural height so nothing is clipped. */
                    /* The word mark has no room on a phone: at 375px there is
                       space for the logo, four indicators and the burger, or the
                       logo, four indicators and the name. The ghost is already
                       the name. And the ghost gets bigger, because it is the
                       thing you look at first. */
                    #brand-name { display: none; }
                    #sky-stage { gap: 12px; }
                #brand { flex: 0 0 auto; height: auto; }

                #chat-input { min-height: 44px; height: auto; }
                #chat-send { min-height: 44px; }

                /* 320x568 measured 7 of 14 devices FULLY hidden: the tiles are
                   84px tall and only ~2 rows fit the bounded strip. On short
                   screens compact the tile so the devices are actually
                   reachable rather than merely scrollable. */
                @media (max-height: 700px) {
                    .device-toggle { min-height: 64px; padding: 6px 8px; }
                    .device-icon { width: 32px; height: 32px; font-size: 1rem; margin-bottom: 4px; }
                    .device-label { font-size: 0.7rem; }
                }

                /* Scroll affordance. The strip is an inner scroller inside a
                   non-scrolling app shell, so a half-visible row was
                   indistinguishable from a rendering bug. The class is toggled
                   in JS only when the container actually overflows, so a
                   fully-visible grid is not dimmed for no reason. */
                #devices.is-scrollable {
                    -webkit-mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 28px), transparent 100%);
                    mask-image: linear-gradient(to bottom, #000 0, #000 calc(100% - 28px), transparent 100%);
                }
                /* #header-strip is a COLUMN here, but the max-width:768px block
                   above still gave #devices `flex: 1 1 100%` + the strip
                   `flex-wrap: wrap` -- machinery written for a ROW. In a column
                   flex-basis is the HEIGHT, so none of it positioned the strip
                   horizontally, and with `align-items: flex-start` on the strip
                   #devices measured at left:375px inside a 375px viewport
                   (documentElement.scrollWidth 750). A column needs no flex
                   basis games: full width, natural height. */
                /* The device strip is the SCROLLING region, so it is the part
                   that must yield when the header is capped. With `flex: 0 0 auto`
                   it took its natural height (measured 299px at 375 wide) and
                   pushed the admin links out of the header, where the strip's
                   overflow:hidden clipped them: measured invisible AND
                   unclickable at 375x667. Catching that required checking
                   hit-testing, not just `getBoundingClientRect().width > 0`. */
                #devices { flex: 1 1 auto; min-height: 64px; width: 100%; overflow-y: auto; }
                #brand {
                    width: 100%; height: auto; flex-direction: row; align-items: center;
                    /* flex-start, not space-between. The bar is a row that reads
                       left to right -- ghost, sky, burger -- and space-between
                       pushed the sky to the middle of the screen, which is not
                       "a seguir" anything. The burger is fixed to the right
                       independently, so nothing here needs pushing. */
                    justify-content: flex-start; border-right: none; padding: 8px 12px;
                }
                #sky-stage { margin-bottom: 0; gap: 10px; }
                #ghost-stage { margin-bottom: 0; }
                /* Bigger than the 0.8rem this used to be, and last in the
                   stylesheet: both rules carry !important, so the later one wins,
                   and this one has to be the later one. */
                #brand-logo svg { width: 1em; height: 1em; }
                /* right was -8px, hanging outside the box on purpose on desktop. With
                   #header-strip overflow:hidden on mobile that clipped the AQI value
                   in half at the screen edge, so it reads as a stray character. */
                #aqi-indicator { right: 4px; bottom: 2px; font-size: 1rem; }
                #brand-name { font-size: 0.75rem; }
                #power-display { font-size: 1.1rem; }
                #nav-menu:not(.nav-menu-always) {
                    width: 100%; padding: 10px; box-sizing: border-box;
                    height: auto; align-content: flex-start;
                    flex-wrap: wrap; overflow-x: hidden; overflow-y: auto;
                    max-height: 46vh;
                }
                .device-room { margin-right: 10px; margin-bottom: 0; padding-right: 10px; flex-shrink: 0; }
                .device-toggle { min-width: 60px; min-height: 84px; height: auto; }
                .device-icon { font-size: 1.2rem; }
                .device-label, .room-header { font-size: 0.75rem; line-height: 1.15; }
                #admin-links {
                    /* Column, like every other nav: measured, 6 links collapsed
                       into 1 row here while /admin stacked 7 into 7. The root
                       menu and the admin menu must present the same list the
                       same way. */
                    display: flex; flex-direction: column; flex-wrap: wrap; gap: 8px; width: 100%;
                    padding: 10px 2px 2px; margin-top: 4px;
                    border-top: 1px solid var(--border); box-sizing: border-box;
                }
                #admin-links a {
                    display: inline-flex; align-items: center; gap: 6px;
                    min-height: 44px; padding: 0 14px; border-radius: 8px;
                    border: 1px solid var(--border); background: var(--surface-2);
                    color: var(--text); text-decoration: none; font-size: 0.85rem;
                }
                #admin-links a:hover { border-color: var(--brand-500); color: var(--brand-400); }
                #admin-links a:focus-visible { outline: 2px solid var(--brand-500); outline-offset: 2px; }
                #admin-links a.logout { color: var(--destructive); }
                /* Was `width:max-content; max-width:none`, which sized the room
                   strip to fit every tile on ONE line. Measured at 375px: the
                   strip was 361px wide and its tiles reached right=514px,
                   forcing 382px of horizontal document overflow. The strip must
                   be allowed to WRAP inside the viewport, so the max-content
                   sizing is dropped and the row is capped to its container. */
                .room-content { width: 100%; max-width: 100%; box-sizing: border-box; }
                #chat-input { font-size: 16px; }
                .msg { max-width: 92%; font-size: 1.05rem; }
                #cli-help.open { max-height: 150px; }
            }
            /* ---- device strip on a phone ----
               The strip used to become a full-width horizontal scroller on
               phones, pushing the chat log off-screen. The fix was never a
               hamburger: the strip scrolls, and the admin links sit beside it
               as a short always-visible row. Nothing here is conditional. */
            /* The links stay visible at every width. On a phone they shrink to
               icons with an accessible name rather than disappearing behind a
               toggle -- five short links fit; a menu gating them did not earn
               its tap. */
            /* The shared .nav-menu is display:none -- the ADMIN pages hide theirs
               behind a burger, and that default is unconditional, not inside a
               media query. This page has no burger, so it opts out with
               .nav-menu-always, declared in design.py next to the rule it
               overrides. Playwright measured 0x0 here before it existed. */
            /* Right-aligned at EVERY width. Playwright measured nav-bar at
               x=210 on a 1280px viewport when this rule only applied below
               900px -- the burger sat left of centre and its panel opened at
               x=54, i.e. to the left of the button that controls it. */
            /* !important on purpose: .nav-bar in the shared design system is
               position:sticky with its own box, and the root page's header
               layout must win. Measured: without it the burger sat at x=210 on a
               1280px viewport (left of centre) and its panel opened at x=54, to
               the LEFT of the button that opens it. */
            .nav-bar {
                margin-left:auto !important;
                /*  and nothing else. An earlier version
                   added  to line the burger up with the
                   weather, and that pushed it off y=0 -- which the desktop
                   layout test had been asserting since it was written. The
                   burger is at the top of the bar and the brand carries its own
                   ; nudging the burger instead of aligning the two
                   moves the one element that was already right. */
                align-self: flex-start;
            }

            /* Admin panel: collapses behind the burger, same contract as the
               admin pages and the same design.js() that drives it. */
            /* The brand owns the left edge; navigation is pushed to the right
               with margin-left:auto so the two never compete. The burger is
               44x44 at every width -- the same floor as every other control,
               and the reason this is not a 24px icon. */
            #nav-menu:not(.nav-menu-always) { right:0; }
            /* No burger. Removed 2026-09-27 by owner decision, after a round
               that had just added one: the panel holds four admin links and
               nothing else, so the toggle gated nothing worth gating. They are
               always visible now, right-aligned because the left edge belongs
               to the brand. */
            @media (max-width: 480px) {
                /* The emoji is the icon; font-size 0 hides the redundant text
                   but the accessible name comes from the aria-label below. */
            }

            /* --- FlyBrain reaction bar -------------------------------------
               44px targets, same floor as every other control: these are the
               primary way to teach the brain, so a miss is a lost signal. */
            .react-holder { display:flex; justify-content:flex-end; margin-top:2px; }
.react-bar {
  display:flex; gap:4px; flex-wrap:wrap;
  /* Closed state: invisible AND inert. `opacity` alone was the bug: it hides the
     bar without removing it from hit-testing, so the 44px row kept swallowing
     taps meant for the text. `pointer-events:none` takes it out of the target
     tree. The buttons stay focusable, which is what `:focus-within` needs --
     `visibility:hidden` would break that (R2). */
  opacity:0; pointer-events:none;
  transition:opacity .15s ease;
}
/* Open state, one trigger per input model. The state lives on the .msg-row
   because that element survives the /api/reactions backfill: the .react-bar
   innerHTML is replaced when the emoji map arrives, and state kept on the bar
   would die with it (R7). */
.msg-row:hover > .react-holder .react-bar,
.msg-row:focus-within > .react-holder .react-bar,
.msg-row.react-revealed > .react-holder .react-bar {
  opacity:1; pointer-events:auto;
}
/* Closed at EVERY width, not only on touch. The holder being merely
   transparent reserved a 44px band inside every message forever, so the replies
   were permanently indented and the band was a dead zone for text selection.
   `display:none` is what makes the closed state actually closed; the input model
   only decides WHAT opens it (hover / focus-within / long-press). */
.msg-row > .react-holder { display:none; }
.msg-row:hover > .react-holder,
.msg-row:focus-within > .react-holder,
.msg-row.react-revealed > .react-holder { display:flex; }
            .react-btn {
              min-width:44px; min-height:44px; padding:0 6px;
              background:transparent; border:1px solid transparent;
              border-radius:var(--radius,8px); cursor:pointer;
              font-size:1.15rem; line-height:1;
              display:inline-flex; align-items:center; justify-content:center;
            }
            .react-btn:hover { background:var(--surface-2,#222); border-color:var(--border); }
            .react-btn:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
            /* "reacted" is set optimistically and reverted on a network error,
               so a stuck highlight always means the signal was accepted. */
            .msg.reacted { border-color:var(--accent); }
            .react-loading { opacity:.5; font-size:.85rem; padding:0 8px; }
            /* The chosen reaction, so it is visible which one was given. A
               border + tint rather than a colour swap, so it stays legible on
               both themes without relying on --accent resolving. */
            .react-btn.is-chosen {
              background:rgba(255,255,255,.10);
              border-color:var(--brand-500, #3b82f6);
              box-shadow:inset 0 0 0 1px var(--brand-500, #3b82f6);
            }
            /* The outcome of the reaction, from the server's own report. */
            .react-note {
              font-size:.75rem; color:var(--muted, #999);
              padding:2px 0 0; text-align:right; line-height:1.4;
            }
            .react-note.is-error { color:var(--destructive, #ef4444); }
            /* "Guardar esta resposta como no": the explicit alternative to
               auto-creating a concept on every unmatched reaction. */
            .react-count { font-size:.75rem; color:var(--muted); align-self:center; }

            /* Accessibility: visible focus outline */
            .device-toggle:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
            }
                /* ---- PHONE LAYOUT OVERRIDE LAYER ----
                   Deliberately LAST in the stylesheet. An earlier version sat in
                   the middle, where later rules won on order: `#main` computed
                   as `position: relative` instead of absolute, so the chat
                   panel stayed IN the flow, took 459px of a 667px viewport, and
                   the device strip collapsed to 40px. The tiles were not hidden
                   by a rule about the tiles -- they were squeezed by a rule
                   about the chat. Cascade order, not intent. */
                @media (max-width: 768px) {
                    html, body { height: 100%; overflow: hidden; }
                    body { display: flex; flex-direction: column; }
                    /* `#devices` is a CHILD of #header-strip, so no amount of
                       flex on #devices makes it grow past its parent. Measured
                       at 375x667 before this: the strip was 208px and #devices
                       was 45 of it -- the tiles were squeezed by the brand
                       wrapper, not by anything about the tiles.

                       `display: contents` removes the wrapper from the layout
                       while leaving the DOM untouched, so #brand and #devices
                       become children of the body's column: the brand takes its
                       natural height, the tiles take everything that is left.
                       Every `#header-strip #devices` selector still matches,
                       because the markup did not change. */
                    #header-strip { display: contents; }
                    #brand { flex: 0 0 auto; }

                    /* THE BURGER GOES TO THE TOP, on owner instruction, and to
                       the top-RIGHT: that is the corner a right thumb reaches on
                       a phone held in one hand, and it is where every other app
                       puts it. It was inside the brand strip, bottom-right, which
                       is also fine -- except it shared the bottom band with the
                       dock and the two competed for the same corner. Fixed to the
                       top of the viewport, out of the brand's way. */
                    .nav-bar {
                        position: fixed; top: 8px; right: 8px; z-index: 65;
                        margin: 0; display: flex; align-items: center;
                    }
                    #nav-menu {
                        /* The menu was 306px tall -- 46vh -- which wrapped the
                           sign-out into a second flex column and pushed it to
                           x=365, off a 375px screen. Every entry is a single row
                           and the panel scrolls if the list is ever longer. */
                        max-height: calc(100vh - 24px);
                        overflow-y: auto; align-content: flex-start;
                        width: min(78vw, 320px);
                    }
                    #nav-menu .nav-group { flex-direction: column; align-items: stretch; width: 100%; }
                    #nav-menu .nav-brand { display: none; }
                    #nav-menu .nav-spacer, #nav-menu .nav-sep { display: none; }
                    #nav-menu .nav-link { width: 100%; min-height: 48px; }

                    /* ALL TILES BY DEFAULT, on owner instruction. The rooms were
                       laid out in a ROW with `flex-shrink: 0`, so the strip
                       measured 857px of content inside a 375px box with
                       `overflow-x: hidden`: 7 of 17 switches were painted outside
                       the edge and unreachable, while `scrollWidth == innerWidth`
                       so the "no side scroll" test passed. Rooms stack, tiles
                       wrap inside a room, and the strip scrolls vertically only. */
                    #devices { flex-direction: column; align-items: stretch; }
                    .device-room {
                        display: block; width: 100%; margin-right: 0;
                        border-right: 0; padding-right: 0;
                    }
                    .room-content {
                        display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start;
                    }
                        .room-header {
                        display: flex; align-items: center; gap: 6px;
                        margin: 12px 0 6px; color: #9a9a9a; font-size: 0.68rem;
                        letter-spacing: 0.06em; text-transform: uppercase;
                    }
                    .room-header svg { width: 15px; height: 15px; flex: 0 0 auto; opacity: 0.85; }

                    /* THE PRIORITY: the tiles get the screen. min-height:0 and
                       flex:1 1 auto so the strip takes what is left and scrolls
                       INSIDE itself. No max-height cap -- the 22vh cap is what
                       forced a nested scroller into a 20vh band, and a
                       scroller that small reads as a clipping bug. */
                    #devices {
                        flex: 1 1 auto; min-height: 0; max-height: none;
                        width: 100%; overflow-y: auto;
                        -webkit-overflow-scrolling: touch;
                    }
                    #devices.is-scrollable { -webkit-mask-image: none; mask-image: none; }

                    /* The panel covers the screen and is OUT of the flow, so it
                       cannot take height from the tiles. `fixed` rather than
                       `absolute`: #main's ancestors are not positioned, and
                       `absolute` against the initial containing block behaved
                       differently depending on whether an ancestor had a
                       transform.

                       NOT `inset: 0`. The owner asked for the top bar to stay
                       visible with the chat open, and at 375x667 `inset: 0`
                       covered it completely -- not because the bar was short,
                       but because below 768px `#header-strip` is
                       `display: contents`, so #brand and #devices are plain
                       static flex children of the body with no z-index, and a
                       fixed panel at z-index 40 paints straight over them.
                       `top: var(--brand-h)` is measured from the live #brand
                       box (see syncBrandHeight) because the strip has no box of
                       its own to copy a height from. */
                    #main {
                        position: fixed; left: 0; right: 0; bottom: 0;
                        top: var(--brand-h, 0px); z-index: 40;
                        display: flex; flex-direction: column; background: #0a0a0a;
                        transform: translateY(100%);
                        transition: transform 0.2s ease-out, visibility 0.2s;
                        visibility: hidden; pointer-events: none;
                    }
                    #main.open {
                        transform: translateY(0); visibility: visible;
                        pointer-events: auto;
                    }
                    /* While dragged, the panel follows the finger and nothing
                       under the thumb is clickable. */
                    #main.dragging { transition: none; }
                    #main.dragging #chat-log,
                    #main.dragging #chat-input-box { pointer-events: none; }

                    #chat-log { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
                    /* Room for the HANDLE, so the first thing the ghost says is
                       not underneath it. 26px of padding against a 44px handle
                       put the first row at y=26, and `elementFromPoint` on it
                       returned the handle. The number has to be read off the
                       handle, not guessed smaller. The handle is now a
                       transparent row inside the panel, so the padding is what
                       keeps the first message clear of it. */
                    #main.open #chat-log { padding-top: 52px; }
                    #chat-input-box { flex: 0 0 auto; padding-right: 12px; }

                    /* ---- THE DOCK: one prominent bar, bottom of the screen ----
                       Pull it up to open the conversation, push it down to get
                       back to the tiles. Full width, because the ask was a bar
                       and not a pill, and because a 71px pill in the corner is
                       something a thumb misses while a 60px band across the
                       bottom is not. The microphone sits inside it, so "talk to
                       the house" and "read the conversation" are the same place
                       and the two cannot overlap. */
                    #chat-dock {
                        position: fixed; left: 0; right: 0; bottom: 0; z-index: 70;
                        display: flex; align-items: stretch; gap: 0;
                        background: #141414; border-top: 1px solid #2e2e2e;
                        padding-bottom: env(safe-area-inset-bottom);
                        box-shadow: 0 -4px 18px rgba(0,0,0,0.55);
                    }
                    #chat-tab {
                        flex: 1 1 auto; display: flex; flex-direction: column;
                        align-items: center; justify-content: center; gap: 4px;
                        min-height: 60px; padding: 8px 12px;
                        background: transparent; border: 0; color: #ddd;
                        font-size: 0.8rem; letter-spacing: 0.02em; cursor: pointer;
                        /* The handle you actually grab. Wide, high-contrast, and
                           the visual top of the bar, because the bar is the
                           affordance and the word "Chat" is only its label. */
                        touch-action: none;
                    }
                    #dock-grip {
                        display: block; width: 56px; height: 5px; border-radius: 3px;
                        background: #6b6b6b; box-shadow: 0 0 0 1px #0a0a0a;
                    }
                    #dock-label { color: #cfcfcf; }
                    #chat-dock .nav-voice {
                        width: 60px; min-width: 60px; align-self: stretch;
                        margin: 0; border: 0; border-left: 1px solid #2e2e2e;
                        border-radius: 0; background: #1c1c1c; color: #eee;
                    }
                    /* OPEN: the dock leaves, because a full-width bar pinned to
                       the bottom is exactly where the composer is. It was
                       measured covering it -- tab [0,607,315,60] over input box
                       [0,574,375,93] -- and the `top:10px` meant to lift the close
                       button did nothing at all, because the tab became a static
                       flex child of the dock when the dock was introduced, so
                       `top`/`right` had no positioned element to apply to.

                       The composer has its own microphone, so nothing is lost by
                       the dock going. The way back is the handle at the top of
                       the panel and the drag.

                       This line was, for a while, `#chat-dock #chat-dock` followed
                       by this comment: a selector with no brace. A malformed
                       selector swallows the rule that follows it, which is why
                       `display: none` appeared to be ignored while being present
                       and correct in the file. */
                    body.chat-open #chat-dock { display: none; }
                    /* The `background: #9a9a9a` that used to be here is gone, and
                       that one line was the whole complaint. It painted the
                       grip's full 375px width, so opening the chat put a grey
                       slab across the top of the screen. The affordance is the
                       pill and the chevron now; the row stays transparent. */
                    /* Room for the dock while it is on screen. */
                    body:not(.chat-open) #chat-log { padding-bottom: 0; }

                    /* ---- THE PERSIANA (roller blind) ----
                       The panel unrolls downward from its own top edge rather
                       than sliding up as a rectangle. Implemented with
                       clip-path, because a translate alone reads as a sheet
                       moving; an edge that travels down the screen while the
                       content beneath it is revealed reads as a blind being
                       pulled down, which is what was asked for. */
                    #main {
                        clip-path: inset(0 0 100% 0);
                        opacity: 0.98;
                    }
                    #main.open {
                        animation: persiana-abrir 0.26s ease-out both;
                    }
                    @keyframes persiana-abrir {
                        from { clip-path: inset(0 0 100% 0); }
                        to   { clip-path: inset(0 0 0 0); }
                    }
                    @keyframes persiana-fechar {
                        from { clip-path: inset(0 0 0 0); }
                        to   { clip-path: inset(0 0 100% 0); }
                    }
                    #main.closing { animation: persiana-fechar 0.2s ease-in both; }

                     /* The drag handle. A sheet you can pull down, which is the
                        gesture the owner asked for and the one a phone user
                        already expects from a full-screen sheet.

                        The handle IS the close control, so the whole 44px row is
                        the target -- a close target you have to hit is not a
                        close target, and it is the only one on the panel. What
                        it must NOT be is a full-width coloured band: that is
                        what the owner was seeing and calling "a barra
                        cinzenta", a 375px slab of #9a9a9a across the top of the
                        screen. So the row is transparent and only the pill and
                        the chevron are drawn, centred, on the panel's own top
                        edge. Width comes from `inset`, not from a background. */
                    #chat-grip {
                        position: absolute; top: 0; left: 0; right: 0; z-index: 2;
                        display: none; flex-direction: column;
                        align-items: center; justify-content: center;
                        min-height: 44px; touch-action: none; cursor: grab;
                        background: none; border: 0; color: #8a8a8a;
                        font-size: 0.72rem; letter-spacing: 0.06em;
                        gap: 3px; padding: 0; -webkit-tap-highlight-color: transparent;
                    }
                    body.chat-open #chat-grip { display: flex; }
                    /* The pill, and only the pill, is ink. A faint scrim behind
                       it keeps the handle legible over a bright chat bubble
                       without being a bar. */
                    #chat-grip .grip-pill {
                        display: block; width: 44px; height: 4px;
                        border-radius: 2px; background: #6b6b6b;
                    }
                    #chat-grip .grip-chevron { width: 18px; height: 18px; opacity: .75; }
                    #chat-grip:active .grip-pill,
                    #chat-grip:active .grip-chevron { color: #d4d4d4; }
                    #chat-grip .grip-pill { background: #8a8a8a; }


                    /* The microphone in the composer, beside the send button it
                       matches, so sending a voice message needs no detour to the
                       header. */
                    #voice-btn-chat {
                        flex: 0 0 auto; align-self: flex-end;
                        width: 44px; height: 44px; border-radius: 8px;
                        background: #222; border: 1px solid #333; color: #ccc;
                        display: inline-flex; align-items: center;
                        justify-content: center; cursor: pointer; padding: 0;
                    }
                    #voice-btn-chat:hover { border-color: #555; }
                    #voice-btn-chat.recording { border-color: #ef4444; color: #ef4444; }
                    #voice-btn-chat[disabled] { opacity: 0.4; cursor: not-allowed; }
                }
              </style>
    </head>
    <body>
        <div id="easter-egg-layer"><div id="big-ghost"><svg class='ghost-svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><path d='M5 21v-9a7 7 0 0 1 14 0v9a1.5 1.5 0 0 1-2.5 1.1L14 20.5l-2 1.6-2-1.6-2.5 1.1A1.5 1.5 0 0 1 5 21Z'/><circle cx='9.5' cy='11' r='1.2'/><circle cx='14.5' cy='11' r='1.2'/></svg></div></div>

          <div id="header-strip">
              <div id="brand" onclick="triggerEasterEgg()">
                  <!-- A ROW, on owner instruction: the ghost flush left, then the
                       sky, in the order it is read -- weather, moon, UV, air --
                       and the burger sits at the far right. The indicators used
                       to be in two stacked groups on either side of the logo, so
                       "which is the air and which is the weather" was a puzzle
                       before you could read either.

                       The word mark is hidden on a phone, and that is a
                       consequence rather than a flourish: at 375px there is room
                       for the logo, four indicators and the burger, or for the
                       logo, four indicators and the name, but not both. The ghost
                       already IS the name. -->
                  <div id="brand-row">
                      <div id="brand-logo" class="ghost-normal"></div>
                      <div id="sky-stage">
                          <div class="sky-element" title="Meteorologia">
                              <div id="main-weather-icon"></div>
                              <div id="main-weather-temp">--°</div>
                          </div>
                          <!-- Night only. The moon phase means something when it
                               is dark and nothing when it is not, so it is not
                               shown at noon; JS adds the class. -->
                          <div class="sky-element" id="moon-slot" title="Fase Lunar">
                              <div id="main-moon-icon"></div>
                          </div>
                          <div class="sky-element" id="uv-slot" title="Índice UV">
                              <div id="uv-indicator"></div>
                          </div>
                          <div class="sky-element" title="Qualidade do Ar">
                              <div id="aqi-indicator"></div>
                          </div>
                      </div>
                      <div id="brand-name">pHantasma</div>
                  </div>
              </div>
              <!-- Secondary nav, always visible. Shares .nav-menu styling with
                   the admin pages but has no toggle: on this screen a burger
                   would gate five links and nothing else. -->
               
                   <!-- Always available, outside the burger. -->
                   <div id="devices" aria-label="Dispositivos"></div>
                   <!-- Navigation is built by admin._build_nav_menu() and arrives
                        whole, including its own .nav-bar and the .nav-toggle
                        burger. This page must NOT wrap it: an earlier version did,
                        which produced two elements with id="nav-menu" and left the
                        burger inside the copy that the under-900px CSS hides -- so
                        the menu could not be opened on a phone at all. The burger
                        in /admin and the burger in / are now the same element,
                        built by the same function, and the page-specific controls
                        travel inside the bar via extra_in_bar.

                        And the `</div>` that used to sit HERE, between #brand and
                        #devices, closed #header-strip before either of them
                        existed. Two closing tags in a row with nothing between
                        them, so the browser's parser repaired the document by
                        re-parenting: #devices and the nav bar ended up as
                        SIBLINGS of #header-strip and #main landed at depth -1.

                        Nobody noticed, and that is the part worth recording. A
                        browser does not refuse a malformed document, it invents
                        a well-formed one, so the page rendered -- wrong. The
                        desktop symptom was the burger's panel appearing
                        mid-screen, because `position:absolute; top:100%` had lost
                        the positioned ancestor it was written against. Every
                        Playwright test still passed: they ask questions of the
                        DOM the browser built, and the repaired DOM answers
                        questions perfectly. Counting the tags is the only thing
                        that could have caught it. -->
                   __ADMIN_LINKS__
          </div>



        <div id="main">
            <!-- The drag handle lives INSIDE the panel now, as its first child.
                 It used to be a sibling pinned to the top of the viewport, and
                 that is precisely what put a full-width grey band across the top
                 of the screen on top of the brand row -- the owner reported it
                 as "a barra cinzenta". As an absolute child of the panel it
                 travels with the sheet, so it sits on the panel's own top edge
                 and can never reach the top bar. The tab that opens the chat
                 stays OUTSIDE this element: the panel is `visibility: hidden`
                 when closed, visibility is inherited, and a control inside the
                 thing it controls is invisible with it (measured at 375x667: the
                 tab sat at y=1076 in a 667px viewport). -->
            <button id="chat-grip" type="button" aria-label="Fechar a conversa, ou arrastar para baixo">
                <span class="grip-pill" aria-hidden="true"></span>
                <!-- The chevron is the instruction, not decoration: a bare pill
                     asks to be guessed at, a down arrow says "this goes down". -->
                <svg class="grip-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                     aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            <!-- The chat is a PANEL, not a column, on a phone. The owner asked
                 for the device tiles to own the screen and for the
                 conversation to appear over them only when it is asked for. -->
            <div id="chat-log"></div>
            <div id="help-toggle" onclick="toggleHelp()">Ver Comandos</div>
            <div id="cli-help"><pre id="help-content" style="color:#888; font-size:0.8em; margin:0;">...</pre></div>
            <div id="chat-input-box">
                <textarea id="chat-input" placeholder="Mensagem..." autocomplete="off"></textarea>
                <!-- The microphone belongs IN the conversation, next to the
                     other way of talking to the house. The one in the nav bar
                     stays as a shortcut, but a voice MESSAGE is part of the
                     chat, and a chat whose only voice affordance is two
                     screens away in the header is a chat you cannot speak into
                     while reading it. -->
                <button id="voice-btn-chat" type="button"
                        aria-label="Enviar mensagem de voz" title="Mensagem de voz">
                    <svg class='mic-svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='9' y='2.5' width='6' height='11' rx='3'/><path d='M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7'/></svg>
                </button>
                <button id="chat-send">Enviar</button>
            </div>
        </div>
        <!-- THE AMBIENT LAYER. Rain over the whole screen when it rains, drifting
             gusts when it is windy, a warm wash when it is sunny, and a tint for
             the air and the hour. Decoration, and decoration that can be
             turned off -- see ambientLayer, which respects prefers-reduced-motion
             and pauses when the tab is hidden rather than burning a phone
             battery on drops nobody is looking at. -->
        <canvas id="ambient" aria-hidden="true"></canvas>
        <div id="ambient-tint" aria-hidden="true"></div>
        <!-- THE DOCK. One prominent bar at the bottom of a phone: pull it up (or
             tap it) and the conversation unrolls over the screen; push it down
             and the tiles come back. It replaces the small pill that used to sit
             bottom-right, which was both too easy to miss and sitting on top of
             the microphone -- measured: the element at the centre of the
             microphone was the pill, so the mic could not be tapped.

             The microphone lives HERE, in the dock, not in the navigation bar.
             Two reasons, both measured: the nav bar is a menu that closes, and
             the pill covered the mic. A voice control that is only reachable
             while some other panel is open is not a voice control. -->
        <div id="chat-dock" role="group" aria-label="Conversa e voz">
            <button id="chat-tab" type="button" aria-controls="main"
                    aria-expanded="false" aria-label="Abrir conversa">
                <span id="dock-grip" aria-hidden="true"></span>
                <span id="dock-label">Chat</span>
            </button>
            <button id="voice-btn" type="button" class="nav-voice"
                    aria-label="Falar com o Phantasma" title="Falar com o Phantasma">
                <svg class='mic-svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'><rect x='9' y='2.5' width='6' height='11' rx='3'/><path d='M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7'/></svg>
            </button>
        </div>
        <!-- The grab handle moved INSIDE #main (see the comment there). -->

        <!-- ==========================================================
             THE GHOST AVATAR
             One ghost, ten expressions, geometry frozen.

             The design is borrowed from a system built elsewhere for a
             different character; the artwork is pHantasma's own. What was
             taken is the contract, and the contract is the point:

               * the silhouette is byte-identical in all ten expressions --
                 the body path below is the same `d` the brand ghost has
                 always used, so the character in the chat is provably the
                 same ghost as the one on the bar;
               * only the face varies. Body, halo and glint sit OUTSIDE the
                 face groups and are never switched;
               * an unknown, empty or missing expression renders `normal`.
                 That is CSS, not JavaScript: the ten rules below only ever
                 HIDE normal, so a name none of them matches leaves the
                 default in place. A malformed attribute cannot blank the
                 avatar, and it cannot show two faces, because exactly one
                 rule pair can ever apply;
               * no idle motion: an expression, once painted, holds still.
                 THE ONE EXCEPTION, on owner instruction 2026-10-01: the
                 `thinking` face breathes (brow lift + glint drift, 1.8s) while
                 the assistant is composing a reply, so the avatar carries that
                 state and not only the three dots. It is scoped to
                 `[data-expression="thinking"]`, which only the typing row ever
                 sets, so it cannot run on a delivered message; and it is a
                 transform, so no geometry moves and a still screenshot is
                 indistinguishable from the frozen ghost. Everything else about
                 the artwork is still frozen, and `prefers-reduced-motion`
                 turns this off too;
               * nine `--avatar-*` variables and no hardcoded colours, so
                 the ghost can be re-coloured without touching the markup;
               * legible at 22px (the size the chat uses). No visible text
                 inside the SVG -- the accessible name is an aria-label.

             One definition, many instances: every <ghost-avatar> clones this
             template into its own shadow root, so the markup exists once in
             the document instead of being inlined per message. Shadow DOM
             also means the page's CSS cannot reach the face, which is what
             keeps `.ia-avatar svg { ... }` from quietly re-sizing it.
             ========================================================== -->
        <template id="ghost-avatar-tpl">
        <style>
        :host {
            /* The nine theming variables. Defaults are pHantasma's dark
               theme, which is the only theme there is today; they exist so
               a future light theme does not have to touch the geometry.
               Contrast: --avatar-face and --avatar-body must keep a 3:1
               ratio against --avatar-background, and the face against the
               body fill. At 22px a 1.6 stroke is ~1.5 device px. */
            --avatar-size: 22px;
            --avatar-body: #d6d6d6;
            --avatar-fill: transparent;
            --avatar-face: #d6d6d6;
            --avatar-highlight: #4db6ac;
            --avatar-background: transparent;
            --avatar-shadow: transparent;
            --avatar-opacity: 1;
            --avatar-stroke-width: 1.6;
            display: inline-flex;
            width: var(--avatar-size);
            height: var(--avatar-size);
            flex: 0 0 auto;
            opacity: var(--avatar-opacity);
        }
        :host([hidden]) { display: none; }
        svg { width: 100%; height: 100%; display: block; }
        .ghost-halo { fill: var(--avatar-background); }
        .ghost-body {
            fill: var(--avatar-fill);
            stroke: var(--avatar-body);
            stroke-width: var(--avatar-stroke-width);
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        .ghost-glint {
            fill: none; stroke: var(--avatar-highlight); opacity: .7;
            stroke-width: calc(var(--avatar-stroke-width) * .7);
            stroke-linecap: round;
        }
        .face {
            display: none; fill: none; stroke: var(--avatar-face);
            stroke-width: var(--avatar-stroke-width);
            stroke-linecap: round; stroke-linejoin: round;
        }
        /* normal is the default and is only ever hidden by a rule that
           RECOGNISES the name. Anything else leaves it standing. */
        .face[data-f="normal"] { display: block; }
        :host([data-expression="wink"])      .face[data-f="normal"],
        :host([data-expression="happy"])     .face[data-f="normal"],
        :host([data-expression="thinking"])  .face[data-f="normal"],
        :host([data-expression="surprised"]) .face[data-f="normal"],
        :host([data-expression="confused"])  .face[data-f="normal"],
        :host([data-expression="sleepy"])    .face[data-f="normal"],
        :host([data-expression="excited"])   .face[data-f="normal"],
        :host([data-expression="error"])     .face[data-f="normal"],
        :host([data-expression="loading"])   .face[data-f="normal"] { display: none; }
        :host([data-expression="wink"])      .face[data-f="wink"],
        :host([data-expression="happy"])     .face[data-f="happy"],
        :host([data-expression="thinking"])  .face[data-f="thinking"],
        :host([data-expression="surprised"]) .face[data-f="surprised"],
        :host([data-expression="confused"])  .face[data-f="confused"],
        :host([data-expression="sleepy"])    .face[data-f="sleepy"],
        :host([data-expression="excited"])   .face[data-f="excited"],
        :host([data-expression="error"])     .face[data-f="error"],
        :host([data-expression="loading"])   .face[data-f="loading"] { display: block; }
        /* Motion, and only on the thinking face. See the contract note below:
           the ten expressions are frozen artwork EXCEPT while the assistant is
           actually composing a reply, which the owner asked to be visible in
           the avatar rather than only in the dots.

           Scoped to `[data-expression="thinking"]` on purpose. If the motion
           lived on `.face` or on the host, it would animate every message in
           the chat forever, and the test that used to forbid all motion would
           be right to fail. Here it can only run while an expression named
           thinking is showing -- which is only the typing row -- so it stops
           the moment the row is removed.

           Transforms only, never geometry: no width/height/top/left, so the
           painted box and the silhouette stay exactly where they were. The
           brow lifts by 0.35px, the glint travels 2px, both on a 1.8s ease
           that starts and ends at rest, so a still screenshot looks like the
           un-animated ghost. */
        @keyframes ghostThinking {
            0%, 100% { transform: translateY(0);    opacity: 1;   }
            50%      { transform: translateY(-.35px); opacity: .78; }
        }
        @keyframes ghostGlint {
            0%, 100% { transform: translateX(0);    opacity: .35; }
            50%      { transform: translateX(2px);  opacity: .7;  }
        }
        :host([data-expression="thinking"]) .face[data-f="thinking"] {
            animation: ghostThinking 1.8s ease-in-out infinite;
            transform-box: fill-box;
            transform-origin: center;
        }
        :host([data-expression="thinking"]) .ghost-glint {
            animation: ghostGlint 1.8s ease-in-out infinite;
            transform-box: fill-box;
            transform-origin: center;
        }
        /* A page or OS that asks for less motion gets a thinking face that
           thinks without moving. The expression still changes, so the state is
           still legible without the animation. */
        @media (prefers-reduced-motion: reduce) {
            :host([data-expression="thinking"]) .face[data-f="thinking"],
            :host([data-expression="thinking"]) .ghost-glint {
                animation: none;
            }
        }
        </style>
        <svg viewBox="0 0 24 24" focusable="false">
            <circle class="ghost-halo" cx="12" cy="12" r="11.6"/>
            <!-- FROZEN. This `d` is the brand ghost's, unchanged. -->
            <path class="ghost-body" d="M5 21v-9a7 7 0 0 1 14 0v9a1.5 1.5 0 0 1-2.5 1.1L14 20.5l-2 1.6-2-1.6-2.5 1.1A1.5 1.5 0 0 1 5 21Z"/>
            <path class="ghost-glint" d="M7.7 8.1a5.4 5.4 0 0 1 2.9-1.9"/>
            <g class="face" data-f="normal">
                <circle cx="9.6" cy="11.2" r="1.2"/><circle cx="14.4" cy="11.2" r="1.2"/>
                <path d="M10.9 14.4q1.1 1 2.2 0"/>
            </g>
            <g class="face" data-f="wink">
                <circle cx="9.6" cy="11.2" r="1.2"/>
                <path d="M13.2 11.4q1.2 -1.1 2.4 0"/>
                <path d="M10.9 14.4q1.1 1 2.2 0"/>
            </g>
            <g class="face" data-f="happy">
                <path d="M8.4 11.4q1.2 -1.2 2.4 0"/>
                <path d="M13.2 11.4q1.2 -1.2 2.4 0"/>
                <path d="M9.9 14.2q2.1 1.6 4.2 0"/>
            </g>
            <g class="face" data-f="thinking">
                <circle cx="9.2" cy="10.5" r="1.05"/><circle cx="14.3" cy="10.9" r="1.05"/>
                <path d="M8 8.5q1.5 -0.8 3 0.1"/>
                <path d="M11 15.1h2.1"/>
            </g>
            <g class="face" data-f="surprised">
                <circle cx="9.5" cy="10.9" r="1.45"/><circle cx="14.5" cy="10.9" r="1.45"/>
                <ellipse cx="12" cy="15" rx="1" ry="1.25"/>
            </g>
            <g class="face" data-f="confused">
                <circle cx="9.5" cy="11.2" r="1.2"/><circle cx="14.7" cy="11.9" r=".95"/>
                <path d="M7.9 8.9l2.7 -1"/>
                <path d="M10.2 14.8q.9 -.9 1.8 0t1.8 0"/>
            </g>
            <g class="face" data-f="sleepy">
                <path d="M8.4 10.6q1.2 1.2 2.4 0"/>
                <path d="M13.2 10.6q1.2 1.2 2.4 0"/>
                <path d="M11.2 15h1.7"/>
            </g>
            <g class="face" data-f="excited">
                <circle cx="9.5" cy="10.8" r="1.35"/><circle cx="14.5" cy="10.8" r="1.35"/>
                <path d="M9.2 13.9q2.8 2.4 5.6 0"/>
            </g>
            <g class="face" data-f="error">
                <path d="M8.5 10.1l2 2m0 -2l-2 2"/>
                <path d="M13.5 10.1l2 2m0 -2l-2 2"/>
                <path d="M9.8 15q1.1 -1.1 2.2 0t2.2 0"/>
            </g>
            <g class="face" data-f="loading">
                <path d="M8.5 10.6h2.2"/><path d="M13.3 10.6h2.2"/>
                <path d="M11 15h2"/>
            </g>
        </svg>
        </template>
        <script>
        /* Defined before the chat script below so an element created in the
           first millisecond still upgrades: `customElements.define` upgrades
           existing markup synchronously, and anything created after that
           upgrades on insertion. There is no "waiting for DOMContentLoaded"
           window where the ghost would be blank. */
        (function () {
            const EXPRESSIONS = ['normal','wink','happy','thinking','surprised',
                                 'confused','sleepy','excited','error','loading'];
            class GhostAvatar extends HTMLElement {
                static get observedAttributes() { return ['expression', 'label', 'decorative']; }
                connectedCallback() { this._build(); this._reflect(); }
                attributeChangedCallback() { if (this._built) this._reflect(); }
                _build() {
                    if (this._built) return;
                    const tpl = document.getElementById('ghost-avatar-tpl');
                    if (!tpl) return;   /* no template: renders nothing, does not throw */
                    this.attachShadow({ mode: 'open' });
                    this.shadowRoot.appendChild(tpl.content.cloneNode(true));
                    this._built = true;
                }
                _reflect() {
                    /* The attribute is passed through unvalidated on purpose.
                       Validating here would mean the ALLOWED list lives in two
                       places -- this array and the CSS -- and they would drift.
                       CSS is the single authority for what a name means, and an
                       unrecognised one falls through to normal for free. */
                    const expr = (this.getAttribute('expression') || '').trim();
                    if (expr) this.setAttribute('data-expression', expr);
                    else this.removeAttribute('data-expression');
                    if (this.hasAttribute('decorative')) {
                        this.setAttribute('aria-hidden', 'true');
                        this.removeAttribute('role');
                        this.removeAttribute('aria-label');
                    } else {
                        this.setAttribute('role', 'img');
                        this.setAttribute('aria-label', this.getAttribute('label') || 'pHantasma');
                    }
                }
            }
            customElements.define('ghost-avatar', GhostAvatar);
            /* Exposed for the tests, which assert the list against the CSS
               rather than against a comment. */
            window.__GHOST_EXPRESSIONS__ = EXPRESSIONS;
            window.ghostAvatar = function (expression, label) {
                const el = document.createElement('ghost-avatar');
                el.setAttribute('expression', expression || 'normal');
                if (label) el.setAttribute('label', label);
                else el.setAttribute('decorative', '');
                return el;
            };
        })();
        </script>

        <script>
            const chatLog = document.getElementById('chat-log');
            const chatInput = document.getElementById('chat-input');
            const chatSend = document.getElementById('chat-send');
            // Dispositivos sao montados dentro de #devices, que vive no painel
          // .nav-menu partilhado com o admin. Antes apontava para #topbar.
          const devicesEl = document.getElementById('devices');
            const helpContent = document.getElementById('help-content');
            
            const ALL_DEVICES_ELEMENTS = []; 
            const ROOMS_ORDER = ["Geral", "WC", "Sala", "Quarto", "Entrada"]; 

            function triggerEasterEgg() {
                document.body.classList.add('boo');
                setTimeout(() => { document.body.classList.remove('boo'); }, 1200);
            }

            // --- UI HELPERS ---
            /* Device icons as inline SVG, not emoji.
               Emoji were replaced on owner instruction (2026-09-29), and the
               reasons are not only taste:

               * They render differently on every platform. The same light was a
                 yellow bulb on iOS, a white bulb on Android and a flat glyph on
                 desktop, so the page did not look like itself across devices.
               * They ignore the tile's own `filter: grayscale(100%)`, so a
                 device that is OFF still shouted its colour while the switch
                 beside it said off.
               * A single catch-all `⚡` was the answer for anything unrecognised
                 -- `porta`, `ar`, `camera` all rendered as a lightning bolt, so
                 the icon was actively lying about the device. The fallback here
                 is a plain device outline: honest and neutral.
               * Emoji are one to two font-em wide and shift with the system
                 font, which is why the brand measured 7.7 x 19.2px for a 2.5rem
                 badge. A 24x24 viewBox does not move.

               `currentColor` throughout, so the icon inherits the tile state and
               the grayscale filter applies to it like to the tile. */
            const SVG = {
                luz: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.3 1 2.1h5c0-.8.4-1.6 1-2.1A6 6 0 0 0 12 3Z"/>',
                /* A robot vacuum, not a sun. The first version was a circle with
                   four radial lines, which is the universal sun/brightness
                   glyph -- so the one device that is genuinely round was drawn
                   as the one thing it is not. This is a disc seen from above:
                   body, a bumper line, a sensor turret and a side brush. */
                aspirador: '<circle cx="12" cy="12" r="8.2"/><path d="M3.8 12a8.2 8.2 0 0 0 16.4 0"/><circle cx="12" cy="10.5" r="2.2"/><path d="M12 15.5v2"/><path d="M18.6 17.8l1.6 1.6M5.4 17.8l-1.6 1.6"/>',
                /* The blade path and the droplet carry their own class so CSS can
                   animate them without touching the outline: one rotating hub,
                   one running drop, both transform-only and scoped to
                   `data-state="on"` on the tile. */
                exaustor: '<circle cx="12" cy="12" r="2.5"/><path class="tile-icon-fan" d="M12 9.5c0-4 1-6 4-6-1 3-1 6-4 6ZM14.5 12c4 0 6 1 6 4-3-1-6-1-6-4ZM12 14.5c0 4-1 6-4 6 1-3 1-6 4-6ZM9.5 12c-4 0-6-1-6-4 3 1 6 1 6 4Z"/>',
                desumidificador: '<path class="tile-icon-drop" d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z"/>',
                gas: '<path d="M12 2c2 4-1 5 1 8 1.5 2.5 4 3 4 6a5 5 0 0 1-10 0c0-2 1-3 1-5 1 1.5 2 1.5 2 0 .5-3-1-5 2-9Z"/>',
                carro: '<path d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11v6h-2v-2H7v2H5v-6Z"/><circle cx="7.5" cy="13" r="1"/><circle cx="16.5" cy="13" r="1"/>',
                forno: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/><rect x="7" y="12" width="10" height="5" rx="1"/>',
                tomada: '<path d="M7 3v6M17 3v6"/><rect x="4" y="9" width="16" height="12" rx="3"/><path d="M10 14h4"/>',
                sensor: '<path d="M12 3v3M5.6 5.6l2.1 2.1M18.4 5.6l-2.1 2.1"/><circle cx="12" cy="12" r="2.5"/><path d="M3.5 12a8.5 8.5 0 0 1 17 0M6.5 15.5a5.5 5.5 0 0 1 11 0"/>',
                /* The honest answer for anything unrecognised. */
                dispositivo: '<rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M9 7h6M10 17h4"/>',
                /* Rooms, so a group of tiles is a place and not just a word.
                   A word alone in a 14px header is not something a thumb reads
                   while hunting for the light. */
                _sala: '<path d="M4 20V9l8-6 8 6v11"/><path d="M9 20v-7h6v7"/>',
                _wc: '<path d="M5 12a7 7 0 0 1 14 0v3a3 3 0 0 1-3 3h-1l-1 3h-4l-1-3H8a3 3 0 0 1-3-3v-3Z"/><path d="M8 6.5h.01M16 6.5h.01"/>',
                _quarto: '<path d="M3 18v-8h18v8"/><path d="M3 14h18"/><path d="M6 10V7h5v3"/><path d="M3 18v2M21 18v2"/>',
                _entrada: '<path d="M4 21V5a1 1 0 0 1 1-1h6v17"/><path d="M14 21V6h5a1 1 0 0 1 1 1v14"/><path d="M11 12h.01"/>',
            };
            function roomIcon(room) {
                const map = {Geral: '_sala', Sala: '_sala', Quarto: '_quarto',
                             WC: '_wc', Entrada: '_entrada'};
                const kind = map[room] || '_sala';
                return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                     + 'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
                     + 'aria-hidden="true" focusable="false">' + SVG[kind] + '</svg>';
            }
            /* Weather and moon, as SVG. Same reasons as the device icons: they
               rendered differently per platform, and `innerText` on an emoji is
               the only reason the moon ever appeared at all. */
            const _wrap = (inner) => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>';
            /* UV, as a sun with rays that shrink with the index: a number in a
               circle reads as a badge nobody learns. A sun whose ray count and
               stroke mean something is a picture you get at a glance. */
            const _uvSvg = (uv) => {
                const v = Math.max(0, Math.min(11, Math.round(Number(uv) || 0)));
                const rays = ['', 'M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2',
                              'M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6',
                              'M3.4 3.4l2 2M18.6 18.6l2 2M20.6 3.4l-2 2M5.4 20.6l-2-2'];
                const body = rays[Math.min(3, Math.floor(v / 3))];
                return _wrap('<circle cx="12" cy="12" r="3.6"/>' + body +
                    '<path d="M12 8.4v7.2M8.4 12h7.2" opacity="0"/>');
            };
            function uvAdvice(uv) {
                const v = Number(uv);
                if (isNaN(v)) return 'desconhecido';
                if (v < 3) return 'Baixo';
                if (v < 6) return 'Moderado';
                if (v < 8) return 'Alto';
                if (v < 11) return 'Muito alto';
                return 'Extremo';
            }
            const AQI = {
                good: _wrap('<path d="M12 3c5 3 7 7 7 10a7 7 0 0 1-14 0c0-3 2-7 7-10Z"/><path d="M12 20V9"/>'),
                moderate: _wrap('<circle cx="12" cy="12" r="8"/><path d="M8 14h.01M16 14h.01M9 17h.01M15 17h.01"/>'),
                bad: _wrap('<circle cx="12" cy="12" r="8"/><path d="M9 10h.01M15 10h.01"/><path d="M8.5 15.5c2 2 5 2 7 0"/>'),
            };
            const WEATHER = {
                sun: _wrap('<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"/>'),
                partly: _wrap('<circle cx="9" cy="9" r="3.2"/><path d="M9 2.6v1.8M2.6 9h1.8M4.6 4.6l1.3 1.3"/><path d="M8 20h9a3.5 3.5 0 0 0 0-7 5 5 0 0 0-9.5 1.5A2.8 2.8 0 0 0 8 20Z"/>'),
                rain: _wrap('<path d="M7 15a4 4 0 0 1 .6-7.96A5.5 5.5 0 0 1 18.4 6.2 3.9 3.9 0 0 1 17 15H7Z"/><path d="M9 18l-1 3M13 18l-1 3M17 18l-1 3"/>'),
                fog: _wrap('<path d="M4 10h16M6 14h12M4 18h16"/>'),
            };
            const MOON = {
                new: _wrap('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16Z" fill="currentColor" stroke="none"/>'),
                crescent: _wrap('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16 10 10 0 0 1 0-16Z" fill="currentColor" stroke="none"/>'),
                full: _wrap('<circle cx="12" cy="12" r="8" fill="currentColor" stroke="none"/>'),
                waning: _wrap('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16 10 10 0 0 0 0-16Z" fill="currentColor" stroke="none"/>'),
            };
            const GHOST_SVG = '<svg class="ghost-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 21v-9a7 7 0 0 1 14 0v9a1.5 1.5 0 0 1-2.5 1.1L14 20.5l-2 1.6-2-1.6-2.5 1.1A1.5 1.5 0 0 1 5 21Z"/><circle cx="9.5" cy="11" r="1.2"/><circle cx="14.5" cy="11" r="1.2"/></svg>';
            const CLOUD_SVG = '<svg class="ghost-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 18a4 4 0 0 1 .6-7.96A5.5 5.5 0 0 1 18.4 9.2 3.9 3.9 0 0 1 17 18H7Z"/></svg>';

            /* The brand and the sky icon are filled here rather than written as
               literal markup. A JavaScript string of SVG cannot be inlined into
               this Python string literal without either quote-escaping -- which
               is exactly how the ghost ended up as stroke="&apos;currentColor"
               and drew NOTHING, invisibly, for a release -- or entity-escaping,
               which is the same bug. Empty markup, one injection point. */
            const _brandEl = document.getElementById('brand-logo');
            if (_brandEl) _brandEl.innerHTML = GHOST_SVG;
            const _skyEl = document.getElementById('main-weather-icon');
            if (_skyEl) _skyEl.innerHTML = CLOUD_SVG;

            function icon(kind) {
                /* The kind is a class, so a single icon can be recoloured without
                   a parallel data attribute: the lit-bulb fill targets
                   `svg.dev-icon-luz` and nothing else needs to know the kind. */
                return '<svg class="dev-icon dev-icon-' + kind + '" viewBox="0 0 24 24" '
                     + 'fill="none" stroke="currentColor" '
                     + 'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
                     + 'aria-hidden="true" focusable="false">' + SVG[kind] + '</svg>';
            }
            function getDeviceIcon(name) {
                const n = (name || '').toLowerCase();
                if (n.includes('aspirador') || n.includes('robot') || n.includes('aspir')) return icon('aspirador');
                if (n.includes('luz') || n.includes('candeeiro') || n.includes('lamp')) return icon('luz');
                if (n.includes('exaustor') || n.includes('ventoinha') || n.includes('ventilador')) return icon('exaustor');
                if (n.includes('desumidificador') || n.includes('humidific')) return icon('desumidificador');
                if (n.includes('gás') || n.includes('gas') || n.includes('fumo') || n.includes('dete')) return icon('gas');
                if (n.includes('carro') || n.includes('carrinha') || n.includes('veículo') || n.includes('carregador')) return icon('carro');
                if (n.includes('forno') || n.includes('oven')) return icon('forno');
                if (n.includes('tomada') || n.includes('ficha') || n.includes('chuveiro') || n.includes('termo')) return icon('tomada');
                if (n.includes('sensor') || n.includes('temperatura') || n.includes('humidade')) return icon('sensor');
                return icon('dispositivo');
            }

            function getRoomName(name) {
                const n = name.toLowerCase();
                if (n.includes("wc") || n.includes("banho")) return "WC";
                // "luz do balcao" carries no room word of its own, so it fell
                // through to "Geral" and the light looked misplaced. The balcony
                // belongs to the Sala group in the layout, so name it here
                // instead of renaming the device.
                if (n.includes("balcao") || n.includes("balcão")) return "Sala";
                if (n.includes("sala")) return "Sala";
                if (n.includes("quarto")) return "Quarto";
                if (n.includes("entrada") || n.includes("corredor")) return "Entrada";
                return "Geral";
            }
            function getOrCreateRoomContainer(room) {
                let roomContainer = document.getElementById(`room-content-${room}`);
                if (roomContainer) return roomContainer;
                const roomWrapper = document.createElement('div'); roomWrapper.className = 'device-room';
                /* The header carries an icon, so a group of tiles reads as a
                   place. innerHTML because it is markup, and the name goes in a
                   sibling so it is still selectable text and still announced.
                   The readings span is the slot the sensors write into -- see
                   createSensor -- so the numbers sit beside the name that gives
                   them meaning instead of in a tile of their own. */
                const header = document.createElement('div'); header.className = 'room-header';
                header.innerHTML = roomIcon(room) + '<span class="room-name">' + room + '</span>'
                                 + '<span class="room-readings" data-empty="1"></span>';
                header.title = room;
                roomContainer = document.createElement('div'); roomContainer.className = 'room-content'; roomContainer.id = `room-content-${room}`;
                roomWrapper.append(header, roomContainer); devicesEl.appendChild(roomWrapper);
                return roomContainer;
            }
            /* The slot a room's readings are written into. Found through the
               container rather than kept in a side table, because a room
               container can be created by a switch first and claimed by a
               sensor afterwards -- a second `getElementById` on the header is
               cheap, and a stale reference in a Map is not something a test
               would catch. Returns null rather than creating one, so a missing
               header is a visible no-op instead of a second readings element
               somewhere else. */
            function getRoomReadings(container) {
                if (!container) return null;
                const header = container.parentElement && container.parentElement.querySelector('.room-readings');
                return header || null;
            }

            function showTypingIndicator() {
                if (document.getElementById('typing-indicator-row')) return;
                const row = document.createElement('div'); row.id = 'typing-indicator-row'; row.className = 'msg-row ia'; 
                /* thinking, and labelled: in this row the ghost is the only
                   thing saying "I am working", so it needs a name. The dots
                   stay -- removing them is a design call, not this ticket. */
                const avatar = document.createElement('div'); avatar.className = 'ia-avatar';
                avatar.appendChild(ghostAvatar('thinking', 'pHantasma está a pensar'));
                const bubble = document.createElement('div'); bubble.className = 'typing-indicator'; 
                bubble.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                row.append(avatar, bubble); chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }
            function removeTypingIndicator() { const row = document.getElementById('typing-indicator-row'); if (row) row.remove(); }

            // --- FlyBrain reactions -------------------------------------
            // The reward map is fetched from /api/reactions so the buttons and
            // the training behaviour cannot drift: an emoji added server-side
            // appears here with no frontend change.
            //
            // Declared BEFORE addToChatLog uses it. An earlier version of this
            // referenced reactionEmojis from inside addToChatLog without ever
            // declaring it, which threw a ReferenceError on the FIRST message:
            // the user's own text rendered and the assistant's reply never did.
            // A chat that silently drops half its messages is worse than one
            // that visibly fails.
            let reactionEmojis = [];
            async function loadReactions() {
              try {
                const r = await fetch('/api/reactions');
                const d = await r.json();
                reactionEmojis = (d.emojis || []).map(e => e.emoji);
                // Backfill bars drawn while this fetch was in flight. The first
                // assistant message is usually rendered BEFORE the map arrives,
                // so without this it would keep an empty bar forever -- which is
                // what made the reactions look broken while the API was fine.
                document.querySelectorAll('.react-bar').forEach(bar => {
                  if (bar.querySelector('.react-btn')) return;
                  const fresh = reactionBar();
                  bar.innerHTML = fresh.innerHTML;
                });
              } catch (e) { reactionEmojis = []; }
            }
            loadReactions();
            wireReactionDelegation();

            // One delegated listener on #chat-log, attached once. It survives
            // bars rendered later and re-rendered again when the map arrives --
            // a listener bound to a button that gets replaced dies with it, which
            // is why the reactions were visible but did nothing.
            let msgSeq = 0;
            function wireReactionDelegation() {
              const log = document.getElementById('chat-log');
              if (!log || log.dataset.reactWired) return;
              log.dataset.reactWired = '1';
              // Long-press reveal for touch. Attached here, once, on the same
              // element as the click delegation: a per-message listener would
              // die every time the /api/reactions backfill rebuilds a bar (R6).
              // Gated on the media query, NOT on a user-agent string (R5).
              const coarse = matchMedia('(pointer: coarse)');
              const HOLD_MS = 600, MOVE_TOLERANCE = 10;
              let holdTimer = null, holdStart = null, holdRow = null;
              function cancelHold() {
                if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
                holdStart = null; holdRow = null;
              }
              log.addEventListener('touchstart', ev => {
                if (!coarse.matches) return;                 // desktop: hover rules
                const row = ev.target.closest('.msg-row');
                // Only the assistant's own replies are reactable, and only
                // starting on the text, never on a button.
                if (!row || row.classList.contains('user')) return;
                if (ev.target.closest('.react-btn')) return;
                const t = ev.changedTouches[0];
                holdRow = row; holdStart = {x: t.clientX, y: t.clientY};
                holdTimer = setTimeout(() => {
                  if (holdRow) holdRow.classList.add('react-revealed');
                  holdTimer = null;
                }, HOLD_MS);
              }, {passive: true});
              log.addEventListener('touchmove', ev => {
                // A press that turns into a scroll must never reveal the bar (R4).
                if (!holdStart) return;
                const t = ev.changedTouches[0];
                if (Math.abs(t.clientX - holdStart.x) > MOVE_TOLERANCE
                 || Math.abs(t.clientY - holdStart.y) > MOVE_TOLERANCE) cancelHold();
              }, {passive: true});
              ['touchend','touchcancel'].forEach(t =>
                log.addEventListener(t, cancelHold, {passive: true}));
              // Tapping elsewhere closes a revealed bar: one open at a time, and
              // the bar is a transient affordance, not a pinned panel.
              log.addEventListener('click', ev => {
                if (ev.target.closest('.react-btn')) return;
                const open = log.querySelector('.msg-row.react-revealed');
                if (open && !ev.target.closest('.msg-row')) {
                  open.classList.remove('react-revealed');
                }
              });
              log.addEventListener('pointerdown', ev => {
                if (ev.pointerType === 'touch' || !ev.target.closest('.react-btn')) return;
                const row = ev.target.closest('.msg-row');
                if (row) row.classList.add('react-revealed');
              }, {passive: true});
              log.addEventListener('click', ev => {
                const btn = ev.target.closest('.react-btn');
                if (!btn) return;
                const row = btn.closest('.msg-row');
                const msg = row && row.querySelector('.msg');
                if (msg) sendReaction(btn.textContent, msg, btn);
              });
            }
            // The backend does real work and says what it did: the reply carries
            // `flybrain: "stepped"`, `graph: "rewarded"`, the reward and the topic
            // it touched. This used to discard the whole body and only toggle a
            // transient class, so reacting looked like it did nothing -- the
            // request succeeded and the screen said nothing at all. The outcome
            // is now shown on the message it belongs to.
            function reactionNote(msgEl, text, isError) {
              let note = msgEl.parentElement.querySelector('.react-note');
              if (!note) {
                note = document.createElement('div');
                note.className = 'react-note';
                msgEl.parentElement.appendChild(note);
              }
              note.textContent = text;
              note.classList.toggle('is-error', !!isError);
            }

            function reactionReport(d) {
              const bits = [(d.reward > 0 ? '+' : '') + Number(d.reward).toFixed(2)];
              if (d.flybrain) bits.push('FlyBrain: ' + d.flybrain);
              // "no_match" is not jargon to show a user: it means the reply
              // talked about nothing the graph has a node for, so the graph was
              // deliberately left alone. Saying so is the whole point -- the
              // previous behaviour credited an unrelated node and looked fine.
              if (d.graph === 'no_match') {
                bits.push('sem no no grafo; a resposta fica para o sono decidir');
              } else if (d.graph === 'topic_fallback') {
                bits.push('grafo: no do topico actual (a resposta nao foi usada)');
              } else if (d.graph) {
                bits.push('grafo: ' + d.graph + (d.graph_node ? ' (' + d.graph_node + ')' : ''));
              }
              if (d.topic_key) bits.push(d.topic_key);
              if (!d.applied) bits.push('nao aplicada');
              return bits.join(' \u00b7 ');
            }


            async function sendReaction(emoji, msgEl, btn) {
              msgEl.classList.add('reacted');
              try {
                const r = await fetch('/api/reaction', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({emoji: emoji, source: 'web',
                                        // message_text travels with the reaction: the graph reward must
                                        // resolve the node THIS reply is about. Without it the backend fell
                                        // back to the ambient topic, so every reaction reinforced whatever
                                        // node happened to be current -- a thumbs-up on an answer about
                                        // mortality reported node:capitalismo tardio.
                                        message_id: msgEl.dataset.mid || '',
                                        message_text: (msgEl.innerText || '').slice(0, 2000)})
                });
                const d = await r.json();
                if (!d.ok) {
                  msgEl.classList.remove('reacted');
                  reactionNote(msgEl, 'Reacao nao aplicada: ' + (d.error || d.message || 'recusada'), true);
                  return;
                }
                // The chosen reaction is marked, so it is visible WHICH one was
                // given instead of all six looking identical.
                if (btn) {
                  msgEl.parentElement.querySelectorAll('.react-btn.is-chosen')
                    .forEach(b => b.classList.remove('is-chosen'));
                  btn.classList.add('is-chosen');
                }
                reactionNote(msgEl, emoji + ' ' + reactionReport(d), false);
                /* No button here any more. When a reply named no node, this
                   used to offer "Guardar esta resposta como nó do grafo" and
                   wait for the owner to press it -- so the common case, a good
                   answer the graph had never heard of, ended in a control they
                   had to notice. The reply is queued instead (the backend's
                   `queued`), and the sleep cycle decides where it belongs with
                   the whole graph open. Where a reply lands is the brain's
                   housekeeping, and it is not worth the owner's attention. */
              } catch (e) {
                msgEl.classList.remove('reacted');
                reactionNote(msgEl, 'Erro de rede: ' + e, true);
              }
            }

            function reactionBar() {
              const bar = document.createElement('div'); bar.className = 'react-bar';
              for (const e of reactionEmojis) {
                const b = document.createElement('button');
                b.type = 'button'; b.className = 'react-btn';
                b.textContent = e;
                b.title = e + ' — ensina o cérebro';
                b.setAttribute('aria-label', 'Reagir com ' + e);
                bar.appendChild(b);
              }
              return bar;
            }


            function addToChatLog(text, sender = 'ia', expression = 'normal') {
                removeTypingIndicator(); 
                const row = document.createElement('div'); row.className = `msg-row ${sender}`;
                /* The expression is a parameter, not a lookup: a caller that
                   knows the house failed passes 'error', and everyone else gets
                   normal. The ghost reports the state the row is about, which
                   is the only reason it varies at all. */
                if (sender === 'ia') { const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.appendChild(ghostAvatar(expression)); row.appendChild(avatar); }
                const msgDiv = document.createElement('div'); msgDiv.className = `msg msg-${sender}`;
                // Stable per-message id. Without it `message_id` was always the
                // empty string, so the endpoint's 30s rate limit keyed on
                // (actor, emoji, '') and refused a second reaction -- even on a
                // different message -- as "already reacted to this message".
                msgDiv.dataset.mid = msgDiv.dataset.mid ||
                  ('m' + Date.now().toString(36) + (msgSeq++).toString(36));
                msgDiv.innerText = text;
                row.appendChild(msgDiv);
                // Reactions attach to assistant messages only: reacting to your own
                // prompt is not feedback on anything.
                if (sender === 'ia') {
                    const holder = document.createElement('div'); holder.className = 'react-holder';
                    const bar = reactionBar();
                    bar.dataset.mid = msgDiv.dataset.mid || '';
                    holder.appendChild(bar);
                    row.appendChild(holder);
                }
                chatLog.appendChild(row); chatLog.scrollTop = chatLog.scrollHeight;
            }

            async function sendChatCommand() {
                const prompt = chatInput.value.trim(); if (!prompt) return;
                addToChatLog(prompt, 'user'); chatInput.value = '';
                /* Back to the resting height, which is the CSS min-height.
                   It was pinned to '24px' here as well, so after the first
                   message the field shrank to the clipped size and stayed
                   there for the rest of the session. */
                chatInput.style.height = ''; 
                showTypingIndicator(); 
                try {
                    const res = await fetch('/comando', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt}) });
                    const data = await res.json(); 
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); addToChatLog('Erro rede.', 'ia', 'error'); }
            }

            async function handleDeviceAction(device, action, tile) {
                /* Does NOT open the conversation, on owner instruction: switching
                   a light is not a request to read. The old code called
                   openChat() here, before the request, with a comment saying a
                   reply into a closed panel is a reply nobody reads.

                   That reasoning does not survive contact with the endpoint. On
                   ANY failure -- 400, 500, 502 -- /device_action answers with
                   `{"status":"error","message":...}` and NO `response` key, and
                   the front end only ever looked at `data.response`. So the
                   panel used to slam open, the typing indicator appeared and
                   vanished, and the failure was dropped on the floor. Measured,
                   both ways, with the action stubbed:

                       success  -> chat opened, "Luz da Sala ligada." in the log
                       502      -> chat opened, log byte-for-byte unchanged

                   Opening the chat was not delivering the failure. It was only
                   interrupting. The reply still goes to the log, in the
                   background, and the log is the transcript -- the conversation
                   is there when you go and read it, which is the point of a
                   transcript.

                   What does NOT happen in the background is a failure going
                   unnoticed, because the eye is on the tile that was just
                   touched, not on a panel. So the error is written to the log
                   AND shown on the tile, and the switch goes back to the state
                   the device is really in rather than holding the state we
                   optimistically claimed for it. */
                showTypingIndicator();
                try {
                    const res = await fetch('/device_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device, action}) });
                    const data = await res.json();
                    removeTypingIndicator();
                    if (data.response) {
                        addToChatLog(data.response, 'ia');
                        markTileResult(tile, true, data.response);
                    } else {
                        /* The branch that used not exist. `data.response` being
                           absent was treated as "nothing to say" and the
                           backend's own explanation was thrown away. */
                        const why = data.message || data.status || `HTTP ${res.status}`;
                        addToChatLog(`Não foi possível ${action} ${device}: ${why}`, 'ia', 'error');
                        markTileResult(tile, false, why);
                    }
                } catch (e) {
                    removeTypingIndicator();
                    const why = (e && e.message) ? e.message : 'erro de rede';
                    addToChatLog(`Não foi possível ${action} ${device}: ${why}`, 'ia', 'error');
                    markTileResult(tile, false, why);
                }
            }
            /* The result of an action, on the tile that was touched. Success
               clears the mark; failure keeps it until the next poll brings the
               truth, and puts the reason in the title, which is where a phone
               reads it. The switch is returned to what the device is really
               doing: the front end set it optimistically the instant it was
               tapped, and for up to five seconds that was a lie the size of a
               light that is not on. */
            function markTileResult(tile, ok, why) {
                if (!tile) return;
                const input = tile.querySelector('input[type=checkbox]');
                tile.classList.toggle('action-failed', !ok);
                /* `title` is the device's real name, put there at creation, and
                   it is the only place a phone can show a reason. Appending the
                   reason rather than replacing the name keeps both. */
                const name = tile.getAttribute('data-device-name') || tile.title.split(' — ')[0];
                tile.setAttribute('data-device-name', name);
                if (ok) {
                    tile.removeAttribute('data-action-why');
                    tile.title = name;
                    return;
                }
                tile.setAttribute('data-action-why', why);
                tile.title = `${name} — ${why}`;
                if (input) {
                    input.checked = !input.checked;
                    tile.classList.toggle('active', input.checked);
                    tile.dataset.state = input.checked ? 'on' : 'off';
                }
            }

            /* What a tile actually says: the device's full name.

               It used to be `device.split(' ').pop().substring(0,12)`, which
               threw away everything but the LAST word -- measured at 375x667
               with the real device list, "Luz da Sala" and "Exaustor da Sala"
               both read "Sala". A middle version shortened a name by the room
               word the header already carries, so "Luz da Sala" became "Luz".
               The owner's 2026-10-01 report is that this left many tiles that
               only said "luz". The full name is the label again, and a name
               wider than its tile scrolls (applyLabelMarquee) rather than being
               cut down to a word.

               The text is written once, here, and never again: both the builder
               and the poller show `device`, so they cannot disagree about what
               a tile is called. */
            function createToggle(device) {
                const container = getOrCreateRoomContainer(getRoomName(device));
                const div = document.createElement('div'); div.className = 'device-toggle'; div.title = device;
                div.dataset.state = 'unreachable'; div.dataset.type = 'toggle';
                const icon = document.createElement('span'); icon.className = 'device-icon';
                /* innerHTML, not innerText. The icon is markup now, and with
                   innerText the browser renders the <svg> source as visible text
                   inside the tile. Nothing warns you about that: the string is
                   valid, the element exists, and the badge simply shows code. */
                icon.innerHTML = getDeviceIcon(device);
                const switchLabel = document.createElement('label'); switchLabel.className = 'switch';
                const input = document.createElement('input'); input.type = 'checkbox'; input.disabled = true;
                input.onchange = () => {
                    handleDeviceAction(device, input.checked ? 'ligar' : 'desligar', div);
                    div.dataset.state = input.checked ? 'on' : 'off';
                    if(input.checked) div.classList.add('active'); else div.classList.remove('active');
                };
                const slider = document.createElement('div'); slider.className = 'slider'; switchLabel.append(input, slider);
                /* Two elements, because a box cannot clip and scroll itself:
                   `.device-label` is the fixed-width window and
                   `.device-label-text` is the mover its animation translates.
                   Keeping them apart also keeps textContent == the full name,
                   which is what the title and the tests read. */
                const label = document.createElement('span'); label.className = 'device-label';
                const labelTextEl = document.createElement('span'); labelTextEl.className = 'device-label-text';
                labelTextEl.textContent = device;
                label.appendChild(labelTextEl);
                /* Watts get their own line instead of overwriting the name. The
                   label used to become "210 W" whenever the device drew power,
                   which meant the desumidifier -- the single most expensive thing
                   in the house -- was the one tile that never said what it was.
                   A reading is not a name, and a name that changes with the
                   load cannot be scanned. */
                const power = document.createElement('span'); power.className = 'device-power';
                power.setAttribute('data-empty', '1');
                div.append(icon, switchLabel, label, power); container.appendChild(div);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'toggle', element: div, input: input, label: label, labelTextEl: labelTextEl, power: power });
            }

            /* The full name gets the space it needs, in motion.

               A name wider than the tile used to be shortened to a word ("Luz"),
               which is the owner's complaint, and a 3-line clamp cannot show a
               single word wider than the tile at all. So the label is one line,
               and only when the text is genuinely wider than its window does it
               drift -- with a pause at each end so the first word is readable
               before the text moves. The overflow is measured, not assumed: a
               short name like "forno" never animates.

               `matchMedia` is read once. Reduced motion is not "scroll slower"
               -- a name that moves is the thing the preference is about, so the
               fallback wraps the text to a second line instead of running it. */
            const PREFERS_REDUCED_MOTION = !!(window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

            function applyLabelMarquee(label, textEl) {
                if (!label || !textEl) return;
                label.classList.remove('marquee', 'wrap');
                label.style.removeProperty('--marquee-shift');
                label.style.removeProperty('--marquee-duration');
                const overflow = textEl.offsetWidth - label.clientWidth;
                if (overflow <= 2) return;
                if (PREFERS_REDUCED_MOTION) { label.classList.add('wrap'); return; }
                label.style.setProperty('--marquee-shift', `-${overflow}px`);
                /* ~25px a second, floored so a barely-overflowing name is not a
                   blur. The distance is measured in px, so duration and
                   distance stay proportional at any tile width. */
                label.style.setProperty('--marquee-duration',
                                        `${Math.max(6, Math.round(overflow / 25) + 4)}s`);
                label.classList.add('marquee');
            }
            function applyAllLabelMarquees() {
                ALL_DEVICES_ELEMENTS.forEach(i => {
                    if (i.type === 'toggle') applyLabelMarquee(i.label, i.labelTextEl);
                });
            }
            
            /* Temperature and humidity have no tile of their own any more.

               They were three of the fourteen devices, and each one was a whole
               tile whose entire content was a number -- "21.4° · 48%" -- next to
               the room it was standing in. The room header already says which
               room that is. So the tile spent a tile's worth of screen to repeat
               the location and show two numbers, and the owner was right that
               the space was wasted.

               The readings move into the room header, beside the name, which is
               where they answer the question the header is already asking. The
               device keeps its own status entry, so the polling, the staleness
               and the per-device request are unchanged -- only the rendering
               moved. A room with a sensor and no switch now still gets its
               header, which is the point: "WC 18.1° · 62%" instead of a tile. */
            function createSensor(device) {
                if(device.toLowerCase().includes('casa') || device.toLowerCase() === 'geral') return;
                const room = getRoomName(device);
                const container = getOrCreateRoomContainer(room);
                const readings = getRoomReadings(container);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'sensor', room: room, readings: readings, dataSpan: readings });
            }

            async function fetchDeviceStatus(item) {
                const { name, element, input, label, labelTextEl, power } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    const isOn = data.state === 'on';
                    if (element.dataset.state !== data.state) {
                        input.checked = isOn;
                        if (isOn) element.classList.add('active'); else element.classList.remove('active');
                        element.dataset.state = data.state;
                    }
                    element.style.opacity = data.state === 'unreachable' ? 0.3 : 1;
                    input.disabled = false; element.classList.add('loaded');
                    /* A healthy poll supersedes the last failed action. The red
                       mark means "the last thing I asked for did not happen", and
                       a device that has come back and is reporting its state is
                       the newer truth. Without this the mark outlives the fault
                       it was reporting: the owner fixes the switch, the tile goes
                       green, and it is still wearing the failure. */
                    if (data.state !== 'unreachable' && element.classList.contains('action-failed')) {
                        markTileResult(element, true, '');
                    }
                    /* The name never depends on the reading. It used to be
                       recomputed on every poll from a second expression, so a
                       light that stopped drawing power lost "Luz da Sala" and
                       went back to reading "Sala": the tile's name changed
                       depending on whether it happened to be on. Anything
                       conditional about a label is a label that lies. The text
                       is already `name` from createToggle; rewritten here from
                       the same variable so there is still one source. */
                    if (labelTextEl) labelTextEl.textContent = name;
                    label.style.color = "#aaa";
                    /* Watts on their own line. Overwriting the name with the
                       wattage meant the desumidifier -- the biggest load in the
                       house, the one you most want to identify at a glance --
                       was the one tile that never said what it was. */
                    if (data.power_w > 0.5 && power) {
                        power.innerText = `${Math.round(data.power_w)} W`;
                        power.removeAttribute('data-empty');
                    } else if (power) {
                        power.innerText = '';
                        power.setAttribute('data-empty', '1');
                    }
                } catch (e) {}
            }
            
            /* Render every sensor of a room into the one readings slot, rather
               than each sensor into a tile. A room can have more than one
               sensor, so each device writes its own record and the header is
               re-rendered from all of them: a room with two sensors must show
               both, and two writers cannot share one text node without the
               second overwriting the first. The slot is one place on screen and
               the request per device is unchanged.

               Two maps, keyed by room and by sensor name, rather than one map
               with keys glued together as "Sala:parts:Sensor da Sala". Room
               names come from device names, so a device called anything with a
               colon in it would collide with another device's slot, and the
               symptom would be one room's temperature showing another room's. */
            const ROOM_SLOTS = new Map();     /* room -> the span */
            const ROOM_PARTS = new Map();     /* room -> Map(sensor -> text) */

            /* room -> last temperature (°C), one entry per room. Kept apart
               from ROOM_PARTS because the average needs the numbers, not the
               formatted text, and because a reading that carries temperature
               AND humidity must contribute its temperature once. */
            const ROOM_TEMPS = new Map();

            /* The average of every room currently reporting a temperature, or
               null when none is. Rooms with no sensor (Entrada) contribute
               nothing; a sensor that has gone unreachable is deleted in
               fetchSensorStatus, so a stale number cannot keep the average
               alive. */
            function averageRoomTemperature() {
                const vals = [...ROOM_TEMPS.values()].filter(v => Number.isFinite(v));
                if (!vals.length) return null;
                return (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1);
            }

            /* A room sensor changed the set of temperatures, but the average
               lives in the Geral header, which that sensor does not write to.
               Re-render it so the number tracks the rooms instead of waiting
               for the next gas poll. */
            function refreshGeralAverage() {
                if (ROOM_SLOTS.has('Geral')) renderRoomReadings('Geral');
            }

            function renderRoomReadings(room) {
                const el = ROOM_SLOTS.get(room);
                if (!el) return;
                const parts = ROOM_PARTS.get(room);
                const values = parts ? [...parts.values()].filter(Boolean) : [];
                /* "Geral" has no sensor of its own: it is where the gas meter
                   and anything without a room land. The average of the rooms
                   belongs there, before the gas reading, so the header answers
                   "how is the house" before it answers "is there gas". */
                const avg = room === 'Geral' ? averageRoomTemperature() : null;
                const txt = (avg !== null ? ['média ' + avg + '°'] : [])
                    .concat(values).join(' · ');
                el.innerText = txt;
                /* The attribute, not an empty string: a header that has never
                   had a reading must not reserve the space for one, and this is
                   what the CSS keys off. */
                if (txt) { el.removeAttribute('data-empty'); }
                else { el.setAttribute('data-empty', '1'); }
            }
            function putRoomReading(room, el, sensor, text) {
                ROOM_SLOTS.set(room, el);
                if (!ROOM_PARTS.has(room)) ROOM_PARTS.set(room, new Map());
                ROOM_PARTS.get(room).set(sensor, text);
                renderRoomReadings(room);
            }

            async function fetchSensorStatus(item) {
                const { name, room, readings } = item;
                if (!readings) return;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    if (data.state === 'unreachable') {
                        ROOM_TEMPS.delete(room);
                        putRoomReading(room, readings, name, 'indisponível');
                        refreshGeralAverage();
                        readings.style.color = '#737373';
                        readings.style.opacity = .6;
                        readings.title = name + ' — indisponível';
                        return;
                    }
                    let measurements = [];
                    let color = '#4db6ac';
                    if (data.power_w !== undefined) {
                        measurements.push(Math.round(data.power_w) + ' W');
                        color = '#ffb74d';
                    }
                    if (data.temperature !== undefined) {
                        measurements.push(data.temperature + '°');
                        /* Room sensors are the only source of the Geral
                           average: the gas meter reports ppm, not degrees. */
                        ROOM_TEMPS.set(room, Number(data.temperature));
                    }
                    if (data.humidity !== undefined) measurements.push(data.humidity + '%');
                    if (data.ppm !== undefined) {
                        measurements.push(data.ppm + ' ppm');
                        if (data.status !== 'normal') color = '#ff5252';
                    }
                    let agePart = '';
                    if (data.age_s !== undefined) {
                        const m = Math.round(data.age_s / 60);
                        agePart = m < 1 ? 'agora' : (m < 60 ? m + 'm' : Math.round(m / 60) + 'h');
                    }
                    const base = measurements.length ? measurements.join(' · ') : 'sem leitura';
                    const text = agePart ? base + ' · ' + agePart : base;
                    putRoomReading(room, readings, name, text);
                    /* A room sensor does not write to the Geral header, so the
                       average it feeds would otherwise only move on the next
                       gas poll. */
                    if (room !== 'Geral') refreshGeralAverage();
                    /* This line used to read `parts.length`, a name that does not
                       exist in this function -- the array is `measurements`. It
                       threw a ReferenceError, and because the body sits in a
                       bare `catch (e) {}` the throw was invisible: the colour
                       was never applied and the title below it, the only place
                       the reading's age is available, was never written. A stale
                       reading looked live because the thing that said it was old
                       had silently stopped being set. */
                    readings.style.color = measurements.length ? color : '#737373';
                    readings.style.opacity = data.stale ? 0.55 : 1;
                    const ageDesc = data.age_s !== undefined ? Math.round(data.age_s / 60) + ' min' : '?';
                    readings.title = `${name} — última leitura há ${ageDesc}${data.stale ? ' (desatualizado)' : ''}`;
                } catch (e) {}
            }async function updateHomePower() {
                try {
                    const res = await fetch(`/device_status?nickname=casa`);
                    const data = await res.json();
                    const el = document.getElementById('power-display');
                    if (data.power_w !== undefined) {
                        el.innerText = `${Math.round(data.power_w)} W`; el.style.color = "#ffb74d"; 
                    } else { el.innerText = "-- W"; el.style.color = "#444"; }
                } catch(e) {}
            }

            /* ================= AMBIENT LAYER =================
               Rain across the whole screen when it rains, drifting gusts when it
               is windy, a warm wash when it is sunny, plus a tint for the hour
               and for the air. Owner request, 2026-09-29.

               Four decisions that are not obvious, and each of them is a
               correction of something that is easy to get wrong:

               * A CANVAS, not a hundred absolutely-positioned divs. A rain
                 effect made of DOM nodes is 150 elements and a layout per drop
                 per frame, on a phone that is also running the audio pipeline.
               * DRAWS, DOES NOT DOMINATE. pointer-events:none, z-index below
                 the page content, and the intensity comes from real numbers --
                 `precipitaProb` for the rain, `classWindSpeed` for the gusts.
                 An effect that ignores the forecast is wallpaper.
               * PAUSES. Stops when the document is hidden, which is what a phone
                 in a pocket is, and never starts under
                 `prefers-reduced-motion`. An animation nobody asked for that
                 keeps burning battery is a defect, not a feature.
               * DRAWS NOTHING when there is nothing to report, and the canvas is
                 cleared. An idle layer must not keep a loop alive. */
            const ambient = (function () {
                const canvas = document.getElementById('ambient');
                const tint = document.getElementById('ambient-tint');
                if (!canvas || !canvas.getContext) return { set() {}, setTint() {}, stop() {}, resize() {}, mode: 'none' };
                const ctx = canvas.getContext('2d');
                const reduce = !!(window.matchMedia
                    && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

                let drops = [], gusts = [], raf = null, kind = 'none', level = 0;
                let w = 0, h = 0, dpr = 1;

                function size() {
                    dpr = Math.min(window.devicePixelRatio || 1, 2);
                    w = canvas.clientWidth || window.innerWidth;
                    h = canvas.clientHeight || window.innerHeight;
                    canvas.width = Math.max(1, Math.round(w * dpr));
                    canvas.height = Math.max(1, Math.round(h * dpr));
                    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
                }
                size();

                /* Capped. Past this the picture stops improving and the frames
                   start costing more than the effect is worth. */
                function budget() { return Math.min(150, Math.round(w * h / 2800)); }

                function seedRain(n) {
                    drops = [];
                    for (let i = 0; i < n; i++) drops.push({
                        x: Math.random() * w, y: Math.random() * h,
                        len: 9 + Math.random() * 16, v: 6 + Math.random() * 9,
                        a: 0.10 + Math.random() * 0.20,
                    });
                }
                function seedWind(n) {
                    gusts = [];
                    for (let i = 0; i < n; i++) gusts.push({
                        x: Math.random() * w, y: Math.random() * h,
                        len: 30 + Math.random() * 90, v: 0.7 + Math.random() * 1.6,
                        a: 0.05 + Math.random() * 0.10, wob: Math.random() * 6.28,
                    });
                }

                function draw() {
                    ctx.clearRect(0, 0, w, h);
                    if (kind === 'rain') {
                        ctx.strokeStyle = 'rgb(205,228,255)';
                        ctx.lineWidth = 1;
                        for (const d of drops) {
                            ctx.globalAlpha = d.a * level;
                            ctx.beginPath();
                            ctx.moveTo(d.x, d.y);
                            ctx.lineTo(d.x - d.len * 0.2, d.y + d.len);
                            ctx.stroke();
                            d.y += d.v * (0.6 + level);
                            d.x -= d.v * 0.16 * (0.6 + level);
                            if (d.y > h) { d.y = -d.len; d.x = Math.random() * w; }
                            if (d.x < 0) d.x = w;
                        }
                    } else if (kind === 'wind') {
                        ctx.strokeStyle = 'rgb(222,232,242)';
                        ctx.lineWidth = 1.2;
                        for (const g of gusts) {
                            ctx.globalAlpha = g.a * level;
                            g.wob += 0.02;
                            ctx.beginPath();
                            ctx.moveTo(g.x, g.y);
                            ctx.bezierCurveTo(
                                g.x + g.len * 0.3, g.y + Math.sin(g.wob) * 14,
                                g.x + g.len * 0.7, g.y - Math.sin(g.wob) * 12,
                                g.x + g.len, g.y);
                            ctx.stroke();
                            g.x += g.v * (0.5 + level * 1.6);
                            if (g.x > w) { g.x = -g.len; g.y = Math.random() * h; }
                        }
                    }
                    ctx.globalAlpha = 1;
                }
                function loop() { draw(); raf = requestAnimationFrame(loop); }
                function start() { if (reduce || raf) return; raf = requestAnimationFrame(loop); }
                function halt() {
                    if (raf) cancelAnimationFrame(raf);
                    raf = null;
                    ctx.clearRect(0, 0, w, h);
                }

                return {
                    set(k, l) {
                        level = Math.max(0, Math.min(1, l || 0));
                        if (k === 'rain' && level > 0.04) {
                            kind = 'rain';
                            const n = Math.round(budget() * level);
                            if (drops.length !== n) seedRain(n);
                            canvas.classList.remove('hidden');
                            start();
                        } else if (k === 'wind' && level > 0.04) {
                            kind = 'wind';
                            const n = Math.round(budget() * 0.5 * level);
                            if (gusts.length !== n) seedWind(n);
                            canvas.classList.remove('hidden');
                            start();
                        } else {
                            kind = 'none';
                            canvas.classList.add('hidden');
                            halt();
                        }
                    },
                    setTint(night, hazy) {
                        if (!tint) return;
                        tint.classList.toggle('night', !!night);
                        tint.classList.toggle('hazy', !night && !!hazy);
                        tint.style.opacity = night ? 0.85 : (hazy ? 0.5 : 0);
                    },
                    resize() { size(); if (kind !== 'none') draw(); },
                    stop() { halt(); },
                    get mode() { return kind; },
                    get level() { return level; },
                };
            })();
            window.addEventListener('resize', () => ambient.resize());
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) ambient.stop();
                else if (ambient.mode !== 'none') ambient.set(ambient.mode, ambient.level);
            });

            /* Is it night? The moon phase is the honest answer when we have one,
               and the sky is when we do not: weatherType 1 is clear sky, which is
               daytime by definition, and a waning or new moon is a dark one. With
               neither, it is a guess, and it is labelled as one by the comment
               rather than pretending otherwise. */
            function _isNight(moon, today) {
                /* The SKY decides first. WeatherType 1 is clear sky, which is
                   daytime whatever the moon is doing, and putting the phase first
                   showed a waning moon at noon and then hid the UV index because
                   "it was night" -- so a clear afternoon rendered neither.

                   Then the phase, for the days that are not clear. Then, with
                   neither, night: a wrong moon is a cosmetic mistake, and no
                   moon at all is the same mistake. */
                if (Number(today && today.idWeatherType) === 1) return false;
                const m = String(moon || '');
                if (m.includes('Cheia')) return false;
                if (m.includes('Minguante')) return true;
                if (m.includes('Crescente')) return false;
                return true;
            }

            /* The real forecast, mapped onto the layer. IPMA weatherType: 1
               clear, 2-5 partly, 6-15 rain, 16+ fog. `precipitaProb` and
               `classWindSpeed` are what decide how hard it comes down. */
            function ambientFromForecast(today, aqi, moon) {
                const type = Number(today.idWeatherType) || 0;
                const prob = Number(today.precipitaProb);
                const windClass = parseInt(String(today.classWindSpeed || '0'), 10) || 0;
                /* IPMA wind classes 0 calm .. 5 extreme, mapped to 0..1. */
                const wind = [0, 0.18, 0.42, 0.68, 0.88, 1][Math.max(0, Math.min(5, windClass))];
                const rain = type >= 6 && type <= 15;
                const sunny = type === 1;
                const foggy = type >= 16;

                if (rain) {
                    const p = isNaN(prob) ? 0.6 : prob / 100;
                    ambient.set('rain', Math.max(0.12, p));
                } else if (wind >= 0.42) {
                    ambient.set('wind', wind);
                } else {
                    ambient.set('none', 0);
                }

                const a = Number(aqi);
                const bad = !isNaN(a) && a > 100;
                const moderate = !isNaN(a) && a > 50 && a <= 100;
                /* A full moon and a clear sky are light; a waning or absent
                   moon is not. Fog reads as its own kind of grey. */
                const m = String(moon || '');
                const lit = m.includes('Cheia') || m.includes('Crescente') || sunny;
                const dark = !lit;
                ambient.setTint(dark, bad || moderate);
            }

            async function updateWeather() {
                try {
                    const res = await fetch('/api/weather'); const data = await res.json();
                    if (!data.forecast) return;
                    // When the weather cache is stale, the user should not be left wondering
                    // whether the data is current. Mark the icon as stale in its title.
                    const weatherIcon = document.getElementById('main-weather-icon');
                    if (weatherIcon) {
                        if (data.stale) {
                            weatherIcon.title = data.fetched_at ? `Dados meteorológicos desatualizados (atualizados em ${data.fetched_at})` : 'Dados arquivados';
                        } else {
                            weatherIcon.title = 'Meteorologia';
                        }
                    }
                    const today = data.forecast[0];
                    let wType = today.idWeatherType;
                    let wIcon = CLOUD_SVG;
                    let ghostClass = 'ghost-normal';
                    if (wType === 1) { wIcon = WEATHER.sun; ghostClass = 'ghost-sun'; }
                    else if (wType <= 5) { wIcon = WEATHER.partly; ghostClass = 'ghost-normal'; }
                    else if (wType <= 15) { wIcon = WEATHER.rain; ghostClass = 'ghost-rain'; }
                    else if (wType >= 16) { wIcon = WEATHER.fog; ghostClass = 'ghost-normal'; }
                    document.getElementById('main-weather-icon').innerHTML = wIcon;
                    document.getElementById('main-weather-temp').innerText = `${Math.round(today.tMax)}°`;
                    const ghost = document.getElementById('brand-logo');
                    ghost.className = ''; ghost.classList.add(ghostClass);
                    /* The moon as a disc with a terminator, not a face. An empty
                       string was worse than the emoji it replaced: the phase
                       element then rendered nothing at all, which is a silent
                       loss of a reading rather than a change of style. */
                    let mIcon = MOON.new; const moon = data.moon_phase || "";
                    if (moon.includes("Crescente")) mIcon = MOON.crescent;
                    else if (moon.includes("Cheia")) mIcon = MOON.full;
                    else if (moon.includes("Minguante")) mIcon = MOON.waning;
                    document.getElementById('main-moon-icon').innerHTML = mIcon;
                    document.querySelector('.sky-element[title="Fase Lunar"]').title = moon || "Fase Lunar";
                    /* Night and UV are shown only when they are TRUE. The moon
                       phase at noon is noise, and a UV index from three hours
                       ago is a lie, so each slot is a function of the current
                       sky rather than a leftover from the last fetch. */
                    const isNight = _isNight(moon, today);
                    const moonSlot = document.getElementById('moon-slot');
                    if (moonSlot) moonSlot.classList.toggle('night', isNight);
                    const uvNum = Number(data.uv_index);
                    const uvSlot = document.getElementById('uv-slot');
                    const uvEl = document.getElementById('uv-indicator');
                    /* Below 3 the advice is "no precautions needed" and a badge
                       about it is clutter; it earns its place from Moderate up. */
                    const showUv = !!uvSlot && !isNaN(uvNum) && !isNight && uvNum >= 3;
                    if (uvSlot) uvSlot.classList.toggle('day', showUv);
                    if (uvEl) uvEl.innerHTML = showUv ? _uvSvg(uvNum) : '';
                    if (uvSlot) uvSlot.title = showUv
                        ? `Índice UV ${Math.round(uvNum)} (${uvAdvice(uvNum)})`
                        : 'Índice UV';

                    const aqi = data.aqi; const aqiEl = document.getElementById('aqi-indicator');
                    if (aqi !== undefined) {
                        /* Air quality, as SVG and not emoji. A leaf for "good"
                           and a skull for "bad" is a cartoon reading of a number
                           that matters, and the emoji rendered differently per
                           platform for the same measurement. */
                        if (aqi <= 50) { aqiEl.innerHTML = AQI.good; aqiEl.title = `AQI ${aqi} (Bom)`; }
                        else if (aqi <= 100) { aqiEl.innerHTML = AQI.moderate; aqiEl.title = `AQI ${aqi} (Moderado)`; }
                        else { aqiEl.innerHTML = AQI.bad; aqiEl.title = `AQI ${aqi} (Mau)`; }
                    } else { aqiEl.innerText = ''; }
                    /* Feed the ambient layer with what was just fetched. */
                    try { ambientFromForecast(today, data.aqi, moon); } catch (e) {}
                } catch(e) {}
            }

            /* Fetch every device's state once, and return how long it took.

               Extracted because the states used to arrive only when the
               5-second poll first fired: every switch was `disabled` and inert
               for the first 5.29s of every page load. A person cannot throw a
               switch they have not waited for, and a UI that is visibly
               assembled but not yet usable reads as broken.
               Also wrapped in its own try/catch: `loadDevicesStructure` has one
               around the whole body, and a throw inside the poll setup used to
               be swallowed silently, taking the first update with it. */
            async function refreshDeviceStates() {
                const t0 = performance.now();
                await Promise.all(ALL_DEVICES_ELEMENTS.map(i =>
                    i.type === 'toggle' ? fetchDeviceStatus(i) : fetchSensorStatus(i)
                ));
                updateHomePower();
                return performance.now() - t0;
            }

            async function loadDevicesStructure() {
                try {
                    const res = await fetch('/get_devices'); const data = await res.json();
                    const allDevices = [];
                    if (data.devices?.status) data.devices.status.forEach(d => allDevices.push({name: d, type: 'sensor'}));
                    if (data.devices?.toggles) data.devices.toggles.forEach(d => allDevices.push({name: d, type: 'toggle'}));
                    const grouped = {};
                    ROOMS_ORDER.forEach(r => grouped[r] = []);
                    allDevices.forEach(d => grouped[getRoomName(d.name)].push(d));
                    for (const room of ROOMS_ORDER) {
                        const devs = grouped[room];
                        if (devs && devs.length > 0) devs.forEach(d => {
                            if (d.type === 'sensor') createSensor(d.name);
                            else createToggle(d.name);
                        });
                    }
                    updateHomePower(); updateWeather();
                    /* Prime the states before the first paint settles, so the
                       switches are live when they are first seen. */
                    await refreshDeviceStates();
                    /* Measured after the tiles carry their `loaded` border, so
                       the overflow test uses the width the user actually gets. */
                    applyAllLabelMarquees();
                    window.addEventListener('resize', applyAllLabelMarquees);
                    setInterval(refreshDeviceStates, 5000);
                    setInterval(updateWeather, 600000);
                } catch (e) {}
            }

            async function loadHelp() {
                try {
                    const res = await fetch('/help'); const data = await res.json();
                    if (data.commands) { let t = ""; for (const c in data.commands) t += `${c}: ${data.commands[c]}\\n`; helpContent.innerText = t = t.replace(/\\n/g, '\\n'); }
                } catch (e) {}
            }
            function toggleHelp() { document.getElementById('cli-help').classList.toggle('open'); }

            /* ---- The chat panel, on a phone ----
               The tiles own the screen; the conversation is summoned. Kept to
               two functions because a panel that can be opened by three
               different affordances and closed by none of them is a panel the
               owner cannot get out of.

               On desktop this is all inert: the media query that turns #main
               into an overlay is max-width:768px, and the chat is a normal
               column there, exactly as it was. The functions are still safe to
               call -- openChat is a no-op when the panel is not a panel. */
            const chatPanel = document.getElementById('main');
            const chatTab = document.getElementById('chat-tab');
            function openChat() {
                if (!chatPanel) return;
                chatPanel.classList.add('open');
                /* On <body>, not only on the panel: the stylesheet moves the
                   tab out of the thumb arc and the grip into view from it, and
                   a rule that has no matching class never applies no matter how
                   many classes the panel itself carries. */
                document.body.classList.add('chat-open');
                if (chatTab) {
                    chatTab.setAttribute('aria-expanded', 'true');
                    chatTab.textContent = 'Fechar';
                }
            }
            function closeChat() {
                if (!chatPanel) return;
                chatPanel.classList.remove('open');
                chatPanel.classList.remove('dragging');
                document.body.classList.remove('chat-open');
                if (chatTab) {
                    chatTab.setAttribute('aria-expanded', 'false');
                    chatTab.textContent = 'Chat';
                }
            }
            if (chatPanel) {
                if (chatTab) {
                    chatTab.onclick = () => chatPanel.classList.contains('open')
                        ? closeChat() : openChat();
                }
                /* Drag the panel down to dismiss, from the grip or from the top
                   of the log. A full-screen sheet that can only be closed by a
                   small target is a sheet people cannot close; the drag is the
                   gesture a phone user already expects, and it is the one the
                   owner asked for.

                   Three details that are the difference between working and not:
                   the panel follows the finger (a transition on `open` would
                   fight the drag and feel broken), the drag is only recognised
                   downwards (a flick upwards is not "dismiss"), and releasing
                   below a third of the height snaps it shut while a shorter
                   drag springs back, so a small slip does not close the
                   conversation the owner is reading.

                   Wired on `chatPanel` and NOT inside the old
                   `if (chatTab && chatPanel)`. The only close control on a phone
                   is the grip, and tying the grip's wiring to the existence of
                   the tab meant that deleting the tab -- which is display:none on
                   a phone anyway, and exists there only for the desktop toggle --
                   would silently take the sole way out of the conversation with
                   it. The condition was the same class of bug as the one the
                   comment above it describes. */
                const grip = document.getElementById('chat-grip');
                let dragY = null, dragMoved = 0, dragSeen = false;
                const dragStart = (e) => {
                    if (!chatPanel.classList.contains('open')) return;
                    dragY = (e.touches ? e.touches[0].clientY : e.clientY);
                    dragMoved = 0;
                    /* "The finger moved at all", which is NOT the same as
                       dragMoved. An upward drag leaves dragMoved at 0 by design
                       -- upwards is not a dismissal -- and tap-to-close keyed on
                       dragMoved therefore fired on it and closed the sheet. The
                       existing test `test_dragging_up_does_nothing` caught that,
                       which is the whole reason it is in the suite. */
                    dragSeen = false;
                    chatPanel.classList.add('dragging');
                };
                const dragMove = (e) => {
                    if (dragY === null) return;
                    const y = (e.touches ? e.touches[0].clientY : e.clientY);
                    const dy = y - dragY;
                    /* Recorded BEFORE the upward early-return, and it is what the
                       click handler below consults: a browser still fires `click`
                       after `touchend`, so a drag that springs back (below the
                       threshold, so it did NOT close) would otherwise be closed a
                       moment later by the click it emits. */
                    if (dy !== 0) {
                        dragSeen = true;
                        if (grip) grip.dataset.dragged = '1';
                    }
                    if (dy < 0) return;  /* upwards is not a dismissal */
                    dragMoved = dy;
                    chatPanel.style.transform = 'translateY(' + dy + 'px)';
                    if (e.cancelable) e.preventDefault();
                };
                const dragEnd = () => {
                    if (dragY === null) return;
                    dragY = null;
                    chatPanel.classList.remove('dragging');
                    chatPanel.style.transform = '';
                    if (dragMoved > innerHeight / 3) closeChat();
                };
                /* A tap is no movement AT ALL. Not "no downward movement": an
                   upward drag is a gesture that goes nowhere, and treating it as a
                   tap closed a conversation the owner was reading. */
                const tapped = () => dragMoved === 0 && !dragSeen;
                /* TAP the grip and it closes, with no drag at all. The comment
                   in the stylesheet claims the handle IS the close control, and
                   for a long time it was not: a tap did nothing and only a
                   third-of-the-screen drag worked. On a phone that is the
                   difference between a control and a decoration. A tap is
                   `dragMoved === 0`, so it is distinguished from the drag by the
                   same variable rather than by a second listener racing the
                   first. */
                if (grip) {
                    grip.addEventListener('touchstart', dragStart, { passive: true });
                    grip.addEventListener('touchmove', dragMove, { passive: false });
                    grip.addEventListener('touchend', () => {
                        const wasTap = tapped();
                        dragEnd();
                        if (wasTap) closeChat();
                    });
                    grip.addEventListener('touchcancel', dragEnd);
                    grip.addEventListener('mousedown', dragStart);
                    grip.addEventListener('mousemove', dragMove);
                    grip.addEventListener('mouseup', () => {
                        const wasTap = tapped();
                        dragEnd();
                        if (wasTap) closeChat();
                    });
                    /* And the click a browser sends after all of that. One way
                       out, the way a keyboard uses it, guarded by the same record
                       of whether the finger moved. */
                    grip.addEventListener('click', () => {
                        if (grip.dataset.dragged === '1') { delete grip.dataset.dragged; return; }
                        closeChat();
                    });
                }
                /* PULL UP TO OPEN. The dock is the bar, so the bar is the
                   handle: dragging it upwards opens the conversation and the
                   panel follows the finger, the same direct manipulation as
                   closing it. Half the gesture is not needed -- the dock is only
                   ever dragged to open, so a third of the height is enough and a
                   short but deliberate pull counts. */
                const dock = document.getElementById('chat-dock');
                if (dock) {
                    let upY = null, upMoved = 0;
                    const upStart = (e) => {
                        if (chatPanel.classList.contains('open')) return;
                        if (e.target && e.target.id === 'voice-btn') return;
                        upY = (e.touches ? e.touches[0].clientY : e.clientY);
                        upMoved = 0;
                    };
                    const upMove = (e) => {
                        if (upY === null) return;
                        const y = (e.touches ? e.touches[0].clientY : e.clientY);
                        const dy = upY - y;          /* upwards is positive */
                        if (dy < 0) return;
                        upMoved = dy;
                        if (e.cancelable) e.preventDefault();
                    };
                    const upEnd = () => {
                        if (upY === null) return;
                        upY = null;
                        if (upMoved > 60) openChat();
                    };
                    dock.addEventListener('touchstart', upStart, {passive: true});
                    dock.addEventListener('touchmove', upMove, {passive: false});
                    dock.addEventListener('touchend', upEnd);
                }

                /* Tapping a device answers with text in the log, so the panel
                   opens by itself there too -- otherwise a reply would be
                   produced into a panel that is closed and never read. */
                /* Escape closes it, for a phone with a keyboard attached. */
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape' && chatPanel.classList.contains('open')) closeChat();
                });
            }


            /* The device strip is an inner scroller inside a non-scrolling app
               shell (body is 100dvh + overflow:hidden), so a clipped row of
               tiles looked like a rendering bug rather than "scroll me". Toggle
               a fade class from the ACTUAL overflow state, so a grid that fits
               is not dimmed, and re-evaluate on resize/orientation. */
            function updateDeviceScrollHint() {
                const dev = document.getElementById('devices');
                if (!dev) return;
                dev.classList.toggle('is-scrollable', dev.scrollHeight > dev.clientHeight + 2);
            }
            window.addEventListener('resize', updateDeviceScrollHint);
            window.addEventListener('orientationchange', updateDeviceScrollHint);
            if (window.ResizeObserver) {
                const ro = new ResizeObserver(updateDeviceScrollHint);
                ro.observe(document.body);
            }

            /* How tall the top bar is, in a custom property, so the chat panel
               can start underneath it instead of on top of it.

               There is no header BOX to copy a height from below 768px:
               `#header-strip` is `display: contents` there, so it generates no
               box at all (getBoundingClientRect returns 0x0) and #brand and
               #devices are static flex children of the body. `#brand` is a real
               box, so it is the one measured.

               Measured from a LIVE box rather than a constant because the bar
               is not a fixed height: it carries the brand row, the weather
               indicators and the burger, and the burger moves between the top
               and the nav bar with the same breakpoint that turns the strip
               into `contents`. A hardcoded number here is a number that is right
               on exactly one screen. */
            function syncBrandHeight() {
                const brand = document.getElementById('brand');
                if (!brand) return;
                const h = Math.round(brand.getBoundingClientRect().height);
                if (!h) return;
                document.documentElement.style.setProperty('--brand-h', h + 'px');
            }
            window.addEventListener('resize', syncBrandHeight);
            window.addEventListener('orientationchange', syncBrandHeight);
            if (window.ResizeObserver) {
                const brandRO = new ResizeObserver(syncBrandHeight);
                const brandEl = document.getElementById('brand');
                if (brandEl) brandRO.observe(brandEl);
            }
            syncBrandHeight();
            /* The weather/moon/UV/AQI indicators fill asynchronously, and the
               first paint measures #brand before they have their text. Without
               this the panel would be positioned against a bar that was then
               20px taller, and the burger would sit under the sheet. */
            window.addEventListener('load', syncBrandHeight);
            setTimeout(syncBrandHeight, 400);
            chatSend.onclick = sendChatCommand; 
            chatInput.onkeydown = (e) => { 
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatCommand(); }
                setTimeout(() => { chatInput.style.height = 'auto'; chatInput.style.height = chatInput.scrollHeight + 'px'; }, 0);
            };
            loadDevicesStructure(); loadHelp(); addToChatLog("Nas sombras, aguardo...", "ia");
            /* After the device tiles are in the DOM, not before -- measuring
               scrollHeight on an empty container would always report "fits". */
            setTimeout(updateDeviceScrollHint, 300);
            /* ============ VOICE (phone) ============
               Press to talk, release to send. One round trip: the browser
               decodes its own recording to 16 kHz mono WAV, posts it, and gets
               back the transcript, the answer and audio to play.

               Why the decode happens HERE and not on the server: MediaRecorder
               produces webm/opus on Android Chrome, which `soundfile` cannot
               read. The server's fallback would have read the opus container as
               raw 16-bit PCM and handed the recogniser noise -- which
               transcribes into a confident, wrong sentence with no error
               anywhere. decodeAudioData understands every format the browser
               itself can record, and it costs ffmpeg nothing on the server.

               Not covered by the pytest suite: getUserMedia, MediaRecorder and
               AudioContext need a real device and a real microphone, and no
               amount of asserting on this string proves the permission prompt
               appears. What IS tested is the server half (tests/test_ui_voice.py).
               Verified by hand on a real phone before this was called done. */
            let _voiceStream = null, _voiceRec = null, _voiceChunks = [], _voiceBusy = false;
            /* The container the recorder actually produced, kept at page scope
               for the same reason as the buttons below: sendRecording() is a
               SIBLING of initVoice() and used a variable declared inside it, so
               the send path threw `_voiceMime is not defined` on its very first
               line -- before touching the network. That is why the user saw
               "falha ao enviar o áudio" with the recording working perfectly:
               the failure was never in the audio, it was a name. */
            let _voiceMime = 'audio/webm';
            const voiceBtn = document.getElementById('voice-btn');
            const voiceStatus = document.createElement('div');
            voiceStatus.className = 'voice-status';
            voiceStatus.setAttribute('role', 'status');
            voiceStatus.setAttribute('aria-live', 'polite');
            document.body.appendChild(voiceStatus);
            let _voiceStatusTimer = null, _voiceStatusSticky = false;

            function voiceSay(msg, ms) {
                voiceStatus.textContent = msg;
                voiceStatus.classList.add('show');
                clearTimeout(_voiceStatusTimer);
                /* A status with NO deadline is sticky: it means "the box is
                   working", and only the code that finishes the work may take it
                   down. It used to be dropped on the floor instead --
                   `voiceSay('A ouvir...')` carries no `ms`, so `if (ms)` armed no
                   timer, and on the success path nothing else removed `.show`.
                   The float therefore stayed over the page for the rest of the
                   session, on top of the answer it was announcing. */
                _voiceStatusSticky = !ms;
                if (ms) _voiceStatusTimer = setTimeout(() => voiceStatus.classList.remove('show'), ms);
            }

            function voiceClearBusy() {
                /* The work is over. A message that brought its own deadline
                   belongs to whoever put it there -- an error the user still
                   has to read -- so this only takes down the sticky one. */
                if (!_voiceStatusSticky) return;
                _voiceStatusSticky = false;
                voiceStatus.classList.remove('show');
            }

            function bytesToBase64(bytes) {
                let bin = '';
                const CH = 0x8000;
                for (let i = 0; i < bytes.length; i += CH) {
                    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
                }
                return btoa(bin);
            }

            function playReply(b64) {
                if (!b64) return;
                try {
                    const bin = atob(b64);
                    const bytes = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
                    const audio = new Audio(url);
                    audio.onended = () => URL.revokeObjectURL(url);
                    audio.play().catch(() => voiceSay('Resposta em texto acima.', 4000));
                } catch (e) { voiceSay('Resposta em texto acima.', 4000); }
            }

            /* Send what was recorded. This runs from the recorder's onstop, NOT
               from the click that stopped it: the final ondataavailable is
               delivered after stop() returns, so building the blob in the click
               handler sends a recording with its last chunk missing. The first
               version did exactly that, and also re-entered through onstop into
               a guard that had already been cleared -- a silent no-op that
               looked like a permissions problem. */
            /* The buttons, in a scope BOTH the recorder and the sender can see.

               It was declared inside initVoice() while sendRecording() is a
               sibling, so `sendRecording` threw ReferenceError on its very first
               line: the recording was sent but the buttons were never released,
               so they stayed red and disabled for ever. No page error was
               reported -- an unhandled rejection inside a promise nobody awaits
               is silent, which is why `page.on('pageerror')` did not catch it
               either. Sibling functions, shared state at the right scope. */
            const voiceBtnChat = document.getElementById('voice-btn-chat');
            const allVoiceBtns = [voiceBtn, voiceBtnChat].filter(Boolean);

            async function sendRecording() {
              /* Wrapped whole. This function used to throw a ReferenceError on
                 its first line, and because it is async and nobody awaits the
                 promise, the failure was completely silent: the button stayed red
                 and disabled, the transcript never appeared, and `pageerror`
                 reported nothing. A send path that cannot fail visibly is a send
                 path that fails invisibly. */
              try {
                const blob = new Blob(_voiceChunks, { type: _voiceMime || 'audio/webm' });
                _voiceChunks = [];
                allVoiceBtns.forEach(b => b.classList.remove('recording'));
                if (_voiceStream) { _voiceStream.getTracks().forEach(t => t.stop()); _voiceStream = null; }
                if (blob.size < 2000) { voiceSay('Demasiado curto.', 2000); return; }
                _voiceBusy = true;
                allVoiceBtns.forEach(b => { b.classList.add('busy'); b.disabled = true; });
                voiceSay('A ouvir...');
                try {
                    /* The recording goes up as the microphone produced it.

                       This used to be: decodeAudioData, a hand-written
                       resampler, a hand-written WAV container, then base64. Three
                       things to get wrong in a browser -- and on a real Android
                       phone it failed at the first, with "falha ao enviar o
                       áudio", while the recording and everything else worked.

                       The server already carries PyAV: it ships with
                       faster-whisper, and it opens webm, opus, ogg, mp4/aac and
                       wav, resampling to 16 kHz mono on the way in. So the page
                       sends bytes and the one job that needs a decoder happens
                       where it can be measured. The content type travels for the
                       log; PyAV sniffs the container and does not need it. */
                    const bytes = new Uint8Array(await blob.arrayBuffer());
                    const res = await fetch('/api/voz', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            audio_base64: bytesToBase64(bytes),
                            content_type: blob.type || 'audio/webm',
                        }),
                    });
                    if (res.status === 401) { location.href = '/login?next=/'; return; }
                    const data = await res.json();
                    if (data.success === false) {
                        /* The server's reason, in the message. A generic
                           "não deu" on a phone is the one failure nobody can
                           debug, because it cannot be reproduced anywhere else. */
                        voiceSay((data.error || 'Não deu.')
                            + (data.detail ? ' (' + data.detail + ')' : ''), 6000);
                        console.error('voice: refused', data);
                        return;
                    }
                    if (data.transcript) addToChatLog(data.transcript, 'user');
                    if (data.text) addToChatLog(data.text, 'ia');
                    /* A spoken command always opens the panel. The transcript
                       is the receipt: if the house heard the wrong thing, the
                       only way to find out is to read what it thought it heard,
                       and on a phone that text is behind a panel the owner did
                       not open. */
                    if (data.transcript || data.text) openChat();
                    playReply(data.audio_base64);
                } catch (err) {
                    voiceSay('Falha de rede.', 3000);
                } finally {
                    _voiceBusy = false;
                    /* The one place that knows the work is over. Before this,
                       the float announced "A ouvir..." and nothing on the
                       success path ever took it down. */
                    voiceClearBusy();
                    allVoiceBtns.forEach(b => { b.classList.remove('busy'); b.disabled = false; });
                }
              } catch (err) {
                console.error('voice: send failed', err);
                voiceSay('Falha ao enviar o áudio.', 3000);
                allVoiceBtns.forEach(b => { b.classList.remove('recording', 'busy'); b.disabled = false; });
                _voiceBusy = false;
              }
            }

            function initVoice() {
                /* BOTH microphones, one machine. Two independent recorders
                   would mean two getUserMedia streams, two decodes and two
                   uploads for one utterance, and whichever the owner pressed
                   last wins -- the visible state of the other button would be a
                   lie. The composer button is the one for voice MESSAGES; the
                   bar button stays as the shortcut. */
                if (!voiceBtn) return;
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia
                    || typeof window.MediaRecorder === 'undefined') {
                    allVoiceBtns.forEach(b => {
                        b.disabled = true;
                        b.title = 'Este navegador não suporta gravação';
                    });
                    return;
                }
                /* PRESS AND HOLD, which is what a microphone button is.

                   It was a click handler that toggled: tap once to start, tap
                   again to stop. The owner pressed and held and nothing was
                   recorded, because nothing happens on a press -- and from the
                   outside a button that ignores your thumb and shows no state is
                   indistinguishable from a broken one.

                   Pointer events rather than touch+mouse, so one handler covers
                   finger, stylus and mouse and cannot double-fire on a device
                   that emits both. `setPointerCapture` keeps the release
                   delivered even if the thumb slides off the button, which is
                   what people actually do. Three safety behaviours, because a
                   microphone that stays open after you let go is a privacy
                   problem, not a cosmetic one:

                   * a MINIMUM hold, so a brush of the thumb does not send a
                     40ms recording;
                   * a MAXIMUM, so holding forever cannot fill memory -- a 20s
                     clip is longer than any command in this house;
                   * release ANYWHERE ends it, including pointercancel (the
                     browser took the gesture) and visibility change (the phone
                     was put down mid-sentence).

                   Keyboard and assistive tech keep a click: `keydown` on Enter
                   or Space starts, and the same release path ends it, so the
                   button is not a finger-only feature. */
                let _voiceHeld = false, _voiceStart = 0, _voiceMin = 350, _voiceMax = 20000;

                async function voiceBegin() {
                    if (_voiceBusy || _voiceRec) return;
                    _voiceStart = Date.now();
                    // Immediate feedback, before any permission prompt: the
                    // prompt itself is slow and silence reads as "not working".
                    voiceSay('A ouvir...');
                    allVoiceBtns.forEach(b => b.classList.add('recording'));
                    try {
                        _voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    } catch (err) {
                        voiceSay('Sem acesso ao microfone.', 3000);
                        allVoiceBtns.forEach(b => b.classList.remove('recording'));
                        return;
                    }
                    _voiceChunks = [];
                    try {
                        const opts = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
                            .find(m => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m));
                        _voiceRec = opts ? new MediaRecorder(_voiceStream, { mimeType: opts })
                                         : new MediaRecorder(_voiceStream);
                        _voiceMime = _voiceRec.mimeType || _voiceMime;
                    } catch (err) {
                        voiceSay('Gravação indisponível.', 3000);
                        allVoiceBtns.forEach(b => b.classList.remove('recording'));
                        if (_voiceStream) _voiceStream.getTracks().forEach(t => t.stop());
                        _voiceStream = null;
                        return;
                    }
                    _voiceRec.ondataavailable = (e) => { if (e.data && e.data.size) _voiceChunks.push(e.data); };
                    _voiceRec.onstop = () => { _voiceRec = null; sendRecording(); };
                    _voiceRec.start();
                }

                function voiceEnd() {
                    if (!_voiceRec) { allVoiceBtns.forEach(b => b.classList.remove('recording')); return; }
                    const held = Date.now() - _voiceStart;
                    if (held < _voiceMin) {
                        // Too short to be a word. Say so instead of transcribing a
                        // click, which the recogniser would turn into noise.
                        voiceSay('Mantém premido para falar.', 2000);
                        _voiceRec.onstop = null;
                        try { _voiceRec.stop(); } catch (e) {}
                        _voiceRec = null;
                        if (_voiceStream) { _voiceStream.getTracks().forEach(t => t.stop()); _voiceStream = null; }
                        allVoiceBtns.forEach(b => b.classList.remove('recording'));
                        return;
                    }
                    if (held >= _voiceMax) {
                        voiceSay('Demasiado longo.', 2000);
                    }
                    _voiceRec.stop();   // onstop -> sendRecording
                }

                allVoiceBtns.forEach(btn => {
                    btn.addEventListener('pointerdown', (e) => {
                        e.preventDefault();          // no synthetic click after this
                        _voiceHeld = true;
                        try { btn.setPointerCapture(e.pointerId); } catch (err) {}
                        voiceBegin();
                    });
                    const release = () => { if (!_voiceHeld) return; _voiceHeld = false; voiceEnd(); };
                    btn.addEventListener('pointerup', release);
                    btn.addEventListener('pointercancel', release);
                    btn.addEventListener('lostpointercapture', release);
                    /* The finger slid off and the capture is gone: still stop.
                       Without this the microphone stays open. */
                    btn.addEventListener('pointerleave', () => { if (_voiceHeld) release(); });
                    /* Keyboard: Enter and Space hold-to-talk, released by the key
                       going up. Without it the button cannot be used at all
                       without a finger. */
                    btn.addEventListener('keydown', (e) => {
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        e.preventDefault();
                        if (e.repeat || _voiceHeld) return;
                        _voiceHeld = true; voiceBegin();
                    });
                    btn.addEventListener('keyup', (e) => {
                        if (e.key !== 'Enter' && e.key !== ' ') return;
                        e.preventDefault();
                        if (!_voiceHeld) return;
                        _voiceHeld = false; voiceEnd();
                    });
                });
                /* Put down mid-sentence: release the microphone. */
                document.addEventListener('visibilitychange', () => {
                    if (document.hidden && _voiceHeld) { _voiceHeld = false; voiceEnd(); }
                });
            }

            /* initVoice() LAST, and deliberately: it used to be called near the
               top of this script while `const voiceBtn` was declared twenty
               lines below it, which is a temporal dead zone access -- a
               ReferenceError thrown before any listener was attached. The page
               rendered two microphones, neither disabled, neither doing
               anything, and the status element never entered the DOM. Function
               declarations hoist, so `typeof initVoice === 'function'` was true
               and the code looked present.

               Called from the end, after every binding it touches exists. */
            initVoice();
        </script>
        <script>__SHARED_JS__</script>
        <script>
            /* The service worker, registered on LOAD rather than on DOMContentLoaded
               so the first paint is not held up by a fetch. Registered only on
               https (or localhost): a service worker on plain http is silently
               refused by every browser, and a registration that appears to
               succeed is worse than none. */
            if ('serviceWorker' in navigator) {
                window.addEventListener('load', function () {
                    navigator.serviceWorker.register('/sw.js', { scope: '/' })
                        .catch(function (e) { console.warn('sw: não registado', e); });
                });
            }
        </script>
    </body>
    </html>
    """).replace("__ADMIN_LINKS__", admin_links_html) \
        .replace("__SHARED_CSS__", _css) \
        .replace("__SHARED_JS__", _js)

def handle(user_prompt_lower, user_prompt_full):
    """UI skill doesn't handle voice commands - only registers web routes."""
    return None
