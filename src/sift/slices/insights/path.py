# SPDX-License-Identifier: AGPL-3.0-or-later
"""Get to know Sift: learning paths, each an ordered set of goals drawn from the whole app.

The feel of a habit-forming app and none of its machinery: short, one thing at a time, progress you
can see, a small celebration when something is done, and no guilt. So there are no points, no
levels, no weekly chores and no streak: a path is something to learn, and a goal is done once.

## A path is declared here, and made of goals from every part of Sift

`PATHS` is the whole list: each path a name, one sentence and its goals in order, declared in code
rather than in a table nobody edits. A goal belongs to exactly one path, and the goals come from
everywhere somebody works: the library and its Scan, Browse and the player, Loops, Collections,
Theater, Downloads, the stash-boxes and Organize. Organize is one path of several, never the
whole of it.

## A goal is judged from the record, never from a flag

Two kinds, both read from what happened:

* A FIRST TIME (the first Scan, the first Loop, the first file opened): one indexed statement that
  stops at its first row, or the store that owns a permission-carrying table. Nothing stores
  "step 3 done", which is what makes a goal done BEFORE this screen existed show as done on its
  first visit.
* A MILESTONE (100 faces named, a year of Sift, every file fingerprinted, a pile emptied): the
  achievements, each frozen ONCE as a `recaps` row through the insights store,
  `achievement:<name>`, one card, dated the day it happened (the table's
  `UNIQUE (user_id, period)` makes a second write a no-op). A frozen milestone stays reached.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import said
from sift.kernel.content import ContentStore, LibraryStore, lacks_fingerprint
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.kernel.when import day_of
from sift.kernel.wire import pieces_of
from sift.kernel.workbench import Band, Summary, Workbench
from sift.slices.insights.metrics import rows_of
from sift.slices.insights.models import Figure
from sift.slices.insights.path_models import Goal, LearningPath, Path
from sift.slices.insights.recaps_models import KeptCard, RecapHead
from sift.slices.insights.store import (
    RecapRow,
    day_bounds,
    local_today,
    recaps_of,
    write_recap,
)

log = get_logger(__name__)


# --- the goals that are a first time ---------------------------------------------------------------
#
# One statement per goal, each answering the moment the record says it was FIRST done (or no row).
# Every one is bounded by an index and stops at its first row; the ones over small tables
# (`library_roots`, `collections`, `loops`) read what little there is.


async def _first_root(library: LibraryStore) -> int | None:
    """A library folder was added: the earliest root, read through the store that owns the roots
    (their table carries permissions, so no statement here names it). A handful of rows."""
    return min((root.created_at for root in await library.roots()), default=None)


#: A Scan ran to its end. A canceled one did not look at the library, so it does not count.
#: `ix_work_runs_family` (family, started_at).
_FIRST_SCAN = """
SELECT finished_at AS at FROM work_runs
 WHERE family = ? AND finished_at IS NOT NULL AND stopped = 0
 ORDER BY started_at
 LIMIT 1
"""

#: A decision by this User on one queue, the first one. `ix_workbench_actor` (actor_id, decided_at)
#: bounds it to this User's own decisions. Undone ones count: the step is having done it once.
_FIRST_DECISION_ON = """
SELECT decided_at AS at FROM workbench_decisions
 WHERE actor_id = ? AND queue = ?
 ORDER BY decided_at
 LIMIT 1
"""

#: A file this User viewed: the view the player's own threshold counted (`record_view` stamps
#: `last_viewed_at` only for a sitting that crossed it). The table keeps the LAST view of each file,
#: so this is the earliest of those, which is the oldest view the record still holds.
#: `ix_aus_recent` (user_id, last_viewed_at).
_FIRST_VIEW = """
SELECT last_viewed_at AS at FROM asset_user_state
 WHERE user_id = ? AND last_viewed_at IS NOT NULL
 ORDER BY last_viewed_at
 LIMIT 1
"""

#: A star or a rating by this User. From the opinions this User has written (`ix_opinions_user`),
#: or, for one given before opinions were recorded, a file this User has starred or rated now,
#: whose last change is the oldest moment the record holds for it.
_FIRST_OPINION = """
SELECT MIN(at) AS at FROM (
  SELECT at FROM (SELECT o.at FROM opinions o
                   WHERE o.user_id = ? AND o.kind IN ('rating', 'favorite')
                   ORDER BY o.at LIMIT 1)
  UNION ALL
  SELECT at FROM (SELECT s.updated_at AS at FROM asset_user_state s
                   WHERE s.user_id = ? AND (s.favorite = 1 OR s.rating IS NOT NULL)
                   LIMIT 1)
)
"""

#: A Collection this User created. `created_by_*` says who; a Collection from before those columns
#: existed has only its owner. The table is small; nothing indexes the creator.
_FIRST_COLLECTION = """
SELECT MIN(created_at) AS at FROM collections
 WHERE (created_by_kind = 'user' AND created_by_user_id = ?)
    OR (created_by_kind IS NULL AND owner_id = ?)
"""

#: A stash-box asked about a file on this User's press: the ledger's `enriched`, by this User.
_FIRST_ENRICHED = """
SELECT decided_at AS at FROM workbench_decisions
 WHERE actor_id = ? AND verb = 'enriched'
 ORDER BY decided_at
 LIMIT 1
"""

#: A filter this User kept: the first one, by the row's id (a ULID minted in order, which a
#: device's clock need not be: it can step backwards). `ix_saved_searches_user` (user_id,
#: created_at) filters to the User; a User keeps a handful, so the order over them is nothing.
_FIRST_KEPT_FILTER = """
SELECT created_at AS at FROM saved_searches WHERE user_id = ? ORDER BY id LIMIT 1
"""

#: A Loop this User made. `loops` has no index on its maker; it is a small table of things made by
#: hand, so reading it whole costs a few hundred rows at most.
_FIRST_LOOP = """
SELECT MIN(created_at) AS at FROM loops WHERE created_by = ?
"""

#: A Theater wall this User opened. `ix_theater_sessions_user` (user_id, started_at).
_FIRST_THEATER = """
SELECT started_at AS at FROM theater_sessions WHERE user_id = ? ORDER BY started_at LIMIT 1
"""

#: A download that brought a file into the library. Downloads belong to the library, not to one
#: User, so this is an admin's goal. `ix_downloads_asset` holds only the rows that name a file.
_FIRST_DOWNLOAD = """
SELECT finished_at AS at FROM downloads
 WHERE asset_id IS NOT NULL AND finished_at IS NOT NULL
 ORDER BY finished_at
 LIMIT 1
"""

#: The queue a naming decision on the Faces card is recorded under. The faces feature's
#: `IDENTIFIED_QUEUE`, written out because a slice may not import another: naming a group, agreeing
#: with a match and refusing one are all receipts under it (`slices/faces/receipts.py`).
FACES_DECISIONS = "identified"

#: The Folders card's queue: `slices/suggestions/service.py` `QUEUE`, for the same reason.
FOLDERS_DECISIONS = "folders"


@dataclass(frozen=True, slots=True)
class GoalRule:
    """One goal: its words, where it is done, and how the record says so."""

    id: str
    title: str
    help: str
    href: str
    #: How the record says it was done: a statement over the tables this slice may read, answering
    #: `at`, or, for a goal whose record is a permission-carrying table, the store that owns it.
    #: Neither, for a milestone: it is done when its achievement is frozen (`ACHIEVEMENTS`).
    statement: str | None
    #: Only an admin can do it (a guest cannot add a library, or name a face).
    admins: bool
    #: What the statement is bound to. `user` stands for this User's id.
    binds: tuple[str, ...] = ()
    asks: Callable[[LibraryStore], Awaitable[int | None]] | None = None

    @property
    def milestone(self) -> bool:
        """Done when its achievement is frozen, rather than by a statement of its own."""
        return self.statement is None and self.asks is None


def _milestone(name: str, title: str, help_: str, href: str, *, admins: bool) -> GoalRule:
    return GoalRule(name, title, help_, href, None, admins=admins)


#: Every goal, each once. Which path holds each, and in what order, is `PATHS`.
GOALS: tuple[GoalRule, ...] = (
    GoalRule(
        "add_library",
        "Add your library",
        "Choose the folders your library is in. Nothing in them is moved or changed. Adding the "
        "first one benchmarks this device, so Sift can make the best use of it.",
        "/settings/library",
        None,
        admins=True,
        asks=_first_root,
    ),
    GoalRule(
        "first_scan",
        "Scan your library",
        "A scan finds every file and fills in what it can about each one.",
        "/settings/tasks",
        _FIRST_SCAN,
        admins=True,
        binds=(Family.SCAN.value,),
    ),
    GoalRule(
        "name_face",
        "Name a face",
        "Faces are on Organize. Name one and Sift finds the rest.",
        "/organize/faces",
        _FIRST_DECISION_ON,
        admins=True,
        binds=("user", FACES_DECISIONS),
    ),
    GoalRule(
        "review_folder",
        "Review a folder",
        "Folders are on Organize. Say whose a folder is and Sift files what's in it.",
        "/organize/folders",
        _FIRST_DECISION_ON,
        admins=True,
        binds=("user", FOLDERS_DECISIONS),
    ),
    GoalRule(
        "view_something",
        "View something",
        "Open any file. What you view is counted for you alone.",
        "/browse",
        _FIRST_VIEW,
        admins=False,
        binds=("user",),
    ),
    GoalRule(
        "star_or_rate",
        "Star or rate a file",
        "Press the star on any file, or give it a rating.",
        "/browse",
        _FIRST_OPINION,
        admins=False,
        binds=("user", "user"),
    ),
    GoalRule(
        "create_collection",
        "Create a Collection",
        "A Collection holds the files you choose, in the order you choose.",
        "/collections",
        _FIRST_COLLECTION,
        admins=True,
        binds=("user", "user"),
    ),
    GoalRule(
        "ask_stash_box",
        "Ask a stash-box (optional)",
        "A stash-box can say who and what a file is. Turn one on in Settings.",
        "/settings/stash-boxes",
        _FIRST_ENRICHED,
        admins=True,
        binds=("user",),
    ),
    GoalRule(
        "keep_filter",
        "Keep a filter",
        "Keep a filter on Browse and it's one press away.",
        "/browse",
        _FIRST_KEPT_FILTER,
        admins=False,
        binds=("user",),
    ),
    GoalRule(
        "make_loop",
        "Make a Loop",
        "Choose where a Loop starts and ends on any video, and it plays over and over.",
        "/loops",
        _FIRST_LOOP,
        admins=False,
        binds=("user",),
    ),
    GoalRule(
        "open_theater",
        "Watch in Theater",
        "Theater plays several files side by side.",
        "/theater",
        _FIRST_THEATER,
        admins=False,
        binds=("user",),
    ),
    GoalRule(
        "first_download",
        "Download something",
        "Paste a link on Downloads and Sift saves it into your library.",
        "/downloads",
        _FIRST_DOWNLOAD,
        admins=True,
    ),
    _milestone(
        "library_fingerprinted",
        "Fingerprint every file",
        "After a scan, Sift fingerprints each file for you. That's how it finds copies.",
        "/settings/tasks",
        admins=True,
    ),
    _milestone(
        "faces_100",
        "Name 100 faces",
        "Each face you name teaches Sift who that person is.",
        "/organize/faces",
        admins=True,
    ),
    _milestone(
        "faces_1000",
        "Name 1,000 faces",
        "By now Sift recognizes most of the people in your library.",
        "/organize/faces",
        admins=True,
    ),
    _milestone(
        "queue_emptied",
        "Clear a group on Organize",
        "Answer everything in one group until nothing in it is left.",
        "/organize",
        admins=True,
    ),
    _milestone(
        "year_of_sift",
        "A year of Sift",
        "A year after the first file you open in Sift.",
        "/insights",
        admins=False,
    ),
)

#: The goals by name.
GOAL_BY_ID: dict[str, GoalRule] = {rule.id: rule for rule in GOALS}


@dataclass(frozen=True, slots=True)
class PathRule:
    """One learning path: its name, the one sentence that says what it teaches, its goals in order."""

    id: str
    title: str
    sentence: str
    goals: tuple[str, ...]


#: The learning paths, in the order they are listed. Declared here: a path is a thing to learn,
#: written once, and never a row somebody edits.
PATHS: tuple[PathRule, ...] = (
    PathRule(
        "set_up",
        "Set up your library",
        "Tell Sift where your files are, and let it read them.",
        ("add_library", "first_scan", "library_fingerprinted", "ask_stash_box", "first_download"),
    ),
    PathRule(
        "watch",
        "Watch and keep",
        "Watch what you have, keep what you like, and find it again.",
        (
            "view_something",
            "star_or_rate",
            "make_loop",
            "create_collection",
            "keep_filter",
            "open_theater",
            "year_of_sift",
        ),
    ),
    PathRule(
        "organize",
        "Organize with Sift",
        "Answer the questions on Organize, and Sift files the rest.",
        ("review_folder", "name_face", "faces_100", "queue_emptied", "faces_1000"),
    ),
)


async def goals_of(
    database: Database, viewer: Viewer, *, library: LibraryStore, frozen: dict[str, int]
) -> dict[str, Goal]:
    """Every goal this User can reach, each judged now: a first time from the record, a milestone
    from the achievements frozen (`frozen`, each name with the moment it was filed)."""
    goals: dict[str, Goal] = {}
    for rule in GOALS:
        if rule.admins and not viewer.is_admin:
            continue
        if rule.milestone:
            at = frozen.get(rule.id)
        elif rule.asks is not None:
            at = await rule.asks(library)
        else:
            params = tuple(viewer.id if one == "user" else one for one in rule.binds)
            row = await database.fetch_one(str(rule.statement), params)
            at = None if row is None or row["at"] is None else int(row["at"])
        goals[rule.id] = Goal(
            id=rule.id,
            title=rule.title,
            help=pieces_of(said(rule.help)),
            done=at is not None,
            done_at=at,
            href=rule.href,
        )
    return goals


def paths_of(goals: dict[str, Goal]) -> list[LearningPath]:
    """The paths, each with the goals this User can reach, in its order. A path with none is not
    listed: a guest is never shown a path made only of an admin's goals."""
    out: list[LearningPath] = []
    for rule in PATHS:
        held = [goals[name] for name in rule.goals if name in goals]
        if held:
            out.append(
                LearningPath(
                    id=rule.id,
                    title=rule.title,
                    sentence=pieces_of(said(rule.sentence)),
                    goals=held,
                )
            )
    return out


# --- the Organize board's cards, for the pile-emptied milestone ---------------------------------------

#: The bands whose piles are work somebody empties: a judgement or a clean-up. A log is not.
WORK_BANDS: frozenset[Band] = frozenset({Band.DECISION, Band.CLEANUP})


@dataclass(slots=True)
class Card:
    """One card as the Organize board draws it: a queue standing alone, or a group as one page.

    The same joining the board's client does (`frontend/src/lib/organize/bands.ts` `bandsOf`): the
    pending queues of a group join the first one's card and add their counts; the group's records
    ride on the card uncounted. The records are kept here for one reason: a decision made on the
    card may be recorded under one of them.
    """

    lead: Summary
    names: list[str] = field(default_factory=list)
    count: int = 0

    @property
    def title(self) -> str:
        return (self.lead.group_title if len(self.names) > 1 else None) or self.lead.title


def cards_of(queues: Sequence[Summary]) -> list[Card]:
    """The board's cards, in the order the board has them."""
    cards: list[Card] = []
    by_group: dict[str, Card] = {}
    for queue in queues:
        if not queue.pending:
            continue
        joined = by_group.get(queue.group) if queue.group else None
        if joined is not None:
            joined.names.append(queue.name)
            joined.count += queue.count
            continue
        card = Card(lead=queue, names=[queue.name], count=queue.count)
        cards.append(card)
        if queue.group:
            by_group[queue.group] = card
    for queue in queues:
        if queue.pending or not queue.group:
            continue
        found = by_group.get(queue.group)
        if found is not None:
            found.names.append(queue.name)
    return cards


# --- the achievements -----------------------------------------------------------------------------
#
# Milestones, each frozen ONCE as a `recaps` row (`achievement:<name>`) holding one card: the day it
# happened and the figure. Every date is worked out from the record, so an achievement judged a
# month late still carries the day it happened, which is what lets the same judgement run from the
# page and from the helper without either one's timing deciding the date.

#: The faces-named milestones: the figure, and the achievement's name.
FACES_MILESTONES: tuple[tuple[int, str], ...] = ((100, "faces_100"), (1000, "faces_1000"))

#: What each achievement is called on its card and in a list.
ACHIEVEMENTS: dict[str, str] = {
    "faces_100": "100 faces named",
    "faces_1000": "1,000 faces named",
    "queue_emptied": "Nothing left to decide",
    "library_fingerprinted": "Every file fingerprinted",
    "year_of_sift": "A year of Sift",
}

#: The period an achievement is filed under.
PREFIX = "achievement:"

_FIRST_PLAY = "SELECT MIN(started_at) AS at FROM plays WHERE user_id = ?"

#: This User's standing decisions on a card's queues, ever: how many, and the last one.
_DECIDED_ON = """
SELECT COUNT(*) AS n, MAX(decided_at) AS at FROM workbench_decisions
 WHERE actor_id = ? AND queue IN (SELECT value FROM json_each(?)) AND reversed_at IS NULL
"""

_ROLE = "SELECT role FROM users WHERE id = ?"
_LAST_FINGERPRINT_RUN = """
SELECT MAX(finished_at) AS at FROM work_runs WHERE family = ? AND finished_at IS NOT NULL
"""

#: The earliest day a count milestone is looked for on. Before Sift, so before any record.
_EPOCH_DAY = date(2020, 1, 1)


def in_words(day: date) -> str:
    """ "September 12, 2026"."""
    return f"{day:%B} {day.day}, {day.year}"


def achievement_head(row: RecapRow) -> RecapHead | None:
    """An achievement's row as a list names it, or None for a row that is not one."""
    name = row.period.removeprefix(PREFIX)
    if not row.period.startswith(PREFIX) or name not in ACHIEVEMENTS:
        return None
    return RecapHead(
        id=row.id,
        period=row.period,
        title=ACHIEVEMENTS[name],
        span=in_words(day_of(row.made_at)),
        made_at=row.made_at,
        seen_at=row.seen_at,
        cards=1,
    )


@dataclass(frozen=True, slots=True)
class Reached:
    """One milestone reached: which, when, what it says and the figure."""

    name: str
    day: date
    statement: str
    label: str
    value: int


async def freeze(database: Database, user_id: str, reached: Reached) -> None:
    """File one achievement. A second filing of the same one is a no-op (`write_recap`)."""
    card = KeptCard(
        id=new_id(),
        kind="achievement",
        statement=pieces_of(said(reached.statement)),
        figure=Figure(label=reached.label, value=reached.value, unit="count"),
    )
    await write_recap(
        database,
        user_id,
        PREFIX + reached.name,
        json.dumps([card.model_dump(mode="json")]),
        made_at=day_bounds(reached.day)[0],
    )
    log.info("insights.achievement", user_id=user_id, name=reached.name)


async def faces_named_by(database: Database, user_id: str, day: date) -> int:
    """How many faces this User had named by the end of a day.

    The insights engine's own `faces_named` statement, asked over everything before the day ends
    rather than over one day: one rule for what naming a face is, whichever reader asks.
    """
    _start, end = day_bounds(day)
    found = await rows_of(
        "faces_named",
        database.fetch_all,
        {"user": user_id, "start": 0, "end": end, "views": "[]", "hidden": "[]"},
    )
    return sum(int(row["whole"]) for row in found)


async def faces_milestones(
    database: Database, user_id: str, today: date, frozen: set[str]
) -> list[Reached]:
    """The faces-named milestones reached and not yet frozen, each on the day it was crossed.

    The day is found by halving: the count up to a day's end only rises, so the first day it
    reaches the figure is found in about a dozen reads, once, and never again after it is frozen.
    """
    wanted = [(figure, name) for figure, name in FACES_MILESTONES if name not in frozen]
    if not wanted:
        return []
    total = await faces_named_by(database, user_id, today)
    reached: list[Reached] = []
    for figure, name in wanted:
        if total < figure:
            continue
        low, high = _EPOCH_DAY, today
        while low < high:
            middle = low + timedelta(days=(high - low).days // 2)
            if await faces_named_by(database, user_id, middle) >= figure:
                high = middle
            else:
                low = middle + timedelta(days=1)
        reached.append(
            Reached(name, low, f"You named your {figure:,}th face.", "Faces named", figure)
        )
    return reached


async def year_of_sift(database: Database, user_id: str, today: date) -> Reached | None:
    """A year since this User's first sitting, on the day the year came round."""
    row = await database.fetch_one(_FIRST_PLAY, (user_id,))
    if row is None or row["at"] is None:
        return None
    first = day_of(int(row["at"]))
    try:
        came_round = first.replace(year=first.year + 1)
    except ValueError:  # the 29th of February: the year comes round on the 1st of March
        came_round = date(first.year + 1, 3, 1)
    if came_round > today:
        return None
    return Reached(
        "year_of_sift",
        came_round,
        f"A year ago you first opened a file in Sift, on {in_words(first)}.",
        "Years",
        1,
    )


async def queue_emptied(database: Database, user_id: str, cards: Sequence[Card]) -> Reached | None:
    """The first card this User worked on and that has nothing left, on the day of their last
    decision on it. A card nobody here decided on was not emptied by them, so it is not theirs."""
    for card in cards:
        if card.count > 0 or card.lead.band not in WORK_BANDS:
            continue
        row = await database.fetch_one(_DECIDED_ON, (user_id, json.dumps(card.names)))
        if row is None or not row["n"]:
            continue
        return Reached(
            "queue_emptied",
            day_of(int(row["at"])),
            f"Nothing left to decide in {card.title}.",
            "Decisions",
            int(row["n"]),
        )
    return None


async def library_fingerprinted(
    database: Database, content: ContentStore, today: date
) -> Reached | None:
    """Every file in the library fingerprinted, on the day the last fingerprinting finished.

    The same count the Build sheet shows (`lacks_fingerprint`), so this cannot disagree with it. A
    whole-library count, which is why only the helper asks it, never a page.
    """
    files = await content.asset_count()
    if not files:
        return None
    lacking = await content.count_lacking([lacks_fingerprint()])
    if lacking.files:
        return None
    run = await database.fetch_one(_LAST_FINGERPRINT_RUN, (Family.FINGERPRINT.value,))
    day = today if run is None or run["at"] is None else day_of(int(run["at"]))
    return Reached(
        "library_fingerprinted",
        day,
        "Sift fingerprinted every file in your library.",
        "Files",
        files,
    )


async def frozen_names(database: Database, user_id: str) -> set[str]:
    """The achievements this User already has."""
    return set(await frozen_at(database, user_id))


async def frozen_at(database: Database, user_id: str) -> dict[str, int]:
    """The achievements this User already has, each with the moment it is dated."""
    return {
        row.period.removeprefix(PREFIX): row.made_at
        for row in await recaps_of(database, user_id)
        if row.period.startswith(PREFIX)
    }


async def make_due(
    database: Database, user_id: str, today: date, *, content: ContentStore
) -> list[str]:
    """Freeze every achievement this User has reached and does not have yet. THE HELPER'S CALL.

    Called by the insights helper after it adds up a day, beside `recaps.make_due`. It judges the
    milestones that need no Organize board: faces named, a year of Sift, and (for an admin, since
    it is a fact about the whole library) every file fingerprinted, which is a whole-library count
    and so is asked here and never by a page. Answers the names it froze.
    """
    frozen = await frozen_names(database, user_id)
    found: list[Reached] = await faces_milestones(database, user_id, today, frozen)
    if "year_of_sift" not in frozen:
        year = await year_of_sift(database, user_id, today)
        if year is not None:
            found.append(year)
    if "library_fingerprinted" not in frozen:
        role = await database.fetch_one(_ROLE, (user_id,))
        if role is not None and str(role["role"]) == "admin":
            whole = await library_fingerprinted(database, content, today)
            if whole is not None:
                found.append(whole)
    for one in found:
        await freeze(database, user_id, one)
    return [one.name for one in found]


# --- Get to know Sift, whole ------------------------------------------------------------------------


class PathService:
    """Get to know Sift for one User: read the paths."""

    def __init__(
        self,
        database: Database,
        workbench: Workbench,
        *,
        library: LibraryStore,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._workbench = workbench
        self._library = library
        self._now = now

    async def summary(self, viewer: Viewer) -> Path:
        """Every path this User can walk, each goal judged now.

        The milestones a page may judge are frozen here first (faces named, a year of Sift, a pile
        emptied), so a goal reached since the last visit is done on this one; every file
        fingerprinted is a whole-library count and is left to the helper (`make_due`).
        """
        today = local_today(self._now())
        frozen = await frozen_names(self._db, viewer.id)
        found = await faces_milestones(self._db, viewer.id, today, frozen)
        if "year_of_sift" not in frozen:
            year = await year_of_sift(self._db, viewer.id, today)
            if year is not None:
                found.append(year)
        if "queue_emptied" not in frozen:
            emptied = await queue_emptied(self._db, viewer.id, await self._cards(viewer))
            if emptied is not None:
                found.append(emptied)
        for one in found:
            await freeze(self._db, viewer.id, one)
        goals = await goals_of(
            self._db, viewer, library=self._library, frozen=await frozen_at(self._db, viewer.id)
        )
        return Path(paths=paths_of(goals))

    async def _cards(self, viewer: Viewer) -> list[Card]:
        """The Organize board's cards, for somebody the board is for. The board is an admin's.

        Read only while the pile-emptied milestone is not yet reached: it is the one goal that needs
        the board, and once frozen it is never asked again.

        Each card also carries the names of the RECEIPT-ONLY reversers of its group: a decision
        taken on a card is not always recorded under a queue on it: naming a group on the Faces
        card is a receipt of `identified`, and discarding one of `ignored`, and neither is a card.
        A reverser says which card's decisions it holds by declaring the card's `group`, exactly
        as a queue does; see `Workbench.recorded_under`.
        """
        if not viewer.is_admin:
            return []
        cards = cards_of(await self._workbench.board(viewer))
        for card in cards:
            for name in self._workbench.recorded_under(card.lead.group):
                if name not in card.names:
                    card.names.append(name)
        return cards
