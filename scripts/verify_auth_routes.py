"""Authenticated-route verification.

Why this exists
---------------
The admin surface is protected by a passwordless OTP flow, so neither a test
suite nor a browser probe can reach it: there is no way to read the email. The
previous route audits therefore measured the login screen over and over and
could not see a single authenticated page -- which is why the "no /api traffic"
conclusion had to be hedged.

The loopback bypass (src/api/localauth.py) removes that blocker. This module is
the harness around it, so the capability is exercised deliberately and the same
way every time, instead of ad-hoc curl with a hand-written cookie.

Design constraints
------------------
* Uses the bypass, never a real credential. Nothing here can read a mailbox.
* Asserts the bypass is actually IN EFFECT before trusting any result. A
  verification that silently ran unauthenticated is worse than no verification.
* Never touches production data: the database is redirected to a temporary one
  built from tests/schema.sql.
* Read-only by default. Any mutating check must be requested explicitly.

Usage
-----
    python3 -m scripts.verify_auth_routes            # report
    python3 -m scripts.verify_auth_routes --strict   # non-zero on failure
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Every admin page that must render for an authenticated admin. /admin/dashboard
# is intentionally absent: it is a 301 to /admin/brain as of 2026-09-27.
# /admin redirects to /admin/ with a 308 (Flask's trailing-slash rule), so it
# is verified at its canonical form.
ADMIN_ROUTES = [
    "/admin/",
    "/admin/brain",
    "/admin/memory",
    "/admin/rag",
    "/admin/flybrain",
    "/admin/config",
    "/admin/users",
    "/admin/env",
]

# /api routes reachable without auth, used to confirm the bypass is not
# over-reaching into the public surface.
# "/" is NOT verified here: it is served by a skill registering a url rule at
# runtime, which the test client does not load. It is verified by Playwright
# against the live service instead -- see the note in the report.
PUBLIC_ROUTES = ["/help", "/api/info", "/api/health"]


def _isolated_app():
    """Build the Flask app against a throwaway database.

    Production data is never touched: the paths are redirected at import time,
    which is what the conftest fixture relies on too.
    """
    tmp = Path(tempfile.mkdtemp(prefix="pHantasma-verify-"))
    schema = (REPO / "tests" / "schema.sql").read_text(encoding="utf-8")
    cfg_db, brain_db = tmp / "config.db", tmp / "brain.db"
    for db in (cfg_db, brain_db):
        con = sqlite3.connect(db)
        con.executescript(schema)
        con.commit()
        con.close()
    con = sqlite3.connect(cfg_db)
    con.execute(
        "INSERT INTO users (email,password_hash,role,is_active) VALUES (?,?,?,1)",
        ("verify@localhost", "not-a-real-hash", "admin"),
    )
    con.execute("INSERT INTO config_categories (name,display_order) VALUES ('geral',1)")
    con.execute(
        "INSERT INTO config (category,key,value) VALUES ('geral','idioma','pt-PT')"
    )
    con.commit()
    con.close()

    os.environ["PHANTASMA_LOCAL_ADMIN_BYPASS"] = "1"
    from dotenv import load_dotenv

    load_dotenv()
    import config

    config.CONFIG_DB_PATH = str(cfg_db)
    config.BRAIN_DB_PATH = str(brain_db)
    config.config.config_db_path = str(cfg_db)
    config.config.brain_db_path = str(brain_db)

    import importlib

    from src.api import admin as A

    importlib.reload(A)
    A.CONFIG_DB_PATH = cfg_db
    A.BRAIN_DB_PATH = brain_db

    from src.api import routes as R

    app = R.create_app(pipeline=None)
    app.config["TESTING"] = True
    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any check fails")
    args = parser.parse_args()

    app = _isolated_app()
    failures: list[str] = []

    with app.test_client() as client:
        # Prove the bypass is live before trusting anything below it.
        probe = client.get("/admin/users", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        if probe.status_code != 200:
            print("  ABORT: the loopback bypass is not in effect; "
                  "results below would be meaningless", file=sys.stderr)
            return 2
        print("  ok  bypass confirmed active on loopback")

        print("\n  authenticated admin routes:")
        for path in ADMIN_ROUTES:
            r = client.get(path, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            size = len(r.get_data())
            # 200 = rendered, 301 = the dashboard's permanent redirect to
            # /admin/brain, 302 = the index redirecting into the brain hub.
            # All three mean "reachable and correctly wired". A 4xx/5xx does not.
            ok = r.status_code in (200, 301, 302)
            # A redirect is fine; a 4xx/5xx is not. An empty body on a 200 is
            # the "loads nothing" failure the owner reported, so it is a failure.
            if ok and r.status_code == 200 and size < 500:
                ok = False
                note = f"{size}B -- suspiciously empty"
            else:
                note = f"{r.status_code} {size}B"
            print(f"    {'ok ' if ok else 'FAIL'} {path:<20} {note}")
            if not ok:
                failures.append(path)

        print("\n  a LAN address must NOT be bypassed:")
        r = client.get("/admin/users", environ_overrides={"REMOTE_ADDR": "10.0.0.114"})
        ok = r.status_code == 302
        print(f"    {'ok ' if ok else 'FAIL'} 10.0.0.114 -> {r.status_code} "
              f"(expected 302 to login)")
        if not ok:
            failures.append("lan-not-bypassed")

        print("\n  public routes still reachable:")
        for path in PUBLIC_ROUTES:
            r = client.get(path, environ_overrides={"REMOTE_ADDR": "10.0.0.114"})
            ok = r.status_code == 200
            print(f"    {'ok ' if ok else 'FAIL'} {path:<20} {r.status_code}")
            if not ok:
                failures.append(path)

    print(f"\n  {len(failures)} failure(s)" + (f": {failures}" if failures else ""))
    if args.strict and failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
