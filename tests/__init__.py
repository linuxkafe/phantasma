"""The project's own test package.

This file exists for one reason, and removing it breaks production in a way
that looks nothing like the cause.

`pytest-cov` ships a top-level `tests` package inside its wheel, so
`/opt/phantasma/venv/lib/python3.11/site-packages/tests/__init__.py` exists.
Without this `__init__.py`, `tests/` here is only a namespace-package portion
during import, and a namespace portion always loses to a REGULAR package found
anywhere on the path -- regardless of order. So in prod:

    from tests.helpers_ui_auth import make_app_with_user
    -> ModuleNotFoundError: No module named 'tests.helpers_ui_auth'

while the same suite passed in dev, whose venv does not have that stray
package. The test file is in `tests/` either way; only the surrounding
environment differed, which is the worst kind of difference to find out about
from a deploy that "passed the tests" yesterday and fails today.

With this `__init__.py`, `tests` is a regular package found at the rootdir, and
a regular package at the front of the path wins over the one in site-packages.
Same import, same result, in both environments.

Kept empty of logic on purpose: pytest still collects by path, and the point is
the marker, not behaviour.
"""
