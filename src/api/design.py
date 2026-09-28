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
  /* Centred. The admin templates set max-width:960px inline and nothing centred
     it, so the whole content column sat flush against the left edge and the nav
     toggle -- which is the last child of the bar -- landed 960px from the right
     edge of a 1920px screen. pdftools centres with `max-w-7xl mx-auto`; this is
     the same idea, and it is what puts the button on the right *visually*
     instead of trying to pin it to the viewport. */
  .container { margin-left:auto; margin-right:auto; }
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
  border-bottom:1px solid var(--border);
}
  .nav-toggle {
    /* Always visible, at every width. It used to be display:none and only
       appeared under max-width:900px, which left desktop with no menu control
       at all. Because the button is now shown everywhere, the panel it
       controls is collapsible everywhere too -- a toggle that does nothing
       above the breakpoint would be worse than hiding it. */
    /* NOT positioned. Modelled on pdftools/components/Navbar.tsx, where the
       button is a plain flex child at the end of a
       `flex justify-between items-center` bar. margin-left:auto pins it to the
       right end of the flow instead of the viewport.

       Both earlier attempts were wrong: position:absolute landed on the
       centred container's right edge (976px from the screen edge at 1920px
       wide), and position:fixed only reached the viewport because it had no
       filtered ancestor -- .nav-bar had backdrop-filter:blur(8px), which
       creates a containing block for fixed descendants. Layout is the fix; the
       positioning was the bug. */
    display:flex; margin-left:auto; flex:0 0 auto;
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
  /* The panel is a DROPDOWN anchored under the bar, not a full-screen overlay.
     It used to be position:fixed covering the viewport at every width, which
     produced a scrollbar on desktop (the links fit in a few hundred pixels) and
     a permanent 57px band of dead space above it. Anchoring to the bar removes
     both: the panel is exactly as tall as its links. */
  /* The toggle is position:fixed against the viewport, so the bar no longer
     has to reserve 60px for it. Reserving it left a 60px dead band on the right
     of every page. */
  .nav-bar { position:relative;}

    /* The full-height overlay is for collapsible panels only. A nav that
       opted out is inline content and must never be turned into a
       fixed overlay, or it covers the page it belongs to. */
  .nav-menu:not(.nav-menu-always) {
    flex-direction:column; align-items:stretch; gap:0;
    position:absolute; top:100%; right:0; z-index:70;
    min-width:200px; max-height:70vh; overflow-y:auto;
    padding:var(--sp-2);
    background:var(--surface); border:1px solid var(--border);
    border-radius:var(--radius); box-shadow:0 8px 24px rgb(0 0 0 / .45);
    display:none;                                /* closed by default */
  }
  .nav-menu.open { display:flex; }

  /* Opt-out for pages that have no toggle. The rule above hides every .nav-menu
     by default, which is right for the admin pages (a burger reveals it) and
     wrong for the device page, where the five admin links are always shown.
     Declared here rather than with !important from the page, so the override
     and the rule it overrides live together. */
  .nav-menu.nav-menu-always { display:flex !important; }
  /* The opt-out above forces `display` but leaves the geometry of a collapsible
     panel in place, and the mobile block turns that geometry into a full-height
     overlay (`position:fixed; inset:0` below 900px). A page that opts out
     therefore ended up with a 307px-tall fixed panel sitting ON TOP of its
     content: Playwright measured the four links at y=67/119/171/223 over a chat
     that starts at y=190, so a tap on the message text hit an admin link and
     navigated to /admin/login. "Always visible" has to mean inline-and-wrapped,
     so this resets the geometry as well as the display -- one rule, next to the
     one it completes, instead of a page-level !important fight. */
  .nav-menu.nav-menu-always {
    position:static; inset:auto;
    flex-direction:row; flex-wrap:wrap; align-items:center;
    max-height:none; min-width:0; overflow:visible;
    /* No border and no radius: an opted-out nav is a row of links, not a
       panel. Keeping the border here is what made the admin links on `/`
       read as an always-open menu box in the top-right corner. */
    border:0; border-radius:0;
    box-shadow:none; background:transparent; padding:0;
    margin-left:auto; gap:var(--sp-2);
  }
  .nav-menu.nav-menu-always .nav-link,
  .nav-menu.nav-menu-always .nav-group {
    width:auto; flex-direction:row; align-items:center;
  }

  /* Always-visible nav keeps the 44px touch floor at EVERY width. The
     min-height below the breakpoint covers the root page on a phone; without
     this the desktop layout let the links fall to 37px, which Playwright
     measured at 1280x900. 44px is the practical floor, not 24px: a 37px target
     is the size a thumb reliably misses. */
  /* The login form gets the 44px floor at EVERY width, not just on a phone.
     It was previously only inside the mobile media query, so at 1280 the input
     measured 1280x39 and the submit button 104x21 -- a wide, short field and a
     label-sized button. Playwright measured both. 44px is the practical floor. */
  .container input:not([type=checkbox]):not([type=radio]),
  .container select, .container textarea, .container button {
    min-height:44px;
    font-size:16px;   /* <16px makes iOS Safari zoom on focus */
  }
  /* Label-sized, not full width. An earlier pass set width:100% on the
     reasoning that a full-width primary action is standard on phones; it
     flattened the button into a generic rectangle and the owner reported the
     login screen as bland. Only the HEIGHT is forced, to the 44px touch
     floor -- a 21px button is one a thumb reliably misses. */
  .container form > button[type=submit] {
    min-width:44px;
    padding-left:var(--sp-5, 1.5rem);
    padding-right:var(--sp-5, 1.5rem);
  }
  .container label { display:block; margin-bottom:var(--sp-1); }
  .container h1 { font-size:var(--fs-h1); margin-top:0; }
  .container .subtitle { line-height:1.5; }

  .nav-menu-always .nav-link {
    min-height:44px;
    display:inline-flex;
    align-items:center;
    padding:6px 10px;
  }
  .nav-menu .nav-brand { margin:0 0 var(--sp-3) 0; }
  /* The links must stack top-to-bottom. .nav-group is a flex ROW by default
     (that is what lays the links out inline on wide screens), so width:100%
     alone did nothing: measured, 7 links collapsed into 3 rows. The group has
     to become a column whenever its menu is collapsed. */
  .nav-menu:not(.nav-menu-always) .nav-link,
  .nav-menu:not(.nav-menu-always) .nav-group { width:100%; }
  .nav-menu:not(.nav-menu-always) .nav-group { flex-direction:column; align-items:stretch; gap:0; }

  @media (max-width:900px) {
    /* Below 900px an anchored dropdown cannot also hold the device strip, so it
       becomes a full-height overlay -- but only here, which is the width where
       a scrollbar is expected rather than a defect. */
    .nav-menu:not(.nav-menu-always) {
      position:fixed; top:57px; left:0; right:0; bottom:0;
      max-height:none; min-width:0;
      border-radius:0; border-left:0; border-right:0; border-top:0;
      box-shadow:none; background:var(--bg-color);
      padding:var(--sp-4);
    }
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

  /* Measured on a 375px viewport: the page title sat at x=0, flush against the
     screen edge, because the admin templates hardcode max-width inline and
     nothing supplied horizontal breathing room at this width. */
  .page, .container { padding-left:var(--sp-4); padding-right:var(--sp-4); }
  .page-header { flex-direction:column; gap:var(--sp-2); }

  /* 44px is the practical touch floor (WCAG 2.2 AA minimum is 24px, but a
     39px field is the size a thumb reliably misses). The 39px measured here
     came from padding + border-box on a bare input. */
  .page input:not([type=checkbox]):not([type=radio]),
  .page select, .page textarea, .page button,
  /* /admin/login is the ONLY admin page that renders inside .container rather
     than .page, so the rules above never reached it. Measured on a 375px
     viewport: its email field was 343x39 and its submit button 104x21 -- a
     native-height control that a thumb reliably misses. Selector widened to
     .container so the login form gets the same 44px floor as every other
     admin form. */
  .container input:not([type=checkbox]):not([type=radio]),
  .container select, .container textarea, .container button {
    min-height:44px;
    font-size:16px;   /* <16px makes iOS Safari zoom on focus */
  }
  /* The button was native-width and native-height, i.e. sized to its label.
     Full width is correct here: it is the page's single call to action. */
  /* Label-sized, not full width. An earlier pass set width:100% on the
     reasoning that a full-width primary action is standard on phones; it
     flattened the button into a generic rectangle and the owner reported the
     login screen as bland. Only the HEIGHT is forced, to the 44px touch
     floor -- a 21px button is one a thumb reliably misses. */
  .container form > button[type=submit] {
    min-width:44px;
    padding-left:var(--sp-5, 1.5rem);
    padding-right:var(--sp-5, 1.5rem);
  }
  .page label, .container label { display:block; margin-bottom:var(--sp-1); }
  .container h1 { font-size:var(--fs-h1); margin-top:0; }
  .container .subtitle { line-height:1.5; }

  /* /admin/config measured 8988px tall on a 375px screen -- 13 screens of
     scrolling. The cause was the grid, not the fields: repeat(auto-fit,
     minmax(280px, 1fr)) collapses to ONE column at 375px, so ~60 config
     cards stacked end to end. One column is correct for a phone; the gap and
     the card padding are what made each row cost so much. */
  .cfg-grid { grid-template-columns: 1fr !important; gap: var(--sp-3) !important; }
  .cfg-card { padding: var(--sp-3) !important; }
  .cfg-card > div:first-child { margin-bottom: var(--sp-2) !important; }
  .cfg-input { min-height: 44px; font-size: 16px !important; }  /* inline sets 0.875rem; iOS zooms below 16px */
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
      // Labels come from the element so this shared script does not force
      // English on a Portuguese UI. data-label-* wins; the English strings
      // remain only as a last-resort default for a caller that sets neither.
      toggle.setAttribute(
        'aria-label',
        open ? (toggle.dataset.labelClose || 'Close menu')
             : (toggle.dataset.labelOpen || 'Open menu')
      );
    }

    setOpen(false);

    toggle.addEventListener('click', function () {
      setOpen(!menu.classList.contains('open'));
    });

    // Tapping a link should dismiss the overlay, otherwise the menu covers the
    // page the user just asked for. Not breakpoint-gated any more: the panel is
    // collapsible at every width, so gating it would leave the overlay covering
    // the destination on desktop.
    menu.addEventListener('click', function (ev) {
      if (ev.target.closest('a')) setOpen(false);
    });

    // Click-outside to dismiss, ported from pdftools/components/Navbar.tsx
    // (handleClickOutside on mousedown, guarded by a ref around button+menu).
    // We only had Escape and link-click, so on desktop -- where the panel is a
    // small dropdown, not a full overlay -- a stray click left it open with no
    // obvious way out.
    document.addEventListener('mousedown', function (ev) {
      if (!menu.classList.contains('open')) return;
      if (menu.contains(ev.target) || toggle.contains(ev.target)) return;
      setOpen(false);
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

// Sleep & Dream trigger for the admin pages.
//
// The endpoint answers 202 with status "started": every step calls the LLM, so
// the cycle takes minutes and cannot be waited out inside the request. This
// version treated every status other than "ok" as a failure, so a cycle that
// was genuinely running raised "Error: Sleep/dream cycle started in
// background" -- a success reported as a failure. Start, then follow
// /admin/brain/sleep/status until the run reaches a terminal state.
const SLEEP_STEP_LABELS = {
  classify_edges: 'classificar arestas',
  classify_nodes: 'classificar nos',
  consolidate_memories: 'consolidar memorias',
  gmif_dream: 'sonhar (GMIF)'
};
function sleepStatusLine() {
  let line = document.getElementById('sleep-status-line');
  if (!line) {
    line = document.createElement('div');
    line.id = 'sleep-status-line';
    line.className = 'brain-note';
    (document.querySelector('.brain-panel.is-active') || document.body).appendChild(line);
  }
  return line;
}
function sleepButtons() { return document.querySelectorAll('.brain-panel.is-active [data-sleep]'); }
function resetSleepButtons() {
  sleepButtons().forEach(b => { b.disabled = false; b.textContent = 'Dormir e Sonhar'; });
}
function renderSleepStatus(d) {
  const steps = (d && d.steps) || {};
  const names = Object.keys(steps);
  const parts = names.map(n => {
    const ok = steps[n] && steps[n].ok;
    const mark = ok === true ? 'ok' : (ok === false ? 'FALHOU' : 'a correr');
    return (SLEEP_STEP_LABELS[n] || n) + ': ' + mark;
  });
  const line = sleepStatusLine();
  const failed = names.filter(n => steps[n] && steps[n].ok === false);
  line.textContent = 'Ciclo em curso - ' + names.length + ' passo(s): ' + (parts.join(' | ') || 'a iniciar');
  line.style.color = failed.length ? 'var(--destructive)' : '';
}
function pollSleep() {
  fetch('/admin/brain/sleep/status', {credentials: 'same-origin'})
    .then(r => r.json())
    .then(d => {
      if (d.status === 'running') { renderSleepStatus(d); setTimeout(pollSleep, 3000); return; }
      if (d.status === 'done') { location.reload(); return; }
      sleepStatusLine().textContent = 'Ciclo terminou com estado: ' + (d.status || 'desconhecido');
      resetSleepButtons();
    })
    .catch(err => { sleepStatusLine().textContent = 'Erro ao consultar o estado: ' + err; });
}
function triggerSleep() {
  if (!confirm('Iniciar o ciclo de sono/sonho? Cada passo chama o LLM e demora minutos.')) return;
  sleepButtons().forEach(b => { b.disabled = true; b.textContent = 'A dormir...'; });
  fetch('/admin/brain/sleep', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'same-origin'
  })
    .then(r => r.json().then(d => ({http: r.status, data: d})))
    .then(({http, data}) => {
      if (http !== 202 && data.status !== 'started') {
        // A genuine refusal: not allowlisted, already running, or a crash.
        sleepStatusLine().textContent = 'Erro: ' + (data.message || ('HTTP ' + http));
        resetSleepButtons();
        return;
      }
      sleepStatusLine().textContent = 'Ciclo iniciado. Acompanhando...';
      pollSleep();
    })
    .catch(err => {
      sleepStatusLine().textContent = 'Erro de rede: ' + err;
      resetSleepButtons();
    });
}
"""


def design_js() -> str:
    """Return the shared responsive-nav script injected into every admin page."""
    return _JS
