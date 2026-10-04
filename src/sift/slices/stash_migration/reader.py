# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a Stash database: a copy of it, opened so nothing can be written.

Everything here is blocking and pure over one `sqlite3` connection, so it runs off the event loop
in a thread and can be tested against a small database shaped like Stash's without an application.

## What is read, and in what words

Stash's own rows, turned into the field keys Sift's record registry declares for a person, a Site,
a tag and a file (`kernel.records`), which are the same keys a stash-box answer arrives in. So the
import is written by the same writers that apply a stash-box answer, and nothing here decides what
a field of Sift's is called or how it is stored.

## Which Stash

Schema 85 and 86 (which adds one scene column this does not read), with `dirty = 0`: a database part way through one of Stash's own upgrades is not
read at all. Anything else is refused with the version it is, so the answer is to open it once in
the Stash that matches.

## What does not come across, and why

The dates of an O and of a view: before schema 55 Stash kept counts, and its upgrade invented a
date for each counted event, so every date from then is a date nothing happened on. The counts are
real and are read, and so are where a scene was stopped (its resume point) and how long it has been
watched in all. A marker with no end is a moment rather than a stretch, so it is read as one and
made into a short Loop by the caller, never passed off as a range.

## What has no field here, and where it goes instead

A field Sift has no place for (a scene's director, whether Stash called it organized, a custom
field, the languages of its captions, a performer's death date or weight) is not dropped: each is
said in plain words on ONE line that starts "From Stash:", put after the row's own details
(`_with_stash_line`). The line is part of the details text, so it is filled only where the row here
has no details yet, like every other field of the import.

## Pictures

A performer's, a studio's and a tag's picture is a blob named by its checksum. Stash keeps a blob
in its database (the `blobs` table's own column) or, when it is set to keep them as files, in its
blobs folder, two folder levels deep by the checksum's first four characters (`picture_of`). The
checksum is the picture's content, so a checksum read from one copy names the same picture in any
other.
"""

from __future__ import annotations

import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (a Stash database is not a Sift library; it is read through a read-only copy here and nowhere else)
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

#: The Stash schema versions this reads.
SUPPORTED_VERSIONS = (85, 86)

#: How many pages the copy takes per step, so a database of several gigabytes is copied in steps
#: rather than in one call that holds its source for minutes.
_COPY_PAGES = 4096


class NotAStashDatabase(Exception):
    """The file is not a Stash database this can read. The message says why, for a person."""


def copy_in(source: Path, target: Path) -> None:
    """Copy a Stash database to `target` through SQLite's own backup, reading it read-only.

    SQLite's backup and not a file copy: Stash keeps its newest writes in a write-ahead log beside
    the file, and a file copied while Stash runs is a torn database that opens and is quietly
    missing whatever the log held. The source is opened `mode=ro`, so it is never written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    # nosemgrep: sift-no-file-removal-outside-delete-trash (the reader's own copy of a Stash database, under Sift's cache; never a library file)
    target.unlink(missing_ok=True)
    try:
        reading = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as unopenable:
        raise NotAStashDatabase("Sift couldn't open that file as a database.") from unopenable
    try:
        writing = sqlite3.connect(target)
        try:
            reading.backup(writing, pages=_COPY_PAGES)
        except sqlite3.DatabaseError as broken:
            raise NotAStashDatabase("That file isn't a database Sift can read.") from broken
        finally:
            writing.close()
    finally:
        reading.close()


def open_copy(copy: Path) -> sqlite3.Connection:
    """Open the copy so nothing can be written to it, and check it is a Stash this reads.

    `immutable=1` as well as `mode=ro`: the copy is Sift's own and nothing else has it open, so
    SQLite may skip the locking a live file needs, and it cannot make a side file beside it.

    Usable from any thread: a run reads it through one worker-thread call after another, each
    awaited before the next, and the pool may hand each call a different thread.
    """
    connection = sqlite3.connect(
        f"{copy.resolve().as_uri()}?mode=ro&immutable=1", uri=True, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        version_of(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def version_of(connection: sqlite3.Connection) -> int:
    """Stash's schema version, or `NotAStashDatabase` with a sentence saying why not."""
    try:
        row = connection.execute("SELECT version, dirty FROM schema_migrations").fetchone()
    except sqlite3.DatabaseError as broken:
        raise NotAStashDatabase(
            "That file isn't a Stash database. Choose Stash's own database file."
        ) from broken
    if row is None:
        raise NotAStashDatabase("That Stash database doesn't say which version it is.")
    version, dirty = int(row[0]), int(row[1])
    if dirty:
        raise NotAStashDatabase(
            "That Stash database is part way through an upgrade of Stash's own. Open it in Stash "
            "once, then try again."
        )
    if version not in SUPPORTED_VERSIONS:
        raise NotAStashDatabase(
            f"That Stash database is schema version {version}. Sift reads versions "
            f"{SUPPORTED_VERSIONS[0]} and {SUPPORTED_VERSIONS[-1]}; open it once in a Stash that "
            "matches, then try again."
        )
    return version


# --- the summary --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TopFolder:
    """One of the folders Stash scans, and how many files it holds (not counting inside zips)."""

    path: str
    files: int


@dataclass(frozen=True)
class Summary:
    """What a Stash database holds, counted, before anything is brought across."""

    version: int
    people: int
    sites: int
    sites_with_a_parent: int
    tags: int
    tags_with_one_parent: int
    tags_with_several_parents: int
    scenes: int
    images: int
    #: Pictures whose every file is inside a zip. Matched by nothing yet, so said apart.
    images_in_zips: int
    galleries: int
    markers_with_an_end: int
    markers_without_an_end: int
    rated_scenes: int
    scenes_with_os: int
    scenes_viewed: int
    saved_filters: int
    folders: list[TopFolder] = field(default_factory=list)
    #: Hearts Stash kept, per kind of named thing. Scenes and images carry none in Stash.
    favorite_people: int = 0
    favorite_sites: int = 0
    favorite_tags: int = 0
    #: Stars Stash kept on performers and studios.
    rated_people: int = 0
    rated_sites: int = 0
    #: Rows Stash matched to a stash-box, per kind, whichever box it was.
    people_with_a_box_id: int = 0
    sites_with_a_box_id: int = 0
    tags_with_a_box_id: int = 0
    scenes_with_a_box_id: int = 0
    #: Saved filters over scenes and images: the only ones that can become searches over files.
    filters_over_files: int = 0
    #: Pictures Stash kept on performers, studios and tags, and how many of all those it keeps as
    #: files in its blobs folder rather than in its database (those need the folder to be read).
    people_with_a_picture: int = 0
    sites_with_a_picture: int = 0
    tags_with_a_picture: int = 0
    pictures_in_a_folder: int = 0
    #: Groups (Stash once called them movies), which become Collections of their scenes.
    groups: int = 0
    #: Scenes with a place to pick up from.
    scenes_with_a_resume_point: int = 0


_FILES_UNDER = """
WITH RECURSIVE under(id) AS (
  SELECT ? UNION SELECT f.id FROM folders f JOIN under u ON f.parent_folder_id = u.id
   WHERE f.zip_file_id IS NULL)
SELECT COUNT(*) FROM files WHERE zip_file_id IS NULL AND parent_folder_id IN (SELECT id FROM under)
"""


def _count(connection: sqlite3.Connection, statement: str) -> int:
    return int(connection.execute(statement).fetchone()[0] or 0)


def summarize(connection: sqlite3.Connection) -> Summary:
    """Every count the screen shows before a run, read straight off the database."""
    tops = connection.execute(
        "SELECT id, path FROM folders WHERE parent_folder_id IS NULL AND zip_file_id IS NULL"
        " ORDER BY path"
    ).fetchall()
    folders = []
    for top in tops:
        # Every plain file in the folder or under it. The recursive walk rather than a path prefix,
        # so a folder whose name begins with its neighbour's is not counted as part of it.
        files = int(connection.execute(_FILES_UNDER, (int(top["id"]),)).fetchone()[0] or 0)
        folders.append(TopFolder(str(top["path"]), files))
    return Summary(
        version=version_of(connection),
        people=_count(connection, "SELECT COUNT(*) FROM performers"),
        sites=_count(connection, "SELECT COUNT(*) FROM studios"),
        sites_with_a_parent=_count(
            connection, "SELECT COUNT(*) FROM studios WHERE parent_id IS NOT NULL"
        ),
        tags=_count(connection, "SELECT COUNT(*) FROM tags"),
        tags_with_one_parent=_count(
            connection,
            "SELECT COUNT(*) FROM (SELECT child_id FROM tags_relations GROUP BY child_id"
            " HAVING COUNT(*) = 1)",
        ),
        tags_with_several_parents=_count(
            connection,
            "SELECT COUNT(*) FROM (SELECT child_id FROM tags_relations GROUP BY child_id"
            " HAVING COUNT(*) > 1)",
        ),
        scenes=_count(connection, "SELECT COUNT(*) FROM scenes"),
        images=_count(connection, "SELECT COUNT(*) FROM images"),
        # Pictures ONLY inside zips: one with a plain file as well is found by that file.
        images_in_zips=_count(
            connection,
            "SELECT COUNT(*) FROM images i WHERE EXISTS (SELECT 1 FROM images_files imf"
            " WHERE imf.image_id = i.id) AND NOT EXISTS (SELECT 1 FROM images_files imf"
            " JOIN files f ON f.id = imf.file_id"
            " WHERE imf.image_id = i.id AND f.zip_file_id IS NULL)",
        ),
        galleries=_count(connection, "SELECT COUNT(*) FROM galleries"),
        markers_with_an_end=_count(
            connection, "SELECT COUNT(*) FROM scene_markers WHERE end_seconds IS NOT NULL"
        ),
        markers_without_an_end=_count(
            connection, "SELECT COUNT(*) FROM scene_markers WHERE end_seconds IS NULL"
        ),
        rated_scenes=_count(connection, "SELECT COUNT(*) FROM scenes WHERE rating IS NOT NULL"),
        scenes_with_os=_count(connection, "SELECT COUNT(DISTINCT scene_id) FROM scenes_o_dates"),
        scenes_viewed=_count(connection, "SELECT COUNT(DISTINCT scene_id) FROM scenes_view_dates"),
        saved_filters=_count(connection, "SELECT COUNT(*) FROM saved_filters"),
        folders=folders,
        favorite_people=_count(connection, "SELECT COUNT(*) FROM performers WHERE favorite"),
        favorite_sites=_count(connection, "SELECT COUNT(*) FROM studios WHERE favorite"),
        favorite_tags=_count(connection, "SELECT COUNT(*) FROM tags WHERE favorite"),
        rated_people=_count(connection, "SELECT COUNT(*) FROM performers WHERE rating IS NOT NULL"),
        rated_sites=_count(connection, "SELECT COUNT(*) FROM studios WHERE rating IS NOT NULL"),
        people_with_a_box_id=len({one.owner for one in box_ids(connection, "performer")}),
        sites_with_a_box_id=len({one.owner for one in box_ids(connection, "studio")}),
        tags_with_a_box_id=len({one.owner for one in box_ids(connection, "tag")}),
        scenes_with_a_box_id=len({one.owner for one in box_ids(connection, "scene")}),
        filters_over_files=_count(
            connection,
            "SELECT COUNT(*) FROM saved_filters WHERE UPPER(mode) IN ('SCENES', 'IMAGES')",
        ),
        people_with_a_picture=_count(
            connection, "SELECT COUNT(*) FROM performers WHERE image_blob IS NOT NULL"
        ),
        sites_with_a_picture=_count(
            connection, "SELECT COUNT(*) FROM studios WHERE image_blob IS NOT NULL"
        ),
        tags_with_a_picture=_count(
            connection, "SELECT COUNT(*) FROM tags WHERE image_blob IS NOT NULL"
        ),
        pictures_in_a_folder=_count(connection, _PICTURES_IN_A_FOLDER),
        groups=_count(connection, "SELECT COUNT(*) FROM groups"),
        scenes_with_a_resume_point=_count(
            connection, "SELECT COUNT(*) FROM scenes WHERE resume_time > 0"
        ),
    )


#: The pictures performers, studios and tags name whose bytes are not in the database: Stash keeps
#: those as files in its blobs folder.
_PICTURES_IN_A_FOLDER = """
SELECT COUNT(*) FROM (
  SELECT image_blob AS checksum FROM performers WHERE image_blob IS NOT NULL
  UNION SELECT image_blob FROM studios WHERE image_blob IS NOT NULL
  UNION SELECT image_blob FROM tags WHERE image_blob IS NOT NULL) named
 WHERE NOT EXISTS (SELECT 1 FROM blobs b WHERE b.checksum = named.checksum AND b.blob IS NOT NULL)
"""


# --- the records, in Sift's field keys -----------------------------------------------------


def _year(value: object) -> int | None:
    """The year of a Stash date (year, month and day joined by dashes), or None."""
    text = str(value or "")
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _grouped(connection: sqlite3.Connection, statement: str) -> dict[int, list[str]]:
    """Rows of (owner id, text) as a list per owner, in the order the statement gives."""
    found: dict[int, list[str]] = {}
    for owner, text in connection.execute(statement):
        if text:
            found.setdefault(int(owner), []).append(str(text))
    return found


def _plain(value: object) -> str:
    """A Stash value as text: bytes read as UTF-8, a JSON string unwrapped, a number as written."""
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            found = json.loads(text)
        except ValueError:
            return text
        return str(found) if isinstance(found, str) else text
    return text


def _custom_fields(connection: sqlite3.Connection, table: str, owner: str) -> dict[int, list[str]]:
    """Every custom field Stash keeps on one kind of row, as "name: value", by row."""
    # `table` and `owner` are constants from this module's own callers, never a value from
    # the file being read.
    rows = connection.execute(
        f"SELECT {owner}, field, value FROM {table} ORDER BY {owner}, field"  # noqa: S608  # nosemgrep: sift-no-string-built-sql
    )
    found: dict[int, list[str]] = {}
    for owner_id, name, value in rows:
        said = _plain(value)
        if str(name or "").strip() and said:
            found.setdefault(int(owner_id), []).append(f"{str(name).strip()}: {said}")
    return found


#: What starts the one line a row's details get for what Sift has no field for.
FROM_STASH = "From Stash:"


def _with_stash_line(details: object, said: Sequence[str]) -> str | None:
    """A row's details with one "From Stash:" line after them, saying each field Sift has no place
    for; the details alone where there is nothing to say, and None where there is neither."""
    text = str(details or "").strip()
    parts = [one.strip() for one in said if one and one.strip()]
    if not parts:
        return text or None
    line = f"{FROM_STASH} {'; '.join(parts)}"
    return f"{text}\n\n{line}" if text else line


def _flag(value: object, word: str) -> list[str]:
    """`word` when a Stash yes-or-no column says yes, else nothing."""
    return [word] if value else []


def _captions(connection: sqlite3.Connection) -> dict[int, list[str]]:
    """The caption languages of each scene's files, by scene: Sift reads no caption file."""
    return _grouped(
        connection,
        "SELECT DISTINCT sf.scene_id, vc.language_code FROM scenes_files sf"
        " JOIN video_captions vc ON vc.file_id = sf.file_id ORDER BY sf.scene_id, vc.language_code",
    )


def stars(rating: object) -> int | None:
    """A Stash rating (1 to 100) as Sift's (1 to 10), a half rounded up, never nought.

    Half up, as a person reads a half: Python's `round` takes a half to the even number, which
    would read 25 as 2 and 35 as 4."""
    if rating is None:
        return None
    return min(10, max(1, int(int(str(rating)) / 10 + 0.5)))


@dataclass(frozen=True)
class Entity:
    """One performer, studio or tag, as the fields Sift's writers take, and the opinion on it."""

    stash_id: int
    name: str
    fields: Mapping[str, object]
    favorite: bool = False
    rating: int | None = None
    #: The one parent by name, for a studio or a tag that has exactly one.
    parent: str | None = None
    #: The checksum of the picture Stash kept on it, read with `picture_of`; None for none.
    picture: str | None = None


def _circumcised(value: object) -> list[str]:
    """Stash's CUT or UNCUT, in words."""
    word = str(value or "").strip().upper()
    return ["circumcised"] if word == "CUT" else ["not circumcised"] if word == "UNCUT" else []


def performers(connection: sqlite3.Connection) -> Iterator[Entity]:
    """Every performer, as a person's fields (the keys a stash-box answer uses)."""
    aliases = _grouped(connection, "SELECT performer_id, alias FROM performer_aliases")
    links = _grouped(
        connection, "SELECT performer_id, url FROM performer_urls ORDER BY performer_id, position"
    )
    labels = _names_by(connection, "performers_tags", "performer_id", "tag_id", "tags")
    custom = _custom_fields(connection, "performer_custom_fields", "performer_id")
    for row in connection.execute("SELECT * FROM performers ORDER BY id"):
        stash_id = int(row["id"])
        # What a person here has no field for, said on the one line (see the module's own words).
        said = [
            *([f"death date {row['death_date']}"] if row["death_date"] else []),
            *([f"weight {row['weight']} kg"] if row["weight"] else []),
            *([f"penis length {row['penis_length']:g} cm"] if row["penis_length"] else []),
            *_circumcised(row["circumcised"]),
            *custom.get(stash_id, []),
        ]
        fields: dict[str, object] = {
            "name": row["name"],
            "aliases": aliases.get(int(row["id"]), []),
            "disambiguation": row["disambiguation"],
            "gender": row["gender"],
            "birth_date": row["birthdate"],
            "country": row["country"],
            "ethnicity": row["ethnicity"],
            "eye_color": row["eye_color"],
            "hair_color": row["hair_color"],
            "height_cm": row["height"],
            "measurements": row["measurements"],
            "breast_type": str(row["fake_tits"]).upper() if row["fake_tits"] else None,
            "career_start_year": _year(row["career_start"]),
            "career_end_year": _year(row["career_end"]),
            "tattoos": row["tattoos"],
            "piercings": row["piercings"],
            "links": links.get(stash_id, []),
            "tags": labels.get(stash_id, []),
            "details": _with_stash_line(row["details"], said),
        }
        yield Entity(
            stash_id=stash_id,
            name=str(row["name"]),
            fields={key: value for key, value in fields.items() if value not in (None, "", [])},
            favorite=bool(row["favorite"]),
            rating=stars(row["rating"]),
            picture=row["image_blob"] or None,
        )


def studios(connection: sqlite3.Connection) -> Iterator[Entity]:
    """Every studio, as a Site's fields, with its parent by name."""
    aliases = _grouped(connection, "SELECT studio_id, alias FROM studio_aliases")
    links = _grouped(
        connection, "SELECT studio_id, url FROM studio_urls ORDER BY studio_id, position"
    )
    names = {
        int(one["id"]): str(one["name"])
        for one in connection.execute("SELECT id, name FROM studios")
    }
    labels = _names_by(connection, "studios_tags", "studio_id", "tag_id", "tags")
    custom = _custom_fields(connection, "studio_custom_fields", "studio_id")
    for row in connection.execute("SELECT * FROM studios ORDER BY id"):
        stash_id = int(row["id"])
        parent = names.get(int(row["parent_id"])) if row["parent_id"] is not None else None
        said = [*_flag(row["organized"], "organized"), *custom.get(stash_id, [])]
        fields: dict[str, object] = {
            "name": row["name"],
            "aliases": aliases.get(stash_id, []),
            "parent": parent,
            "links": links.get(stash_id, []),
            "tags": labels.get(stash_id, []),
            "details": _with_stash_line(row["details"], said),
        }
        yield Entity(
            stash_id=stash_id,
            name=str(row["name"]),
            fields={key: value for key, value in fields.items() if value not in (None, "", [])},
            favorite=bool(row["favorite"]),
            rating=stars(row["rating"]),
            parent=parent,
            picture=row["image_blob"] or None,
        )


def tags(connection: sqlite3.Connection) -> Iterator[Entity]:
    """Every tag, as a tag's fields, with its parent by name where it has exactly one.

    A tag Stash files under several parents keeps none here: Sift's tags form a tree, and choosing
    one of several would be choosing at random. The caller says which ones in its report.
    """
    aliases = _grouped(connection, "SELECT tag_id, alias FROM tag_aliases")
    names = {
        int(one["id"]): str(one["name"]) for one in connection.execute("SELECT id, name FROM tags")
    }
    parents: dict[int, list[int]] = {}
    for parent_id, child_id in connection.execute("SELECT parent_id, child_id FROM tags_relations"):
        parents.setdefault(int(child_id), []).append(int(parent_id))
    custom = _custom_fields(connection, "tag_custom_fields", "tag_id")
    for row in connection.execute("SELECT * FROM tags ORDER BY id"):
        stash_id = int(row["id"])
        above = parents.get(stash_id, [])
        # A tag with several parents keeps none (above); the others are said on its line instead.
        others = [names[one] for one in above if one in names] if len(above) > 1 else []
        said = [
            *([f"sorted as {row['sort_name']}"] if row["sort_name"] else []),
            *([f"filed under {', '.join(sorted(others))}"] if others else []),
            *custom.get(stash_id, []),
        ]
        fields: dict[str, object] = {
            "name": row["name"],
            "aliases": aliases.get(stash_id, []),
            "description": _with_stash_line(row["description"], said),
        }
        yield Entity(
            stash_id=stash_id,
            name=str(row["name"]),
            fields={key: value for key, value in fields.items() if value not in (None, "", [])},
            favorite=bool(row["favorite"]),
            parent=names.get(above[0]) if len(above) == 1 else None,
            picture=row["image_blob"] or None,
        )


def tags_with_several_parents(connection: sqlite3.Connection) -> list[str]:
    """The names of the tags Stash files under more than one parent, for the report."""
    return [
        str(one[0])
        for one in connection.execute(
            "SELECT t.name FROM tags t JOIN tags_relations r ON r.child_id = t.id"
            " GROUP BY t.id HAVING COUNT(*) > 1 ORDER BY t.name"
        )
    ]


# --- scenes and images, with the files they are ------------------------------------------------


@dataclass(frozen=True)
class StashFile:
    """One file Stash holds: where it is, the two fingerprints Sift keeps too, and its size and
    length, which say which file it was to a person reading the list of what waits for it."""

    path: str
    oshash: str | None = None
    #: Stash's video fingerprint, spelt as Sift spells one: sixteen hexadecimal digits.
    phash: str | None = None
    in_zip: bool = False
    size_bytes: int | None = None
    #: A video's length in milliseconds, from Stash's reading of it; None for a picture.
    duration_ms: int | None = None


@dataclass(frozen=True)
class Item:
    """One scene or image: its files, its record in Sift's field keys, and the opinions on it."""

    stash_id: int
    kind: str
    files: Sequence[StashFile]
    fields: Mapping[str, object]
    rating: int | None = None
    o_count: int = 0
    views: int = 0
    #: Where Stash would pick the scene up again, in milliseconds; None for nowhere.
    resume_ms: int | None = None
    #: How long it has been watched in all, in milliseconds. A total, never a date.
    played_ms: int = 0


def _ms(seconds: object) -> int:
    """Stash's seconds (a float) as whole milliseconds; nothing and below nothing read as none."""
    try:
        return max(0, round(float(str(seconds or 0)) * 1000))
    except ValueError:
        return 0


def stash_path(path: str) -> PurePath:
    """A path as the Stash that wrote it spells it: a Windows one by its drive or its backslashes,
    else a POSIX one, so its folders and its name read the same on whichever system Sift runs."""
    windows = "\\" in path or (len(path) > 1 and path[1] == ":")
    return PureWindowsPath(path) if windows else PurePosixPath(path)


def phash_hex(value: object) -> str | None:
    """Stash's phash (a signed 64-bit number) as sixteen hexadecimal digits, the way Sift keeps one."""
    if value is None:
        return None
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return f"{number & 0xFFFFFFFFFFFFFFFF:016x}"


def _file_of(row: sqlite3.Row) -> StashFile:
    duration = row["duration"]
    return StashFile(
        path=str(stash_path(str(row["folder"])) / str(row["basename"])),
        oshash=row["oshash"],
        phash=phash_hex(row["phash"]),
        in_zip=row["zip_file_id"] is not None,
        size_bytes=None if row["size"] is None else int(row["size"]),
        duration_ms=None if duration is None else round(float(duration) * 1000),
    )


_FILES_OF = """
SELECT link.{owner} AS owner, fo.path AS folder, f.basename AS basename, f.zip_file_id, f.size,
       (SELECT duration FROM video_files WHERE file_id = f.id) AS duration,
       (SELECT fingerprint FROM files_fingerprints WHERE file_id = f.id AND type = 'oshash') AS oshash,
       (SELECT fingerprint FROM files_fingerprints WHERE file_id = f.id AND type = 'phash') AS phash
  FROM {table} link
  JOIN files f ON f.id = link.file_id
  JOIN folders fo ON fo.id = f.parent_folder_id
 ORDER BY link.{owner}, link."primary" DESC, f.id
"""


def _files(connection: sqlite3.Connection, table: str, owner: str) -> dict[int, list[StashFile]]:
    found: dict[int, list[StashFile]] = {}
    # `table` and `owner` are two constants from this module's own callers.
    # nosemgrep: sift-no-string-built-sql (`table` and `owner` are two constants from this module's own callers)
    for row in connection.execute(_FILES_OF.format(table=table, owner=owner)):
        found.setdefault(int(row["owner"]), []).append(_file_of(row))
    return found


def _names_by(
    connection: sqlite3.Connection, link: str, owner: str, other: str, table: str
) -> dict[int, list[str]]:
    # Every name spliced in is a constant from this module's own callers.
    return _grouped(
        connection,
        f"SELECT l.{owner}, t.name FROM {link} l JOIN {table} t ON t.id = l.{other}"  # noqa: S608
        f" ORDER BY l.{owner}, t.name",
    )


def scenes(connection: sqlite3.Connection) -> Iterator[Item]:
    """Every scene, as a file's fields, with its files, its rating and its counts."""
    files = _files(connection, "scenes_files", "scene_id")
    people = _names_by(connection, "performers_scenes", "scene_id", "performer_id", "performers")
    labels = _names_by(connection, "scenes_tags", "scene_id", "tag_id", "tags")
    links = _grouped(connection, "SELECT scene_id, url FROM scene_urls ORDER BY scene_id, position")
    os = dict(connection.execute("SELECT scene_id, COUNT(*) FROM scenes_o_dates GROUP BY scene_id"))
    views = dict(
        connection.execute("SELECT scene_id, COUNT(*) FROM scenes_view_dates GROUP BY scene_id")
    )
    sites = {
        int(one["id"]): str(one["name"])
        for one in connection.execute("SELECT id, name FROM studios")
    }
    custom = _custom_fields(connection, "scene_custom_fields", "scene_id")
    captions = _captions(connection)
    for row in connection.execute("SELECT * FROM scenes ORDER BY id"):
        stash_id = int(row["id"])
        said = [
            *([f"director {str(row['director']).strip()}"] if row["director"] else []),
            *_flag(row["organized"], "organized"),
            *([f"captions in {', '.join(captions[stash_id])}"] if stash_id in captions else []),
            *custom.get(stash_id, []),
        ]
        resume = _ms(row["resume_time"])
        fields: dict[str, object] = {
            "title": row["title"],
            "details": _with_stash_line(row["details"], said),
            "release_date": row["date"],
            "site_code": row["code"],
            "site": sites.get(int(row["studio_id"])) if row["studio_id"] is not None else None,
            "people": people.get(stash_id, []),
            "tags": labels.get(stash_id, []),
            "links": links.get(stash_id, []),
        }
        yield Item(
            stash_id=stash_id,
            kind="scene",
            files=files.get(stash_id, []),
            fields={key: value for key, value in fields.items() if value not in (None, "", [])},
            rating=stars(row["rating"]),
            o_count=int(os.get(stash_id, 0)),
            views=int(views.get(stash_id, 0)),
            resume_ms=resume or None,
            played_ms=_ms(row["play_duration"]),
        )


def images(connection: sqlite3.Connection) -> Iterator[Item]:
    """Every image, as a file's fields, with its files, its rating and its O count."""
    files = _files(connection, "images_files", "image_id")
    people = _names_by(connection, "performers_images", "image_id", "performer_id", "performers")
    labels = _names_by(connection, "images_tags", "image_id", "tag_id", "tags")
    sites = {
        int(one["id"]): str(one["name"])
        for one in connection.execute("SELECT id, name FROM studios")
    }
    links = _grouped(connection, "SELECT image_id, url FROM image_urls ORDER BY image_id, position")
    custom = _custom_fields(connection, "image_custom_fields", "image_id")
    for row in connection.execute("SELECT * FROM images ORDER BY id"):
        stash_id = int(row["id"])
        said = [
            *([f"photographer {str(row['photographer']).strip()}"] if row["photographer"] else []),
            *_flag(row["organized"], "organized"),
            *custom.get(stash_id, []),
        ]
        fields: dict[str, object] = {
            "title": row["title"],
            "details": _with_stash_line(row["details"], said),
            "release_date": row["date"],
            "site_code": row["code"],
            "site": sites.get(int(row["studio_id"])) if row["studio_id"] is not None else None,
            "people": people.get(stash_id, []),
            "tags": labels.get(stash_id, []),
            "links": links.get(stash_id, []),
        }
        yield Item(
            stash_id=stash_id,
            kind="image",
            files=files.get(stash_id, []),
            fields={key: value for key, value in fields.items() if value not in (None, "", [])},
            rating=stars(row["rating"]),
            o_count=int(row["o_counter"] or 0),
        )


@dataclass(frozen=True)
class Gallery:
    """One gallery: what its Photo Set is called, and its pictures by Stash's image ids."""

    stash_id: int
    name: str
    images: tuple[int, ...]


#: What a gallery with no title, no folder and no zip is called.
UNNAMED_GALLERY = "From Stash"


def galleries(connection: sqlite3.Connection) -> list[Gallery]:
    """Every gallery with its pictures, named as its Photo Set is: its title, else its folder's or
    its zip's name (a zip gallery has no folder, so its zip names it as a folder would)."""
    rows = connection.execute(
        "SELECT g.id, COALESCE(g.title, fo.path, (SELECT zf.basename FROM galleries_files"
        " gf JOIN files zf ON zf.id = gf.file_id WHERE gf.gallery_id = g.id LIMIT 1))"
        " AS name, gi.image_id"
        " FROM galleries g JOIN galleries_images gi ON gi.gallery_id = g.id"
        " LEFT JOIN folders fo ON fo.id = g.folder_id ORDER BY g.id, gi.image_id"
    ).fetchall()
    found: dict[int, tuple[str, list[int]]] = {}
    for row in rows:
        name = stash_path(str(row["name"] or "")).name
        name = (name[: -len(".zip")] if name.lower().endswith(".zip") else name) or UNNAMED_GALLERY
        found.setdefault(int(row["id"]), (name, []))[1].append(int(row["image_id"]))
    return [Gallery(key, name, tuple(images)) for key, (name, images) in found.items()]


@dataclass(frozen=True)
class Group:
    """One group (a movie, in older Stash): its name and its scenes in the order Stash kept them."""

    stash_id: int
    name: str
    scenes: tuple[int, ...]


def groups(connection: sqlite3.Connection) -> list[Group]:
    """Every group with at least one scene, its scenes by their place in it (a scene with no place
    after those with one, then by id)."""
    rows = connection.execute(
        "SELECT g.id, g.name, gs.scene_id FROM groups g JOIN groups_scenes gs ON gs.group_id = g.id"
        " ORDER BY g.id, gs.scene_index IS NULL, gs.scene_index, gs.scene_id"
    ).fetchall()
    found: dict[int, tuple[str, list[int]]] = {}
    for row in rows:
        name = str(row["name"] or "").strip() or UNNAMED_GALLERY
        found.setdefault(int(row["id"]), (name, []))[1].append(int(row["scene_id"]))
    return [Group(key, name, tuple(scenes)) for key, (name, scenes) in found.items()]


def picture_of(connection: sqlite3.Connection, checksum: str, folder: Path | None) -> bytes | None:
    """The bytes of one picture Stash kept, by its checksum: from the database where Stash keeps
    them there, else from its blobs folder when one was named; None where neither has it.

    Stash files a blob under two folders named by the checksum's first two pairs of characters
    (`ab/cd/abcd...`); the folder's own top is tried too, for a folder somebody flattened. The
    checksum is refused unless it is plain hexadecimal, so it can never name a path outside the
    folder."""
    row = connection.execute("SELECT blob FROM blobs WHERE checksum = ?", (checksum,)).fetchone()
    if row is not None and row[0]:
        return bytes(row[0])
    wanted = checksum.strip().lower()
    if folder is None or len(wanted) < 5 or any(one not in "0123456789abcdef" for one in wanted):
        return None
    for place in (folder / wanted[:2] / wanted[2:4] / wanted, folder / wanted):
        try:
            return place.read_bytes()
        except OSError:
            continue
    return None


# --- markers, stash-box ids and saved filters --------------------------------------------------


@dataclass(frozen=True)
class Marker:
    """One scene marker: where it starts, where it ends when it has an end, and its words."""

    scene_id: int
    title: str
    start_seconds: float
    #: None for a moment rather than a stretch. The caller decides what a moment becomes.
    end_seconds: float | None
    #: Its primary tag first, then its others, by name.
    tags: Sequence[str] = ()


def markers(connection: sqlite3.Connection) -> Iterator[Marker]:
    """Every scene marker, in scene order, with its tags by name."""
    names = {
        int(one["id"]): str(one["name"]) for one in connection.execute("SELECT id, name FROM tags")
    }
    others = _grouped(
        connection,
        "SELECT mt.scene_marker_id, t.name FROM scene_markers_tags mt"
        " JOIN tags t ON t.id = mt.tag_id ORDER BY mt.scene_marker_id, t.name",
    )
    for row in connection.execute("SELECT * FROM scene_markers ORDER BY scene_id, seconds, id"):
        primary = names.get(int(row["primary_tag_id"])) if row["primary_tag_id"] else None
        words = [primary] if primary else []
        words += [one for one in others.get(int(row["id"]), []) if one not in words]
        yield Marker(
            scene_id=int(row["scene_id"]),
            title=str(row["title"] or "").strip(),
            start_seconds=float(row["seconds"] or 0),
            end_seconds=None if row["end_seconds"] is None else float(row["end_seconds"]),
            tags=tuple(words),
        )


@dataclass(frozen=True)
class BoxId:
    """What one stash-box calls one of Stash's rows: the box by its address, and its id there."""

    owner: int
    endpoint: str
    remote_id: str


#: The four tables Stash keeps its stash-box ids in, by what their rows are about.
_BOX_ID_TABLES = {
    "performer": ("performer_stash_ids", "performer_id"),
    "studio": ("studio_stash_ids", "studio_id"),
    "tag": ("tag_stash_ids", "tag_id"),
    "scene": ("scene_stash_ids", "scene_id"),
}


def box_ids(connection: sqlite3.Connection, kind: str) -> list[BoxId]:
    """Every stash-box id Stash holds for one kind of row, where the table exists at all."""
    table, owner = _BOX_ID_TABLES[kind]
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if present is None:
        return []
    return [
        BoxId(int(row[0]), str(row[1]).strip(), str(row[2]).strip())
        for row in connection.execute(
            # `table` and `owner` come from the constant mapping above; nothing arrives at run time.
            # nosemgrep: sift-no-string-built-sql
            f"SELECT {owner}, endpoint, stash_id FROM {table} ORDER BY {owner}"  # noqa: S608
        )
        if row[0] is not None and row[1] and row[2]
    ]


@dataclass(frozen=True)
class SavedFilter:
    """One of Stash's saved filters: which list it was kept on, its name, and what it asks."""

    mode: str
    name: str
    #: The typed words and the sort, as Stash keeps them.
    find: Mapping[str, object]
    #: The criteria, by Stash's own field names.
    criteria: Mapping[str, object]


def _json_object(value: object) -> dict[str, object]:
    """A Stash JSON column (text or bytes) as a dictionary; anything else reads as empty."""
    if value is None:
        return {}
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    try:
        found = json.loads(text)
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def saved_filters(connection: sqlite3.Connection) -> Iterator[SavedFilter]:
    """Every saved filter Stash keeps, in the order it made them."""
    for row in connection.execute("SELECT * FROM saved_filters ORDER BY id"):
        keys = row.keys()
        yield SavedFilter(
            mode=str(row["mode"] or "").upper(),
            name=str(row["name"] if "name" in keys else "").strip(),
            find=_json_object(row["find_filter"] if "find_filter" in keys else None),
            criteria=_json_object(row["object_filter"] if "object_filter" in keys else None),
        )


def names_of(connection: sqlite3.Connection, table: str) -> dict[int, str]:
    """Every row of performers, studios or tags by id, for reading a filter's ids back as names."""
    if table not in ("performers", "studios", "tags"):
        raise ValueError(table)
    # nosemgrep: sift-no-string-built-sql (`table` is one of the three names checked above)
    rows = connection.execute(f"SELECT id, name FROM {table}")  # noqa: S608
    return {int(one[0]): str(one[1]) for one in rows}
