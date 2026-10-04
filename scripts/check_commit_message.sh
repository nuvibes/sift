#!/usr/bin/env bash
#
# A commit message reads like the code's comments: a subject that says the change, in a shape the
# history can be read by. Refuses:
#
#   - a subject line longer than 72 characters;
#   - a subject that is not `type(area): the change`, with the type from TYPES below and the
#     area in lower case and optional (the Conventional Commits header, so a history reads by type);
#   - a character outside ASCII (the source is ASCII; so is its history);
#   - the words in tests/gates/data/narration.json, which comments do not use either.
#
# The commit-msg hook: scripts/check_commit_message.sh <file holding the message>

set -uo pipefail

MSG_FILE="${1:?usage: check_commit_message.sh <message file>}"

# What git keeps: lines starting with # are dropped by the default cleanup, and everything below
# the scissors line that `git commit -v` writes is dropped with them. Read before moving to the
# top of the tree, because the path may be relative.
body=$(sed '/^# -\{24\} >8 -\{24\}$/,$d' "$MSG_FILE" | grep -v '^#' || true)

cd "$(git rev-parse --show-toplevel)" || exit 1

# grep -P needs a UTF-8 locale, and a non-login shell started by a git hook has none.
export LC_ALL=C.UTF-8

FAIL=0
note() { printf '  \033[31mFAIL\033[0m %s\n' "$*" >&2; FAIL=1; }

if ! printf 'a\xc3\xa9b' | grep -qP '[^\x00-\x7F]'; then
  note "grep cannot run -P here, so the checks below would pass ANY message"
  exit 1
fi

subject=$(printf '%s\n' "$body" | sed '/^[[:space:]]*$/d' | head -1)

if [ -z "$subject" ]; then
  exit 0  # an empty message aborts the commit on its own
fi

if [ "${#subject}" -gt 72 ]; then
  note "the subject is ${#subject} characters; 72 is the most"
fi

TYPES='feat fix chore docs test refactor perf build ci'
if ! printf '%s\n' "$subject" | grep -qP "^(?:${TYPES// /|})(?:\([a-z0-9][a-z0-9-]*\))?: \S"; then
  note "the subject is not type(area): the change, with the type one of: $TYPES (the area in lower case, and optional)"
fi

if printf '%s\n' "$body" | grep -qP '[^\x00-\x7F]'; then
  note "the message holds a character outside ASCII:"
  printf '%s\n' "$body" | grep -nP '[^\x00-\x7F]' | sed 's/^/      /' >&2
fi


# The narration list. One entry per line in the file (the gate beside it holds that layout), each
# read back here as: the case flag, the pattern, the example it must match.
LIST=tests/gates/data/narration.json
if [ ! -r "$LIST" ]; then
  note "$LIST is missing, so the message cannot be read against it"
else
  # The first field is never empty ("i", or "i" and the case clause), because read with a tab
  # for IFS folds an empty field into the separator beside it.
  entries=$(sed -n 's/^[[:space:]]*{"pattern": "\([^"]*\)"\(, "case": true\)\{0,1\}, "example": "\([^"]*\)", "clean": "[^"]*"},\{0,1\}$/i\2\t\1\t\3/p' "$LIST" \
    | sed 's/\\\\/\\/g')
  expected=$(grep -c '"pattern":' "$LIST")
  read_back=$(printf '%s\n' "$entries" | sed '/^$/d' | wc -l)
  if [ "$read_back" -ne "$expected" ]; then
    note "read $read_back of the $expected entries in $LIST; its layout has changed under this reader"
  fi
  said=""
  while IFS=$'\t' read -r sensitive pattern example; do
    [ -z "$pattern" ] && continue
    flags=-iP
    [ "$sensitive" != i ] && flags=-P
    if ! printf '%s\n' "$example" | grep -q "$flags" -- "$pattern"; then
      note "the entry [$pattern] does not match its own example here, so it would pass anything"
      continue
    fi
    hit=$(printf '%s\n' "$body" | grep -o "$flags" -- "$pattern" | head -1)
    [ -n "$hit" ] && said+="[$hit] "
  done <<< "$entries"
  if [ -n "$said" ]; then
    note "narrates rather than describes the change: $said"
  fi
fi


if [ "$FAIL" -ne 0 ]; then
  cat >&2 <<'EOF_HELP'

  A subject of 72 characters or fewer shaped type(area): the change, such as
  fix(theater): shuffle keeps the current clip; a body of plain factual lines if it needs one.

EOF_HELP
  exit 1
fi
exit 0
