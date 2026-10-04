#!/usr/bin/env bash
#
# The per-module coverage gates, and the concurrency budget that both they and the full suite run
# under.
#
# This file exists so the local runner and the workflow cannot disagree: two copies of seventeen
# commands (steps in .github/workflows/suite.yml and functions in scripts/ci-local.sh) drift. Both
# call this.
#
# Why it is parallel: every gate is a pytest run over one module or one slice. They are the bulk of
# a run's wall clock and independent by construction, so they fan out.
#
# Two things make that safe, and neither announces itself when it is wrong:
#
#   1. Coverage writes its measurements to a data file in the working directory. Concurrent gates
#      sharing one would overwrite each other's numbers, and the result of that is not an error:
#      it is a fast, green, meaningless figure. Each gate therefore runs with its own COVERAGE_FILE
#      (the environment variable wins over the project's coverage configuration), in a scratch
#      directory thrown away at the end.
#
#   2. Output is captured per gate and replayed in the order the gates are declared, not the order
#      they happen to finish. Interleaved output from seventeen pytest runs is unreadable and
#      different every time; a log you cannot diff is a log nobody reads.
#
# Usage:
#   scripts/coverage_gates.sh            run every coverage gate, report, exit with the failure count
#   scripts/coverage_gates.sh --jobs     print the concurrency budget and exit
#   scripts/coverage_gates.sh --shard K/N [--list]
#                                        run (or list) only shard K of N: the gates whose position
#                                        in COV_LABELS, counted from nought, leaves K-1 over N
#
# Sourced, it defines COV_LABELS / COV_FNS (the gates, in order), run_cov_gates (fan out and wait)
# and the results the caller formats: COV_RCS, COV_SECS and the captured output under COV_DIR.

# ---------------------------------------------------------------------------------------------
# The concurrency budget. One number, honoured by the fan-out below AND by the full suite, because
# the two run in the same machine. Leaving the suite on "as many workers as there are cores" while
# the gates also fan out is how several checkouts running their gates at once put three times the
# processes on the machine that it has cores, and load-induced flakiness follows.
#
# The budget is a share of what is SPARE, not a share of the machine. Dividing every core between
# the checkouts assumes CI is the only thing running, and a machine that also carries other
# long-running work is left with nothing for it. So a reserve comes off the top first, and only the
# remainder is divided.
#
#   jobs = (cores - reserve) / checkouts, floor of two
#
# Defaults: a quarter of the cores held back (minimum two), the rest divided between the
# checkouts. Floor of two so a small hosted runner still runs something. Every term is
# overridable: SIFT_CI_JOBS forces the answer outright; SIFT_CI_RESERVE changes how much is held
# back for everything else; SIFT_CI_CHECKOUTS says how many runs to size for, so a machine running
# ONE checkout can say so and get the cores back rather than being throttled for absent siblings.
#
# This does not stop three runs colliding: nothing here coordinates between checkouts, and the
# budget is per-run by construction. It bounds the damage when they do.
# ---------------------------------------------------------------------------------------------
# Resolve the budget once, then take the three knobs OUT of the environment.
#
# They are read from the environment, which means the ordinary way to pass one (`SIFT_CI_CHECKOUTS=1
# scripts/ci-local.sh`) exports it to everything the run starts. Some of what a run starts is the
# application itself: the integration gates build it, and the end-to-end run serves it. It refuses to
# boot on a SIFT_ variable it does not recognize, which is a deliberate guard against a typo in a real
# setting and cannot tell one of these from one of those. The whole of a run then fails with the same
# configuration error, many times over, and none of the messages mention the variable that caused
# it.
#
# So the value is resolved into CI_JOBS_RESOLVED (a name the application has no opinion about), and
# the knobs are unset. Call this before anything else; sift_ci_jobs honours the resolved value
# afterwards, including from the fan-out, which asks again on its own.
sift_ci_take_env() {
  CI_JOBS_RESOLVED="$(sift_ci_jobs)"
  unset SIFT_CI_JOBS SIFT_CI_RESERVE SIFT_CI_CHECKOUTS
}

# How many checkouts to size the budget for: count them rather than assume.
#
# Not a fixed three. Most of the time there is one checkout, and sizing for three absent siblings
# hands a 24-core box six cores and leaves eighteen idle. The coverage gates are the bulk of a run
# and they are almost perfectly parallel, so that is most of the wall clock, spent waiting for
# nothing.
#
# `git worktree list` names the main checkout plus every linked one, which is exactly the set that
# can be running a gate. Falls back to one outside a repository, and never to zero.
_sift_ci_checkouts() {
  local n
  n="$(git worktree list 2>/dev/null | wc -l)" || n=1
  [ -z "$n" ] || [ "$n" -lt 1 ] && n=1
  printf '%s\n' "$n"
}

sift_ci_jobs() {
  if [ -n "${CI_JOBS_RESOLVED:-}" ]; then
    printf '%s\n' "$CI_JOBS_RESOLVED"
    return 0
  fi
  if [ -n "${SIFT_CI_JOBS:-}" ]; then
    printf '%s\n' "$SIFT_CI_JOBS"
    return 0
  fi
  local cores reserve checkouts n
  cores="$(nproc 2>/dev/null || echo 2)"
  reserve="${SIFT_CI_RESERVE:-$(( cores / 4 ))}"
  [ "$reserve" -lt 2 ] && reserve=2
  checkouts="${SIFT_CI_CHECKOUTS:-$(_sift_ci_checkouts)}"
  [ "$checkouts" -lt 1 ] && checkouts=1
  n=$(( (cores - reserve) / checkouts ))
  [ "$n" -lt 2 ] && n=2
  printf '%s\n' "$n"
}

# ---------------------------------------------------------------------------------------------
# A run that starts onto an already-buried machine produces a result nobody can trust: the failures
# it reports may be nothing but timeouts under load. This does not refuse to start (deciding that
# for the operator would be worse than the problem): it says the number out loud so a red result
# arriving twenty minutes later can be read in the light of it.
# ---------------------------------------------------------------------------------------------
sift_ci_load_warning() {
  local cores load
  cores="$(nproc 2>/dev/null || echo 2)"
  # Field 1 of /proc/loadavg is the one-minute average; cut the fraction rather than round it.
  load="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null | cut -d. -f1)" || return 0
  [ -z "$load" ] && return 0
  if [ "$load" -ge "$cores" ]; then
    printf '\n!! load is %s on %s cores before this run has started.\n' "$load" "$cores" >&2
    printf '!! Something else is already using this machine. A failure from this run may be\n' >&2
    printf '!! load-induced rather than real. Re-run it on a quiet box before believing it.\n\n' >&2
  fi
}

# ---------------------------------------------------------------------------------------------
# The gates. One function each, and a label that names the module or slice it measures: the label
# is what a failure is reported against, so it has to say which gate fell over.
# ---------------------------------------------------------------------------------------------
# Each gate gets processes of its own, and the fan-out narrows to pay for them.
#
# Single-process gates, parallel only with each other, make the whole phase as slow as its slowest
# gate no matter how many run at once, and one of them is minutes long on its own. Splitting
# inside each gate moves the floor instead of the queue, and the coverage figure is the same.
#
# Coverage is still exact under this. Each worker writes its own data file and they are combined
# before the number is taken, which is what pytest-cov does with xdist by design: the 100% above
# is taken from the combined result, not from one worker's share.
COV_INNER="${COV_INNER:-4}"

# A virtual environment puts its executables in `bin/` on POSIX and in `Scripts/` with an `.exe`
# on Windows; a fallback that knew only the first could never fire on the platform Sift ships on.
# Shared by the whole run and by a gate sourced alone.
_find_uv() {
  if [ -z "${UV:-}" ]; then
    if command -v uv >/dev/null 2>&1; then
      UV="uv"
    elif [ -x .venv/bin/uv ]; then
      UV="$PWD/.venv/bin/uv"
    elif [ -x .venv/Scripts/uv.exe ]; then
      UV="$PWD/.venv/Scripts/uv.exe"
    else
      echo "uv not found on PATH, at .venv/bin/uv or at .venv/Scripts/uv.exe. Run: uv sync --extra dev" >&2
      return 2
    fi
  fi
  export UV
}

_cov() {  # _cov <test-path>... <--cov=...>...
  _find_uv || return 2
  local split=()
  [ "$COV_INNER" -gt 1 ] && split=(-n "$COV_INNER")
  # A gate sourced on its own (someone running `_cov_<name>` by hand, beside another doing the same)
  # would otherwise write the working directory's one data file, and two at once overwrite each
  # other's numbers into a green, meaningless figure (point 1 above). The whole run sets
  # COVERAGE_FILE per gate below; a lone call gets a file of its own here and clears it up after.
  local own="" rc
  if [ -z "${COVERAGE_FILE:-}" ]; then
    own="$(mktemp "${TMPDIR:-/tmp}/sift-cov.XXXXXX" 2>/dev/null)" || own="$PWD/.coverage.sift-cov.$$"
    export COVERAGE_FILE="$own"
  fi
  "$UV" run pytest "$@" "${split[@]}" --cov-branch --cov-report=term-missing --cov-fail-under=100
  rc=$?
  if [ -n "$own" ]; then
    unset COVERAGE_FILE
    rm -f "$own" "$own".*
  fi
  return "$rc"
}

# Ingress decides whether untrusted bytes are allowed in at all. An untested line here is not a
# coverage statistic, it is a way into the system that nobody has looked at.
_cov_ingress() { _cov src/sift/kernel/tests/test_ingress.py --cov=sift.kernel.ingress; }
# Held at 100% for a different reason: an untested branch in the claim, the fence or the reclaim is
# not a missing feature, it is work run twice or work lost, and both of those look exactly like
# success from the outside, right up until a user notices their library is half-scanned.
# `paging` rides along because the queue re-exports the page ceiling from it, so it is pinned at
# the same floor every other kernel module has rather than being the one module with no gate. Its
# own test file is named too, since `resume_at` (where a page asked for by a gone row begins) is
# exercised there and nowhere in the queue's tests.
# The schedule table and the switchboard are named beside the queue's own tests for the reason
# the main gate states below: they are measured by `--cov=sift.kernel.jobs`, and with their tests
# not run the gate would report tested code as untested.
_cov_jobs() {
  _cov src/sift/kernel/tests/test_jobs.py src/sift/kernel/tests/test_jobs_settle.py \
    src/sift/kernel/tests/test_jobs_workers.py src/sift/kernel/tests/test_jobs_reads.py \
    src/sift/kernel/tests/test_jobs_controls.py src/sift/kernel/tests/test_jobs_pool.py \
    src/sift/kernel/tests/test_ledger.py \
    src/sift/kernel/tests/test_job_schedules.py src/sift/kernel/tests/test_switchboard.py \
    src/sift/kernel/tests/test_paging.py \
    src/sift/kernel/tests/test_task_timing.py src/sift/kernel/tests/test_work_ahead.py \
    src/sift/kernel/tests/test_failure_words.py src/sift/kernel/tests/test_jobs_line.py \
    src/sift/kernel/tests/test_waiting_for_password.py src/sift/kernel/tests/test_history_pressed.py \
    --cov=sift.kernel.jobs --cov=sift.kernel.paging
}
# What Sift is running on. Its own gate, because measured as part of the queue's it would be
# measured while only the queue's tests ran: a module pinned to a test file that is never opened
# is a gate that reports on nothing.
_cov_hardware() { _cov src/sift/kernel/tests/test_hardware.py --cov=sift.kernel.hardware; }
# What each pass over a library cost, and on what machine. Its own gate rather than riding on the
# scanner's: the scanner writes a run and the Performance screen reads it, so its lines are executed
# incidentally by any test that scans anything, and incidental execution is not a test.
_cov_benchmarks() { _cov src/sift/kernel/tests/test_benchmarks.py --cov=sift.kernel.benchmarks; }
# Reading the tail of the application log. Small, and the seeking read under it is the whole of it:
# the ceiling is a gigabyte, so a version that read the file to find its end would be correct on
# every test fixture and unusable on a real log.
_cov_logs() { _cov src/sift/slices/logs/tests --cov=sift.slices.logs; }
# The typed lookup every route reads the application through, and the sealing helpers under it.
# Both are on the path of ordinary requests, which is exactly why they are pinned: their lines are
# executed by any test that calls any route, so their coverage reads as high whether or not one
# line of either has been checked. Incidental execution is not a test. `secrets` earns it twice
# over: it is what stands between a stolen database file and the saved site logins in it.
_cov_wiring() { _cov src/sift/kernel/tests/test_wiring.py src/sift/kernel/tests/test_secrets.py --cov=sift.kernel.wiring --cov=sift.kernel.secrets; }
# Identity is the one thing in Sift that cannot be rebuilt. Thumbnails, previews and the search
# index are derived from the files and can be thrown away; which asset a file IS is the row every
# tag, rating and collection hangs off. A bug here does not lose a feature, it loses the library.
# Per-user state is held to the same bar for a narrower reason: it is the one table keyed on a
# person, so a query that loses the user from its predicate shows one user another's ratings.
# The country names a nationality is said by, on the server: the client has the same list and a
# gate holds the two equal, so the server copy is read by the disagreement words and that gate.
_cov_countries() { _cov src/sift/slices/stash_boxes/tests/test_disagreement_words.py tests/gates/test_country_names_agree.py --cov=sift.kernel.countries; }
_cov_content() { _cov src/sift/kernel/tests/test_pipeline_certainty.py src/sift/kernel/tests/test_a_bar_counts_one_set_of_files.py src/sift/kernel/tests/test_content.py src/sift/kernel/tests/test_content_derivatives.py src/sift/kernel/tests/test_content_places.py src/sift/kernel/tests/test_content_tables.py src/sift/kernel/tests/test_content_user_state.py src/sift/kernel/tests/test_content_stamps.py src/sift/kernel/tests/test_duplicates.py src/sift/kernel/tests/test_content_schema_baseline.py src/sift/kernel/tests/test_migration_helpers.py src/sift/kernel/tests/test_song_sources.py --cov=sift.kernel.content.identity --cov=sift.kernel.content.identity_arrivals --cov=sift.kernel.content.identity_counts --cov=sift.kernel.content.identity_derivatives --cov=sift.kernel.content.identity_fields --cov=sift.kernel.content.identity_models --cov=sift.kernel.content.identity_paths --cov=sift.kernel.content.identity_places --cov=sift.kernel.content.identity_probes --cov=sift.kernel.content.identity_store --cov=sift.kernel.content.identity_verdicts --cov=sift.kernel.content.hashing --cov=sift.kernel.content.schema --cov=sift.kernel.content.user_state --cov=sift.kernel.content.entity_state --cov=sift.kernel.content.duplicates --cov=sift.kernel.content.presence --cov=sift.kernel.migrations; }
# The library store decides what a root may be and moves real folders on disk. Its refusals (a
# root that overlaps another, or that sits inside a directory Sift writes to) are the difference
# between reading a person's files and writing into them.
# Both files. The grants half of this module is driven from the slice that offers it (the
# folder picker is confined to grants, so that is where the tests for them were written),
# and naming only the kernel's own file would leave that half measured by nothing.
_cov_library() {
  _cov src/sift/kernel/tests/test_library.py src/sift/slices/library_roots/tests/test_grants.py \
    src/sift/slices/library_roots/tests/test_arranging.py \
    src/sift/kernel/tests/test_library_grant_uses.py \
    --cov=sift.kernel.content.library
}
# The fingerprint that finds near-duplicates. Its numbers are stored, so a change here does not
# break anything visibly: it quietly stops matching every fingerprint taken before it.
_cov_perceptual() { _cov src/sift/kernel/tests/test_perceptual.py --cov=sift.kernel.content.perceptual; }
# A line here that no test runs is not a gap in coverage, it is a way for somebody to see what they
# were not shared.
# Every migration test that walks this package forward has to be NAMED here. The list is files
# rather than a directory, so a new one is invisible to this gate until somebody adds it, and the
# branches it is the only thing to reach then read as uncovered code with no test.
# The access package is exercised by every kernel test that reads it (the history tests, the
# waiting tests, every migration test), and a hand-written list of them drifts: a test added for a
# new module is missed here, and the gate reports its lines uncovered. So the list is derived from
# the tests' own imports, plus the slice tests named below.
_access_tests() { grep -lE 'sift\.kernel\.access' src/sift/kernel/tests/test_*.py; }
# shellcheck disable=SC2046  # one path per word; none of these paths holds a space
# The slice tests named beside the kernel's are the ones that prove access modules written for a
# slice (a studio's page, a song's wall, a tag's tree, a Site's filing, a refusal's weight): measured
# by per-test coverage contexts over every slice test, not guessed, so the gate reads tested code
# as tested.
_cov_access() {
  _cov $(_access_tests) src/sift/slices/people/tests/test_site_merge.py \
    src/sift/slices/people/tests/test_merge.py src/sift/slices/people/tests/test_enrich.py \
    src/sift/slices/people/tests/test_facets.py src/sift/slices/sharing/tests/test_sharing.py \
    src/sift/slices/swap/tests/test_weight.py src/sift/slices/swap/tests/test_refusal.py \
    src/sift/slices/stash_boxes/tests/test_creator_studios.py \
    src/sift/slices/stash_boxes/tests/test_api_links.py \
    src/sift/slices/faces/tests/test_each_look_on_history.py \
    src/sift/slices/workbench/tests/test_ledger_route.py src/sift/slices/songs/tests/test_songs.py \
    src/sift/slices/tags_ratings/tests/test_tag_tree.py \
    src/sift/slices/photo_sets/tests/test_origin_step.py \
    src/sift/slices/download/tests/test_filed_by_id.py \
    src/sift/slices/watermarks/tests/test_filing.py src/sift/slices/stash_migration/tests/test_run.py \
    src/sift/slices/suggestions/tests/test_suggestions.py \
    --cov=sift.kernel.access
}
# The one place any external tool is started. Everything heavy Sift does happens on the far side of
# this file (ffmpeg, ffprobe, the downloaders), so an untested branch here is not a coverage
# statistic either: it is a tool left running after the work it belonged to was abandoned, or a
# scheduling setting that was assembled and quietly never applied. Both look like success.
_cov_mp4() { _cov src/sift/kernel/tests/test_mp4.py --cov=sift.kernel.mp4; }

_cov_subprocess() { _cov src/sift/kernel/tests/test_subprocess.py src/sift/kernel/tests/test_background_priority.py --cov=sift.kernel.subprocess; }
# The proxy every download tool goes out through. A branch here that lets a private address past
# is a tool reaching into the home network on a stranger's say-so, and it looks like a download.
_cov_public_net() { _cov src/sift/kernel/tests/test_public_net.py --cov=sift.kernel.public_net; }
_cov_ports() { _cov src/sift/kernel/tests/test_ports.py --cov=sift.kernel.ports; }
# Deciding whether a browser already holds a file, and answering with no body when it does. Every
# branch here either sends bytes or declines to, and the one that gets it wrong hands somebody a
# copy of something they were allowed to see once and have not been re-checked for since.
_cov_serving() { _cov src/sift/kernel/tests/test_serving.py --cov=sift.kernel.serving; }
# The picture six different things are drawn as, served by one function. Every branch here
# either sends a picture or declines to, and the one that gets it wrong hands somebody the
# cover of something they may not open, which is a leak wearing the shape of a thumbnail.
# Both test files: the frame half of `covers.py` (a still cut from a video) is tested beside the
# cover-frame reader, and a gate that opened only the first file reported those lines uncovered.
_cov_covers() { _cov src/sift/kernel/tests/test_covers.py src/sift/kernel/tests/test_cover_frame.py --cov=sift.kernel.covers; }
# The site logos that ship with Sift, and finding one by host or by name. A branch here that lets a
# slug off the wire become part of a path is a directory to walk out of; one that matches the wrong
# host draws somebody else's logo beside a site's name on every wall at once.
_cov_site_icons() { _cov src/sift/kernel/tests/test_site_icons.py --cov=sift.kernel.site_icons; }
# A vector logo drawn to a picture before the cover door re-encodes it. A branch here that lets a
# script or an outside reference through is the one door an untrusted file has into the process.
_cov_svg_raster() { _cov src/sift/kernel/tests/test_svg_raster.py --cov=sift.kernel.svg_raster; }
# How a list of names is put in order. A branch here that folds the wrong thing puts a name
# where nobody would look for it, on every wall at once, and nothing fails.
_cov_sorting() { _cov src/sift/kernel/tests/test_sorting.py --cov=sift.kernel.sorting; }
# The counter that rides in every keepable picture address. A branch here that fails to raise it is
# a permission change that leaves the pictures behind it readable out of a browser's own store,
# with no request and so no check, and nothing on any screen would show it.
_cov_cache_stamp() { _cov src/sift/kernel/tests/test_cache_stamp.py --cov=sift.kernel.cache_stamp --cov=sift.kernel.access.stamps; }
# Who is connected and what each of them is waiting to be told. A branch here that fails to tell
# somebody is a screen quietly showing what it was showing an hour ago, and a branch that tells the
# wrong somebody is a message about a thing they may not see, which, carrying no payload at all,
# still says that the thing exists.
_cov_changes() { _cov src/sift/kernel/tests/test_changes.py --cov=sift.kernel.changes --cov=sift.kernel.audience; }
_cov_download() { _cov src/sift/slices/download/tests --cov=sift.slices.download; }
# The tunnels: the client process and the refusals that keep a pasted configuration from opening a
# listener, the routes and the refusal that keeps a routed site from going out directly, and hosting
# a swap: the listener, the NAT-PMP asking and its renewal. An untested branch here is a download
# sent out of the wrong address, or a port left open after the swap it was opened for.
_cov_tunnels() {
  _cov src/sift/kernel/tests/test_tunnels_process.py src/sift/kernel/tests/test_tunnels_store.py \
    src/sift/kernel/tests/test_tunnels_egress.py src/sift/kernel/tests/test_tunnels_natpmp.py \
    src/sift/kernel/tests/test_tunnels_schema.py src/sift/kernel/tests/test_tunnels_client.py \
    --cov=sift.kernel.tunnels
}
# Turning two spellings of one link into one string. Held at 100% because one of the two functions
# here IS the ledger key: a branch that quietly widens what it strips re-keys every URL already
# stored, and the file downloads a second time with nothing on any screen saying why.
# Telling "the caller said nothing" apart from "clear it". Four lines of code and the reason for
# every partial write in the application: an untested branch here is a column blanked by a request
# that never mentioned it.
_cov_partial_write() {
  _cov src/sift/kernel/tests/test_partial_write.py --cov=sift.kernel.partial_write
}
# What a viewer can reach, and what the app says when they cannot. Every branch is a sentence on a
# screen or a status a client turns into one: an untested one is a person told their file has been
# deleted when it is sitting in their own vault, or offered an Unlock button that unlocks nothing.
_cov_reach() { _cov src/sift/kernel/tests/test_reach.py --cov=sift.kernel.reach; }
# Which kind of window and device a request came from. An untested branch is a sitting Insights
# files under the wrong client.
_cov_kernel_client() { _cov src/sift/kernel/tests/test_client.py --cov=sift.kernel.client; }
# The number of pictures that makes a Photo Set, which three features' sentences are built from.
_cov_kernel_photo_sets() {
  _cov src/sift/slices/photo_sets/tests/test_derive.py --cov=sift.kernel.photo_sets
}
# Who pressed a pass over one file. An untested branch is a History line naming the wrong person,
# or Sift, for work somebody asked for.
_cov_presses() { _cov src/sift/kernel/tests/test_history_pressed.py --cov=sift.kernel.presses; }
# A User's own history, paused and cleared. Every feature that writes a part of it reads this, so an
# untested branch is a pause that one of them does not honour.
_cov_use_history() {
  _cov src/sift/slices/insights/tests/test_capture.py \
    src/sift/slices/player/tests/test_sitting_capture.py --cov=sift.kernel.use_history
}
_cov_urls() { _cov src/sift/kernel/tests/test_urls.py --cov=sift.kernel.urls; }
# What a stash-box's answer is ALLOWED to write. Every branch here is a rule about somebody's
# records: an untested one is either a field silently overwritten or a field silently dropped, and
# neither shows up anywhere except on the page it happened to.
_cov_enrichment() { _cov src/sift/kernel/tests/test_enrichment.py --cov=sift.kernel.enrichment; }
# Turning a stash-box's names into rows. The one file that names two slices together, and the one
# place a bulk import can invent a person, so "creating is permission, not intent" is measured
# rather than assumed.
_cov_composition() { _cov src/sift/kernel/tests/test_composition.py --cov=sift.composition; }
# Pacing requests to somebody else's service. The failure mode of an untested branch here is the
# account belonging to the person running Sift being restricted, which no retry recovers.
_cov_ratelimit() { _cov src/sift/slices/download/tests/test_ratelimit.py --cov=sift.kernel.ratelimit; }
# Sealing a value Sift must never be able to read on its own. Every branch, for the obvious reason.
_cov_secret_store() { _cov src/sift/slices/download/tests/test_secrets.py --cov=sift.kernel.secret_store; }
# The stash-boxes Sift can ask about a person or a file. The whole slice: the adapter is where a 200
# carrying an error is told apart from an empty answer, and the service is where a question is
# decided not to be asked at all.
_cov_stash_boxes() { _cov src/sift/slices/stash_boxes/tests --cov=sift.slices.stash_boxes; }
# The whole slice package, not a single module: capture is a router with no schema, so its logic is
# spread across pipeline/service/jobs/router. Its own tests are omitted from the figure in the
# coverage config, so this measures the slice and not the suite that exercises it.
_cov_capture() { _cov src/sift/slices/capture/tests --cov=sift.slices.capture; }
# The grid decides what each user is allowed to see, and serves the bytes behind that answer.
_cov_browse() { _cov src/sift/slices/browse/tests --cov=sift.slices.browse; }
# Playback: the routes that hand over the media itself, plus the cache whose cap is what keeps a
# long viewing session from filling the disk.
_cov_player() { _cov src/sift/slices/player/tests --cov=sift.slices.player; }
# The library slice reads somebody's folders and, through the picker, enumerates directories on the
# machine Sift runs on. The confinement that keeps that inside the media area, an admin-only check
# in front of it, and the refusal to call a folder managed when it cannot be written are all here.
_cov_library_roots() { _cov src/sift/slices/library_roots/tests --cov=sift.slices.library_roots; }
# The one feature allowed to remove a file. Everything in it is either a refusal or a destructive
# act, so there is no line here that does not matter.
_cov_delete() { _cov src/sift/slices/delete/tests --cov=sift.slices.delete; }
# Tags decide what a grid filter matches and what a count says, and both are permission-scoped: the
# refusal that reads like a miss, the count that leaves out what was never shared, and the grants a
# deleted tag has to take with it are all here.
_cov_tags_ratings() { _cov src/sift/slices/tags_ratings/tests --cov=sift.slices.tags_ratings; }
# Who may sign in at all, and what a session is worth once they have. Everything downstream trusts
# the answer this slice gives: the role on every request, the disabled flag that has to bite on
# the next one rather than the next login, the CSRF token, and the user-management routes that
# are the only way a second user has ever come into being. An untested branch here is not a
# missing feature, it is a door.
#
# The console password-reset tool is not in the figure. It is named in the omit list in
# pyproject.toml, with the reason and what removing it costs.
_cov_auth() { _cov src/sift/slices/auth/tests --cov=sift.slices.auth; }
# Producing the grants the resolver reads. This slice enforces nothing (the resolver decides, on
# every request), so what is held to 100% here is the other half: that a grant which could never
# mean anything is refused rather than stored, and that every route is admin-only. An inert row is
# worse than no row, because the panel lists it as being in force; and a restrict that is quietly
# inert is a promise nobody is keeping.
_cov_sharing() { _cov src/sift/slices/sharing/tests --cov=sift.slices.sharing; }
# Attribution decides who a name belongs to, and the answer is permission-scoped: the person
# concealed from a list, the count that leaves out what was never shared, the site that refuses to
# take its usernames down with it, and the grants a deleted person has to take with them.
_cov_people() { _cov src/sift/slices/people/tests --cov=sift.slices.people; }
# A collection is a curated sequence, and three separate things here are permission-scoped: the
# items it shows, the count beside it, and the cover it wears. Each of those is a way to report the
# size of a set somebody was not shown. The rest is the sequence itself, and the grants a deleted
# collection has to take with it.
_cov_collections() { _cov src/sift/slices/collections/tests --cov=sift.slices.collections; }
# Duplicate-finding proposes removals over somebody's whole library, and both of its surfaces are
# admin-only. Every line here is either a refusal, a comparison that decides whether two files are
# the same, or the seam that hands a removal to the one feature allowed to make one.
_cov_dedup() { _cov src/sift/slices/dedup/tests --cov=sift.slices.dedup; }
# A photo set is a grouping over files, so the same three things are permission-scoped that a
# collection's are: what it shows, the count beside it, and the cover it wears, each a way to
# report the size of a set somebody was not shown. The rest is the promise that grouping pictures
# moves none of them, and the grants a deleted set has to take with it.
_cov_photo_sets() { _cov src/sift/slices/photo_sets/tests --cov=sift.slices.photo_sets; }
# Proposing a Photo Set out of pictures nobody grouped. The lines that matter are the ones that
# decide NOT to propose (the distance, the fewest and the most), and a lost branch in any of them
# is a card proposing a grouping nobody can check, on somebody else's library.
_cov_shoots() { _cov src/sift/slices/shoots/tests --cov=sift.slices.shoots; }
# The music in a file: the fingerprint, which files share a song (`matching.py`), the names Sift
# spreads through them and takes back (`names.py`), and the AcoustID lookup (`acoustid.py`,
# `lookup.py`). The lines that matter are the ones that decide NOT to act: the fingerprint a scan
# only claims and never reads, the folder that did not say yes, the pair under the line, the name
# that would overwrite one or come back after an Undo, and the lookup with its switch off. A lost
# branch in any of them is a machine decoding somebody's library for days unasked, a wrong song on
# their files, or a fingerprint sent to AcoustID that nobody allowed. A directory, so every test
# file the slice gains is run by this gate the day it is written.
# The kernel's song writers and readers (`seed_music_on`, `songs_of`, `song_and_length`) are
# measured by the content gate, whose list names `test_song_sources.py`.
_cov_music() { _cov src/sift/slices/music/tests --cov=sift.slices.music; }
# A song as a thing of the library: the one door a file's song is written through
# (`kernel/content/songs.py`) and the Songs page's routes. The lines that matter are the ones that
# keep ONE home for a file's song: a name that joins the song it means, a recording that takes over
# a name-only song, the Music field moved with a rename, a merge or a delete, and a song taken off
# by hand staying off. A lost branch in any of them is two songs for one piece of music, or a name
# put back on a file somebody took it off.
_cov_songs() { _cov src/sift/kernel/tests/test_songs.py src/sift/kernel/tests/test_song_sources.py src/sift/slices/songs/tests src/sift/slices/music/tests/test_names.py --cov=sift.kernel.content.songs --cov=sift.slices.songs; }
# A swap with another install: the device id, the token, the lock, the session, the offer, the
# guest's diff, the strip and the landing. The lines that matter are the refusals (a token run
# out or used twice, a hello for another session, a chunk whose digest disagrees, a file nobody
# asked for, a photograph's location, a concealed or kept-local file offered), and a lost branch in
# any of them is something of one library leaving, or landing in, another that nobody chose. A
# directory, so every test file the slice gains is run by this gate the day it is written. The
# lock's tests need CPython 3.13 (TLS keyed by a pre-shared key) and skip on anything older.
_cov_swap() { _cov src/sift/slices/swap/tests --cov=sift.slices.swap; }
# The server's desktop app reached from another computer: each act a named route, refused in words
# when no app is behind the backend.
_cov_desktop() { _cov src/sift/slices/desktop/tests --cov=sift.slices.desktop; }
# An act on the computer running Sift, asked from a window: one door that records who, what and from where.
_cov_machine_acts() { _cov src/sift/kernel/tests/test_machine_acts.py --cov=sift.kernel.machine_acts; }
_cov_watermarks() { _cov src/sift/slices/watermarks/tests --cov=sift.slices.watermarks; }
# The Tasks screen and the quiet-hours keep-awake request. Small, and its own gate because
# the keep-awake call is the one place Sift asks the operating system for anything about
# power: a branch nobody measures here is a laptop that never sleeps, or work that never
# runs, and neither shows on any screen.
_cov_tasks() { _cov src/sift/slices/tasks/tests --cov=sift.slices.tasks; }
# A loop is the one thing here anybody signed in may create, so its refusals carry more than most:
# who may move or remove somebody else's Loop, and the join that means a Loop of a film you cannot
# see is not a row at all rather than a row with the details left out.
_cov_loops() { _cov src/sift/slices/loops/tests --cov=sift.slices.loops; }
# The counts on an entity page's tab strip, which is one route and no table. Small, and worth its
# own gate for one reason: every number it answers with is scoped, and an untested branch here is a
# count taken over a population somebody was not shown, which is the size of the set the whole
# permission model exists to keep back.
_cov_records() { _cov src/sift/slices/records/tests --cov=sift.slices.records; }
_cov_related() { _cov src/sift/slices/related/tests --cov=sift.slices.related; }
# The first thing Sift ingests that it cannot verify by DECODING. Every refusal here (zip-slip, a
# bomb, an encrypted member, an index that lies about a size) is the only thing standing between a
# stranger's archive and the cache directory, and an untested branch is one of those not firing.
_cov_archives() { _cov src/sift/kernel/tests/test_archives.py --cov=sift.kernel.archives; }
# Folder-led attribution writes People, aliases and attributions off a folder NAME, which is the
# weakest evidence anything in Sift acts on. An untested branch here is either a wrong attribution
# nobody is auditing or a suggestion that named something concealed.
# What runs when a file arrives. An untested branch here is a job that is queued when it should not
# be, or not queued when it should, and both look like nothing happening, which is precisely how
# faults of this kind go unnoticed.
_cov_importing() { _cov src/sift/slices/importing/tests --cov=sift.slices.importing; }
_cov_suggestions() { _cov src/sift/slices/suggestions/tests --cov=sift.slices.suggestions; }
# The other feature allowed to change a file somebody else put there. A rename that lands on top of
# something destroys it as surely as a delete does, so every refusal in here is load-bearing.
_cov_organize() { _cov src/sift/slices/organize/tests --cov=sift.slices.organize; }
# The vault is the one feature whose whole job is that something is NOT in an answer, and a gap
# here does not look like a gap: an untested branch is a route that hands back what somebody
# deliberately hid, and it reads as working right up until the moment it matters most.
_cov_vault() { _cov src/sift/slices/vault/tests --cov=sift.slices.vault; }
# The search box is the one screen somebody can PROBE with: every other view shows what it was
# handed, this one takes a guess and says whether it was right. An untested line here is a way to
# find out that a file exists: through a result, through a count, or through a dropdown offering
# a name. The parser is held to the same bar for a different reason: it is hand-written, it is fed
# arbitrary text, and it decides what reaches a query.
_cov_search() { _cov src/sift/slices/search/tests --cov=sift.slices.search; }
# The database is the one thing here that cannot be rebuilt from the files on disk, and this is
# what copies it and what puts a copy back. A gap on the export side is a backup that opens and is
# quietly incomplete; a gap on the restore side is a live database replaced by something that was
# never checked. Neither looks wrong until the day somebody needs the backup.
_cov_backup() { _cov src/sift/slices/backup/tests --cov=sift.slices.backup; }
# Where a fetched file lands: the one question both downloads and captured links ask before any
# bytes move, so a folder that is gone or never chosen fails at once and not after the fetch.
_cov_destination() { _cov src/sift/slices/capture/tests/test_pipeline.py src/sift/slices/download/tests/test_jobs.py src/sift/slices/download/tests/test_jobs_landing.py --cov=sift.kernel.destination; }
# The naming words: the tokens, the fill, the tidying and the next number, read by a download's
# name and by a batch rename. Proved by the kernel's own test and by the download names that use it.
_cov_naming() { _cov src/sift/kernel/tests/test_naming.py src/sift/slices/download/tests/test_naming.py src/sift/slices/download/tests/test_naming_words.py --cov=sift.kernel.naming; }
# Bringing a Stash library in: the reader (a copy through the backup API, a schema it accepts), the
# matching by folder and hash, and the run through the writers.
_cov_stash_migration() { _cov src/sift/slices/stash_migration/tests --cov=sift.slices.stash_migration; }
# Notify-only updates. The lines that matter here are the ones that decide NOT to do something: the
# comparison that declines to claim an update it cannot read, the failure path that stays silent
# rather than surfacing an error, and the interval that stops a check becoming a retry storm. An
# untested branch in any of those is a screen telling somebody to run a command on wrong evidence.
_cov_update_notify() { _cov src/sift/slices/update_notify/tests --cov=sift.slices.update_notify; }
# How hard the machine is worked, as settings. Every value here is 0 = automatic, and the resolvers
# are what spend that sentinel: an untested branch is a stored zero read as "stop working", or a
# bad row read as a wrong number of workers in the loop that runs every job. Pure functions with no
# excuse for a gap.
_cov_performance() { _cov src/sift/slices/performance/tests --cov=sift.slices.performance; }
# Faces measures people's faces and keeps what it measured. An untested branch here is not a
# coverage statistic: the guarantees this slice makes are that nothing runs while it is switched
# off, that a poor face is never described, and that a crop from a file somebody may not see is not
# served, and every one of those is a line that does nothing visible when it works.
_cov_faces() { _cov src/sift/slices/faces/tests --cov=sift.slices.faces; }
# Tidying up is the only surface that removes things nobody asked to be removed at the time: the
# leftovers of operations already carried out. Every line is either a count somebody reads before
# pressing an irreversible button, or the deletion behind it. A miscount here is worse than a bug:
# it is the wrong number in front of a permanent decision.
_cov_tidy() { _cov src/sift/slices/tidy/tests --cov=sift.slices.tidy; }
# The look of the app, which is three declared preferences and nothing else. The stylesheet does
# all the work. Held at the same bar anyway, because what IS here is the scope, the fixed set of
# choices and the defaults, and every one of those fails silently when it is wrong: the wrong scope
# turns one person's taste into the whole install's, and a value outside the set leaves the page on
# the default with nothing anywhere saying why.
_cov_theming() { _cov src/sift/slices/theming/tests --cov=sift.slices.theming; }
# Every setting in the application is written through this one route, for every user. What is
# here is the scope rule (an app-wide setting a guest may not touch, and a per-user one they
# may), and the read that has to fall back rather than serve a value the current rules refuse.
_cov_settings_hub() { _cov src/sift/slices/settings_hub/tests --cov=sift.slices.settings_hub; }
# Everything Sift does to a file after it arrives: reading what it is, drawing its thumbnail and
# preview, building the scrub strip, and the three passes that convert a library which predates a
# feature. An untested branch here is a job that quietly does nothing on somebody's library:
# there is no screen that would show it, only the absence of a picture nobody knows to expect.
_cov_media_jobs() { _cov src/sift/slices/media_jobs/tests --cov=sift.slices.media_jobs; }
# The connection a browser is told things on. Every branch here is either a refusal or a message: an
# untested refusal is somebody let in who should not be, and an untested message is a screen that
# does not update, which looks exactly like a feature that was never built.
_cov_live() { _cov src/sift/slices/live/tests --cov=sift.slices.live; }
# What each of those tidyings actually counts and removes, kernel side. Held to the same bar and
# for the same reason: a sweep that takes a little more than it counted takes it for good.
_cov_kernel_tidy() { _cov src/sift/kernel/tests/test_tidy.py --cov=sift.kernel.tidy; }
# The stores no foreign key reaches, and the registry the end of an asset walks. Small, and small
# is exactly where a lost branch goes unnoticed longest.
_cov_forgetting() { _cov src/sift/kernel/tests/test_forgetting.py --cov=sift.kernel.forgetting; }
_cov_chromaprint() { _cov src/sift/kernel/tests/test_chromaprint.py --cov=sift.kernel.chromaprint; }
_cov_landing() { _cov src/sift/kernel/tests/test_landing.py --cov=sift.kernel.landing; }
# Obtaining and loading the models. Two features run one of these and neither may import the other,
# so a gap here is a gap in both at once. The lines that matter are the refusals: a file whose
# digest does not match is refused rather than loaded, because a truncated model loads perfectly
# well and then returns numbers that are quietly wrong, and a device that was asked for and is
# missing stops rather than falling back, because silent CPU is a nine-hour job with no explanation
# anywhere on the machine.
# Every test file, because the gate measures the PACKAGE: a module whose own tests are not named
# here is reported as untested code.
_cov_ml() {
  # The device-wide model store is exercised by its own test file.
  _cov src/sift/kernel/tests/test_ml.py src/sift/kernel/tests/test_child.py src/sift/kernel/tests/test_accel.py src/sift/kernel/tests/test_model_store.py --cov=sift.kernel.ml
}
# Searching by meaning. The lines worth a gate are the ones that do nothing visible when they work:
# the whole feature going dark rather than the application when an add-on cannot be loaded, and a
# file described a second time replacing what was held about it rather than sitting beside it,
# which does not error, it quietly returns two answers to one question, one of them from a model
# whose numbers mean something else.
_cov_semantic() { _cov src/sift/slices/semantic/tests --cov=sift.slices.semantic; }
# The first feature that WRITES a file into somebody's library rather than reading one. Every line
# here is either a refusal, an arithmetic answer somebody acts on before spending four minutes of
# their machine, or the path that produces the file itself, and the two that matter most do
# nothing visible when they work: the floor that reports an unreachable target as unreachable, and
# the concealment a copy inherits, whose absence is a hidden file appearing on the ordinary grid.
_cov_media_edit() { _cov src/sift/slices/media_edit/tests --cov=sift.slices.media_edit; }
# What a safe filename is, which two features accept from a text box: renaming a file, and naming
# the copy the editor writes. Every line of it is a refusal, and it is the whole of what stands
# between that text box and the rest of the disk.
_cov_filenames() { _cov src/sift/kernel/tests/test_filenames.py --cov=sift.kernel.filenames; }

# The shell every queue of waiting work plugs into, and the record of what was decided. The lines
# that matter are the ones nobody sees working: a queue whose feature is off is left off the board
# rather than drawn empty, and a decision is claimed for reversal before it is reversed, so two
# presses on a slow request cannot both put the same rows back. Undo is the whole reason this slice
# exists, and every branch of it either restores something or deliberately declines to.
_cov_workbench() { _cov src/sift/slices/workbench/tests --cov=sift.slices.workbench; }
# The database itself: the one module every slice in the application reads through, and the one
# whose faults do not look like faults. An unmarked whole-library read queues every screen behind a
# background pass; a read pool sized from the wrong number leaves the browser with no connection at
# all; an extension that failed to load has to cost the feature built on it and not the boot. None
# of those raises anything, and any of them can leave the application answering nothing.
# `test_library_preflight.py` is named here, not only in the main gate: `inspect_library` and
# `copy_library_aside` are this module's, and with only `test_db.py` run the gate would read both
# of them as dead code.
_cov_db() {
  _cov src/sift/kernel/tests/test_db.py src/sift/kernel/tests/test_db_lead_and_inline_reads.py \
    src/sift/kernel/tests/test_library_preflight.py \
    src/sift/kernel/tests/test_baseline_refusal.py src/sift/kernel/tests/test_db_inline_schema.py \
    --cov=sift.kernel.db --cov=sift.kernel.db_base --cov=sift.kernel.db_library \
    --cov=sift.kernel.db_readers --cov=sift.kernel.db_schema
}
# What Sift writes down about itself, which is the only thing anybody has to go on when it is
# misbehaving on a machine nobody can log in to. Two halves are load-bearing and neither shows when
# it is wrong: the redaction, where a missed branch puts somebody's filesystem into a file that gets
# pasted into a bug report, and the timing records, where a wait counted as work names the wrong
# component, and every diagnosis follows the wrong name.
_cov_log() { _cov src/sift/kernel/tests/test_log.py --cov=sift.kernel.log; }
# What Sift says its own version is. Twelve lines, and the reason it is gated is that both of its
# branches are load-bearing: one is the number on the About screen and in every backup's name, and
# the other is the empty string that makes the update check decline rather than invent an upgrade.
_cov_version() { _cov src/sift/kernel/tests/test_version.py --cov=sift.kernel.version; }
# Whether somebody is at the keyboard, read from the last input time on Windows: the lever that
# halves the pool while the computer is in use.
_cov_attention() { _cov src/sift/kernel/tests/test_attention.py --cov=sift.kernel.attention; }
# The machine's clock: every day and time of day Sift shows or groups by, in Python and in SQL.
_cov_when() { _cov src/sift/kernel/tests/test_when.py --cov=sift.kernel.when; }
# The location door: a copy of a file with its GPS taken out, for everything Sift writes or sends.
_cov_places() { _cov src/sift/kernel/tests/test_places.py --cov=sift.kernel.places; }
# The HEIF door: a HEIC or AVIF still decoded whole, and the JPEG rendition a browser can draw.
_cov_heif() { _cov src/sift/kernel/tests/test_heif.py --cov=sift.kernel.heif; }
# A note written beside its target and renamed over it. The desktop shell reads the library switch
# note the instant the backend has gone, and a torn note reads as none: the switch never happens.
_cov_whole_file() { _cov src/sift/kernel/tests/test_whole_file.py --cov=sift.kernel.whole_file; }
# The window a cover is shown through: fractions of the picture it was chosen on, bound to that
# picture so a cover moved by any writer that knows nothing of frames shows whole again.
_cov_cover_frame() { _cov src/sift/kernel/tests/test_cover_frame.py --cov=sift.kernel.cover_frame; }
# The splice every SQL rule written once goes through. Small, and every branch is a refusal that
# stops the application starting: a marker left unfilled, a fragment never named, a marker inside a
# comment. Each one let through is a statement that runs with a rule missing from it.
_cov_sql_splice() { _cov src/sift/kernel/tests/test_sql_splice.py --cov=sift.kernel.sql_splice; }
# The four watches, and the two readings that are not about waiting for anything. Every other gate
# can be green while the application is freezing outright, and these are what can see it, so an
# untested branch here is not a coverage statistic, it is a screen that reads clean while somebody
# is telling you the application is unusable.
_cov_diagnostics() { _cov src/sift/kernel/tests/test_diagnostics.py --cov=sift.kernel.diagnostics; }
# The library's shape, read for nobody. It is where the arithmetic over the whole library happens,
# so a wrong answer here is a per-folder number that is quietly somebody else's, and nothing on
# any screen would show it, because a pass runs for nobody and writes what it concluded.
_cov_tree() { _cov src/sift/kernel/tests/test_tree.py --cov=sift.kernel.content.tree; }
# The module every other one imports through, which is the only reason it holds code at all. What
# is in it is two process-wide settings that have to be in place before anything else loads: the
# numeric pools pinned, without which a process cannot reliably start another program, and the
# interpreter's switch interval, without which a query of a few milliseconds can take a minute
# beside two busy threads. A setting made too late does nothing while looking exactly like it worked.
_cov_package() { _cov tests/gates/test_numeric_threads_are_pinned.py --cov --cov-config=scripts/coverage_package.ini; }
# The shape a queue registers with, kernel side. Tiny, and held at the same bar because what is in
# it is the arithmetic behind the rail's badge: a count that includes what has already been dealt
# with never goes down, and a badge that never clears is one people stop reading.
_cov_kernel_workbench() { _cov src/sift/kernel/tests/test_workbench.py --cov=sift.kernel.workbench; }
# The one door every act in the library is written through. Small, and it is the refusals that
# matter: a verb the record has no word for, a list of subjects long enough to be a pass, a
# count beside seven files. Every one of those branches is the difference between a record that
# can be read back and a table that grows without limit in a vocabulary nobody agreed.
# `vocabulary` is measured HERE rather than under a gate of its own, and that is the durable
# place for it: it is the two word lists the door validates against, lifted out of `access/catalog`
# and `workbench` so that the writers underneath them could call the door at all. It has no branches
# to lose (constants, a Literal and a frozen dataclass), so what a gate can say about it is that
# it still imports and that the door's own tests still reach every word. The door's tests are the
# ones that would notice.
_cov_ledger() {
  _cov src/sift/kernel/tests/test_ledger_door.py --cov=sift.kernel.ledger --cov=sift.kernel.vocabulary
}
# Theater holds no media and resolves nothing, so the bar is not about what it could leak by
# accident: it is that every refusal here is somebody's wall being kept from somebody else,
# and an untested branch in a scoped statement looks exactly like a working one.
_cov_theater() { _cov src/sift/slices/theater/tests --cov=sift.slices.theater; }
# The remote: a phone's commands to a user's own screens. The bar is the access model's: every
# branch here is either another user's screen answering exactly like a guessed one, a Hidden file
# named to a phone that has not opened Hidden, or a number from a phone reaching a player unbounded.
_cov_remote() { _cov src/sift/slices/remote/tests --cov=sift.slices.remote; }
# Insights: a User's figures, the recaps frozen from them and Your path. The bar is the vault's:
# every figure is stored with the part that came from hidden things, and what is hidden is decided
# when the page is drawn, so an untested branch in a reader is a hidden person's hours on a
# locked screen, or a hint that the vault holds something in the mode that exists not to say so.
# The adder-up is a loop of the process that no screen shows; a lost branch in it is a day that
# is never counted, which nothing anywhere would report.
_cov_insights() { _cov src/sift/slices/insights/tests --cov=sift.slices.insights; }
# When a sitting counts as a view. It moved out of the player so Insights counts by the same rule
# (a slice may not import another), and its tests stayed with the player, which reads it on every
# report, the same arrangement as the rate limiter measured by the download slice's tests.
_cov_view_rule() { _cov src/sift/slices/player/tests/test_policy.py --cov=sift.kernel.content.view_rule; }

# --- the small kernel modules -------------------------------------------------------------------
#
# Each has its own tests and its own gate, because a module with tests and no gate can lose a
# branch and stay green. Small modules are where that goes unnoticed longest: nobody reads the
# number under a passing suite for a file of forty lines.

# How much of the machine each kind of work may take. Every number the pool converges on comes
# through here, and the failure mode is a share that reads as generous and is a stall.
_cov_budget() { _cov src/sift/kernel/tests/test_budget.py --cov=sift.kernel.budget; }
# Every setting the process is started with, and the sentence a self-hoster gets when one is wrong.
# The refusals are the point: a mistyped variable that boots cleanly and is ignored costs an
# afternoon, which is why one is an error rather than a default.
_cov_settings() { _cov src/sift/kernel/tests/test_config.py --cov=sift.kernel.config; }
# How much of somebody's disk the log may take, and how far back they can look. Both things this
# gets wrong are silent: a total handed straight to a rotating handler takes `backups + 1` times
# what the screen promised, and a value that cannot be read must fall back to the ORDINARY log
# rather than the detailed one.
_cov_log_settings() { _cov src/sift/kernel/tests/test_log_settings.py --cov=sift.kernel.log_settings; }
# The header names and the cookie the whole application agrees on. Nine lines, and a disagreement
# about any of them is a session that does not survive a request.
_cov_http() { _cov src/sift/kernel/tests/test_http.py --cov=sift.kernel.http; }
# The base every model that crosses the wire is built on. One setting, one line, and
# losing it costs nothing that fails. It only makes the server publish a description of
# itself that is wrong about four hundred fields.
_cov_wire() { _cov src/sift/kernel/tests/test_wire.py --cov=sift.kernel.wire; }
# Every id in the library comes from here, and two properties are load-bearing: they sort in
# creation order, and the clock going backwards (which some machines do often) must not
# reorder them.
_cov_ids() { _cov src/sift/kernel/tests/test_ids.py --cov=sift.kernel.ids; }
# Asking this process to stop so that whatever started it starts it again. Every branch here is
# about a restart that did not happen: nothing supervising the process, an exit code that reads as
# a crash, a stop arranged twice. None of them is visible from a screen that says "restarting".
_cov_lifecycle() { _cov src/sift/kernel/tests/test_lifecycle.py --cov=sift.kernel.lifecycle; }
# Whether a path is inside the root it claims to be in. There is no second control: this is what
# stands between a request and a file somewhere else on the disk.
_cov_paths() { _cov src/sift/kernel/tests/test_paths.py --cov=sift.kernel.paths; }
# Getting a large file over HTTP: resuming it, reporting it, stopping it. Two features download
# hundreds of megabytes through this, and every branch in it is about a transfer that went wrong
# halfway: a dropped connection, a server that ignores a range, somebody pressing stop. None of
# them is reachable by running it once on a good connection.
_cov_fetch() { _cov src/sift/kernel/tests/test_fetch.py --cov=sift.kernel.fetch; }
# Which moments of a video are looked at. Two ladders that must not be merged: a fingerprint
# wants the same count whatever the length, and a face pass wants one that never falls.
_cov_sampling() { _cov src/sift/kernel/tests/test_sampling.py --cov=sift.kernel.sampling; }
# The interfaces features reach each other through. It is nothing but shapes, so the bar is cheap
# to hold and the day it is not is the day a seam grew a body.
_cov_seams() { _cov src/sift/kernel/tests/test_seams.py --cov=sift.kernel.seams; }
# What a setting IS. Every screen draws from this and every write is checked against it, so a
# branch here decides whether a value nobody meant can be stored.
_cov_settings_registry() {
  _cov src/sift/kernel/tests/test_settings_registry.py --cov=sift.kernel.settings_registry
}
# Folding and comparing the text people search by. Held here because the answer has to be the same
# for the writer and the reader, and a difference between them is a file nobody can find.
_cov_text() { _cov src/sift/kernel/tests/test_text.py --cov=sift.kernel.text; }
# What may run off the event loop and how many at once. A fault here freezes the whole
# application.
_cov_threads() { _cov src/sift/kernel/tests/test_threads.py --cov=sift.kernel.threads; }
# How many files may be read from one storage at once. A share that has stopped coping reads as a
# healthy loop and healthy pools with every job slow; an untested branch here is a reader the cap
# cannot see.
_cov_lanes() { _cov src/sift/kernel/tests/test_lanes.py --cov=sift.kernel.lanes; }
# An answer kept until the change bus says it moved. Exercised through the search-by-meaning memo,
# which is the one reader it has.
_cov_memo() { _cov src/sift/kernel/tests/test_memo.py src/sift/slices/semantic/tests/test_search.py --cov=sift.kernel.memo; }
# The one format nothing else can decode, and the copy made so that everything else can.
_cov_webp() { _cov src/sift/kernel/tests/test_webp.py --cov=sift.kernel.webp; }
# One description of every field, which three screens and an edit form are generated from. An
# untested refusal here is a field two surfaces can disagree about.
_cov_kernel_records() { _cov src/sift/kernel/tests/test_records.py --cov=sift.kernel.records --cov=sift.kernel.records_registry --cov=sift.kernel.records_found; }
# where: the one place a file's location is put into words for an admin, a guest and the vault.
_cov_kernel_where() { _cov src/sift/kernel/tests/test_where.py --cov=sift.kernel.where; }
# Whether a folder is served from another machine, which decides whether it is watched or polled.
# Wrong either way is quiet: a share treated as local simply never notices a new file.
_cov_mounts() { _cov src/sift/kernel/tests/test_mounts.py --cov=sift.kernel.content.mounts; }
# Reading a number out of text from a request or from somebody else's server. The whole module is
# one trap (`isdigit()` admits characters `int()` refuses), and a caller can reach it.
_cov_numbers() { _cov src/sift/kernel/tests/test_numbers.py --cov=sift.kernel.numbers; }
# What a face pass tells a reader of folder names. Two absences that must stay different: a folder
# nobody has looked at, and one that was looked at and holds nobody.
_cov_attribution() { _cov src/sift/kernel/tests/test_attribution.py --cov=sift.kernel.attribution; }
# The ffmpeg seam: which encoder this machine gets, and finding a copy of a file that opens. Its
# own file plus the slices that drive it. The failures here are a job that cannot run.
_cov_media() { _cov src/sift/kernel/tests/test_media.py src/sift/kernel/tests/test_moments.py src/sift/kernel/tests/test_moving_stream.py --cov=sift.kernel.media; }
# The shapes a write to the library is described by, and the permission behind them. Reached
# through the editor and the deleter, which are the two features allowed to move a FILE, and
# through the library slice, which is the one allowed to arrange the FOLDERS they sit in.
_cov_library_write() {
  _cov src/sift/slices/media_edit/tests src/sift/slices/delete/tests \
    src/sift/slices/library_roots/tests --cov=sift.kernel.library_write
}
# The client the server serves, and the two cache rules that decide whether a new version arrives.
# Without them an upgraded Sift can go on showing the previous version's screens.
_cov_client() {
  _cov tests/integration/test_client.py tests/integration/test_security_headers.py --cov=sift.client
}
# The composition root. Most of it is wiring and is proved by every test that calls a route; what
# this holds to the bar is the handful of rules that live here because they are the only code
# allowed to know two features exist, and the sentence a self-hoster gets before anything starts.
_cov_main() {
  # The clean-shutdown suite is named here because it is the only thing that exercises the parent
  # watch, and a gate that measures a module while running only some of its tests reports the rest
  # as untested code. It lives under tests/ rather than beside the module because it starts a real
  # interpreter and closes its stdin, which is the whole of what it proves.
  # `test_library_preflight.py` for the same reason: the library-folder commands live in this
  # module and their suite is next door, so the gate read them as unreached code.
  _cov src/sift/kernel/tests/test_main.py src/sift/kernel/tests/test_main_wiring.py \
    src/sift/kernel/tests/test_main_wiring_beat.py src/sift/kernel/tests/test_main_wiring_passes.py \
    src/sift/kernel/tests/test_main_wiring_tasks.py src/sift/kernel/tests/test_main_wiring_dry_runs.py \
    src/sift/kernel/tests/test_main_wiring_stash_doors.py \
    src/sift/kernel/tests/test_library_preflight.py \
    src/sift/kernel/tests/test_job_schedules.py \
    tests/integration/test_clean_shutdown.py --cov=sift.main --cov=sift.wiring
}

COV_LABELS=(
  "coverage: destination gate"
  "coverage: naming gate"
  "coverage: stash migration gate"
  "coverage: ingress gate"
  "coverage: job queue gate"
  "coverage: hardware probe gate"
  "coverage: scan benchmarks gate"
  "coverage: logs slice gate"
  "coverage: content identity gate"
  "coverage: library store gate"
  "coverage: perceptual hash gate"
  "coverage: access rules gate"
  "coverage: wiring and secrets gate"
  "coverage: subprocess gate"
  "coverage: public net gate"
  "coverage: port holder gate"
  "coverage: download slice gate"
  "coverage: tunnels gate"
  "coverage: capture gate"
  "coverage: conditional-request gate"
  "coverage: entity cover gate"
  "coverage: site icons gate"
  "coverage: svg raster gate"
  "coverage: name ordering gate"
  "coverage: cache stamp gate"
  "coverage: change bus gate"
  "coverage: mp4 index gate"
  "coverage: browse slice gate"
  "coverage: player slice gate"
  "coverage: library slice gate"
  "coverage: delete gate"
  "coverage: auth slice gate"
  "coverage: sharing slice gate"
  "coverage: tags/ratings gate"
  "coverage: people slice gate"
  "coverage: collections slice gate"
  "coverage: photo sets slice gate"
  "coverage: shoots slice gate"
  "coverage: music slice gate"
  "coverage: songs gate"
  "coverage: swap slice gate"
  "coverage: desktop slice gate"
  "coverage: machine acts gate"
  "coverage: watermarks slice gate"
  "coverage: loops slice gate"
  "coverage: records slice gate"
  "coverage: related slice gate"
  "coverage: archives gate"
  "coverage: dedup slice gate"
  "coverage: importing slice gate"
  "coverage: suggestions slice gate"
  "coverage: organize slice gate"
  "coverage: vault slice gate"
  "coverage: search slice gate"
  "coverage: backup slice gate"
  "coverage: update notify gate"
  "coverage: performance slice gate"
  "coverage: faces slice gate"
  "coverage: theming slice gate"
  "coverage: settings hub gate"
  "coverage: media jobs gate"
  "coverage: live slice gate"
  "coverage: tidy slice gate"
  "coverage: tidy kernel gate"
  "coverage: forgetting gate"
  "coverage: chromaprint gate"
  "coverage: landing gate"
  "coverage: model runtime gate"
  "coverage: semantic slice gate"
  "coverage: compress slice gate"
  "coverage: filename rules gate"
  "coverage: workbench slice gate"
  "coverage: workbench kernel gate"
  "coverage: ledger door gate"
  "coverage: theater slice gate"
  "coverage: remote slice gate"
  "coverage: insights slice gate"
  "coverage: view rule gate"
  "coverage: database kernel gate"
  "coverage: logging kernel gate"
  "coverage: diagnostics kernel gate"
  "coverage: folder tree gate"
  "coverage: package settings gate"
  "coverage: machine budget gate"
  "coverage: settings/config gate"
  "coverage: log settings gate"
  "coverage: http contract gate"
  "coverage: wire base gate"
  "coverage: enrichment rules gate"
  "coverage: composition gate"
  "coverage: identifiers gate"
  "coverage: path confinement gate"
  "coverage: resumable fetch gate"
  "coverage: frame sampling gate"
  "coverage: seams gate"
  "coverage: settings registry gate"
  "coverage: text folding gate"
  "coverage: thread pools gate"
  "coverage: storage lanes gate"
  "coverage: answer memo gate"
  "coverage: animated webp gate"
  "coverage: field registry gate"
  "coverage: kernel where gate"
  "coverage: mount kind gate"
  "coverage: number parsing gate"
  "coverage: folder attribution gate"
  "coverage: ffmpeg seam gate"
  "coverage: library write shapes gate"
  "coverage: browser client gate"
  "coverage: partial write gate"
  "coverage: url normaliser gate"
  "coverage: host pacing gate"
  "coverage: secret store gate"
  "coverage: stash-boxes gate"
  "coverage: application assembly gate"
  "coverage: restart lifecycle gate"
  "coverage: version gate"
  "coverage: attention gate"
  "coverage: machine clock gate"
  "coverage: location door gate"
  "coverage: heif door gate"
  "coverage: whole file gate"
  "coverage: cover frame gate"
  "coverage: sql splice gate"
  "coverage: tasks slice gate"
  "coverage: country names gate"
  "coverage: reach gate"
  "coverage: request client gate"
  "coverage: photo set floor gate"
  "coverage: pass presses gate"
  "coverage: use history gate"
)
COV_FNS=(
  _cov_destination
  _cov_naming
  _cov_stash_migration
  _cov_ingress
  _cov_jobs
  _cov_hardware
  _cov_benchmarks
  _cov_logs
  _cov_content
  _cov_library
  _cov_perceptual
  _cov_access
  _cov_wiring
  _cov_subprocess
  _cov_public_net
  _cov_ports
  _cov_download
  _cov_tunnels
  _cov_capture
  _cov_serving
  _cov_covers
  _cov_site_icons
  _cov_svg_raster
  _cov_sorting
  _cov_cache_stamp
  _cov_changes
  _cov_mp4
  _cov_browse
  _cov_player
  _cov_library_roots
  _cov_delete
  _cov_auth
  _cov_sharing
  _cov_tags_ratings
  _cov_people
  _cov_collections
  _cov_photo_sets
  _cov_shoots
  _cov_music
  _cov_songs
  _cov_swap
  _cov_desktop
  _cov_machine_acts
  _cov_watermarks
  _cov_loops
  _cov_records
  _cov_related
  _cov_archives
  _cov_dedup
  _cov_importing
  _cov_suggestions
  _cov_organize
  _cov_vault
  _cov_search
  _cov_backup
  _cov_update_notify
  _cov_performance
  _cov_faces
  _cov_theming
  _cov_settings_hub
  _cov_media_jobs
  _cov_live
  _cov_tidy
  _cov_kernel_tidy
  _cov_forgetting
  _cov_chromaprint
  _cov_landing
  _cov_ml
  _cov_semantic
  _cov_media_edit
  _cov_filenames
  _cov_workbench
  _cov_kernel_workbench
  _cov_ledger
  _cov_theater
  _cov_remote
  _cov_insights
  _cov_view_rule
  _cov_db
  _cov_log
  _cov_diagnostics
  _cov_tree
  _cov_package
  _cov_budget
  _cov_settings
  _cov_log_settings
  _cov_http
  _cov_wire
  _cov_enrichment
  _cov_composition
  _cov_ids
  _cov_paths
  _cov_fetch
  _cov_sampling
  _cov_seams
  _cov_settings_registry
  _cov_text
  _cov_threads
  _cov_lanes
  _cov_memo
  _cov_webp
  _cov_kernel_records
  _cov_kernel_where
  _cov_mounts
  _cov_numbers
  _cov_attribution
  _cov_media
  _cov_library_write
  _cov_client
  _cov_partial_write
  _cov_urls
  _cov_ratelimit
  _cov_secret_store
  _cov_stash_boxes
  _cov_main
  _cov_lifecycle
  _cov_version
  _cov_attention
  _cov_when
  _cov_places
  _cov_heif
  _cov_whole_file
  _cov_cover_frame
  _cov_sql_splice
  _cov_tasks
  _cov_countries
  _cov_reach
  _cov_kernel_client
  _cov_kernel_photo_sets
  _cov_presses
  _cov_use_history
)

# A gate declared without a label (or the reverse) would silently vanish from the loop and leave the
# run green, which is the one failure mode a parallel loop is good at hiding. Refuse to start.
if [ "${#COV_LABELS[@]}" -ne "${#COV_FNS[@]}" ]; then
  echo "coverage_gates.sh: ${#COV_LABELS[@]} labels but ${#COV_FNS[@]} gates, so one of them is missing" >&2
  exit 2
fi

# ---------------------------------------------------------------------------------------------
# One shard of the gates, for a workflow that runs them as a matrix of jobs rather than as one.
#
# By how long each gate takes, so the shards end together: a run is as long as its longest shard,
# and the gates run from seconds to twenty minutes. The times are in coverage_gate_seconds.tsv; a
# gate with no line there counts as the middle one, so a gate added to the list lands in a shard
# with no edit anywhere else. The longest gate goes first, each to whichever shard is lightest so
# far (the lower number on a tie), which is the same answer on every machine and puts every gate
# in exactly one shard whatever N is. K counts from one, as a matrix is written. The arrays are
# narrowed in place and keep the list's order, so everything below (the fan-out, the replay, the
# count of what failed) reads the shard as if it were the whole list.
# ---------------------------------------------------------------------------------------------
COV_SECONDS_FILE="${COV_SECONDS_FILE:-scripts/coverage_gate_seconds.tsv}"

cov_take_shard() {  # cov_take_shard K N
  local k="$1" n="$2" i
  if ! [[ "$k" =~ ^[0-9]+$ && "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$k" -lt 1 ] || [ "$k" -gt "$n" ]; then
    echo "coverage_gates.sh: --shard wants K/N with 1 <= K <= N, not $k/$n" >&2
    return 2
  fi

  local -A recorded=()
  local seconds label known=()
  if [ -r "$COV_SECONDS_FILE" ]; then
    while IFS=$'\t' read -r seconds label; do
      label="${label%$'\r'}"
      [[ "$seconds" =~ ^[0-9]+$ ]] || continue
      recorded["$label"]="$seconds"
      known+=("$seconds")
    done < "$COV_SECONDS_FILE"
  fi
  # The middle recorded time, for a gate nobody has timed yet.
  local middle=60
  if [ "${#known[@]}" -gt 0 ]; then
    local sorted=()
    mapfile -t sorted < <(printf '%s\n' "${known[@]}" | sort -n)
    middle="${sorted[$(( ${#sorted[@]} / 2 ))]}"
  fi

  # Longest first, the list's order between equals.
  local order=()
  mapfile -t order < <(
    for (( i = 0; i < ${#COV_LABELS[@]}; i++ )); do
      printf '%s %s\n' "${recorded[${COV_LABELS[$i]}]:-$middle}" "$i"
    done | sort -k1,1nr -k2,2n
  )

  local load=() mine=() line at lightest s
  for (( s = 0; s < n; s++ )); do load[s]=0; done
  for line in "${order[@]}"; do
    seconds="${line%% *}"
    at="${line##* }"
    lightest=0
    for (( s = 1; s < n; s++ )); do
      if [ "${load[$s]}" -lt "${load[$lightest]}" ]; then lightest=$s; fi
    done
    load[lightest]=$(( load[lightest] + seconds ))
    if [ "$lightest" -eq $(( k - 1 )) ]; then mine[at]=1; fi
  done

  local labels=() fns=()
  for (( i = 0; i < ${#COV_LABELS[@]}; i++ )); do
    if [ -n "${mine[$i]:-}" ]; then
      labels+=("${COV_LABELS[$i]}")
      fns+=("${COV_FNS[$i]}")
    fi
  done
  COV_LABELS=("${labels[@]}")
  COV_FNS=("${fns[@]}")
}

# ---------------------------------------------------------------------------------------------
# The fan-out. Results land in COV_RCS and COV_SECS, indexed the same as COV_LABELS; each gate's
# output is at $COV_DIR/<index>.out. Nothing is printed here: the caller formats, because the
# local runner and the workflow report into different places.
# ---------------------------------------------------------------------------------------------
COV_DIR=""
COV_RCS=()
COV_SECS=()

_cov_gate_worker() {
  local i="$1"
  local start=$SECONDS
  local rc=0
  # Coverage's data file is per-gate, or concurrent gates overwrite each other's measurements and
  # report numbers that mean nothing. The environment variable beats the project configuration.
  export COVERAGE_FILE="$COV_DIR/data.$i"
  "${COV_FNS[$i]}" > "$COV_DIR/$i.out" 2>&1 || rc=$?
  printf '%d %d\n' "$rc" "$((SECONDS - start))" > "$COV_DIR/$i.meta"
}

run_cov_gates() {
  local jobs n i rc secs
  # The budget is the whole of what this checkout may use, and each gate now spends COV_INNER of it
  # inside itself, so the number of gates in flight is the budget divided by that, not the budget.
  # Without this division the box is oversubscribed by exactly the inner width and every gate slows
  # down together, which reads as a machine problem rather than a scheduling one.
  jobs="$(( $(sift_ci_jobs) / COV_INNER ))"
  [ "$jobs" -lt 1 ] && jobs=1
  n="${#COV_FNS[@]}"
  COV_DIR="$(mktemp -d)"
  COV_RCS=()
  COV_SECS=()

  for (( i = 0; i < n; i++ )); do
    # Hold the line at the budget: start a gate only once a running one has finished.
    while [ "$(jobs -rp | wc -l)" -ge "$jobs" ]; do wait -n; done
    _cov_gate_worker "$i" &
  done
  wait

  for (( i = 0; i < n; i++ )); do
    rc=1
    secs=0
    # A worker that died without writing its result counts as a failure, not as a gate that never
    # ran. The whole point of the count check above is that a missing gate must not read as green.
    if [ -f "$COV_DIR/$i.meta" ]; then
      read -r rc secs < "$COV_DIR/$i.meta"
    else
      printf 'the gate did not report a result\n' > "$COV_DIR/$i.out"
    fi
    COV_RCS+=("$rc")
    COV_SECS+=("$secs")
  done
}

cov_gates_cleanup() {
  [ -n "$COV_DIR" ] && [ -d "$COV_DIR" ] && rm -rf "$COV_DIR"
  COV_DIR=""
}

# ---------------------------------------------------------------------------------------------
# Standalone. This is what the workflow runs: same gates, same budget, same fixed-order replay,
# exit code = the number of gates that failed.
# ---------------------------------------------------------------------------------------------
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail
  # See ci-local.sh: `cd ""` succeeds, so a missing repository must be caught here.
  cd "$(git rev-parse --show-toplevel)" || exit 1

  sift_ci_take_env

  if [ "${1:-}" = "--jobs" ]; then
    sift_ci_jobs
    exit 0
  fi

  if [ "${1:-}" = "--shard" ]; then
    shard="${2:-}"
    cov_take_shard "${shard%%/*}" "${shard#*/}" || exit 2
    shift 2
  fi
  if [ "${1:-}" = "--list" ]; then
    printf '%s\n' "${COV_LABELS[@]}"
    exit 0
  fi

  _find_uv || exit 2

  if [ -t 1 ]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
  else
    BOLD=""; RED=""; GREEN=""; DIM=""; RESET=""
  fi

  printf '%b>> coverage gates: %d gates, %s at a time%b\n' \
    "$BOLD" "${#COV_FNS[@]}" "$(sift_ci_jobs)" "$RESET"
  run_cov_gates

  failed=()
  for (( idx = 0; idx < ${#COV_LABELS[@]}; idx++ )); do
    printf '\n%b>> %s%b\n' "$BOLD" "${COV_LABELS[$idx]}" "$RESET"
    cat "$COV_DIR/$idx.out"
    if [ "${COV_RCS[$idx]}" = "0" ]; then
      printf '%bPASS%b  %s  %b(%ds)%b\n' \
        "$GREEN" "$RESET" "${COV_LABELS[$idx]}" "$DIM" "${COV_SECS[$idx]}" "$RESET"
    else
      failed+=("${COV_LABELS[$idx]}")
      printf '%bFAIL%b  %s  %b(exit %d, %ds)%b\n' \
        "$RED" "$RESET" "${COV_LABELS[$idx]}" "$DIM" "${COV_RCS[$idx]}" "${COV_SECS[$idx]}" "$RESET"
    fi
  done
  cov_gates_cleanup

  if [ "${#failed[@]}" -gt 0 ]; then
    printf '\n%bfailed gates:%b\n' "$RED" "$RESET"
    for f in "${failed[@]}"; do printf '  %s%s%s\n' "$RED" "$f" "$RESET"; done
    exit "${#failed[@]}"
  fi
  printf '\n%ball %d coverage gates passed%b\n' "$GREEN" "${#COV_LABELS[@]}" "$RESET"
  exit 0
fi
