#!/usr/bin/env bash
#
# The repository root holds only the entries tests/gates/data/root_entries.txt declares, read over
# the index. The root is where a scratch file lands (a saved commit message, a batch file), and a
# new top-level entry is added to that list on purpose. tests/gates/test_root_closure.py holds the
# same rule in CI; this is the commit-time half.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

ROOT_ENTRIES=tests/gates/data/root_entries.txt
if [ ! -r "$ROOT_ENTRIES" ]; then
  printf '  \033[31mFAIL\033[0m %s\n' "$ROOT_ENTRIES is missing, so nothing says what the root may hold"
  exit 1
fi

undeclared=$(git ls-files | cut -d/ -f1 | sort -u \
  | grep -vxF -f <(grep -v '^#' "$ROOT_ENTRIES" | sed '/^[[:space:]]*$/d') || true)
if [ -n "$undeclared" ]; then
  printf '  \033[31mFAIL\033[0m %s\n' "at the repository root and not declared in $ROOT_ENTRIES:"
  printf '%s\n' "$undeclared" | sed 's/^/      /'
  echo "      a scratch file does not belong in the repository; a real entry is a line in that list"
  exit 1
fi

printf '  \033[32mok\033[0m   %s\n' "the root holds only declared entries"
exit 0
