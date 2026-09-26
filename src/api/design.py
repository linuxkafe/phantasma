"""Design system for the Phantasma admin UI.

Ported from an external institutional design system (Inter, the ``#009FDF``
brand scale, 4px spacing scale, 8px radius, light/dark surfaces) and
re-implemented as plain CSS custom properties + class rules, because the admin
is server-rendered Flask + Jinja rather than React + Tailwind.

Deliberately NOT carried over from the reference, to keep this project free of
any third-party institutional branding:

* the footer component (it hardcodes an organisation name, a postal address, a
  support e-mail and external privacy-policy links);
* the brand/wordmark component;
* the package name and any third-party install or usage instructions.

Everything else — colour roles, type scale, spacing, radii, and the visual
language of Button / Card / StatCard / SegmentedControl / Toggle / Table — is
preserved so the result is recognisably the same design family.

``FORBIDDEN_BRANDS`` is the machine-checked guard: tests assert none of these
strings appear in this stylesheet or in any admin template.
"""

from __future__ import annotations

# Kept as a module constant so tests can assert the U.Porto strings are gone.
FORBIDDEN_BRANDS = ("uporto", "universidade do porto", "updigital", "up.pt")


def design_css() -> str:
    """Return the full stylesheet injected into every admin page."""
    return _CSS


_CSS = r"""
/* ============================================================
   Phantasma design system
   Language: dark-first, brand #009FDF, Inter, 4px scale, r=8px
   ============================================================ */
:root {
  /* brand scale */
  --brand-50:#e0f7ff;  --brand-100:#b3ecff; --brand-200:#80dfff;
  --brand-300:#4dd2ff; --brand-400:#26c6ff; --brand-500:#009FDF;
  --brand-600:#0082b3; --brand-700:#006688; --brand-800:#004a5e;
  --brand-900:#002e3b;

  /* semantic roles - dark is the default surface */
  --bg-color:#0a0a0a;
  --surface:#171717;
  --surface-2:#1f1f1f;
  --border:#262626;
  --border-strong:#333333;
  --text:#fafafa;
  --text-secondary:#d4d4d4;
  --muted:#737373;
  --brand:#009FDF;
  --brand-hover:#26c6ff;
  --brand-contrast:#002e3b;
  --accent:#22c55e;
  --destructive:#ef4444;
  --warning:#eab308;
  --info:#3b82f6;

  /* type scale */
  --font-sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --font-mono:"SF Mono","Fira Code",ui-monospace,monospace;
  --fs-display:32px; --fs-h1:24px; --fs-h2:20px; --fs-body:14px;
  --fs-small:12px; --fs-mono:13px;
  --fw-display:700; --fw-h1:600; --fw-h2:600; --fw-body:400;

  /* spacing (4px base) + radius */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px;
  --sp-6:24px; --sp-8:32px; --sp-10:40px; --sp-12:48px; --sp-16:64px;
  --radius:8px; --radius-sm:6px; --radius-lg:12px;

  --shadow-1:0 1px 2px rgba(0,0,0,.4);
  --shadow-2:0 4px 12px rgba(0,0,0,.45);
  --focus-ring:0 0 0 2px var(--bg-color),0 0 0 4px var(--brand);
}

* { box-sizing:border-box; }

body {
  margin:0;
  background:var(--bg-color);
  color:var(--text);
  font-family:var(--font-sans);
  font-size:var(--fs-body);
  font-weight:var(--fw-body);
  line-height:1.5;
  -webkit-font-smoothing:antialiased;
}

h1 { font-size:var(--fs-h1); font-weight:var(--fw-h1); margin:0 0 var(--sp-4); }
h2 { font-size:var(--fs-h2); font-weight:var(--fw-h2); margin:0 0 var(--sp-3); }
a  { color:var(--brand); text-decoration:none; }
a:hover { color:var(--brand-hover); text-decoration:underline; }
code, pre, .mono { font-family:var(--font-mono); font-size:var(--fs-mono); }

::selection { background:var(--brand-500); color:var(--brand-contrast); }

:focus-visible { outline:none; box-shadow:var(--focus-ring); border-radius:var(--radius-sm); }

/* ---------------- layout ---------------- */
.page { max-width:1200px; margin:0 auto; padding:var(--sp-6) var(--sp-6) var(--sp-16); }
.page-header { display:flex; align-items:flex-start; justify-content:space-between;
               gap:var(--sp-4); flex-wrap:wrap; margin-bottom:var(--sp-6); }
.page-title  { font-size:var(--fs-display); font-weight:var(--fw-display);
               margin:0; letter-spacing:-.02em; }
.page-subtitle { color:var(--muted); margin:var(--sp-1) 0 0; font-size:var(--fs-body); }

.grid { display:grid; gap:var(--sp-4); }
.grid-stats { grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }
.grid-2 { grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }

/* ---------------- top navigation ---------------- */
.nav-bar {
  position:sticky; top:0; z-index:50;
  background:color-mix(in srgb, var(--bg-color) 88%, transparent);
  backdrop-filter:blur(8px);
  border-bottom:1px solid var(--border);
}
.nav-menu {
  display:flex; align-items:center; gap:var(--sp-1); flex-wrap:wrap;
  background:transparent;
  border-bottom:0;
  padding:var(--sp-2) var(--sp-6);
}
.nav-brand {
  display:flex; align-items:center; gap:var(--sp-2);
  font-weight:var(--fw-h2); margin-right:var(--sp-4); white-space:nowrap;
}
.nav-brand .mark {
  width:26px; height:26px; border-radius:var(--radius-sm);
  background:linear-gradient(135deg,var(--brand-400),var(--brand-600));
  display:grid; place-items:center; color:var(--brand-contrast);
  font-weight:var(--fw-display); font-size:14px;
}
.nav-group { display:flex; align-items:center; gap:var(--sp-1); }
.nav-link {
  display:inline-flex; align-items:center; gap:var(--sp-2);
  padding:var(--sp-2) var(--sp-3);
  border-radius:var(--radius);
  color:var(--text-secondary); font-weight:500;
  white-space:nowrap; transition:background .12s ease,color .12s ease;
}
.nav-link:hover { background:var(--surface-2); color:var(--text); text-decoration:none; }
.nav-link.active { background:var(--brand-500); color:var(--brand-contrast); font-weight:600; }
.nav-spacer { flex:1 1 auto; }
.nav-sep { width:1px; height:20px; background:var(--border-strong); margin:0 var(--sp-2); }

/* ---------------- responsive hamburger ----------------
   The bar wraps the toggle and the menu so the toggle stays put while the
   menu becomes a full-screen overlay. Hidden above 900px, where the links fit
   inline; below it they would wrap into an unreadable multi-row block. */
.nav-bar {
  position:sticky; top:0; z-index:50;
  background:color-mix(in srgb, var(--bg-color) 88%, transparent);
  backdrop-filter:blur(8px);
  border-bottom:1px solid var(--border);
}
.nav-toggle {
  display:none; position:absolute; top:var(--sp-2); right:var(--sp-3); z-index:60;
  width:44px; height:44px;                 /* 44px minimum touch target */
  padding:0;
  border:1px solid var(--border); border-radius:var(--radius);
  background:var(--surface-2); cursor:pointer;
  align-items:center; justify-content:center; flex-direction:column; gap:4px;
}
.nav-toggle span {
  display:block; width:18px; height:2px; border-radius:2px;
  background:var(--text); transition:transform .16s ease, opacity .16s ease;
}
.nav-toggle[aria-expanded="true"] span:nth-child(1) { transform:translateY(6px) rotate(45deg); }
.nav-toggle[aria-expanded="true"] span:nth-child(2) { opacity:0; }
.nav-toggle[aria-expanded="true"] span:nth-child(3) { transform:translateY(-6px) rotate(-45deg); }
.nav-toggle:focus-visible { outline:2px solid var(--brand-400); outline-offset:2px; }

@media (max-width:900px) {
  .nav-toggle { display:flex; }
  .nav-bar { padding-right:60px; }          /* room for the absolute toggle */
  .nav-menu {
    flex-direction:column; align-items:stretch; gap:0;
    position:fixed; top:57px; left:0; right:0; bottom:0;
    overflow-y:auto; padding:var(--sp-4);
    background:var(--bg-color);
    display:none;                              /* closed by default */
  }
  .nav-menu.open { display:flex; }
  .nav-menu .nav-brand { margin:0 0 var(--sp-3) 0; }
  .nav-menu .nav-link,
  .nav-menu .segmented,
  .nav-menu .nav-group { width:100%; }
  .nav-menu .nav-link {
    /* 48px row height: comfortably above the 44px touch-target floor */
    min-height:48px; justify-content:flex-start;
    border-radius:var(--radius); font-size:var(--fs-body);
  }
  .nav-menu .nav-spacer,
  .nav-menu .nav-sep { display:none; }
  .nav-menu .segmented { flex-wrap:wrap; }
}

/* ---------------- Cérebro sub-navigation (tabs) ---------------- */
.subnav { display:flex; gap:var(--sp-1); flex-wrap:wrap;
          border-bottom:1px solid var(--border); margin-bottom:var(--sp-6); }
.subnav-link {
  padding:var(--sp-3) var(--sp-4);
  color:var(--muted); font-weight:500;
  border-bottom:2px solid transparent; margin-bottom:-1px;
}
.subnav-link:hover { color:var(--text); text-decoration:none; }
.subnav-link.active { color:var(--brand-400); border-bottom-color:var(--brand-500); font-weight:600; }

/* ---------------- cards ---------------- */
.card {
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:var(--radius-lg);
  padding:var(--sp-5);
}
.card-title { font-size:var(--fs-h2); font-weight:var(--fw-h2); margin:0 0 var(--sp-4); }
.card-scroll { max-height:420px; overflow-y:auto; display:flex; flex-direction:column; gap:var(--sp-3); }

/* StatCard (mirrors the reference component's variants) */
.stat { display:flex; flex-direction:column; gap:var(--sp-1); }
.stat-label { font-size:var(--fs-small); color:var(--muted); }
.stat-value { font-size:var(--fs-display); font-weight:var(--fw-display);
              letter-spacing:-.02em; line-height:1.1; }
.stat--success .stat-value, .stat--success .stat-label { color:var(--accent); }
.stat--error   .stat-value, .stat--error   .stat-label { color:var(--destructive); }
.stat--warning .stat-value, .stat--warning .stat-label { color:var(--warning); }
.stat--info    .stat-value, .stat--info    .stat-label { color:var(--info); }

/* ---------------- buttons ---------------- */
.btn {
  display:inline-flex; align-items:center; justify-content:center; gap:var(--sp-2);
  padding:var(--sp-2) var(--sp-4);
  border:1px solid transparent; border-radius:var(--radius);
  font-family:inherit; font-size:var(--fs-body); font-weight:600;
  cursor:pointer; white-space:nowrap;
  transition:background .12s ease,border-color .12s ease,color .12s ease;
}
.btn--primary   { background:var(--brand-500); color:var(--brand-contrast); }
.btn--primary:hover { background:var(--brand-400); }
.btn--secondary { background:var(--surface-2); color:var(--text); border-color:var(--border-strong); }
.btn--secondary:hover { background:var(--border); }
.btn--ghost     { background:transparent; color:var(--text-secondary); }
.btn--ghost:hover { background:var(--surface-2); color:var(--text); }
.btn--destructive { background:var(--destructive); color:#fff; }
.btn--sm { padding:var(--sp-1) var(--sp-3); font-size:var(--fs-small); }
.btn--lg { padding:var(--sp-3) var(--sp-6); font-size:var(--fs-h2); }
.btn[disabled] { opacity:.5; cursor:not-allowed; }

/* ---------------- SegmentedControl (language switch) ---------------- */
.segmented {
  display:inline-flex; padding:2px; gap:2px;
  background:var(--surface-2);
  border:1px solid var(--border-strong);
  border-radius:var(--radius);
}
.segmented button {
  padding:4px var(--sp-3);
  font:inherit; font-size:var(--fs-small); font-weight:600;
  color:var(--muted); background:transparent;
  border:none; border-radius:var(--radius-sm); cursor:pointer;
  transition:background .12s ease,color .12s ease;
}
.segmented button:hover { color:var(--text); }
.segmented button[aria-pressed="true"] {
  background:var(--surface); color:var(--text); box-shadow:var(--shadow-1);
}

/* ---------------- badges / tables ---------------- */
.badge {
  display:inline-flex; align-items:center; gap:var(--sp-1);
  padding:2px var(--sp-2); border-radius:999px;
  font-size:var(--fs-small); font-weight:600;
  background:var(--surface-2); color:var(--text-secondary);
  border:1px solid var(--border-strong);
}
.badge--brand { background:var(--brand-900); color:var(--brand-300); border-color:var(--brand-700); }
.badge--ok    { background:color-mix(in srgb,var(--accent) 18%,transparent);
               color:var(--accent); border-color:var(--accent); }
.badge--warn  { background:color-mix(in srgb,var(--warning) 18%,transparent);
               color:var(--warning); border-color:var(--warning); }
.badge--err   { background:color-mix(in srgb,var(--destructive) 18%,transparent);
               color:var(--destructive); border-color:var(--destructive); }

table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:var(--sp-3); border-bottom:1px solid var(--border); }
th { color:var(--muted); font-size:var(--fs-small); font-weight:600;
     text-transform:uppercase; letter-spacing:.04em; }
tbody tr:hover { background:var(--surface-2); }

.empty { color:var(--muted); text-align:center; padding:var(--sp-10) var(--sp-4);
         border:1px dashed var(--border-strong); border-radius:var(--radius-lg); }

input, select, textarea {
  width:100%; padding:var(--sp-2) var(--sp-3);
  background:var(--bg-color); color:var(--text);
  border:1px solid var(--border-strong); border-radius:var(--radius);
  font:inherit;
}
input:focus, select:focus, textarea:focus { border-color:var(--brand-500); }

.flash { padding:var(--sp-3) var(--sp-4); border-radius:var(--radius);
         margin-bottom:var(--sp-4); border:1px solid; }
.flash--ok   { background:color-mix(in srgb,var(--accent) 14%,transparent);
               border-color:var(--accent); color:var(--accent); }
.flash--err  { background:color-mix(in srgb,var(--destructive) 14%,transparent);
               border-color:var(--destructive); color:var(--destructive); }

/* ---------------- misc ---------------- */
.scroll { max-height:420px; overflow-y:auto; }
.muted { color:var(--muted); }
.mono-block { background:var(--bg-color); border:1px solid var(--border);
              border-radius:var(--radius-sm); padding:var(--sp-3);
              white-space:pre-wrap; word-break:break-word; }

@media (max-width:640px) {
  .page { padding:var(--sp-4) var(--sp-3) var(--sp-12); }
  .nav-menu { padding:var(--sp-2) var(--sp-3); }
  .page-title { font-size:var(--fs-h1); }
}

@media (prefers-reduced-motion:reduce) {
  * { animation-duration:.01ms !important; transition-duration:.01ms !important; }
}
"""

# Injected into every admin page. Small, dependency-free, and idempotent: it
# binds to whatever nav it finds and degrades to nothing if there is no toggle.
_JS = r"""
(function () {
  function wire(toggle, menu) {
    if (!toggle || !menu || toggle.dataset.wired === '1') return;
    toggle.dataset.wired = '1';

    var mq = window.matchMedia('(max-width: 900px)');

    function setOpen(open) {
      menu.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    }

    setOpen(false);

    toggle.addEventListener('click', function () {
      setOpen(!menu.classList.contains('open'));
    });

    // Tapping a link should dismiss the overlay, otherwise the menu covers the
    // page the user just asked for.
    menu.addEventListener('click', function (ev) {
      if (ev.target.closest('a') && mq.matches) setOpen(false);
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && menu.classList.contains('open')) {
        setOpen(false);
        toggle.focus();
      }
    });

    // Leaving the mobile breakpoint must not strand a hidden menu.
    var onChange = function () { if (!mq.matches) setOpen(false); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  function init() {
    document.querySelectorAll('.nav-toggle').forEach(function (toggle) {
      var menu = document.getElementById(toggle.getAttribute('aria-controls'));
      wire(toggle, menu || document.querySelector('.nav-menu'));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def design_js() -> str:
    """Return the shared responsive-nav script injected into every admin page."""
    return _JS
