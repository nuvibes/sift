# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap drawn for its reader: the vault's rules for each card, and the recaps opened for the
list and the deck.

## What is frozen, and what is decided when it is drawn

A recap is a snapshot of how a period looked when it closed. A cleanup in February does not
rewrite December: the figures a card reads are kept WHOLE in the card's recipe, counted over
everything the User could see, hidden things included, and they never change again. So is the
choice of who the top person was.

What is hidden is NOT frozen. Locking belongs to one browser session and the helper that makes a
recap has none, so there is no such thing as a recap "made while locked": a recap is made whole and
drawn for whoever opens it, in their vault state right now. Hiding a person in November hides them
from September's recap while the vault is locked. The rules, all in `_draw`:

  * A card that NAMES a thing hidden for the reader now is absent in Show nothing mode (no
    substitute, no hint) and a locked tile in placeholder mode. There is no next person down:
    choosing one would be a second recap made at draw time, and the recap is the frozen one.
  * A card whose figures carry a hidden part is said with that part taken out: 41 hours unlocked,
    35 locked. The hidden part is worked out when the recap is drawn, from the period's rows
    (`store.rows` re-splits a row whose split is older than the User's stamp), so hiding something
    after the recap was made moves it too.
  * A card left with nothing to say once the hidden part is out is absent. The O card is the one
    that rule was written for: locked, it counts presses on files that are not hidden, and when that
    is zero it is left out, as it is whenever the count is simply zero, so its absence reveals
    nothing. The whole recap follows the same rule: locked, and below the floor on what is not
    hidden, it is not there at all in Show nothing mode.

The WORDS are not frozen either. A card is said when it is drawn, from its recipe, by the same
builders the Insights page uses (`statements.py`): "last week" is true only for the seven days after
a week closed, and a recap is opened again long after.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from io import BytesIO
from typing import cast

from pydantic import TypeAdapter, ValidationError

from sift.kernel.access import Viewer, arrivals
from sift.kernel.access.sentences import said
from sift.kernel.access.viewer import Concealment
from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.kernel.wire import pieces_of
from sift.slices.insights import statements, store
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.models import Bar, BarPart, Chart, NamedRow, cover_of
from sift.slices.insights.path import achievement_head
from sift.slices.insights.recaps_cards import (
    BUILDERS,
    FILE_METRICS,
    Said,
    _figure_names,
    _said,
    _spans,
    span_of,
)
from sift.slices.insights.recaps_models import (
    CardKind,
    KeptCard,
    NamedThing,
    RecapCard,
    RecapHead,
    Recipe,
)
from sift.slices.insights.recaps_periods import (
    FLOOR_SITTINGS,
    PREV,
    USUAL,
    Period,
    _parts,
    _usual_window,
    period_from_key,
    source,
    title_of,
)
from sift.slices.insights.recaps_recipes import (
    _READS,
    FILES_COUNTED,
    _Totals,
    _totals,
)
from sift.slices.insights.store import RecapRow

log = get_logger(__name__)

_CARDS: TypeAdapter[list[KeptCard]] = TypeAdapter(list[KeptCard])


def kept_cards(body: str) -> list[KeptCard]:
    """The cards a recap keeps. A body that does not read as cards draws as none, and says so."""
    try:
        return _CARDS.validate_json(body)
    except ValidationError:
        log.warning("insights.recap_unreadable")
        return []


@dataclass(frozen=True, slots=True)
class _Drawn:
    cards: list[RecapCard]
    #: Placeholder mode, locked, and something here is hidden.
    something_hidden: bool


def _stub(kind: str) -> RecapCard:
    """A locked tile: the card is there, and nothing it says is."""
    return RecapCard(id=kind, kind=cast(CardKind, kind), statement=[], hidden=True)


def _draw(
    cards: Sequence[KeptCard],
    period: Period | None,
    viewer: Viewer,
    hidden: Mapping[str, int],
    today: date,
    left_out: frozenset[str] = frozenset(),
) -> _Drawn | None:
    """THE VAULT RULE FOR A RECAP: the cards this reader may be shown now, or None for a recap that
    is not there at all for them. `hidden` is the hidden part of every source, worked out now.

    `left_out` is what the reader took out of this recap before sharing it (a person, a file): a
    card naming one is absent whatever the vault, as in Show nothing mode, and the cards said
    around a thing (`_SAID_AROUND`) are said without it.

    A card with no recipe (an achievement) is drawn as it was built: it names nothing the vault
    decides, and it carries no figure with a hidden part.
    """
    locked = not viewer.show_hidden
    placeholder = viewer.concealment is Concealment.PLACEHOLDER

    def shown(name: str, whole: int) -> int:
        return whole - min(hidden.get(name, 0), whole) if locked else whole

    below_floor = locked and period is not None and _short(cards, shown)
    if below_floor and not placeholder:
        return None

    out: list[RecapCard] = []
    something_hidden = below_floor
    for card in cards:
        recipe = card.recipe
        if recipe is None or period is None:
            out.append(RecapCard(**card.model_dump(exclude=_KEPT_ONLY)))
            continue
        # A named thing is hidden for this reader now when all of its figure in the period is:
        # the store's split says so for a hidden person or Site, for one whose every file is, and
        # for a hidden file.
        gone_now = [
            one.id
            for one in recipe.named
            if any(
                (whole := recipe.sources.get(name, 0)) > 0 and hidden.get(name, 0) >= whole
                for name in _naming(one, recipe)
            )
        ]
        # The first-and-last card chooses among its files when it is said (`_first_last`), and
        # the closing card, which every recap keeps, leaves out a figure naming a hidden thing.
        naming = gone_now if card.kind not in _SAID_AROUND else []
        taken = _taken_out(recipe, left_out)
        if taken and card.kind not in _SAID_AROUND:
            continue
        if locked and (naming or (below_floor and card.kind != "closing")):
            something_hidden = True
            _leave_stub(out, card.kind, placeholder)
            continue
        figures = {name: shown(name, whole) for name, whole in recipe.sources.items()}
        figures.update(dict.fromkeys(taken, 0))
        if locked and any(hidden.get(name, 0) for name in recipe.sources):
            something_hidden = True
        drawn = _spoken(card.kind, figures, recipe, period, today, {} if locked else hidden)
        if drawn is None:
            # Nothing true left to say once the hidden part is out. The O card is absent in both
            # modes; any other card is a locked tile in placeholder mode, so that mode's gap is
            # where the card was.
            if locked:
                _leave_stub(out, card.kind, placeholder)
            continue
        drawn.hidden_things = [] if locked else gone_now
        out.append(drawn)
    _top_picture(out)
    return _Drawn(out, something_hidden and locked and placeholder)


def _leave_stub(out: list[RecapCard], kind: str, placeholder: bool) -> None:
    """A locked tile where the card was, in placeholder mode; the O card is never one."""
    if placeholder and kind != "o":
        out.append(_stub(kind))


#: The cards whose first picture is the period's most viewed file, in the order they are asked.
_TOP_FILE = ("mosaic", "top_file")


def _top_picture(cards: list[RecapCard]) -> None:
    """The closing card's picture: the most viewed file's, as a card this reader is shown has it,
    and none where that card names something hidden."""
    closing = next((card for card in cards if card.kind == "closing" and card.figures), None)
    shown = {card.kind: card for card in cards if not card.hidden and not card.hidden_things}
    top = next((shown[kind] for kind in _TOP_FILE if kind in shown), None)
    if closing is not None and top is not None:
        closing.cover = top.rows[0].cover if top.rows else top.cover


def _spoken(
    kind: str,
    figures: Mapping[str, int],
    recipe: Recipe,
    period: Period,
    today: date,
    hidden: Mapping[str, int],
) -> RecapCard | None:
    """A card said from its figures now, each figure's hidden part on it (`hidden` empty while
    locked), and the cover card's shares by kind; None when there is nothing true to say."""
    words = BUILDERS[kind](figures, recipe, period, today)
    drawn = _said(kind, words, figures)
    if drawn is None or words is None:
        return None
    _hidden_figures(drawn, words, hidden)
    if kind == "headline":
        drawn.chart = _by_kind(figures)
    return drawn


def _hidden_figures(drawn: RecapCard, words: Said, hidden: Mapping[str, int]) -> None:
    """Each figure's hidden part, for an open vault: nothing while locked (`hidden` empty)."""
    if drawn.figure is not None:
        drawn.figure.hidden_part = sum(hidden.get(name, 0) for name in _figure_names(words))
    for figure, cell in zip(drawn.figures, words.cells, strict=True):
        figure.hidden_part = hidden.get(cell.figure, 0)


def _short(cards: Sequence[KeptCard], shown: Callable[[str, int], int]) -> bool:
    """Whether the visits this reader may be told of fall under the floor a recap needs."""
    sittings = next(
        (
            card.recipe.sources[source("sittings")]
            for card in cards
            if card.kind == "closing"
            and card.recipe is not None
            and source("sittings") in card.recipe.sources
        ),
        None,
    )
    return sittings is not None and shown(source("sittings"), sittings) < FLOOR_SITTINGS


#: What a kept card holds that a reader is never sent as it was kept.
_KEPT_ONLY = {"recipe", "hidden_things", "accent_hue", "accent_of"}

#: The cards that leave out what they may not say rather than being locked whole: the Theater
#: wall leaves the cell of such a file empty.
_SAID_AROUND = frozenset({"first_last", "closing", "theater_files"})


def _taken_out(recipe: Recipe, left_out: frozenset[str]) -> set[str]:
    """The sources of the things this card names that the reader took out of the recap."""
    return {name for one in recipe.named if one.id in left_out for name in _naming(one, recipe)}


def _by_kind(figures: Mapping[str, int]) -> Chart | None:
    """The time viewed by kind, as the cover card's one bar of shares; None for a card whose
    recipe keeps no split."""
    parts = [
        BarPart(kind=kind, value=figures.get(source("viewed_ms:kind", kind), 0))
        for kind in (*statements.KINDS, statements.THEATER)
    ]
    if sum(1 for part in parts if part.value > 0) < 2:
        return None
    return Chart(kind="share", unit="ms", bars=[Bar(label="Viewed", parts=parts)])


#: A pixel greyer than this (OKLCH chroma) has no colour to give a card.
_GREY = 0.04
#: The share of a still's pixels that must have a colour for it to give one.
_COLOURED = 0.08


def _oklab(red: int, green: int, blue: int) -> tuple[float, float, float]:
    """An sRGB pixel in OKLab: lightness, then the two axes of colour."""

    def linear(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = linear(red), linear(green), linear(blue)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)  # noqa: E741
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


async def still_hue(content: ContentStore, asset_id: str) -> int | None:
    """The hue of a file's thumbnail, read through the cache's own confinement; None where it has
    none on disk."""
    thumb = next(
        (one for one in await content.derivatives(asset_id) if one.kind is DerivativeKind.THUMB),
        None,
    )
    path = None if thumb is None else await content.derivative_at(thumb.rel_cache_path)
    if path is None:
        return None
    try:
        still = await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None
    return await asyncio.to_thread(accent_hue, still)


def accent_hue(still: bytes) -> int | None:
    """The hue a still is mostly, in OKLCH degrees, for the card it heads to take its colour
    from: each coloured pixel pulls towards its hue by how coloured it is. None for a still that
    is mostly grey, or that does not open as a picture."""
    # Here and not at the top: the picture library is never loaded while the server starts.
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(still)) as opened:
            raw = opened.convert("RGB").resize((24, 24)).tobytes()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    across = down = 0.0
    coloured = 0
    pixels = [tuple(raw[at : at + 3]) for at in range(0, len(raw), 3)]
    for red, green, blue in pixels:
        lightness, a, b = _oklab(red, green, blue)
        chroma = math.hypot(a, b)
        if chroma < _GREY or not 0.15 < lightness < 0.95:
            continue
        coloured += 1
        across += a
        down += b
    if coloured < _COLOURED * len(pixels):
        return None
    return round(math.degrees(math.atan2(down, across))) % 360


def _naming(one: NamedThing, recipe: Recipe) -> list[str]:
    """The sources that say whether a thing a card names is hidden: a person's, a Site's, a
    tag's or a song's time, or the rows a file is counted in."""
    if one.kind != "asset":
        return [source(f"viewed_ms:{one.kind}", one.id)]
    return [
        name
        for name in recipe.sources
        if (parts := _parts(name))[1] in FILE_METRICS and split_file_key(parts[2])[1] == one.id
    ]


def _sources_of(cards: Sequence[KeptCard]) -> set[str]:
    return {name for card in cards if card.recipe is not None for name in card.recipe.sources}


async def _hidden_parts(
    database: Database, user_id: str, recaps: Sequence[tuple[Period, Sequence[KeptCard]]]
) -> list[dict[str, int]]:
    """The hidden part of every source of every recap, NOW, from one read of the rows they span.

    One read for the lot rather than one per recap: a year and the months and weeks inside it
    share their days, so a read per recap would read most days several times. The period before a
    recap's is read only where one of its cards compares with it.
    """
    if not recaps:
        return []
    metrics = {_parts(name)[1] for _, cards in recaps for name in _sources_of(cards)} & _READS
    metrics |= {FILES_COUNTED[metric] for metric in metrics if metric in FILES_COUNTED}
    if not metrics:
        return [{} for _ in recaps]

    def windows(period: Period, cards: Sequence[KeptCard]) -> dict[str, tuple[date, date]]:
        """The days each scope of these cards' sources was read over."""
        before = period.previous()
        spans = {"": (period.first, period.last), PREV: (before.first, before.last)}
        spans[USUAL] = _usual_window(period)
        scopes = {_parts(name)[0] for name in _sources_of(cards)} | {""}
        return {scope: spans[scope] for scope in scopes}

    spans = [windows(period, cards) for period, cards in recaps]
    first = min(window[0] for span in spans for window in span.values())
    last = max(window[1] for span in spans for window in span.values())
    rows = await _totals(database, user_id, first, last, metrics)
    out: list[dict[str, int]] = []
    for (_, cards), span in zip(recaps, spans, strict=True):
        read = {scope: rows.of(*window) for scope, window in span.items()}
        parts: dict[str, int] = {}
        for name in _sources_of(cards):
            scope, metric, key = _parts(name)
            parts[name] = read[scope].get((metric, key), (0, 0))[1]
        parts.update(_within(rows, _sources_of(cards)))
        out.append(parts)
    return out


def _within(rows: _Totals, names: Iterable[str]) -> dict[str, int]:
    """The hidden part of each source read over part of its period (a day, a month), from one
    walk of the rows."""
    wanted: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for name in names:
        _, metric, key = _parts(name)
        span = span_of(key)
        if span is not None:
            base, first, last = span
            wanted.setdefault((metric, base), []).append(
                (name, first.isoformat(), last.isoformat())
            )
    out = {name: 0 for found in wanted.values() for name, _, _ in found}
    for row in rows.rows:
        for name, low, high in wanted.get((row.metric, row.key), ()):
            if low <= row.day <= high:
                out[name] += row.hidden
    return out


@dataclass(frozen=True, slots=True)
class _Opened:
    row: RecapRow
    period: Period | None
    drawn: _Drawn


def _named_as(opened: _Opened, today: date) -> tuple[str, str]:
    """(title, span) of a recap: a period's from its days, an achievement's from its learning
    path."""
    if opened.period is not None:
        return title_of(opened.period, today), opened.period.span
    head = achievement_head(opened.row)
    return (head.title, head.span) if head is not None else ("", "")


def _head(opened: _Opened, today: date) -> RecapHead:
    title, span = _named_as(opened, today)
    return RecapHead(
        id=opened.row.id,
        period=opened.row.period,
        title=title,
        span=span,
        made_at=opened.row.made_at,
        seen_at=opened.row.seen_at,
        cards=len(opened.drawn.cards),
    )


async def _open_all(
    database: Database,
    viewer: Viewer,
    rows: Sequence[RecapRow],
    today: date,
    *,
    only_counting: bool = False,
    hours: statements.Clock = "12",
) -> list[_Opened]:
    """These recaps as this reader may be shown them now, the ones not there for them left out.

    `only_counting` is the list's case: an open vault is shown every card as it was made, so where
    all that is wanted is how many there are, an open vault's reader costs no read of the rows.
    """
    kept = [
        (row, _on_clock(period_from_key(row.period), hours), kept_cards(row.body)) for row in rows
    ]
    periods = [(period, cards) for _, period, cards in kept if period is not None]
    found: list[dict[str, int]] = (
        [{} for _ in periods]
        if only_counting and viewer.show_hidden
        else await _hidden_parts(database, viewer.id, periods)
    )
    parts = iter(found)
    out: list[_Opened] = []
    for row, period, cards in kept:
        hidden = next(parts) if period is not None else {}
        drawn = _draw(cards, period, viewer, hidden, today, frozenset(row.left_out))
        if drawn is not None:
            if not only_counting:
                await _month_pictures(database, viewer.id, cards, drawn.cards)
                await _coloured(database, viewer, today, cards, drawn.cards, row.left_out)
            out.append(_Opened(row, period, drawn))
    return out


async def _coloured(
    database: Database,
    viewer: Viewer,
    today: date,
    kept: Sequence[KeptCard],
    cards: Sequence[RecapCard],
    left_out: Sequence[str],
) -> None:
    """The cards' colour, the hue of the period's most-viewed file: never one a locked reader may
    not be shown now, nor one the reader took out of the recap."""
    first = kept[0] if kept else None
    if first is None or first.accent_hue is None or first.accent_of in (None, *left_out):
        return
    files = [str(first.accent_of)]
    if not viewer.show_hidden and await store.hidden_files(
        database.fetch_all, viewer.id, today, files
    ):
        return
    for card in cards:
        if not card.hidden:
            card.accent_hue = first.accent_hue


#: How many of each month's files the first-and-last-month card draws.
MONTH_PICTURES = 3


async def _month_pictures(
    database: Database, user_id: str, kept: Sequence[KeptCard], cards: Sequence[RecapCard]
) -> None:
    """The first and last month's most viewed files beside their shares, as many of each, read
    when the card is drawn from those months' own rows. Never a file with any hidden part, nor one
    since gone, so the card names nothing hidden for any reader."""
    card = next((one for one in cards if one.kind == "before_after" and not one.hidden), None)
    recipe = next((one.recipe for one in kept if one.kind == "before_after" and one.recipe), None)
    if card is None or recipe is None:
        return
    months: list[dict[str, int]] = []
    for first, last in _spans(recipe, "viewed_ms:kind"):
        read = await store.rows(database, user_id, first, last, ("sittings:file",))
        views: dict[str, list[int]] = {}
        for row in read.rows:
            pair = views.setdefault(split_file_key(row.key)[1], [0, 0])
            pair[0] += row.whole
            pair[1] += row.hidden
        ranked = sorted(
            ((whole, asset) for asset, (whole, hidden) in views.items() if not hidden),
            key=lambda one: (-one[0], one[1]),
        )
        months.append({asset: whole for whole, asset in ranked[: MONTH_PICTURES * 2]})
    ids = sorted({asset for month in months for asset in month})
    names = {
        str(one["id"]): str(one["name"])
        for one in await arrivals.file_names(database.fetch_all, ids)
    }
    shown = [[asset for asset in month if asset in names][:MONTH_PICTURES] for month in months]
    most = min((len(month) for month in shown), default=0)
    card.rows = [
        _file_row(asset, names[asset], month[asset])
        for month, picked in zip(months, shown, strict=True)
        for asset in picked[:most]
    ]


def _file_row(asset: str, name: str, views: int) -> NamedRow:
    line = said(statements.named(statements.Named("asset", asset, name)))
    return NamedRow(
        piece=pieces_of(line)[0], value=views, unit="views", cover=cover_of("asset", asset)
    )


def _on_clock(period: Period | None, hours: statements.Clock) -> Period | None:
    """The period with the clock its cards say a time of day on."""
    return None if period is None else replace(period, hours=hours)
