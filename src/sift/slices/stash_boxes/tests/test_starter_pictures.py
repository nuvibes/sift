# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's stash-box pictures, handed to the face feature as bytes through this slice's door.

The face feature files up to five of them as STARTER references (see its `file_starters`). What
this slice owes it: every usable picture a box offered, largest first, kept with the link so it is
never asked for twice, and nothing at all for somebody kept local.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sift.kernel.records import FoundRecord, SourceLink, Subject
from sift.slices.stash_boxes.adapter import (
    StashBoxUnreachable,
    _picture,
    _pictures,
    as_json,
    from_json,
)
from sift.slices.stash_boxes.entities import EntityEnricher, StarterPictures


def test_every_usable_picture_is_kept_largest_first() -> None:
    images = [
        {"url": "https://box.test/small", "width": 100, "height": 100},
        {"url": "https://box.test/broken", "width": -1, "height": -1},
        {"url": "https://box.test/large", "width": 900, "height": 1200},
        {"url": "https://box.test/middle", "width": 400, "height": 600},
    ]

    assert _pictures(images) == (
        "https://box.test/large",
        "https://box.test/middle",
        "https://box.test/small",
        # A picture the box gives no size for (a vector logo reads as -1) is ranked last rather
        # than dropped, so a Site whose only picture is one still has a picture.
        "https://box.test/broken",
    )
    # And the one picture a chooser draws is the first of them: one reading of one reply.
    assert _picture(images) == "https://box.test/large"


def test_the_list_is_kept_with_the_record() -> None:
    record = FoundRecord(
        source_id="box",
        remote_id="1",
        subject=Subject.PERSON,
        name="Nadia Vance",
        image_url="https://box.test/large",
        pictures=("https://box.test/large", "https://box.test/middle"),
    )

    (back,) = from_json(as_json([record]))

    assert back.pictures == record.pictures


@dataclass
class _Door:
    """The stash-box service, standing still: two links, one kept before the list existed."""

    kept_local_people: set[str] = field(default_factory=set)
    asked_again: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)

    async def kept_local(self, subject: Subject, local_id: str) -> bool:
        return local_id in self.kept_local_people

    async def links_of(self, subject: Subject, local_id: str) -> list[SourceLink]:
        def link(box: str, pictures: tuple[str, ...], image: str) -> SourceLink:
            record = FoundRecord(
                source_id=box,
                remote_id="1",
                subject=Subject.PERSON,
                name="Nadia Vance",
                image_url=image,
                pictures=pictures,
            )
            return SourceLink(box, box.upper(), "1", record, 0)

        return [
            link("fansdb", ("https://f/1", "https://f/2"), "https://f/1"),
            link("stashdb", (), "https://s/1"),
        ]

    async def refresh(
        self, subject: Subject, local_id: str, box_id: str, master_key: bytes | None
    ) -> SourceLink | None:
        self.asked_again.append(box_id)
        return None

    async def picture(
        self, box_id: str, url: str, master_key: bytes | None
    ) -> tuple[bytes, str] | None:
        self.fetched.append(url)
        return url.encode(), "image/jpeg"


async def test_pictures_come_from_every_link_and_an_old_link_is_asked_again_once() -> None:
    door = _Door()
    pictures = StarterPictures(door)  # type: ignore[arg-type]

    got = await pictures.pictures_of("p", b"key", most=5)

    assert got == [
        ("FANSDB", b"https://f/1"),
        ("FANSDB", b"https://f/2"),
        ("STASHDB", b"https://s/1"),
    ]
    assert door.asked_again == ["stashdb"]


async def test_no_more_than_asked_for_is_fetched() -> None:
    door = _Door()

    got = await StarterPictures(door).pictures_of("p", b"key", most=1)  # type: ignore[arg-type]

    assert got is not None
    assert [source for source, _ in got] == ["FANSDB"]
    assert door.fetched == ["https://f/1"]


@pytest.mark.parametrize("most", [0, 5])
async def test_somebody_kept_local_is_asked_about_nowhere(most: int) -> None:
    door = _Door(kept_local_people={"p"})

    # None, not an empty list: she was not asked about, which says nothing about her pictures.
    assert await StarterPictures(door).pictures_of("p", b"key", most=most) is None  # type: ignore[arg-type]
    assert door.fetched == [] and door.asked_again == []


@dataclass
class _Bare(_Door):
    """One link that kept no picture and no list; asking again answers, or cannot be done."""

    unreachable: bool = False

    async def links_of(self, subject: Subject, local_id: str) -> list[SourceLink]:
        record = FoundRecord(
            source_id="fansdb", remote_id="1", subject=Subject.PERSON, name="Nadia Vance"
        )
        return [SourceLink("fansdb", "FANSDB", "1", record, 0)]

    async def refresh(
        self, subject: Subject, local_id: str, box_id: str, master_key: bytes | None
    ) -> SourceLink | None:
        self.asked_again.append(box_id)
        if self.unreachable:
            raise StashBoxUnreachable("FansDB could not be asked.")
        return None


async def test_every_box_answering_with_no_picture_is_an_empty_list_and_not_asking_is_none() -> (
    None
):
    """ "Nothing there" and "could not ask" are two answers, so the face feature can remember the
    first and stop offering starters for People no box holds a picture of."""
    answered = _Bare()
    unreachable = _Bare(unreachable=True)

    assert await StarterPictures(answered).pictures_of("p", b"key", most=5) == []  # type: ignore[arg-type]
    assert await StarterPictures(unreachable).pictures_of("p", b"key", most=5) is None  # type: ignore[arg-type]
    assert answered.asked_again == unreachable.asked_again == ["fansdb"]


async def test_a_picture_that_will_not_come_is_not_asking() -> None:
    @dataclass
    class _Refusing(_Door):
        async def picture(
            self, box_id: str, url: str, master_key: bytes | None
        ) -> tuple[bytes, str] | None:
            self.fetched.append(url)
            return None

    door = _Refusing()

    assert await StarterPictures(door).pictures_of("p", b"key", most=5) is None  # type: ignore[arg-type]
    assert door.fetched == ["https://f/1", "https://f/2", "https://s/1"]


# --- the link that asks for them ------------------------------------------------------------------


class _Heard:
    """The face feature's ear, standing still: who it was told was linked."""

    def __init__(self) -> None:
        self.people: list[str] = []

    async def linked(self, person_id: str) -> None:
        self.people.append(person_id)


def _heard_by(heard: _Heard, service: object) -> EntityEnricher:
    from sift.slices.stash_boxes.tests.test_entities import (
        _Access,
        _Covers,
        _Enricher,
        _Naming,
        _Settings,
    )

    return EntityEnricher(
        service,  # type: ignore[arg-type]
        _Access(),  # type: ignore[arg-type]
        _Enricher(),  # type: ignore[arg-type]
        _Settings(),  # type: ignore[arg-type]
        _Covers(),  # type: ignore[arg-type]
        naming=_Naming(),  # type: ignore[arg-type]
        recognition=heard,  # type: ignore[arg-type]
    )


async def test_a_person_linked_by_name_is_told_to_the_face_feature_once() -> None:
    from sift.slices.stash_boxes.tests.test_entities import _answer, _found, _link, _Service

    heard = _Heard()

    await _heard_by(heard, _Service(answers=[_answer(_found())], linked=_link())).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert heard.people == ["p1"]


async def test_a_person_linked_by_the_boxs_own_id_is_told_too() -> None:
    from sift.slices.stash_boxes.tests.test_entities import _link, _Service

    heard = _Heard()

    await _heard_by(heard, _Service(linked=_link())).link_known(
        Subject.PERSON, "p1", "b1", "r-Jane", None
    )

    assert heard.people == ["p1"]


async def test_a_site_or_a_tag_is_nobody_to_take_pictures_of() -> None:
    heard = _Heard()

    await _heard_by(heard, object()).person_linked(Subject.SITE, "s1")

    assert heard.people == []


# --- the list of people, a list asked for again, and a box gone mid-fetch -------------------------


async def test_the_people_offered_starters_are_the_ones_the_door_says_are_linked() -> None:
    asked: list[bool] = []

    class _Linked(_Door):
        async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]:
            asked.append(with_picture_lists)
            return ["p1", "p2"]

    pictures = StarterPictures(_Linked())  # type: ignore[arg-type]

    assert await pictures.linked_people(with_picture_lists=True) == ["p1", "p2"]
    assert asked == [True], "the question is passed on as it was asked"


async def test_a_link_asked_again_takes_its_pictures_from_the_fresh_record() -> None:
    """The old link kept one picture and no list; asking again brings the list, and that is what
    is fetched, not the one picture the old record kept."""

    @dataclass
    class _Refreshed(_Bare):
        async def refresh(
            self, subject: Subject, local_id: str, box_id: str, master_key: bytes | None
        ) -> SourceLink | None:
            self.asked_again.append(box_id)
            record = FoundRecord(
                source_id=box_id,
                remote_id="1",
                subject=Subject.PERSON,
                name="Nadia Vance",
                pictures=("https://f/fresh-1", "https://f/fresh-2"),
            )
            return SourceLink(box_id, "FANSDB", "1", record, 0)

    door = _Refreshed()

    got = await StarterPictures(door).pictures_of("p", b"key", most=5)  # type: ignore[arg-type]

    assert got == [("FANSDB", b"https://f/fresh-1"), ("FANSDB", b"https://f/fresh-2")]


async def test_a_box_gone_while_its_pictures_are_fetched_costs_its_pictures_not_the_rest() -> None:
    @dataclass
    class _GoneMidway(_Door):
        async def picture(
            self, box_id: str, url: str, master_key: bytes | None
        ) -> tuple[bytes, str] | None:
            if box_id == "stashdb":
                raise StashBoxUnreachable("StashDB could not be asked.")
            return await super().picture(box_id, url, master_key)

    door = _GoneMidway()

    got = await StarterPictures(door).pictures_of("p", b"key", most=5)  # type: ignore[arg-type]

    assert got == [("FANSDB", b"https://f/1"), ("FANSDB", b"https://f/2")]
