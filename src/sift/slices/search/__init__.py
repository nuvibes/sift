# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search: one query language, one engine, and an index that can always be rebuilt.

A library of fifty thousand clips is a pile until it can be asked questions. Two ways to ask, and
they are deliberately the same question underneath:

    people:jane tags:beach rating:4+          typed, with the dropdown offering matches
    the Filters modal                         clicked, for anyone who has not learned the tokens

Both become the same parsed query and are compiled by the same code. That is the load-bearing rule
of this slice: two parsers would disagree within a month about quoting, or ranges, or what happens
to a name nobody has, and neither of them would be *wrong*, because there would be no single
definition to be wrong against. The only way to settle a bug like that is to not have two.

Free text goes to a trigram index, so typing the middle of a filename finds it. The index is a
cache: the SQLite tables are the truth, and it can be dropped and rebuilt at any time.

Everything is permission-scoped, and that includes the two things it is easy to forget. The COUNT
is scoped, because the difference between "42 results" and the eleven shown is the size of the set
somebody was kept out of. And the DROPDOWN is scoped, because a suggester offering a name has
already answered "is there somebody by that name here", whatever the search then returns.

No search is ever written to the log. `search_history` is a feature that belongs to the user it
came from; a query log would be a record of what the person running this went looking for, and
nothing here keeps one.
"""

from __future__ import annotations

from sift.kernel.jobs import ScheduledTask, register_schedule
from sift.kernel.jobs.quiet_hours import WHEN_QUIET
from sift.kernel.settings_registry import register_setting
from sift.slices.search import jobs, schema
from sift.slices.search.filters import (
    ENTITY_FIELDS,
    Caret,
    Field,
    FilterCompiler,
    Query,
    Term,
    parse,
    token_prefix,
)
from sift.slices.search.jobs import (
    FTS_REINDEX,
    SEARCH_EVENTS_PRUNE,
    catch_up_if_behind,
    ensure_scheduled,
    register_handlers,
)
from sift.slices.search.reindex import Reindexer
from sift.slices.search.router import router
from sift.slices.search.service import SERVICE, SearchService, Suggestion

# How long the record of searches is kept.
#
# Filed under Scheduled tasks rather than anywhere search-shaped, which is where the backup's
# cadence and the quarantine sweep's rule already are: the question it answers is "what does Sift
# do on a clock", and that question has one door.
register_setting(
    key=jobs.KEEP_DAYS_KEY,
    scope="app",
    default=jobs.DEFAULT_KEEP_DAYS,
    minimum=0,
    maximum=3650,
    unit="days",
    section="Privacy and Security",
    label="Keep search history for",
    automatic_label="Forever",
    disclosure=(
        "Only you can see your search history, and Sift's log never records a search. Leave it "
        "empty to keep it until you clear it yourself."
    ),
    help=(
        "Sift saves each search you run, so it can show how you use your library. Set a number "
        "of days to delete older searches."
    ),
)

#: The sweep: upkeep nobody times, so it is drawn on no pane and left off Activity. The horizon is
#: WHAT it deletes (zero days is nothing, and no run is placed); it runs once a day, at the opening
#: of quiet hours.
register_schedule(
    ScheduledTask(
        id="search-records-prune",
        title="Delete old search history",
        explain="Deletes searches from your search history once they are older than the limit set in Privacy.",
        setting_keys=(jobs.KEEP_DAYS_KEY,),
        job_type=SEARCH_EVENTS_PRUNE,
        when_default=WHEN_QUIET,
        every=lambda values: (
            jobs.PRUNE_EVERY_SECONDS
            if jobs.keep_days_from(values.get(jobs.KEEP_DAYS_KEY)) > 0
            else None
        ),
        set_in="privacy",
        records_runs=True,
        shown=False,
    )
)

__all__ = [
    "ENTITY_FIELDS",
    "FTS_REINDEX",
    "SEARCH_EVENTS_PRUNE",
    "SERVICE",
    "Caret",
    "Field",
    "FilterCompiler",
    "Query",
    "Reindexer",
    "SearchService",
    "Suggestion",
    "Term",
    "catch_up_if_behind",
    "ensure_scheduled",
    "jobs",
    "parse",
    "register_handlers",
    "router",
    "schema",
    "token_prefix",
]
