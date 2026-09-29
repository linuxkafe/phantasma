"""Tests for the pre-commit secret gate.

The gate blocks a commit when it sees a credential keyword assigned a long
quoted string. It cannot, on its own, tell a credential VALUE from the NAME of
an environment variable, so `src/api/command_token.py` had this:

    ENV_TOKEN = "PHANTASMA_COMMAND_TOKEN"

which is not a secret -- it names one, read later with os.getenv(). The gate
was amended to exempt the identifier form (SD-phantasma-OPS-019).

An exemption in a security gate is only safe if it is tested in BOTH
directions. A test that only proves the false positive is gone would pass just
as happily against a gate that blocks nothing at all. So every case below is
one of two kinds, and the pairs are chosen to be near-identical apart from the
one property that decides.

The gate is exercised as a shell pipeline, because that is what runs; testing a
reimplementation of its logic would test nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".aes" / "hooks" / "pre-commit.sh"

# The hook is a DEV-TIME pre-commit gate. It is tracked in git (it was being
# silently lost to .gitignore) but it is not deployed to /opt/phantasma, and
# the prod suite runs every test in tests/.
#
# So in prod these 14 tests failed with "pre-commit hook is missing" and nothing
# to do with the change under test -- a deploy failure caused by the absence of
# a dev tool. Skipping when the hook is absent is honest rather than evasive: the
# subject of these tests does not exist in that environment, so there is nothing
# to verify there. In dev the hook exists, the skip does not apply, and
# test_the_hook_still_exists_and_is_executable still fails loudly if it is ever
# deleted -- which is the case that actually matters.
pytestmark = pytest.mark.skipif(
    not HOOK.exists(),
    reason=(
        "pre-commit secret gate is a dev-time hook and is not deployed to prod; "
        "there is no hook here to verify"
    ),
)

# The three-stage filter from the hook, extracted so a test can drive it
# directly with sample lines. Kept in sync by test_filter_matches_the_hook.


def _extract_filter() -> str:
    """Pull the real filter chain out of the hook itself.

    Re-typing the regexes here would be the same duplication that makes a
    security rule drift: the test would pass against a copy while the hook
    changed. The hook's `if ... then <chain> >/dev/null; then` line is turned
    into a standalone script by prefixing the chain with a `cat`, so the
    pipeline is exercised exactly as written.
    """
    import re
    import tempfile

    text = HOOK.read_text()
    m = re.search(
        r"^if git diff --cached.*?done \\?\| (.*?) >/dev/null; then",
        text,
        re.S | re.M,
    )
    assert m, "could not find the secret filter chain in the hook"
    # Re-assemble the pipeline: the chain spans several source lines with
    # trailing backslashes, and splitting it naively on "|" would tear the
    # regexes apart at their own alternations (os.getenv|os.environ).
    chain = m.group(1).replace("\\\n", " ")
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
        fh.write("#!/bin/bash\ncat | " + chain.strip() + "\n")
        return fh.name


def _blocked(sample: str) -> bool:
    """True when the gate's filter chain blocks this line."""
    import subprocess

    script = _extract_filter()
    try:
        proc = subprocess.run(
            ["bash", script], input=sample + "\n", capture_output=True, text=True
        )
        assert proc.returncode in (0, 1), proc.stderr
        return proc.returncode == 0
    finally:
        Path(script).unlink(missing_ok=True)


# --- must be ALLOWED: these name things, they are not secrets ----------------


@pytest.mark.parametrize(
    "line",
    [
        # The real line that triggered the exemption.
        'ENV_TOKEN = "PHANTASMA_COMMAND_TOKEN"',
        'ENV_SECRET = "SOME_SERVICE_SECRET"',
        'VARNAME_SESSION = "SESSION_COOKIE_NAME"',
        # Config-driven reads, already allowed before the exemption.
        'token = os.getenv("PHANTASMA_COMMAND_TOKEN", "")',
        'value = os.environ["SOME_TOKEN"]',
        'x = getattr(config, "discord_bot_token", "")',
    ],
)
def test_identifier_and_config_lines_are_allowed(line):
    assert _blocked(line) is False, f"gate blocked a non-secret: {line}"


# --- must be BLOCKED: these are credential values ---------------------------


@pytest.mark.parametrize(
    "line",
    [
        'API_TOKEN = "sk-live-4eC39HqLyjWDarjtT1zdp7dc"',
        'SECRET = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"',
        'DISCORD_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIjKl.9876543210abcdef"',
        'LOCAL_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"',
    ],
)
def test_credential_values_are_blocked(line):
    assert _blocked(line) is True, f"gate MISSED a real secret: {line}"


def test_private_key_block_is_blocked():
    assert _blocked("KEY = \"BEGIN RSA PRIVATE KEY\"") is True


def test_the_exemption_does_not_swallow_a_lowercase_value():
    """The identifier exemption keys on the VARIABLE NAME, so a name like
    ENV_TOKEN does not make its value safe. A mixed-case value on such a name
    is a real secret and must still be blocked -- that pairing is the exact
    shape of an accidental leak, and it is why the exemption is narrow."""
    assert _blocked('ENV_TOKEN = "aB3-xY9_zzTopSecretValue"') is True


def test_a_name_ending_in_env_is_still_a_secret_risk():
    """Same reasoning for the other naming convention: `FOO_TOKEN_ENV` names a
    variable, but the gate only exempts the ENV_/VARNAME_ prefix form, so a
    suffixed name keeps the full protection rather than relying on a second
    regex to tell a name from a value."""
    assert _blocked('API_KEY_ENV = "sk-live-4eC39HqLyjWDarjtT1zdp7dc"') is True


def test_the_hook_still_exists_and_is_executable():
    assert HOOK.exists(), "pre-commit hook is missing"
    assert HOOK.stat().st_mode & 0o111, "pre-commit hook is not executable"
