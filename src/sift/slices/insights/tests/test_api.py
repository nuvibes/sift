# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Insights page over HTTP: one period, read for one reader's vault state, in pieces.

A small application carrying only this router and the two parts it reads (the database and the
permission-scoped repository), with the reader stood in for, because the vault's two facts
(whether it is open on this session, and how it conceals) are what is under test and the auth slice
proves how a session becomes them. The authz matrix calls the real route with real sessions.

The days are written into `insight_days` by hand, in the contracts' shape, with a split stamp no
User has reached, so what is tested is how the PAGE reads a split, not how the store works one out
(that is `test_store.py`'s).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel import wiring
from sift.kernel.access import Concealment, Repository, Role, Viewer
from sift.kernel.access.sentences import text_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.wiring import provide
from sift.kernel.workbench import Band, Preview, Summary, Workbench
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights import definitions, store
from sift.slices.insights import router as page
from sift.slices.insights import router_blocks as blocks
from sift.slices.insights import statements as st
from sift.slices.insights.store import local_today
from sift.slices.theming import CLOCK_KEY as REGISTERED_CLOCK_KEY
from sift.testing.fixtures import World, build_world, create_user, hide

pytestmark = pytest.mark.unit

HOUR = 3_600_000

#: A split stamp no User reaches in these tests, so the page reads the split exactly as written.
SETTLED = 10**12


@pytest.fixture
async def library(temp_db: Database, access: Repository) -> World:
    built = World(**{name: new_id() for name in World.__slots__})
    await build_world(temp_db, built)
    return built


@pytest.fixture
async def people(temp_db: Database, access: Repository) -> dict[str, Viewer]:
    return {
        "admin": await create_user(temp_db, Role.ADMIN),
        "guest": await create_user(temp_db, Role.GUEST),
    }


class Preferences:
    """The preference seam as the page reads it: the clock this test sets, and nothing else stored.

    Starts on the registered default, the twelve-hour clock, so a test that says nothing about it
    reads what a new install reads."""

    def __init__(self) -> None:
        self.clock = "12"

    async def get_app(self, key: str) -> Any:
        return None

    async def get_user(self, user_id: str, key: str) -> Any:
        return self.clock if key == page.CLOCK_KEY else None


@pytest.fixture
def preferences() -> Preferences:
    return Preferences()


class Pile:
    """An Organize pile as the board registers it; only what names its card is read here."""

    reversible = True
    purpose = None

    def __init__(self, name: str, title: str, group: str | None = None, lead: bool = False) -> None:
        self.name, self.title, self.group = name, title, group
        self.group_title = "Faces" if lead else None
        self.band = Band.DECISION

    async def available(self) -> bool:
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        raise AssertionError("the page names a card without counting its pile")

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()


@pytest.fixture
def board() -> Workbench:
    """The faces' card (a group, and the receipts it writes under a name that is no card) and
    the folders' card."""
    bench = Workbench()
    bench.register(Pile("suggestions", "Faces to confirm", "faces", lead=True))
    bench.register(Pile("to_name", "Unnamed faces", "faces"))
    bench.register(Pile("folders", "Folders to review"))
    identified = Pile("identified", "", "faces")
    bench.register_reverser(identified)
    return bench


@pytest.fixture
def app(
    temp_db: Database, access: Repository, preferences: Preferences, board: Workbench
) -> FastAPI:
    app = FastAPI()
    provide(app, wiring.DATABASE, temp_db)
    provide(app, wiring.ACCESS, access)
    provide(app, wiring.SETTINGS_HUB, preferences)
    provide(app, wiring.WORKBENCH, board)
    app.include_router(page.router, prefix="/api")
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        yield running


def as_(app: FastAPI, viewer: Viewer) -> None:
    app.dependency_overrides[current_viewer] = lambda: viewer


def a_closed_month() -> date:
    """A day in the month before this one: a closed period, every day of it added up."""
    return local_today().replace(day=1) - timedelta(days=20)


async def added_up(
    database: Database, user_id: str, day: date, rows: list[tuple[str, str, int, int]]
) -> None:
    """One day's rows for one User, as the helper would have written them, and the helper's
    progress past it."""
    async with database.write() as connection:
        for metric, key, whole, hidden in rows:
            await connection.execute(
                "INSERT INTO insight_days (user_id, day, metric, key, whole, hidden, split_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, day.isoformat(), metric, key, whole, hidden, SETTLED),
            )
        await connection.execute(
            "INSERT INTO insight_progress (user_id, added_up_to) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET added_up_to = excluded.added_up_to",
            (user_id, (local_today() - timedelta(days=1)).isoformat()),
        )


def a_month_of_viewing(person: str, *, sittings: int = 12) -> list[tuple[str, str, int, int]]:
    return [
        ("sittings", "", sittings, 0),
        ("sittings:kind", "video", sittings, 0),
        ("viewed_ms", "", 41 * HOUR, 0),
        ("viewed_ms:kind", "video", 29 * HOUR, 0),
        ("viewed_ms:kind", "image", 9 * HOUR, 0),
        ("viewed_ms:kind", "gif", 3 * HOUR, 0),
        ("viewed_ms:person", person, 6 * HOUR, 0),
        ("pickups", "", 12, 0),
        ("first_opened:person", person, 5, 0),
        ("decided", "", 6, 0),
        ("decided:queue", "identified", 3, 0),
        ("decided:queue", "suggestions", 1, 0),
        ("decided:queue", "folders", 2, 0),
        ("files_added", "", 4, 0),
        ("work_ms:family", "scan", HOUR, 0),
    ]


def said(line: list[dict[str, Any]]) -> str:
    return "".join(str(piece["lead"]) + str(piece["text"]) for piece in line)


def block(body: dict[str, Any], block_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(one for one in body["blocks"] if one["id"] == block_id)
    return found


async def test_the_page_is_its_periods_statements_in_pieces(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    day = a_closed_month()
    # A decision recorded under a pile this version no longer has: in the total, on no card.
    retired = [("decided:queue", "a-pile-since-retired", 1, 0)]
    await added_up(temp_db, people["admin"].id, day, a_month_of_viewing(library.person) + retired)
    as_(app, people["admin"])

    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()

    month = st.period_of("month", day, local_today(), None)
    assert (body["period"], body["from"], body["to"]) == (
        "month",
        month.start.isoformat(),
        month.end.isoformat(),
    )
    assert body["today_is_live"] is False
    assert said(body["first_sentences"][0]) == (
        f"You viewed 41 hours {st.when(month)}: 29 of videos, 9 of pictures, 3 of GIFs."
    )
    assert [one["id"] for one in body["blocks"]] == list(page.TITLES)
    most = block(body, "most_viewed")
    (person,) = [piece for piece in most["statements"][0] if piece["kind"]]
    assert (person["kind"], person["id"], person["text"]) == ("person", library.person, "person")
    (people_list, *_) = most["lists"]
    # The address its wall draws, with the token that lets the browser keep it (`naming`).
    assert people_list["rows"][0]["cover"].startswith(f"/api/people/{library.person}/cover?v=")
    assert said(block(body, "machine")["statements"][0]).startswith(
        "Sift worked on tasks for 1 hour"
    )
    # By kind is its files as figures and the time by kind as one bar of shares.
    by_kind = block(body, "by_kind")
    assert [one["label"] for one in by_kind["figures"]] == [
        "Files opened",
        "Files you came back to",
        "Photo Sets looked through",
    ]
    assert by_kind["chart"]["kind"] == "share"
    (whole,) = by_kind["chart"]["bars"]
    assert {part["kind"]: part["value"] for part in whole["parts"]} == {
        "video": 29 * HOUR,
        "image": 9 * HOUR,
        "gif": 3 * HOUR,
        "theater": 0,
    }
    # A month's heat-map is every day of it, the one with viewing carrying its time.
    days = block(body, "overview")["calendar"]["days"]
    assert [one["day"] for one in days][:1] == [month.start.isoformat()]
    assert len(days) == (month.end - month.start).days + 1
    assert {one["day"]: one["value"] for one in days}[day.isoformat()] == 41 * HOUR
    assert block(body, "organizing")["figures"][0]["label"] == "Questions answered"
    # And where they were answered: a card each, a group's receipts on its card.
    (by_card,) = block(body, "organizing")["lists"]
    assert [
        (row["piece"]["text"], row["piece"]["href"], row["value"]) for row in by_card["rows"]
    ] == [("Faces", "/organize/suggestions", 4), ("Folders to review", "/organize/folders", 2)]
    # A place with an address names its kind, so the client's one rule makes it a link.
    assert {row["piece"]["kind"] for row in by_card["rows"]} == {"place"}


async def test_a_hidden_person_is_passed_over_while_locked_and_only_placeholder_mode_says_so(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    """The September example: the most-viewed person is hidden. Locked in Leave-nothing mode the
    next person down is named with no hint; locked in placeholder mode the one line is said;
    unlocked she is named, and the figure carries its hidden part."""
    admin = people["admin"]
    hidden_one = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'someone', 'someone', 0)",
        (hidden_one,),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (library.twin, hidden_one)
    )
    await hide(temp_db, "person", hidden_one, admin.id)
    day = a_closed_month()
    rows = a_month_of_viewing(library.person)
    rows.remove(("viewed_ms", "", 41 * HOUR, 0))
    rows += [
        ("viewed_ms", "", 41 * HOUR, 8 * HOUR),
        ("viewed_ms:person", hidden_one, 8 * HOUR, 8 * HOUR),
    ]
    await added_up(temp_db, admin.id, day, rows)
    asked = {"period": "month", "at": day.isoformat()}

    def most_viewed(body: dict[str, Any]) -> str:
        return said(block(body, "most_viewed")["statements"][0])

    as_(app, admin)
    locked = (await client.get("/api/insights", params=asked)).json()
    assert "was person:" in most_viewed(locked)
    assert "someone" not in str(locked)
    assert not any("hidden" in said(line) for line in block(locked, "overview")["statements"])
    viewed = block(locked, "overview")["figures"][0]
    assert (viewed["value"], viewed["hidden_part"]) == (33 * HOUR, 0)

    as_(app, Viewer(id=admin.id, role=Role.ADMIN, concealment=Concealment.PLACEHOLDER))
    placeholder = (await client.get("/api/insights", params=asked)).json()
    month = st.period_of("month", day, local_today(), None)
    assert said(block(placeholder, "overview")["statements"][-1]) == text_of(st.some_hidden(month))
    assert "someone" not in str(placeholder)

    as_(app, Viewer(id=admin.id, role=Role.ADMIN, show_hidden=True))
    unlocked = (await client.get("/api/insights", params=asked)).json()
    assert "was someone:" in most_viewed(unlocked)
    viewed = block(unlocked, "overview")["figures"][0]
    assert (viewed["value"], viewed["hidden_part"]) == (41 * HOUR, 8 * HOUR)


async def test_a_guest_gets_their_own_page_and_never_what_sift_did(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    guest = people["guest"]
    as_(app, guest)
    empty = (await client.get("/api/insights")).json()
    assert [said(line) for line in empty["first_sentences"]] == [st.NOTHING_SHARED]

    day = a_closed_month()
    await added_up(temp_db, guest.id, day, a_month_of_viewing(library.person))
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()
    assert "machine" not in [one["id"] for one in body["blocks"]]
    assert "Sift worked" not in str(body)
    assert said(body["first_sentences"][0]).startswith("You viewed 41 hours")


async def test_below_the_floor_every_viewing_block_says_not_enough_and_nothing_else(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    day = a_closed_month()
    rows = a_month_of_viewing(library.person, sittings=9)  # one under the floor of 10
    await added_up(temp_db, people["admin"].id, day, rows)
    as_(app, people["admin"])
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()
    for block_id in page.VIEWING_BLOCKS:
        shown = block(body, block_id)
        assert shown["floor_reached"] is False, block_id
        assert [said(line) for line in shown["statements"]] == [st.NOT_ENOUGH], block_id
        assert shown["figures"] == [] and shown["lists"] == [] and shown["chart"] is None
    assert block(body, "alongside")["floor_reached"] is False
    # Organizing has its own floor, and 6 decisions pass it.
    assert block(body, "organizing")["floor_reached"] is True


async def test_a_month_is_compared_only_with_one_recorded_whole(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    """Rule 3. The month before was recorded from its 15th only: no comparison. Recorded from its
    first day: "That's 12 hours more than ..."."""
    admin = people["admin"]
    day = a_closed_month()
    month = st.period_of("month", day, local_today(), None)
    before = month.previous()
    assert before is not None
    await added_up(temp_db, admin.id, day, a_month_of_viewing(library.person))
    earlier = [("sittings", "", 12, 0), ("viewed_ms", "", 29 * HOUR, 0)]
    await added_up(temp_db, admin.id, before.start + timedelta(days=14), earlier)
    as_(app, admin)
    asked = {"period": "month", "at": day.isoformat()}

    partial = (await client.get("/api/insights", params=asked)).json()
    assert not any(
        said(line).startswith("That's") for line in block(partial, "overview")["statements"]
    )

    await added_up(temp_db, admin.id, before.start, [("files_added", "", 1, 0)])
    whole = (await client.get("/api/insights", params=asked)).json()
    that = [
        said(line)
        for line in block(whole, "overview")["statements"]
        if said(line).startswith("That's")
    ]
    assert that == [f"That's 12 hours more than {st.named_period(before)}."]


def every_block_of_a_month(library: World, wall: str) -> list[tuple[str, str, int, int]]:
    """A month with something in every block: each kind of thing named, the Theater, the minutes
    of the day, the opinions, and a task family the Activity screen no longer names."""
    return [
        *a_month_of_viewing(library.person),
        ("viewed_ms:site", library.site, 5 * HOUR, 0),
        ("viewed_ms:tag", library.tag, 4 * HOUR, 0),
        ("viewed_ms:collection", library.collection, 3 * HOUR, 0),
        ("viewed_ms:photo_set", library.photo_set, 2 * HOUR, 0),
        ("sittings:file", f"video:{library.solo}", 3, 0),
        # A file gone since it was counted: in no list, and still the picture it was, because the
        # key carries the kind the sitting wrote down.
        ("sittings:file", f"image:{new_id()}", 1, 0),
        # A day added up before the key carried a kind, about a file gone since: of no kind.
        ("sittings:file", new_id(), 1, 0),
        ("viewed_ms:kind", "theater", 5 * HOUR, 0),
        ("sittings:kind", "theater", 10, 0),
        ("theater_ms:wall", wall, 4 * HOUR, 0),
        ("earliest_start", "", 7 * 60 + 10, 0),
        ("latest_finish", "", 23 * 60, 0),
        ("viewed_ms:weekday", "4", 12 * HOUR, 0),
        ("rated", "", 3, 0),
        ("starred", "", 1, 0),
        ("starred:file", library.solo, 1, 0),
        ("o", "", 2, 0),
        ("o:file", library.solo, 2, 0),
        ("work_ms:family", "a-retired-family", HOUR, 0),
    ]


async def test_every_block_names_what_it_counted(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    admin = people["admin"]
    wall = new_id()
    await temp_db.execute(
        "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at, updated_at)"
        " VALUES (?, ?, 'Nine up', 'grid', 0, 0)",
        (wall, admin.id),
    )
    day = a_closed_month()
    await added_up(temp_db, admin.id, day, every_block_of_a_month(library, wall))
    as_(app, admin)
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()

    most = block(body, "most_viewed")
    # The person's line counts their files by kind: the one file of theirs viewed is a video.
    assert said(most["statements"][0]).endswith(": 6 hours and 1 video.")
    assert {one["title"]: said([one["rows"][0]["piece"]]) for one in most["lists"]} == {
        "People": "person",
        "Sites": "site",
        "Tags": "tag",
        "Collections": "collection",
        "Photo Sets": "photo set",
        "Files": "solo.mp4",
    }
    assert [said(line) for line in block(body, "by_kind")["statements"]] == [
        "1 video and 1 picture, across 12 sessions.",
        "1 file you came back to three times or more.",
        "You looked through 1 Photo Set.",
    ]
    assert said(block(body, "theater")["statements"][0]) == (
        "You spent 5 hours in Theater, mostly on your Saved Layout 'Nine up'."
    )
    when = [said(line) for line in block(body, "when")["statements"]]
    assert when[0].startswith("Your earliest start was 7:10 AM on ")
    assert "your latest finish 11:00 PM on " in when[0]
    assert when[1] == "You viewed the most on Fridays: 12 hours."
    opinions = block(body, "opinions")
    assert [one["title"] for one in opinions["lists"]] == ["Files you starred", "Most O presses"]
    # A count on a list says what it counts: a file's figure is its views, never a bare number.
    units = {one["title"]: one["rows"][0]["unit"] for one in most["lists"] + opinions["lists"]}
    assert units["Files"] == "views"
    assert units["People"] == "ms"
    assert units["Files you starred"] == "times"
    assert units["Most O presses"] == "presses"
    (tasks,) = block(body, "machine")["lists"]
    assert [row["piece"]["text"] for row in tasks["rows"]] == ["Other", "Scan"]


async def test_a_day_has_no_bars_and_a_year_has_one_a_month(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    people: dict[str, Viewer],
) -> None:
    admin = people["admin"]
    day = a_closed_month()
    await added_up(
        temp_db,
        admin.id,
        day,
        [
            ("sittings", "", 12, 0),
            ("viewed_ms", "", 3 * HOUR, 0),
            ("viewed_ms:kind", "video", 3 * HOUR, 0),
        ],
    )
    as_(app, admin)
    one_day = (
        await client.get("/api/insights", params={"period": "day", "at": day.isoformat()})
    ).json()
    # A day's chart is its hours; its When is the two times alone, so the hours are drawn once.
    hours = block(one_day, "overview")["chart"]
    # Each hour marked on the reader's clock, the twelve-hour one by default.
    marks = [bar["label"] for bar in hours["bars"]]
    assert marks[:3] == ["12 AM", "1 AM", "2 AM"]
    assert marks[12:14] == ["12 PM", "1 PM"]
    assert marks[-1] == "11 PM"
    assert block(one_day, "when")["chart"] is None
    assert block(one_day, "overview")["calendar"] is None
    # Nothing arrived: the first sentences are the viewing alone.
    assert [said(line) for line in one_day["first_sentences"]] == [
        said(block(one_day, "overview")["statements"][0])
    ]

    year = (
        await client.get("/api/insights", params={"period": "year", "at": day.isoformat()})
    ).json()
    bars = block(year, "overview")["chart"]["bars"]
    assert [bar["label"] for bar in bars] == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    ]  # fmt: skip
    (filled,) = [bar for bar in bars if any(part["value"] for part in bar["parts"])]
    assert filled["label"] == day.strftime("%b")
    assert {part["kind"]: part["value"] for part in filled["parts"]}["video"] == 3 * HOUR
    everything = (await client.get("/api/insights", params={"period": "all"})).json()
    assert block(everything, "overview")["chart"]["bars"][0]["label"] == day.strftime("%b %Y")
    # All is the whole record, so the month holding today is the last bar, still counting.
    whole = block(everything, "overview")["chart"]
    assert whole["today"] == len(whole["bars"]) - 1
    # And the library's growth: the files that arrived each month, over the whole record.
    growth = block(everything, "arrived")["chart"]
    assert growth["unit"] == "count"
    assert growth["bars"][0]["label"] == day.strftime("%b %Y")
    assert block(year, "arrived")["chart"] is None


async def test_a_closed_day_is_compared_with_your_daily_average_and_never_the_day_before(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    people: dict[str, Viewer],
) -> None:
    admin = people["admin"]
    day = a_closed_month()
    for back in range(1, 11):
        await added_up(
            temp_db,
            admin.id,
            day - timedelta(days=back),
            [("sittings", "", 2, 0), ("viewed_ms", "", HOUR, 0)],
        )
    await added_up(
        temp_db,
        admin.id,
        day,
        [
            ("sittings", "", 12, 0),
            ("viewed_ms", "", 3 * HOUR, 0),
            ("viewed_ms:hour", "22", HOUR, 0),
        ],
    )
    as_(app, admin)
    body = (
        await client.get("/api/insights", params={"period": "day", "at": day.isoformat()})
    ).json()
    overview = block(body, "overview")
    viewed = overview["figures"][0]
    assert said(viewed["caption"]) == "That's 2 hours more than your daily average."
    assert said(overview["statements"][1]) == said(viewed["caption"])
    assert said(overview["chart"]["caption"]) == "Your most-viewed hour began at 10:00 PM: 1 hour."

    # Today is still happening: it is never compared, with anything.
    today = (await client.get("/api/insights", params={"period": "day"})).json()
    assert block(today, "overview")["figures"] == [] or not any(
        "average" in said(one["caption"]) for one in block(today, "overview")["figures"]
    )


async def test_a_month_sift_is_still_counting_says_so(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    admin = people["admin"]
    day = a_closed_month()
    await added_up(temp_db, admin.id, day, a_month_of_viewing(library.person))
    # The helper had got only as far as that day: the rest of the month is not counted yet.
    await temp_db.execute(
        "UPDATE insight_progress SET added_up_to = ? WHERE user_id = ?",
        (day.isoformat(), admin.id),
    )
    as_(app, admin)
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()
    lines = [said(line) for line in block(body, "overview")["statements"]]
    assert text_of(st.still_counting()) in lines


async def test_a_period_is_not_compared_with_one_sift_is_still_counting(
    temp_db: Database, people: dict[str, Viewer]
) -> None:
    """Rule 3: the period before must be recorded whole AND reached by the helper. A long-ago
    month whose month before has not been added up is compared with nothing; added up, it is."""
    admin = people["admin"]
    today = local_today()
    month = st.period_of("month", date(today.year - 2, 6, 15), today, None)
    before = month.previous()
    assert before is not None
    book = page.Book.of(
        [store.DayRow(month.start.isoformat(), "viewed_ms", "", 3 * HOUR, 0)], locked=True
    )
    shown = page.Page(
        viewer=admin, period=month, book=book, figures=store.Figures((), (), ()), hours="12"
    )
    assert await blocks._compared(temp_db, shown, before.start) is None
    await added_up(
        temp_db, admin.id, before.start, [("sittings", "", 12, 0), ("viewed_ms", "", HOUR, 0)]
    )
    line = await blocks._compared(temp_db, shown, before.start)
    assert text_of(line or ()) == f"That's 2 hours more than {st.named_period(before)}."


async def test_a_day_is_compared_only_with_a_usual_day_recorded_whole_and_past_the_floor(
    temp_db: Database, people: dict[str, Viewer]
) -> None:
    """The average day needs a week of record behind it, every day of it added up, and enough
    sittings in it to be an average of something. Short of any of the three it says nothing,
    each asked where the other two would let a line through."""
    admin, guest = people["admin"], people["guest"]
    today = local_today()
    day = st.period_of("day", date(today.year - 2, 6, 15), today, None)
    book = page.Book.of(
        [store.DayRow(day.start.isoformat(), "viewed_ms", "", 3 * HOUR, 0)], locked=True
    )

    def shown(viewer: Viewer) -> page.Page:
        return page.Page(
            viewer=viewer, period=day, book=book, figures=store.Figures((), (), ()), hours="12"
        )

    enough = [("sittings", "", st.SITTINGS_FLOOR, 0), ("viewed_ms", "", HOUR, 0)]
    first = day.start - timedelta(days=20)
    # Today is still happening, and a record with no first day has nothing before it.
    still = page.Page(
        viewer=admin,
        period=st.period_of("day", today, today, None),
        book=book,
        figures=store.Figures((), (), ()),
        hours="12",
    )
    assert await blocks._usual(temp_db, still, first) is None
    assert await blocks._usual(temp_db, shown(admin), None) is None
    for back in (15, 2):
        await added_up(temp_db, admin.id, day.start - timedelta(days=back), enough)
    # Three days of record, every one added up and past the floor: not yet a week.
    assert await blocks._usual(temp_db, shown(admin), day.start - timedelta(days=3)) is None
    # A week and more, with the days after the tenth before it not added up yet.
    await temp_db.execute(
        "UPDATE insight_progress SET added_up_to = ? WHERE user_id = ?",
        ((day.start - timedelta(days=10)).isoformat(), admin.id),
    )
    assert await blocks._usual(temp_db, shown(admin), first) is None
    # Every day added up, and the sittings in them under the floor.
    below = [("sittings", "", st.SITTINGS_FLOOR - 1, 0), ("viewed_ms", "", HOUR, 0)]
    await added_up(temp_db, guest.id, day.start - timedelta(days=15), below)
    assert await blocks._usual(temp_db, shown(guest), first) is None


async def test_a_month_is_not_compared_with_one_under_the_floor(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    admin = people["admin"]
    day = a_closed_month()
    before = st.period_of("month", day, local_today(), None).previous()
    assert before is not None
    await added_up(
        temp_db,
        admin.id,
        before.start,
        [("sittings", "", st.SITTINGS_FLOOR - 1, 0), ("viewed_ms", "", HOUR, 0)],
    )
    await added_up(temp_db, admin.id, day, a_month_of_viewing(library.person))
    as_(app, admin)
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()
    assert not any(
        said(line).startswith("That's") for line in block(body, "overview")["statements"]
    )


async def test_somebody_who_has_viewed_nothing_yet_is_told_so(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    """A guest something IS shared with, and an admin, both with no sitting yet: nothing yet,
    rather than nothing shared."""
    guest = people["guest"]
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'item', ?, ?, 'share', 0)",
        (new_id(), library.solo, guest.id),
    )
    for viewer in (guest, people["admin"]):
        as_(app, viewer)
        body = (await client.get("/api/insights")).json()
        assert [said(line) for line in body["first_sentences"]] == [st.NOTHING_YET], viewer.role


async def test_a_time_of_day_is_said_on_the_readers_own_clock(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    people: dict[str, Viewer],
    preferences: Preferences,
) -> None:
    """The hour a reader viewed most, and the marks under the day's bars, on the clock they chose
    in Appearance: "22:00" and "22" on the twenty-four-hour one, "10:00 PM" and "10 PM" on the
    twelve-hour one."""
    admin = people["admin"]
    day = a_closed_month()
    await added_up(
        temp_db,
        admin.id,
        day,
        [("sittings", "", 12, 0), ("viewed_ms", "", HOUR, 0), ("viewed_ms:hour", "22", HOUR, 0)],
    )
    as_(app, admin)
    asked = {"period": "day", "at": day.isoformat()}

    preferences.clock = "24"
    chart = block((await client.get("/api/insights", params=asked)).json(), "overview")["chart"]
    assert said(chart["caption"]) == "Your most-viewed hour began at 22:00: 1 hour."
    assert [bar["label"] for bar in chart["bars"]][22] == "22"

    preferences.clock = "12"
    chart = block((await client.get("/api/insights", params=asked)).json(), "overview")["chart"]
    assert said(chart["caption"]) == "Your most-viewed hour began at 10:00 PM: 1 hour."
    assert [bar["label"] for bar in chart["bars"]][22] == "10 PM"


def test_the_clock_is_read_under_the_key_the_appearance_settings_register() -> None:
    assert page.CLOCK_KEY == REGISTERED_CLOCK_KEY


def test_a_chart_of_nothing_names_no_tallest_bar() -> None:
    """Every bar at nothing: there is no biggest day to say."""
    today = date(2026, 9, 17)
    chart = blocks._overview_chart(
        st.period_of("week", today, today, None), page.Book.of([], locked=True), "12"
    )
    assert chart is not None and len(chart.bars) == 7
    assert chart.caption == []


def test_an_open_period_draws_every_bar_it_will_have() -> None:
    """The week, the month and the year still happening: every bar of the period, the days to come
    at nothing, so the chart has one shape from the period's first day to its last."""
    today = date(2026, 9, 17)
    book = page.Book.of(
        [store.DayRow(today.isoformat(), "viewed_ms:kind", "video", HOUR, 0)], locked=True
    )
    for span, count in (("week", 7), ("month", 30), ("year", 12)):
        chart = blocks._overview_chart(st.period_of(span, today, today, None), book, "12")
        assert chart is not None and chart.today is not None, span
        assert len(chart.bars) == count, span
        after = chart.bars[chart.today + 1 :]
        assert after and all(sum(part.value for part in bar.parts) == 0 for bar in after), span


async def test_the_songs_viewed_longest_are_listed_under_the_pages_name_music(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    library: World,
    people: dict[str, Viewer],
) -> None:
    """Every list of Most viewed is titled with its page's name, and the songs' page is Music."""
    admin = people["admin"]
    song = new_id()
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'Harbour Lights', 'h', 0)",
        (song,),
    )
    day = a_closed_month()
    await added_up(
        temp_db,
        admin.id,
        day,
        [*a_month_of_viewing(library.person), ("viewed_ms:song", song, 3 * HOUR, 0)],
    )
    as_(app, admin)
    body = (
        await client.get("/api/insights", params={"period": "month", "at": day.isoformat()})
    ).json()
    titled = {
        one["title"]: said([one["rows"][0]["piece"]]) for one in block(body, "most_viewed")["lists"]
    }
    assert titled["Music"] == "Harbour Lights"


async def test_a_visit_is_reported_for_whoever_reports_it_and_cleared_by_them_alone(
    app: FastAPI,
    client: httpx.AsyncClient,
    temp_db: Database,
    people: dict[str, Viewer],
    preferences: Preferences,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client reports the pages it had in front; the visit is written for the signed-in User
    with the device the request came from, and a clear takes only the presser's own history."""
    from sift.kernel.client import CLIENT_HEADER, DEVICE_COOKIE_NAME
    from sift.kernel.use_history import RECORD_KEY
    from sift.slices.insights import capture_router

    async def keeps(user_id: str, key: str) -> Any:
        return True if key == RECORD_KEY else None

    monkeypatch.setattr(preferences, "get_user", keeps)
    app.include_router(capture_router.router, prefix="/api")
    app.dependency_overrides[csrf_protect] = lambda: None
    visit = {
        "id": "a-visit-of-sixteen-x",
        "place": "wall",
        "ref": "library",
        "opened_ago_ms": 60_000,
        "last_ago_ms": 0,
        "front_ms": 30_000,
    }
    client.cookies.set(DEVICE_COOKIE_NAME, "a-phone-of-sixteen")
    for viewer in people.values():
        as_(app, viewer)
        answer = await client.post(
            "/api/insights/visits",
            json={"visits": [{**visit, "id": f"{visit['id']}-{viewer.role}"}]},
            headers={CLIENT_HEADER: "phone"},
        )
        assert answer.json() == {"kept": 1}

    as_(app, people["guest"])
    assert (await client.delete("/api/insights/history")).status_code == 204

    rows = await temp_db.fetch_all("SELECT user_id, device_id, client_kind FROM page_visits")
    assert [tuple(row) for row in rows] == [(people["admin"].id, "a-phone-of-sixteen", "phone")]


async def test_alongside_says_two_measures_rose_together_with_its_sample(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, people: dict[str, Viewer]
) -> None:
    """Ten days of a closed month on which Theater time and starring rose together: one sentence,
    with its sample; the week inside it is under the floor of eight periods."""
    month = a_closed_month().replace(day=1)
    for n in range(1, 11):
        await added_up(
            temp_db,
            people["admin"].id,
            month + timedelta(days=n),
            [
                ("sittings", "", 10 + n % 3, 0),
                ("viewed_ms", "", n * HOUR, 0),
                ("viewed_ms:kind", "theater", n * HOUR, 0),
                ("sittings:kind", "theater", 1, 0),
                ("starred", "", n, 0),
            ],
        )
    as_(app, people["admin"])

    body = (
        await client.get("/api/insights", params={"period": "month", "at": month.isoformat()})
    ).json()
    alongside = block(body, "alongside")
    assert alongside["title"] == "Alongside"
    assert [said(line) for line in alongside["statements"]] == [
        "Over 10 days, the days you viewed Theater most were the days you starred the most files."
    ]
    # Two figures a sentence, the first measure's then the second's, each with its definition.
    assert [(one["label"], one["unit"]) for one in alongside["figures"]] == [
        ("In Theater", "ms"),
        ("Starred", "count"),
    ]
    assert all(one["value"] > 0 and one["defines"] for one in alongside["figures"])
    theater = block(body, "theater")
    defines = {one["label"]: said(one["defines"]) for one in theater["figures"]}
    assert defines["Visits"] == text_of(definitions.definition("Theater visits") or ())
    assert defines["In Theater"].startswith("Each hour a Theater wall played counts once")

    week = (
        await client.get(
            "/api/insights",
            params={"period": "week", "at": (month + timedelta(days=3)).isoformat()},
        )
    ).json()
    assert [said(line) for line in block(week, "alongside")["statements"]] == [st.NOT_ENOUGH]
    at = (month + timedelta(days=3)).isoformat()
    day = (await client.get("/api/insights", params={"period": "day", "at": at})).json()
    assert block(day, "overview")["floor_reached"] is True
    assert "alongside" not in [one["id"] for one in day["blocks"]]
