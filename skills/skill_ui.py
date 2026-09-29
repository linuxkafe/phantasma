import config
from pathlib import Path
import json
import logging
import os
import time

from flask import jsonify, make_response, redirect, request, session, url_for

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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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
  </form>
  <p class="sub" style="margin:1.25rem 0 0;text-align:center">
    <a href="/recuperar" style="color:var(--muted)">Esqueci-me a password</a>
  </p>
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
        return render_verify_page(error="O código não é válido ou expirou.", email=email)

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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pHantasma</title><style>{_AUTH_CSS}</style></head>
<body><div class="card"><h1>{title}</h1>{sub_html}{body_html}</div></body></html>"""
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
    """The user's own page: tokens and trusted devices.

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
        + f"</span></span>"
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
        minted_block = f"""
<p class="msg ok" role="alert"><strong>Token criado — copia-o agora,
não voltarás a vê-lo.</strong><br><span class="mono">{secret}</span></p>"""

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

    Resolved server-side so admin links are never SENT to a non-admin, rather
    than hidden in CSS: a display:none link still leaves its URL in the page
    source, which is disclosure by accident. The real gate remains
    @admin_required on the routes; not sending a link the viewer cannot use is
    the honest behaviour on top of it.

    `/` is behind a session now, so this reads that session and never the
    loopback admin bypass: a bypassed local request is admin in /admin/*, but
    it is not signed in as a user here, and `handle_request` is only reached
    once a real session exists.
    """
    try:
        from src.api import ui_auth

        return ui_auth.is_admin()
    except Exception:
        # Never let an auth probe break the device page.
        return False


def handle_request():
        _css, _js = _shared_design()
        is_admin = _viewer_is_admin()
        # Menu links are built from the RESOLVED role, not from what the page
        # hopes the viewer is. This used to hardcode `admin_links_html = ""`,
        # which is why there was no menu at all on `/` for anyone: an admin had
        # to know the URLs, and a plain user had nothing to click. Now a signed-in
        # user always gets a menu; the admin entries are rendered only for an
        # admin, so a display:none link never leaves an admin URL in the page of
        # someone who may not use it.
        links = []
        if is_admin:
            links.append(('/admin/brain', '🧠', 'Cérebro'))
            links.append(('/admin', '📊', 'Dashboard'))
            links.append(('/admin/config', '⚙️', 'Configuração'))
            links.append(('/admin/users', '👤', 'Utilizadores'))
        links.append(('/perfil', '🔑', 'Perfil'))
        links.append(('/logout', '🚪', 'Sair'))
        admin_links_html = "".join(
            f'<a class="nav-link" href="{href}">'
            f'<span class="ico" aria-hidden="true">{ico}</span>'
            f'<span class="lbl">{lbl}</span></a>'
            for href, ico, lbl in links
        )
        return (
        """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            #brand {
                display: flex; flex-direction: column; align-items: center; justify-content: center;
                /* The brand is the identity block and nothing else. It was
                   width:210px with a right border, which on a wide desktop
                   spent fixed space on a logo while the device strip -- the
                   part that runs out of room -- was capped at 34dvh and
                   scrolled. Below 900px it is still width:100% and still
                   carries the border, because there the rule is what separates
                   the header from the list. */
                width: 210px; height: 100%;
                border-right: 1px solid #333; background: #151515;
                cursor: pointer; user-select: none; z-index: 10;
                padding: 10px; box-sizing: border-box;
                position: relative; overflow: hidden;
                flex: 0 0 auto;
            }
            #brand:active { background: #222; }

            /* ZONA DO CÉU (Tempo + Lua) */
            #sky-stage {
                display: flex; align-items: flex-end; justify-content: center;
                gap: 15px; margin-bottom: 5px; width: 100%;
            }
            .sky-element {
                display: flex; flex-direction: column; align-items: center;
                position: relative;
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

            /* The device panel now collapses at EVERY width, because the
               toggle is visible at every width -- the shared design system made
               the toggle permanent, so a panel that stayed pinned open on
               desktop would leave the button doing nothing there. Closed by
               default; design.js() adds .open. The device strip is unchanged
               once opened: same 4 rooms, same 14 tiles. */
            #nav-menu:not(.nav-menu-always) {
                flex: 1; display: none; align-items: flex-start; align-content: flex-start;
                flex-wrap: wrap; overflow-y: auto; overflow-x: hidden;
                height: 100%; padding: 20px 0 20px 20px;
            }
            #nav-menu.open { display: flex; }
            #nav-menu::-webkit-scrollbar { width: 4px; }
            #nav-menu::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }

              .device-room {
                  display: inline-flex; flex-direction: column;
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
            .room-header { font-size: 0.75rem; font-weight: bold; color: #666; margin-bottom: 8px; text-transform: uppercase; }
            .room-content { display: flex; gap: 8px; flex-wrap: wrap; }

            /* WIDGETS */
                /* Vertical stack, cross-axis centred, text centred -- the
                   `flex flex-col items-center text-center` behaviour. It was
                   already a column with align-items:center, but the tile was
                   pinned to height:44px, which squeezed the label into a 2-line
                   clamp at 0.6rem and made the centring invisible. 44px is kept
                   as a MINIMUM (the touch-target floor), never as a cap. */
                .device-toggle, .device-sensor {
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
                .device-toggle, .device-sensor {
                    min-width: 60px;
                    min-height: 88px;
                    height: auto;
                    flex-grow: 0;
                }
            }
              .device-sensor { background: #252525; border: 1px solid #333; }
              .device-toggle.loaded { opacity: 1; border: 1px solid #333; }
              .device-toggle.active .device-icon { filter: grayscale(0%); }

              /* Compact view: every device visible at once, no scrollbar.
                 The default view is deliberately capped at 34dvh and scrolls,
                 because a capped panel is what keeps the chat above the fold on
                 a phone. That is a real constraint, but it means a home with 14
                 devices hides most of them behind an inner scroller that reads
                 as a clipping bug rather than as "scroll me".

                 This mode trades the 44px touch-target floor and the icon
                 badge for a single dense row per room, so all of them fit. It
                 is opt-in and remembered, and it does not change the default:
                 the icon is still the tile, and the switch is still there, just
                 laid out sideways. */
              body.dev-compact #devices { overflow-y: visible; max-height: none; }
              /* Give the strip its own row. In the default layout #devices is
                 a capped scroller next to the nav; with the cap lifted it would
                 otherwise sit beside the burger on one line and wrap oddly on a
                 phone. */
              body.dev-compact #devices { flex: 1 1 100%; min-width: 0; }
              body.dev-compact .device-room {
                  display: block; margin-right: 0; margin-bottom: 6px;
                  padding-right: 0; border-right: none; width: 100%;
              }
              body.dev-compact .room-header {
                  display: inline-block; margin: 0 8px 0 0; font-size: 0.6rem;
              }
              body.dev-compact .room-content { display: inline-flex; gap: 4px; }
              body.dev-compact .device-toggle, body.dev-compact .device-sensor {
                  flex-direction: row; min-width: 0; min-height: 28px;
                  height: auto; padding: 3px 6px; border-radius: 6px; gap: 4px;
                  background: #1e1e1e;
              }
              body.dev-compact .device-icon {
                  width: 1.1rem; height: 1.1rem; font-size: 0.75rem;
                  margin-bottom: 0; border-radius: 4px;
              }
              body.dev-compact .device-label, body.dev-compact .sensor-label {
                  font-size: 0.6rem; width: auto; text-align: left;
              }
              body.dev-compact .device-label { max-width: 9rem; }
              body.dev-compact .sensor-data { font-size: 0.6rem; }
              body.dev-compact .switch { width: 26px; height: 15px; }
              body.dev-compact .slider { width: 12px; height: 12px; }
              body.dev-compact .slider::before { width: 11px; height: 11px; }

              #dev-view-toggle {
                  background: transparent; border: 1px solid #333; color: #888;
                  border-radius: 6px; width: 30px; height: 30px; cursor: pointer;
                  font-size: 0.9rem; line-height: 1; align-self: center;
                  margin-right: 6px; flex: 0 0 auto;
              }
              #dev-view-toggle:hover { border-color: #555; color: #ccc; }
              #dev-view-toggle[aria-pressed="true"] {
                  border-color: var(--accent, #22c55e); color: var(--accent, #22c555);
              }
            
                /* The icon is the tile's badge, matching pdftools ToolCard.tsx:52-53
                   -- `flex h-10 w-10 items-center justify-center rounded-lg` with
                   `mb-2` and a hover scale. The tile itself was already
                   `flex flex-direction:column; align-items:center` (215-217);
                   only the badge box was missing, so the glyph sat loose on the
                   tile background. Styled here rather than wrapped in the JS
                   builder (line 540) so the DOM contract is untouched. */
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
                .device-label {
                    font-size: 0.65rem; color: #aaa; width: 100%; text-align: center;
                    line-height: 1.15; white-space: normal; overflow: hidden;
                    /* Clamp at 3, not 2: the tile is taller now, so a 2-line
                       clamp was cutting real device names in half. */
                    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
                }

            .switch { position: relative; display: inline-block; width: 28px; height: 14px; margin-bottom: 2px; }
            .switch input { opacity: 0; width: 0; height: 0; }
            .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #444; transition: .4s; border-radius: 34px; }
            .slider:before { position: absolute; content: ""; height: 10px; width: 10px; left: 3px; bottom: 2px; background-color: white; transition: .4s; border-radius: 50%; }
            input:checked + .slider { background-color: var(--ia-msg); }
            input:checked + .slider:before { transform: translateX(12px); }

            .sensor-data { font-size: 0.7rem; color: #4db6ac; font-weight: bold; 
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
            }
            .sensor-label { font-size: 0.6rem; color: #888; width: 100%; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

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
            #chat-input { flex: 1; background: #2a2a2a; color: #fff; border: none; padding: 12px; border-radius: 20px; font-size: 16px; outline: none; resize: none; height: 24px; max-height: 100px; font-family: inherit; overflow-y: hidden; }
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
                #brand { flex: 0 0 auto; height: auto; }

                #chat-input { min-height: 44px; height: auto; }
                #chat-send { min-height: 44px; }

                /* 320x568 measured 7 of 14 devices FULLY hidden: the tiles are
                   84px tall and only ~2 rows fit the bounded strip. On short
                   screens compact the tile so the devices are actually
                   reachable rather than merely scrollable. */
                @media (max-height: 700px) {
                    .device-toggle, .device-sensor { min-height: 64px; padding: 6px 8px; }
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
                    justify-content: space-between; border-right: none; padding: 8px 12px;
                }
                #sky-stage { margin-bottom: 0; gap: 8px; }
                #ghost-stage { margin-bottom: 0; }
                #brand-logo { font-size: 0.8rem !important; }
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
                .device-toggle, .device-sensor { min-width: 60px; min-height: 84px; height: auto; }
                .device-icon { font-size: 1.2rem; }
                .sensor-data, .device-label, .sensor-label, .room-header { font-size: 0.75rem; line-height: 1.15; }
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
            .nav-bar { margin-left:auto !important; }

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
            .react-savenode {
              align-self:flex-end; margin-top:4px; padding:4px 8px;
              font-size:.7rem; font-family:inherit; cursor:pointer;
              color:var(--muted, #999); background:transparent;
              border:1px dashed var(--border, #333); border-radius:var(--radius, 8px);
            }
            .react-savenode:hover { color:var(--brand-400, #22d3ee); border-color:var(--brand-500, #06b6d4); }
            .react-savenode:disabled { opacity:.5; cursor:default; }
            .react-count { font-size:.75rem; color:var(--muted); align-self:center; }

            /* Accessibility: visible focus outline */
            .device-toggle:focus-visible,
            .device-sensor:focus-visible {
                outline: 2px solid var(--accent);
                outline-offset: 2px;
            }
        </style>
    </head>
    <body>
        <div id="easter-egg-layer"><div id="big-ghost">👻</div></div>

          <div id="header-strip">
              <div id="brand" onclick="triggerEasterEgg()">
                  <div id="sky-stage">
                      <div class="sky-element" title="Meteorologia">
                          <div id="main-weather-icon">☁️</div>
                          <div id="main-weather-temp">--°</div>
                      </div>
                      <div class="sky-element" title="Fase Lunar">
                          <div id="main-moon-icon">🌑</div>
                      </div>
                  </div>
                  <div id="ghost-stage">
                      <div id="brand-logo" class="ghost-normal">👻</div>
                      <div id="aqi-indicator" title="Qualidade do Ar"></div>
                  </div>
                  <div id="brand-name">pHantasma</div>
                  <div id="power-display" title="Consumo Geral">-- W</div>
              </div>
              <!-- Secondary nav, always visible. Shares .nav-menu styling with
                   the admin pages but has no toggle: on this screen a burger
                   would gate five links and nothing else. -->
              
                  <!-- Always available, outside the burger. -->
                  <div id="devices" aria-label="Dispositivos"></div>
<div class="nav-bar">
                    <!-- View toggle for the device strip: the default caps the
                         panel and scrolls, this shows every device at once.
                         Kept next to the devices it affects, and small enough
                         not to compete with the nav burger. -->
                    <button id="dev-view-toggle" type="button"
                            title="Ver todos os dispositivos sem scroll"
                            aria-label="Alternar vista dos dispositivos">▤</button>
                  <!-- Admin navigation lives here, and only here. It is a
                       right-aligned burger because the LEFT edge of this page is
                       the brand: the layout reads left-to-right as
                       identity -> navigation, and a menu in the top-left
                       competes with the mark that names the thing.

                       Rendered server-side only for an admin viewer, so a
                       non-admin is not sent links they cannot use. The burger
                       collapses the panel below 900px, matching the admin
                       pages, which is the behaviour that shared design.js()
                       implements. -->
                  
                  <nav class="nav-menu nav-menu-always" id="nav-menu" aria-label="Administração">__ADMIN_LINKS__</nav>
              </div>
          </div>


        <div id="main">
            <div id="chat-log"></div>
            <div id="help-toggle" onclick="toggleHelp()">Ver Comandos</div>
            <div id="cli-help"><pre id="help-content" style="color:#888; font-size:0.8em; margin:0;">...</pre></div>
            <div id="chat-input-box">
                <textarea id="chat-input" placeholder="Mensagem..." autocomplete="off"></textarea>
                <button id="chat-send">Enviar</button>
            </div>
        </div>

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
            function getDeviceIcon(name) {
                const n = name.toLowerCase();
                if (n.includes('aspirador')||n.includes('robot')) return '🤖';
                if (n.includes('luz')||n.includes('candeeiro')) return '💡';
                if (n.includes('exaustor')||n.includes('ventoinha')) return '💨';
                if (n.includes('desumidificador')) return '💧';
                if (n.includes('gás')||n.includes('fumo')) return '🔥';
                if (n.includes('carro')||n.includes('carrinha')||n.includes('veículo')) return '🚗';
                if (n.includes('forno')) return '♨️';
                if (n.includes('tomada')||n.includes('ficha')) return '⚡';
                return '⚡';
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
                const header = document.createElement('div'); header.className = 'room-header'; header.innerText = room;
                roomContainer = document.createElement('div'); roomContainer.className = 'room-content'; roomContainer.id = `room-content-${room}`; 
                roomWrapper.append(header, roomContainer); devicesEl.appendChild(roomWrapper);
                return roomContainer;
            }

            function showTypingIndicator() {
                if (document.getElementById('typing-indicator-row')) return;
                const row = document.createElement('div'); row.id = 'typing-indicator-row'; row.className = 'msg-row ia'; 
                const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻';
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
                bits.push('esta resposta nao nomeia nenhum no do grafo, por isso o grafo ficou intacto');
              } else if (d.graph === 'topic_fallback') {
                bits.push('grafo: no do topico actual (a resposta nao foi usada)');
              } else if (d.graph) {
                bits.push('grafo: ' + d.graph + (d.graph_node ? ' (' + d.graph_node + ')' : ''));
              }
              if (d.topic_key) bits.push(d.topic_key);
              if (!d.applied) bits.push('nao aplicada');
              return bits.join(' \u00b7 ');
            }

            function addSaveNodeAction(msgEl, btn) {
              const row = msgEl.parentElement;
              if (row.querySelector('.react-savenode')) return;
              const b = document.createElement('button');
              b.className = 'react-savenode';
              b.type = 'button';
              b.textContent = 'Guardar esta resposta como nó do grafo';
              b.title = 'Cria um nó a partir desta resposta, para que futuras '
                      + 'reações a ela reforcem esse nó';
              b.addEventListener('click', async () => {
                b.disabled = true;
                const r = await fetch('/api/graph/node', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({
                    label: (msgEl.innerText || '').trim().slice(0, 120),
                    text: (msgEl.innerText || '').trim().slice(0, 2000),
                  }),
                });
                const d = await r.json();
                const note = row.querySelector('.react-note');
                if (d.ok) {
                  if (note) {
                    note.textContent += ' \u00b7 nó criado: ' + (d.result.label || '');
                  }
                  b.remove();
                } else {
                  b.disabled = false;
                  if (note) note.textContent = 'Nó não criado: ' + (d.error || 'erro');
                }
              });
              row.appendChild(b);
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
                // When the reply named no node, the graph was deliberately left
                // alone. Creating one automatically is NOT the right default: a
                // reaction is a sentiment, and turning every unanswered text into
                // a concept would bloat the graph with whatever happened to be
                // said. So the capability is offered as an explicit action, and
                // the operator decides.
                if (d.graph === 'no_match' && btn) {
                  addSaveNodeAction(msgEl, btn);
                }
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


            function addToChatLog(text, sender = 'ia') {
                removeTypingIndicator(); 
                const row = document.createElement('div'); row.className = `msg-row ${sender}`;
                if (sender === 'ia') { const avatar = document.createElement('div'); avatar.className = 'ia-avatar'; avatar.innerText = '👻'; row.appendChild(avatar); }
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
                addToChatLog(prompt, 'user'); chatInput.value = ''; chatInput.style.height = '24px'; 
                showTypingIndicator(); 
                try {
                    const res = await fetch('/comando', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt}) });
                    const data = await res.json(); 
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); addToChatLog('Erro rede.', 'ia'); }
            }

            async function handleDeviceAction(device, action) {
                showTypingIndicator();
                try {
                    const res = await fetch('/device_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({device, action}) });
                    const data = await res.json(); 
                    if (data.response) addToChatLog(data.response, 'ia'); else removeTypingIndicator();
                } catch (e) { removeTypingIndicator(); }
            }

            function createToggle(device) {
                const container = getOrCreateRoomContainer(getRoomName(device));
                const div = document.createElement('div'); div.className = 'device-toggle'; div.title = device;
                div.dataset.state = 'unreachable'; div.dataset.type = 'toggle';
                const icon = document.createElement('span'); icon.className = 'device-icon'; icon.innerText = getDeviceIcon(device);
                const switchLabel = document.createElement('label'); switchLabel.className = 'switch';
                const input = document.createElement('input'); input.type = 'checkbox'; input.disabled = true;
                input.onchange = () => {
                    handleDeviceAction(device, input.checked ? 'ligar' : 'desligar');
                    div.dataset.state = input.checked ? 'on' : 'off';
                    if(input.checked) div.classList.add('active'); else div.classList.remove('active');
                };
                const slider = document.createElement('div'); slider.className = 'slider'; switchLabel.append(input, slider);
                const label = document.createElement('span'); label.className = 'device-label'; 
                label.innerText = device.split(' ').pop().substring(0,12);
                div.append(icon, switchLabel, label); container.appendChild(div);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'toggle', element: div, input: input, label: label });
            }
            
            function createSensor(device) {
                if(device.toLowerCase().includes('casa') || device.toLowerCase() === 'geral') return;
                const container = getOrCreateRoomContainer(getRoomName(device));
                const div = document.createElement('div'); div.className = 'device-sensor'; div.title = device;
                div.dataset.state = 'unreachable'; div.dataset.type = 'sensor';
                const dataSpan = document.createElement('span'); dataSpan.className = 'sensor-data'; dataSpan.innerText = '...';
                const label = document.createElement('span'); label.className = 'sensor-label'; 
                label.innerText = device.replace(/sensor|alarme/gi, '').trim().substring(0,12);
                div.append(dataSpan, label); container.appendChild(div);
                ALL_DEVICES_ELEMENTS.push({ name: device, type: 'sensor', element: div, dataSpan: dataSpan, label: label });
            }

            async function fetchDeviceStatus(item) {
                const { name, element, input, label } = item;
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
                    if (data.power_w > 0.5) {
                         label.innerText = `${Math.round(data.power_w)} W`; label.style.color = "#ffb74d";
                    } else {
                         label.innerText = name.split(' ').pop().substring(0,12); label.style.color = "#aaa";
                    }
                } catch (e) {}
            }
            
            async function fetchSensorStatus(item) {
                const { name, element, dataSpan } = item;
                try {
                    const res = await fetch(`/device_status?nickname=${encodeURIComponent(name)}`);
                    const data = await res.json();
                    if (data.state === 'unreachable') {
                        element.style.opacity = 0.35;
                        dataSpan.innerText = 'indisponível';
                        dataSpan.style.color = '#737373';
                        element.title = name + ' — indisponível';
                        return;
                    }
                    element.style.opacity = data.stale ? 0.45 : 1;
                    let measurements = [];
                    let color = '#4db6ac';
                    if (data.power_w !== undefined) {
                        measurements.push(Math.round(data.power_w) + ' W');
                        color = '#ffb74d';
                    }
                    if (data.temperature !== undefined) measurements.push(data.temperature + '°');
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
                    dataSpan.innerText = text;
                    dataSpan.style.color = parts.length ? color : '#737373';
                    const ageDesc = data.age_s !== undefined ? Math.round(data.age_s / 60) + ' min' : '?';
                    element.title = `${name} — última leitura há ${ageDesc}${data.stale ? ' (desatualizado)' : ''}`;
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
                    let wIcon = '☁️';
                    let ghostClass = 'ghost-normal';
                    if (wType === 1) { wIcon = '☀️'; ghostClass = 'ghost-sun'; }
                    else if (wType <= 5) { wIcon = '⛅'; ghostClass = 'ghost-normal'; }
                    else if (wType <= 15) { wIcon = '🌧️'; ghostClass = 'ghost-rain'; }
                    else if (wType >= 16) { wIcon = '🌫️'; ghostClass = 'ghost-normal'; }
                    document.getElementById('main-weather-icon').innerText = wIcon;
                    document.getElementById('main-weather-temp').innerText = `${Math.round(today.tMax)}°`;
                    const ghost = document.getElementById('brand-logo');
                    ghost.className = ''; ghost.classList.add(ghostClass);
                    let mIcon = '🌑'; const moon = data.moon_phase || "";
                    if (moon.includes("Crescente")) mIcon = '🌓'; else if (moon.includes("Cheia")) mIcon = '🌕'; else if (moon.includes("Minguante")) mIcon = '🌗';
                    document.getElementById('main-moon-icon').innerText = mIcon;
                    document.querySelector('.sky-element[title="Fase Lunar"]').title = moon || "Fase Lunar";
                    const aqi = data.aqi; const aqiEl = document.getElementById('aqi-indicator');
                    if (aqi !== undefined) {
                        if (aqi <= 50) { aqiEl.innerText = '🍃'; aqiEl.title = `AQI ${aqi} (Bom)`; }
                        else if (aqi <= 100) { aqiEl.innerText = '😷'; aqiEl.title = `AQI ${aqi} (Moderado)`; }
                        else { aqiEl.innerText = '☠️'; aqiEl.title = `AQI ${aqi} (Mau)`; }
                    } else { aqiEl.innerText = ''; }
                } catch(e) {}
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
                        if (devs && devs.length > 0) devs.forEach(d => { if (d.type === 'sensor') createSensor(d.name); else createToggle(d.name); });
                    }
                    updateHomePower(); updateWeather();
                    setInterval(() => {
                        ALL_DEVICES_ELEMENTS.forEach(i => i.type==='toggle'?fetchDeviceStatus(i):fetchSensorStatus(i));
                        updateHomePower(); 
                    }, 5000);
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

            /* Compact device view. Applied from localStorage BEFORE the tiles
               are built, not after: rendering into the default layout and then
               collapsing it shows a visible reflow and, on a phone, a scroll
               bar that disappears a frame later. */
            const DEV_COMPACT_KEY = 'phantasma.devcompact';
            function applyDeviceView(compact) {
                document.body.classList.toggle('dev-compact', compact);
                const btn = document.getElementById('dev-view-toggle');
                if (btn) {
                    btn.setAttribute('aria-pressed', compact ? 'true' : 'false');
                    btn.title = compact
                        ? 'Vista compacta: todos os dispositivos visiveis'
                        : 'Ver todos os dispositivos sem scroll';
                }
            }
            function initDeviceView() {
                let stored = null;
                try { stored = localStorage.getItem(DEV_COMPACT_KEY); } catch (e) {}
                applyDeviceView(stored === '1');
                const btn = document.getElementById('dev-view-toggle');
                if (!btn) return;
                btn.onclick = () => {
                    const next = !document.body.classList.contains('dev-compact');
                    applyDeviceView(next);
                    try { localStorage.setItem(DEV_COMPACT_KEY, next ? '1' : '0'); } catch (e) {}
                    updateDeviceScrollHint();
                };
            }
            initDeviceView();

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
            chatSend.onclick = sendChatCommand; 
            chatInput.onkeydown = (e) => { 
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatCommand(); }
                setTimeout(() => { chatInput.style.height = 'auto'; chatInput.style.height = chatInput.scrollHeight + 'px'; }, 0);
            };
            loadDevicesStructure(); loadHelp(); addToChatLog("Nas sombras, aguardo...", "ia");
            /* After the device tiles are in the DOM, not before -- measuring
               scrollHeight on an empty container would always report "fits". */
            setTimeout(updateDeviceScrollHint, 300);
        </script>
        <script>__SHARED_JS__</script>
    </body>
    </html>
    """).replace("__ADMIN_LINKS__", admin_links_html) \
        .replace("__SHARED_CSS__", _css) \
        .replace("__SHARED_JS__", _js)

def handle(user_prompt_lower, user_prompt_full):
    """UI skill doesn't handle voice commands - only registers web routes."""
    return None
