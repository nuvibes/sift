# SPDX-License-Identifier: AGPL-3.0-or-later
"""`GET /api/insights`: the page for one period, as the server says it."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from sift.kernel import wiring
from sift.kernel.access import (
    MAX_PAGE_SIZE,
    AllOf,
    AssetFilter,
    Concealment,
    Repository,
    Viewer,
    Where,
)
from sift.kernel.access.sentences import Line
from sift.kernel.db import Database
from sift.kernel.jobs.families import FAMILY_LABELS, Family
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import HistoryPiece, pieces_of
from sift.kernel.workbench import Card, Workbench
from sift.slices.auth import current_viewer
from sift.slices.insights import statements as st
from sift.slices.insights import store
from sift.slices.insights.metrics import ADMIN_ONLY, MINUTES, split_file_key
from sift.slices.insights.models import (
    Bar,
    BarPart,
    Calendar,
    Chart,
    DayValue,
    Figure,
    InsightsBlock,
    InsightsPage,
    NamedList,
    NamedRow,
    Unit,
)
from sift.slices.insights.naming import LIST_LENGTH, known, named_list, top
from sift.slices.insights.statements import Moment, Period

router = APIRouter(tags=["insights"])

#: The spans the tabs offer, as the address spells them.
Span = Literal["day", "week", "month", "year", "all"]

#: How many recaps the page's recaps block lists; every one is on the recaps screen.
RECAPS_LISTED = 12

#: The metrics the page reads for anybody, and the ones it reads for an admin as well.
PAGE_METRICS = frozenset(
    {
        "viewed_ms",
        "viewed_ms:kind",
        "sittings",
        "sittings:kind",
        "viewed_ms:person",
        "viewed_ms:site",
        "viewed_ms:tag",
        "viewed_ms:collection",
        "viewed_ms:photo_set",
        "viewed_ms:song",
        "sittings:file",
        "viewed_ms:hour",
        "viewed_ms:weekday",
        "theater_ms:wall",
        "pickups",
        "first_opened:person",
        "earliest_start",
        "latest_finish",
        "rated",
        "rated:file",
        "starred",
        "o",
        "o:file",
        "starred:file",
        "decided",
        "decided:queue",
        "faces_named",
        "files_filed",
        "files_added",
        "files_added:site",
        "files_removed",
    }
)

#: What the viewing blocks need of the period before, for a comparison.
COMPARED_METRICS = frozenset({"viewed_ms", "sittings"})

#: Which clock the reader writes a time of day on: the key the appearance settings register it
#: under, read here through the preference seam because a slice does not import the slice that
#: registers it. `test_api` holds the two spellings together.
CLOCK_KEY = "appearance.clock"

#: A single day is compared with the average day over this many days before it.
USUAL_DAYS = 28

#: The fewest recorded days that average has to cover before a day is compared with it.
USUAL_FLOOR_DAYS = 7

#: THE BLOCKS, in the order the page draws them, with the words at the head of each. American
#: spelling on screen ("Organizing"); the id is the contract's and never shown.
TITLES: Mapping[str, str] = {
    "overview": "Overview",
    "most_viewed": "Most viewed",
    "by_kind": "By kind",
    "theater": "Theater",
    "sittings": "Visits",
    "when": "When",
    "opinions": "Opinions",
    "organizing": "Organizing",
    "arrived": "What arrived",
    "machine": "What Sift did",
}

#: The blocks about VIEWING, which say nothing below `SITTINGS_FLOOR` sittings.
VIEWING_BLOCKS = ("overview", "most_viewed", "by_kind", "theater", "sittings", "when", "opinions")


# --- the figures, as this reader may see them ----------------------------------------------------


@dataclass
class Book:
    """One period's rows read for one reader: summed over the days, and kept per day for the
    charts and the minutes of the day. Every figure in here is already what the reader may see."""

    locked: bool
    #: (metric, key) -> the figure over the period, as this reader may see it.
    shown: dict[tuple[str, str], int] = field(default_factory=dict)
    #: (metric, key) -> the part of it that came from hidden things. Only kept while unlocked.
    hidden: dict[tuple[str, str], int] = field(default_factory=dict)
    #: day -> (metric, key) -> figure, for the bars and the minutes of the day.
    daily: dict[str, dict[tuple[str, str], int]] = field(default_factory=dict)
    #: Whether anything this period counted came from hidden things.
    any_hidden: bool = False

    @classmethod
    def of(cls, rows: Iterable[store.DayRow], *, locked: bool) -> Book:
        book = cls(locked=locked)
        for row in rows:
            value = row.shown(locked=locked)
            where = (row.metric, row.key)
            book.daily.setdefault(row.day, {})[where] = value
            book.any_hidden = book.any_hidden or row.hidden > 0
            if row.metric in MINUTES:
                continue
            book.shown[where] = book.shown.get(where, 0) + value
            if not locked:
                book.hidden[where] = book.hidden.get(where, 0) + row.hidden
        return book

    def total(self, metric: str) -> int:
        return self.shown.get((metric, ""), 0)

    def hidden_of(self, metric: str, key: str = "") -> int:
        return self.hidden.get((metric, key), 0)

    def keyed(self, metric: str) -> dict[str, int]:
        """Every key of a metric with something in it for this reader, largest first. A key that
        comes to nothing while locked is a hidden thing, and is left out here, once, for every
        list and every statement that ranks."""
        found = {
            key: value for (name, key), value in self.shown.items() if name == metric and value > 0
        }
        return dict(sorted(found.items(), key=lambda one: (-one[1], one[0])))

    def files(self, metric: str) -> tuple[int, int]:
        """How many FILES a per-file metric names over the period, and how many of them are
        hidden things (0 while locked): each file once, however many days it appears on."""
        whole = hidden = 0
        for (name, key), value in self.shown.items():
            if name != metric or value <= 0:
                continue
            whole += 1
            if not self.locked and self.hidden.get((name, key), 0) >= value:
                hidden += 1
        return whole, hidden

    def files_figure(self, label: str, metric: str) -> Figure:
        """A count of the files a per-file metric names (`files`), as a figure."""
        whole, hidden = self.files(metric)
        return Figure(label=label, value=whole, unit="count", hidden_part=hidden)

    def figure(
        self, label: str, metric: str, unit: Unit, key: str = "", caption: Line | None = None
    ) -> Figure:
        return Figure(
            label=label,
            value=self.shown.get((metric, key), 0),
            unit=unit,
            hidden_part=0 if self.locked else self.hidden_of(metric, key),
            caption=pieces_of(caption) if caption else [],
        )

    def day_total(self, day: date, metric: str) -> int:
        return self.daily.get(day.isoformat(), {}).get((metric, ""), 0)

    def moments(self, metric: str) -> list[Moment]:
        """A minute of the day per day that had a sitting this reader may see. A day whose every
        sitting was hidden reads 0 while locked, and is not a moment (`metrics.MINUTES`)."""
        found: list[Moment] = []
        for day, figures in sorted(self.daily.items()):
            if figures.get(("sittings", ""), 0) <= 0 or (metric, "") not in figures:
                continue
            found.append(Moment(date.fromisoformat(day), figures[(metric, "")]))
        return found


# --- names, covers and what is still there -------------------------------------------------------


async def _kinds_of(access: Repository, viewer: Viewer, ids: Sequence[str]) -> dict[str, str]:
    """Each of these files' kind, for the ones this reader may be shown."""
    if not ids:
        return {}
    return {
        key: view.asset.media_type for key, view in (await access.assets_of(viewer, ids)).items()
    }


async def _files_of_person(
    access: Repository, viewer: Viewer, ids: Sequence[str], person_id: str
) -> set[str]:
    """Which of these files this person is on, as this reader may see them: the walls' own read,
    filtered by the same two leaves a search for her among these files would use."""
    wanted = sorted(set(ids))
    found: set[str] = set()
    for start in range(0, len(wanted), MAX_PAGE_SIZE):
        chunk = tuple(wanted[start : start + MAX_PAGE_SIZE])
        page = await access.visible_assets(
            viewer,
            limit=len(chunk),
            asset_filter=AssetFilter(
                where=AllOf((Where("assets", chunk), Where("people", (person_id,))))
            ),
        )
        found.update(item.asset.id for item in page.items)
    return found


async def _files_seen(
    access: Repository, viewer: Viewer, keyed: Mapping[str, int]
) -> tuple[dict[str, int], dict[str, str]]:
    """The period's files with their views, largest first, and each one's kind."""
    views: dict[str, int] = defaultdict(int)
    kinds: dict[str, str] = {}
    for key, value in keyed.items():
        kind, asset = split_file_key(key)
        views[asset] += value
        if kind:
            kinds[asset] = kind
    unknown = [asset for asset in views if asset not in kinds]
    kinds.update(await _kinds_of(access, viewer, unknown))
    ranked = dict(sorted(views.items(), key=lambda one: (-one[1], one[0])))
    return ranked, kinds


def _by_kind(kinds: Mapping[str, str], ids: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for key in ids:
        kind = kinds.get(key)
        if kind is not None:
            counts[kind] += 1
    return dict(counts)


# --- the blocks ----------------------------------------------------------------------------------


def _block(
    block_id: str,
    lines: Sequence[Line | None] = (),
    *,
    floor_reached: bool = True,
    figures: Sequence[Figure] = (),
    chart: Chart | None = None,
    lists: Sequence[NamedList | None] = (),
    calendar: Calendar | None = None,
    notes: Sequence[Line | None] = (),
) -> InsightsBlock:
    """One block. Below its floor it says the one line and nothing else: no figure a person could
    read a claim into."""
    if not floor_reached:
        return InsightsBlock(
            id=block_id,
            title=TITLES[block_id],
            floor_reached=False,
            statements=[pieces_of(st.plain(st.NOT_ENOUGH))],
        )
    return InsightsBlock(
        id=block_id,
        title=TITLES[block_id],
        floor_reached=True,
        statements=[pieces_of(line) for line in st.statements_of(lines)],
        figures=list(figures),
        chart=chart,
        lists=[one for one in lists if one is not None],
        calendar=calendar,
        notes=[pieces_of(line) for line in st.statements_of(notes)],
    )


def _kind_parts(figures: Mapping[tuple[str, str], int]) -> list[BarPart]:
    return [
        BarPart(kind=kind, value=figures.get(("viewed_ms:kind", kind), 0))
        for kind in (*st.KINDS, st.THEATER)
    ]


def _overview_chart(period: Period, book: Book, hours: st.Clock) -> Chart | None:
    """A day's hours; bars per day split by kind over a week or a month; per month over a year or
    more. The bar holding today is marked while the period does, and the tallest bar is said.

    **Every bar of the period, the days still to come at nothing.**
    """
    if period.span == "day":
        return _hours_chart(book, hours)
    last = period.end
    if period.span in ("week", "month"):
        bars = []
        days: list[date] = []
        day = period.start
        while day <= last:
            label = st.WEEKDAYS_SHORT[day.weekday()] if period.span == "week" else str(day.day)
            bars.append(Bar(label=label, parts=_kind_parts(book.daily.get(day.isoformat(), {}))))
            days.append(day)
            day += timedelta(days=1)
        return _said_chart(period, bars, days)
    months: dict[str, dict[tuple[str, str], int]] = {}
    for iso, figures in book.daily.items():
        month = months.setdefault(iso[:7], {})
        for where, value in figures.items():
            if where[0] == "viewed_ms:kind":
                month[where] = month.get(where, 0) + value
    bars = []
    firsts: list[date] = []
    cursor = period.start.replace(day=1)
    while cursor <= last:
        key = cursor.strftime("%Y-%m")
        label = cursor.strftime("%b") if period.span == "year" else cursor.strftime("%b %Y")
        bars.append(Bar(label=label, parts=_kind_parts(months.get(key, {}))))
        firsts.append(cursor)
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return _said_chart(period, bars, firsts)


def _trend(period: Period, book: Book, metric: str) -> list[int]:
    """One figure bar by bar of the Overview's chart: the days of a week or a month, the months of
    a year or of everything, and, for time viewed only, the hours of a day. The same bars the
    chart draws, so a tile's little run of bars and the chart below it agree bar for bar."""
    if period.span == "day":
        if metric != "viewed_ms":
            return []
        return [book.shown.get(("viewed_ms:hour", f"{hour:02d}"), 0) for hour in range(24)]
    if period.span in ("week", "month"):
        return [
            book.day_total(period.start + timedelta(days=n), metric)
            for n in range((period.end - period.start).days + 1)
        ]
    months: dict[str, int] = defaultdict(int)
    for iso, figures in book.daily.items():
        months[iso[:7]] += figures.get((metric, ""), 0)
    out: list[int] = []
    cursor = period.start.replace(day=1)
    while cursor <= period.end:
        out.append(months.get(cursor.strftime("%Y-%m"), 0))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return out


def _said_chart(period: Period, bars: list[Bar], starts: Sequence[date]) -> Chart:
    """Bars with today's marked and the tallest said. `starts` is the first day of each bar."""
    totals = [sum(part.value for part in bar.parts) for bar in bars]
    caption: Line | None = None
    if totals and max(totals) > 0:
        top = totals.index(max(totals))
        caption = st.biggest(period, starts[top], totals[top])
    today: int | None = None
    if period.is_open and starts:
        today = max(at for at, first in enumerate(starts) if first <= period.today)
    return Chart(unit="ms", bars=bars, today=today, caption=pieces_of(caption) if caption else [])


def _hours_chart(book: Book, hours: st.Clock) -> Chart:
    """The hours of the day you view in, midnight to midnight, each marked on the reader's clock.
    Not split by kind: the hour a sitting started in is recorded for the whole of it."""
    viewed = [book.shown.get(("viewed_ms:hour", f"{hour:02d}"), 0) for hour in range(24)]
    busiest = max(range(24), key=lambda hour: (viewed[hour], -hour))
    caption = st.busiest_hour(busiest, viewed[busiest], hours)
    return Chart(
        unit="ms",
        bars=[
            Bar(label=st.hour_mark(hour, hours), parts=[BarPart(kind="all", value=viewed[hour])])
            for hour in range(24)
        ],
        caption=pieces_of(caption) if caption else [],
    )


def _calendar(period: Period, book: Book) -> Calendar | None:
    """Every day of a month or a year so far with the time viewed on it, for the heat-map."""
    if period.span not in ("month", "year"):
        return None
    days: list[DayValue] = []
    day, last = period.start, min(period.end, period.today)
    while day <= last:
        days.append(DayValue(day=day.isoformat(), value=book.day_total(day, "viewed_ms")))
        day += timedelta(days=1)
    return Calendar(unit="ms", days=days)


def _growth(period: Period, book: Book) -> Chart | None:
    """The files that arrived each month, over the whole record: how the library grew."""
    if period.span != "all":
        return None
    months: dict[str, int] = {}
    for iso, figures in book.daily.items():
        months[iso[:7]] = months.get(iso[:7], 0) + figures.get(("files_added", ""), 0)
    bars = []
    cursor = period.start.replace(day=1)
    while cursor <= period.end:
        key = cursor.strftime("%Y-%m")
        bars.append(
            Bar(
                label=cursor.strftime("%b %Y"),
                parts=[BarPart(kind="added", value=months.get(key, 0))],
            )
        )
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return Chart(unit="count", bars=bars, today=len(bars) - 1 if bars else None)


# --- the page ------------------------------------------------------------------------------------


@dataclass
class Page:
    """What one request is working with: who, when, the figures, and the reader's clock."""

    viewer: Viewer
    period: Period
    book: Book
    figures: store.Figures
    hours: st.Clock


async def _first_day(database: Database, user_id: str) -> date | None:
    return await store.first_day(database, user_id)


async def _ever_sat(database: Database, user_id: str) -> bool:
    return await store.ever_sat(database, user_id)


async def _compared(database: Database, page: Page, first: date | None) -> Line | None:
    """A comparison with the period before, only between two CLOSED periods and only where the
    earlier one was recorded whole, reached by the helper, and past the floor itself."""
    period, book = page.period, page.book
    before = period.previous()
    if before is None or period.is_open or first is None or first > before.start:
        return None
    earlier = await store.rows(database, page.viewer.id, before.start, before.end, COMPARED_METRICS)
    if earlier.missing_days:
        return None
    then = Book.of(earlier.rows, locked=book.locked)
    if then.total("sittings") < st.SITTINGS_FLOOR:
        return None
    return st.compared(period, book.total("viewed_ms"), before, then.total("viewed_ms"))


async def _usual(database: Database, page: Page, first: date | None) -> Line | None:
    """A closed day compared with the average day over the `USUAL_DAYS` before it: only days
    since the record began, at least `USUAL_FLOOR_DAYS` of them, recorded whole and past the
    floor. Today is never compared: it is still happening."""
    period, book = page.period, page.book
    if period.span != "day" or period.is_open or first is None:
        return None
    start = max(period.start - timedelta(days=USUAL_DAYS), first)
    end = period.start - timedelta(days=1)
    counted_days = (end - start).days + 1
    if counted_days < USUAL_FLOOR_DAYS:
        return None
    earlier = await store.rows(database, page.viewer.id, start, end, COMPARED_METRICS)
    if earlier.missing_days:
        return None
    then = Book.of(earlier.rows, locked=book.locked)
    if then.total("sittings") < st.SITTINGS_FLOOR:
        return None
    return st.compared_with_usual(book.total("viewed_ms"), then.total("viewed_ms") // counted_days)


async def _viewing_blocks(
    access: Repository, database: Database, page: Page, first: date | None
) -> list[InsightsBlock]:
    book = page.book
    if book.total("sittings") < st.SITTINGS_FLOOR:
        return [_block(block_id, floor_reached=False) for block_id in VIEWING_BLOCKS]
    kinds_ms = {
        kind: value for (metric, kind), value in book.shown.items() if metric == "viewed_ms:kind"
    }
    viewed_ms = book.total("viewed_ms")

    overview, busiest_day = await _overview_block(database, page, first, kinds_ms, viewed_ms)
    most_viewed, files_seen, all_kinds = await _most_viewed_block(access, database, page)
    by_kind = await _by_kind_block(access, database, page, files_seen, all_kinds)
    theater = await _theater_block(access, database, page)
    sittings = await _sittings_block(access, database, page)
    when = await _when_block(page, busiest_day)
    opinions = await _opinions_block(access, database, page)
    return [overview, most_viewed, by_kind, theater, sittings, when, opinions]


async def _overview_block(
    database: Database, page: Page, first: date | None, kinds_ms: dict[str, int], viewed_ms: int
) -> tuple[InsightsBlock, Line | None]:
    """Overview, and the busiest weekday the When block repeats."""
    viewer, period, book = page.viewer, page.period, page.book
    # Overview. A day is compared with your average day; a longer period with the one before.
    compared = (
        await _usual(database, page, first)
        if period.span == "day"
        else await _compared(database, page, first)
    )
    notes: list[Line | None] = []
    if page.figures.missing_days:
        notes.append(st.still_counting())
    if book.locked and viewer.concealment is Concealment.PLACEHOLDER and book.any_hidden:
        notes.append(st.some_hidden(period))
    days = _days_counted(period, first)
    weekdays = book.keyed("viewed_ms:weekday")
    busiest_day: Line | None = None
    if weekdays:
        busiest, busiest_ms = next(iter(weekdays.items()))
        busiest_day = st.busiest_weekday(period, int(busiest), busiest_ms, viewed_ms)
    third = (
        Figure(
            label="First opened at",
            value=min((one.minute for one in book.moments("earliest_start")), default=0),
            unit="minute_of_day",
        )
        if period.span == "day" and book.moments("earliest_start")
        else Figure(
            label="Daily average",
            value=viewed_ms // days,
            unit="ms",
            hidden_part=0 if book.locked else book.hidden_of("viewed_ms") // days,
        )
    )
    overview = _block(
        "overview",
        [st.viewed(period, viewed_ms, kinds_ms), compared, *notes],
        figures=[
            # One caption a card: the comparison where there is one, else the busiest weekday.
            book.figure("Viewed", "viewed_ms", "ms", caption=compared or busiest_day).model_copy(
                update={"trend": _trend(period, book, "viewed_ms")}
            ),
            book.figure("Sessions", "sittings", "count").model_copy(
                update={"trend": _trend(period, book, "sittings")}
            ),
            third,
        ],
        chart=_overview_chart(period, book, page.hours),
        calendar=_calendar(period, book),
        notes=notes,
    )
    return overview, busiest_day


async def _most_viewed_block(
    access: Repository, database: Database, page: Page
) -> tuple[InsightsBlock, dict[str, int], dict[str, str]]:
    """The most viewed people, Sites, tags, Collections, Photo Sets, songs and files."""
    viewer, period, book = page.viewer, page.period, page.book
    # Most viewed: the people, Sites, tags, Collections, Photo Sets, songs and files.
    files_seen, all_kinds = await _files_seen(access, viewer, book.keyed("sittings:file"))
    people = await top(access, database, viewer, "person", book.keyed("viewed_ms:person"))
    person_line: Line | None = None
    if people:
        leader, leader_ms, _cover = people[0]
        theirs = await _files_of_person(access, viewer, list(files_seen), leader.id)
        person_line = st.most_viewed_person(period, leader, leader_ms, _by_kind(all_kinds, theirs))
    most_viewed = _block(
        "most_viewed",
        [person_line],
        lists=[
            named_list("People", people, "ms"),
            named_list(
                "Sites",
                await top(access, database, viewer, "site", book.keyed("viewed_ms:site")),
                "ms",
            ),
            named_list(
                "Tags",
                await top(access, database, viewer, "tag", book.keyed("viewed_ms:tag")),
                "ms",
            ),
            named_list(
                "Collections",
                await top(
                    access, database, viewer, "collection", book.keyed("viewed_ms:collection")
                ),
                "ms",
            ),
            named_list(
                "Photo Sets",
                await top(access, database, viewer, "photo_set", book.keyed("viewed_ms:photo_set")),
                "ms",
            ),
            # The songs on the files watched longest: the time of every file carrying each. Titled
            # with the page's name, as every list here is (People, Sites, Tags): the page is Music.
            named_list(
                "Music",
                await top(access, database, viewer, "song", book.keyed("viewed_ms:song")),
                "ms",
            ),
            named_list("Files", await top(access, database, viewer, "asset", files_seen), "views"),
        ],
    )
    return most_viewed, files_seen, all_kinds


async def _by_kind_block(
    access: Repository,
    database: Database,
    page: Page,
    files_seen: dict[str, int],
    all_kinds: dict[str, str],
) -> InsightsBlock:
    """The files by kind, the files come back to, and the Photo Sets looked through."""
    viewer, book = page.viewer, page.book
    # By kind: the files, the files you came back to, the Photo Sets looked through.
    came_back = sum(1 for value in files_seen.values() if value >= st.CAME_BACK)
    files_line = st.files_by_kind(_by_kind(all_kinds, files_seen), book.total("sittings"))
    by_kind = _block(
        "by_kind",
        [
            files_line,
            st.came_back(came_back),
            st.photo_sets(len(book.keyed("viewed_ms:photo_set"))),
            # Counted over the songs this reader may still be shown: a song they hid since, or one
            # whose files the vault holds back, is no line of theirs (the access layer's answer).
            st.songs(
                len(
                    await known(
                        access, database, viewer, "song", list(book.keyed("viewed_ms:song"))
                    )
                )
            ),
        ],
        figures=[
            Figure(
                label="Files opened",
                value=len(files_seen),
                unit="count",
                caption=pieces_of(files_line) if files_line else [],
            ),
            Figure(
                label="Files you came back to",
                value=came_back,
                unit="count",
                caption=pieces_of(st.three_times_or_more()),
            ),
            Figure(
                label="Photo Sets looked through",
                value=len(book.keyed("viewed_ms:photo_set")),
                unit="count",
            ),
        ],
        # The time by kind as one whole, split into its shares: the Overview's bars, summed.
        chart=Chart(
            kind="share",
            unit="ms",
            bars=[Bar(label="Viewed", parts=_kind_parts(book.shown))],
        ),
    )
    return by_kind


async def _theater_block(access: Repository, database: Database, page: Page) -> InsightsBlock:
    """Time in Theater and the Saved Layouts it was spent on."""
    viewer, book = page.viewer, page.book
    # Theater.
    walls = book.keyed("theater_ms:wall")
    named_walls = await top(access, database, viewer, "wall", walls)
    top_wall = named_walls[0] if named_walls else None
    theater_ms = book.shown.get(("viewed_ms:kind", st.THEATER), 0)
    sessions = book.shown.get(("sittings:kind", st.THEATER), 0)
    theater = _block(
        "theater",
        [
            st.theater(
                page.period,
                theater_ms,
                top_wall[0] if top_wall else None,
                top_wall[1] if top_wall else 0,
                sessions,
            )
        ],
        figures=[
            book.figure("In Theater", "viewed_ms:kind", "ms", st.THEATER),
            book.figure("Sessions", "sittings:kind", "count", st.THEATER),
        ],
        lists=[named_list("Saved Layouts", named_walls, "ms")],
    )
    return theater


async def _sittings_block(access: Repository, database: Database, page: Page) -> InsightsBlock:
    """How often Sift was opened, and what was opened first."""
    viewer, period, book = page.viewer, page.period, page.book
    # Visits: how often you sat down, and what you opened first.
    pickups = book.total("pickups")
    firsts = await top(access, database, viewer, "person", book.keyed("first_opened:person"))
    sittings = _block(
        "sittings",
        [
            st.opened(period, pickups),
            st.opened_first(pickups, firsts[0][0], firsts[0][1]) if firsts else None,
        ],
        figures=[
            book.figure("Times you opened Sift", "pickups", "count").model_copy(
                update={"trend": _trend(period, book, "pickups")}
            )
        ],
        lists=[named_list("First opened", firsts, "times")],
    )
    return sittings


async def _when_block(page: Page, busiest_day: Line | None) -> InsightsBlock:
    """The earliest start, the latest finish, the busiest weekday and the hours."""
    period, book = page.period, page.book
    # When: the earliest start, the latest finish, the busiest weekday, the hours. A day's hours
    # are the Overview's chart, so its When is the two times alone.
    starts = book.moments("earliest_start")
    finishes = book.moments("latest_finish")
    when_lines: list[Line | None] = []
    when_figures: list[Figure] = []
    if starts and finishes:
        earliest = min(starts, key=lambda one: (one.minute, one.day))
        latest = max(finishes, key=lambda one: (one.minute, one.day))
        when_lines.append(st.earliest_and_latest(period, earliest, latest, page.hours))
        for label, moment in (("Earliest start", earliest), ("Latest finish", latest)):
            caption = st.on_the_day(period, moment)
            when_figures.append(
                Figure(
                    label=label,
                    value=moment.minute,
                    unit="minute_of_day",
                    caption=pieces_of(caption) if caption else [],
                )
            )
    when_lines.append(busiest_day)
    when = _block(
        "when",
        when_lines,
        figures=when_figures,
        chart=None if period.span == "day" else _hours_chart(book, page.hours),
    )
    return when


async def _opinions_block(access: Repository, database: Database, page: Page) -> InsightsBlock:
    """Files rated and starred, and the O presses."""
    viewer, period, book = page.viewer, page.period, page.book
    # Opinions. Files rated and starred are counted by FILE over the period (`Book.files`).
    rated, _ = book.files("rated:file")
    starred, _ = book.files("starred:file")
    o_files = await top(access, database, viewer, "asset", book.keyed("o:file"))
    starred_files = await top(access, database, viewer, "asset", book.keyed("starred:file"))
    opinions = _block(
        "opinions",
        [
            st.opinions(rated, starred),
            st.o_count(period, book.total("o")),
        ],
        figures=[
            book.files_figure("Rated", "rated:file"),
            book.files_figure("Starred", "starred:file"),
            book.figure("O count", "o", "count"),
        ],
        lists=[
            named_list("Files you starred", starred_files, "times"),
            named_list("Most O presses", o_files, "presses"),
        ],
    )
    return opinions


def _days_counted(period: Period, first: date | None) -> int:
    """The days a daily average divides by: the period's days so far, from the day the record
    began where it began inside the period. Before that day Sift was not counting, and dividing by
    days nobody counted would make a month begun on the 22nd read a quarter of what was viewed a
    day."""
    start = max(period.start, first) if first is not None else period.start
    last = min(period.end, period.today)
    return max((last - start).days + 1, 1)


#: The kind of a piece that names a place on a screen (an Organize card) by its address alone.
PLACE = "place"


def _by_card(board: Workbench, decided: Mapping[str, int]) -> NamedList | None:
    """The questions answered, by the Organize card each was answered on, most first. A decision
    recorded under a name no card answers is in the total and in no row."""
    counts: dict[Card, int] = defaultdict(int)
    for name, value in decided.items():
        card = board.card_of(name)
        if card is not None:
            counts[card] += value
    ranked = sorted(counts.items(), key=lambda one: (-one[1], one[0].title))[:LIST_LENGTH]
    rows = [
        NamedRow(
            # A place on a screen rather than a thing with a page: an address carries its kind, so
            # the client's one rule for a piece draws it as a link.
            piece=HistoryPiece(text=card.title, kind=PLACE, href=f"/organize/{card.name}"),
            value=value,
            unit="count",
        )
        for card, value in ranked
    ]
    return NamedList(title="By card", rows=rows) if rows else None


def _organizing(page: Page, board: Workbench) -> InsightsBlock:
    book = page.book
    if book.total("decided") < st.DECISIONS_FLOOR:
        return _block("organizing", floor_reached=False)
    return _block(
        "organizing",
        [
            st.organized(page.period, book.total("decided")),
            st.named_and_filed(book.total("faces_named"), book.total("files_filed")),
        ],
        figures=[
            book.figure("Questions answered", "decided", "count"),
            book.figure("Faces named", "faces_named", "count"),
            book.figure("Files filed", "files_filed", "count"),
        ],
        lists=[_by_card(board, book.keyed("decided:queue"))],
    )


async def _arrived(access: Repository, database: Database, page: Page) -> InsightsBlock:
    book = page.book
    sites = await top(access, database, page.viewer, "site", book.keyed("files_added:site"))
    most = st.most_from(sites[0][0], sites[0][1]) if sites else None
    return _block(
        "arrived",
        [
            st.arrived(page.period, book.total("files_added")),
            st.deleted(book.total("files_removed")),
        ],
        figures=[
            book.figure("Arrived", "files_added", "count", caption=most),
            book.figure("Deleted", "files_removed", "count"),
        ],
        chart=_growth(page.period, book),
        lists=[named_list("By Site", sites, "files")],
    )


def _machine(page: Page) -> InsightsBlock:
    """What Sift did. Only ever built for an admin: see the module docstring."""
    book = page.book
    # By the words Activity names a family with: two families it names alike (each read as Other)
    # are one row of their added time, never two rows both called Other.
    families: dict[str, int] = defaultdict(int)
    for family, value in book.keyed("work_ms:family").items():
        families[_family_words(family)] += value
    rows = [
        NamedRow(
            piece=HistoryPiece(text=words),
            value=value,
            unit="ms",
            cover=None,
        )
        for words, value in sorted(families.items(), key=lambda one: (-one[1], one[0]))
    ]
    return _block(
        "machine",
        [
            st.worked(page.period, sum(families.values())),
            st.found_and_fingerprinted(book.total("faces_found"), book.total("fingerprints_made")),
        ],
        figures=[
            Figure(label="Tasks", value=sum(families.values()), unit="ms"),
            book.figure("Faces found", "faces_found", "count"),
            book.figure("Files fingerprinted", "fingerprints_made", "count"),
        ],
        lists=[NamedList(title="By task", rows=rows) if rows else None],
    )


def _family_words(family: str) -> str:
    """A task family as the Activity screen names it."""
    try:
        return FAMILY_LABELS[Family(family)]
    except ValueError:
        return FAMILY_LABELS[Family.OTHER]


async def _first_sentences(
    access: Repository, page: Page, blocks: Sequence[InsightsBlock], ever: bool
) -> list[list[HistoryPiece]]:
    """Up to three statements, by the fixed order of interest (viewing, then organizing, then the
    library), each only past its floor. A guest nothing is shared with, and a User who has viewed
    nothing yet, are told so instead."""
    viewer, book = page.viewer, page.book
    if not viewer.is_admin and not ever:
        permitted, _concealed = await access.visible_counts(viewer)
        if permitted == 0:
            return [pieces_of(st.plain(st.NOTHING_SHARED))]
    if not ever:
        return [pieces_of(st.plain(st.NOTHING_YET))]
    first: list[list[HistoryPiece]] = []
    by_id = {block.id: block for block in blocks}
    for block_id in ("overview", "organizing"):
        block = by_id.get(block_id)
        if block is not None and block.floor_reached and block.statements:
            first.append(block.statements[0])
    if book.total("files_added") > 0:
        first.append(pieces_of(st.arrived(page.period, book.total("files_added"))))
    return first[:3]


async def clock_of(settings: SettingsSeam, viewer: Viewer) -> st.Clock:
    """The clock this reader writes a time of day on. Anything but "24" is the default, which is
    what an unset or retired value means, the same reading the browser's own store makes."""
    return "24" if await settings.get_user(viewer.id, CLOCK_KEY) == "24" else "12"


@router.get("/insights", response_model=InsightsPage)
async def insights_page(
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    board: Annotated[Workbench, Depends(wiring.workbench)],
    period: Annotated[Span, Query()] = "day",
    at: Annotated[date | None, Query()] = None,
) -> InsightsPage:
    """The page for one period: `at` is any day inside it, today by default."""
    today = store.local_today()
    first = await _first_day(database, viewer.id)
    within = st.period_of(period, min(at or today, today), today, first)
    metrics = PAGE_METRICS | ADMIN_ONLY if viewer.is_admin else PAGE_METRICS
    figures = await store.rows(database, viewer.id, within.start, within.end, metrics)
    book = Book.of(figures.rows, locked=not viewer.show_hidden)
    page = Page(
        viewer=viewer,
        period=within,
        book=book,
        figures=figures,
        hours=await clock_of(settings, viewer),
    )

    blocks = await _viewing_blocks(access, database, page, first)
    blocks.append(_organizing(page, board))
    blocks.append(await _arrived(access, database, page))
    if viewer.is_admin:
        blocks.append(_machine(page))

    ever = await _ever_sat(database, viewer.id) or any(
        day.get(("sittings", ""), 0) > 0 for day in book.daily.values()
    )
    return InsightsPage(
        period=period,
        from_=within.start.isoformat(),
        to=within.end.isoformat(),
        today_is_live=today.isoformat() in figures.live_days,
        first_sentences=await _first_sentences(access, page, blocks, ever),
        blocks=blocks,
    )
