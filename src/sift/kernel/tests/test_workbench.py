# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shape a queue registers with, and how the board decides where to draw it."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from sift.kernel import changes
from sift.kernel.access import Role, Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, ChangeBus
from sift.kernel.workbench import (
    KEPT_SUMMARIES,
    Band,
    Card,
    Preview,
    Recorded,
    Summary,
    Workbench,
    payload_held,
)

pytestmark = pytest.mark.unit


def summary(name: str, count: int) -> Summary:
    """What a survey returns. It never carries a band. See `Summary.band`: the board stamps it
    from the queue, so a survey that set one would be a second copy free to disagree."""
    return Summary(
        name=name,
        title=name.title(),
        decision="Say yes or no.",
        verb="things to answer",
        verb_one="thing to answer",
        icon="inbox",
        count=count,
    )


class FakeQueue:
    def __init__(
        self,
        name: str,
        *,
        count: int = 0,
        present: bool = True,
        band: Band = Band.DECISION,
        group: str | None = None,
        group_title: str | None = None,
        purpose: str | None = None,
        reversible: bool = True,
    ) -> None:
        self.name = name
        self.title = name.title()
        self.band = band
        self.group = group
        self.group_title = group_title
        self.purpose = purpose
        self.reversible = reversible
        self._count = count
        self._present = present
        #: Whether anybody asked. The only way to assert the board skipped a queue is to watch
        #: for the survey never happening.
        self.surveyed = False
        #: How many times it was surveyed, for the tests of what the board keeps.
        self.surveys = 0

    async def available(self) -> bool:
        return self._present

    async def survey(self, viewer: Viewer) -> Summary:
        self.surveyed = True
        self.surveys += 1
        return summary(self.name, self._count)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing to show. A queue is allowed to answer this way and several really do.

        Here because the protocol has it: a stand-in short of one member is not a stand-in for the
        thing, and `register` is typed to say so.
        """
        return ()


@pytest.fixture
def admin() -> Viewer:
    return Viewer(id="admin", role=Role.ADMIN)


def test_a_queue_is_found_by_name(admin: Viewer) -> None:
    """Registering a queue registers its reverser too, so nothing that draws a card has to."""
    _ = admin
    bench = Workbench()
    bench.register(FakeQueue("folders"))

    assert bench.reverser("folders") is not None
    assert bench.reverser("nothing-like-that") is None


def test_claiming_a_name_twice_is_a_bug_rather_than_an_override() -> None:
    """Two features quietly fighting over one queue would show as one of them simply not being on
    the board, on somebody else's install, months later."""
    bench = Workbench()
    bench.register(FakeQueue("folders"))

    with pytest.raises(ValueError, match="already registered"):
        bench.register(FakeQueue("folders"))


async def test_only_the_queues_with_something_behind_them_are_surveyed(admin: Viewer) -> None:
    """An empty panel reads as a feature that is broken rather than one that is not turned on."""
    bench = Workbench()
    bench.register(FakeQueue("folders", count=2))
    bench.register(FakeQueue("faces", count=9, present=False))

    assert [one.name for one in await bench.board(admin)] == ["folders"]
    assert [one.name for one in await bench.available()] == ["folders"]


async def test_a_board_of_named_queues_surveys_those_and_no_other(admin: Viewer) -> None:
    """A screen asking again for the queues it draws costs those surveys, not every queue's."""
    bench = Workbench()
    queues = [FakeQueue(name) for name in ("folders", "duplicates", "copies", "faces")]
    for one in queues:
        bench.register(one)

    found = await bench.board(admin, {"copies", "duplicates", "gone"})

    assert [one.name for one in found] == ["duplicates", "copies"]
    assert [one.name for one in queues if one.surveyed] == ["duplicates", "copies"]


async def test_the_board_keeps_the_order_things_registered_in(admin: Viewer) -> None:
    bench = Workbench()
    for name in ("folders", "unidentified", "ignored"):
        bench.register(FakeQueue(name))

    assert [one.name for one in await bench.board(admin)] == ["folders", "unidentified", "ignored"]


async def test_the_board_draws_the_records_as_well_as_the_questions(admin: Viewer) -> None:
    """A pile that is a record is still on the board. It is drawn apart from the questions rather
    than left off, because it is the only way back to what was already settled."""
    bench = Workbench()
    bench.register(FakeQueue("folders", count=3))
    bench.register(FakeQueue("identified", count=900, band=Band.RECORD))

    assert [one.name for one in await bench.board(admin)] == ["folders", "identified"]


async def test_the_board_puts_each_queues_own_band_and_group_on_its_summary(
    admin: Viewer,
) -> None:
    """A summary wears the band its QUEUE declared, whatever the survey returned.

    There is one declaration and the board stamps it, so a queue cannot be drawn in the wrong half
    of the screen by a second copy disagreeing; this is what proves the stamping happens.

    The stand-in's survey deliberately returns a summary with the DEFAULT band, so a stamp that
    was not applied would leave a record looking like a decision.
    """
    bench = Workbench()
    declared = FakeQueue("a", purpose="Things somebody has to answer.")
    declared.title = "Things to answer"
    bench.register(declared)
    bench.register(FakeQueue("b", band=Band.RECORD, group="faces", group_title="Faces"))

    drawn = {one.name: one for one in await bench.board(admin)}

    assert drawn["a"].band is Band.DECISION
    assert drawn["a"].group is None
    assert drawn["a"].pending is True
    assert drawn["b"].band is Band.RECORD
    assert drawn["b"].group == "faces"
    # And the group's own name, which the board needs to draw a group of cards as one card.
    assert drawn["a"].group_title is None
    assert drawn["b"].group_title == "Faces"
    # And what the card is for, which the survey never sets either.
    assert drawn["a"].purpose == "Things somebody has to answer."
    assert drawn["b"].purpose is None
    #: The whole reason `pending` is derived rather than declared: it can only ever agree.
    assert drawn["b"].pending is False
    # And the title the class declares, whatever the survey said.
    assert (drawn["a"].title, drawn["b"].title) == ("Things to answer", "B")


class Receipts:
    """A reverser with no card, declaring the group whose card it records for, or none."""

    reversible = True

    def __init__(self, name: str, group: str | None = None, *, grouped: bool = True) -> None:
        self.name = name
        if grouped:
            self.group = group

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()


def test_a_decision_s_card_is_named_without_a_survey() -> None:
    """Insights names the card each decision was taken on, and a survey would count every pile."""
    bench = Workbench()
    bench.register(FakeQueue("seen", band=Band.RECORD, group="faces"))
    bench.register(FakeQueue("suggested", group="faces", group_title="Faces"))
    bench.register(FakeQueue("to-name", group="faces"))
    bench.register(FakeQueue("shoots"))
    bench.register_reverser(Receipts("identified", "faces"))
    bench.register_reverser(Receipts("starters", grouped=False))
    bench.register_reverser(Receipts("loose"))

    assert bench.recorded_under("faces") == ["seen", "suggested", "to-name", "identified"]
    assert bench.card_of("identified") == Card("suggested", "Faces")
    assert bench.card_of("seen") == Card("suggested", "Faces")
    assert bench.card_of("shoots") == Card("shoots", "Shoots")
    for nobody in ("starters", "loose", "retired"):
        assert bench.card_of(nobody) is None


def test_a_receipt_whose_group_has_no_card_left_names_no_card() -> None:
    """A group whose piles are all records, or all retired, has no card to lead it, so a decision
    recorded under it names none rather than a record band no board draws. And a queue outside
    every group is recorded under its own name alone."""
    bench = Workbench()
    bench.register(FakeQueue("kept", band=Band.RECORD, group="kept-only"))
    bench.register_reverser(Receipts("kept-receipts", "kept-only"))
    bench.register_reverser(Receipts("old-receipts", "retired-group"))

    assert bench.card_of("kept-receipts") is None
    assert bench.card_of("kept") is None
    assert bench.card_of("old-receipts") is None
    assert bench.recorded_under(None) == []


async def test_a_queue_may_not_claim_a_name_a_screen_already_lives_at(admin: Viewer) -> None:
    """`/organize/decisions` is a real address (History's Decisions), so no queue may register
    under `decisions`.

    A router prefers its fixed route, so such a queue would draw a card on the board that opens
    somebody else's screen, and nothing anywhere would report it. Refused at boot instead, which
    makes it a failed start rather than a card that quietly goes to the wrong place.
    """
    _ = admin
    bench = Workbench()
    with pytest.raises(ValueError, match="reserved"):
        bench.register(FakeQueue("decisions"))


async def test_a_retired_queue_can_still_take_its_own_decisions_back(admin: Viewer) -> None:
    """A reverser is registered without a card, and `reverser` finds it.

    A pile can move onto the records it is about while its receipts stay on disk. Looked up among
    the CARDS they would answer "nothing in this version knows how
    to take that decision back", about a version that knows perfectly well.
    """
    _ = admin
    bench = Workbench()
    retired = FakeQueue("reconcile")
    bench.register_reverser(retired)

    assert bench.reverser("reconcile") is retired
    #: And it is not drawn: a reverser is not a queue.
    assert [one.name for one in await bench.board(admin)] == []


async def test_two_things_cannot_claim_one_receipt_name(admin: Viewer) -> None:
    """Two reversers under one name is a bug and not an override: the same rule `register` has.

    Without it the second registration would be silently unreachable, and every receipt of that
    kind would be put back by whichever was registered first.
    """
    _ = admin
    bench = Workbench()
    bench.register(FakeQueue("folders"))
    with pytest.raises(ValueError, match="take its decisions back"):
        bench.register_reverser(FakeQueue("folders"))


async def test_every_registered_queue_says_whether_its_decisions_are_final(
    admin: Viewer,
) -> None:
    """`Queue.reversible` is a Protocol default, and a Protocol default is not inherited.

    So a queue that simply does not declare it has no such attribute at all, and the shell reading
    one gets an `AttributeError` rather than the default it looks like it would get. Read through a
    `getattr(..., True)` instead it would be worse: every queue that forgot would silently claim
    its decisions can be taken back, which puts an Undo button on decisions that deleted files.

    So it is read plainly, and this is what makes forgetting it a failed build rather than a button
    that lies. The same arrangement `pending` has, and for the same reason.
    """
    bench = Workbench()
    bench.register(FakeQueue("a"))

    for registered in await bench.available():
        assert isinstance(registered.reversible, bool)


def test_a_payload_is_read_as_a_record_and_anything_else_holds_nothing() -> None:
    assert payload_held('{"asset_id": "a1", "count": 2}') == {"asset_id": "a1", "count": 2}
    assert payload_held("{not json") == {}
    assert payload_held("[1, 2]") == {}
    assert payload_held('"a string"') == {}
    assert payload_held(None) == {}  # type: ignore[arg-type]

    recorded = Recorded(
        id="r1", queue="q", payload='{"from": "a2"}', title="t", detail="", decided_at=0
    )
    assert recorded.held() == {"from": "a2"}
    assert Recorded(id="r2", queue="q", payload="", title="t", detail="", decided_at=0).held() == {}


# --- what the board keeps between reads --------------------------------------------------------


class Narrow(FakeQueue):
    """A queue that names the announcements that can move it. See `Moves`."""

    def __init__(self, name: str, moved_by: frozenset[About] | None, **kwargs: object) -> None:
        super().__init__(name, **kwargs)  # type: ignore[arg-type]
        self.moved_by = moved_by


@pytest.fixture
def bus() -> Iterator[ChangeBus]:
    """A bus this process announces to, as the running application has."""
    listening = ChangeBus()
    changes.listens(listening)
    yield listening
    changes.listens(None)


def _told(bus: ChangeBus, about: About) -> None:
    bus.publish(EVERY_ADMIN, about)


async def test_a_summary_is_kept_until_an_announcement_that_can_move_it(
    admin: Viewer, bus: ChangeBus
) -> None:
    """Every open screen asking again, and the job queue moving several times a second, must not
    each cost a survey; a decision written to the pile must."""
    bench = Workbench()
    folders = FakeQueue("folders", count=2)
    bench.register(folders)

    await bench.board(admin)
    await bench.board(admin)
    _told(bus, About.JOBS)
    _told(bus, About.DOWNLOADS)
    assert [one.count for one in await bench.board(admin)] == [2]
    assert folders.surveys == 1

    folders._count = 1
    _told(bus, About.LIBRARY)
    assert [one.count for one in await bench.board(admin)] == [1]
    assert folders.surveys == 2


async def test_a_queue_naming_its_subjects_is_kept_under_those_alone(
    admin: Viewer, bus: ChangeBus
) -> None:
    bench = Workbench()
    music = Narrow("music", frozenset({About.JOBS}))
    bench.register(music)

    await bench.board(admin)
    _told(bus, About.LIBRARY)
    await bench.board(admin)
    assert music.surveys == 1

    _told(bus, About.JOBS)
    await bench.board(admin)
    assert music.surveys == 2


class Stilled(FakeQueue):
    """A queue whose cards draw only files whose still is made. See `Stills`."""

    draws_stills = True


async def test_a_picture_made_moves_only_a_queue_that_draws_stills(
    admin: Viewer, bus: ChangeBus
) -> None:
    """Generate announces an arrival per picture: a pile it cannot move is kept through them, and
    a card that draws only made stills is surveyed again. A file arriving moves both."""
    bench = Workbench()
    faces = FakeQueue("faces", count=2)
    duplicates = Stilled("duplicates", count=1)
    bench.register(faces)
    bench.register(duplicates)

    await bench.board(admin)
    bus.publish(EVERY_ADMIN, About.ARRIVALS, picture=True)
    await bench.board(admin)
    assert (faces.surveys, duplicates.surveys) == (1, 2)

    _told(bus, About.ARRIVALS)
    await bench.board(admin)
    assert (faces.surveys, duplicates.surveys) == (2, 3)


async def test_a_queue_naming_none_is_surveyed_on_every_read(admin: Viewer, bus: ChangeBus) -> None:
    """A folder on disk moves without any announcement, so nothing could say it went stale."""
    _ = bus
    bench = Workbench()
    quarantine = Narrow("quarantine", None)
    bench.register(quarantine)

    await bench.board(admin)
    await bench.board(admin)

    assert quarantine.surveys == 2


async def test_a_summary_is_kept_per_viewer(admin: Viewer, bus: ChangeBus) -> None:
    """What somebody may see moves their cache stamp, and an unlocked vault shows more: either is
    a different summary, never the one kept for somebody else."""
    _ = bus
    bench = Workbench()
    folders = FakeQueue("folders")
    bench.register(folders)

    await bench.board(admin)
    await bench.board(Viewer(id="admin", role=Role.ADMIN, cache_stamp=1))
    await bench.board(Viewer(id="admin", role=Role.ADMIN, show_hidden=True))
    await bench.board(Viewer(id="other", role=Role.ADMIN))

    assert folders.surveys == 4


async def test_readers_arriving_together_share_one_survey(admin: Viewer, bus: ChangeBus) -> None:
    _ = bus
    bench = Workbench()
    gate = asyncio.Event()

    class Slow(FakeQueue):
        async def survey(self, viewer: Viewer) -> Summary:
            await gate.wait()
            return await super().survey(viewer)

    slow = Slow("faces", count=3)
    bench.register(slow)

    asked = [asyncio.ensure_future(bench.board(admin)) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    answers = await asyncio.gather(*asked)

    assert [[one.count for one in found] for found in answers] == [[3], [3], [3]]
    assert slow.surveys == 1


async def test_a_survey_that_fails_keeps_nothing(admin: Viewer, bus: ChangeBus) -> None:
    _ = bus
    bench = Workbench()

    class Failing(FakeQueue):
        async def survey(self, viewer: Viewer) -> Summary:
            await super().survey(viewer)
            if self.surveys == 1:
                raise RuntimeError("the read failed")
            return summary(self.name, 4)

    failing = Failing("faces")
    bench.register(failing)

    with pytest.raises(RuntimeError):
        await bench.board(admin)
    assert [one.count for one in await bench.board(admin)] == [4]
    assert failing.surveys == 2


async def test_with_nothing_announcing_every_read_surveys(admin: Viewer) -> None:
    """A console tool or a test with no bus: nothing could say a kept summary went stale."""
    bench = Workbench()
    folders = FakeQueue("folders")
    bench.register(folders)

    await bench.board(admin)
    await bench.board(admin)

    assert folders.surveys == 2


async def test_the_board_keeps_a_bounded_number_of_summaries(bus: ChangeBus) -> None:
    _ = bus
    bench = Workbench()
    bench.register(FakeQueue("folders"))

    for stamp in range(KEPT_SUMMARIES + 3):
        await bench.board(Viewer(id="admin", role=Role.ADMIN, cache_stamp=stamp))

    assert len(bench._kept) == KEPT_SUMMARIES
