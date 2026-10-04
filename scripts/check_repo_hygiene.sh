#!/usr/bin/env bash
#
# Repository hygiene, run by pre-commit and CI: secret files by path, symlinks that leave the tree,
# licence headers, NUL bytes, ASCII, dead .md references, case collisions, line endings, and files
# written but never added. gitleaks reads file contents; this reads paths and shapes.

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

FAIL=0
note() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=1; }
pass() { printf '  \033[32mok\033[0m   %s\n' "$*"; }

# grep -P needs a UTF-8 locale, and a non-login shell (as a hook or a gate starts this) has none.
export LC_ALL=C.UTF-8

# Several rules below read an empty grep as a clean tree, so a grep that cannot run -P must fail.
if ! printf 'a\xc3\xa9b' | grep -qP '[^\x00-\x7F]'; then
  note "grep cannot run -P here, so the NUL-byte and ASCII rules below would pass ANY file"
  echo "      needs a grep built with PCRE, run under a unibyte or UTF-8 locale"
  exit 1
fi

# The NUL-byte rule excludes binaries with pathspec magic, and a git that refuses the magic
# answers with nothing, which would read as a clean tree.
if ! git ls-files -- ':(icase)*' ':(exclude,icase)*.svelte' >/dev/null 2>&1; then
  note "git here cannot read the pathspec magic the NUL-byte rule is written in"
  echo "      an unknown magic is refused, and a refusal reads as a tree with nothing to report"
  echo "      needs a git that understands ':(icase)' and ':(exclude,...)'"
  exit 1
fi

# Each pattern is listed at the root and at any depth: a pathspec that begins with a literal is
# anchored to the root. The ignore rules cover all of these, so this catches a `git add -f`.
# shellcheck disable=SC2034  # read through `local -n pats=$1` in collect().
SECRET_PATHS=(
  '.env' '*/.env'
  '.env.*' '*/.env.*'
  ':!.env.example' ':!*/.env.example'
  'cookies/*' '*/cookies/*'
  '*_cookies.txt' '*.cookies'
  # Keys and certificates, browser traffic captures (a HAR carries every cookie of the session),
  # Java keystores, databases (a library is everything in it) and config files.
  '*.pem' '*.key' '*.har' '*.jar' '*.sqlite' '*.sqlite3' '*.conf'
)

# Tracked files and newly-staged files both count.
collect() {
  local -n pats=$1
  local out=""
  out+=$(git ls-files -- "${pats[@]}" 2>/dev/null)
  out+=$'\n'
  out+=$(git diff --cached --name-only --diff-filter=A -- "${pats[@]}" 2>/dev/null)
  printf '%s' "$out" | sed '/^$/d' | sort -u
}

secrets=$(collect SECRET_PATHS)
if [ -n "$secrets" ]; then
  note "secret file tracked or staged:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $secrets
else
  pass "no secrets tracked (.env.example only)"
fi

# A symlink commits as a pointer, so content scanners see nothing, but it resolves for
# anyone who clones it.
escaping=""
while IFS= read -r link; do
  [ -z "$link" ] && continue
  target=$(readlink "$link" 2>/dev/null) || continue
  case "$target" in
    /*|*..*) escaping+="$link -> $target"$'\n' ;;
  esac
done < <(git ls-files -s | awk '$1=="120000"{ $1=$2=$3=""; sub(/^[ \t]+/,""); print }')

if [ -n "$escaping" ]; then
  note "symlink points outside the repo:"
  printf '      %s\n' "$escaping"
else
  pass "no symlinks escaping the repo"
fi

# Deleting an ignore entry is a silent way to disarm the first line of defence.
for entry in '.env' 'cookies/'; do
  if grep -qxF -- "$entry" .gitignore 2>/dev/null; then
    pass ".gitignore covers '$entry'"
  else
    note ".gitignore no longer covers '$entry'"
  fi
done

# Every source file identifies its licence. Checked rather than trusted, because a file added
# without the header looks exactly like one that was deliberately excluded.
missing=""
while IFS= read -r f; do
  [ -z "$f" ] && continue
  head -3 "$f" | grep -q 'SPDX-License-Identifier' || missing+="$f"$'\n'
done < <(git ls-files -- 'src/*.py' 'tests/*.py')

if [ -n "$missing" ]; then
  note "missing the SPDX licence header:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $missing
  echo "      add: # SPDX-License-Identifier: AGPL-3.0-or-later"
else
  pass "every source file carries a licence header"
fi

# A NUL byte makes a text file binary to every rule here that skips binaries with -I, so it would
# pass the ASCII rule unread; this runs first. -a is needed because grep reports no match for the
# NUL that made it call the file binary. Binaries are excluded by extension, so a text file never
# hides in an excluded folder.
binary_source=$(git ls-files -z -- src tests scripts .github README.md SECURITY.md ARCHITECTURE.md \
    frontend/src frontend/e2e frontend/scripts \
    ':(exclude,icase)*.png' ':(exclude,icase)*.jpg' ':(exclude,icase)*.jpeg' \
    ':(exclude,icase)*.gif' ':(exclude,icase)*.webp' ':(exclude,icase)*.heic' \
    ':(exclude,icase)*.ico' ':(exclude,icase)*.mp4' ':(exclude,icase)*.mov' \
    ':(exclude,icase)*.mkv' ':(exclude,icase)*.webm' ':(exclude,icase)*.woff2' \
    ':(exclude,icase)*.sql.gz' \
  | xargs -0 grep -laUP '\x00' 2>/dev/null || true)

if [ -n "$binary_source" ]; then
  note "a text source file holds a NUL byte, which makes it binary to every check that skips binaries:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $binary_source
  echo "      write the value out instead; a control character in source is invisible and un-greppable"
else
  pass "no source file reads as binary"
fi

# Source is ASCII, so it renders the same in every terminal, editor and diff; the en and em dash
# are the one exception. -I skips binaries such as fonts and images.
non_ascii=$(git ls-files -z -- . ':!src/sift/kernel/tests/fixtures' \
  | xargs -0 grep -lIP '[^\x00-\x7F\x{2013}\x{2014}]' 2>/dev/null || true)

if [ -n "$non_ascii" ]; then
  note "not ASCII:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $non_ascii
else
  pass "source is ASCII"
fi

# A .md file named in a tracked file must be in the tree: a dead reference names nothing a reader
# can open. A name with a directory must match a tracked path or the end of one; a bare name must
# match some tracked file's own name. The look-behind keeps a URL's last segment and a CSS class
# (`.chip.md`) out of the token. This script and the gate test that plants a missing name are
# excluded.
MD_TOKEN='(?<![\w./-])(?:\.{1,2}/)*(?:[\w-]+/)*[\w-]+(?:\.[\w-]+)*\.[mM][dD](?![\w-])'

# A known positive for the pattern, because the rule reads an empty answer as a clean tree.
if [ "$(printf 'see missing-guide.md here\n' | grep -oP "$MD_TOKEN" 2>/dev/null)" != "missing-guide.md" ]; then
  note "the pattern that finds a .md name cannot find one, so the rule below would pass ANY tree"
  exit 1
fi

md_named=$(git ls-files -z -- . ':!scripts/check_repo_hygiene.sh' ':!tests/gates/test_gates.py' \
  | xargs -0 grep -oHIP "$MD_TOKEN" 2>/dev/null || true)

# The index is read first, from its own file; the names found arrive on stdin (`-`). Told apart by
# FILENAME rather than NR == FNR, which would misread a repository whose index is empty.
md_missing=$(printf '%s\n' "$md_named" | awk '
  FILENAME != "-" { whole[$0] = 1; name = $0; sub(/.*\//, "", name); own[name] = 1; next }
  $0 == "" { next }
  {
    # grep -H prints "file:token"; a token holds no colon, so the LAST colon is the separator.
    at = match($0, /:[^:]*$/)
    file = substr($0, 1, at - 1); token = substr($0, at + 1)
    while (token ~ /^\.\.?\//) sub(/^\.\.?\//, "", token)
    if (index(token, "/") == 0) { if (token in own) next }
    else {
      if (token in whole) next
      found = 0
      for (path in whole) {
        tail = "/" token
        if (length(path) > length(tail) && substr(path, length(path) - length(tail) + 1) == tail) { found = 1; break }
      }
      if (found) next
    }
    print file ": " token
  }' <(git ls-files) - | sort -u)

if [ -n "$md_missing" ]; then
  note "names a .md file that is not in the tree:"
  # Quoted and indented by sed rather than split by printf, because each line holds a space.
  printf '%s\n' "$md_missing" | sed 's/^/      /'
  echo "      a dead reference: name a file that is here, or say what it would have said"
else
  pass "every .md file named is in the tree"
fi

# Two paths that differ only in capitals are one path on a case-insensitive filesystem: a clone
# collides, and tooling that keys files that way silently runs only one of them. `sort -u` first,
# because a conflicted merge lists one path once per stage.
collisions=$(git ls-files | sort -u | tr '[:upper:]' '[:lower:]' | sort | uniq -d)

if [ -n "$collisions" ]; then
  note "two files differ only by case, and are one file on a case-insensitive filesystem:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $collisions
else
  pass "no two paths differ only by case"
fi

# Line endings in the working tree, the half `.gitattributes` cannot enforce: a file written with
# CRLF is normalised on the way in, so git shows no diff while the copy on disk is wrong (a shell
# script with CRLF fails as a "bad interpreter"). A file whose attribute says `eol=crlf` is CRLF on
# purpose; binaries report `w/-text` and are not matched.
endings=$(git ls-files --eol | grep -E "^i/[^ ]+[[:space:]]+w/(crlf|mixed)" | grep -v "eol=crlf" | sed "s/.*\t//")

if [ -n "$endings" ]; then
  note "carries CRLF in the working tree, where this repository is LF everywhere:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $endings
  echo "      whatever wrote it opened the file in text mode; ask it for LF explicitly"
else
  pass "every tracked file is LF in the working tree"
fi

# A file written and never added is invisible to every rule that reads the index. .gitignore
# answers what is generated, so what is left was written and not added. In CI this always passes;
# it binds at the commit. A known positive first, in a throwaway repository, because an empty
# listing reads as a clean tree. Entered with `cd` rather than `git -C`: under MSYS_NO_PATHCONV a
# Windows git cannot read the temp directory's POSIX spelling.
canary_repo=$(mktemp -d)
canary=$(cd "$canary_repo" && git init -q >/dev/null 2>&1 && : > canary.ts \
  && git ls-files --others --exclude-standard 2>&1)
rm -rf "$canary_repo"

if [ "$canary" != "canary.ts" ]; then
  note "git cannot list untracked files here, so the rule below would pass ANY tree"
  echo "      it read [$canary] where a clean tree would also read nothing"
  exit 1
fi

untracked=$(git ls-files --others --exclude-standard)

if [ -n "$untracked" ]; then
  note "written but never added, so every check that reads the index looks straight past it:"
  # shellcheck disable=SC2086  # unquoted ON PURPOSE: printf repeats its format once per word,
  # which is how each offending path lands on its own line.
  printf '      %s\n' $untracked
  echo "      git add it, or add it to .gitignore if it is not meant to be in the repository"
else
  pass "nothing is written and left out of the index"
fi

# Each module's coverage gate is checked by tests/gates/test_every_module_has_a_coverage_gate.py.
pass "coverage gates are checked by their own gate, over every module rather than every slice"

echo
[ "$FAIL" -ne 0 ] && { echo "repo-hygiene failed."; exit 1; }
echo "repo-hygiene passed."
exit 0
