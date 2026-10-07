# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking three public services what a file is, and how sure the answer is worth calling.

Nothing here is written to the library. What the pass produces is a pile of CLAIMS, each one about
a file, and the whole design rests on that: a threshold decided what was worth keeping, a person
decides what is true.

The grading is the part with real consequences. An exact-file hash is an identity; a perceptual
match is a resemblance, and the commonest wrong answer to a perceptual match is a different cut of
the same shoot, which the length rules out and which no stash-box can be asked about, because the
fingerprint query carries a hash and an algorithm and no duration at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

# The ledger's tables, registered so a take-back's History line has somewhere to go.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes import scanning as scanning_module
from sift.slices.stash_boxes.adapter import LONE, ONE_OF_SEVERAL, Box, StashBoxUnreachable
from sift.slices.stash_boxes.service import (
    EXACT,
    SHORT_MS,
    TIGHT_MS,
    Grade,
    StashBoxService,
    grade_of,
    grade_unproven,
)

pytestmark = pytest.mark.anyio

A_KEY = b"0" * 32
HASHES = {"oshash": "abc", "phash": "def"}


def _found(
    *, confidence: float = 0.5, duration_ms: int | None = None, remote_id: str = "r1"
) -> FoundRecord:
    fields: dict[str, object] = {}
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    return FoundRecord(
        source_id="box",
        remote_id=remote_id,
        subject=Subject.ASSET,
        name="A Clip",
        fields=fields,
        confidence=confidence,
    )


class _Adapter:
    """Answers with whatever it was given, per call, and can be told to be unreachable."""

    def __init__(self, answers: list[list[FoundRecord]] | None = None) -> None:
        self.answers = answers if answers is not None else []
        self.asked: list[Mapping[str, str]] = []
        self.refuse: str | None = None

    async def search(self, box: Box, term: str) -> list[FoundRecord]:
        raise AssertionError("the scan never searches by name")

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        self.asked.append(dict(hashes))
        if self.refuse:
            raise StashBoxUnreachable(self.refuse)
        return self.answers.pop(0) if self.answers else []


async def _service(temp_db: Database, adapter: _Adapter) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]


async def _files(temp_db: Database, *asset_ids: str) -> None:
    """Rows for the files a scan is about. The match table points at them with a real key."""
    for asset_id in asset_ids:
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, asset_id),
        )


async def _a_box(service: StashBoxService, name: str = "StashDB") -> str:
    return await service.add(
        name=name,
        endpoint=f"https://{name.lower()}.example/graphql",
        api_key="a-real-key",
        master_key=A_KEY,
    )


# --- how sure an answer is worth calling ------------------------------------------------------


class TestTheGrade:
    def test_an_exact_file_hash_is_an_identity_and_needs_no_length(self) -> None:
        """It means somebody else uploaded the identical file. There is nothing left to check, and
        the length is not asked for, so a file Sift never measured still grades certain."""
        assert (
            grade_of(_found(confidence=EXACT), length_ms=None, tolerance_ms=10_000) is Grade.CERTAIN
        )

    def test_a_resemblance_whose_length_agrees_only_loosely_is_likely(self) -> None:
        """Inside the window somebody set, outside the one a certain match needs."""
        answer = _found(confidence=0.9, duration_ms=68_000)

        assert grade_of(answer, length_ms=60_000, tolerance_ms=10_000) is Grade.LIKELY

    def test_a_resemblance_is_never_certain_however_closely_its_length_agrees(self) -> None:
        """Once the scene's own fingerprints are read, an identical file shows as one, so an answer
        that only resembles the file is not the same file, whatever its length."""
        answer = _found(confidence=LONE, duration_ms=60_000)

        assert grade_of(answer, length_ms=60_000, tolerance_ms=10_000) is Grade.LIKELY

    def test_a_resemblance_on_a_short_clip_is_never_more_than_unsure(self) -> None:
        """Unrelated clips of a few seconds land a few bits apart, and a few seconds is most of the
        clip, so neither the picture nor the length can say it is the same video."""
        short = SHORT_MS - 1
        answer = _found(confidence=LONE, duration_ms=short)

        assert grade_of(answer, length_ms=short, tolerance_ms=10_000) is Grade.UNSURE

    def test_one_of_several_resemblances_is_likely_at_best(self) -> None:
        answer = _found(confidence=ONE_OF_SEVERAL, duration_ms=120_000)

        assert grade_of(answer, length_ms=120_000, tolerance_ms=10_000) is Grade.LIKELY

    def test_a_kept_answer_stays_certain_only_while_its_length_agrees_closely(self) -> None:
        """An identical file runs for the same time. Inside the window somebody set but outside
        the close one, the kept answer is only as good as a resemblance."""
        close = _found(confidence=EXACT, duration_ms=120_000 + TIGHT_MS)
        loose = _found(confidence=EXACT, duration_ms=120_000 + TIGHT_MS + 1_000)

        assert grade_unproven(close, length_ms=120_000, tolerance_ms=10_000) is Grade.CERTAIN
        assert grade_unproven(loose, length_ms=120_000, tolerance_ms=10_000) is Grade.LIKELY

    def test_the_close_window_is_held_under_the_tolerance_somebody_set(self) -> None:
        answer = _found(confidence=EXACT, duration_ms=122_000)

        assert grade_unproven(answer, length_ms=120_000, tolerance_ms=3_000) is Grade.CERTAIN
        assert grade_unproven(answer, length_ms=120_000, tolerance_ms=1_000) is Grade.UNSURE

    def test_a_match_kept_before_the_proof_was_read_is_graded_as_a_resemblance(self) -> None:
        """Such a record says exact because an exact hash was SENT, which it always was."""
        answer = _found(confidence=EXACT, duration_ms=232_000)

        assert grade_unproven(answer, length_ms=8_067, tolerance_ms=10_000) is Grade.UNSURE
        # A clip of a few seconds is not kept on its length either: short clips share lengths.
        clip = _found(confidence=EXACT, duration_ms=8_000)
        assert grade_unproven(clip, length_ms=8_067, tolerance_ms=10_000) is Grade.UNSURE
        assert grade_unproven(answer, length_ms=232_400, tolerance_ms=10_000) is Grade.CERTAIN

    def test_a_resemblance_whose_length_disagrees_is_unsure(self) -> None:
        """The commonest wrong answer a perceptual hash gives: a two-minute trailer matches the
        ninety-minute film it was cut from perfectly well."""
        answer = _found(confidence=0.9, duration_ms=5_400_000)

        assert grade_of(answer, length_ms=120_000, tolerance_ms=10_000) is Grade.UNSURE

    def test_a_length_sift_does_not_hold_is_not_a_disagreement(self) -> None:
        """A file whose duration was never measured grades unsure rather than being thrown away.
        The match may well be right, and nothing here can tell."""
        answer = _found(confidence=0.9, duration_ms=61_000)

        assert grade_of(answer, length_ms=None, tolerance_ms=10_000) is Grade.UNSURE

    def test_a_length_the_stash_box_did_not_send_is_not_one_either(self) -> None:
        assert (
            grade_of(_found(confidence=0.9), length_ms=60_000, tolerance_ms=10_000) is Grade.UNSURE
        )

    def test_a_length_of_nothing_is_read_as_no_length_rather_than_as_zero(self) -> None:
        """A stash-box sending 0 has not said the file is instantaneous; it has said nothing. Taken
        as a number it would disagree with every real duration and grade everything unsure."""
        answer = _found(confidence=0.9, duration_ms=0)

        assert grade_of(answer, length_ms=60_000, tolerance_ms=10_000) is Grade.UNSURE

    def test_the_tolerance_is_a_window_around_the_length_and_not_a_ratio(self) -> None:
        answer = _found(confidence=0.9, duration_ms=70_000)

        assert grade_of(answer, length_ms=60_000, tolerance_ms=10_000) is Grade.LIKELY
        assert grade_of(answer, length_ms=60_000, tolerance_ms=9_999) is Grade.UNSURE


# --- one file, asked of every switched-on box -------------------------------------------------


async def test_a_file_a_box_recognises_is_kept_and_graded(temp_db: Database) -> None:
    adapter = _Adapter([[_found(confidence=EXACT)]])
    service = await _service(temp_db, adapter)
    await _a_box(service)
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert [(one.asset_id, one.grade, one.state) for one in kept] == [
        ("a1", Grade.CERTAIN, "waiting")
    ]
    assert adapter.asked == [HASHES]
    assert (await service.waiting(limit=0))[1] == 1


async def test_the_box_is_named_on_the_answer_so_a_row_reads_without_a_second_read(
    temp_db: Database,
) -> None:
    adapter = _Adapter([[_found(confidence=EXACT)]])
    service = await _service(temp_db, adapter)
    await _a_box(service, name="FansDB")
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert kept[0].box_name == "FansDB"


async def test_only_the_first_answer_from_a_box_is_kept(temp_db: Database) -> None:
    """The adapter has already thrown away the shape that means "no match", so what arrives is
    either one answer or a short list whose first entry is the best of them. Four candidates per
    file per box is a screen nobody works through."""
    adapter = _Adapter([[_found(remote_id="best"), _found(remote_id="second")]])
    service = await _service(temp_db, adapter)
    await _a_box(service)
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert [one.remote_id for one in kept] == ["best"]


async def test_a_file_nobody_recognises_is_written_down_as_asked(temp_db: Database) -> None:
    """The asking-down is what makes a second pass cheap, and it is not bookkeeping: a pass that
    remembered only its finds would ask about that file again on every run for ever, through a
    throttle that exists to stop exactly that."""
    service = await _service(temp_db, _Adapter([[]]))
    await _a_box(service)
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert kept == []
    assert await service.unasked(["a1"]) == []


async def test_a_box_that_could_not_be_reached_is_not_written_down_as_asked(
    temp_db: Database,
) -> None:
    """The honest reading of a service that was down. The next pass tries again."""
    adapter = _Adapter()
    adapter.refuse = "it did not answer"
    service = await _service(temp_db, adapter)
    await _a_box(service)
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert kept == []
    assert await service.unasked(["a1"]) == ["a1"]


async def test_asking_the_same_question_twice_neither_asks_again_nor_piles_up(
    temp_db: Database,
) -> None:
    """Two things together, and both matter.

    The cached answer is why the box hears from this installation once, which is the whole reason
    the cache is in front of every question rather than beside some of them. And the match is an
    upsert keyed on the file and the box, so a pass run twice leaves one row rather than a pile of
    the same claim.
    """
    adapter = _Adapter([[_found(remote_id="first")], [_found(remote_id="second")]])
    service = await _service(temp_db, adapter)
    await _a_box(service)
    await _files(temp_db, "a1")

    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert len(adapter.asked) == 1
    waiting, total = await service.waiting(limit=10)
    assert total == 1
    assert [one.remote_id for one in waiting] == ["first"]


async def test_a_switched_off_box_is_not_asked_and_leaves_the_file_unasked(
    temp_db: Database,
) -> None:
    adapter = _Adapter([[_found()]])
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await service.set_enabled(box, False)
    await _files(temp_db, "a1")

    kept = await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert kept == []
    assert adapter.asked == []


# --- which files still have to be asked about -------------------------------------------------


async def test_a_file_counts_as_done_only_once_every_box_has_been_asked(
    temp_db: Database,
) -> None:
    """The other way round (done as soon as one box has) would mean a stash-box added later was
    never asked about anything already in the library."""
    adapter = _Adapter([[]])
    service = await _service(temp_db, adapter)
    first = await _a_box(service, name="StashDB")
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.unasked(["a1"]) == []

    await _a_box(service, name="FansDB")

    assert await service.unasked(["a1"]) == ["a1"]
    assert first  # the first box is still configured; this is about the second one


async def test_nothing_is_unasked_when_there_is_nothing_to_ask_or_nobody_to_ask(
    temp_db: Database,
) -> None:
    service = await _service(temp_db, _Adapter())

    assert await service.unasked(["a1"]) == [], "no box is configured, so nothing is owed"

    await _a_box(service)
    assert await service.unasked([]) == []


async def test_a_page_is_answered_in_one_question_per_box_and_keeps_its_order(
    temp_db: Database,
) -> None:
    """Asked of the page rather than of the file: five hundred checked one at a time is five
    hundred round trips to settle a question one `IN` clause answers."""
    adapter = _Adapter([[]])
    service = await _service(temp_db, adapter)
    await _a_box(service)
    await _files(temp_db, "a2")
    await service.scan_one("a2", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert await service.unasked(["a1", "a2", "a3", "a1"]) == ["a1", "a3"]


# --- the pile, and answering it ---------------------------------------------------------------


async def test_the_surest_answers_are_at_the_top_of_the_pile(temp_db: Database) -> None:
    """Somebody working through a pile should meet the answers that need the least thought first."""
    adapter = _Adapter(
        [
            [_found(confidence=0.9)],
            [_found(confidence=0.9, duration_ms=61_000)],
            [_found(confidence=EXACT)],
        ]
    )
    service = await _service(temp_db, adapter)
    await _a_box(service)
    # Named so that ALPHABETICAL order is the reverse of the order being asserted. The final
    # tie-break in both statements is `asset_id ASC`, so ids that happen to sort into the expected
    # answer would let this pass with the grade ordering deleted.
    await _files(temp_db, "x-unsure", "y-likely", "z-certain")

    # A DIFFERENT fingerprint per file, and that is the half without which none of this works. The
    # answers are cached against the question, so three files asking the identical question get the
    # first answer three times: all `unsure`, all filed in the same second, and the pile would then
    # come out in whatever order the tie-break gave.
    await service.scan_one(
        "x-unsure", {"oshash": "a1", "phash": "b1"}, A_KEY, length_ms=None, tolerance_ms=10_000
    )
    await service.scan_one(
        "y-likely", {"oshash": "a2", "phash": "b2"}, A_KEY, length_ms=60_000, tolerance_ms=10_000
    )
    await service.scan_one(
        "z-certain", {"oshash": "a3", "phash": "b3"}, A_KEY, length_ms=None, tolerance_ms=10_000
    )

    waiting, total = await service.waiting(limit=10)

    assert [one.grade.value for one in waiting] == ["certain", "likely", "unsure"], (
        "the pile is ordered by how sure the answer is, and nothing else decides it"
    )
    assert [one.asset_id for one in waiting] == ["z-certain", "y-likely", "x-unsure"]
    assert total == 3


async def test_a_library_that_has_never_been_scanned_has_no_pile_to_offer(
    temp_db: Database,
) -> None:
    """Asked separately from the count, and the difference decides whether the panel is drawn at
    all: an empty panel reads as a feature that does not work rather than one nobody has run."""
    service = await _service(temp_db, _Adapter([[]]))
    await _a_box(service)
    await _files(temp_db, "a1")

    assert await service.any_match() is False

    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.any_match() is False, "a file nobody recognised is not a pile"


async def test_a_library_that_has_answered_everything_keeps_its_panel_at_zero(
    temp_db: Database,
) -> None:
    """The state this screen is trying to reach, and it is worth showing."""
    service = await _service(temp_db, _Adapter([[_found()]]))
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert await service.settle("a1", box, applied=True) is True

    assert (await service.waiting(limit=0))[1] == 0
    assert await service.any_match() is True


async def test_answering_the_same_match_twice_is_nothing_rather_than_a_second_decision(
    temp_db: Database,
) -> None:
    """What makes a bulk confirm safe to press again after it half-failed."""
    service = await _service(temp_db, _Adapter([[_found()]]))
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    assert await service.settle("a1", box, applied=True) is True
    assert await service.settle("a1", box, applied=False) is False


async def test_asked_again_on_purpose_a_changed_answer_is_a_question_again(
    temp_db: Database,
) -> None:
    """A settled answer is left alone by an ordinary ask, so pressing Identify again would change
    nothing: the new answer would be fetched and thrown away. Asked again on purpose, an answer that
    differs from the settled one waits again, and one that does not leaves the decision alone."""
    adapter = _Adapter(
        [[_found(remote_id="first")], [_found(remote_id="second")], [_found(remote_id="second")]]
    )
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.settle("a1", box, applied=True) is True

    await service.forget_answers(box)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000, again=True)

    held = await service.match("a1", box)
    assert held is not None
    assert (held.state, held.remote_id) == ("waiting", "second")

    assert await service.settle("a1", box, applied=True) is True
    await service.forget_answers(box)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000, again=True)

    same = await service.match("a1", box)
    assert same is not None
    assert (same.state, same.remote_id) == ("applied", "second")


async def test_asked_again_on_purpose_a_refused_answer_that_changed_is_a_question_again(
    temp_db: Database,
) -> None:
    """A No is settled too. Asked again on purpose, a different answer from the box is a new
    question rather than the old No: nothing was applied, so there is nothing to take back first,
    and only the re-ask's own statement can put it back in the pile."""
    adapter = _Adapter([[_found(remote_id="first")], [_found(remote_id="second")]])
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.settle("a1", box, applied=False) is True

    await service.forget_answers(box)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000, again=True)

    held = await service.match("a1", box)
    assert held is not None
    assert (held.state, held.remote_id) == ("waiting", "second")


async def test_asked_again_where_the_box_now_knows_nothing_the_settled_answer_stands(
    temp_db: Database,
) -> None:
    """Silence is not a different answer: a box that no longer recognises the file leaves what
    was applied from it where it is."""
    adapter = _Adapter([[_found(remote_id="first")], []])
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.settle("a1", box, applied=True) is True

    await service.forget_answers(box)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000, again=True)

    held = await service.match("a1", box)
    assert held is not None and (held.state, held.remote_id) == ("applied", "first")


async def test_an_ordinary_ask_never_reopens_a_settled_answer(temp_db: Database) -> None:
    """The other half: a sweep asking about everything must not put decisions back in the pile."""
    adapter = _Adapter([[_found(remote_id="first")], [_found(remote_id="second")]])
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.settle("a1", box, applied=True) is True

    await service.forget_answers(box)
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    held = await service.match("a1", box)
    assert held is not None
    assert (held.state, held.remote_id) == ("applied", "first")


async def test_one_answer_can_be_read_back_and_one_that_is_not_there_says_so(
    temp_db: Database,
) -> None:
    """What a confirm screen reads before it writes anything."""
    service = await _service(temp_db, _Adapter([[_found(remote_id="r9")]]))
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    held = await service.match("a1", box)

    assert held is not None
    assert held.remote_id == "r9"
    assert await service.match("nothing", box) is None


async def test_a_decision_taken_back_puts_the_question_among_the_others(
    temp_db: Database,
) -> None:
    service = await _service(temp_db, _Adapter([[_found()]]))
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    await service.settle("a1", box, applied=True)

    assert await service.reopen("a1", box) is True

    assert (await service.waiting(limit=0))[1] == 1
    # And a second undo of the same decision puts nothing back, because nothing is settled.
    assert await service.reopen("a1", box) is False


# --- a payload that will not read ----------------------------------------------------------------


async def test_a_kept_answer_that_cannot_be_read_draws_as_an_empty_record(
    temp_db: Database,
) -> None:
    """A record outlives the version that wrote it, and a row a screen still has to draw is better
    answered with a visibly empty record than with an exception that empties the whole pile."""
    service = await _service(temp_db, _Adapter())
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
        " VALUES ('a1', ?, 'r1', '[]', 'unsure', 'waiting', 0)",
        (box,),
    )

    waiting, total = await service.waiting(limit=10)

    assert total == 1
    assert waiting[0].record.name == ""
    assert waiting[0].record.source_id == box


async def test_a_search_asks_the_reader_that_speaks_for_that_kind_of_subject(
    temp_db: Database,
) -> None:
    """One cache in front of three questions, and the kind rides in the key. Without it a search
    for a SITE called Northlight would be answered out of the row a search for a PERSON left behind."""

    class _Kinds:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def search(self, box: Box, term: str) -> list[FoundRecord]:
            self.asked.append("person")
            return []

        async def search_sites(self, box: Box, term: str) -> list[FoundRecord]:
            self.asked.append("site")
            return []

        async def search_tags(self, box: Box, term: str) -> list[FoundRecord]:
            self.asked.append("tag")
            return []

        async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
            self.asked.append("recognise")  # pragma: no cover - not what this asks
            return []

    adapter = _Kinds()
    await temp_db.initialize_schema()
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await _a_box(service)

    await service.search("northlight", A_KEY, subject=Subject.SITE)
    await service.search("northlight", A_KEY, subject=Subject.TAG)
    await service.search("northlight", A_KEY, subject=Subject.PERSON)

    assert adapter.asked == ["site", "tag", "person"]


async def test_a_picture_from_a_box_that_cannot_be_unsealed_is_no_picture(
    temp_db: Database,
) -> None:
    """The key exists in memory only while somebody is signed in. Answering with nothing is what
    makes a missing thumbnail a missing thumbnail rather than an error on a screen."""
    service = await _service(temp_db, _Adapter())
    box = await _a_box(service)

    assert await service.picture(box, "https://stashdb.example/a.png", None) is None


# --- one file, one transaction ------------------------------------------------------------------


async def test_what_every_box_said_about_one_file_lands_in_one_transaction(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One transaction per file, not per box, so a failure part-way leaves nothing half written.

    Proved by breaking the second box's write and asking whether the first box's row survived it.
    The asking is still a box at a time (that half is a network call and must not be inside a
    write), so what this holds is only where the writing happens.
    """
    adapter = _Adapter([[_found(confidence=EXACT)], [_found(confidence=EXACT)]])
    service = await _service(temp_db, adapter)
    await _a_box(service, name="StashDB")
    await _a_box(service, name="FansDB")
    await _files(temp_db, "a1")

    graded = 0
    honest = grade_of

    def failing(record: FoundRecord, *, length_ms: int | None, tolerance_ms: int) -> Grade:
        nonlocal graded
        graded += 1
        if graded == 2:
            raise RuntimeError("the second box's row will not write")
        return honest(record, length_ms=length_ms, tolerance_ms=tolerance_ms)

    monkeypatch.setattr(scanning_module, "grade_of", failing)

    with pytest.raises(RuntimeError):
        await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)

    # Read off the rows, not through `unasked`. That question intersects across every box, so it
    # answers "not asked yet" whether ONE row landed or none: a discriminator that cannot see
    # this fault at all, and it would pass with the write put back inside the loop.
    written = await temp_db.fetch_all("SELECT box_id FROM stash_box_scans")
    assert list(written) == [], "a file must not be left asked-down by one box and not the other"


class _Writer:
    """The file's writer, as far as a take-back uses it: it reads an answer as written and clears
    the columns one answer said, answering what it cleared."""

    def __init__(self) -> None:
        self.cleared: list[tuple[str, Mapping[str, object]]] = []

    async def read_as_written(self, values: Mapping[str, object]) -> Mapping[str, object]:
        return values

    async def take_back_fields(
        self, local_id: str, offered: Mapping[str, object], *, still_said: object = ()
    ) -> tuple[dict[str, object], list[str]]:
        self.cleared.append((local_id, offered))
        return {"title": "A Clip"}, ["https://first.example/scene"]


async def test_asked_again_an_applied_answer_that_changed_takes_back_what_it_wrote(
    temp_db: Database,
) -> None:
    """A question has written nothing, so an applied answer a re-ask turns back into one takes its
    rows and its columns off the file in the same write, with one History line whose Undo puts
    them back and the answer that was applied with them. An identical answer touches nothing."""
    from sift.kernel.ledger import Actor
    from sift.slices.stash_boxes.taken_back import RECEIPTS

    adapter = _Adapter(
        [[_found(remote_id="first")], [_found(remote_id="second")], [_found(remote_id="first")]]
    )
    service = await _service(temp_db, adapter)
    box = await _a_box(service)
    await _files(temp_db, "a1")
    await service.scan_one("a1", HASHES, A_KEY, length_ms=None, tolerance_ms=10_000)
    assert await service.settle("a1", box, applied=True) is True
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES ('p', 'Wren Halloway', 'w', 0)"
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source) VALUES ('a1', 'p', 'stash_box')"
    )
    writer = _Writer()

    await service.forget_answers(box)
    await service.scan_one(
        "a1",
        HASHES,
        A_KEY,
        length_ms=None,
        tolerance_ms=10_000,
        again=True,
        writer=writer,  # type: ignore[arg-type]
        actor=Actor.sift("stash"),
    )

    held = await service.match("a1", box)
    assert held is not None and (held.state, held.remote_id) == ("waiting", "second")
    people = await temp_db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = 'a1'")
    assert people == []
    assert [one for one, _ in writer.cleared] == ["a1"]
    [receipt] = await temp_db.fetch_all(
        "SELECT id, payload FROM workbench_decisions WHERE queue = ?", (RECEIPTS,)
    )
    put = await service.put_back_taken(str(receipt["id"]), json.loads(str(receipt["payload"])))
    assert (put.asset_id, dict(put.fields), list(put.links)) == (
        "a1",
        {"title": "A Clip"},
        ["https://first.example/scene"],
    )
    back = await service.match("a1", box)
    assert back is not None and (back.state, back.remote_id) == ("applied", "first")
    people = await temp_db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = 'a1'")
    assert [str(one["person_id"]) for one in people] == ["p"]

    # The same answer again leaves the decision, the rows and the columns where they are.
    await service.forget_answers(box)
    await service.scan_one(
        "a1",
        HASHES,
        A_KEY,
        length_ms=None,
        tolerance_ms=10_000,
        again=True,
        writer=writer,  # type: ignore[arg-type]
    )
    same = await service.match("a1", box)
    assert same is not None and (same.state, same.remote_id) == ("applied", "first")
    assert len(writer.cleared) == 1
