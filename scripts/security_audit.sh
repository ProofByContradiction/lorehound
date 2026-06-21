#!/bin/sh
# Lorehound dependency security audit — run locally (pre-commit), on demand, and
# in CI. Two checks:
#   1. pip check  — no broken / conflicting dependency versions
#   2. pip-audit  — no installed dependency has a known CVE (PyPI / OSV DB)
#
# Usage:   ./scripts/security_audit.sh
# Returns non-zero on a real problem. A pip-audit network failure is a warning
# (so offline commits aren't blocked); an actual vulnerability is a hard failure.
set -eu

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="$(command -v python3 || command -v python || true)"
fi
[ -n "${PY:-}" ] || { echo "✖ No Python interpreter found."; exit 1; }

echo "🔒 Lorehound security audit — $("$PY" --version 2>&1)"

# 1) Dependency conflicts -----------------------------------------------------
echo "• pip check (dependency conflicts)…"
if ! "$PY" -m pip check; then
    echo "✖ Dependency conflicts found (see above)."
    exit 1
fi

# 2) Known CVEs ---------------------------------------------------------------
echo "• pip-audit (known vulnerabilities)…"
if ! "$PY" -c "import pip_audit" 2>/dev/null; then
    echo "⚠ pip-audit not installed — run: $PY -m pip install -r requirements-dev.txt"
    echo "  Skipping CVE scan (install the dev tooling to enable it)."
    exit 0
fi

set +e
out=$("$PY" -m pip_audit --progress-spinner off 2>&1)
status=$?
set -e
printf '%s\n' "$out"
if [ "$status" -ne 0 ]; then
    if printf '%s' "$out" | grep -qiE "GHSA-|PYSEC-|CVE-|vulnerab"; then
        echo "✖ pip-audit found known vulnerabilities (see above)."
        exit 1
    fi
    echo "⚠ pip-audit could not complete (offline or service error). Not blocking."
    exit 0
fi
echo "✅ Security audit passed — no conflicts, no known CVEs."
