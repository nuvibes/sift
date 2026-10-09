# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every line the record can say, with the things it names: the table the sentence tests read."""

from __future__ import annotations

from sift.kernel.access import sentences as say
from sift.kernel.vocabulary import Subject

#: Names invented for this file; see `tests/gates/data/names_cast.txt`.
SOMEBODY = "Neve Alder"
A_BOX = "StashDB"
PERSON = say.thing("person", "p1", SOMEBODY)
FILE = say.thing("asset", "a1", "holiday.mp4")
OTHER_FILE = say.thing("asset", "a2", "beach.mp4")
TAG = say.thing("tag", "t1", "poolside")
SITE = say.thing("site", "s1", "Studio")
FOLDER = say.thing("folder", "clips", "clips")
FILES = say.thing("files", "p1", "7,000 files", href="/browse?people=x")
SHELF = say.thing("collection", "c1", "d")
USERNAME = say.thing("username", "u1", "harlowquin")
#: The other install in a swap, as its device id is shown: eight groups of four base32 characters.
DEVICE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"


def _page(verb: str, here: str, **rest: object) -> say.Line:
    """One ledger act on the page of a thing it names, said by You unless the row says otherwise."""
    by = str(rest.pop("by", say.YOU))
    return say.event_said(verb, by=by, here=here, **rest).pieces  # type: ignore[arg-type]


def _feed(verb: str, **rest: object) -> say.Line:
    """One ledger act in the Settings feed, which names everything."""
    by = str(rest.pop("by", say.YOU))
    return say.feed_line(verb, by=by, **rest).pieces  # type: ignore[arg-type]


#: The two files a library can say it came from, named as their writers name them.
BACKUP = say.thing("backup", "1700000000", "the backup from 14 November 2023")
DATABASE_FILE = say.thing(
    "database_file", "old-library.sqlite3", "the database file old-library.sqlite3"
)


#: The computer running Sift, named as it names itself, and the two ways a window says where it is.
COMPUTER = say.thing("computer", "DESK-ONE", "DESK-ONE")


FROM_LAPTOP: dict[str, object] = {"from": "LAPTOP-TWO", "here": False}


FROM_A_BROWSER: dict[str, object] = {"from": None, "here": False}


FILE_PAGE = say.VANTAGE_FILE
PERSON_PAGE = say.VANTAGE_PERSON
ENTITY_PAGE = say.VANTAGE_ENTITY


#: EVERY LINE A HISTORY CAN SAY, as (what it is, who said it (None if passive), the line).
#: Two fields a box filled in: one value, and a list with its rows.
_FILLED = (
    say.FilledField("gender", values=(say.said("Female"),)),
    say.FilledField("aliases", count=2, values=(say.said("Ada"), say.said("Bea"))),
)


SAID: tuple[tuple[str, str | None, say.Line], ...] = (
    # --- a file's own thread
    ("file: arrived", "Sift", say.added("holiday.mp4")),
    ("file: arrived with no name", "Sift", say.added(None)),
    ("file: moved", "You", say.moved("You", "clips/summer/holiday.mp4")),
    ("file: moved to the top", None, say.moved(None, "holiday.mp4")),
    ("file: renamed", "Sift", say.renamed("Sift", "clips/holiday.mp4")),
    ("file: a move undone", None, say.undone("move")),
    ("file: a decision undone", None, say.undone("decision")),
    # A song name Sift shared, put back by its Undo: said as a song name, not as a decision.
    ("file: a song name undone", None, say.undone("song_name")),
    ("file: named from a folder", "Sift", say.named_sentence("Sift", "folder", (PERSON,))),
    ("file: named by a box", A_BOX, say.named_sentence(A_BOX, "stash_box", (PERSON,))),
    ("file: named by nobody recorded", None, say.named_sentence(None, None, (PERSON,))),
    ("file: a press of namings", "Sift", say.named_sentence("Sift", "folder", "3 people", 3)),
    ("file: tagged from a name", "Sift", say.tagged_sentence("Sift", "filename", (TAG,))),
    ("file: tagged by nobody recorded", None, say.tagged_sentence(None, None, (TAG,))),
    ("file: filed from a name", "Sift", say.filed_sentence("Sift", "harlowquin", SITE, "filename")),
    ("file: filed, poster unknown", None, say.filed_sentence(None, "", SITE)),
    ("file: a box recognized it", A_BOX, say.recognized(A_BOX, True, ["2 people", "1 tag"])),
    ("file: a box, nothing recorded", A_BOX, say.recognized(A_BOX, None, None)),
    ("file: a box changed nothing", A_BOX, say.recognized(A_BOX, False, [])),
    ("file: a box had no match", "Sift", say.asked_and_found_nothing(A_BOX, 5)),
    # What a box filled in, every field named with its values, and a link older than that record.
    ("box: filled in, named", A_BOX, say.box_filled_named(A_BOX, _FILLED)),
    ("box: linked before the record", A_BOX, say.linked_before_recorded(A_BOX, _FILLED[:1])),
    ("file: faces found", "Sift", say.looked_for_faces(2, say.listed([(PERSON,)]))),
    ("file: no faces", "Sift", say.looked_for_faces(0, ())),
    # A pass somebody pressed, said as theirs, and its fingerprints said the same way.
    ("file: a pressed look", "You", say.had_sift("You", "look for faces here", "it found none")),
    ("file: pressed fingerprints", "You", say.fingerprinted_by("You", empty=False)),
    # A press of a pass no line on the file says, and three of them folded into one line.
    ("file: a press no line said", "You", say.pressed_here("You", ["faces"])),
    ("file: three presses folded", "You", say.pressed_here("You", ["thumbnails", "previews"], 3)),
    ("file: faces too small", "Sift", say.looked_for_faces(0, (), small=3, closer=1)),
    ("file: Sift recognized a face", "Sift", say.recognized_face(PERSON, "here", [0.75])),
    ("file: a face confirmed", None, say.agreed_to_be(PERSON)),
    ("file: a face marked not them", None, say.refused_as(PERSON)),
    ("file: copied from", "You", say.copied_from("You", "trim", OTHER_FILE)),
    ("file: copied from a file it may not see", "You", say.copied_from("You", "edit", None)),
    ("file: copied into", "You", say.copied_into("You", "gif", OTHER_FILE)),
    ("file: shared by nobody recorded", None, say.shared_with(None, "fff")),
    ("file: kept private", "You", say.shared_with("You", "fff", share=False)),
    (
        "file: a watermark filed",
        "Sift",
        say.watermark_found("site", "x", SITE, "itsnatalie", filed=True),
    ),
    (
        "file: a watermark filed nothing",
        "Sift",
        say.watermark_found("site", "x", SITE, None, filed=False),
    ),
    ("file: a channel", "Sift", say.watermark_found("channel", "@chan", None, None, filed=False)),
    ("file: a band", "Sift", say.watermark_found("notice", "x", SITE, None, filed=True)),
    (
        "file: a bare username",
        "Sift",
        say.watermark_found("username", "nat", None, None, filed=True),
    ),
    ("file: no watermark", "Sift", say.watermark_none()),
    ("file: a watermark filing undone", None, say.watermark_refused()),
    ("file: downloaded", "Sift", say.downloaded(SITE, "harlowquin")),
    ("file: downloaded from the web", "Sift", say.downloaded(None, None)),
    ("file: downloaded by you", "You", say.downloaded(SITE, "harlowquin", by="You")),
    ("file: pictures generated", "Sift", say.made_ready(["thumb", "preview", "sprite"])),
    ("file: indexed", "Sift", say.read_its_meaning()),
    # The passes whose rows said nothing on a file's History before: each pass's answer, the empty
    # ones as plainly as the full.
    ("file: a music fingerprint", "Sift", say.music_fingerprinted(empty=False)),
    ("file: no sound to fingerprint", "Sift", say.music_fingerprinted(empty=True)),
    ("file: details read again", "Sift", say.details_read_again()),
    ("file: AcoustID did not know", "Sift", say.acoustid_answered("nothing")),
    ("file: AcoustID gave no answer", "Sift", say.acoustid_answered("failed")),
    ("file: AcoustID refused", "Sift", say.acoustid_answered("refused")),
    ("file: AcoustID named a song kept off", "Sift", say.acoustid_answered("named", "Blue")),
    ("file: a pass gave up", "Sift", say.could_not("identity", again=True)),
    ("file: a box match waiting", "Sift", say.asked_and_waiting(A_BOX)),
    ("file: faces deleted", None, say.faces_taken_off(3)),
    ("file: a face discarded", None, say.faces_set_aside(1)),
    ("file: a person marked not in it", None, say.person_refused(PERSON)),
    ("file: hidden", "You", say.hidden_here()),
    ("file: a box's answer refused", None, say.kept_mine(None, A_BOX, "breast type")),
    # --- a person's own thread
    ("person: arrived from a folder", "Sift", say.person_added("Sift", PERSON, "folder")),
    ("person: arrived, nobody recorded", None, say.person_added(None, PERSON)),
    (
        "person: named on files from a folder",
        "Sift",
        say.named_on("Sift", "folder", FILES, FOLDER, 1),
    ),
    ("person: named on files, nobody recorded", None, say.named_on(None, None, FILES)),
    ("person: named from their folders", "Sift", say.named_on("Sift", "folder", FILES, None, 2)),
    ("person: named by a box", A_BOX, say.named_on(A_BOX, "stash_box", FILES)),
    ("person: faces confirmed", None, say.faces_agreed(say.thing("faces", "p1", "12 faces"), 12)),
    ("person: faces marked not them", None, say.faces_refused(3)),
    ("person: learned from faces", "Sift", say.taught_by(12)),
    ("person: marked not in files", None, say.ruled_out_of(3)),
    (
        "person: a box filled in",
        A_BOX,
        say.box_line(A_BOX, True, ["birthday"], "their", whom="them")[1],
    ),
    ("person: linked to a box", "You", say.box_line(A_BOX, True, None, "their", whom="them")[1]),
    (
        "person: a box, nobody recorded",
        None,
        say.box_line(A_BOX, None, None, "their", whom="them")[1],
    ),
    (
        "person: a box asked again",
        "Sift",
        say.box_line(A_BOX, False, [], "their", True, whom="them")[1],
    ),
    (
        "person: Sift recognized them",
        "Sift",
        say.recognized_face("them", say.said("in ", FILE), [0.76]),
    ),
    # --- an entity's own thread
    ("entity: a tag arrived", "Sift", say.entity_added("Sift", TAG, " from a folder name")),
    ("entity: a Collection created", "You", say.entity_created("You", SHELF)),
    ("entity: a Photo Set created, nobody recorded", None, say.entity_created(None, SHELF)),
    ("entity: a tag put on files", "Sift", say.put_on("Sift", "folder", FILES)),
    ("entity: a tag put on, nobody recorded", None, say.put_on(None, None, FILES)),
    # A song's own page: the files it was named on, in the family a song's naming is said in.
    (
        "entity: a song named on files from AcoustID",
        "Sift",
        say.song_named_here("Sift", FILES, 7726, "acoustid", None),
    ),
    (
        "entity: a song named from the same music as a file",
        "Sift",
        say.song_named_here("Sift", FILE, 1, "shared", OTHER_FILE),
    ),
    (
        "entity: a song named from a download page",
        "Sift",
        say.song_named_here("Sift", FILE, 1, "site", None),
    ),
    (
        "entity: a song named on a file by hand",
        None,
        say.song_named_here(None, FILE, 1, None, None),
    ),
    # The one line catalog step 81 writes for a library: its songs moved onto rows.
    # `feed_line` answers None for any other `added`; this one is the step's.
    (
        "feed: songs moved onto their own rows",
        "Sift",
        say.songs_moved("Sift", {"songs": 12}, 40) or say.said(),
    ),
    (
        "feed: which stash-box added each filing, recorded",
        "Sift",
        say.boxes_recorded("Sift", {"boxes_recorded": 12}) or say.said(),
    ),
    # The one line Insights' step 6 writes for a library: the deleted files it kept the days of.
    (
        "feed: departures kept",
        "Sift",
        say.departures_kept("Sift", {"departures_kept": 13}) or say.said(),
    ),
    ("entity: files filed under a Site", A_BOX, say.filed_under(A_BOX, "stash_box", FILES, 7726)),
    ("entity: files filed from folders", "Sift", say.filed_under("Sift", "folder", FILES, 7726)),
    ("entity: usernames added", None, say.usernames_added(None, [USERNAME])),
    (
        "entity: many usernames added",
        None,
        say.usernames_added(None, [say.thing("username", str(n), f"u{n}") for n in range(9)]),
    ),
    ("entity: a day of downloads", "Sift", say.downloads_folded("Sift", 9, ENTITY_PAGE)[0]),
    ("person: a download of theirs", "Sift", say.downloads_folded("Sift", 1, PERSON_PAGE)[0]),
    # --- the ledger's acts, at a vantage
    (
        "ledger: a tag added",
        "You",
        _page("linked", FILE_PAGE, object_kind="tag", object_id="t", object_name="poolside"),
    ),
    (
        "ledger: a tag removed",
        "You",
        _page("unlinked", FILE_PAGE, object_kind="tag", object_id="t", object_name="poolside"),
    ),
    (
        "ledger: a person removed",
        "You",
        _page("unlinked", FILE_PAGE, object_kind="person", object_id="p", object_name=SOMEBODY),
    ),
    (
        "ledger: filed under a Site",
        "Sift",
        _page(
            "linked",
            FILE_PAGE,
            by="Sift",
            task="filename",
            object_kind="site",
            object_id="s",
            object_name="Studio",
        ),
    ),
    (
        "ledger: a file added to a Collection",
        "You",
        _page(
            "linked",
            ENTITY_PAGE,
            object_kind="collection",
            object_id="c",
            object_name="d",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="holiday.mp4")],
        ),
    ),
    (
        "ledger: a file removed from a Photo Set",
        "You",
        _page(
            "unlinked",
            ENTITY_PAGE,
            object_kind="photo_set",
            object_id="c",
            object_name="d",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="holiday.mp4")],
        ),
    ),
    (
        "ledger: a username joined to them",
        "You",
        _page(
            "linked",
            PERSON_PAGE,
            object_kind="person",
            object_id="p",
            object_name=SOMEBODY,
            from_object=True,
            subjects=[Subject(kind="username", id="u", name="harlowquin")],
        ),
    ),
    ("ledger: hidden", "You", _page("hidden", FILE_PAGE)),
    ("ledger: unhid", "You", _page("revealed", FILE_PAGE)),
    (
        "ledger: shared",
        "You",
        _page("shared", ENTITY_PAGE, object_kind="login", object_id="u", object_name="fff"),
    ),
    (
        "ledger: stopped sharing",
        "You",
        _page("unshared", ENTITY_PAGE, object_kind="login", object_id="u", object_name="fff"),
    ),
    ("ledger: lookups off", "You", _page("kept_local", PERSON_PAGE)),
    ("ledger: lookups on", "You", _page("allowed", PERSON_PAGE)),
    ("ledger: renamed", "You", _page("renamed", PERSON_PAGE, payload={"before": "Ada"})),
    ("ledger: renamed, old name not kept", "You", _page("renamed", PERSON_PAGE)),
    (
        "ledger: fields edited",
        "You",
        _page("edited", FILE_PAGE, payload={"fields": [{"field": "title"}, {"field": "details"}]}),
    ),
    (
        "ledger: many fields edited",
        "You",
        _page(
            "edited",
            FILE_PAGE,
            payload={"fields": [{"field": one} for one in ("title", "details", "music", "links")]},
        ),
    ),
    (
        "ledger: an unknown field edited",
        "You",
        _page("edited", FILE_PAGE, payload={"fields": [{"field": "weird"}]}),
    ),
    ("ledger: nothing said about the edit", "You", _page("edited", ENTITY_PAGE)),
    (
        "ledger: a cover chosen",
        "You",
        _page("edited", PERSON_PAGE, object_kind="asset", object_id="a", object_name="192bd.jpg"),
    ),
    (
        "ledger: chosen as a cover",
        "You",
        _page(
            "edited",
            FILE_PAGE,
            object_kind="asset",
            object_id="a",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="person", id="p", name=SOMEBODY)],
        ),
    ),
    (
        "ledger: a box's picture as the cover",
        "Sift",
        _page("edited", PERSON_PAGE, by="Sift", payload={"cover": "picture", "box": A_BOX}),
    ),
    ("ledger: a cover removed", "You", _page("edited", ENTITY_PAGE, payload={"cover": "none"})),
    (
        "ledger: a cover reframed",
        "You",
        _page("edited", ENTITY_PAGE, payload={"cover": "reframed"}),
    ),
    ("ledger: filled in with no box", "Sift", _page("enriched", PERSON_PAGE, by="Sift")),
    (
        "ledger: asked a box",
        "Sift",
        _page("asked", FILE_PAGE, by="Sift", object_kind="box", object_id="b", object_name=A_BOX),
    ),
    ("ledger: fingerprinted", "Sift", _page("scanned", FILE_PAGE, by="Sift", task="fingerprint")),
    (
        "ledger: nothing to fingerprint",
        "Sift",
        _page("scanned", FILE_PAGE, by="Sift", task="fingerprint", payload={"empty": True}),
    ),
    ("file: nothing to fingerprint", "Sift", say.nothing_to_fingerprint("Sift", "this file")),
    (
        "ledger: created from",
        "You",
        _page(
            "produced",
            FILE_PAGE,
            object_kind="asset",
            object_id="b",
            object_name="beach.mp4",
            payload={"operation": "trim"},
        ),
    ),
    (
        "ledger: a copy made of it",
        "You",
        _page(
            "produced",
            FILE_PAGE,
            object_kind="asset",
            object_id="b",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="beach-cut.mp4")],
            payload={"operation": "crop"},
        ),
    ),
    (
        "ledger: deleted from the disk",
        "You",
        _page(
            "deleted",
            FILE_PAGE,
            subjects=[
                Subject(kind="person", id="p", name=SOMEBODY),
                Subject(kind="tag", id="t", name="poolside"),
            ],
            payload={"from": "disk"},
        ),
    ),
    (
        "ledger: a file of theirs deleted",
        "You",
        _page(
            "deleted",
            ENTITY_PAGE,
            about_kind="person",
            subjects=[Subject(kind="asset", id="a", name="d0dd.jpg")],
            payload={"from": "sift"},
        ),
    ),
    (
        "ledger: a file with this tag deleted",
        "You",
        _page(
            "deleted",
            ENTITY_PAGE,
            about_kind="tag",
            subjects=[Subject(kind="asset", id="a", name="d0dd.jpg")],
        ),
    ),
    (
        "feed: face data deleted",
        "You",
        _feed("forgot", subjects=[("setting", say.Piece("Faces"))], count=1203),
    ),
    (
        "ledger: a tag added, on its own page",
        "You",
        _page(
            "linked",
            ENTITY_PAGE,
            object_kind="tag",
            object_id="t",
            object_name="poolside",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="holiday.mp4")],
        ),
    ),
    (
        "ledger: a tag removed, on its own page",
        "You",
        _page(
            "unlinked",
            ENTITY_PAGE,
            object_kind="tag",
            object_id="t",
            object_name="poolside",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="holiday.mp4")],
        ),
    ),
    (
        "ledger: a face chosen as the cover",
        "Sift",
        _page(
            "edited",
            PERSON_PAGE,
            by="Sift",
            task="faces",
            object_kind="asset",
            object_id="a",
            object_name="image.png",
        ),
    ),
    (
        "ledger: a download failed, and why",
        "Sift",
        _page(
            "download_failed",
            FILE_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
            payload={"code": "http-410"},
        ),
    ),
    (
        "ledger: downloaded, on the file",
        "Sift",
        _page(
            "downloaded",
            FILE_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
        ),
    ),
    (
        "ledger: downloaded, on the Site",
        "Sift",
        _page(
            "downloaded",
            ENTITY_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
            from_object=True,
        ),
    ),
    (
        "ledger: a download failed",
        "Sift",
        _page(
            "download_failed",
            ENTITY_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="Studio",
            from_object=True,
        ),
    ),
    (
        "ledger: merged into",
        "You",
        _page("merged", PERSON_PAGE, object_kind="person", object_id="p2", object_name="Ada Lumen"),
    ),
    (
        "ledger: merged in",
        "You",
        _page(
            "merged",
            PERSON_PAGE,
            object_kind="person",
            object_id="p2",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="person", id="p1", name="Ada")],
            payload={"files": 3},
        ),
    ),
    (
        "ledger: merged in, nothing came",
        "You",
        _page(
            "merged",
            PERSON_PAGE,
            object_kind="person",
            object_id="p2",
            object_name="x",
            from_object=True,
            subjects=[Subject(kind="person", id="p1", name="Ada")],
            payload={"files": 0},
        ),
    ),
    ("ledger: paused", "You", _page("paused", ENTITY_PAGE)),
    ("ledger: resumed", "You", _page("resumed", ENTITY_PAGE)),
    ("ledger: cookies saved", "You", _page("cookies_saved", ENTITY_PAGE)),
    ("ledger: cookies replaced", "You", _page("cookies_replaced", ENTITY_PAGE)),
    ("ledger: cookies deleted", "You", _page("cookies_forgotten", ENTITY_PAGE)),
    ("ledger: canceled", "You", _page("canceled", FILE_PAGE)),
    ("ledger: saved to a device", "You", _page("saved", FILE_PAGE)),
    (
        "ledger: a song named, on the file",
        "Sift",
        _page(
            "song_named",
            FILE_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="PMVHaven",
            payload={"song": "Blue"},
        ),
    ),
    (
        "ledger: a song named, on the Site",
        "Sift",
        _page(
            "song_named",
            ENTITY_PAGE,
            by="Sift",
            object_kind="site",
            object_id="s",
            object_name="PMVHaven",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="x.mp4")],
            payload={"song": "Blue"},
        ),
    ),
    (
        "ledger: a song named from AcoustID, on the file",
        "Sift",
        _page("song_named", FILE_PAGE, by="Sift", payload={"song": "Blue", "source": "acoustid"}),
    ),
    (
        "ledger: a song shared, on the file that received it",
        "Sift",
        _page(
            "song_named",
            FILE_PAGE,
            by="Sift",
            object_kind="asset",
            object_id="b",
            object_name="y.mp4",
            payload={"song": "Blue", "source": "shared", "from": "b"},
        ),
    ),
    (
        "ledger: a song shared, on the file it came from",
        "Sift",
        _page(
            "song_named",
            FILE_PAGE,
            by="Sift",
            object_kind="asset",
            object_id="b",
            object_name="y.mp4",
            from_object=True,
            subjects=[Subject(kind="asset", id="a", name="x.mp4")],
            payload={"song": "Blue", "source": "shared", "from": "b"},
        ),
    ),
    ("ledger: a decision", "You", _page("decided", FILE_PAGE, title="You agreed with 19 matches")),
    ("ledger: an act from a newer build", "You", _page("invented_later", FILE_PAGE)),
    # --- the same acts in the feed, which names everything
    (
        "feed: Sift recognized a face",
        "Sift",
        _feed(
            "linked",
            by="Sift",
            task="faces",
            subjects=[("person", PERSON), ("asset", FILE)],
            object_kind="person",
            object_piece=PERSON,
            sures=[0.75],
        ),
    ),
    (
        "feed: filed from file names",
        "Sift",
        _feed(
            "filed",
            by="Sift",
            task="filename",
            subjects=[("asset", FILE)],
            object_kind="username",
            object_piece=USERNAME,
        ),
    ),
    (
        "feed: a Photo Set under the floor",
        "Sift",
        _feed(
            "deleted",
            by="Sift",
            task="photo_set_floor",
            subjects=[("photo_set", say.thing("photo_set", "ps", "harlowquin"))],
            payload={"under_floor": 10},
        ),
    ),
    (
        "feed: a file deleted from Sift",
        "You",
        _feed("deleted", subjects=[("asset", FILE), ("person", PERSON)], payload={"from": "sift"}),
    ),
    (
        "feed: a decision",
        "You",
        _feed("decided", title="You agreed with 19 matches", subjects=[("asset", FILE)]),
    ),
    (
        "feed: a setting changed",
        "You",
        _feed(
            "edited",
            subjects=[("setting", say.Piece("Starting volume"))],
            payload={"key": "k", "before": "0.4", "after": "0.7"},
        ),
    ),
    (
        "feed: a default an update changed",
        "The update to 0.1.218",
        _feed(
            "edited",
            by="Sift",
            task="update",
            subjects=[("setting", say.Piece("Hide personal details in the log"))],
            payload={"key": "k", "after": "false", "update_to": "0.1.218"},
        ),
    ),
    (
        "feed: a cover chosen",
        "You",
        _feed("edited", subjects=[("person", PERSON)], object_kind="asset", object_piece=FILE),
    ),
    (
        "feed: fields edited",
        "You",
        _feed("edited", subjects=[("asset", FILE)], payload={"fields": [{"field": "title"}]}),
    ),
    (
        "feed: a file added to a Collection",
        "You",
        _feed("linked", subjects=[("asset", FILE)], object_kind="collection", object_piece=SHELF),
    ),
    (
        "feed: a username arrived",
        "Sift",
        _feed(
            "added",
            by="Sift",
            task="filename",
            subjects=[("username", USERNAME)],
            object_kind="site",
            object_piece=SITE,
        ),
    ),
    (
        "feed: a username from before",
        None,
        _feed(
            "added",
            by="Sift",
            subjects=[("username", USERNAME)],
            object_kind="site",
            object_piece=SITE,
            payload={"backfilled": True},
        ),
    ),
    (
        "feed: a song named",
        "Sift",
        _feed(
            "song_named",
            by="Sift",
            subjects=[("asset", FILE)],
            object_kind="site",
            object_piece=SITE,
            payload={"song": "Blue"},
        ),
    ),
    (
        "feed: a song named from AcoustID",
        "Sift",
        _feed(
            "song_named",
            by="Sift",
            subjects=[("asset", FILE)],
            payload={"song": "Blue", "source": "acoustid"},
        ),
    ),
    (
        "feed: a song shared",
        "Sift",
        _feed(
            "song_named",
            by="Sift",
            subjects=[("asset", FILE)],
            object_kind="asset",
            object_piece=say.thing("asset", "a2", "longer.mp4"),
            payload={"song": "Blue", "source": "shared", "from": "a2"},
        ),
    ),
    # The Organize music card's Later: a receipt's own words, which name nobody first.
    (
        "feed: the music card's Later",
        None,
        _feed("decided", title="Chose to fingerprint the music later"),
    ),
    (
        "feed: downloaded",
        "Sift",
        _feed(
            "downloaded",
            by="Sift",
            subjects=[("asset", FILE), ("site", SITE)],
            object_kind="site",
            object_piece=SITE,
        ),
    ),
    (
        "feed: a copy",
        "You",
        _feed(
            "produced",
            subjects=[("asset", FILE)],
            object_kind="asset",
            object_piece=OTHER_FILE,
            payload={"operation": "trim"},
        ),
    ),
    (
        "feed: renamed",
        "You",
        _feed("renamed", subjects=[("person", PERSON)], payload={"before": "Ada"}),
    ),
    (
        "feed: merged",
        "You",
        _feed(
            "merged",
            subjects=[("person", say.thing("person", "p0", "Ada"))],
            object_kind="person",
            object_piece=PERSON,
        ),
    ),
    ("feed: a task ran", "Sift", _feed("ran", by="Sift", subjects=[("run", say.Piece("Scan"))])),
    (
        "feed: a task over many files",
        "Sift",
        _feed("scanned", by="Sift", task="fingerprint", count=1203, subjects=[("folder", FOLDER)]),
    ),
    (
        "feed: another user shared",
        "harlowquin",
        _feed(
            "shared",
            by="harlowquin",
            subjects=[("asset", FILE)],
            object_kind="login",
            object_piece=say.Piece("fff"),
        ),
    ),
    ("feed: an act from a newer build", "You", _feed("invented_later", subjects=[("asset", FILE)])),
    (
        "feed: a box filled in details",
        A_BOX,
        _feed(
            "enriched",
            by=A_BOX,
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
            payload={"birth_date": 1, "height_cm": 1},
        ),
    ),
    (
        "feed: a box's answer applied by you",
        "You",
        _feed(
            "enriched",
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
            payload={one: 1 for one in ("birth_date", "height_cm", "country", "gender")},
        ),
    ),
    (
        "feed: a box recognized them",
        A_BOX,
        _feed(
            "enriched",
            by=A_BOX,
            subjects=[("person", PERSON)],
            object_kind="box",
            object_piece=say.Piece(A_BOX),
        ),
    ),
    (
        "feed: a face chosen as the cover",
        "Sift",
        _feed(
            "edited",
            by="Sift",
            task="faces",
            subjects=[("person", PERSON)],
            object_kind="asset",
            object_piece=FILE,
        ),
    ),
    (
        "feed: a download failed, and why",
        "Sift",
        _feed(
            "download_failed",
            by="Sift",
            subjects=[("download", say.thing("download", "d1", "https://example.test/v/1"))],
            object_kind="site",
            object_piece=SITE,
            payload={"code": "wall-login"},
        ),
    ),
    # --- the feed's fold: one press of many acts, said once
    (
        "feed folded: a task filed files under many usernames",
        "Sift",
        say.feed_folded(
            "filed",
            by="Sift",
            task="filename",
            acts=4181,
            object_kind="username",
            objects=[USERNAME, say.thing("username", "u2", "sophidies")],
            objects_total=23,
            subjects=[("asset", FILE), ("asset", OTHER_FILE)],
            subject_counts={"asset": 4181},
        ).pieces,
    ),
    (
        "feed folded: files added to one Collection",
        "You",
        say.feed_folded(
            "linked",
            by="You",
            acts=12,
            object_kind="collection",
            objects=[SHELF],
            subjects=[("asset", FILE)],
            subject_counts={"asset": 12},
        ).pieces,
    ),
    (
        "feed folded: the floor task's Photo Sets",
        "Sift",
        say.feed_folded(
            "deleted",
            by="Sift",
            task="photo_set_floor",
            acts=726,
            subjects=[("photo_set", say.thing("photo_set", "ps", "harlowquin"))],
            subject_counts={"photo_set": 726},
            payload={"under_floor": 10},
        ).pieces,
    ),
    (
        "feed folded: a setting moved over a sitting",
        "You",
        say.feed_folded(
            "edited",
            by="You",
            acts=5,
            subjects=[("setting", say.Piece("Starting volume"))],
            first={"key": "k", "before": "40", "after": "57"},
            payload={"key": "k", "before": "13", "after": "70"},
        ).pieces,
    ),
    (
        "feed folded: a setting moved and moved back",
        "You",
        say.feed_folded(
            "edited",
            by="You",
            acts=6,
            subjects=[("setting", say.Piece("Theater layout"))],
            first={"key": "k", "before": "true", "after": "false"},
            payload={"key": "k", "before": "false", "after": "true"},
        ).pieces,
    ),
    (
        "feed folded: the backfilled usernames",
        "Sift",
        say.feed_folded(
            "added",
            by="Sift",
            acts=3669,
            object_kind="site",
            objects=[SITE],
            objects_total=40,
            subjects=[("username", USERNAME)],
            subject_counts={"username": 3669},
            payload={"backfilled": True},
            untold=3631,
        ).pieces,
    ),
    (
        "feed folded: decisions that said the same thing",
        None,
        say.feed_folded(
            "decided",
            by="Sift",
            acts=4,
            title="Made a Photo Set for harlowquin",
            subjects=[("asset", FILE)],
            subject_counts={"asset": 48},
        ).pieces,
    ),
    (
        "feed folded: faces Sift recognized",
        "Sift",
        say.feed_folded(
            "linked",
            by="Sift",
            task="faces",
            acts=10,
            object_kind="person",
            objects=[PERSON, say.thing("person", "p2", "Wren Halloway")],
            objects_total=4,
            subjects=[("asset", FILE)],
            subject_counts={"asset": 9},
        ).pieces,
    ),
    # One settle of a file's music names every file sharing its song after ONE file; a Site's page
    # and AcoustID each say their own place. The files open under "Show each".
    (
        "feed folded: a song named on many files from the same music",
        "Sift",
        say.feed_folded(
            "song_named",
            by="Sift",
            task="fingerprint",
            acts=11,
            object_kind="asset",
            objects=[say.thing("asset", "a2", "longer.mp4")],
            subjects=[("asset", FILE)],
            subject_counts={"asset": 11},
            payload={"song": "Blue", "source": "shared", "from": "a2"},
        ).pieces,
    ),
    (
        "feed folded: a song named on many files from AcoustID",
        "Sift",
        say.feed_folded(
            "song_named",
            by="Sift",
            task="music_lookup",
            acts=2,
            subjects=[("asset", FILE)],
            subject_counts={"asset": 2},
            payload={"song": "Blue", "source": "acoustid"},
        ).pieces,
    ),
    (
        "feed folded: a song named on many files from a Site's page",
        "Sift",
        say.feed_folded(
            "song_named",
            by="Sift",
            task="download",
            acts=3,
            object_kind="site",
            objects=[SITE],
            subjects=[("asset", FILE)],
            subject_counts={"asset": 3},
            payload={"song": "Blue"},
        ).pieces,
    ),
    # --- the file's routine lines, the stash-box's grade and a shelf's additions
    ("file: routine folded", "Sift", say.processed()),
    (
        "file: a box recognized it, a certain match",
        A_BOX,
        say.recognized(A_BOX, None, ["13 people", "9 tags"], grade="certain"),
    ),
    (
        "file: a box recognized it, an unsure match, nothing recorded",
        A_BOX,
        say.recognized(A_BOX, None, None, grade="unsure"),
    ),
    (
        "entity: files a task put in it",
        "Sift",
        say.members_added("Sift", "6 files", 6, " from a folder"),
    ),
    ("entity: a file put in it, nobody recorded", None, say.members_added(None, (FILE,), 1)),
    (
        "person: a box's answer refused, both values said",
        None,
        say.kept_mine(None, A_BOX, "breast type", "Natural", "Fake"),
    ),
    # --- a swap with another Sift: the file's arrival, the feed's two ends of a swap, and one
    # swap's arrivals folded. The device id is the only thing that names the other side.
    (
        "file: arrived by swap",
        "Sift",
        say.added_by("swap", {"device": DEVICE, "session": "7Q4K2M9X"}),
    ),
    ("swap: started", "You", say.swap_started("You", {"role": "host"})),
    ("swap: joined, nobody recorded", None, say.swap_started(None, {"role": "guest"})),
    ("swap: ended, nothing recorded", None, say.swap_ended({})),
    # --- where a library came from: a backup restored over it, or imported on the Database
    # Switcher as a new library from a backup or from a Sift database file
    (
        "feed: a library restored from a backup",
        "Sift",
        _feed("restored", by=say.SIFT, subjects=[("backup", BACKUP)]),
    ),
    (
        "feed: a library created from a database file",
        "Sift",
        _feed("adopted", by=say.SIFT, subjects=[("database_file", DATABASE_FILE)]),
    ),
    # --- an act on the computer running Sift, asked from a window: who, on which computer, and
    # from which device (the Sift app's own computer, its own screen, or a browser's "another")
    (
        "feed: sharing turned on from another computer's Sift app",
        "You",
        _feed("sharing_turned_on", subjects=[("computer", COMPUTER)], payload=FROM_LAPTOP),
    ),
    (
        "feed: sharing turned off from a browser",
        "You",
        _feed("sharing_turned_off", subjects=[("computer", COMPUTER)], payload=FROM_A_BROWSER),
    ),
    (
        "feed: set to start with Windows",
        "You",
        _feed("start_with_windows_on", subjects=[("computer", COMPUTER)], payload=FROM_LAPTOP),
    ),
    (
        "feed: no longer starts with Windows",
        "You",
        _feed("start_with_windows_off", subjects=[("computer", COMPUTER)], payload=FROM_LAPTOP),
    ),
    (
        "feed: the firewall port opened",
        "You",
        _feed("firewall_opened", subjects=[("computer", COMPUTER)], payload=FROM_LAPTOP),
    ),
    (
        "feed: Sift data moved",
        "You",
        _feed("storage_moved", subjects=[("computer", COMPUTER)], payload=FROM_LAPTOP),
    ),
    (
        "feed: an update started, by its version",
        "You",
        _feed(
            "update_started",
            subjects=[("computer", COMPUTER)],
            payload={**FROM_LAPTOP, "version": "0.1.300"},
        ),
    ),
    (
        "feed: a library opened, by its name",
        "You",
        _feed(
            "library_opened",
            subjects=[("computer", COMPUTER)],
            payload={**FROM_LAPTOP, "library": "Holidays"},
        ),
    ),
    (
        "feed: a restart asked from a browser, said by its builder",
        "You",
        say.machine_line("restarted", say.YOU, say.said(COMPUTER), FROM_A_BROWSER),
    ),
    (
        "feed: a restart from the computer's own screen",
        "You",
        _feed("restarted", subjects=[("computer", COMPUTER)], payload={"here": True}),
    ),
    ("feed: you started a swap", "You", _feed("swap_started", payload={"role": "host"})),
    (
        "feed: you joined a swap",
        "You",
        _feed("swap_started", payload={"role": "guest", "device": DEVICE}),
    ),
    (
        "feed: a swap received its files",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "guest", "device": DEVICE, "reason": "done", "files": 38},
        ),
    ),
    (
        "feed: a swap sent its files",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "host", "device": DEVICE, "reason": "done", "files": 38},
        ),
    ),
    (
        "feed: a swap this device ended",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "guest", "device": DEVICE, "reason": "ended by you", "files": 12},
        ),
    ),
    (
        "feed: a swap the other device ended",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "host", "device": DEVICE, "reason": "ended by them", "files": 12},
        ),
    ),
    (
        "feed: a swap that lost the connection",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "guest", "device": DEVICE, "reason": "lost", "files": 12},
        ),
    ),
    (
        "feed: a swap nobody joined",
        None,
        _feed("swap_ended", by="Sift", task="swap", payload={"role": "host", "reason": "lost"}),
    ),
    (
        "feed: a swap whose codes did not match",
        None,
        _feed(
            "swap_ended",
            by="Sift",
            task="swap",
            payload={"role": "guest", "device": DEVICE, "reason": "refused", "files": 0},
        ),
    ),
    (
        "feed: a file arrived by swap",
        "Sift",
        _feed(
            "added",
            by="Sift",
            task="swap",
            subjects=[("asset", FILE)],
            payload={"device": DEVICE, "session": "7Q4K2M9X"},
        ),
    ),
    (
        "feed folded: files that arrived by swap",
        "Sift",
        say.feed_folded(
            "added",
            by="Sift",
            task="swap",
            acts=38,
            subjects=[("asset", FILE), ("asset", OTHER_FILE)],
            subject_counts={"asset": 38},
            payload={"device": DEVICE, "session": "7Q4K2M9X"},
        ).pieces,
    ),
)
