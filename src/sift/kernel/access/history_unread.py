# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sources of a file's thread that need no fold, Undo or access layer: rows with a moment, said in a sentence, and the passes run over the file."""

from __future__ import annotations

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _names_of, _Who
from sift.kernel.access.history_boxes import kept_events, shown_of
from sift.kernel.access.history_folds import EPISODE_GAP, episodes
from sift.kernel.access.history_line import Actor, Event, by_of
from sift.kernel.access.history_presses import _LEFT_OUT, NOBODY_PRESSED, Pressers, by_pressed
from sift.kernel.access.history_reads import _seconds
from sift.kernel.access.sentences import A_THING, SIFT, Part, Piece
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, point_read

# --- NINE MORE SOURCES --------------------------------------------------------------------------
#
# Nine tables, each carrying a moment for an act on the file: Sift downloaded it, read a watermark
# off it, built a preview for it, had a face taken off it, hid it from the person looking.
#
# **What this costs.** A pane is twenty-one point reads, run again for the tab's count, each a seek
# on the event loop (`point_read`): tens of microseconds each, less than a thread handoff.

#: Whether a look for a watermark found one. `found = 0` is the interesting half: a file nothing was
#: read off looks exactly like a file nobody ever looked at, which is a distinction somebody opens
#: this pane to make.
_WATERMARK_SCAN = point_read(
    "history.watermark_scan",
    "SELECT found, scanned_at FROM watermark_scans WHERE asset_id = ?",
)

#: WHAT the watermark said, which is the whole point of the line. A reading names a site and often a
#: username, and those are how a file with a meaningless name comes to be filed at all.
#:
#: The KIND and the LETTERS ride along: a channel and a bare username store no site, so without
#: them the line would have nothing to say about either. See `sentences.watermark_found`.
#:
#: The SITE ROW it filed under rides along, by id, for the link: see `_named_site`.
_WATERMARK_READ = point_read(
    "history.watermark_read",
    "SELECT COALESCE(s.name, w.site) AS site, w.username AS username, w.text AS text,"
    " w.kind AS kind, w.read_at AS read_at, s.id AS site_id"
    " FROM watermark_reads w LEFT JOIN sites s ON s.id = w.site_id WHERE w.asset_id = ?",
)

#: Somebody said the reading was wrong. Its own line rather than a silence, because a refusal is the
#: one act here that stops a pass from ever filing this file again.
_WATERMARK_REFUSED = point_read(
    "history.watermark_refused",
    "SELECT created_at FROM watermark_refusals WHERE asset_id = ?",
)

#: Where a downloaded file came from; `ix_downloads_asset` makes it a point read. The FINISHED
#: moment where there is one, because that is when the file existed. The Site rides along by id,
#: for the link (`_named_site`).
_DOWNLOADED = point_read(
    "history.downloaded",
    "SELECT d.id AS id, COALESCE(s.name, d.site) AS site, d.username AS username,"
    " d.created_at AS created_at, d.finished_at AS finished_at, d.state AS state,"
    " s.id AS site_id, d.requested_by AS requested_by"
    " FROM downloads d LEFT JOIN sites s ON s.id = d.site_id WHERE d.asset_id = ?"
    " ORDER BY d.id ASC",
)

#: How long before a file's arrival the download that brought it in can have started and still be
#: that download, in seconds, beside `downloads.state = 'done'`, which says a download landed a
#: file, but for a gallery names only its last file, one that may have been in the library already.
#: The slack is for a clock that steps backwards by a few seconds.
_ARRIVAL_SLACK = 60

#: The pictures and copies Sift builds so a file can be looked at. Read as rows and folded into ONE
#: line (see `made_ready` for why three lines of housekeeping is the wrong shape).
#:
#: `sqlite_autoindex_derivatives_1` on (asset_id, kind, params) is the seek.
_DERIVATIVES = point_read(
    "history.derivatives",
    "SELECT kind, created_at FROM derivatives WHERE asset_id = ?"
    "\n -- ordered by the clock: a sitting's line is dated by its first build, and a rebuild keeps"
    "\n -- its id and moves created_at forward (`identity._ADD_DERIVATIVE`)"
    "\n ORDER BY created_at ASC",
)

#: When the model that answers a search by meaning read this file.
_INDEXED = point_read(
    "history.indexed",
    "SELECT indexed_at FROM semantic_indexed WHERE asset_id = ?",
)

#: Appearances taken off the file altogether, and appearances set aside. Counted rather than listed,
#: for the reason a press of namings is: ten identical lines say nothing the first one did not.
_FACES_OFF = point_read(
    "history.faces_off",
    "SELECT created_at FROM face_removals WHERE asset_id = ?",
)

_FACES_ASIDE = point_read(
    "history.faces_aside",
    "SELECT created_at FROM face_ignored WHERE asset_id = ?",
)

#: Somebody said a person is NOT in this file and meant it for good. The opposite of a naming, and
#: the only line that says a pass was told to stop proposing somebody here.
_RULED_OUT = point_read(
    "history.ruled_out",
    """
SELECT p.id AS person_id, p.name AS name, r.refused_at AS refused_at
  FROM asset_person_refusals r
  JOIN people p ON p.id = r.person_id
 WHERE r.asset_id = ?
""",
)

#: Whether the user READING this has concealed the file, and when.
#:
#: Bound to the viewer and to nobody else, which is the whole of the privacy rule here: what another
#: user has hidden is that user's business, and a history is drawn to anybody who may see the
#: file. `PRIMARY KEY (asset_id, user_id)` is the seek.
_HIDDEN = point_read(
    "history.hidden",
    "SELECT hidden, hidden_at FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
)

#: THE MUSIC FINGERPRINT, made or answered empty. One row per file (the primary key is the seek),
#: replaced when the file is fingerprinted again, so the line is dated by the newest. The length of
#: the fingerprint, never the fingerprint: an empty one is the answer for a sound track Sift cannot
#: read, and it is said as plainly as a full one (`sentences.music_fingerprinted`).
_MUSIC_PRINT = point_read(
    "history.music_fingerprint",
    "SELECT computed_at, length(fingerprint) AS size FROM audio_fingerprints WHERE asset_id = ?",
)

#: WHAT ACOUSTID WAS ASKED ABOUT THIS FILE, and what it answered: the one kept answer per file
#: (`music_lookups`, replaced by the next ask). `song_said` is whether the file's own song line
#: says this ask already: an answer that named a song the file was given writes a `song_named` act
#: from AcoustID, and one act is one line. Read off `ix_workbench_subject (kind, subject_id)`, the
#: seek `_DECIDED` reads the same rows by; the JSON is opened only for a `song_named` row whose
#: payload is JSON, as the feed's own reading of a song's payload does (`history_events`).
_MUSIC_ASKED = point_read(
    "history.music_lookup",
    """
SELECT l.status AS status, l.looked_up_at AS looked_up_at, l.title AS title,
       EXISTS (
         SELECT 1 FROM workbench_decision_subjects s
           JOIN workbench_decisions d ON d.id = s.decision_id
          WHERE s.kind = 'asset' AND s.subject_id = l.asset_id
            AND (CASE WHEN d.verb = 'song_named' AND json_valid(d.payload)
                      THEN json_extract(d.payload, '$.source') END) = 'acoustid'
       ) AS song_said
  FROM music_lookups l
 WHERE l.asset_id = ?
""",
)

#: WHEN THE FILE'S DETAILS WERE LAST READ from disk (its size, length and kind): the probe's own kept
#: answer, one row per file and replaced by the next read. Said only where it is not the read that
#: took the file in (see `_unread_sources`).
_DETAILS_READ = point_read(
    "history.details_read",
    "SELECT probed_at FROM asset_probes WHERE asset_id = ?",
)

#: A STASH-BOX MATCH NOBODY HAS ANSWERED YET: the third answer an ask can have, beside a match applied
#: (`_ENRICHED`) and nothing matched (`_ASKED`). Without it a file a box recognised and a person has
#: not yet looked at reads as a file nobody asked about. The `+` keeps the seek on the primary key,
#: for the reason written over `_ENRICHED`.
_WAITING_MATCH = point_read(
    "history.asked_waiting",
    "SELECT b.name AS box, m.found_at AS found_at"
    " FROM asset_stash_box_matches m JOIN stash_boxes b ON b.id = m.box_id"
    " WHERE m.asset_id = ? AND +m.state = 'waiting'",
)


def _named_site(row: Row) -> Piece | None:
    """The Site a download or a watermark reading names, as the way to it, where one is there.

    The link does not claim how the name was come by (a watermark's letters are matched to a Site by
    name afterwards); it offers the way to the Site the line NAMES, as every other line on this pane
    that names a Site does. A name with no Site of that name behind it stays words, which is the
    same drawing every kind without a page gets.

    Joined by the Site's ID, which the download (download v34) and the reading (watermarks v6) keep
    beside the word they read: joined by name, renaming the Site would break every such link. The line
    says the name the Site has NOW (the word the row kept where no Site is behind it), and the
    link carries that same name, because the client finds a link by looking for its name in the
    sentence.
    """
    if row["site_id"] is None or not row["site"]:
        return None
    return say.thing("site", str(row["site_id"]), str(row["site"]))


async def _unread_sources(
    database: Database,
    viewer: Viewer,
    asset_id: str,
    *,
    here: set[str],
    hides_recorded: bool,
    filed_by_watermark: bool,
    arrived: tuple[int, object] | None = None,
    pressers: Pressers = NOBODY_PRESSED,
) -> list[Event]:
    """Every act this file's own tables record that needs no fold, Undo or access layer.

    Each is guarded on its table being registered: a statement naming a missing table is a hard
    error rather than an empty answer. `hides_recorded` says the ledger has this user's own hiding
    of the file, so the column's line is not drawn. `filed_by_watermark` says a watermark's filing
    line is on the pane, handed in so the reading says "filed nothing" exactly when no line beside
    it filed. `arrived` is the file's arrival, its moment and the name it arrived under. `pressers`
    is who pressed the passes here (`Pressers`).
    """
    events: list[Event] = []
    events.extend(await _watermark_events(database, asset_id, here, filed_by_watermark, pressers))
    events.extend(await _download_events(database, viewer, asset_id, here, arrived))
    events.extend(await _built_events(database, asset_id, here, pressers))
    events.extend(await _faces_off_events(database, asset_id, here))
    events.extend(await _hidden_events(database, viewer, asset_id, hides_recorded))
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "asset", asset_id))
    events.extend(
        await _passes_said(database, asset_id, here=here, arrived=arrived, pressers=pressers)
    )
    return events


async def _watermark_events(
    database: Database,
    asset_id: str,
    here: set[str],
    filed_by_watermark: bool,
    pressers: Pressers,
) -> list[Event]:
    """What the watermark pass looked for, read, and had refused."""
    events: list[Event] = []
    if "watermark_scans" in here:
        for row in await database.fetch_all(_WATERMARK_SCAN, (asset_id,)):
            # A scan that FOUND something is told by the reading below, in the words of what it
            # read. Drawing both would be the looking and its answer as two lines, which is the
            # duplicate attribution the folds in `history_folds` exist to end.
            if not int(row["found"]):
                at = int(row["scanned_at"])
                actor, actor_name, by = by_pressed(pressers.of(_WATERMARK_SCAN.name, at))
                events.append(
                    Event(
                        at=at,
                        actor=actor,
                        actor_name=actor_name,
                        kind="watermark",
                        pieces=say.watermark_none(by=by),
                        via="watermark",
                        routine=by is None,
                    )
                )
    if "watermark_reads" in here:
        for row in await database.fetch_all(_WATERMARK_READ, (asset_id,)):
            at = int(row["read_at"])
            actor, actor_name, by = by_pressed(pressers.of(_WATERMARK_READ.name, at))
            events.append(
                Event(
                    at=at,
                    actor=actor,
                    actor_name=actor_name,
                    kind="watermark",
                    # The way to the Site only where the line NAMES it, which is a reading that
                    # filed: one that filed nothing says its letters instead, because the site it
                    # would name is the matcher's guess (see `watermark_found`).
                    pieces=say.watermark_found(
                        row["kind"],
                        row["text"],
                        _named_site(row) if filed_by_watermark else None,
                        row["username"],
                        filed=filed_by_watermark,
                        by=by,
                    ),
                    via="watermark",
                )
            )
    if "watermark_refusals" in here:
        for row in await database.fetch_all(_WATERMARK_REFUSED, (asset_id,)):
            events.append(
                Event(
                    at=int(row["created_at"]),
                    # SOMEBODY and not the viewer: the table records that the reading was refused
                    # and nothing at all about who refused it.
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="watermark",
                    pieces=say.watermark_refused(),
                )
            )
    return events


_USERNAME_ON = "SELECT id FROM usernames WHERE name = ? COLLATE NOCASE AND site_id = ?"
_SITE_CALLED = "SELECT id FROM sites WHERE name = ? COLLATE NOCASE"


async def _told_where(
    database: Database, viewer: Viewer, rows: list[Row]
) -> dict[str, tuple[Part, object]]:
    """Each download's Site and username, by row, as a viewer who is not an admin may be told them:
    a Site they may not be shown is nameless, and a username is said only where they may be."""
    told: dict[str, tuple[Part, object]] = {}
    for row in rows:
        sites = (
            [str(row["site_id"])]
            if row["site_id"]
            # Written before a download kept its Site's id: a Site of that name may still be one.
            else [str(one["id"]) for one in await database.fetch_all(_SITE_CALLED, (row["site"],))]
        )
        # A name no Site here answers to is a Site that was deleted: nameless too.
        if not sites or set(sites) - await shown_of(database, viewer, "site", sites):
            told[str(row["id"])] = (A_THING["site"], None)
            continue
        # The same two reads whether a username was kept, is in the library or may be shown.
        found = await database.fetch_all(_USERNAME_ON, (row["username"] or "", row["site_id"]))
        named = [str(one["id"]) for one in found]
        seen = await shown_of(database, viewer, "username", named or [""])
        shown = bool(named) and set(named) <= seen
        told[str(row["id"])] = (_named_site(row) or row["site"], row["username"] if shown else None)
    return told


async def _download_events(
    database: Database,
    viewer: Viewer,
    asset_id: str,
    here: set[str],
    arrived: tuple[int, object] | None,
) -> list[Event]:
    """Each download of the file, the one that brought it in dated at its arrival."""
    events: list[Event] = []
    if "downloads" in here:
        landing = True
        downloads = list(await database.fetch_all(_DOWNLOADED, (asset_id,)))
        told = {} if viewer.is_admin else await _told_where(database, viewer, downloads)
        # WHO ASKED FOR IT (download v33): the one line here that names a user, so the one place
        # this function reads a name. "You downloaded this file from ..." for the viewer's own paste.
        asked = _Who(
            viewer=viewer,
            names=await _names_of(
                database, [str(row["requested_by"]) for row in downloads if row["requested_by"]]
            ),
        )
        for row in downloads:
            finished = row["finished_at"]
            moment = int(finished if finished is not None else row["created_at"])
            # The first download that landed this file and began before it arrived brought it in.
            # A later one found it here already, and keeps a line of its own.
            landed = (
                landing
                and arrived is not None
                and row["state"] == "done"
                and int(row["created_at"]) <= arrived[0] + _ARRIVAL_SLACK
            )
            landing = landing and not landed
            actor, actor_name = (
                (Actor.SIFT, SIFT)
                if row["requested_by"] is None
                else asked.of(str(row["requested_by"]))
            )
            site, username = told.get(
                str(row["id"]), (_named_site(row) or row["site"], row["username"])
            )
            events.append(
                Event(
                    at=min(moment, arrived[0]) if landed and arrived is not None else moment,
                    actor=actor,
                    actor_name=actor_name,
                    kind="downloaded",
                    pieces=say.downloaded(
                        site,
                        username,
                        arrived[1] if landed and arrived is not None else None,
                        by=by_of(actor, actor_name),
                    ),
                    landed=landed,
                )
            )
    return events


async def _built_events(
    database: Database, asset_id: str, here: set[str], pressers: Pressers
) -> list[Event]:
    """What Sift built from the file, a line a sitting, and its reading for Smart Search."""
    events: list[Event] = []
    # ONE LINE for the pictures and copies Sift built in one sitting, at the moment the first of
    # them was made (`episodes`). See `made_ready`.
    built = list(await database.fetch_all(_DERIVATIVES, (asset_id,)))
    for run in episodes([int(row["created_at"]) for row in built]):
        at = int(built[run[0]]["created_at"])
        actor, actor_name, by = by_pressed(pressers.of(_DERIVATIVES.name, at))
        events.append(
            Event(
                at=at,
                actor=actor,
                actor_name=actor_name,
                kind="ready",
                pieces=say.made_ready([str(built[position]["kind"]) for position in run], by=by),
                routine=by is None,
            )
        )
    if "semantic_indexed" in here:
        for row in await database.fetch_all(_INDEXED, (asset_id,)):
            at = _seconds(row["indexed_at"]) or 0
            actor, actor_name, by = by_pressed(pressers.of(_INDEXED.name, at))
            events.append(
                Event(
                    at=at,
                    actor=actor,
                    actor_name=actor_name,
                    kind="ready",
                    pieces=say.read_its_meaning(by=by),
                    routine=by is None,
                )
            )
    return events


async def _faces_off_events(database: Database, asset_id: str, here: set[str]) -> list[Event]:
    """The faces taken off or set aside, and each person ruled out of the file."""
    events: list[Event] = []
    if "face_removals" in here:
        taken = list(await database.fetch_all(_FACES_OFF, (asset_id,)))
        if taken:
            moments = [_seconds(row["created_at"]) for row in taken]
            told = [one for one in moments if one is not None]
            events.append(
                Event(
                    at=max(told) if told else None,
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="face_off",
                    pieces=say.faces_taken_off(len(taken)),
                )
            )
    if "face_ignored" in here:
        aside = list(await database.fetch_all(_FACES_ASIDE, (asset_id,)))
        if aside:
            moments = [_seconds(row["created_at"]) for row in aside]
            told = [one for one in moments if one is not None]
            events.append(
                Event(
                    at=max(told) if told else None,
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="face_off",
                    pieces=say.faces_set_aside(len(aside)),
                )
            )
    for row in await database.fetch_all(_RULED_OUT, (asset_id,)):
        events.append(
            Event(
                at=int(row["refused_at"]),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="ruled_out",
                pieces=say.person_refused(
                    say.thing("person", str(row["person_id"]), str(row["name"]))
                ),
            )
        )
    return events


async def _hidden_events(
    database: Database, viewer: Viewer, asset_id: str, hides_recorded: bool
) -> list[Event]:
    """The file hidden, from the column, where the ledger never saw it."""
    events: list[Event] = []
    # THE COLUMN IS ONLY THE FALLBACK. `hidden_at` is written when a thing is concealed and is not
    # cleared when it is shown again, so it can date an act that is over. The ledger records the
    # hiding AND the showing again, each with its own moment, so this line is drawn only for a
    # concealment the ledger never saw.
    if not hides_recorded:
        for row in await database.fetch_all(_HIDDEN, (asset_id, viewer.id)):
            if int(row["hidden"]) and row["hidden_at"] is not None:
                events.append(
                    Event(
                        at=int(row["hidden_at"]),
                        actor=Actor.YOU,
                        actor_name=None,
                        kind="hidden",
                        pieces=say.hidden_here(),
                    )
                )
    return events


async def _passes_said(
    database: Database,
    asset_id: str,
    *,
    here: set[str],
    arrived: tuple[int, object] | None,
    pressers: Pressers = NOBODY_PRESSED,
) -> list[Event]:
    """The passes over this file whose own rows said nothing on its History: the music fingerprint,
    AcoustID's answer, a pass that gave up, the details read again, a stash-box match waiting.

    None of these tables keeps who pressed the work, and the job that did is gone a week after it
    settles (`SETTLED_RETENTION_SECONDS`), so who pressed is read from the record of presses
    beside them (`Pressers`): a pass somebody pressed is said as theirs, and one Sift ran by itself
    as Sift's. Each is guarded on its table as the reads above are, the music tables being a
    feature's.
    """
    events: list[Event] = []
    if "audio_fingerprints" in here:
        for row in await database.fetch_all(_MUSIC_PRINT, (asset_id,)):
            at = int(row["computed_at"])
            actor, actor_name, by = by_pressed(pressers.of(_MUSIC_PRINT.name, at))
            events.append(
                Event(
                    at=at,
                    actor=actor,
                    actor_name=actor_name,
                    # The fingerprints mark: what the line says is a fingerprint generated, which is
                    # the duplicate pass's own line's mark too.
                    kind="scanned",
                    pieces=say.music_fingerprinted(empty=not int(row["size"] or 0), by=by),
                    # Housekeeping, as the pictures Sift generates for a file are: one sitting of
                    # them is one line (`_one_processed_line`). Not where somebody pressed it.
                    routine=by is None,
                )
            )
    if {"music_lookups", "workbench_decisions", "workbench_decision_subjects"} <= here:
        for row in await database.fetch_all(_MUSIC_ASKED, (asset_id,)):
            status = str(row["status"])
            if status == "named" and int(row["song_said"]):
                continue
            at = int(row["looked_up_at"])
            actor, actor_name, by = by_pressed(pressers.of(_MUSIC_ASKED.name, at))
            events.append(
                Event(
                    at=at,
                    actor=actor,
                    actor_name=actor_name,
                    kind="asked",
                    pieces=say.acoustid_answered(status, row["title"], by=by),
                    # NOT routine, unlike a stash-box that matched nothing: asking AcoustID sends
                    # the file's fingerprint to somebody else's service, a task of its own that
                    # runs when it is pressed out of the box, so it is never folded out of sight
                    # into a sitting of housekeeping.
                )
            )
    for row in await database.fetch_all(_LEFT_OUT, (asset_id,)):
        at = int(row["at"])
        product = str(row["product"])
        actor, actor_name, by = by_pressed(pressers.of_verdict(product, at))
        events.append(
            Event(
                at=at,
                actor=actor,
                actor_name=actor_name,
                kind="left_out",
                pieces=say.could_not(product, again=bool(int(row["transient"])), by=by),
            )
        )
    # The read that took the file in is part of its arrival, which is the line above everything
    # else here, as an arriving file's fingerprints are (`identity.record_fingerprints`). A read
    # after that sitting (a Run task press of File details, a pass that reads files again) is said.
    taken_in = None if arrived is None else arrived[0] + EPISODE_GAP
    for row in await database.fetch_all(_DETAILS_READ, (asset_id,)):
        if taken_in is not None and int(row["probed_at"]) <= taken_in:
            continue
        at = int(row["probed_at"])
        actor, actor_name, by = by_pressed(pressers.of(_DETAILS_READ.name, at))
        events.append(
            Event(
                at=at,
                actor=actor,
                actor_name=actor_name,
                kind="ready",
                pieces=say.details_read_again(by=by),
                routine=by is None,
            )
        )
    if {"asset_stash_box_matches", "stash_boxes"} <= here:
        for row in await database.fetch_all(_WAITING_MATCH, (asset_id,)):
            at = int(row["found_at"])
            actor, actor_name, by = by_pressed(pressers.of(_WAITING_MATCH.name, at))
            events.append(
                Event(
                    at=at,
                    actor=actor,
                    actor_name=actor_name,
                    kind="asked",
                    pieces=say.asked_and_waiting(str(row["box"]), by=by),
                )
            )
    return events
