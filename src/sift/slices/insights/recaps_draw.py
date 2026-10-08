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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import cast

from pydantic import TypeAdapter, ValidationError

from sift.kernel.access import Viewer
from sift.kernel.access.viewer import Concealment
from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.slices.insights import statements
from sift.slices.insights.metrics import split_file_key
from sift.slices.insights.path import achievement_head
from sift.slices.insights.recaps_cards import (
    BUILDERS,
    FILE_METRICS,
    Said,
    _figure_names,
    _said,
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
) -> _Drawn | None:
    """THE VAULT RULE FOR A RECAP: the cards this reader may be shown now, or None for a recap that
    is not there at all for them. `hidden` is the hidden part of every source, worked out now.

    A card with no recipe (an achievement) is drawn as it was built: it names nothing the vault
    decides, and it carries no figure with a hidden part.
    """
    locked = not viewer.show_hidden
    placeholder = viewer.concealment is Concealment.PLACEHOLDER

    def shown(name: str, whole: int) -> int:
        return whole - min(hidden.get(name, 0), whole) if locked else whole

    below_floor = False
    if locked and period is not None:
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
        if sittings is not None and shown(source("sittings"), sittings) < FLOOR_SITTINGS:
            if not placeholder:
                return None
            below_floor = True

    out: list[RecapCard] = []
    something_hidden = below_floor
    for card in cards:
        recipe = card.recipe
        if recipe is None or period is None:
            out.append(RecapCard(**card.model_dump(exclude={"recipe", "hidden_things"})))
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
        if locked and (naming or (below_floor and card.kind != "closing")):
            something_hidden = True
            if placeholder and card.kind != "o":
                out.append(_stub(card.kind))
            continue
        figures = {name: shown(name, whole) for name, whole in recipe.sources.items()}
        if locked and any(hidden.get(name, 0) for name in recipe.sources):
            something_hidden = True
        words = BUILDERS[card.kind](figures, recipe, period, today)
        drawn = _said(card.kind, words, figures)
        if drawn is None or words is None:
            # Nothing true left to say once the hidden part is out. The O card is absent in both
            # modes; any other card is a locked tile in placeholder mode, so that mode's gap is
            # where the card was.
            if locked and placeholder and card.kind != "o":
                out.append(_stub(card.kind))
            continue
        _hidden_figures(drawn, words, {} if locked else hidden)
        drawn.hidden_things = [] if locked else gone_now
        out.append(drawn)
    return _Drawn(out, something_hidden and locked and placeholder)


def _hidden_figures(drawn: RecapCard, words: Said, hidden: Mapping[str, int]) -> None:
    """Each figure's hidden part, for an open vault: nothing while locked (`hidden` empty)."""
    if drawn.figure is not None:
        drawn.figure.hidden_part = sum(hidden.get(name, 0) for name in _figure_names(words))
    for figure, cell in zip(drawn.figures, words.cells, strict=True):
        figure.hidden_part = hidden.get(cell.figure, 0)


#: The cards that leave out what they may not say rather than being locked whole.
_SAID_AROUND = frozenset({"first_last", "closing"})


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
        drawn = _draw(cards, period, viewer, hidden, today)
        if drawn is not None:
            out.append(_Opened(row, period, drawn))
    return out


def _on_clock(period: Period | None, hours: statements.Clock) -> Period | None:
    """The period with the clock its cards say a time of day on."""
    return None if period is None else replace(period, hours=hours)
