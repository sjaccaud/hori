#!/bin/bash
# devin_resume.sh — automated crash recovery protocol for Devin sessions.
#
# Run this at the start of every new session. It checks the 6 things
# the resume protocol in .devin/AGENTS.md requires, verifies the git
# safety hooks are installed, and rebuilds them if missing.
#
# This makes the resume protocol trusted architecture (enforced by a
# script) rather than a guideline Devin has to remember to follow.
#
# Usage: scripts/devin_resume.sh
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [[ -z "$REPO_ROOT" ]]; then
    echo "ERROR: Not in a git repository."
    exit 1
fi

cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

echo "=== Devin Resume Protocol ==="
echo ""

# 1. SLICE_LOG — where are we?
echo "1. Session state (.devin/SLICE_LOG.md):"
if [[ -f .devin/SLICE_LOG.md ]]; then
    current_slice=$(grep -A5 "^## Current Slice" .devin/SLICE_LOG.md | grep -v '^## ' | grep -v '^$' | head -1 | sed 's/^ *//')
    if [[ -z "$current_slice" || "$current_slice" == "None" || "$current_slice" =~ ^\(None ]]; then
        warn "Current Slice is empty or 'None' — may be stale after a crash."
        warn "Update it before starting work (see AGENTS.md → Session state file)."
    else
        ok "$current_slice"
    fi
else
    fail ".devin/SLICE_LOG.md not found."
fi
echo ""

# 2. Current branch
echo "2. Current branch:"
branch=$(git branch --show-current)
ok "$branch"
echo ""

# 3. Recent commits
echo "3. Recent commits:"
git log --oneline -5 | while read -r line; do
    echo "    $line"
done
echo ""

# 4. Working directory status
echo "4. Working directory:"
status=$(git status --short)
if [[ -z "$status" ]]; then
    ok "clean (no uncommitted changes)"
else
    count=$(echo "$status" | wc -l)
    warn "$count uncommitted file(s):"
    echo "$status" | head -10 | sed 's/^/    /'
    if [[ $count -gt 10 ]]; then
        echo "    ... ($((count - 10)) more)"
    fi
fi
echo ""

# 5. Git safety hooks
echo "5. Git safety hooks:"
hooks_ok=true
if [[ -x .git/hooks/pre-commit ]]; then
    ok "pre-commit hook installed"
else
    fail "pre-commit hook MISSING"
    hooks_ok=false
fi
if [[ -x .git/hooks/pre-push ]]; then
    ok "pre-push hook installed"
else
    fail "pre-push hook MISSING"
    hooks_ok=false
fi

if [[ "$hooks_ok" == "false" ]]; then
    echo ""
    echo "  Rebuilding hooks from .devin/hook-patterns.conf..."
    if bash scripts/install_hooks.sh 2>&1; then
        echo -e "  ${GREEN}✓${NC} Hooks rebuilt successfully."
    else
        echo -e "  ${RED}✗${NC} Hook rebuild failed. See scripts/install_hooks.sh."
        echo "  If .devin/hook-patterns.conf is missing (fresh clone), ask the"
        echo "  product owner to provide it."
    fi
fi
echo ""

# 6. Archive branch protection
echo "6. Archive branch protection:"
archive_pushremote=$(git config branch.archive/pre-squash.pushRemote 2>/dev/null || echo "")
if [[ -n "$archive_pushremote" ]]; then
    ok "archive/pre-squash pushRemote = '$archive_pushremote' (blocked)"
else
    if git show-ref --verify --quiet refs/heads/archive/pre-squash 2>/dev/null; then
        warn "archive/pre-squash exists but has no pushRemote barrier."
        warn "  Run: git config branch.archive/pre-squash.pushRemote no-push"
    else
        ok "archive/pre-squash branch not present (clean clone)"
    fi
fi
echo ""

# 7. Tests (optional — may not apply to all branches)
echo "7. Test suite:"
if [[ -f Makefile ]]; then
    if make test >/dev/null 2>&1; then
        ok "tests pass (make test)"
    else
        warn "tests fail (make test) — check before continuing work"
    fi
elif [[ -f pytest.ini || -f setup.py || -f pyproject.toml ]]; then
    if python -m pytest --tb=no -q >/dev/null 2>&1; then
        ok "tests pass (pytest)"
    else
        warn "tests fail (pytest) — check before continuing work"
    fi
else
    echo "    (no test runner detected — skip if this branch has no tests)"
fi
echo ""

echo "=== Resume complete ==="
echo "Read .devin/SLICE_LOG.md for full context before starting work."
