# SPDX-License-Identifier: AGPL-3.0-or-later
"""What runs when a file arrives, and what stops it.

The two faults these guard against both look like nothing happening, so neither shows up by using
the application:

* a job gated on the wrong question is still queued, claimed, run and recorded once per file, for
  ever, and its handler correctly does nothing, so there is no wrong output anywhere to notice;
* a folder answering differently is invisible until a file lands in that folder, and then it is
  invisible again, because the difference is a job that was not written.

So they are asserted on WHAT WAS ASKED FOR, which is the whole of the behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.importing.service import ImportPolicy
from sift.slices.importing.store import RootPreferences
from sift.testing.fixtures import LibraryRoot

#: A file the ingress gate accepts. Nothing here is about the gate, and a file that does not pass it
#: cannot become an asset at all.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


async def a_file_in(
    root: LibraryRoot, content: ContentStore, settings: Settings, name: str = "clip.mp4"
) -> str:
    """One real file in a real folder, indexed. Returns its asset id."""
    landed = root.path / name
    landed.write_bytes((CORPUS / "accepted.mp4").read_bytes())
    checked = verify_ingress(landed, origin=Origin.SCAN, settings=settings)
    ingested = await content.ingest(checked, root_id=root.id, rel_path=name)
    return ingested.asset.id


async def a_second_folder(database: Database, where: Path) -> LibraryRoot:
    """Another library root, written the way the `library_root` fixture writes the first one.

    A raw insert rather than the store that owns roots, for that fixture's own reason: nothing here
    is about how a root comes to exist, and the real path would bring overlap rules with it.
    """
    directory = where / "second"
    directory.mkdir(exist_ok=True)
    root_id = new_id()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "second", str(directory), 1_700_000_000),
    )
    return LibraryRoot(id=root_id, path=directory)


async def a_file_in_both(
    first: LibraryRoot,
    second: LibraryRoot,
    content: ContentStore,
    settings: Settings,
) -> str:
    """The same bytes in two folders: one asset, two locations, which is the case below.

    Not contrived: it is how a library that keeps a copy of a file in a second folder looks.
    """
    asset_id = await a_file_in(first, content, settings)
    again = await a_file_in(second, content, settings)
    assert again == asset_id, "the same bytes in two folders became two assets"
    return asset_id


pytestmark = pytest.mark.anyio


class Preferences:
    """The settings hub, as much of it as a gate reads."""

    def __init__(self, **values: Any) -> None:
        self.values = values
        self.asked: list[str] = []

    async def get_app(self, key: str) -> Any:
        self.asked.append(key)
        return self.values.get(key, False)

    async def get_user(self, user_id: str, key: str) -> Any:  # pragma: no cover - not read here
        raise AssertionError("the gate has no account to read for")


GATES = {
    "thumbnail": ("importing.generate", "performance.generate_thumbnails"),
    # A second kind of picture, gated separately, exactly as the composition root pairs them. One
    # kind alone cannot express the case that actually bites: SOME switches on and some off.
    "preview": ("importing.generate", "performance.generate_previews"),
    "face_scan": ("importing.identify", "faces.enabled", "performance.scan_faces_on_import"),
}

OVERRIDABLE = (
    "importing.generate",
    "importing.identify",
    "performance.generate_thumbnails",
    "performance.scan_faces_on_import",
)


def policy_over(
    preferences: Preferences, content: ContentStore, roots: RootPreferences
) -> ImportPolicy:
    return ImportPolicy(
        settings=preferences,
        content=content,
        roots=roots,
        gates=GATES,
        overridable=OVERRIDABLE,
    )


async def test_every_key_has_to_be_on(temp_db: Database, content_store: ContentStore) -> None:
    """One switch off is enough, whichever it is.

    Gated only on WHEN recognition runs and never on whether it runs at all, `face_scan` would be
    queued by every import with the feature off, a scan that opens by asking and returning.
    """
    roots = RootPreferences(temp_db)
    every = dict.fromkeys(GATES["face_scan"], True)

    for missing in GATES["face_scan"]:
        preferences = Preferences(**{**every, missing: False})
        allowed = await policy_over(preferences, content_store, roots).allows("face_scan")
        assert allowed is False, f"{missing} was off and the scan was queued anyway"

    preferences = Preferences(**every)
    assert await policy_over(preferences, content_store, roots).allows("face_scan") is True


async def test_a_job_nothing_governs_always_runs(
    temp_db: Database, content_store: ContentStore
) -> None:
    """Otherwise this becomes a list every new job has to be added to before it will run."""
    preferences = Preferences()
    policy = policy_over(preferences, content_store, RootPreferences(temp_db))

    assert await policy.allows("probe") is True
    assert preferences.asked == [], "an ungoverned job read a setting"


async def test_a_folder_can_answer_differently(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
) -> None:
    """A file in a folder that says no is not worked on, and the library still says yes."""
    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": True}
    )
    policy = policy_over(preferences, content_store, roots)

    asset_id = await a_file_in(library_root, content_store, settings)

    # The known positive. Without it the refusal below could be any other reason at all.
    assert await policy.allows("thumbnail", asset_id) is True

    await roots.set(library_root.id, {"performance.generate_thumbnails": False})

    assert await policy.allows("thumbnail", asset_id) is False, (
        "the folder said no and the work was queued anyway"
    )
    # And the library is untouched: another folder, and a job with no file at all, still say yes.
    assert await policy.allows("thumbnail") is True


async def test_a_folder_following_the_library_still_has_a_say(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """One folder says no, the other follows a library that says yes, and the work is done.

    Reading only the folders carrying a stored row, a folder following the library would contribute
    nothing and the single no would be the whole answer. That is the refusal `_on` says in writing
    it will not make (work asked for in one of the two places the file was put, refused because of
    the other): one file in two folders, one of them overriding the watermark read to off, and the
    read skipped.
    """
    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": True}
    )
    policy = policy_over(preferences, content_store, roots)
    second = await a_second_folder(temp_db, tmp_path)

    asset_id = await a_file_in_both(library_root, second, content_store, settings)

    assert await policy.allows("thumbnail", asset_id) is True, "the known positive"

    await roots.set(library_root.id, {"performance.generate_thumbnails": False})

    assert await policy.allows("thumbnail", asset_id) is True, (
        "a folder with no answer of its own was read as having no say, so one no refused the work"
    )


async def test_every_folder_saying_no_is_the_one_case_that_refuses(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """EITHER allows means nobody allows is the refusal, and the library is not asked at all.

    The second half is the shape of the rule rather than an optimisation: every folder has answered
    the question, so there is nothing left for the library's answer to decide.
    """
    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": True}
    )
    policy = policy_over(preferences, content_store, roots)
    second = await a_second_folder(temp_db, tmp_path)

    asset_id = await a_file_in_both(library_root, second, content_store, settings)
    for root in (library_root, second):
        await roots.set(root.id, {"performance.generate_thumbnails": False})

    assert await policy.allows("thumbnail", asset_id) is False, (
        "both folders said no and the work was queued anyway"
    )
    assert "performance.generate_thumbnails" not in preferences.asked, (
        "every folder had answered and the library was asked anyway"
    )


async def test_one_folder_saying_yes_carries_a_library_that_says_no(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """A folder is where work is asked for, so one folder asking is enough.

    The other direction from the case above, and it is what makes an override worth having on a
    library whose own switch is off.
    """
    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": False}
    )
    policy = policy_over(preferences, content_store, roots)
    second = await a_second_folder(temp_db, tmp_path)

    asset_id = await a_file_in_both(library_root, second, content_store, settings)

    assert await policy.allows("thumbnail", asset_id) is False, "the known positive"

    await roots.set(second.id, {"performance.generate_thumbnails": True})

    assert await policy.allows("thumbnail", asset_id) is True, (
        "the folder asked for the work and the library's no refused it"
    )


async def test_going_back_to_following_the_library(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
) -> None:
    """Clearing an override is not the same as storing the library's current answer.

    A stored copy looks identical today and stops looking identical the moment the library's answer
    moves: one follows and the other does not.
    """
    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": True}
    )
    policy = policy_over(preferences, content_store, roots)

    asset_id = await a_file_in(library_root, content_store, settings)

    await roots.set(library_root.id, {"performance.generate_thumbnails": False})
    await roots.set(library_root.id, {"performance.generate_thumbnails": None})

    assert await roots.for_root(library_root.id) == {}, "the override was stored, not removed"
    assert await policy.allows("thumbnail", asset_id) is True

    preferences.values["performance.generate_thumbnails"] = False
    assert await policy.allows("thumbnail", asset_id) is False, (
        "the folder kept an old copy of the answer instead of following the library"
    )


async def test_a_folder_cannot_answer_a_feature_s_own_consent(
    temp_db: Database, content_store: ContentStore
) -> None:
    """`faces.enabled` decides whether the feature exists, which is not a folder's question."""
    policy = policy_over(Preferences(), content_store, RootPreferences(temp_db))

    offered = policy.overridable()

    assert "faces.enabled" not in offered
    assert "performance.scan_faces_on_import" in offered, (
        "nothing about recognition can be answered per folder, which is not the intent either"
    )


# --- the parts a screen and a folder read, which the gate itself never touches ------------------


async def test_the_gates_are_readable_so_the_screen_can_draw_the_same_groups(
    temp_db: Database, content_store: ContentStore
) -> None:
    """The Importing screen groups its rows by which job each key governs, and it reads that from
    here rather than keeping a second copy: two lists of the same pairs would drift the first
    time a switch was added."""
    policy = policy_over(Preferences(), content_store, RootPreferences(temp_db))

    assert dict(policy.gates) == GATES
    # A mapping the caller cannot edit into a different answer than the one the jobs obey.
    assert set(policy.gates) == set(GATES)


async def test_a_file_in_no_folder_at_all_is_asked_about_nothing(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> None:
    """A file with no location has no folder to answer for it, so the library's own answer stands.

    Reachable rather than theoretical: a location is deleted when a file goes, and a job already
    queued for it runs afterwards, which is exactly when this returns nothing instead of raising.
    """
    policy = policy_over(Preferences(), content_store, RootPreferences(temp_db))
    asset_id = await a_file_in(library_root, content_store, settings)
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (asset_id,))

    assert await policy._overrides_for(asset_id) == []
    # And no asset at all is the same answer by a shorter road.
    assert await policy._overrides_for(None) == []


async def test_a_folder_can_be_put_back_to_following_the_library(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    """`clear` is the undo for the whole row rather than for one key, and it is what the screen's
    "follow the library" does, so a folder that once answered differently leaves nothing behind
    describing a switch that may since have been removed.

    A real root, because `root_import_prefs.root_id` is a foreign key: a preference against a
    folder that is not there is refused by the database, which is the row keeping itself honest.
    """
    prefs = RootPreferences(temp_db)
    await prefs.set(library_root.id, {"importing.generate": False, "importing.identify": False})
    assert await prefs.for_root(library_root.id) != {}

    await prefs.clear(library_root.id)

    assert await prefs.for_root(library_root.id) == {}


async def test_asking_about_several_folders_answers_for_every_one_of_them(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, tmp_path: Path
) -> None:
    """A folder that follows the library is in the answer with nothing in it, not missing.

    The contract the gate leans on: it counts the folders in the answer to know how many folders
    the file sits in, so a sparse answer (the rows as they come out of the table) would hide
    every folder that follows the library and the gate could not tell one from a folder it had not
    asked about. `for_root` has always answered `{}` for such a folder; this is the same question.
    """
    prefs = RootPreferences(temp_db)
    second = await a_second_folder(temp_db, tmp_path)
    await prefs.set(library_root.id, {"importing.generate": False})

    found = await prefs.for_roots([library_root.id, second.id])

    assert found == {library_root.id: {"importing.generate": False}, second.id: {}}
    assert list(found) == [library_root.id, second.id], "the order asked for was not kept"
    assert await prefs.for_roots([]) == {}


async def _distinct_file_in(
    root: LibraryRoot, content: ContentStore, settings: Settings, name: str, tail: bytes
) -> str:
    """A file with bytes of its own, read: `a_file_in`'s file with a tail, so each is its own
    asset, and marked probed because a count of what is lacking reads only files that were."""
    landed = root.path / name
    landed.write_bytes((CORPUS / "accepted.mp4").read_bytes() + tail)
    checked = verify_ingress(landed, origin=Origin.SCAN, settings=settings)
    ingested = await content.ingest(checked, root_id=root.id, rel_path=name)
    return ingested.asset.id


async def test_a_folder_that_refuses_takes_its_files_out_of_both_ends_of_the_bar(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """The count of what is lacking and the count of what is wanted both apply the EITHER rule.

    Three files: one only in the folder that refuses, one only in a folder following the library,
    and one in both. The first is wanted by nobody (`allows` refuses it), so neither count may
    include it; the other two are wanted, the third because a folder following a library yes still
    has a say.
    """
    from sift.kernel.content import Lack
    from sift.kernel.jobs.families import Family
    from sift.slices.importing.products import Product, ProductRegistry, count_lacking

    roots = RootPreferences(temp_db)
    preferences = Preferences(
        **{"importing.generate": True, "performance.generate_thumbnails": True}
    )
    policy = policy_over(preferences, content_store, roots)
    second = await a_second_folder(temp_db, tmp_path)

    refused = await _distinct_file_in(library_root, content_store, settings, "a.mp4", b"\x00a")
    followed = await _distinct_file_in(second, content_store, settings, "b.mp4", b"\x00b")
    both = await _distinct_file_in(library_root, content_store, settings, "c.mp4", b"\x00c")
    assert both == await _distinct_file_in(second, content_store, settings, "c.mp4", b"\x00c")
    assert len({refused, followed, both}) == 3
    await temp_db.execute("UPDATE assets SET probed_at = 1")

    async def on() -> bool:
        return True

    async def lack() -> Lack:
        return Lack(condition="1")

    async def nothing(_arg: object) -> Any:
        return set()

    registry = ProductRegistry(night_start=_hour, policy=policy)
    registry.register(
        Product(
            key="thumbnails",
            label="Thumbnails",
            help="",
            switched_on=on,
            lack=lack,
            lacking_among=nothing,
            build=nothing,
            family=Family.GENERATE,
            governed_by="thumbnail",
        )
    )

    # The known positive: with no folder refusing, all three are lacking and all three wanted.
    assert (await count_lacking(registry, content_store, ["thumbnails"], [])).each == {
        "thumbnails": 3
    }
    assert await policy.folder_term("thumbnail") is None

    await roots.set(library_root.id, {"performance.generate_thumbnails": False})
    assert await policy.allows("thumbnail", refused) is False
    assert await policy.allows("thumbnail", both) is True

    within = await registry.within(registry.get("thumbnails"))  # type: ignore[arg-type]
    assert (await count_lacking(registry, content_store, ["thumbnails"], [])).each == {
        "thumbnails": 2
    }
    assert await content_store.asset_count(within) == 2
    assert await content_store.asset_count() == 3
    # A job no folder can refuse is not narrowed at all.
    assert await policy.folder_term("probe") is None


async def _hour() -> str:
    return "23:00"


async def test_a_press_is_not_asked_whether_the_work_starts_on_its_own(
    temp_db: Database, content_store: ContentStore
) -> None:
    """With Generate or recognition set to "Only when I press it", a press of Run task, the Build
    or Run now is not refused with "switched off for this file". A When answers
    whether work starts ON ITS OWN, and a press has answered that; only what is made is asked."""
    import sift.main  # noqa: F401 (retires the old switches into the tasks' Whens)

    await temp_db.initialize_schema()
    preferences = Preferences(
        **{
            "importing.generate": False,
            "performance.generate_thumbnails": True,
            "importing.identify": False,
            "faces.enabled": False,
            "performance.scan_faces_on_import": False,
        }
    )
    policy = policy_over(preferences, content_store, RootPreferences(temp_db))

    assert await policy.allows("thumbnail") is False, "arrival still follows the When"
    assert await policy.allows("thumbnail", pressed=True) is True
    assert await policy.refused_by("face_scan", pressed=True) == "faces.enabled", (
        "and a press still asks whether the feature exists at all"
    )


#: The music fingerprint's gate, written as the composition root pairs it: Generate's master and
#: the music switch, both retired into Whens (`sift.wiring.tasks.retire_the_switches_into_whens`).
MUSIC_GATES = {"audio_fingerprint": ("importing.generate", "music.fingerprint")}


def music_policy(
    preferences: Preferences, content: ContentStore, roots: RootPreferences
) -> ImportPolicy:
    return ImportPolicy(
        settings=preferences,
        content=content,
        roots=roots,
        gates=MUSIC_GATES,
        overridable=("importing.generate", "music.fingerprint"),
    )


async def test_a_press_reads_music_whatever_the_switch_says(
    temp_db: Database, content_store: ContentStore
) -> None:
    """Music is "Only when I press it" out of the box, and Run now, the Build, a file's Run task
    and the card work whatever that says: the music key is a When, and a press has answered it."""
    import sift.main  # noqa: F401 (retires the old switches into the tasks' Whens)

    await temp_db.initialize_schema()
    preferences = Preferences(**{"importing.generate": True, "music.fingerprint": False})
    policy = music_policy(preferences, content_store, RootPreferences(temp_db))

    assert await policy.refused_by("audio_fingerprint") == "music.fingerprint", (
        "the known positive: unpressed, the switch refuses"
    )
    assert await policy.refused_by("audio_fingerprint", pressed=True) is None


async def test_the_count_of_what_is_left_counts_what_a_press_reads(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot
) -> None:
    """The Build's and the cards' counts narrow to the folders a PRESS would read: a folder's
    answer to a When is passed over as `refused_by` passes it over for a press, while its answer
    to what is made (Generate) still counts it out. A count that left out a folder the press then
    read would be a bar ending before the work did."""
    import sift.main  # noqa: F401 (retires the old switches into the tasks' Whens)

    roots = RootPreferences(temp_db)
    preferences = Preferences(**{"importing.generate": True, "music.fingerprint": False})
    policy = music_policy(preferences, content_store, roots)

    await roots.set(library_root.id, {"music.fingerprint": False})
    assert await policy.folder_term("audio_fingerprint") is None

    # Generate's master is a When as well, so a press passes that over too: a pressed music read
    # narrows to no folder at all. (The known positive, a key that says WHAT is made narrowing
    # the count, is the thumbnails test above.)
    await roots.set(library_root.id, {"importing.generate": False})
    assert await policy.folder_term("audio_fingerprint") is None


async def test_a_root_is_asked_before_a_file_is_in_it(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    tmp_path: Path,
) -> None:
    """At staging a file sits in no folder yet, so the DESTINATION is asked: its own answer where
    it gave one, the library's where it follows the library. Music is off unless the folder says
    on."""
    roots = RootPreferences(temp_db)
    preferences = Preferences(**{"importing.generate": True, "music.fingerprint": False})
    policy = music_policy(preferences, content_store, roots)
    silent = await a_second_folder(temp_db, tmp_path)

    await roots.set(library_root.id, {"music.fingerprint": True})

    assert await policy.allows_for_root("audio_fingerprint", library_root.id) is True
    assert await policy.allows_for_root("audio_fingerprint", silent.id) is False, (
        "a folder that says nothing follows a library that says off"
    )

    # And a folder saying no is heard over a library saying yes.
    preferences.values["music.fingerprint"] = True
    await roots.set(silent.id, {"music.fingerprint": False})
    assert await policy.allows_for_root("audio_fingerprint", silent.id) is False
    assert await policy.allows_for_root("audio_fingerprint", library_root.id) is True
    # A job nothing governs is never refused, and reads nothing to say so.
    asked = len(preferences.asked)
    assert await policy.allows_for_root("probe", silent.id) is True
    assert len(preferences.asked) == asked
