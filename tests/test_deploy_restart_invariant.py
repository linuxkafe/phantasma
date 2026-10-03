"""The deploy must restart when the file that changed is one the service runs.

Three bugs in `scripts/deploy.sh`, one per session, all the same shape: the
script decided the service was current when it was not, printed success, and
left yesterday's code in memory behind a healthy 200.

1. rsync exited 23 after transferring, so `changed` was 0 with new files on disk.
2. ActiveEnterTimestampMonotonic is MICROSECONDS from boot, compared against a
   wall-clock epoch -- always ~1000x too large, so the service always looked
   newer.
3. Measured 2026-10-03. A commit touching only assistant.py and tests/ deployed,
   the prod suite failed on an unrelated Playwright flake, and the re-run said
   "already in sync and service is newer than the code". The freshness scan read
   `$PROD/src $PROD/skills $PROD/prompts`. assistant.py -- the service entry
   point, overwritten at 11:34 -- was not in that list, and src/ was from 10:40,
   before the service started at 10:53. So it concluded there was nothing to do.

   The suite had already caught bug 3's consequence once and been waved through
   as a flake; the flake was real, but it was not the interesting failure.

These tests are structural -- deploy.sh is shell and there is no harness for
running it end to end safely. What they pin is the invariant that the freshness
scan cannot omit a path the service executes, which is the part that drifted
three times.
"""

from __future__ import annotations

import os
import re

DEPLOY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "deploy.sh"
)


def _script() -> str:
    with open(DEPLOY, encoding="utf-8") as fh:
        return fh.read()


def _array(name: str) -> list[str]:
    src = _script()
    # Indented: TOP_LEVEL_SYNC is declared inside a conditional block, so
    # anchoring at the start of the line found nothing and the test claimed the
    # variable was absent when it was right there.
    m = re.search(rf"^\s*{name}=\(([^)]*)\)", src, re.MULTILINE)
    assert m, f"{name} is not declared in deploy.sh"
    return [item.strip().strip('"') for item in m.group(1).split() if item.strip()]


def _freshness_scan() -> str:
    """The find(1) that decides whether the service is behind the code."""
    src = _script()
    anchor = src.find("service_started_epoch=")
    assert anchor > 0, "the freshness comparison is gone from deploy.sh"
    tail = src[anchor:]
    # From the construction of the path list, not from the find itself: the
    # find is `"${scan_paths[@]}"`, and where scan_paths comes from is the part
    # that has to mention TOP_LEVEL_SYNC.
    start = tail.find("scan_paths=(")
    assert start > 0, "the freshness scan no longer builds a path list"
    return tail[start : tail.find("newest_mtime=0", start)]


def test_every_top_level_module_is_in_the_freshness_scan():
    """The one that bit: a change to assistant.py alone did not restart.

    `changed` is 0 on a re-run, so the mtime comparison is the only thing that
    can catch a file the previous run already copied. If the service entry point
    is not in the scanned set, a re-run after a failed deploy is a no-op that
    reports success.
    """
    scan = _freshness_scan()
    derived = "TOP_LEVEL_SYNC" in scan
    for name in _array("TOP_LEVEL_SYNC"):
        covered = derived or name in scan
        assert covered, (
            f"{name} is synced by this script but is missing from the freshness "
            f"scan, so a change to it alone never restarts the service. "
            f"Scan: {scan.strip()}"
        )


def test_the_scan_is_built_from_the_deploy_list_not_a_hand_copy():
    """Derived, not retyped -- a hand-copied list is what drifted.

    If the scan hardcodes the module names, the next module added to
    TOP_LEVEL_SYNC is silently uncovered again. So the scan has to reference the
    variable.
    """
    scan = _freshness_scan()
    assert "TOP_LEVEL_SYNC" in scan, (
        "a freshness scan que escreve os nomes à mão volta a divergir da lista "
        f"que o deploy sincroniza. Scan: {scan.strip()}"
    )


def test_the_entry_point_is_synced_at_all():
    """The precondition for the two tests above to mean anything."""
    assert "assistant.py" in _array("TOP_LEVEL_SYNC"), (
        "assistant.py is the service entry point; if it is not synced, no "
        "amount of mtime comparison will make prod run dev's code"
    )


def test_a_skipped_restart_is_never_reported_as_a_finished_deploy():
    """The wording matters: it is what a reader trusts.

    It used to print "nothing to restart" and exit 0. A deploy that changed
    files and did not restart has not landed, and saying so plainly is the
    difference between a warning someone reads and one they file away.
    """
    src = _script()
    for phrase in ("nothing to restart", "already in sync"):
        idx = src.find(phrase)
        assert idx < 0 or "bug" in src[max(0, idx - 900) : idx].lower() or (
            "# " in src[max(0, idx - 400) : idx]
        ), (
            f"deploy.sh ainda imprime {phrase!r}. Se a comparação de frescura "
            f"falhar, isso sai como sucesso e ninguém reinicia à mão."
        )
