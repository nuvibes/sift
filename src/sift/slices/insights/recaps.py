# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recaps: a day, a week, a month or a year, frozen as a handful of cards the day after it closes.

What a recap freezes and what is decided when it is drawn is `recaps_draw`'s head.

## When a recap is made

The quiet helper calls `make_due` after it has added up a day. A period's recap is due once the
period has closed and its last day is added up, and only for the period immediately before the one
today falls in: yesterday, last week on a Monday, last month on the 1st, last year on 1 January
(`due`), each kind only while its switch is on (`kinds_on`). It is made only where the period
passes the floor. A device that was off on Monday makes last week's
on Tuesday. A device off for the whole of the next week does NOT make the week before last: "Your
week" is about the week just gone, and a flood of old weeks on the first run after an install is not
a recap of anything anybody remembers.

A period already made is never made again: `make_due` skips a period that has a row, and the table
holds one row per User and period besides. The one exception is a correction: a recap made by a
statement a later version corrected is made again, once, from the days added up again
(`remake_behind`).

## Never a notification

A recap is announced by one card at the top of Insights and one quiet line on Browse's header, for a
week (a day's for a day), the longest period first, inside Sift and nowhere else (`announced`). Opening it or pressing the cross ends that. It is
never a share: Sift sends a recap nowhere, and no button posts a card or puts it on another device.
A card may be kept by the one person reading it, as a picture or the deck as a video, through the
one door every screenshot takes (`deliver`), and only a card that is not a locked tile, names
nothing hidden and has something to say (the client's `savable`).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import date

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import capitalized, said, text_of
from sift.kernel.db import Connection, Database, in_clause
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_INSIGHTS, Subject
from sift.slices.insights import statements, store
from sift.slices.insights.recaps_cards import (
    BUILDERS,
)
from sift.slices.insights.recaps_draw import (
    _head,
    _named_as,
    _open_all,
    kept_cards,
)
from sift.slices.insights.recaps_models import (
    Recap,
    RecapHead,
)
from sift.slices.insights.recaps_periods import (
    ANNOUNCED_DAYS,
    ANNOUNCED_FOR_DAYS,
    FLOOR_SITTINGS,
    LONGEST_FIRST,
    PREV,
    SETTING_KEYS,
    USUAL,
    Period,
    PeriodKind,
    due,
    period_from_key,
    period_of,
    source,
    title_of,
)
from sift.slices.insights.recaps_recipes import (
    DECK_MOST,
    DECKS,
    body_of,
    build,
)
from sift.slices.insights.store import RecapRow

log = get_logger(__name__)

__all__ = [
    "ANNOUNCED_DAYS",
    "ANNOUNCED_FOR_DAYS",
    "BUILDERS",
    "DECKS",
    "DECK_MOST",
    "FLOOR_SITTINGS",
    "LONGEST_FIRST",
    "PREV",
    "SETTING_KEYS",
    "USUAL",
    "Period",
    "PeriodKind",
    "announced",
    "body_of",
    "build",
    "dismissed",
    "due",
    "heads",
    "kept_cards",
    "kinds_on",
    "make_due",
    "opened",
    "period_from_key",
    "period_of",
    "remake_behind",
    "source",
    "title_of",
]


_SWITCHES = "SELECT key, value FROM user_settings WHERE user_id = ? AND key IN (?*)"


async def kinds_on(database: Database, user_id: str) -> list[PeriodKind]:
    """The kinds of recap this User has not turned off (`SETTING_KEYS`, each on by default). Read
    here rather than through the settings service because the quiet helper has none; a stored
    value that is not exactly false reads as the default."""
    sql, keys = in_clause(_SWITCHES, list(SETTING_KEYS.values()))
    rows = await database.fetch_all(sql, [user_id, *keys])
    off = {str(row["key"]) for row in rows if _is_false(str(row["value"]))}
    return [kind for kind, key in SETTING_KEYS.items() if key not in off]


def _is_false(stored: str) -> bool:
    try:
        return json.loads(stored) is False
    except ValueError:
        return False


async def make_due(database: Database, user_id: str, today: date) -> list[RecapRow]:
    """Make every recap due for this User today, and answer the ones made.

    THE ONE CALL the quiet helper makes, after it has added a day up for this User. Idempotent: a
    period that has a recap is skipped before anything is read, and the write keeps the first row
    for a period whatever happens. A period below the floor makes nothing, and is asked again the
    next day while it is still the one just closed.
    """
    reached = await store.added_up_to(database, user_id)
    await remake_behind(database, user_id, today, reached)
    periods = due(today, reached, await kinds_on(database, user_id))
    if not periods:
        return []
    made_before = {row.period for row in await store.recaps_of(database, user_id)}
    made: list[RecapRow] = []
    for period in periods:
        if period.key in made_before:
            continue
        cards = await build(database, user_id, period, today=today)
        if cards is None:
            continue
        made.append(await store.write_recap(database, user_id, period.key, body_of(cards)))
        log.info("insights.recap_made", period=period.key, cards=len(cards))
    return made


def _recounted(recap_id: str, period: Period) -> Callable[[Connection], Awaitable[object]]:
    """The History line a recap counted again writes: "Sift re-counted your September 2026 recap
    after a correction.", on the write that changed it."""

    async def record(connection: Connection) -> object:
        return await record_event(
            connection,
            actor=Actor.sift(VIA_INSIGHTS),
            verb="recounted",
            subject=Subject("recap", recap_id, f"{period.span} recap"),
        )

    return record


async def remake_behind(
    database: Database, user_id: str, today: date, reached: date | None
) -> list[str]:
    """Make again, once, every recap made by statements a later version corrected, and answer the
    periods made again.

    A recap is frozen against what happens later, never against a count that was wrong: a statement
    corrected in a version step makes the recaps it reached wrong too. Each is made from the days
    added up again, so only once its last day has been, and keeps its id, when it was made and
    whether it was opened, so a correction neither moves it in the list nor announces it again. An
    achievement is what it was when it was reached, and stays.
    """
    remade: list[str] = []
    for row in await store.recaps_behind(database, user_id):
        period = period_from_key(row.period)
        if period is None or reached is None or period.last > reached:
            continue
        cards = await build(database, user_id, period, today=today, floor=False) or []
        # Only a row still behind is written, so two helpers racing make it once.
        if await store.remake_recap(  # pragma: no branch
            database,
            user_id,
            row.id,
            body_of(cards),
            made_at=row.made_at,
            then=_recounted(row.id, period),
        ):
            remade.append(row.period)
    return remade


async def heads(
    database: Database, viewer: Viewer, *, today: date | None = None, now: float | None = None
) -> tuple[list[RecapHead], RecapHead | None]:
    """Every recap of a period this reader has, newest first, and the one being announced.

    Each is drawn for the reader's vault state now, so a recap a locked vault leaves out is neither
    listed nor announced, and each head's card count is the count they will be shown.
    """
    on = today or store.local_today(now)
    rows = [
        row
        for row in await store.recaps_of(database, viewer.id)
        if period_from_key(row.period) is not None
    ]
    drawn = await _open_all(database, viewer, rows, on, only_counting=True)
    listed = [_head(one, on) for one in drawn]
    return listed, announced(listed, now=time.time() if now is None else now)


def announced(listed: Sequence[RecapHead], *, now: float) -> RecapHead | None:
    """THE ANNOUNCEMENT: of the recaps neither opened nor dismissed and still within their kind's
    days (`ANNOUNCED_DAYS`: a day's for one day, the rest for a week), the longest period's, the
    newest of those.

    Opening a recap and pressing the cross both draw its `seen_at`, and either ends this. Only ever
    one: a second card announcing a second recap is a queue of chores, which a recap is not.
    """

    def kind(head: RecapHead) -> PeriodKind:
        return PeriodKind(head.period.partition(":")[0])

    fresh = [
        head
        for head in listed
        if head.seen_at is None and head.made_at >= now - ANNOUNCED_DAYS[kind(head)] * 24 * 3600
    ]
    return max(
        fresh,
        key=lambda head: (-LONGEST_FIRST.index(kind(head)), head.made_at, head.id),
        default=None,
    )


async def opened(
    database: Database,
    viewer: Viewer,
    recap_id: str,
    *,
    today: date | None = None,
    hours: statements.Clock = "12",
) -> Recap | None:
    """One of this reader's recaps, drawn for them now on their clock, and marked seen the first
    time.

    None (a 404) for somebody else's, for one that is not there, and for one a locked vault
    leaves out: the three are the same answer, so the answer says nothing about which it was.
    """
    on = today or store.local_today()
    row = await store.recap(database, viewer.id, recap_id)
    if row is None:
        return None
    found = await _open_all(database, viewer, [row], on, hours=hours)
    if not found:
        return None
    one = found[0]
    await store.mark_seen(database, viewer.id, recap_id)
    title, span = _named_as(one, on)
    count = len(one.drawn.cards)
    cards = "1 card" if count == 1 else f"{count} cards"
    # An achievement is one card and is headed by what it is called; a period by its cards.
    heading = (
        text_of(capitalized(said(f"{statements.named_period(one.period.said_on(on))}, in {cards}")))
        if one.period is not None
        else title
    )
    line = (
        text_of(statements.some_hidden(one.period.said_on(on)))
        if one.drawn.something_hidden and one.period is not None
        else None
    )
    return Recap(
        id=row.id,
        period=row.period,
        title=title,
        span=span,
        heading=heading,
        made_at=row.made_at,
        cards=one.drawn.cards,
        hidden_line=line,
        first_day="" if one.period is None else one.period.first.isoformat(),
    )


async def dismissed(database: Database, viewer: Viewer, recap_id: str) -> bool:
    """The cross on the announcement: the recap stops being announced.

    False (a 404) for somebody else's recap and for one that is not there for this reader now.
    The table has one column for "this recap has been looked at", and dismissing is that: it is what
    ends the announcement, and a dismissed recap is still in the list to be opened.
    """
    row = await store.recap(database, viewer.id, recap_id)
    if row is None:
        return False
    if not await _open_all(database, viewer, [row], store.local_today()):
        return False
    await store.mark_seen(database, viewer.id, recap_id)
    return True
