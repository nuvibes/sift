# SPDX-License-Identifier: AGPL-3.0-or-later
"""Teaching the library what a Site's own username number is called, out of the pictures.

The filename pass can read a username NUMBER off a name and cannot read what that username is
CALLED. Thousands of files in a library can carry that shape under a handful of numbers, and
where nothing in Sift knows any of those numbers, every one of those files sits under no site at
all.

What this reads is two fields of a handful of one number's pictures, once. The rules it is held to
here are the whole of it: two pictures have to agree, one teaches nothing, a disagreement teaches
nothing, and everything in the container except those two fields is out of reach by construction.
"""

from __future__ import annotations

import struct
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

# Imported for its side effect: the workbench slice registers the ledger's table, which a username
# arriving is now written to (`catalog._seed_username_on`). `temp_db.initialize_schema()` creates
# only what is registered, so without this the table would depend on import order in the worker.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import (
    MADE_BY_A_PERSON,
    Repository,
    Viewer,
    seed_site_username,
    username_number,
)
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.suggestions.metadata import (
    FIELDS,
    Fields,
    agreed_name,
    names_the_site,
    probe_args,
    read_fields,
    understand,
    username_in_address,
)
from sift.slices.suggestions.queue import FiledFromFilenamesQueue
from sift.slices.suggestions.service import PictureFields, SuggestionService
from sift.slices.suggestions.settings import READ_METADATA_KEY
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import (
    CORPUS,
    FakeFaces,
    FakePreferences,
    Library,
)
from sift.slices.workbench.store import Store as DecisionStore

#: One number's files, as the desktop downloader names them. The username number is the tail.
NUMBER = "31415926"
HANDLE = "orla_fennimore"


def _named(post: str) -> str:
    return f"Unsorted/2023-07-1{post} 12.37.1{post} 271828182845904508{post}_{NUMBER}.jpg"


class FakePictures:
    """The two fields of a file, answered from what a test planted against its id.

    The seam and not the reader: what crosses it is an asset id and two strings, so a test about the
    RULE does not have to write EXIF into a JPEG. The reader itself is proved against a real file by
    `TestOnlyTwoFieldsAreEverRead`, which is where the refusal actually lives.
    """

    def __init__(self, by_name: dict[str, Fields] | None = None) -> None:
        self.by_name = by_name or {}
        self.asked: list[str] = []
        self.names: dict[str, str] = {}

    async def read(self, asset_id: str) -> Fields:
        self.asked.append(asset_id)
        return self.by_name.get(self.names.get(asset_id, ""), Fields())

    def seam(self) -> PictureFields:
        return PictureFields(read=self.read)


@pytest.fixture
async def decisions(temp_db: Database) -> DecisionStore:
    return DecisionStore(temp_db)


@pytest.fixture
def pictures() -> FakePictures:
    return FakePictures()


@pytest.fixture
async def reading(
    store: Store,
    access: Repository,
    faces: FakeFaces,
    preferences: FakePreferences,
    decisions: DecisionStore,
    pictures: FakePictures,
) -> SuggestionService:
    """The service as the application builds it, with a way to open a picture."""
    return SuggestionService(
        store=store,
        access=access,
        faces=faces,
        preferences=preferences,
        recorder=decisions,
        pictures=pictures.seam(),
    )


def _agreeing(name: str = HANDLE, post: str = "B-Exampl4Ka") -> Fields:
    """What one downloaded picture carries: the username and the post's address."""
    return Fields(artist=name, description=f"https://www.instagram.com/p/{post}/")


async def _plant(
    add_file: Callable[..., Any],
    library: Library,
    pictures: FakePictures,
    said: Sequence[Fields],
) -> list[str]:
    """One file per reading, each named with one username number and carrying that reading."""
    landed = []
    for at, fields in enumerate(said):
        one = await add_file(library, _named(str(at)), source="accepted.jpg")
        pictures.names[one.asset.id] = f"file-{at}"
        pictures.by_name[f"file-{at}"] = fields
        landed.append(one.asset.id)
    return landed


class TestHowManyPicturesHaveToAgree:
    """Two, and the two that are not two."""

    async def test_two_agreeing_pictures_name_the_number_and_file_its_files(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The whole of it: a username is made, the number goes on it, the files land under it."""
        landed = await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await reading.file_from_filenames() == 2

        row = await temp_db.fetch_one(
            "SELECT a.id AS id, a.name AS name, p.name AS site FROM usernames a"
            " JOIN sites p ON p.id = a.site_id",
            (),
        )
        assert row is not None
        assert str(row["name"]) == HANDLE
        assert str(row["site"]) == "Instagram"
        # The number, and how it was come by, on the username itself.
        number = await username_number(temp_db, str(row["id"]))
        assert (number.number, number.via, number.agreed) == (NUMBER, "metadata", 2)
        # And the filings wear the pass's second word, so `enriched:metadata` finds exactly these.
        filed = await temp_db.fetch_all(
            "SELECT asset_id, source FROM asset_usernames ORDER BY asset_id", ()
        )
        assert sorted(str(one["asset_id"]) for one in filed) == sorted(landed)
        assert {str(one["source"]) for one in filed} == {"metadata"}

    async def test_one_picture_teaches_nothing(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """One file is one file's opinion. Nothing is written and nothing is invented."""
        await _plant(add_file, library, pictures, [_agreeing()])

        assert await reading.file_from_filenames() == 0
        assert await temp_db.fetch_all("SELECT id FROM usernames", ()) == []
        assert await temp_db.fetch_all("SELECT id FROM sites", ()) == []

    async def test_a_disagreement_teaches_nothing(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """Not the commonest answer and not a majority: a column filled once is not a vote.

        Three pictures, two of which agree (which is exactly the shape a majority rule would
        have written), and the third disagreeing is enough to write nothing.
        """
        await _plant(
            add_file,
            library,
            pictures,
            [_agreeing(), _agreeing(), _agreeing(name="someone_else")],
        )

        assert await reading.file_from_filenames() == 0
        assert await temp_db.fetch_all("SELECT id FROM usernames", ()) == []

    async def test_a_picture_that_says_nothing_is_not_a_dissenter(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """Most pictures in any library carry none of this, so silence cannot count against.

        A video sitting beside the pictures of the same username is the real case: it answers with
        both fields empty, and it must not stop the two that spoke.
        """
        await _plant(add_file, library, pictures, [_agreeing(), Fields(), _agreeing()])

        assert await reading.file_from_filenames() == 3

    async def test_an_address_on_another_site_corroborates_nothing(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The shape said Instagram. Two fields agreeing with each other is one fact, not two."""
        elsewhere = Fields(artist=HANDLE, description="https://example.test/p/abc/")
        await _plant(add_file, library, pictures, [elsewhere, elsewhere])

        assert await reading.file_from_filenames() == 0

    async def test_an_address_naming_a_different_handle_refuses(self) -> None:
        """The one shape whose address names a username: a profile address rather than a post's.

        This is the rare case. Where the address does name a username it has to be the
        same username, because two of a file's own fields disagreeing is the strongest reason there
        is to write nothing down.
        """
        profile = Fields(artist=HANDLE, description="https://www.instagram.com/somebody_else")
        assert agreed_name([profile, profile], "Instagram") is None

        agrees = Fields(artist=HANDLE, description=f"https://www.instagram.com/{HANDLE}")
        assert agreed_name([agrees, agrees], "Instagram") == (HANDLE, 2)


class TestOnlyTwoFieldsAreEverRead:
    """The refusal, against a real file with a real GPS block spliced into it.

    This is the test that matters most in the module. A reader that took a container's tag
    dictionary whole would carry a phone's coordinates into a database with no column for them,
    and some videos really do carry a `location` tag.
    """

    def test_the_field_list_is_the_argument_handed_to_the_tool(self, settings: Settings) -> None:
        """Named in ffprobe's own arguments, so nothing else is ever returned to be filtered."""
        argv = probe_args(Path("picture.jpg"), settings=settings)
        assert "frame_tags=Artist,ImageDescription" in argv
        assert FIELDS == ("Artist", "ImageDescription")

    def test_it_keeps_only_the_two_it_asked_for(self) -> None:
        """And filters again on the way in, so a tool that ignored the argument cannot widen it."""
        found = understand(
            {
                "frames": [
                    {
                        "tags": {
                            "Artist": HANDLE,
                            "ImageDescription": "https://www.instagram.com/p/AAA/",
                            "GPSLatitudeRef": "N",
                            "location": "+51.5000+000.1167/",
                        }
                    }
                ]
            }
        )
        assert found == Fields(artist=HANDLE, description="https://www.instagram.com/p/AAA/")

    @pytest.mark.parametrize(
        "said",
        [
            {},
            {"frames": []},
            {"frames": ["not a frame"]},
            {"frames": [{"tags": ["Artist", HANDLE]}]},
            {"frames": [{}]},
        ],
    )
    def test_an_answer_in_any_other_shape_teaches_nothing(self, said: dict[str, object]) -> None:
        """Most pictures carry no fields at all, so an empty answer is the ordinary one, and a
        tool answering in a shape of its own is read the same way rather than raising."""
        assert understand(said) == Fields()

    async def test_a_picture_ffprobe_will_not_open_teaches_nothing(
        self, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A dead share or a container ffprobe refuses is "this picture teaches nothing": raising
        would turn one unreadable file into a failed pass."""
        from sift.kernel.media import FFmpegError
        from sift.slices.suggestions import metadata

        async def refuses(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise FFmpegError("Invalid data found when processing input")

        monkeypatch.setattr(metadata, "run_json", refuses)

        assert await read_fields(tmp_path / "gone.jpg", settings=settings) == Fields()

    @pytest.mark.integration
    async def test_a_planted_location_never_reaches_the_reader(
        self, tmp_path: Path, settings: Settings
    ) -> None:
        """A real JPEG, a real EXIF block, real coordinates in it, and they do not come back.

        The fixture is checked for carrying what is being looked for first, because a scrubbing
        test fed a clean file agrees happily for ever. That is the guard `test_metadata.py` makes
        for the thumbnail encoder, made here for the reader.
        """
        picture = _tagged_jpeg(tmp_path / "tagged.jpg")
        whole = subprocess.run(
            [
                settings.ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-read_intervals",
                "%+#1",
                "-show_frames",
                "-of",
                "json",
                str(picture),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "GPSLatitudeRef" in whole.stdout, "the fixture carries no location to refuse"

        read = await read_fields(picture, settings=settings)

        assert read == Fields(artist=HANDLE, description=f"https://www.instagram.com/{HANDLE}")
        assert "51" not in read.artist + read.description


class TestANameAlreadyTaken:
    async def test_a_name_that_already_carries_another_id_learns_nothing_and_files_nothing(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The pictures name a username this library already holds under a different ID. A
        number does not change, so this pass did not learn it, and saying it did would file
        pictures under a username whose ID says they are somebody else's. The number waits."""
        _, known = await seed_site_username(
            temp_db, site="Instagram", name=HANDLE, number="99999999", made=MADE_BY_A_PERSON
        )
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await reading.file_from_filenames() == 0

        assert (await username_number(temp_db, known)).number == "99999999"
        assert await temp_db.fetch_all("SELECT asset_id FROM asset_usernames", ()) == []
        assert await reading.numbers_waiting() == 1


class TestWithNoRecord:
    async def test_a_service_that_records_nothing_still_learns_and_files(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        preferences: FakePreferences,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """A pass that runs for nobody makes no decisions: the number is learned and the files
        filed, and only the receipts are absent."""
        quiet = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=preferences,
            pictures=pictures.seam(),
        )
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await quiet.file_from_filenames() == 2

        row = await temp_db.fetch_one("SELECT id FROM usernames", ())
        assert row is not None
        assert (await username_number(temp_db, str(row["id"]))).number == NUMBER
        receipts = await temp_db.fetch_all(
            "SELECT id FROM workbench_decisions WHERE queue = 'filenames'", ()
        )
        assert receipts == []


class TestTheHistoryLine:
    """What one file says happened to it, in the words the record holds."""

    async def test_it_says_the_number_the_fields_and_how_many_agreed(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])
        assert await reading.file_from_filenames() == 2

        rows = await temp_db.fetch_all(
            "SELECT title, payload FROM workbench_decisions ORDER BY title", ()
        )
        titles = {str(row["title"]) for row in rows}
        assert (
            f"Filed under {HANDLE} on Instagram: the file's name carries ID {NUMBER},"
            f" which the Artist and ImageDescription fields of 2 of its pictures name as {HANDLE}"
        ) in titles
        # And the username's own line, against the username rather than against a file.
        assert (
            f"Learned this username's Instagram ID {NUMBER} from the Artist and"
            " ImageDescription fields of 2 pictures"
        ) in titles

    async def test_the_receipt_carries_what_the_line_is_drawn_from(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The line is read back rather than worked out again, so the record has to hold it all."""
        import json

        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])
        assert await reading.file_from_filenames() == 2

        rows = await temp_db.fetch_all(
            "SELECT payload FROM workbench_decisions WHERE queue = 'filenames'", ()
        )
        filed = [json.loads(str(row["payload"])) for row in rows]
        one = next(payload for payload in filed if payload["kind"] == "filed")
        assert one["number"] == NUMBER
        assert one["name"] == HANDLE
        assert one["fields"] == ["Artist", "ImageDescription"]
        assert one["agreed"] == 2
        assert one["number_via"] == "metadata"


class TestTheDoorForATypedNumber:
    """A number nobody can read off a picture, given to the library by hand."""

    async def test_a_typed_number_files_the_waiting_pictures_and_says_who_typed_it(
        self,
        reading: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
    ) -> None:
        """No pictures read at all: the username has the number because somebody put it there."""
        from sift.kernel.access import set_username_number

        _, known = await seed_site_username(
            temp_db, site="Instagram", name=HANDLE, made=MADE_BY_A_PERSON
        )
        typed = await set_username_number(temp_db, username_id=known, number=NUMBER)
        assert typed.outcome == "written"
        await add_file(library, _named("0"), source="accepted.jpg")

        assert await reading.file_from_filenames() == 1

        titles = {
            str(row["title"])
            for row in await temp_db.fetch_all("SELECT title FROM workbench_decisions", ())
        }
        assert (
            f"Filed under {HANDLE} on Instagram: the file's name carries ID {NUMBER},"
            " which you typed onto the username"
        ) in titles
        # The filing is the FILENAME's, not the metadata's: nothing read a picture here.
        filed = await temp_db.fetch_all("SELECT source FROM asset_usernames", ())
        assert {str(row["source"]) for row in filed} == {"filename"}

    async def test_a_number_only_ever_fills_a_blank(self, store: Store, temp_db: Database) -> None:
        """A number fills a blank. A second, different one is a question and not a correction,
        unless replacing it was asked for, which only the username sheet does, after asking."""
        from sift.kernel.access import set_username_number

        _, known = await seed_site_username(
            temp_db,
            site="Instagram",
            name=HANDLE,
            number=NUMBER,
            made=MADE_BY_A_PERSON,
        )
        held = await set_username_number(temp_db, username_id=known, number="99999999")
        assert held.outcome == "held"
        assert (await username_number(temp_db, known)).number == NUMBER


class TestTheSwitch:
    """`suggestions.read_metadata`: off, no picture is ever opened."""

    async def test_off_nothing_is_read_and_nothing_is_learned(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        refusing = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=FakePreferences(**{READ_METADATA_KEY: False}),
            recorder=decisions,
            pictures=pictures.seam(),
        )
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await refusing.file_from_filenames() == 0
        assert await temp_db.fetch_all("SELECT id FROM usernames", ()) == []
        # And no file was opened. The switch stops the READ, not just the write.
        assert pictures.asked == []

    async def test_a_service_with_no_way_in_behaves_the_same(
        self,
        service: SuggestionService,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The seam absent and the switch off are one behaviour, which is how they cannot disagree."""
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await service.file_from_filenames() == 0
        assert pictures.asked == []

    async def test_a_pass_made_while_off_leaves_the_waiting_files_to_be_read_once_on(
        self,
        store: Store,
        access: Repository,
        faces: FakeFaces,
        decisions: DecisionStore,
        temp_db: Database,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
    ) -> None:
        """The pass while off remembers the number as waiting, and not as read: the first pass
        after the switch goes on opens its pictures and files them, with no new file arriving."""
        switch = FakePreferences(**{READ_METADATA_KEY: False})
        service = SuggestionService(
            store=store,
            access=access,
            faces=faces,
            preferences=switch,
            recorder=decisions,
            pictures=pictures.seam(),
        )
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await service.file_from_filenames() == 0
        assert await service.numbers_waiting() == 1
        assert pictures.asked == []

        switch.values[READ_METADATA_KEY] = True
        assert await service.file_from_filenames() == 2
        assert await service.numbers_waiting() == 0
        assert pictures.asked, "the waiting number's pictures were opened"


class TestTheCardSaysWhatIsWaiting:
    """A number with no username is not silence: the card says how many are waiting."""

    async def test_it_counts_the_numbers_no_handle_was_found_for(
        self,
        reading: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
        admin: Viewer,
    ) -> None:
        await _plant(add_file, library, pictures, [Fields(), Fields()])

        assert await reading.file_from_filenames() == 0
        assert await reading.numbers_waiting() == 1
        card = await FiledFromFilenamesQueue(reading).survey(admin)
        assert card.aside is not None
        assert "1 ID is waiting for a username" in card.aside.said

    async def test_a_number_that_was_named_stops_waiting(
        self,
        reading: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        pictures: FakePictures,
        admin: Viewer,
    ) -> None:
        await _plant(add_file, library, pictures, [_agreeing(), _agreeing()])

        assert await reading.file_from_filenames() == 2
        assert await reading.numbers_waiting() == 0
        card = await FiledFromFilenamesQueue(reading).survey(admin)
        assert card.aside is None


class TestTheAddressReader:
    """The two questions an address is asked, and the answers that are not guesses."""

    def test_a_post_address_names_nobody(self) -> None:
        assert username_in_address("https://www.instagram.com/p/B-Exampl4Ka/") is None
        assert username_in_address("https://www.instagram.com/reel/B-Exampl4Ka/") is None

    @pytest.mark.parametrize(
        "address",
        [
            "https://www.instagram.com/p/",
            "https://www.instagram.com/p/B-Exampl4Ka/",
            "https://www.instagram.com/stories/",
            "https://www.instagram.com/stories/somebody/",
            "https://www.instagram.com/stories/highlights/17900000000000000/",
            "https://www.instagram.com/reel/",
            "https://www.instagram.com/reels/",
            "https://www.instagram.com/reels/B-Exampl4Ka/",
            "https://www.instagram.com/explore/",
            "https://www.instagram.com/explore/tags/whatever/",
            "https://www.instagram.com/tv/",
            "https://www.instagram.com/tv/B-Exampl4Ka/",
        ],
    )
    def test_a_word_of_the_address_that_is_not_a_profile_names_nobody(self, address: str) -> None:
        """The words that would make usernames called `p` and `stories`, and the rest of the
        site's routes: a one-segment `/explore/` is shaped exactly like a profile address."""
        assert username_in_address(address) is None

    def test_a_profile_address_names_its_handle(self) -> None:
        assert username_in_address(f"https://www.instagram.com/{HANDLE}") == HANDLE

    def test_the_site_is_the_host_and_never_the_text(self) -> None:
        """A shortener whose path mentions the site corroborates nothing about the site."""
        assert names_the_site("https://www.instagram.com/p/AAA/", "Instagram")
        assert not names_the_site("https://short.test/instagram.com/p/AAA/", "Instagram")
        assert not names_the_site("https://www.instagram.com/p/AAA/", "Fansly")

    def test_an_address_that_is_not_a_web_address_names_no_site(self) -> None:
        """A `javascript:` or `file:` value can put any host after its scheme; only a page on the
        web is on a Site."""
        for address in ("ftp://www.instagram.com/quillmoss", "javascript://www.instagram.com/"):
            assert not names_the_site(address, "Instagram"), address


# --- the fixture, built here so its absence from the answer means something exact ----------------


def _rational(numerator: int, denominator: int = 1) -> bytes:
    return struct.pack(">II", numerator, denominator)


def _exif() -> bytes:
    """An APP1 segment carrying the two fields this reads AND a GPS block it must not.

    Hand-built for the reason `browse/tests/test_metadata.py` builds its own: no dependency, and a
    fixture written out here is one whose absence from the output means something exact.
    """
    description = f"https://www.instagram.com/{HANDLE}".encode() + b"\x00"
    artist = HANDLE.encode() + b"\x00"

    gps_entries = [
        (0x0001, 2, 2, b"N\x00"),  # GPSLatitudeRef
        (0x0002, 5, 3, None),  # GPSLatitude -> offset
    ]
    root_count = 3
    root_size = 2 + root_count * 12 + 4
    values_at = 8 + root_size
    description_at = values_at
    artist_at = description_at + len(description)
    gps_at = artist_at + len(artist)
    gps_size = 2 + len(gps_entries) * 12 + 4
    latitude_at = gps_at + gps_size
    latitude = b"".join(_rational(value) for value in (51, 30, 0))

    gps = struct.pack(">H", len(gps_entries))
    for tag, kind, count, inline in gps_entries:
        payload = (
            inline + b"\x00" * (4 - len(inline))
            if inline is not None
            else struct.pack(">I", latitude_at)
        )
        gps += struct.pack(">HHI", tag, kind, count) + payload
    gps += struct.pack(">I", 0)

    root = struct.pack(">H", root_count)
    root += struct.pack(">HHI", 0x010E, 2, len(description)) + struct.pack(">I", description_at)
    root += struct.pack(">HHI", 0x013B, 2, len(artist)) + struct.pack(">I", artist_at)
    root += struct.pack(">HHI", 0x8825, 4, 1) + struct.pack(">I", gps_at)
    root += struct.pack(">I", 0)

    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + root + description + artist + gps + latitude
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _tagged_jpeg(destination: Path) -> Path:
    """A real JPEG with that block spliced in after the start-of-image marker."""
    original = (CORPUS / "accepted.jpg").read_bytes()
    assert original[:2] == b"\xff\xd8", "the fixture is not a JPEG any more"
    app0_length = struct.unpack(">H", original[4:6])[0]
    destination.write_bytes(b"\xff\xd8" + _exif() + original[4 + app0_length :])
    return destination
