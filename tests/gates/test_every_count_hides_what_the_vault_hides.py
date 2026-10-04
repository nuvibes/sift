# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every number a screen draws counts what that screen's own list holds, and nothing more.

## The fault this exists for

A count and the list under it are two answers to one question, and they are produced by two pieces
of SQL. The day either moves without the other, the screen says six and draws two, and the
difference is not a cosmetic wobble: it is the size of the set this user was not shown, written
on screen in a number. That is the one fact concealing something was for keeping back, so a count
that outruns its list is a disclosure however small the number is.

A card reading six over a wall drawing two is exactly that shape, and it can come from a NARROWING
as easily as from the vault. The class is real and the vault is where it costs something, so this
is the check for the class rather than for any one instance.

## What it asks, and why it asks it this way

One library, one user, the vault SHUT, and one concealed file in every container. Then every
number the client draws off a file or a person is fetched beside the list it describes, through the
routes, over HTTP, as the screen would. Not a reading of the SQL: a statement can be gated
perfectly and still be handed the wrong flag by the store, or be right and have its number
recomputed in a router. Only the wire says what a screen was told.

Both concealment modes, because the two are different questions and only one of them is the default:

  - `fully_gone` is the default and the strict one. A concealed file is absent from the list AND
    from the count, so the two agree at the smaller number.
  - `placeholder` keeps a locked tile in the grid, so a concealed file is present in both. It is
    a mode people choose, and it is the mode where a count gated on the LOOSER of the two vault
    flags can drift away from a list gated on the stricter one, which is invisible in the default
    mode, because there the two flags are the same nought.

## What it does NOT prove

That any of these numbers is the RIGHT question. A card counting every file of somebody's while
pressing it opens the files they share with the page's subject has a count that agrees with no
list at all (it agrees with a different one), and this gate cannot see that, because both halves
of it are correctly scoped. That is a fault in what the card counts, which no scoping check sees.

It also proves nothing about a number no row here names. A count added to a card next year is
caught by this gate only when somebody adds it below, which is why the table is written out with a
line saying where each number comes from rather than discovered by a pattern.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.access.repository.entities import CARD_TABS
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session, hide_for_caller
from sift.testing.jobs import seed_job
from sift.testing.library import (
    attach_person,
    attach_tag,
    attach_username,
    seed_asset,
    seed_collection,
    seed_loop,
    seed_person,
    seed_photo_set,
    seed_root,
    seed_site,
    seed_song,
    seed_tag,
    seed_username,
    write_rows,
)
from sift.testing.settings import set_app_setting

pytestmark = [pytest.mark.gate]

PASSWORD = "A-Counted-Vault-Passw0rd!"

#: The library this gate looks at. Ids are spelled in the alphabet `new_id` mints from, so nothing
#: here is a shape Sift would refuse to look up.
A_ROOT = "01KZCNTDR00T00000000000000"
A_FOLDER = "01KZCNTDF07DER000000000000"
#: The file this user may see, and the one its vault is holding back. Every container below
#: holds BOTH, which is what makes a count that forgets the vault larger than its own list.
OPEN_FILE = "01KZCNTD0PENF17E0000000000"
SHUT_FILE = "01KZCNTDSHVTF17E0000000000"
#: The subject whose page every related count on it is read from.
SUBJECT = "01KZCNTDSVBJECT00000000000"
#: Somebody else on the same two files: the card on the subject's Seen with tab.
PARTNER = "01KZCNTDPARTNER00000000000"
#: And somebody this user has concealed by NAME, which is a stricter rule than concealing a
#: file: their row leaves every wall until the vault is opened, so every count of people has to
#: drop them too.
UNNAMED = "01KZCNTDVNNAMED00000000000"
A_TAG = "01KZCNTDTAG000000000000000"
#: A tag on the concealed file ALONE, and not hidden by name. It is the partner that exists for
#: the cards' stored pair counts: with the vault shut nothing this user may see carries it, so
#: every card that shares the concealed file with it must leave it out of its Tags cell, and in
#: placeholder mode, where the concealed file keeps its tile, every such card must count it.
SHUT_TAG = "01KZCNTDSHVTTAG00000000000"
#: A marked stretch of each file. A mark is visible exactly when its file is, so every Loops cell on
#: a card holds the concealed file's mark back with the vault shut and counts it in placeholder mode.
OPEN_MARK = "01KZCNTD0PENMARK0000000000"
SHUT_MARK = "01KZCNTDSHVTMARK0000000000"
#: A tag that NO FILE carries, only the two marks (`loop_tags`). A tag's Loops wall lists the marks
#: carrying it as well as the marks cut from its files, and only the second is a stored pair, so
#: this tag's card is the one whose Loops cell is read through the marks' own counts alone.
MARK_TAG = "01KZCNTDMARKTAG00000000000"
A_COLLECTION = "01KZCNTDC077ECT10N00000000"
A_PHOTO_SET = "01KZCNTDPH0T0SET0000000000"
#: One song carried by BOTH files: a file carries one song, and one song over a file on each side
#: of the vault is what a count that forgets the vault reads as two.
A_SONG = "01KZCNTDS0NG00000000000000"
A_SITE = "01KZCNTDP7ATF0RM0000000000"
A_USERNAME = "01KZCNTDACC0VNT00000000000"
#: A username that BELONGS to the concealed person, holding a file that is not hers, which is what
#: keeps the row on the wall while her concealment is in force, and is therefore the only shape in
#: which the name on it can be seen at all.
HER_USERNAME = "01KZCNTDHERACC0VNT00000000"
A_SHOOT = "01KZCNTDSH00T0000000000000"

#: Names from the invented cast (`tests/gates/data/names_cast.txt`). Nothing off a screen.
SUBJECT_NAME = "Elina Sorrel"
PARTNER_NAME = "Halla Nordquist"
UNNAMED_NAME = "Bryn Calloway"
TAG_NAME = "seeded mark"
SHUT_TAG_NAME = "sealed mark"
MARK_TAG_NAME = "cut moment"
COLLECTION_NAME = "seeded album"
PHOTO_SET_NAME = "seeded sheet"
SONG_NAME = "seeded tune"
SITE_NAME = "seeded site"
USERNAME_NAME = "seeded_username"
HER_USERNAME_NAME = "seeded_her_username"

#: The two container links `seed_collection` and `seed_photo_set` take one of each. A container
#: holding ONE file cannot show this fault at all (what it needs is a file on each side of the
#: vault), so the second is written here.
_ALSO_IN_COLLECTION = (
    "INSERT OR IGNORE INTO collection_items (collection_id, asset_id, position, added_at)"
    " VALUES (?, ?, 1, 0)"
)
_ALSO_IN_PHOTO_SET = (
    "INSERT OR IGNORE INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
    " VALUES (?, ?, 1, 0)"
)
_ALSO_ON_SONG = (
    "INSERT OR IGNORE INTO song_files (asset_id, song_id, source, added_at) VALUES (?, ?, NULL, 0)"
)

#: A mark carrying a tag of its own. There is no seed helper for it because nothing else needed one.
_MARK_TAGGED = "INSERT INTO loop_tags (loop_id, tag_id) VALUES (?, ?)"

#: One proposed shoot over both files. There is no seed helper for these because nothing else
#: needed one; the board is here because it draws a count of pictures beside the pictures.
_A_PROPOSAL = (
    "INSERT INTO shoot_proposals (id, person_id, name, found_at) VALUES (?, ?, 'seeded shoot', 0)"
)
_A_PROPOSAL_ITEM = (
    "INSERT INTO shoot_proposal_items (proposal_id, asset_id, position, named) VALUES (?, ?, ?, 1)"
)

#: Faces with the subject's name on them, one on each side of the vault, and one more on the open
#: file somebody agreed to: what the People Sift can recognize wall counts by how each face was named.
#: No seed helper for these because nothing else in this file needed one.
_A_FACE = (
    "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
    " confidence, attribution, attributed_at, created_at) VALUES (?, ?, 0, 0, 1, 1.0, ?, 0.9, ?, 1, 0)"
)
OPEN_FACE = "01KZCNTDFACE0PEN0000000000"
SHUT_FACE = "01KZCNTDFACESHVT0000000000"
AGREED_FACE = "01KZCNTDFACEAGREED00000000"

#: A standing verdict: the thumbnail pass gave up on this file. One on each side of the vault, so the
#: Importing pane's "2 files couldn't have thumbnails generated" has a concealed file to outrun its
#: wall by. No seed helper for these because nothing else in this file needed one.
_GAVE_UP = (
    "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
    " VALUES (?, 'thumbnails', 'not_decodable', 'seeded', 0, 1)"
)

#: Who a username belongs to. `seed_username` writes the row and nothing else, because nothing else
#: needed the link; the one row here that does is written on top of it.
_USERNAME_BELONGS_TO = "UPDATE usernames SET person_id = ? WHERE id = ?"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The real application over a library of its own.

    **The worker pool RUNS here, and it has to.** Visibility is stored: a file becomes visible to
    a user when the resolver writes its row, and the resolver is background work. Kept from
    starting, as the authorization matrix keeps it (that gate asks who is turned away, not what is
    listed), every wall here answers with an empty list and every count agrees with it at nought:
    the gate passes, proves nothing, and says so nowhere.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI, tmp_path: Path) -> Iterator[TestClient]:
    """An admin, signed in, with two files and a concealed one among them.

    An ADMIN, because an admin is past every access rule, so the only thing that can take a file
    off a screen here is the vault, which is what this gate is about. A guest would answer the same
    questions with grants in the way, and a count that agreed with its list because both were empty
    would prove nothing.
    """
    with TestClient(app) as running:
        db_path = Path(running.app.state.database.path)  # type: ignore[attr-defined]
        cache_dir = Path(running.app.state.settings.cache_dir)  # type: ignore[attr-defined]
        library = tmp_path / "library"
        seed_root(db_path, A_ROOT, folder_id=A_FOLDER, path=library)
        for asset_id, filename in ((OPEN_FILE, "open.mp4"), (SHUT_FILE, "shut.mp4")):
            seed_asset(
                db_path,
                asset_id,
                root_id=A_ROOT,
                folder_id=A_FOLDER,
                root_path=library,
                cache_dir=cache_dir,
                filename=filename,
            )
        seed_person(db_path, SUBJECT, SUBJECT_NAME)
        seed_person(db_path, PARTNER, PARTNER_NAME)
        seed_person(db_path, UNNAMED, UNNAMED_NAME)
        seed_tag(db_path, A_TAG, TAG_NAME)
        seed_tag(db_path, SHUT_TAG, SHUT_TAG_NAME)
        seed_tag(db_path, MARK_TAG, MARK_TAG_NAME)
        seed_site(db_path, A_SITE, SITE_NAME)
        seed_username(db_path, A_USERNAME, A_SITE, USERNAME_NAME)
        seed_username(db_path, HER_USERNAME, A_SITE, HER_USERNAME_NAME)
        seed_collection(db_path, A_COLLECTION, COLLECTION_NAME, holding=OPEN_FILE)
        seed_photo_set(db_path, A_PHOTO_SET, PHOTO_SET_NAME, holding=OPEN_FILE)
        seed_song(db_path, A_SONG, SONG_NAME, holding=OPEN_FILE)
        write_rows(
            db_path,
            [
                (_ALSO_IN_COLLECTION, (A_COLLECTION, SHUT_FILE)),
                (_ALSO_IN_PHOTO_SET, (A_PHOTO_SET, SHUT_FILE)),
                (_ALSO_ON_SONG, (SHUT_FILE, A_SONG)),
                (_A_PROPOSAL, (A_SHOOT, SUBJECT)),
                (_A_PROPOSAL_ITEM, (A_SHOOT, OPEN_FILE, 0)),
                (_A_PROPOSAL_ITEM, (A_SHOOT, SHUT_FILE, 1)),
                (_USERNAME_BELONGS_TO, (UNNAMED, HER_USERNAME)),
                (_A_FACE, (OPEN_FACE, OPEN_FILE, SUBJECT, "matched")),
                (_A_FACE, (SHUT_FACE, SHUT_FILE, SUBJECT, "matched")),
                (_A_FACE, (AGREED_FACE, OPEN_FILE, SUBJECT, "confirmed")),
                (_GAVE_UP, (OPEN_FILE,)),
                (_GAVE_UP, (SHUT_FILE,)),
            ],
        )
        # The People Sift can recognize wall answers only while the feature is switched on.
        set_app_setting(db_path, "faces.enabled", "true")
        seed_loop(db_path, OPEN_MARK, OPEN_FILE)
        seed_loop(db_path, SHUT_MARK, SHUT_FILE)
        # ...and the same two marks carry A_TAG as well, which their files carry too: a mark that
        # tag's card reaches BOTH ways (as a pair and through `loop_tags`) is one mark, and the
        # Loops cell must say so or it reads two over a wall of one.
        write_rows(
            db_path,
            [
                (_MARK_TAGGED, (OPEN_MARK, MARK_TAG)),
                (_MARK_TAGGED, (SHUT_MARK, MARK_TAG)),
                (_MARK_TAGGED, (OPEN_MARK, A_TAG)),
                (_MARK_TAGGED, (SHUT_MARK, A_TAG)),
            ],
        )
        for asset_id in (OPEN_FILE, SHUT_FILE):
            for person_id in (SUBJECT, PARTNER):
                attach_person(db_path, asset_id, person_id)
            attach_tag(db_path, asset_id, A_TAG)
            attach_username(db_path, asset_id, A_USERNAME)
            attach_username(db_path, asset_id, HER_USERNAME)
        # The concealed person goes on the concealed file ALONE, and that is a fact about the model
        # rather than tidiness: concealing somebody conceals the files they are in. Put on both,
        # they conceal the whole library, and every count then agrees with every list at nought.
        attach_person(db_path, SHUT_FILE, UNNAMED)
        attach_tag(db_path, SHUT_FILE, SHUT_TAG)

        user_id, token, csrf = establish_session(
            db_path, role="admin", username="counted-admin", password=PASSWORD
        )
        running.cookies.set(SESSION_COOKIE_NAME, token)
        running.headers[CSRF_HEADER_NAME] = csrf
        assert user_id
        # The vault is never opened in this file. It starts shut and the user holds no PIN, so
        # there is nothing here that could open it by accident.
        hide_for_caller(running, "asset", SHUT_FILE)
        hide_for_caller(running, "person", UNNAMED)
        yield running


def _json(client: TestClient, path: str) -> Any:
    answer = client.get(path)
    assert answer.status_code == 200, f"{path} answered {answer.status_code}"
    return answer.json()


def _row(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    """The one row of a list naming this thing, or an empty one.

    Empty rather than an error, because a row that is absent is a perfectly good answer here: a
    concealed person has no row on any wall, and the count beside them still has to be nought. An
    exception would turn the interesting case into a crash.
    """
    for row in rows:
        if row.get(key) == value:
            return row
    return {}


@dataclass(frozen=True, slots=True)
class Drawn:
    """One number a screen draws, and the list it claims to be the size of.

    `where` names the statement or the route that produces the number, so a failure says which
    piece of SQL to go and read rather than only which screen looked wrong.
    """

    what: str
    where: str
    number: Callable[[TestClient], int]
    listing: Callable[[TestClient], int]


#: How many of anything the wall shows, asked at the cap the client asks at. A page of one would
#: buy the total and not the LENGTH, and the length is the thing a count has to agree with.
_PAGE = "limit=200"


def _files_where(query: str) -> Callable[[TestClient], int]:
    """The grid, narrowed the way the client narrows it: by the NAME, as the chip is written."""
    return lambda client: len(_json(client, f"/api/assets?{query}&{_PAGE}")["items"])


#: Every number the client draws off a file or a person, beside the list each one describes.
#:
#: Written out rather than discovered. "Every count in the API" is not something a pattern can
#: tell (a queue depth, a job count and a page cap are all integers on the wire and none of them
#: is about what this user may see), and the thought that belongs in a row here is which LIST
#: the number claims to be the size of. That question is the whole gate.
COUNTS: tuple[Drawn, ...] = (
    Drawn(
        what="the number on a person's card, on the People wall",
        where="`whole` in _VISIBLE_PEOPLE (repository/entities.py)",
        number=lambda client: _row(
            _json(client, f"/api/people?prefix={SUBJECT_NAME}&{_PAGE}")["items"], "id", SUBJECT
        ).get("asset_count", 0),
        listing=_files_where(f"people={SUBJECT_NAME}"),
    ),
    Drawn(
        # A Seen with card counts the wall its press opens, so the client asks for `count=narrowed`
        # and the list it claims to be the size of is the two of them together rather than the
        # partner's own. Both halves are scoped by the same line of the same statement, which is
        # why the gate can see nothing wrong either way, and why the row has to name the right
        # pair.
        what="the number on a card of the Seen with tab",
        where="`counted` in _VISIBLE_PEOPLE (repository/wall_people.py), asked for by `count` (slices/people/router_people.py)",
        number=lambda client: _row(
            _json(client, f"/api/people?with_person={SUBJECT}&count=narrowed&{_PAGE}")["items"],
            "id",
            PARTNER,
        ).get("asset_count", 0),
        listing=_files_where(f"people={SUBJECT_NAME}&people={PARTNER_NAME}"),
    ),
    Drawn(
        what="the number on a concealed person's card",
        where="`:reveal_named` in _VISIBLE_PEOPLE: the row goes, so the number must be nought",
        number=lambda client: _row(
            _json(client, f"/api/people?prefix={UNNAMED_NAME}&{_PAGE}")["items"], "id", UNNAMED
        ).get("asset_count", 0),
        listing=lambda client: 0,
    ),
    Drawn(
        what="the number on a tag's card",
        where="`whole` in _VISIBLE_TAGS (repository/entities.py)",
        number=lambda client: _row(_json(client, f"/api/tags?{_PAGE}")["items"], "id", A_TAG).get(
            "asset_count", 0
        ),
        listing=_files_where(f"tags={TAG_NAME}"),
    ),
    Drawn(
        what="the number on a collection's card",
        where="`whole` in _VISIBLE_COLLECTIONS (repository/entities.py)",
        number=lambda client: _row(
            _json(client, f"/api/collections?{_PAGE}")["items"], "id", A_COLLECTION
        ).get("item_count", 0),
        listing=_files_where(f"collections={COLLECTION_NAME}"),
    ),
    Drawn(
        what="the number on a Photo Set's card",
        where="`whole` in _VISIBLE_PHOTO_SETS (repository/entities.py)",
        number=lambda client: _row(
            _json(client, f"/api/photo-sets?{_PAGE}")["items"], "id", A_PHOTO_SET
        ).get("item_count", 0),
        listing=_files_where(f"photo_sets={PHOTO_SET_NAME}"),
    ),
    Drawn(
        what="the number on a song's card",
        where="`whole` in _VISIBLE_SONGS (repository/entities.py)",
        number=lambda client: _row(_json(client, f"/api/songs?{_PAGE}")["items"], "id", A_SONG).get(
            "item_count", 0
        ),
        listing=_files_where(f"songs={A_SONG}"),
    ),
    Drawn(
        what="the files on a Site's card",
        where="`whole` in _VISIBLE_SITES (repository/entities.py)",
        number=lambda client: _row(_json(client, f"/api/sites?{_PAGE}")["items"], "id", A_SITE).get(
            "asset_count", 0
        ),
        listing=_files_where(f"sites={SITE_NAME}"),
    ),
    Drawn(
        what="the people on a Site's card",
        where="the site card's People cell, `_CARD_COUNTS` (repository/entities.py)",
        number=lambda client: _row(_json(client, f"/api/sites?{_PAGE}")["items"], "id", A_SITE).get(
            "people_count", 0
        ),
        # The site's People tab is the people wall narrowed to the site; it has no route of its
        # own.
        listing=lambda client: len(_json(client, f"/api/people?site={A_SITE}&{_PAGE}")["items"]),
    ),
    Drawn(
        what="the number on a username's card",
        where="`whole` in _VISIBLE_USERNAMES (repository/entities.py)",
        number=lambda client: _row(
            _json(client, f"/api/usernames?{_PAGE}")["items"], "id", A_USERNAME
        ).get("asset_count", 0),
        listing=lambda client: len(
            _json(client, f"/api/assets?username={A_USERNAME}&{_PAGE}")["items"]
        ),
    ),
    Drawn(
        # NOT A COUNT, and it is here because it fails the same way, in the same statement, under
        # the same flag, and because the rule this whole file is the check for is one sentence:
        # locked, a concealed person is neither counted nor listed nor NAMED. A username is not the
        # person and keeps its row; what may not appear on it is who it belongs to.
        #
        # Said as one and nought so it can sit in the same table as the numbers: a name on the row
        # is one thing the screen showed, and nothing is what it should have shown.
        what="a concealed person's name, on the username that belongs to them",
        where="`:reveal_named` on `pe.name` in _VISIBLE_USERNAMES (repository/entities.py)",
        number=lambda client: sum(
            1
            for row in _json(client, f"/api/usernames?{_PAGE}")["items"]
            if row.get("person_name") == UNNAMED_NAME or row.get("person_id") == UNNAMED
        ),
        listing=lambda client: 0,
    ),
    Drawn(
        what="Files, on a person's tab strip",
        where="visible_assets, at limit=1 (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["files"],
        listing=_files_where(f"people={SUBJECT_NAME}"),
    ),
    Drawn(
        what="Tags, on a person's tab strip",
        where="list_tags, at limit=1 (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["tags"],
        listing=lambda client: len(_json(client, f"/api/tags?person={SUBJECT}&{_PAGE}")["items"]),
    ),
    Drawn(
        what="Collections, on a person's tab strip",
        where="list_collections, at limit=1 (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["collections"],
        listing=lambda client: len(
            _json(client, f"/api/collections?person={SUBJECT}&{_PAGE}")["items"]
        ),
    ),
    Drawn(
        what="Photo Sets, on a person's tab strip",
        where="list_photo_sets, at limit=1 (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["photo_sets"],
        listing=lambda client: len(
            _json(client, f"/api/photo-sets?person={SUBJECT}&{_PAGE}")["items"]
        ),
    ),
    Drawn(
        what="Sites, on a person's tab strip",
        where="list_sites, at limit=1 (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["sites"],
        listing=lambda client: len(_json(client, f"/api/sites?person={SUBJECT}&{_PAGE}")["items"]),
    ),
    Drawn(
        what="Seen with, on a person's tab strip",
        where="`_people` (slices/related/router.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["people"],
        listing=lambda client: len(
            _json(client, f"/api/people?with_person={SUBJECT}&{_PAGE}")["items"]
        ),
    ),
    Drawn(
        what="History, on a person's tab strip",
        where="history_count_of_person (kernel/access/history_person.py)",
        number=lambda client: _json(client, f"/api/related/person/{SUBJECT}")["history"] or 0,
        listing=lambda client: len(_json(client, f"/api/people/{SUBJECT}/history")),
    ),
    Drawn(
        what="a facet row under the grid's People dimension",
        where="facet_query (repository/assets.py)",
        # By the NAME, which is what a facet row on this dimension carries: the row is pressed to
        # write the filter that finds its own files, and the query language names people by name.
        number=lambda client: _row(
            _json(client, "/api/assets/facets?facet=people")["values"], "value", SUBJECT_NAME
        ).get("count", 0),
        listing=_files_where(f"people={SUBJECT_NAME}"),
    ),
    Drawn(
        what="the faces on a person's card, on People Sift can recognize",
        where="Store.face_bands, over viewer_entity_counts kind face_band (slices/faces)",
        number=lambda client: _row(
            _json(client, f"/api/faces/identified/people?{_PAGE}")["people"], "person_id", SUBJECT
        ).get("size", 0),
        listing=lambda client: len(
            _json(client, f"/api/faces/identified/people/{SUBJECT}?{_PAGE}")["items"]
        ),
    ),
    Drawn(
        what="the faces Sift matched on its own, on the same card",
        where="Store.face_bands, the 'matched' band (slices/faces)",
        number=lambda client: _row(
            _json(client, f"/api/faces/identified/people?{_PAGE}")["people"], "person_id", SUBJECT
        ).get("matched", 0),
        listing=lambda client: len(
            _json(client, f"/api/faces/identified/people/{SUBJECT}?attribution=matched&{_PAGE}")[
                "items"
            ]
        ),
    ),
    Drawn(
        what="the pictures on a card of the shoots board",
        where="_WAITING (slices/shoots/store.py), drawn by slices/shoots/router.py",
        number=lambda client: _row(_json(client, "/api/shoots")["shoots"], "id", A_SHOOT).get(
            "pictures", 0
        ),
        listing=lambda client: len(
            _row(_json(client, "/api/shoots")["shoots"], "id", A_SHOOT).get("items", [])
        ),
    ),
    Drawn(
        # The count in "24 files couldn't have thumbnails generated and are left out" is a link to
        # the Files wall filtered to those files, so it is that wall's size and nothing more.
        what="the files a product gave up on, on the Importing pane",
        where="`_left_out_count` (slices/importing/router.py), Repository.count_visible",
        number=lambda client: _row(
            _json(client, "/api/importing/build")["rows"], "key", "thumbnails"
        ).get("cannot", 0),
        listing=_files_where("left_out=thumbnails"),
    ),
)


#: The cards on the five walls that draw what their row REACHES ("3 photo sets, 12 tags") beside
#: the name: the wall, the row's id, and the query word that narrows another wall to that row.
#:
#: Each cell is read off the stored pairs (`viewer_pair_counts`, through `Repository.card_counts`)
#: and each has to agree with the length of the wall its press opens: the same list the entity
#: page's tab strip counts. The cells are taken from `CARD_TABS` rather than written again here, so
#: a cell added to a card is checked by being added; a cell a card stops carrying fails the key
#: check in `test_every_card_carries_its_cells` instead of passing quietly.
_CARDS = (
    ("person", f"/api/people?prefix={SUBJECT_NAME}&{_PAGE}", SUBJECT, "person"),
    ("tag", f"/api/tags?{_PAGE}", A_TAG, "tag"),
    # The concealed file's own tag. An admin's Tags wall lists it anyway (an editor sees every
    # tag), so its card is drawn, and with the vault shut every cell on it must be the nought its
    # related walls are, while in placeholder mode each must count the file the tile stands for.
    ("tag", f"/api/tags?{_PAGE}", SHUT_TAG, "tag"),
    # The tag only the marks carry: its Loops cell is the one read off the marks' own counts
    # rather than a stored pair, and it must hold the concealed file's mark back all the same.
    ("tag", f"/api/tags?{_PAGE}", MARK_TAG, "tag"),
    ("site", f"/api/sites?{_PAGE}", A_SITE, "site"),
    ("collection", f"/api/collections?{_PAGE}", A_COLLECTION, "collection"),
    ("photo_set", f"/api/photo-sets?{_PAGE}", A_PHOTO_SET, "photo_set"),
    ("song", f"/api/songs?{_PAGE}", A_SONG, "song"),
)

#: The wall each cell's press opens.
_WALL_OF_TAB = {
    "photo_sets": "/api/photo-sets",
    "loops": "/api/loops",
    "tags": "/api/tags",
    "sites": "/api/sites",
    "sites_within": "/api/sites",
    "collections": "/api/collections",
    "people": "/api/people",
    # The Music tab: the songs a row's files carry.
    "songs": "/api/songs",
}


def _card_cells(client: TestClient, wall: str, object_id: str) -> dict[str, int]:
    return dict(_row(_json(client, wall)["items"], "id", object_id).get("counts") or {})


def _card_cell(wall: str, object_id: str, tab: str) -> Callable[[TestClient], int]:
    # Minus one for a cell the card did not carry: no list is that long, so it reads as a failure.
    return lambda client: _card_cells(client, wall, object_id).get(tab, -1)


def _related_wall(tab: str, word: str, object_id: str) -> Callable[[TestClient], int]:
    # The Sites within a Site are its children: the wall is asked by parent, not by the kind word.
    asked = "parent" if tab == "sites_within" else word
    return lambda client: len(
        _json(client, f"{_WALL_OF_TAB[tab]}?{asked}={object_id}&{_PAGE}")["items"]
    )


COUNTS = COUNTS + tuple(
    Drawn(
        what=f"the {tab} cell on a {kind}'s card",
        where=(
            "_CARD_COUNTS (repository/entities.py), over viewer_pair_counts and the partner"
            " totals they keep, viewer_partner_counts (visibility.py)"
        ),
        number=_card_cell(wall, object_id, tab),
        listing=_related_wall(tab, word, object_id),
    )
    for kind, wall, object_id, word in _CARDS
    for tab in CARD_TABS[kind]
)


def _size_of(client: TestClient, asset_id: str) -> int:
    """One file's size, off the file's own record: a tile on the grid does not carry it."""
    return int(_json(client, f"/api/assets/{asset_id}")["size_bytes"] or 0)


def _bytes_where(query: str) -> Callable[[TestClient], int]:
    """The grid narrowed as `_files_where` narrows it, as the sizes of its files added up."""
    return lambda client: sum(
        _size_of(client, one["id"])
        for one in _json(client, f"/api/assets?{query}&{_PAGE}")["items"]
    )


def _card_size(wall: str, key: str, value: str) -> Callable[[TestClient], int]:
    """The size a card says beside its count, off the wall it is drawn on."""
    return lambda client: int(
        _row(_json(client, f"{wall}{_PAGE}")["items"], key, value).get("size_bytes") or 0
    )


#: Every SIZE the client says beside a count of files, beside the files that count holds.
#:
#: A size is a count of bytes and outruns its list the same way a count does: a card saying the
#: size of two files over a wall of one has said there is a second file, and roughly how big. Every
#: seeded file is the same size, so a size that counted the concealed one reads double.
SIZES: tuple[Drawn, ...] = (
    Drawn(
        what="the size on a person's card, on the People wall",
        where="`whole` in _VISIBLE_PEOPLE, off viewer_entity_counts (repository/entities.py)",
        number=_card_size(f"/api/people?prefix={SUBJECT_NAME}&", "id", SUBJECT),
        listing=_bytes_where(f"people={SUBJECT_NAME}"),
    ),
    Drawn(
        what="the size on a card of the Seen with tab",
        where="`counted` in _VISIBLE_PEOPLE (repository/wall_people.py), asked for by `count` (slices/people/router_people.py)",
        number=_card_size(f"/api/people?with_person={SUBJECT}&count=narrowed&", "id", PARTNER),
        listing=_bytes_where(f"people={SUBJECT_NAME}&people={PARTNER_NAME}"),
    ),
    Drawn(
        what="the size on a tag's card",
        where="`whole` in _VISIBLE_TAGS (repository/entities.py)",
        number=_card_size("/api/tags?", "id", A_TAG),
        listing=_bytes_where(f"tags={TAG_NAME}"),
    ),
    Drawn(
        what="the size on a collection's card",
        where="`whole` in _VISIBLE_COLLECTIONS (repository/entities.py)",
        number=_card_size("/api/collections?", "id", A_COLLECTION),
        listing=_bytes_where(f"collections={COLLECTION_NAME}"),
    ),
    Drawn(
        what="the size on a Photo Set's card",
        where="`whole` in _VISIBLE_PHOTO_SETS (repository/entities.py)",
        number=_card_size("/api/photo-sets?", "id", A_PHOTO_SET),
        listing=_bytes_where(f"photo_sets={PHOTO_SET_NAME}"),
    ),
    Drawn(
        what="the size on a song's card",
        where="`whole` in _VISIBLE_SONGS (repository/entities.py)",
        number=_card_size("/api/songs?", "id", A_SONG),
        listing=_bytes_where(f"songs={A_SONG}"),
    ),
    Drawn(
        what="the size on a Site's card",
        where="`whole` in _VISIBLE_SITES (repository/entities.py)",
        number=_card_size("/api/sites?", "id", A_SITE),
        listing=_bytes_where(f"sites={SITE_NAME}"),
    ),
    Drawn(
        what="the size on a username's card",
        where="`whole` in _VISIBLE_USERNAMES (repository/entities.py)",
        number=_card_size("/api/usernames?", "id", A_USERNAME),
        listing=_bytes_where(f"username={A_USERNAME}"),
    ),
    Drawn(
        what="the size beside Files, on a person's tab strip and hover card",
        where="visible_assets, at limit=1 (slices/related/router.py)",
        number=lambda client: int(_json(client, f"/api/related/person/{SUBJECT}")["files_bytes"]),
        listing=_bytes_where(f"people={SUBJECT_NAME}"),
    ),
    Drawn(
        what="the size beside Browse's own count",
        where="viewer_stats, read by visible_assets (repository/store.py)",
        number=lambda client: int(_json(client, f"/api/assets?{_PAGE}")["total_bytes"]),
        listing=lambda client: sum(
            _size_of(client, one["id"]) for one in _json(client, f"/api/assets?{_PAGE}")["items"]
        ),
    ),
    Drawn(
        what="the size beside a narrowed Browse's count",
        where="assets_count_query (repository/assets.py)",
        number=lambda client: int(
            _json(client, f"/api/assets?tags={TAG_NAME}&{_PAGE}")["total_bytes"]
        ),
        listing=_bytes_where(f"tags={TAG_NAME}"),
    ),
)


def test_every_card_carries_its_cells(client: TestClient) -> None:
    """Every card on the five walls carries exactly the cells its kind draws.

    The disagreement check above reads a missing cell as minus one and would fail on it too; this
    says WHICH cells went missing, which is the thing to know when a card stops carrying one."""
    for kind, wall, object_id, _word in _CARDS:
        assert set(_card_cells(client, wall, object_id)) == set(CARD_TABS[kind]), kind


def _disagreements(client: TestClient) -> list[str]:
    """Every number that does not describe its own list, reported together.

    Together rather than one assertion each, because the answer to "is there anything else like
    it?" is a LIST, and a gate that stops at the first one makes that list arrive one failure at a
    time.
    """
    return [
        f"{drawn.what}: the screen says {number}, the list holds {held} ({drawn.where})"
        for drawn in (*COUNTS, *SIZES)
        for number, held in [(drawn.number(client), drawn.listing(client))]
        if number != held
    ]


def test_a_locked_vault_takes_a_concealed_file_out_of_every_count(client: TestClient) -> None:
    """The default mode. A concealed file is absent from the list and from the number alike.

    The two files, one of them concealed, mean every honest count here is ONE. A two is the whole
    fault: it is the screen saying there is a second file that it is not going to show you.
    """
    assert _disagreements(client) == []


def test_a_locked_vault_counts_what_a_placeholder_shows(client: TestClient) -> None:
    """And the placeholder mode, where a concealed file keeps its tile.

    The tile is there, so the number counts it, and a count reading the other vault flag would
    stay at one while the list went to two. That drift cannot be seen in the default mode at all:
    there, both flags are nought and every count agrees with every list by accident.
    """
    applied = client.put("/api/settings", json={"values": {"vault.concealment": "placeholder"}})
    assert applied.status_code == 204, "the concealment mode could not be set"
    assert _disagreements(client) == []


def test_every_size_is_said_and_is_the_open_files_alone(client: TestClient) -> None:
    """The sizes above agree with their lists, and they are not noughts agreeing with noughts.

    Every seeded file is the same size, so each size beside a count of the one open file is that
    one file's size: neither nothing (a size nobody filled in agrees with an empty sum) nor two
    files' worth (the concealed one counted)."""
    shown = _json(client, f"/api/assets?{_PAGE}")["items"]
    assert [file["id"] for file in shown] == [OPEN_FILE]
    one = _size_of(client, OPEN_FILE)
    assert one > 0
    said = {drawn.what: drawn.number(client) for drawn in SIZES}
    assert said == dict.fromkeys(said, one), said


#: The same question asked of the one other thing a card says about a file: its picture.
#:
#: A cover is not a count, and it is here rather than in a file of its own because it fails the
#: same way and in the same mode. A card cannot draw a lock (the grid can, which is what
#: placeholder mode is for), so a card handed the address of a concealed file gets a picture that
#: answers 404: a broken thumbnail, in the place where a card says whether there is anything here.
COVERS = (
    ("a tag's card", "/api/tags?limit=200", "items", A_TAG),
    ("a Site's card", "/api/sites?limit=200", "items", A_SITE),
    ("a person's card", f"/api/people?prefix={SUBJECT_NAME}&limit=200", "items", SUBJECT),
    ("a collection's card", "/api/collections?limit=200", "items", A_COLLECTION),
    ("a Photo Set's card", "/api/photo-sets?limit=200", "items", A_PHOTO_SET),
    ("a song's card", "/api/songs?limit=200", "items", A_SONG),
)

#: Every wall's cover column, pointed at the file in the vault. One statement per table because a
#: cover is a column on the thing's own row, and the five tables are five rows.
_COVERS_ONTO = (
    "UPDATE tags SET cover_asset_id = ? WHERE id = ?",
    "UPDATE sites SET cover_asset_id = ? WHERE id = ?",
    "UPDATE people SET cover_asset_id = ? WHERE id = ?",
    "UPDATE collections SET cover_asset_id = ? WHERE id = ?",
    "UPDATE photo_sets SET cover_asset_id = ? WHERE id = ?",
    "UPDATE songs SET cover_asset_id = ? WHERE id = ?",
)


def test_no_card_wears_a_concealed_file_as_its_picture(client: TestClient) -> None:
    """Every wall, in the mode where the two vault flags differ.

    Placeholder mode is the whole of the test. With the default mode both flags are nought and a
    wall reading either one answers the same, so a wall reading the looser of the two looks
    perfectly correct right up until somebody turns placeholders on.
    """
    applied = client.put("/api/settings", json={"values": {"vault.concealment": "placeholder"}})
    assert applied.status_code == 204, "the concealment mode could not be set"
    db_path = Path(client.app.state.database.path)  # type: ignore[attr-defined]
    write_rows(
        db_path,
        [
            (statement, (SHUT_FILE, object_id))
            for statement, (_what, _path, _key, object_id) in zip(_COVERS_ONTO, COVERS, strict=True)
        ],
    )
    wearing = [
        what
        for what, path, key, object_id in COVERS
        if _row(_json(client, path)[key], "id", object_id).get("cover_asset_id") == SHUT_FILE
    ]
    assert wearing == [], (
        f"{wearing} name a file in a locked vault as their picture. A card cannot draw a lock, so "
        f"what the screen gets is an address that answers 404. Gate the cover on `:reveal_named`, "
        f"which is the flag the walls that do not do this already read."
    )


#: A reference face for each of two people, so the People Sift can recognize list has both to choose from.
_A_REFERENCE = (
    "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin,"
    " recognizer, created_at) VALUES (?, ?, ?, x'00', 1.0, 'added', 'seeded', 0)"
)
#: A decision on the record, and what it was about.
_A_DECISION = (
    "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
    " actor_kind, object_kind, object_id) VALUES (?, 'identified', ?, ?, '{}', ?, 'linked', 'sift',"
    " 'person', ?)"
)
_ABOUT = "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id) VALUES (?, ?, ?)"
OPEN_DECISION = "01KZCNTDDEC1S10N0PEN000000"
SHUT_DECISION = "01KZCNTDDEC1S10NSHVT000000"
NAMED_DECISION = "01KZCNTDDEC1S10NNAMED00000"
OPEN_JOB = "01KZCNTDJ0B0PEN00000000000"
SHUT_JOB = "01KZCNTDJ0BSHVT00000000000"


@pytest.mark.parametrize("mode", ["fully_gone", "placeholder"])
def test_no_admin_screen_names_what_a_shut_vault_holds_back(client: TestClient, mode: str) -> None:
    """Names and subjects, beside the counts above: the People Sift can recognize list, the board, the
    decisions in History's record, and the task list, in both modes, since a placeholder is a locked
    tile and a name is never one.

    Each screen that draws a name is asked for one it may say as well, so a screen that went blank
    would fail here rather than pass for saying nothing.
    """
    applied = client.put("/api/settings", json={"values": {"vault.concealment": mode}})
    assert applied.status_code == 204, "the concealment mode could not be set"
    db_path = Path(client.app.state.database.path)  # type: ignore[attr-defined]
    write_rows(
        db_path,
        [
            (_A_REFERENCE, ("01KZCNTDREF0PEN00000000000", SUBJECT, "open-digest")),
            (_A_REFERENCE, ("01KZCNTDREFSHVT00000000000", UNNAMED, "shut-digest")),
            (
                _A_DECISION,
                (OPEN_DECISION, f"Recognized as {SUBJECT_NAME}", "in open.mp4", 3, SUBJECT),
            ),
            (_ABOUT, (OPEN_DECISION, "asset", OPEN_FILE)),
            (
                _A_DECISION,
                (SHUT_DECISION, "Recognized in the sealed reel", "in shut.mp4", 2, PARTNER),
            ),
            (_ABOUT, (SHUT_DECISION, "asset", SHUT_FILE)),
            (_A_DECISION, (NAMED_DECISION, f"Recognized as {UNNAMED_NAME}", "", 1, UNNAMED)),
        ],
    )
    seed_job(db_path, OPEN_JOB, state="done", job_type="thumbnail", payload={"asset_id": OPEN_FILE})
    seed_job(db_path, SHUT_JOB, state="done", job_type="thumbnail", payload={"asset_id": SHUT_FILE})

    screens = {
        "People Sift can recognize": client.get("/api/faces/known").text,
        # The screen called People Sift can recognize also asks for every person's reference count,
        # keyed by id, held to the People wall so the hidden person's id is not among them.
        "the reference counts": client.get("/api/faces/references/strength").text,
        "the board": client.get("/api/workbench").text,
        "the decisions record": client.get("/api/ledger", params={"decisions": "true"}).text,
        # The thumbnail rows run by themselves and stay off the list unless their type is asked for.
        "the task list": client.get("/api/jobs?limit=200&type=thumbnail").text,
    }
    held_back = (UNNAMED_NAME, "sealed reel", "shut.mp4", SHUT_FILE, UNNAMED)
    leaks = [(where, one) for where, text in screens.items() for one in held_back if one in text]
    assert leaks == [], f"a shut vault is named on an admin screen: {leaks}"

    assert SUBJECT_NAME in screens["People Sift can recognize"]
    assert SUBJECT in screens["the reference counts"]
    assert SUBJECT_NAME in screens["the decisions record"]
    assert "open.mp4" in screens["the task list"]
