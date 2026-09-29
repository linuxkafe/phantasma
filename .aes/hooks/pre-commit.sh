#!/bin/sh
# AES pre-commit hook — runs quick quality gates before commit
# Installed by AES bootstrap protocol. Do not remove.

set -e

echo "🔍 AES pre-commit: running quality gates..."

# 1. Check for secrets.
# Intent matches docs/QUALITY_GATES.md "No secrets in source": hardcoded
# credential VALUES are blocked; config-driven reads (config.py, os.getenv,
# os.environ, getattr(config,...)) and docs quoting the pattern are allowed.
#
# A constant that NAMES an environment variable is not a secret:
#   ENV_TOKEN = "PHANTASMA_COMMAND_TOKEN"
# names the variable; the secret is read later with os.getenv(). The keyword
# filter cannot tell that from a value, so the identifier form is exempted
# explicitly -- narrow, because exempting the file would hide a real secret in
# it. Only an all-caps-with-underscores value on a name that ends in _ENV or
# starts with ENV_/VARNAME_ qualifies.
#
# Prefixed provider tokens (sk-, ghp_, xoxb-, AKIA, ghp, hf_, eyJ for JWT) are
# blocked on the VALUE regardless of the variable name. They were not caught
# before: the original rule only matched a quoted string, so any real token
# with a provider prefix slipped through. Found by
# tests/test_pre_commit_secret_gate.py, which tests both directions.
#
# Two carve-outs, both narrow and both about the gate testing itself:
#   * comment lines -- a docstring showing the shape is not a secret. The
#     original filter only dropped "# " at column 0, so "#   ENV_TOKEN = ..."
#     was treated as code;
#   * tests/test_pre_commit_secret_gate.py, which must contain fake provider
#     tokens in order to prove they are caught. Naming the one file is the
#     point: exempting "any test file" would exempt every fixture in the suite.
# See SD-phantasma-OPS-019 and SD-phantasma-OPS-020.
if git diff --cached --name-only \
   | grep -vE "test_pre_commit_secret_gate\.py$" \
   | while read -r f; do
    grep -nE "ACCESS_KEY|LOCAL_KEY|TOKEN|SECRET" "$f" 2>/dev/null
done | grep -vE "config\.py" \
     | grep -vE "getattr\(config|os\.getenv|os\.environ|\bTOKEN\.(json|get)|__all__|ACCESS_KEY\|LOCAL_KEY\|TOKEN|config-driv" \
     | grep -vE "^[[:space:]]*([0-9]+:)?[[:space:]]*#" \
     | grep -vE "^([0-9]+:)?[[:space:]]*(ENV_|VARNAME_)[A-Z0-9_]*[[:space:]]*=[[:space:]]*['\"][A-Z][A-Z0-9_]*['\"]" \
     | grep -E "=\s*['\"][^'\"]{8,}['\"]|['\"](sk-|ghp_|gho_|xox[baprs]-|AKIA|hf_|AIza)[A-Za-z0-9_\-]{8,}|['\"]eyJ[A-Za-z0-9_\-]{10,}|BEGIN (RSA|OPENSSH|EC|PRIVATE)" >/dev/null; then
    echo "❌ BLOCKER: Potential hardcoded secret in staged files"
    echo "   Use config.py + environment variables instead"
    exit 1
fi

# 2. Check for TODO in src (excluding tests)
if git diff --cached --name-only | xargs grep -l "TODO:" 2>/dev/null | grep -E "^src/" >/dev/null; then
    echo "⚠️  WARNING: TODO found in staged src/ files"
    echo "   Consider creating a ticket instead"
fi

# 3. Canonical lint gate. Delegate to the project's own `make lint`
#    (ruff scope: src tests assistant.py config.py src/main.py), so the
#    hook enforces exactly the gate CLAUDE.md requires — no stricter, no
#    weaker. Vendored legacy skills/helpers are outside that declared
#    scope by project decision. Runs via the venv interpreter because the
#    hook executes outside the activated venv (bare `python` is not on PATH).
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOK_PY="$REPO_ROOT/venv/bin/python"
[ -x "$HOOK_PY" ] || HOOK_PY="python3"
if ! (cd "$REPO_ROOT" && "$HOOK_PY" -m ruff check \
        src tests assistant.py config.py src/main.py --quiet); then
    echo "❌ BLOCKER: Ruff lint failed (make lint scope)"
    echo "   Run 'make format' and 'make lint' to fix"
    exit 1
fi

# 4. Quick test smoke test (only if test files changed)
STAGED_TESTS=$(git diff --cached --name-only -- "tests/*.py" | head -5)
if [ -n "$STAGED_TESTS" ]; then
    # Override --cov-fail-under=30 (from pyproject addopts): a smoke run of
    # only the staged files can't reach suite-wide coverage. Full gate runs
    # via make test.
    (cd "$REPO_ROOT" && "$HOOK_PY" -m pytest $STAGED_TESTS -q --tb=short --cov-fail-under=0 2>/dev/null) || {
        echo "❌ BLOCKER: Tests failed on staged test files"
        exit 1
    }
fi

echo "✅ AES pre-commit: all gates passed"
exit 0