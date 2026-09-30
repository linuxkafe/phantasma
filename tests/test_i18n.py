"""The test this module's docstring has been promising since it was written.

``src/api/i18n.py`` says, in two places, that a missing translation is "a visible
test failure rather than a silent fallback to the key name" and that
``t()``'s never-blank behaviour is "covered by test_i18n.py". There was no
``test_i18n.py``. ``nav.profile`` was missing from the catalogue and the
hamburger rendered the literal string "nav.profile" to the owner, in the product,
for a release -- while every test that looked at i18n stayed green, because the
tests that existed checked the *shape* of the catalogue (does every entry have
both languages) and never checked that the catalogue *covers its callers*.

A catalogue can be perfectly well-formed and still be missing the key somebody
uses. That is the whole failure, and no amount of checking entries catches it.

So the checks here are deliberately about USAGE:

* every ``t("...")`` literal in the source resolves;
* every key in the nav link tables resolves;
* and, for completeness, every entry still carries both languages.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api.i18n import (  # noqa: E402
    DEFAULT_LANGUAGE,
    LANGUAGE_CODES,
    STRINGS,
    missing_translations,
    t,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIRS = ("src", "skills")


def _py_files():
    for base in SOURCE_DIRS:
        for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            if "__pycache__" in dirpath:
                continue
            for name in filenames:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


# The catalogue is well-formed. Necessary, and on its own nowhere near enough.
def test_every_entry_carries_every_language():
    gaps = missing_translations()
    assert not gaps, f"entries missing a language: {gaps}"


def test_the_catalogue_is_not_empty():
    assert len(STRINGS) > 50, f"only {len(STRINGS)} strings: is STRINGS truncated?"


# ... and, the part that was missing: it covers the keys the code asks for.
def _t_call_keys():
    """Every ``t("some.key"...)`` literal in src/ and skills/."""
    pattern = re.compile(r"""\bt\(\s*["']([a-z0-9_]+\.[a-z0-9_.]+)["']""")
    found = {}
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for key in pattern.findall(line):
                    found.setdefault(key, []).append(f"{os.path.relpath(path, ROOT)}:{lineno}")
    return found


def test_every_key_the_code_asks_for_exists():
    unknown = {k: v for k, v in _t_call_keys().items() if k not in STRINGS}
    assert not unknown, (
        "these t() calls render the key itself to the user, because the "
        f"catalogue has no entry: {unknown}"
    )


def test_the_navigation_tables_only_use_keys_that_exist():
    """The nav table is a list of (endpoint, key, human label, url).

    The human label is what made the bug invisible: the author wrote "Perfil",
    saw a label in the tuple, and reasonably assumed the page would show it. It
    did not, because ``t()`` had no fallback parameter and the label was being
    passed as a ``str.format`` kwarg that the unknown-key branch never reached.

    Scoped to keys in a known area prefix on purpose. A bare "anything that looks
    like ``word.word`` in a four-tuple" pattern also matches `("tue", "terca",
    "Terc-feira", ...)`, and a sweep that cries wolf on the Portuguese word for
    Tuesday is a sweep that gets deleted.
    """
    areas = {k.split(".")[0] for k in STRINGS}
    pattern = re.compile(
        r"""\(\s*["'][a-z0-9_.]+["']\s*,\s*["']([a-z0-9_]+\.[a-z0-9_.]+)["']"""
        r"""\s*,\s*["'][^"']+["']\s*,"""
    )
    keys = {}
    for path in _py_files():
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                for key in pattern.findall(line):
                    if key.split(".")[0] in areas:
                        keys.setdefault(key, []).append(
                            f"{os.path.relpath(path, ROOT)}:{lineno}"
                        )
    assert keys, "no nav-style tuples found: the pattern stopped matching the code"
    unknown = {k: v for k, v in keys.items() if k not in STRINGS}
    assert not unknown, f"nav entries with no translation: {unknown}"


def test_the_profile_link_is_translated():
    """The one that shipped. Named on its own so the regression is legible."""
    assert t("nav.profile", "pt") == "Perfil"
    assert t("nav.profile", "en") == "Profile"
    # And it is a label, not a key: this is what the owner was reading.
    assert "." not in t("nav.profile", "pt"), t("nav.profile", "pt")


# The fallback parameter, which is new and was silently ignored before.
def test_an_unknown_key_returns_the_key_when_there_is_no_default():
    assert t("nav.does_not_exist") == "nav.does_not_exist"
    assert t("nav.does_not_exist", "pt") == "nav.does_not_exist"


def test_an_unknown_key_returns_the_default_when_one_is_given():
    assert t("nav.does_not_exist", "pt", default="Perfil") == "Perfil"


def test_a_known_key_ignores_the_default():
    """The default is for missing keys, not for overriding a translation. A
    default that won over the catalogue would make the catalogue decorative in
    the other direction."""
    assert t("nav.logout", "pt", default="SAIR") == "Sair"


def test_the_default_is_not_consumed_as_a_format_placeholder():
    """`default` is a named parameter, so it cannot reach `str.format`. The
    previous call site passed `_fallback=`, which could."""
    assert t("nav.logout", "pt", default="X") == "Sair"
    out = t("greeting.hello", "pt", default="ignored", name="Ana")
    assert "Ana" in out or out == "ignored", out


def test_a_missing_placeholder_does_not_raise():
    assert t("greeting.hello", "pt", nobody="x") != ""


def test_language_resolution_prefers_an_explicit_choice():
    assert t("nav.logout", "en") == "Sign out"
    assert t("nav.logout", "pt") == "Sair"
    assert t("nav.logout", "xx") == t("nav.logout", DEFAULT_LANGUAGE), (
        "an unknown language should fall back to the default, not to a key"
    )


@pytest.mark.parametrize("key", sorted(STRINGS))
def test_no_entry_renders_as_its_own_key(key):
    """No entry may be a verbatim copy of its own key: that is what a
    placeholder that was never filled in looks like from the outside."""
    for code in LANGUAGE_CODES:
        assert STRINGS[key].get(code) != key, f"{key}.{code} is the key itself"
