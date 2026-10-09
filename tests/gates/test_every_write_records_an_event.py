# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every write that changes what the library IS says so in the record, or says why it does not.

There is no single write transaction to catch every act from underneath, so each writer records
what it did, and a writer that says nothing is an absence no search for a mistake can find. So a
function that changes a `WATCHED` table and calls no door fails until somebody answers in
`EXCUSED`: a measurement, a cache or a derived row nobody decided.

`opinions`, `plays` and `search_events` are not watched: they ARE a record, one append-only row
each, read only by the user they belong to. An `INSERT ... ON CONFLICT ... DO UPDATE` is an update
here, or the opinion writers would pass by the shape of their statement
(`test_the_upsert_form_is_seen`).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: The tables whose changes the record is about: each row is something somebody DECIDED and says
#: only what is true now, so without a record the fact is overwritten or deleted with it.
WATCHED: dict[str, str] = {
    "assets": "a record field typed on a file, overwritten in place",
    "asset_people": "who is in a file; the row is deleted on removal",
    "asset_tags": "what a file is tagged; the row is deleted on removal",
    "asset_usernames": "what a file is filed under; the row is deleted on removal",
    "people": "somebody renamed, merged away or deleted",
    "sites": "a Site renamed, merged away or deleted",
    "tags": "a tag renamed or deleted",
    "collections": "a collection renamed or deleted",
    "photo_sets": "a grouping renamed or deleted",
    "collection_items": "membership, which carries no timestamp column at all",
    "photo_set_items": "membership, which carries no timestamp column at all",
    "songs": "a song renamed, merged away or deleted",
    "song_files": "which song a file carries; the row is deleted when it is taken off",
    "people_aliases": "the other names somebody answers to",
    "people_links": "where somebody can be found",
    "site_links": "where a Site can be found",
    "acl_grants": "a share made or taken back; a revoke deletes its own row",
    "users": "a user made, turned off or removed",
    "app_settings": "a setting changed; the row is a key and a value and nothing else",
    "user_settings": "the same, for one user",
    "asset_user_state": "a file concealed or revealed",
    "person_user_state": "somebody concealed or revealed",
    "site_user_state": "a Site concealed or revealed",
    "folder_user_state": "a folder concealed or revealed",
    # The downloads queue's two: a download row is overwritten by every attempt, and forgetting a
    # Site's cookies deletes its row.
    "downloads": "how a fetch ended; the row is overwritten in place by every attempt",
    "site_connections": "cookies saved, replaced or forgotten for a Site; a forget deletes its row",
}

#: The door, in each spelling a writer reaches it by: the door itself, the queues' receipt form,
#: and the `Recording` a kernel write carries into its own transaction. A helper in the same module
#: is covered by the module-level pass below.
_RECORDS = (
    "record_event(",
    "record_on(",
    "Recording(",
    "events=",
    "event=(",
    "_user_event(",
    "_record_deletions(",
    "_record_edit(",
    # Helpers in a writer's own module that write the event in the caller's transaction.
    "_cover_set(",
    "_record_outcome(",
    # The opinion door, at the CALL SITE: an opinion is not a ledger event, but the write that
    # replaces what somebody thought appends what it replaced in the same transaction.
    # `records=OpinionKind.` by the enum's name, so an unrelated `records` argument cannot match.
    "records=OpinionKind.",
    "record_opinion(",
)

#: Where the record IS (the ledger, the schema), history restated (migrations), and tests.
_SKIP = ("kernel/ledger.py", "schema.py", "schema_steps.py", "migrations.py", "/tests/", "testing/")

#: The writes that deliberately record nothing, each a promise that what it writes is not an ACT:
#: a measurement, a cache, a row Sift read off a file, or a helper whose caller records.
NOT_AN_ACT: dict[str, str] = {
    # A derived set re-tied to its folder or archive that came back; its making and filling record.
    "slices.photo_sets.service.from_folder": "a derived set re-tied to the folder that came back: nobody decided it; its making and filling record their events",
    "slices.photo_sets.service.from_archive": "a derived set re-tied to the archive that came back: nobody decided it; its making and filling record their events",
    # The upsert writers, each answered.
    "kernel.content.user_state.record_view": "a view counted: a measurement of what happened, not an act on the library, and it has a table of its own, `plays`",
    "kernel.content.user_state.record_watch_time": "time watched and where somebody got to: the same measurement as the view beside it, minus the view",
    "kernel.access.lineage.inherit": "a derivative takes its source's concealment and rating; nobody decided it, the decision it copies is already on the record",
    "kernel.access.lineage._match_concealment": "the same inheritance, for the concealment half; it carries a decision forward rather than making one",
    "kernel.content.identity_store._upsert_asset": "a file as Sift read it off the disk, which is the family every other identity writer here is excused as",
    "kernel.content.identity_probes.keep_probe": "what ffprobe said about a file, kept; Sift read it rather than deciding it",
    # `_on` helpers: the caller opened the transaction and knows who acted, so the event is theirs.
    "kernel.access.default_covers.faces_back_to_the_rule": "a one-time step of catalog 79 run by the schema: it restates the cover rule over covers the faces store had taken, and the ledger keeps the covers' own acts",
    "slices.faces.store_removals.take_filed_off": "a helper on the caller's connection; the disagreements' No writes the receipt and its record",
    "kernel.access.catalog.people.give_person_a_cover_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.catalog.people.detach_person_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.catalog.people.remove_person_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.catalog.people.remove_alias_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.catalog.carried.uncarry_attribution_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.catalog.posts.set_filing_post_on": "a helper on the caller's connection; the act and its record are the caller's",
    "kernel.access.edited._username_owner_trigger": "the text of a trigger that keeps when a person was last edited; it fires under a write whose act the writer records, and the moment is a measurement of that act",
    "kernel.access.catalog.makers.mark_made_via": "a mark of which pass of Sift's made a row the shared lookups made, written beside that row's own recorded act; the act is the lookup's",
    "kernel.content.identity_probes.record_still_moment": "a measurement: where the still maker cut this file's tile, kept so the picture can be cut again the same way",
    "kernel.access.catalog.makers.mark_pmv_creator_on": "a helper on the caller's connection; the act and its record are the caller's",
    "slices.suggestions.store.unfile_on": "a helper on the caller's connection; the undo that calls it writes the receipt",
    "slices.suggestions.store.unfile_from_a_name_on": "a helper on the caller's connection; the undo that calls it writes the receipt",
    "slices.watermarks.store.unfile_on": "a helper on the caller's connection; the undo that calls it writes the receipt",
    "kernel.access.creator_studios.put_back": "the Undo of a receipt on the caller's connection; the workbench marks that receipt taken back, which is the record of the act",
    "kernel.access.creator_studios._site_put_back": "the Site half of `put_back`, moved out whole; the same receipt is the record",
    "kernel.access.creator_studios._let_go_of_what_the_move_made": "the let-go half of `put_back`, moved out whole; the same receipt is the record",
    # A stash-box answer taken back writes ONE receipt for the whole act.
    "kernel.access.catalog.take_back.take_off_filed_on": "a helper on the caller's connection; the take-back that calls it writes one receipt for the whole act",
    "kernel.access.catalog.take_back.remove_shell_on": "a helper on the caller's connection; the take-back that calls it writes one receipt for the whole act",
    "kernel.access.catalog.take_back.put_back_on": "the Undo of a take-back's receipt on the caller's connection; the workbench marks that receipt taken back, which is the record of the act",
    # The song door's helpers (`kernel/content/songs.py`), on the caller's connection.
    "kernel.content.songs.song_called": "a helper on the caller's connection: the song a name means, stamped with the recording an answer named; the act and its record are the caller's",
    "kernel.content.songs.choose": "a helper on the caller's connection: a person's choice of a file's song; the record form's Save records it",
    "kernel.content.songs.put_on_by_hand": "a helper on the caller's connection; the song's page records the file it put on the song",
    "kernel.content.songs.take_off": "a helper on the caller's connection; the song's page records the file it took off the song",
    "kernel.content.songs.take_back": "the Undo of a shared name's receipt on the caller's connection; the workbench marks that receipt taken back, which is the record of the act",
    # One press of Save is one act, recorded by `browse.set_record` naming every field that moved.
    "kernel.content.identity_fields.set_title": "one field of one Save; browse.set_record writes the event for the whole save",
    "kernel.content.identity_fields.set_details": "one field of one Save; browse.set_record writes the event for the whole save",
    "kernel.content.identity_fields.set_release_date": "one field of one Save; browse.set_record writes the event for the whole save",
    "kernel.content.identity_fields.set_production_date": "one field of one Save; browse.set_record writes the event for the whole save",
    "kernel.content.identity_fields.set_site_code": "one field of one Save; browse.set_record writes the event for the whole save",
    "kernel.content.identity_fields.set_download_url": "one field of one Save; browse.set_record writes the event for the whole save",
    # What Sift READ off a file.
    "kernel.content.identity_probes.record_probe": "what the decoder read off the file; nobody decided it",
    "kernel.content.hashing.record_whole_digest": "the digest of every byte of a file, read while it was still in staging; nobody decided it",
    "kernel.content.identity_probes.reclassify": "a correction to what a file IS, read off the bytes again",
    "kernel.content.identity_probes.send_to_classifier": "a row sent back to the classifier because its bytes refute its kind; the reclassify pass records the correction",
    "kernel.content.identity_arrivals.adopt_identity": "the digest generation a row belongs to; a migration of the key, not an act",
    "kernel.content.identity_arrivals._adopt_legacy": "the same, on the way in; nobody decided it",
    "kernel.content.identity_fields.seed_download_url": "fills a blank at import and never argues with a correction",
    "kernel.content.identity_store._add_location": "another place the same bytes sit; the arrival row is the record",
    "kernel.content.identity_store._end_unplaced": "the file ending; the deleter above records it with the name it had",
    # Caches, derived rows and positions.
    "kernel.cache_stamp.bump_cache_stamp": "a cache stamp, which is what makes a picture stale",
    "kernel.cache_stamp.bump_every_cache_stamp": "a cache stamp, which is what makes a picture stale",
    "slices.people.merge._filled": "a read: it works out which blanks a merge WOULD fill, to say so on the sheet; the merge is the act",
    "slices.people.site_merge._links_go_after": "one step of a Site merge: it re-mints the going site's link ids so they land after the survivor's; the merge records the fold in the same transaction",
    "slices.faces.store_removals._bring_into_line": "derived from the face tables; a redraw, not a decision",
    # A merge records each fold one level up, in the same transaction.
    "slices.people.merge._one_into": "one step of a merge; merge_many records each fold in the same transaction",
    "slices.people.site_merge._one_into": "one step of a merge; merge_many records each fold in the same transaction",
    "slices.people.site_merge._fold_usernames": "one step of a merge; merge_many records each fold in the same transaction",
    # Credentials: nothing anybody can act on (as `test_a_write_says_who_it_concerns.py` says).
    "slices.auth.service._judge_login": "stamps a session; a credential, not a list",
    "slices.download.service_listing.mark_seen": "an acknowledgement for the rail; no download row changes",
    "slices.people.service_sites._rewrite_site_links": "the link rows of a merge, which the merge itself records",
    "slices.auth.service.set_pin": "a credential, not an act on the library",
    "slices.auth.service.change_password": "a credential, not an act on the library",
    "slices.auth.service.reset_user_password": "a credential, not an act on the library",
    "slices.auth.admin_cli.reset_password": "a credential, from the console, with no session behind it",
    "slices.auth.service._record_rename": "the daily rename allowance; a counter, not an act",
    # The downloads queue's bookkeeping: the row moving through states is work in progress, and
    # `mark_done` and `mark_failed` each write the one event.
    "slices.download.service_base._queue_for": "writes which job runs this row; the job is how, not what",
    "slices.download.service_ledger.mark_running": "how far a fetch has got; a progress state, not an act",
    "slices.download.service_ledger.mark_skipped": "this link was already fetched; nothing was added",
    "slices.download.service_ledger.record_route": "which way out one attempt went; a measurement of it",
    "slices.download.service_ledger.record_folder": "which folder one attempt resolved; the landing is the act",
    "slices.download.service_ledger.mark_quarantined": "the gate set the file aside; nothing entered the library",
    "slices.download.service_controls.retry": "puts a failed row back in the line; the next ending records itself",
    "slices.download.service_base.fetch_anyway": "runs a skipped row again; the file landing is the act",
    # The list's own tidying: the row stays, so a re-pasted link is not fetched twice.
    "slices.download.service_controls.hide": "takes a settled row off the queue's list; the download is unchanged",
    "slices.download.service_controls.restore": "puts a removed row back on the list; the download is unchanged",
    # A Site's answer, and a stamp where the cookies are unsealed: the machinery working.
    "slices.download.service_connections.record_login_health": "what a Site answered about the cookies; nobody decided it",
    "slices.download.service_connections.open_cookie": "stamps when the cookies were last used; a counter, not an act",
    # Statements held in a mapping, a tuple or an import.
    "kernel.access.catalog.makers.mark_created_by_box": "which box invented a row, stamped moments after the creation the caller records; provenance, nobody decided it",
    "kernel.access.catalog.makers.clear_created_by_box": "the foreign key's SET NULL written by hand when a box is removed; the rows themselves do not change",
    "kernel.access.repository.store.forget_object": "the grants of a thing that is being deleted; the delete is the act and the deleter records it",
    "kernel.access.repository.store.forget_items": "the grants of files that have ended; the delete is the act and the deleter records it",
    "slices.people.service_merges.merge_person_record": "one step of an enrichment; the run's own writer records it with the box that answered",
    "slices.people.service_merges.merge_site_record": "one step of an enrichment; the run's own writer records it with the box that answered",
    "slices.tags_ratings.service._write_record": "the rows of a tag's record; set_record records edited in the same transaction, and merge_enriched_record is one step of an enrichment whose run writer records enriched",
}

#: Writers that ARE acts and do not record yet, with what each needs. EMPTY, and apart from
#: `NOT_AN_ACT` so "not got to it yet" can never be written down as "not an act".
OWED: dict[str, str] = {
    # An entry that learns to record is taken out, or it waits to excuse the next of that name.
}


EXCUSED: dict[str, str] = {**NOT_AN_ACT, **OWED}


def _watched(sql: str) -> bool:
    """Whether a string changes one of the tables above. Reads the statement, not the name."""
    lowered = " ".join(sql.split()).lower()
    upserts = "on conflict" in lowered and "do update" in lowered
    for table in WATCHED:
        if re.search(rf"\b(update|update or ignore|delete from)\s+{table}\b", lowered):
            return True
        if re.search(rf"\bupdate\s+or\s+\w+\s+{table}\b", lowered):
            return True
        # The upsert overwrites a row as an UPDATE does; a plain INSERT destroys nothing.
        if upserts and re.search(rf"\binsert\s+(or\s+\w+\s+)?into\s+{table}\b", lowered):
            return True
    return False


def _holds_a_watched_write(value: ast.expr | None) -> bool:
    """Whether a module-level value IS a watched statement, or a mapping or sequence of them
    (`entity_state._SET_PINNED` is five upserts under five keys)."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return _watched(value.value)
    if isinstance(value, ast.Dict):
        return any(_holds_a_watched_write(one) for one in value.values)
    if isinstance(value, ast.Tuple | ast.List):
        return any(_holds_a_watched_write(one) for one in value.elts)
    return False


def _local_statement_names(tree: ast.Module) -> set[str]:
    """Every module-level constant this module DEFINES holding a write to a watched table."""
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _holds_a_watched_write(node.value):
            found.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _holds_a_watched_write(node.value)
        ):
            found.add(node.target.id)
    return found


def _module_path(module: str) -> Path | None:
    """The file a `sift.` module name is, or None for anything outside the tree."""
    if module != "sift" and not module.startswith("sift."):
        return None
    parts = module.split(".")[1:]
    if not parts:
        return SOURCE / "__init__.py"
    relative = Path(*parts)
    for candidate in (SOURCE / relative.with_suffix(".py"), SOURCE / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_from(path: Path, node: ast.ImportFrom) -> str | None:
    """The absolute module name an import statement reads from, relative ones resolved."""
    if node.level == 0:
        return node.module
    package = path.relative_to(SOURCE).parent.parts
    if node.level > 1:
        package = package[: len(package) - (node.level - 1)]
    return ".".join(["sift", *package, *([node.module] if node.module else [])])


_LOCAL_CACHE: dict[Path, set[str]] = {}


def _defined_in(path: Path) -> set[str]:
    if path not in _LOCAL_CACHE:
        _LOCAL_CACHE[path] = _local_statement_names(ast.parse(path.read_text(encoding="utf-8")))
    return _LOCAL_CACHE[path]


def _statement_names(tree: ast.Module, path: Path) -> set[str]:
    """Every name in this module that holds a write to a watched table, read from the statements.

    A bare string at module level, a MAPPING of them (`entity_state._SET_PINNED`), or a statement
    IMPORTED from a sibling (`repository.folders._SET_FOLDER_VAULT`).
    """
    found = _local_statement_names(tree)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _imported_from(path, node)
        target = _module_path(module) if module else None
        if target is None:
            continue
        defined = _defined_in(target)
        for alias in node.names:
            if alias.name in defined:
                found.add(alias.asname or alias.name)
    return found


def _writers() -> dict[str, str]:
    """Every function that changes a watched table, named module.function, with its body."""
    found: dict[str, str] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        posix = path.as_posix()
        if any(one in posix for one in _SKIP):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = _statement_names(tree, path)
        module = path.relative_to(SOURCE).as_posix().removesuffix(".py").replace("/", ".")
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            body = ast.get_source_segment(source, node) or ""
            names_used = any(re.search(rf"\b{one}\b", body) for one in names)
            inline = any(
                _watched(piece.value)
                for piece in ast.walk(node)
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
            )
            if names_used or inline:
                found[f"{module}.{node.name}"] = body
    return found


def _silent() -> list[str]:
    """The writers that call no door at all."""
    return sorted(
        name for name, body in _writers().items() if not any(one in body for one in _RECORDS)
    )


def test_a_write_that_changes_the_library_records_an_event_or_says_why_not() -> None:
    undeclared = sorted(set(_silent()) - set(EXCUSED))

    assert not undeclared, (
        "\nThese change what the library IS and write nothing down. Whatever they did becomes\n"
        "invisible the moment the row they changed is read back, which is the fault the record\n"
        "exists to end. Call `record_event` in the same transaction, or name it in EXCUSED with\n"
        "the reason it is a measurement rather than an act.\n\n  " + "\n  ".join(undeclared) + "\n"
    )


def test_nothing_is_excused_that_no_longer_needs_excusing() -> None:
    """No exemption names a writer that has gone or learned to record."""
    stale = sorted(set(EXCUSED) - set(_silent()))

    assert not stale, (
        "\nThese are excused and do not need to be: they have gone, or they record now.\n"
        "Take them out of EXCUSED, or the next writer to take one of these names is excused\n"
        "with them.\n\n  " + "\n  ".join(stale) + "\n"
    )


def test_the_walk_finds_the_writes_it_is_about() -> None:
    """A walk that found nothing would pass for ever, and so would one that found everything."""
    writers = _writers()
    speaking = [name for name, body in writers.items() if any(one in body for one in _RECORDS)]

    assert len(writers) >= 20, f"only {len(writers)} writers found; the walk is broken"
    assert len(speaking) >= 15, f"only {len(speaking)} writers record; the rule has not landed"


def test_every_reason_is_a_reason() -> None:
    """An empty excuse is not one, and neither is a name repeated back."""
    for name, reason in EXCUSED.items():
        assert len(reason.split()) >= 4, f"{name} is excused without a reason"


def test_the_upsert_form_is_seen() -> None:
    """The upsert form is seen, written as real statements so an empty walk cannot pass; the last
    two must NOT match."""
    favorite = (
        "INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(asset_id, user_id) DO UPDATE SET favorite = excluded.favorite"
        " RETURNING *"
    )
    hide = (
        "INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(person_id, user_id) DO UPDATE SET hidden = excluded.hidden"
    )
    # An upsert on an unwatched table, and a plain insert on a watched one.
    unwatched = (
        "INSERT INTO opinions (id, user_id, kind) VALUES (?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET kind = excluded.kind"
    )
    made = "INSERT INTO people (id, name) VALUES (?, ?)"
    edited = "UPDATE assets SET title = ? WHERE id = ?"
    untagged = "DELETE FROM asset_tags WHERE asset_id = ?"

    assert _watched(favorite)
    assert _watched(hide)
    assert not _watched(unwatched)
    assert not _watched(made)
    assert _watched(edited)
    assert _watched(untagged)


def test_the_two_lists_mean_different_things_and_stay_apart() -> None:
    """No writer is in both lists, or a reader believes whichever half excuses it."""
    both = sorted(set(NOT_AN_ACT) & set(OWED))

    assert not both, "\n  ".join(["these are excused twice, with two different meanings:", *both])


def test_a_statement_held_in_a_mapping_a_sequence_or_an_import_is_seen() -> None:
    """Real writers holding their statement in a mapping (`set_pinned`), a tuple (the created-by-box
    sweep) and a sibling module (`set_folder_vault`) are found."""
    found = set(_writers())
    assert "kernel.content.entity_state.set_pinned" in found
    assert "kernel.access.catalog.makers.clear_created_by_box" in found
    assert "kernel.access.repository.store.set_folder_vault" in found

    store = SOURCE / "kernel" / "access" / "repository" / "store.py"
    names = _statement_names(ast.parse(store.read_text(encoding="utf-8")), store)
    assert "_SET_FOLDER_VAULT" in names
