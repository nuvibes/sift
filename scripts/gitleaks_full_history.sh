#!/usr/bin/env bash
#
# Scans every commit, not just the staged diff. Wired into pre-push.
#
# The pre-commit hook only sees staged changes, which is what makes it fast enough to run
# every time. It cannot see a secret committed before the hooks were installed, committed
# with --no-verify, or added and later "removed": deleting a file does not remove it from
# history, and every clone still carries it. Push is the point at which that becomes
# irreversible, so the full scan runs here.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks is not installed: https://github.com/gitleaks/gitleaks#installing" >&2
  exit 1
fi

if ! git rev-parse HEAD >/dev/null 2>&1; then
  echo "no commits yet, nothing to scan"
  exit 0
fi

echo "gitleaks: scanning full history..."
if gitleaks detect --source . --config .gitleaks.toml --redact --verbose; then
  echo "gitleaks: clean"
  exit 0
fi

cat >&2 <<'EOF'

Push blocked: a secret is present in the history.

Committing a fix on top does not remove it: the secret is in an earlier commit object and
every clone carries every commit.

  1. Rotate the credential. Assume it is compromised.
  2. Rewrite the history containing it (git filter-repo), or start from a clean root if the
     repo has not been pushed yet.
  3. Re-run this scan.

If it is a false positive, add a narrow allowlist entry to .gitleaks.toml with a comment.
EOF
exit 1
