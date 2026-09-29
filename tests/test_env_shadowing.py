"""Guards against environment-dependent imports.

The failure this exists to prevent already happened once, and it presented as
something else entirely: a deploy where 15 tests failed in prod and passed in
dev, none of them related to the change under test. The real cause was that
`tests/` was a namespace-package portion, and `pytest-cov` ships a regular
top-level `tests` package in site-packages. A regular package beats a namespace
portion no matter where each sits on the path, so `from tests.helpers_ui_auth
import ...` was unresolvable in prod and perfectly resolvable in dev.

Nothing about the test file was different. Only the environment was, and the
environment is exactly the thing a developer never changes and a deployment
always changes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_our_tests_package_is_not_shadowed_by_an_installed_one():
    """`import tests` must resolve to THIS project, not to a wheel's leftovers.

    If some distribution ever ships a top-level `tests/` again -- and pytest-cov
    already does -- this is the assertion that names the thief instead of leaving
    14 tests to fail with a ModuleNotFoundError nobody connects to packaging.
    """
    spec = importlib.util.find_spec("tests")
    assert spec is not None, "the tests package does not resolve at all"
    origin = Path(spec.origin or spec.submodule_search_locations[0]).resolve()
    assert origin.is_relative_to(PROJECT_ROOT), (
        f"`import tests` resolves to {origin}, which is NOT this project "
        f"({PROJECT_ROOT}). Something in site-packages is shadowing it -- check "
        f"sys.path[0:3]={sys.path[0:3]}. A regular package always beats a "
        f"namespace-package portion, which is why tests/__init__.py exists."
    )


def test_the_rootdir_is_importable_as_a_package_root():
    """A helper must be importable both as `tests.helpers_ui_auth` and after a
    bare rootdir insertion.

    The first is how the tests spell it. The second is what a plain
    `python -m pytest` from the project root gives you, and if the two ever
    disagree the suite passes locally and fails on the box that runs the
    service -- which is the only box that matters.
    """
    saved = list(sys.path)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        for name in ("tests.helpers_ui_auth",):
            spec = importlib.util.find_spec(name)
            assert spec is not None, (
                f"{name} is not importable from the project root, so a suite "
                f"that relies on it is environment-dependent"
            )
    finally:
        sys.path[:] = saved
