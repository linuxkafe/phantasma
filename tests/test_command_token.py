"""Tests for the command bearer token.

A token is a secret, so the tests are about the ways this can go wrong rather
than the ways it works:

* accepting an empty or unset token (fail open);
* comparing with `==` and leaking the token byte by byte;
* accepting the token from a query string, where it would be written to the
  access log;
* one leaked token granting more than "send a command".
"""

from __future__ import annotations

import pytest

from src.api import command_token


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(command_token.ENV_TOKEN, raising=False)


def test_unset_token_disables_the_feature(monkeypatch):
    """Fail closed on configuration, not open. With no token set, `verify` must
    reject everything -- including an empty string, which is what a client that
    does not know about auth would send."""
    assert command_token.enabled() is False
    assert command_token.verify("") is False
    assert command_token.verify(None) is False
    assert command_token.verify("anything") is False


def test_configured_token_verifies(monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    assert command_token.enabled() is True
    assert command_token.verify("s3cret-token") is True
    assert command_token.verify("s3cret-toke") is False
    assert command_token.verify("s3cret-tokens") is False
    assert command_token.verify("S3CRET-TOKEN") is False  # case sensitive
    assert command_token.verify("") is False


def test_token_is_whitespace_tolerant_but_the_secret_is_not(monkeypatch):
    """A trailing newline in a .env must not become part of the secret."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "  s3cret \n")
    assert command_token.verify("s3cret") is True


def test_comparison_is_constant_time(monkeypatch):
    """`==` returns at the first difference, so it leaks the prefix length.
    The module must compare with hmac.compare_digest."""
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    src = open(command_token.__file__).read()
    assert "compare_digest" in src
    assert "token ==" not in src
    assert "== token" not in src


def test_bearer_header_is_accepted(monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    assert command_token.check_header("Bearer s3cret-token") is True
    assert command_token.check_header("bearer s3cret-token") is True  # scheme
    assert command_token.check_header("Bearer  s3cret-token") is True


def test_other_auth_schemes_are_refused(monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    assert command_token.check_header("Basic s3cret-token") is False
    assert command_token.check_header("Token s3cret-token") is False
    assert command_token.check_header("s3cret-token") is False  # no scheme
    assert command_token.check_header("Bearer") is False
    assert command_token.check_header("Bearer ") is False
    assert command_token.check_header(None) is False
    assert command_token.check_header("") is False


def test_describe_never_leaks_the_token(monkeypatch):
    monkeypatch.setenv(command_token.ENV_TOKEN, "s3cret-token")
    blob = repr(command_token.describe())
    assert "s3cret-token" not in blob
    assert command_token.describe()["enabled"] is True


def test_token_is_read_at_call_time_not_import_time(monkeypatch):
    """The admin config editor writes env values; a token cached at import
    would need a restart to take effect."""
    assert command_token.enabled() is False
    monkeypatch.setenv(command_token.ENV_TOKEN, "later")
    assert command_token.enabled() is True
