# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guest's answer: what it already has by the duplicate rule, who the people are, the screen.

The rule's numbers are never written here. Every test reads them from the duplicate feature's own
binding (`_filter_params`) at its default level, so "one bit past the rule" is one bit past the
number the Duplicates queue itself is reading, and a change there moves these with it.
"""

from __future__ import annotations

import random

import pytest

import sift.slices.stash_boxes.schema  # noqa: F401 (registers the stash-box link tables)
from sift.kernel.access import Repository, Role
from sift.kernel.content.perceptual import distance
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.dedup.matcher import DEFAULT_ACCURACY, DEFAULT_MAX_DURATION_GAP_MS
from sift.slices.dedup.service import _filter_params
from sift.slices.swap.diff import (
    Held,
    NearRule,
    PersonMatch,
    answer,
    assess,
    choose,
    held_from,
    match_people,
    screen,
    wanted,
)
from sift.slices.swap.models import Diff, Offer, OfferedFaces, OfferedFile, OfferedPerson
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: The rule in force on a device nobody has changed a setting on.
RULE = NearRule.from_bound(_filter_params(DEFAULT_ACCURACY, DEFAULT_MAX_DURATION_GAP_MS))

_BASE = 0x0123456789ABCDEF


def _hex(value: int) -> str:
    return f"{value:016x}"


def _flip(value: int, bits: int) -> int:
    """`value` with its lowest `bits` bits flipped: exactly that many bits apart."""
    return value ^ ((1 << bits) - 1)


def _file(key: str, **fields: object) -> OfferedFile:
    values: dict[str, object] = {
        "key": key,
        "size": 1_000,
        "identity": f"host-{key}",
        "kind": "video",
        "title": f"Clip {key}",
        "video_phash": _hex(_BASE),
        "fingerprint_version": 1,
        "duration_ms": 60_000,
    }
    values.update(fields)
    return OfferedFile.model_validate(values)


def _held(**fields: object) -> Held:
    values: dict[str, object] = {
        "asset_id": "mine",
        "media_type": "video",
        "identity": "guest-copy",
        "video_phash": _hex(_BASE),
        "fingerprint_version": 1,
        "duration_ms": 60_000,
    }
    values.update(fields)
    return Held(**values)  # type: ignore[arg-type]


def _offer(*files: OfferedFile, people: int = 0) -> Offer:
    return Offer(
        files=list(files),
        people=[OfferedPerson(name=f"Person {n}") for n in range(people)],
    )


def test_the_rule_is_the_duplicate_queues_own_binding() -> None:
    """The seam: the three numbers the queue binds, and nothing re-derived."""
    bound = _filter_params(DEFAULT_ACCURACY, DEFAULT_MAX_DURATION_GAP_MS)
    assert RULE.video_phash == bound["video_phash"]
    assert RULE.phash == bound["phash"]
    assert RULE.max_duration_gap_ms == bound["gap"]


def test_a_file_within_the_rule_is_held_and_one_bit_past_it_is_wanted() -> None:
    edge = RULE.video_phash
    near = _file("near", video_phash=_hex(_flip(_BASE, edge)))
    far = _file("far", video_phash=_hex(_flip(_BASE, edge + 1)))
    held = [_held(video_phash=_hex(_BASE))]
    assert distance(near.video_phash or "", held[0].video_phash or "") == edge
    found = wanted(_offer(near, far), held, RULE)
    assert found.wanted == ("far",)
    assert found.already["near"].reason == "near"
    assert found.already["near"].asset_id == "mine"


def test_the_exact_keys_confirm_and_never_decide() -> None:
    edge = RULE.video_phash
    # Same bytes AND the fingerprint agrees: held, and said to be the same file.
    same = _file("same", identity="guest-copy")
    # Same bytes by every exact key, but the fingerprint is past the rule: the keys decide nothing.
    keys_only = _file(
        "keys-only",
        identity="guest-copy",
        oshash="os",
        video_phash=_hex(_flip(_BASE, edge + 1)),
    )
    # The oshash and size agree, and there is no fingerprint to compare at all.
    no_print = _file("no-print", oshash="os", video_phash=None)
    held = [_held(oshash="os", size=1_000)]
    found = wanted(_offer(same, keys_only, no_print), held, RULE)
    assert found.already["same"].reason == "same"
    assert found.wanted == ("keys-only", "no-print")


def test_the_duration_gap_reads_as_the_queue_reads_it() -> None:
    gap = RULE.max_duration_gap_ms
    assert gap is not None
    inside = _file("inside", duration_ms=60_000 + gap)
    outside = _file("outside", duration_ms=60_000 + gap + 1)
    unknown = _file("unknown", duration_ms=None)
    found = wanted(_offer(inside, outside, unknown), [_held()], RULE)
    assert found.wanted == ("outside",)
    assert set(found.already) == {"inside", "unknown"}
    # A gap rule that is off compares no lengths.
    off = NearRule(video_phash=RULE.video_phash, phash=RULE.phash, max_duration_gap_ms=None)
    assert wanted(_offer(outside), [_held()], off).wanted == ()


def test_fingerprints_compare_only_within_one_generation() -> None:
    newer = _file("newer", fingerprint_version=2)
    unversioned = _file("unversioned", fingerprint_version=None)
    assert wanted(_offer(newer, unversioned), [_held()], RULE).wanted == ("newer", "unversioned")
    assert wanted(_offer(_file("x")), [_held(fingerprint_version=None)], RULE).wanted == ("x",)


def test_a_picture_is_compared_with_pictures_by_its_own_fingerprint() -> None:
    edge = RULE.phash
    picture = _file("pic", kind="image", video_phash=None, phash=_hex(_flip(_BASE, edge)))
    other = _file("other", kind="image", video_phash=None, phash=_hex(_flip(_BASE, edge + 1)))
    held_picture = _held(media_type="image", video_phash=None, phash=_hex(_BASE))
    # A video carrying the same number, and an id that would win a tie: it is not a picture.
    held_video = _held(asset_id="a-video", phash=_hex(_BASE))
    found = wanted(_offer(picture, other), [held_video, held_picture], RULE)
    assert found.wanted == ("other",)
    assert found.already["pic"].asset_id == "mine"
    gif = _file("gif", kind="gif", video_phash=None)
    assert wanted(_offer(gif), [_held()], RULE).wanted == ("gif",)


def test_the_block_search_misses_nothing_a_full_comparison_finds() -> None:
    """The index is only allowed to be faster, never different: every pair within the rule that a
    comparison of everything with everything finds, it finds."""
    rng = random.Random(61)
    rule = NearRule(video_phash=6, phash=6, max_duration_gap_ms=None)
    held = []
    for n in range(400):
        value = rng.getrandbits(64)
        held.append(_held(asset_id=f"h{n}", identity=f"g{n}", video_phash=_hex(value)))
    offered = []
    for n in range(300):
        source = int(held[rng.randrange(len(held))].video_phash or "0", 16)
        flips = rng.sample(range(64), rng.randrange(0, 10))
        value = source
        for bit in flips:
            value ^= 1 << bit
        offered.append(_file(f"o{n}", video_phash=_hex(value)))
    found = wanted(_offer(*offered), held, rule)
    for one in offered:
        apart = [distance(one.video_phash or "", candidate.video_phash or "") for candidate in held]
        best = min(64 if one_apart is None else one_apart for one_apart in apart)
        assert (one.key in found.already) == (best <= 6), one.key


def test_held_reads_the_kernels_fingerprint_rows() -> None:
    class Row:
        asset_id = "a"
        identity = "i"
        media_type = "video"
        phash = None
        video_phash = "00"
        duration_ms = 5
        fingerprint_version = 1
        oshash = "o"
        size_bytes = 9

    (one,) = held_from([Row()])
    assert (one.asset_id, one.oshash, one.size, one.fingerprint_version) == ("a", "o", 9, 1)


class _Choices:
    skipped: frozenset[int] = frozenset()
    unticked: frozenset[str] = frozenset()


def _person_file(key: str, people: list[int], size: int = 100) -> OfferedFile:
    return _file(key, people=people, size=size)


def test_up_to_ten_people_get_a_row_each_and_eleven_get_the_whole_offer() -> None:
    ten = _offer(*(_person_file(f"f{n}", [n], size=n + 1) for n in range(10)), people=10)
    drawn = screen(ten, wanted(ten, [], RULE), {})
    assert drawn.layout == "rows"
    assert [row.files for row in drawn.rows] == [1] * 10
    assert drawn.everyone == []

    eleven = _offer(*(_person_file(f"f{n}", [n], size=n + 1) for n in range(11)), people=11)
    drawn = screen(eleven, wanted(eleven, [], RULE), {})
    assert drawn.layout == "whole"
    assert [row.index for row in drawn.rows] == [10, 9, 8, 7, 6]
    assert len(drawn.everyone) == 11
    assert drawn.files == 11 and drawn.bytes == sum(range(1, 12))


def test_a_file_under_two_people_counts_under_both_and_once_in_the_total() -> None:
    offer = _offer(
        _person_file("both", [0, 1], size=10),
        _person_file("first", [0], size=5),
        _person_file("nobody", [], size=3),
        people=2,
    )
    drawn = screen(offer, wanted(offer, [], RULE), {})
    assert [(row.files, row.bytes) for row in drawn.rows] == [(2, 15), (1, 10)]
    assert drawn.files == 3 and drawn.bytes == 18
    assert drawn.shared == 1
    assert (drawn.unfiled_files, drawn.unfiled_bytes) == (1, 3)


def test_a_row_says_how_many_confirmed_faces_the_host_has_where_it_said() -> None:
    offer = Offer(
        people=[
            OfferedPerson(
                name="Juno Pellerin",
                faces=OfferedFaces(recognizer="m", dimension=2, faces=[], confirmed=210),
            ),
            OfferedPerson(name="Odo Venne", faces=OfferedFaces(recognizer="m", dimension=2)),
            OfferedPerson(name="Ilsa Moor"),
        ]
    )
    drawn = screen(offer, wanted(offer, [], RULE), {})
    assert [(row.name, row.confirmed) for row in drawn.rows] == [
        ("Juno Pellerin", 210),
        ("Odo Venne", None),
        ("Ilsa Moor", None),
    ]


def test_a_held_file_is_unticked_and_says_why() -> None:
    offer = _offer(
        _file("here", people=[0]),
        _file("new", people=[0], video_phash=_hex(~_BASE & (2**64 - 1))),
        people=1,
    )
    drawn = screen(offer, wanted(offer, [_held()], RULE), {})
    assert [(one.key, one.reason) for one in drawn.held] == [("here", "near")]
    assert (drawn.rows[0].files, drawn.rows[0].held) == (1, 1)


def test_a_skip_unwants_a_persons_files_unless_a_taken_person_has_them() -> None:
    offer = _offer(
        _person_file("only-skipped", [0]),
        _person_file("shared", [0, 1]),
        _person_file("loose", []),
        _person_file("unticked", [1]),
        people=2,
    )
    judged = wanted(offer, [], RULE)
    kept = choose(offer, judged, skipped={0}, unticked={"unticked"})
    assert kept == ["shared", "loose"]
    frame = answer(offer, judged, {1: PersonMatch(person_id="here")}, skipped={0})
    assert frame.people == {0: None, 1: "here"}
    assert frame.wanted == ["shared", "loose", "unticked"]


async def test_people_match_by_box_then_name_then_alias(
    temp_db: Database, access: Repository
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    db = temp_db
    await db.execute(
        "INSERT INTO library_roots (id, name, abs_path, kind, created_at)"
        " VALUES ('r1', 'root', '/library/r1', 'local', ?)",
        (_EPOCH,),
    )
    await db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
        " VALUES ('f1', 'r1', NULL, 'shoot', 'shoot')"
    )
    people = {
        "boxed": "Priya Sandoval",
        "named": "Orla Tennant",
        "aliased": "Tobias Ellery",
        "twin_a": "Nadia Vance",
        "twin_b": "Sonia Vance",
    }
    ids = {key: new_id() for key in people}
    for key, name in people.items():
        asset_id = new_id()
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset_id, f"digest-{asset_id}", _EPOCH),
        )
        await db.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
            " status, first_seen_at, last_seen_at)"
            " VALUES (?, ?, 'r1', 'f1', ?, ?, 'present', ?, ?)",
            (new_id(), asset_id, f"shoot/{key}.mp4", f"{key}.mp4", _EPOCH, _EPOCH),
        )
        await db.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            (ids[key], name, name.lower(), _EPOCH),
        )
        await db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset_id, ids[key])
        )
    for person, alias in (
        ("aliased", "Rafe Underhill"),
        ("twin_a", "Marit Halvorsen"),
        ("twin_b", "Marit Halvorsen"),
    ):
        await db.execute(
            "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
            (new_id(), ids[person], alias),
        )
    await db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
        " VALUES ('b1', 'box', 'https://box.test/g', 0)"
    )
    await db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, 'b1', 'remote-1', '{}', ?)",
        (ids["boxed"], _EPOCH),
    )
    offered = [
        # The box id wins over a name that names somebody else.
        OfferedPerson(name="Orla Tennant", boxes=["remote-1"]),
        # The name, in another case.
        OfferedPerson(name="ORLA TENNANT"),
        # An alias of the offered person that is this device's person's alias.
        OfferedPerson(name="Juno Pellerin", aliases=["rafe underhill"]),
        # Nobody here.
        OfferedPerson(name="Zev Ondrasik", boxes=["remote-unknown"]),
        # Two people answer to it: nobody is guessed.
        OfferedPerson(name="Zelda Fitzgerald", aliases=["Marit Halvorsen"]),
    ]
    found = await match_people(db, access, admin, offered)
    # The session's one call: the screen names the matches, and the frame carries only ids.
    drawn, reply = await assess(db, access, admin, Offer(people=offered), held=[], rule=RULE)
    assert drawn.rows[0].match_name == "Priya Sandoval"
    assert reply(_Choices()).people[0] == ids["boxed"]
    assert (found[0].person_id, found[0].by) == (ids["boxed"], "box")
    assert (found[1].person_id, found[1].by) == (ids["named"], "name")
    assert (found[2].person_id, found[2].by) == (ids["aliased"], "alias")
    assert found[2].name == "Tobias Ellery"
    assert found[3] == PersonMatch()
    assert found[4] == PersonMatch()


def test_a_rule_bound_with_anything_but_whole_numbers_is_refused() -> None:
    """The binding is the duplicate queue's; a shape it never binds is a wiring fault, said at
    once rather than compared as a float of bits."""
    with pytest.raises(TypeError, match="whole numbers of bits"):
        NearRule.from_bound({"video_phash": 6.5, "phash": 6, "gap": None})
    with pytest.raises(TypeError, match="milliseconds or nothing"):
        NearRule.from_bound({"video_phash": 6, "phash": 6, "gap": "5s"})
    assert NearRule.from_bound({"video_phash": 6, "phash": 5, "gap": None}).phash == 5


def test_a_copy_here_whose_fingerprint_is_not_hex_is_compared_with_nothing() -> None:
    """A row written by something else, holding a fingerprint no reader can split: it is never a
    match, so the offered file is wanted rather than the comparison failing."""
    garbled = _held(video_phash="zzzzzzzzzzzzzzzz")
    assert wanted(_offer(_file("x")), [garbled], RULE).wanted == ("x",)


def test_of_several_copies_here_the_nearest_is_the_one_named() -> None:
    """Three copies within the rule, the nearest first: the ones after it are looked at and are
    not better, so the answer stays the nearest."""
    held = [
        _held(asset_id="nearest", identity="g0", video_phash=_hex(_BASE)),
        _held(asset_id="further", identity="g1", video_phash=_hex(_flip(_BASE, 1))),
        _held(asset_id="furthest", identity="g2", video_phash=_hex(_flip(_BASE, 2))),
    ]

    found = wanted(_offer(_file("x")), held, RULE)

    assert found.already["x"].asset_id == "nearest"


async def test_a_person_this_viewer_may_not_see_is_not_matched_by_their_name(
    temp_db: Database, access: Repository
) -> None:
    """The name answers here, to somebody this viewer is not shown: that is nobody, and the person
    arrives as new rather than being filed under a person the viewer cannot see."""
    guest = await create_user(temp_db, Role.GUEST)
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (new_id(), "Orla Tennant", "orla tennant", _EPOCH),
    )

    found = await match_people(temp_db, access, guest, [OfferedPerson(name="Orla Tennant")])

    assert found[0] == PersonMatch()


def test_the_wire_copy_of_an_answer_carries_no_local_id() -> None:
    """Every matched person stays on this side: the copy sent is the wanted keys alone, and an
    answer from an install that still sends its matches is read and its matches ignored."""
    local = {0: "p-local-one", 1: None, 2: "p-local-two"}
    answer_here = Diff(wanted=["a", "b"], people=local)

    sent = answer_here.for_the_other_side()

    assert sent.wanted == ["a", "b"]
    assert sent.people == {}
    wire = sent.model_dump_json()
    assert "p-local-one" not in wire and "p-local-two" not in wire
    assert answer_here.people == local
    older = Diff.model_validate({"wanted": ["a"], "people": {"0": "p-theirs"}})
    assert older.wanted == ["a"]
    assert Diff.model_validate({"wanted": ["a"]}).people == {}


async def test_do_not_swap_here_shapes_nothing_the_other_side_reads(
    temp_db: Database, access: Repository
) -> None:
    """A file marked "Do not swap" here is not held for the answer, so the offered copy of it is
    wanted as though nothing were here; every person is matched for the landing and none is sent.
    The unmarked twins beside them prove the same answer reaches what is not marked."""
    admin = await create_user(temp_db, Role.ADMIN)
    names = {"marked": "Orla Tennant", "plain": "Priya Sandoval"}
    ids = {key: new_id() for key in names}
    for key, name in names.items():
        await temp_db.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            (ids[key], name, name.lower(), _EPOCH),
        )
        asset_id = f"file-{key}"
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset_id, f"digest-{key}", _EPOCH),
        )
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset_id, ids[key])
        )
    await temp_db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (ids["marked"],))
    other = _BASE ^ 0xFFFF_0000_0000_0000
    held = [
        _held(asset_id="file-marked", video_phash=_hex(_BASE)),
        _held(asset_id="file-plain", video_phash=_hex(other)),
    ]
    offer = Offer(
        files=[_file("a", video_phash=_hex(_BASE)), _file("b", video_phash=_hex(other))],
        people=[OfferedPerson(name="Orla Tennant"), OfferedPerson(name="Priya Sandoval")],
    )

    drawn, reply = await assess(temp_db, access, admin, offer, held=held, rule=RULE)
    kept = reply(_Choices())
    sent = kept.for_the_other_side()

    # The marked person's file is not held: its copy is wanted. The plain one's copy is not.
    assert sent.wanted == ["a"]
    assert [one.key for one in drawn.held] == ["b"]
    # The landing keeps the real matches; the answer sent names nobody, marked or not.
    assert kept.people == {0: ids["marked"], 1: ids["plain"]}
    assert sent.people == {}
    wire = sent.model_dump_json()
    assert ids["marked"] not in wire and ids["plain"] not in wire
