"""Localhost authentication bypass -- for testing and local administration.

What it does
------------
Admin routes are protected by a passwordless OTP flow, which is correct for
production and unworkable for automated verification: a test suite or a
Playwright probe has no way to read an email. This module lets a request that
arrives from the loopback interface be treated as an authenticated admin.

Why it is opt-in
-----------------
This is an authentication bypass, so it is OFF unless
``PHANTASMA_LOCAL_ADMIN_BYPASS=1``. Defaulting it on would mean that anyone who
can reach the service's loopback interface is an administrator, silently.

The threat model, stated plainly
--------------------------------
The service listens on ``0.0.0.0:5000`` and is reachable from the internet
through a router port-forward. Loopback traffic therefore includes:

* anything running as any local user on this host;
* every other service on the host, including anything that can be induced to
  make a request (SSRF in a dependency, a script, a container on a bridge
  network -- ``172.17.0.0/16`` is not loopback, but a container with a
  published port to 127.0.0.1 is);
* anything that can write to the access log or the .env.

So this is safe ONLY on a host you trust to be single-tenant, and it does not
protect against a local attacker. That is a deliberate trade for being able to
verify the authenticated surface at all.

Controls
--------
* Off unless the env var is exactly ``1``.
* Loopback addresses only -- ``127.0.0.0/8`` and ``::1``. NOT RFC1918. A
  private LAN address is remote, and the service is scanned from one.
* Every bypass is logged at WARNING with the address and the path, so it cannot
  be used invisibly. An audit that cannot see its own exceptions is not an
  audit.
* The bypass identifies a user for rendering; it does not create a row in the
  users table, so it cannot silently alter the user store.
"""

from __future__ import annotations

import ipaddress
import logging
import os

logger = logging.getLogger("phantasma.auth")

ENV_FLAG = "PHANTASMA_LOCAL_ADMIN_BYPASS"
BYPASS_EMAIL = "local@localhost"
BYPASS_ROLE = "admin"


def enabled() -> bool:
    """True only when the flag is set to exactly '1'."""
    return os.getenv(ENV_FLAG, "").strip() == "1"


def is_loopback(addr: str | None) -> bool:
    """True for loopback only.

    Deliberately NOT "private": 10.0.0.0/8, 172.16/12 and 192.168/16 are remote
    networks, and this service has been probed from one. A bypass keyed on
    "private" would hand admin to every scanner on the LAN.
    """
    if not addr:
        return False
    # Strip an IPv6 zone / brackets if present.
    candidate = addr.strip().strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return ip.is_loopback


def bypass_identity(addr: str | None) -> dict | None:
    """Return an admin identity if this request may bypass auth, else None."""
    if not enabled():
        return None
    if not is_loopback(addr):
        return None
    return {
        "email": BYPASS_EMAIL,
        "role": BYPASS_ROLE,
        "is_active": 1,
        "via_bypass": True,
    }


def describe() -> dict:
    """Operational description, for the health endpoint and for tests."""
    return {
        "enabled": enabled(),
        "scope": "loopback only (127.0.0.0/8, ::1) -- never RFC1918",
        "env_flag": ENV_FLAG,
        "identity": BYPASS_EMAIL,
        "warning": "grants admin to any local process; log every bypass",
    }
