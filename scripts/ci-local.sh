#!/usr/bin/env bash
#
# Runs the gates continuous integration runs, on this machine, against the caches you already
# have. The point is to answer "would CI pass?" without spending a metered CI run to ask it:
# most failures (a type error, an unformatted file, a dropped coverage branch, a leaked secret)
# are knowable here in a couple of minutes.
#
# This is a MIRROR of the workflows under .github/workflows, not a re-execution of them. It calls
# the same underlying commands, but reuses the local virtualenv, node_modules and build output
# instead of installing everything from scratch. That reuse is the whole speed difference, and the
# one thing it costs: when a step changes in a workflow it must change here too. The workflows
# stay the source of truth; this is the fast road to the same answer.
#
# Usage:
#   scripts/ci-local.sh                 what quick.yml runs on every pull request (the default)
#   scripts/ci-local.sh --all           every gate: the whole suite, coverage, e2e and the shipped tools
#   scripts/ci-local.sh py              only the Python gates (lint, format, types, semgrep, audit, tests, coverage)
#   scripts/ci-local.sh web             only the browser-client gates (types, format, unit, build, offline/token gates, audit)
#   scripts/ci-local.sh test            only the test suite and the per-module coverage gates
#   scripts/ci-local.sh gates           only the repository's gate tests quick.yml runs (the unit tier)
#   scripts/ci-local.sh e2e             only the end-to-end browser run
#   scripts/ci-local.sh secrets         only the full-history secret scan
#   scripts/ci-local.sh schema          only the client-types-match-server check
#   scripts/ci-local.sh wheels          only the Windows-wheel check for the pinned interpreter
#   scripts/ci-local.sh sbom            only the bill-of-materials and licence check
#   scripts/ci-local.sh shipped         only the shipped tools (vendor/bin and the face runtime), run for real
#
# Nothing here is fail-fast: every gate runs and the failures are listed together at the end,
# because seeing all of what to fix beats discovering it one push at a time. The exit code is
# the number of gates that failed.
#
# Keep the transcript. The summary at the end names the gates that failed, not why, and a run long
# enough to walk away from is a run whose output has scrolled off:
#
#   scripts/ci-local.sh 2>&1 | tee /tmp/ci.log
#
# The worker count is sized for one checkout. When several checkouts of this repository run their
# gates together on one computer, say how many, and each run takes its share of the spare cores:
#
#   SIFT_CI_CHECKOUTS=3 scripts/ci-local.sh
#
# Two more, both for when a skip or a reuse is in the way:
#
#   FORCE_WEB_BUILD=1        build the client even if the built one looks current
#   rm -rf frontend/node_modules   force a clean install rather than reusing the tree

# Every `_name` stage function below is invoked INDIRECTLY: `step "label" _name` passes the
# function name as an argument and the helper calls it. shellcheck cannot follow that and reports
# every one as dead code, which is enough noise to bury a real finding. This has to sit ABOVE the
# first command to apply to the whole file; lower down it silences one command and no more.
# shellcheck disable=SC2329
set -uo pipefail

# `|| exit 1` and not bare: with no repository the substitution is empty, and `cd ""` is a
# no-op that RETURNS ZERO, so without this the whole run would proceed from wherever it was
# started, reporting on some other tree. Every other `cd` to the root in this repo carries it.
cd "$(git rev-parse --show-toplevel)" || exit 1

# CI sets this, and a handful of tools behave differently under it (reporters, colour, prompts).
# Mirror it so the answer here matches the answer there.
export CI="${CI:-true}"

# ---------------------------------------------------------------------------------------------
# Toolchain. uv is not always on PATH, but the project venv always carries it. ruff, mypy and
# pip-audit run through `uv run`, so they come from uv.lock, the same versions CI and the
# pre-commit hooks use. semgrep is a pinned tool outside the lock and runs through
# scripts/venv_tool.py, the one route every runner of it takes.
# ---------------------------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  UV="uv"
elif [ -x .venv/bin/uv ]; then
  UV="$PWD/.venv/bin/uv"
# Both layouts: `bin/` on POSIX, `Scripts/` with an `.exe` on Windows. Naming only the first meant
# this fallback could never fire on the platform Sift ships on.
elif [ -x .venv/Scripts/uv.exe ]; then
  UV="$PWD/.venv/Scripts/uv.exe"
else
  echo "uv not found on PATH, at .venv/bin/uv or at .venv/Scripts/uv.exe. Run: uv sync --extra dev" >&2
  exit 2
fi
export UV

# One checkout unless the caller says otherwise; the budget reads it before taking it away.
SIFT_CI_CHECKOUTS="${SIFT_CI_CHECKOUTS:-1}"

# The per-module coverage gates and the concurrency budget both live in one file, which the
# workflow runs too. Seventeen gate commands written out in two places is seventeen chances for the
# local answer and the real one to diverge, so they are declared once and called from both.
# shellcheck source=scripts/coverage_gates.sh
. scripts/coverage_gates.sh
sift_ci_take_env
CI_JOBS="$(sift_ci_jobs)"
sift_ci_load_warning

# ---------------------------------------------------------------------------------------------
# No path rewriting, on a shell that would do it.
#
# Git for Windows converts any argument that looks like a POSIX path into a Windows one before the
# program ever sees it. Every stage below is written for that being off: where a Windows program is
# handed a path, the stage converts it itself (see `_pip_audit`), and nothing else is rewritten on
# the way. `cygpath` exists only where the conversion does, which is what makes this the test for it.
# ---------------------------------------------------------------------------------------------
if command -v cygpath >/dev/null 2>&1; then
  export MSYS_NO_PATHCONV=1
fi

# ---------------------------------------------------------------------------------------------
# Reporting harness. `step` runs one gate, records the result, and never aborts the run.
# ---------------------------------------------------------------------------------------------
if [ -t 1 ]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; DIM=""; RESET=""
fi

FAILURES=()
PASSES=0
SKIPS=()

step() {  # step "label" command...
  local label="$1"; shift
  printf '\n%b>> %s%b\n' "$BOLD" "$label" "$RESET"
  local start=$SECONDS
  if "$@"; then
    PASSES=$((PASSES + 1))
    printf '%bPASS%b  %s  %b(%ds)%b\n' "$GREEN" "$RESET" "$label" "$DIM" "$((SECONDS - start))" "$RESET"
  else
    local rc=$?
    FAILURES+=("$label")
    printf '%bFAIL%b  %s  %b(exit %d, %ds)%b\n' "$RED" "$RESET" "$label" "$DIM" "$rc" "$((SECONDS - start))" "$RESET"
  fi
}

skip() {  # skip "label" "reason"
  SKIPS+=("$1 ($2)")
  printf '\n%bSKIP%b  %s  %b(%s)%b\n' "$DIM" "$RESET" "$1" "$DIM" "$2" "$RESET"
}

# ---------------------------------------------------------------------------------------------
# One-time build of the browser client. The Python test job needs it: several tests assert
# things about the built page (the policy names the script it carries, a deep link is answered,
# a request cannot be walked out of the client's directory). Without a build those read a missing
# client and quietly check the other half of the branch. The e2e job needs it too. Build once,
# reuse.
# ---------------------------------------------------------------------------------------------
WEB_DEPS_READY=0
DESKTOP_DEPS_READY=0

ensure_web_deps() {
  [ "$WEB_DEPS_READY" = "1" ] && return 0
  # Reinstall when the tree is missing OR the lockfile has moved since the last install. npm records
  # the lockfile it installed from at node_modules/.package-lock.json; if the tracked lockfile is
  # newer, the tree is stale: a merge that adds a dependency would otherwise leave this checkout
  # building against a tree that never installed it, and every web gate would then fail for a
  # reason that has nothing to do with the code.
  marker=frontend/node_modules/.package-lock.json
  if [ ! -d frontend/node_modules ] || [ ! -f "$marker" ] || [ frontend/package-lock.json -nt "$marker" ]; then
    ( cd frontend && npm ci --no-audit --no-fund ) || return 1
  else
    printf '%breusing frontend/node_modules (delete it to force a clean install)%b\n' "$DIM" "$RESET"
  fi
  # The fonts and the icon map are generated, untracked, and imported by the client's own source,
  # so on a fresh checkout svelte-check and the browser unit tests fail on a module that is not there
  # yet, and pass on a second run because the build in between wrote it. That reads as a broken gate
  # to anyone running this for the first time. Generated here, before any gate reads the tree, rather
  # than as a side effect of a build step that runs after three checks that need it.
  #
  # Regenerated when the icon list or the generator has moved since the map was written, not only
  # when the map is missing: the same staleness trap as node_modules above, and it bites the same
  # way. The map holds one entry per icon the client asks for; a change that adds an icon leaves an
  # older map in place, and the component looks that name up and renders undefined. Every test that
  # draws the new icon then fails on a checkout where the map happens to be old, and passes
  # everywhere the map was written after the change, which is a difference between two machines
  # rather than a difference in the code.
  icon_map=frontend/src/lib/generated/icon-codepoints.json
  if [ ! -f "$icon_map" ] || [ ! -d frontend/static/fonts ] \
     || [ frontend/src/lib/design/icons.ts -nt "$icon_map" ] \
     || [ frontend/scripts/prepare_fonts.js -nt "$icon_map" ]; then
    ( cd frontend && npm run fonts ) || return 1
  fi
  WEB_DEPS_READY=1
}

# The client build, skipped when the one on disk is newer than everything it was built from. It is
# the most expensive step here and it was being paid twice: once for the Python suite, which
# asserts things about the built page, and again by the end-to-end run. build_if_stale.js decides,
# and is pessimistic: anything missing, unreadable or half-written counts as stale.
WEB_BUILT=0
ensure_web_build() {
  [ "$WEB_BUILT" = "1" ] && return 0
  ensure_web_deps || return 1
  ( cd frontend && npm run build:ifstale ) || return 1
  WEB_BUILT=1
}

# ---------------------------------------------------------------------------------------------
# Jobs. Each mirrors one job (or one clearly separable half) of a workflow.
# ---------------------------------------------------------------------------------------------

_secrets() { scripts/gitleaks_full_history.sh; }
job_secrets() {
  if command -v gitleaks >/dev/null 2>&1; then
    step "secrets: full-history scan" _secrets
  else
    skip "secrets: full-history scan" "gitleaks not installed"
  fi
}

# actionlint is a Go binary, like gitleaks, so it is not in the lockfile and not on every machine.
# Skipped rather than assumed when it is absent, and the skip is loud, because this is the ONLY
# reading the workflow ever gets: there is no second build to check the build, and a fault in that
# file is a run that never starts rather than a run that fails. It shellchecks every `run:` block
# too, which is where the shell in this project is least visible.
_actionlint() { actionlint; }
job_hygiene() {
  step "hygiene: repo layout and ascii" bash scripts/check_repo_hygiene.sh
  step "hygiene: no double-hyphen dash, em dashes in copy" "$UV" run python scripts/check_display_dashes.py
  step "hygiene: code length and prose may only fall" "$UV" run python scripts/check_code_shape.py
  if command -v actionlint >/dev/null 2>&1; then
    step "hygiene: the workflow says what it means" _actionlint
  else
    skip "hygiene: the workflow says what it means" "actionlint not installed"
  fi
}

_ruff_check()   { "$UV" run ruff check .; }
_ruff_format()  { "$UV" run ruff format --check .; }
# The Windows branches, which are the ones the installer runs, whatever machine this is.
_mypy()         { "$UV" run mypy --platform win32; }
_semgrep_test() { "$UV" run python scripts/venv_tool.py semgrep --test --config semgrep/ semgrep/; }
_semgrep_scan() { "$UV" run python scripts/venv_tool.py semgrep scan --config semgrep/ --error; }
# pip-audit against the LOCKED dependencies, not the live environment. Auditing the environment
# trips over Sift itself (an editable install with no PyPI record), which --strict counts as a
# failure. --no-emit-project drops the local project from the export, so --strict keeps its real
# meaning: every third-party package must be auditable. Nothing is ignored, and pyproject.toml
# holds a click floor above its advisory.
# A real file rather than `/dev/stdin`, which is a Linux thing: Windows has no /dev, and pip-audit
# would answer "invalid requirements input: \proc\self\fd\0".
#
# The name it is HANDED is the native one. This script turns off the shell's automatic path
# translation (see MSYS_NO_PATHCONV above), so a POSIX temp path would reach a Windows program
# unchanged and be read as a relative path off the current drive. The shell reads and removes the
# file by its own name; the program is told the machine's.
#
# Cleaned up on both paths rather than by a RETURN trap. A trap set inside a function is not scoped
# to it, so it would fire again when the reporting harness returns, by which point the local
# variable it names is gone, and `set -u` would abort the whole run with "unbound variable".
_pip_audit() {
  local exported native status=0
  exported="$(mktemp -t sift-requirements.XXXXXX)"
  native="$exported"
  if command -v cygpath >/dev/null 2>&1; then native="$(cygpath -w "$exported")"; fi
  if ! "$UV" export --frozen --no-emit-project --extra dev --format requirements-txt > "$exported"
  then
    rm -f "$exported"
    return 1
  fi
  "$UV" run pip-audit --strict -r "$native" || status=$?
  rm -f "$exported"
  return "$status"
}
job_python_static() {
  step "lint: ruff check"            _ruff_check
  step "format: ruff format --check" _ruff_format
  step "types: mypy"                 _mypy
  step "semgrep: rules self-test"    _semgrep_test
  step "semgrep: scan"               _semgrep_scan
  step "audit: pip-audit"            _pip_audit
}

# The suite is subprocess-bound (real ffmpeg), so it scales across cores. Its worker count comes
# from the same budget as the coverage gates' fan-out: "-n auto" would claim every core, and with
# anything else running on the computer that oversubscribes it and tests fail from load alone.
_pytest_all() { "$UV" run pytest -n "$CI_JOBS"; }

# The query parser, put to arbitrary input. A different kind of gate from the two around it: the
# suite says the parser does what the examples say and coverage says every line was reached, while
# this says it cannot be made to misbehave by input nobody thought of. Hand-written, fed whatever
# somebody types into a search box, and the thing that decides what reaches an FTS5 match
# expression, so the claims are that it never raises, never widens a filter it failed to
# understand, and never emits an expression the database reads as anything but literals.
#
# Declared here AND as a step in the workflow, deliberately. The coverage gates share one list
# because there are seventeen of them and drift is certain; this is one command, and pinning it in
# both places is what makes a local run and a build agree about it.
#
# A fixed seed, so a failure here is reproducible rather than a build that went red once.
_pytest_parser_properties() {
  "$UV" run pytest src/sift/slices/search/tests/test_properties.py -p no:randomly --hypothesis-seed=0
}

# The coverage gates run concurrently, up to the budget at a time, and their output is replayed in
# declaration order once they are all done. Same labels, same PASS/FAIL lines, same per-gate
# durations, same failure summary as running them one at a time: the only difference a reader
# should be able to spot is that it finished sooner.
run_coverage_gates() {
  local idx
  printf '\n%b>> coverage gates: %d gates, %s at a time%b\n' \
    "$BOLD" "${#COV_FNS[@]}" "$CI_JOBS" "$RESET"
  run_cov_gates
  for (( idx = 0; idx < ${#COV_LABELS[@]}; idx++ )); do
    printf '\n%b>> %s%b\n' "$BOLD" "${COV_LABELS[$idx]}" "$RESET"
    cat "$COV_DIR/$idx.out"
    if [ "${COV_RCS[$idx]}" = "0" ]; then
      PASSES=$((PASSES + 1))
      printf '%bPASS%b  %s  %b(%ds)%b\n' \
        "$GREEN" "$RESET" "${COV_LABELS[$idx]}" "$DIM" "${COV_SECS[$idx]}" "$RESET"
    else
      FAILURES+=("${COV_LABELS[$idx]}")
      printf '%bFAIL%b  %s  %b(exit %d, %ds)%b\n' \
        "$RED" "$RESET" "${COV_LABELS[$idx]}" "$DIM" "${COV_RCS[$idx]}" "${COV_SECS[$idx]}" "$RESET"
    fi
  done
  cov_gates_cleanup
}
# The repository's own rules, the tier marked `unit` that quick.yml runs with this same command. The
# slow rest run in the whole suite (`--all`); tests/gates/test_ci_runs_where_sift_ships.py names each.
_pytest_gates() { "$UV" run pytest tests/gates -m unit -n "$CI_JOBS" --ignore=tests/gates/test_authz_matrix.py; }
job_gates() {
  step "gates: the repository's own rules, the unit tier" _pytest_gates
}

job_test() {
  # The full suite needs the built client. If that build fails, do NOT quietly return: pytest and
  # every coverage gate below would simply never run, and the summary's "passed" count would read
  # as healthy while nothing in the backend was actually tested. Say so, loudly, so a skipped
  # backend can never be mistaken for a green one.
  if ! ensure_web_build; then
    FAILURES+=("test: client build (prerequisite): PYTEST + ALL COVERAGE GATES SKIPPED, NOT PASSED")
    printf '%b!! client build failed: the ENTIRE Python suite and every coverage gate were SKIPPED.\n   The passed count below does NOT include the backend. This is a failure, not a pass.%b\n' "$RED" "$RESET"
    return
  fi
  step "test: full suite (pytest -n $CI_JOBS)" _pytest_all
  step "properties: the query parser against arbitrary input" _pytest_parser_properties
  run_coverage_gates
}

_web_case()     { ( cd frontend && npm run gate:case ); }
_web_licenses() { ( cd frontend && npm run gate:licenses ); }
_web_check()    { ( cd frontend && npm run check ); }
_web_format()   { ( cd frontend && npm run format:check ); }
# Run WITH coverage, so the suite runs once and the gate below reads what it wrote. Instrumenting
# costs about half a minute on top; running the whole suite twice costs more.
_web_unit()     { ( cd frontend && npm run test:unit:coverage ); }
_web_clientcov(){ ( cd frontend && node scripts/check_client_coverage.js ); }
_web_build()    { ensure_web_build; }
_web_offline()  { ( cd frontend && npm run gate:offline ); }
_web_tokens()   { ( cd frontend && npm run gate:tokens ); }
_web_tokdefs()  { ( cd frontend && npm run gate:tokens-exist ); }
_web_theme()    { ( cd frontend && npm run gate:theme ); }
_web_chrome()   { ( cd frontend && npm run gate:chrome ); }
_web_narration() { ( cd frontend && npm run gate:narration ); }
_web_deadcss()  { ( cd frontend && npm run gate:dead-css ); }
_web_bitsfirst(){ ( cd frontend && npm run gate:bits-first ); }
_web_designentries(){ ( cd frontend && npm run gate:design-entries ); }
_web_designfence(){ ( cd frontend && npm run gate:design-fence ); }
_web_dependencies(){ ( cd frontend && npm run gate:dependencies ); }
_web_handroll() { ( cd frontend && npm run gate:handrolled ); }
_web_hover()    { ( cd frontend && npm run gate:hover ); }
_web_onefield() { ( cd frontend && npm run gate:one-field ); }
_web_shortcuts() { ( cd frontend && npm run gate:shortcuts ); }
_web_scrollbar(){ ( cd frontend && npm run gate:one-scrollbar ); }
_web_capped()   { ( cd frontend && npm run gate:capped-scroller ); }
_web_styled()   { ( cd frontend && npm run gate:styled ); }
_web_handed()   { ( cd frontend && npm run gate:handed-class ); }
_web_csscomments(){ ( cd frontend && npm run gate:css-comments ); }
_web_anchored() { ( cd frontend && npm run gate:anchored-globals ); }
_web_windowchrome(){ ( cd frontend && npm run gate:window-chrome ); }
_web_effectloop(){ ( cd frontend && npm run gate:effect-load-loop ); }
_web_onefacts() { ( cd frontend && npm run gate:one-facts ); }
_web_onestep()  { ( cd frontend && npm run gate:one-step ); }
_web_oneservertype(){ ( cd frontend && npm run gate:one-server-type ); }
_web_settingsfollowed(){ ( cd frontend && npm run gate:settings-followed ); }
_web_tooltipname(){ ( cd frontend && npm run gate:tooltip-name ); }
_web_settingsrow(){ ( cd frontend && npm run gate:settings-row ); }
_web_onetimeformat(){ ( cd frontend && npm run gate:one-time-format ); }
_web_vocabulary(){ ( cd frontend && npm run gate:vocabulary ); }
_web_codeshape(){ ( cd frontend && npm run gate:code-shape ); }
_web_audit()    { ( cd frontend && npm audit --audit-level=high ); }
job_web() {
  ensure_web_deps || { FAILURES+=("web: npm ci (prerequisite)"); return; }
  # FIRST, because it is the one gate here that can only fail on one platform: two modules whose
  # paths differ by capitals are two files on Linux and one file on Windows and on any Mac.
  step "web: no two paths differ by capitals" _web_case
  step "web: licence compatibility"    _web_licenses
  step "web: svelte-check (types)"     _web_check
  step "web: prettier --check"         _web_format
  step "web: vitest unit"              _web_unit
  step "web: what the client tests reach" _web_clientcov
  step "web: production build"         _web_build
  step "web: nothing fetched off-box"  _web_offline
  step "web: colour only in tokens"    _web_tokens
  step "web: every token resolves"     _web_tokdefs
  step "web: one theme only"           _web_theme
  step "web: no native browser chrome" _web_chrome
  step "web: comments say why, never when or who" _web_narration
  step "web: bits-ui first, or why not" _web_bitsfirst
  step "web: every primitive declares itself" _web_designentries
  step "web: the fence around the design system" _web_designfence
  step "web: every dependency says why" _web_dependencies
  step "web: one scrollbar"            _web_scrollbar
  step "web: capped scrollers"         _web_capped
  step "web: every class is styled"    _web_styled
  step "web: a handed class is reachable" _web_handed
  step "web: every css comment closes" _web_csscomments
  step "web: every global rule is bounded" _web_anchored
  step "web: the title strip is cleared with padding" _web_windowchrome
  step "web: no effect loops on its own load" _web_effectloop
  step "web: one definition of a file's facts" _web_onefacts
  step "web: one step through a clip" _web_onestep
  step "web: every setting read is followed" _web_settingsfollowed
step "web: a tooltip is the name it announces" _web_tooltipname
step "web: a settings pane is a column of rows" _web_settingsrow
step "web: one time format, written once" _web_onetimeformat
step "web: the words on screen are the agreed ones" _web_vocabulary
  step "web: code length and comments may only fall" _web_codeshape
  step "web: the server's shapes described once" _web_oneservertype
  step "web: hand-rolled copies"       _web_handroll
  step "web: hover answers"           _web_hover
  step "web: one field"               _web_onefield
  step "web: undeclared shortcuts"    _web_shortcuts
  step "web: no style rule matches nothing" _web_deadcss
  step "web: npm audit (high)"         _web_audit
}

# THE SHELL, WHICH IS WHAT PEOPLE ACTUALLY INSTALL.
#
# `desktop/` has a test suite, a typecheck and a coverage ratchet (`gate:coverage` in its
# package.json). A ratchet that runs only when somebody remembers is no ratchet: a module with no
# test of any kind reads in a summary exactly like a module whose tests all pass.
#
# No format check, deliberately. Prettier is configured under `frontend/` only, so running it here
# would reformat this tree to another one's rules, which is a change to every file in it, dressed
# as a gate.
ensure_desktop_deps() {
  [ "$DESKTOP_DEPS_READY" = "1" ] && return 0
  marker=desktop/node_modules/.package-lock.json
  if [ ! -d desktop/node_modules ] || [ ! -f "$marker" ] || [ desktop/package-lock.json -nt "$marker" ]; then
    ( cd desktop && npm ci --no-audit --no-fund ) || return 1
  else
    printf '%breusing desktop/node_modules (delete it to force a clean install)%b\n' "$DIM" "$RESET"
  fi
  DESKTOP_DEPS_READY=1
}
_desktop_types()    { ( cd desktop && npm run typecheck ); }
_desktop_coverage() { ( cd desktop && npm run gate:coverage ); }
# The same threshold `job_web` holds the client to. NOT the bare one-liner the client
# uses: `desktop/` carries one high with no fix published anywhere, so a bare audit here
# would be red on the day it was added and read past for ever after. See the header of
# desktop/scripts/check_audit.mjs, which names the four and what would retire each.
_desktop_audit()    { ( cd desktop && npm run gate:audit ); }
job_desktop() {
  ensure_desktop_deps || { FAILURES+=("desktop: npm ci (prerequisite)"); return; }
  step "desktop: typecheck"                    _desktop_types
  step "desktop: what the shell's tests reach" _desktop_coverage
  step "desktop: npm audit (high)"             _desktop_audit
}

# The browser run's worker count, which is NOT the budget the rest of this file uses.
#
# The budget is sized for processes doing arithmetic; a Playwright worker is a browser, and the
# suite's real limit is not cores at all: the specs share one admin user and one server, so past a
# handful they contend over state rather than CPU. Handed the whole budget, the suite reports
# failures that have nothing to do with the code and pass on a quiet re-run.
#
# So: the smaller of the budget and four. Still bounded when a second checkout is working, and never
# more browsers than the shared fixtures can take. E2E_WORKERS carries no SIFT_ prefix because the
# server the run starts is the real application, which refuses to boot on a SIFT_ name it does not
# recognise.
E2E_CAP=4
_e2e() {
  local workers="$CI_JOBS"
  [ "$workers" -gt "$E2E_CAP" ] && workers="$E2E_CAP"
  ( cd frontend && E2E_WORKERS="$workers" npm run test:e2e )
}
_playwright_browsers() { ( cd frontend && npx playwright install chromium ); }
job_e2e() {
  ensure_web_deps || { FAILURES+=("e2e: npm ci (prerequisite)"); return; }
  step "e2e: install chromium (idempotent)" _playwright_browsers
  step "e2e: playwright"                     _e2e
}

_schema_regen() {
  "$UV" run python scripts/dump_openapi.py frontend/openapi.json || return 1
  ( cd frontend && npx openapi-typescript openapi.json -o src/lib/api/schema.d.ts )
}
_schema_diff() {
  git diff --exit-code -- frontend/openapi.json frontend/src/lib/api/schema.d.ts
}
job_schema() {
  ensure_web_deps || { FAILURES+=("schema: npm ci (prerequisite)"); return; }
  step "schema: regenerate client types" _schema_regen
  step "schema: types match the server"  _schema_diff
}

# No PY here: the script finds this platform's interpreter itself, and naming the POSIX one made
# it exit 127 on Windows.
# Every dependency can actually be INSTALLED on Windows at the interpreter the installer ships.
# A successful resolve is not this claim: a source-only package resolves perfectly and then demands
# a C compiler at install time, and half this set is compiled. Two seconds, and it is what decides
# whether `.python-version` may move.
_wheels() { "$UV" run python scripts/check_windows_wheels.py --extra dev; }
job_wheels() {
  step "wheels: every dependency has a Windows wheel" _wheels
}

_sbom_gen()      { "$UV" run bash scripts/sbom.sh sbom.cdx.json; }
_sbom_licenses() { "$UV" run python scripts/check_licenses.py sbom.cdx.json; }
job_sbom() {
  step "sbom: generate from lockfile" _sbom_gen
  step "sbom: licence compatibility"  _sbom_licenses
}

# The tools the installer ships, run for real: the ffmpeg and libwebp builds in vendor/bin, which
# Sift's own settings find in a checkout as they do in an installed copy, and the face runtime. The
# unit tests assert the arguments Sift hands ffmpeg; only these look at what came out, and an ffmpeg
# can accept an argument, write nothing and exit 0. A checkout without the vendored tools FAILS the
# first step rather than skipping: run against whatever ffmpeg the machine has, the rest would prove
# nothing about the one that ships.
SHIPPED_CORPUS=src/sift/kernel/tests/fixtures/ingress
_shipped_present() {
  [ -x vendor/bin/ffmpeg.exe ] || {
    echo "no vendor/bin/ffmpeg.exe: fetch the shipped tools with: uv run python scripts/fetch_vendor.py"
    return 1
  }
}
_shipped_ingress()       { "$UV" run python scripts/check_ingress_corpus.py "$SHIPPED_CORPUS"; }
_shipped_faces()         { "$UV" run python scripts/check_face_runtime.py; }
_shipped_stills()        { "$UV" run python scripts/check_derivatives.py "$SHIPPED_CORPUS"; }
_shipped_animated_webp() { "$UV" run python scripts/check_animated_webp.py "$SHIPPED_CORPUS"; }
_shipped_remux()         { "$UV" run python scripts/check_remux.py; }
_shipped_compress()      { "$UV" run python scripts/check_compress.py; }
_shipped_selftest()      { "$UV" run python scripts/check_selftest.py; }
job_shipped() {
  step "shipped: the vendored tools are here"         _shipped_present
  step "shipped: ingress gate on the shipped ffmpeg"  _shipped_ingress
  step "shipped: face runtime, and no model in it"    _shipped_faces
  step "shipped: a still from the shipped ffmpeg"     _shipped_stills
  step "shipped: animated WebP is readable"           _shipped_animated_webp
  step "shipped: the repair on the shipped ffmpeg"    _shipped_remux
  step "shipped: compressing on the shipped ffmpeg"   _shipped_compress
  step "shipped: measuring this machine on the shipped ffmpeg" _shipped_selftest
}

# ---------------------------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------------------------
# The default: what .github/workflows/quick.yml runs, so "would the pull request pass?" is one
# command. The gate tests need the built client, as the rest of the suite does.
run_quick() {
  job_secrets
  job_hygiene
  job_python_static
  job_sbom
  job_wheels
  job_web
  job_schema
  job_gates
  job_desktop
}

# The whole run includes the shipped tools: the ingress corpus against the ffmpeg that ships, the
# face runtime, the animated-WebP tools and the encodes are answered nowhere else, and a green run
# without them says nothing about what ships.
run_default() {
  job_secrets
  job_hygiene
  job_python_static
  job_web
  job_desktop
  job_schema
  job_test
  job_e2e
  job_wheels
  job_sbom
  job_shipped
}

TARGET="${1:-quick}"
printf '%bci-local: %s%b\n' "$BOLD" "$TARGET" "$RESET"
case "$TARGET" in
  quick|"")      run_quick ;;
  --all|all)     run_default ;;
  py|python)     job_python_static; job_test ;;
  web|client)    job_web ;;
  desktop|shell) job_desktop ;;
  test|tests)    job_test ;;
  gates)         job_gates ;;
  e2e)           job_e2e ;;
  secrets)       job_secrets ;;
  hygiene)       job_hygiene ;;
  schema)        job_schema ;;
  wheels)        job_wheels ;;
  sbom)          job_sbom ;;
  shipped)       job_shipped ;;
  -h|--help)
    # The header, however long it is: everything after the shebang up to the first line that is not
    # a comment, so the help never stops halfway when the header grows.
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit 0
    ;;
  *)
    echo "unknown target: $1 (try --help)" >&2
    exit 2
    ;;
esac

# ---------------------------------------------------------------------------------------------
# Summary.
# ---------------------------------------------------------------------------------------------
printf '\n%b=====================================%b\n' "$BOLD" "$RESET"
printf '%bpassed: %d   failed: %d   skipped: %d%b\n' "$BOLD" "$PASSES" "${#FAILURES[@]}" "${#SKIPS[@]}" "$RESET"
if [ "${#SKIPS[@]}" -gt 0 ]; then
  printf '%bskipped:%b\n' "$DIM" "$RESET"
  for s in "${SKIPS[@]}"; do printf '  %s\n' "$s"; done
fi
if [ "${#FAILURES[@]}" -gt 0 ]; then
  printf '%bfailed gates:%b\n' "$RED" "$RESET"
  for f in "${FAILURES[@]}"; do printf '  %s%s%s\n' "$RED" "$f" "$RESET"; done
  exit "${#FAILURES[@]}"
fi
printf '%ball gates passed%b\n' "$GREEN" "$RESET"
exit 0
