# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every write a screen draws says so, or says why it does not.

Almost everything in Sift is a list that was read once and then kept. A write that tells nobody is a
screen somewhere showing the answer from before it: on another tab, on another computer, for as
long as it stays open. That is not a class of bug testing finds: the tab that made the change is
right, and it is every other tab that is wrong.

**It cannot be caught by looking for the mistake, because the mistake is an absence.** So it is
caught the other way round: every write that says nothing is named here with the reason, and a new
one fails the build until somebody has answered the question. Adding a name to the list below is
the moment that question gets asked, which is the whole point of it being a list.

The walk reads every module of every slice, not only its service: a slice's writers live in its
store, its routes, its jobs and its helpers as often as in its service, and a write the walk cannot
see is a write nobody was ever asked about. A slice's write is named `slice.module.function`.

It reads the kernel and the composition root too, by the same rules. A write in the kernel is as
able to leave a screen showing the answer from before it as one in a slice, and more of them sit
under a slice's press than anybody remembers: the Sites a download makes, the folders a library
is given, a run closing on Activity. A kernel write is named by its path, `kernel.package.module.
function`, and the composition root's as `composition.function`. Where the kernel cannot know who
a write concerns because the slice calling it does, the slice announces and the kernel write is
named here with the caller whose bell tells.

The reason matters as much as the entry. What is on it divides into a few kinds: rows that decide
whether a request is answered at all; work whose result the job queue reports instead; the receipt
or companion of a press whose other write in the same request announces; rows no screen draws; and,
a write a screen draws whose caller rings the bell once for many of them.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"
SLICES = SOURCE / "slices"
KERNEL = SOURCE / "kernel"
COMPOSITION = SOURCE / "composition.py"

#: What opening a write looks like, any way a slice does it: its own handle (`self._db.write()`),
#: the database held by a store or handed to a helper or a route (`database.write()`, which also
#: reads `self._database.write()` and `store.database.write()`), a store handing its guard to its
#: service (`store.write()`), and `telling(`, which opens one and says so in the same breath, so it
#: can never be silent. It is here so the walk COUNTS it among the writes that speak.
_WRITES = (
    "_db.write()",
    "_db.execute(",
    "database.write()",
    "database.execute(",
    "store.write()",
    "telling(",
    # The kernel's spellings: a function handed the database as `db` (the catalog's writers), a
    # store that is its own guard (`self.write()`), and a store's one-statement helper
    # (`self._write(`), whose callers are the writes, so each of them is read as one.
    "db.write()",
    "db.execute(",
    "self.write()",
    "self._write(",
)

#: The spellings a walk of `service.py` with `_db` alone would miss, which is why the walk reads
#: every module. See `test_a_slice_walked_whole_is_walked_past_its_service`.
_WIDE = ("database.write()", "database.execute(", "store.write()")

#: The kernel's ways of saying so. Judged by what the function CALLS, never by its text: read as
#: text, a docstring explaining why something "is not announced" would count as announcing, and a
#: write would pass on a sentence about itself.
_ANNOUNCES = ("announce", "announce_now", "announce_arrival", "announce_opinion", "telling")

#: A slice's own wrappers that write and announce in one call. A call to one counts as saying so
#: only because every definition of that name in a slice calls the kernel itself, which
#: `test_every_wrapper_that_counts_as_saying_so_says_so` checks.
_WRAPPERS = ("_say", "_write_shared", "_write_mine", "_write_for")

_SAYS = (*_ANNOUNCES, *_WRAPPERS)

#: The writes that deliberately tell nobody, and why.
#:
#: Every entry is a promise that no screen lists what it writes, or an honest note of which screen
#: does and how it is told. Adding one is not paperwork: it is the moment somebody asks whether a
#: screen draws it, which is the question that has no other way of being asked. A screen left stale
#: is never an entry: the write announces, or its caller does, and the reason names which.
SILENT: dict[str, str] = {
    "sharing.service.share": "the flag that lets a large widening defer its pairs; the grant announces, and the filing announces each page",
    "sharing.service.revoke": "the same flag around the revoke; the revoke announces",
    "faces.store_models.keep_folder_read": "the folders a facial fingerprints import has read whole, a record for the import itself: no screen lists them",
    "kernel.content.identity_derivatives._write_pictures": "the bytes behind rows `add_derivative` announced",
    "kernel.content.identity_probes.probe_still_current": "marks a probe current; nothing a screen draws changes",
    # The two halves of clearing the Smart Search index: `clear` announces once every batch has
    # landed, which is the one moment the size on the settings screen is right.
    "semantic.store._delete_in_batches": "a batch of `clear`, which announces when all have landed",
    "semantic.store._forget_in_batches": "a batch of `clear`, which announces when all have landed",
    # The passes' kept counts: folded forward from the files marked since the last read. The
    # counts are what a screen reads, and the read that folds them is the one that announces.
    "kernel.content.backlog.totals": "folds the kept counts forward; the rows are not drawn",
    "kernel.content.backlog._ensure": "folds the kept counts forward; the rows are not drawn",
    "kernel.content.backlog._build": "folds the kept counts forward; the rows are not drawn",
    # A long pass's kept price, written as its run closes: the run's own close announces, and the
    # jobs page re-reads the price with it.
    "kernel.jobs.ledger._keep_prices": "written as a run closes, whose close announces",
    # The index rows of files already removed, whose removal announced; no screen draws them.
    "kernel.access.search_index.sweep_orphans": "drops index rows of files whose removal announced",
    # Sessions and credentials. These decide whether a request is answered at all; a screen drawing
    # them would be a screen showing somebody's session tokens.
    "auth.admin_cli.reset_password": (
        "a new password and master key from the host console, where no screen is open to tell"
    ),
    "auth.service.create_first_admin": "the first admin, before anything is on screen to tell",
    "auth.service._judge_login": "a session row, which nothing lists",
    "auth.service.logout": "the same row, removed",
    "auth.service.resolve_session_state": "stamps a session as seen; drawn nowhere",
    "auth.service.lock_app": "the lock is a fact about one session",
    "auth.service.unlock_app_with_password": "the same, in the other direction",
    "auth.service.unlock_app": "the same, by PIN",
    "auth.service.change_password": "a credential, not a list",
    "auth.service.set_pin": "a credential, not a list",
    "auth.service.revoke_all_sessions": "sessions again, and the screens they held close with them",
    "auth.service._create_session": "a session row, which nothing lists",
    "auth.service.note_device": (
        "which device and kind of window a session runs in, kept on the session row for"
        " Insights to read later; no screen draws it"
    ),
    # Work whose result the job queue reports, which announces for itself.
    "backup.service.snapshot_into": "writes a file, not a row; the queue reports the job",
    "backup.service._restore": "replaces the database wholesale; nothing survives to be told",
    "backup.service.set_schedule": (
        "the Backup preferences, saved through the settings writer it is handed"
        " (`SettingsService.apply`), which announces on the settings bell; a folder given back"
        " announces in `LibraryStore.release_unused_grants`"
    ),
    "backup.libraries.record_origin": (
        "an imported library's first History line, written at start-up before any screen is open"
    ),
    # Downloads. The queue rows announce through `DownloadService._say`; these are its memories.
    "download.art._through_the_door": (
        "re-encodes a creator's kept picture the first time it is read, keeping what it showed;"
        " the screen asking is the one that gets the answer"
    ),
    "download.art._write": (
        "a creator's picture kept as a download lands; the download queue row announces"
        " through `DownloadService._say`, and the picture is read with it"
    ),
    "download.art._fetch": (
        "a creator's picture read from their page and kept through `_write`, excused above for"
        " the reason given there"
    ),
    "download.art.keep_bytes": (
        "a creator's picture that arrived another way, kept through `_write`, excused above for"
        " the reason given there"
    ),
    "download.service_ledger._keep_site_key": (
        "which Site an address's site files under, kept so a renamed Site is still the one used"
        " (`filed_as`); no screen draws it, and the filing beside it announces in the catalog's"
        " writers"
    ),
    "download.service_ledger.record_item": (
        "which pieces of a gallery a link has already taken, so a re-run skips them; no screen"
        " draws it"
    ),
    # Faces. A press on Faces announces through its own writes: the groups (`set_aside`,
    # `move_tracks`, `set_pile_status`, the grouping), a face removed or refused, a person made,
    # and every file whose People move (`reconcile_people_of`). A scan a person pressed rings the
    # library bell when it ends; a sweep's scans are the queue's, and ring nothing per file, or
    # every open wall would re-read once a second for the length of the sweep.
    "faces.forgetting.forget": (
        "clears a deleted file's id off the reference pictures filed from it; no screen draws"
        " that id, and the file's end is announced by the delete"
    ),
    "faces.service_matching._record_matches": (
        "the receipt of what a scan or re-match named on its own, written per file inside the"
        " pipeline; a pressed scan and a re-match ring the library bell when they end"
    ),
    "faces.service_learning._record_learned": (
        "the receipt of what the learning pass filed as references, written inside a scan or"
        " re-match, which ring the library bell when they end"
    ),
    "faces.store_references.add_recognized_references": (
        "the learning pass's references, written inside a scan or re-match, told when the pass ends"
    ),
    "faces.store_references.unlearn_recognitions": (
        "part of a naming undo and of a No, written inside that press's receipt; the press"
        " announces once for all of them"
    ),
    "faces.service_matching._record_stopped_asking": (
        "the receipt of questions a re-match stopped asking; the re-match rings the library bell"
        " once when it ends (`announce_now`)"
    ),
    "faces.store_references.bring_back_starters": (
        "part of the identified undo, written inside its receipt's own telling write; the undo"
        " announces once for all of them"
    ),
    "faces.store_references.remove_references_by_id": "part of the identified undo, told by the undo's write",
    "faces.store_piles.reopen_accepted_proposal": "part of the identified undo, told by the undo's write",
    "faces.store_found.replace_pass": (
        "what one scan found in one file; the queue reports the scan, a pressed one rings the"
        " library bell, and the grouping that follows a batch announces the groups"
    ),
    "faces.store_found.extend_pass": "a resumed scan's findings for one file, told as `replace_pass` is",
    "faces.store_found.set_status": (
        "one file's scan status, written per file by a scan or a settle; drawn as counts on"
        " Settings > Faces, which re-reads them on the jobs bell the scan's queue rings"
    ),
    "faces.store_found.set_statuses": (
        "several files' scan statuses in one write, from the settle that follows a press; the"
        " press announces its People through `reconcile_people_of`, and the counts on"
        " Settings > Faces re-read on the jobs bell"
    ),
    "faces.store_found.remember_run": "which models a sweep runs under, so it is not changed midway; no screen draws it",
    "faces.store_tracks.write_successors": (
        "that an appearance found again by a rescan is the old one; bookkeeping of face ids"
        " no screen lists"
    ),
    "faces.store_tracks.attribute": (
        "a person on or off one appearance; every caller settles the file afterwards, and"
        " `reconcile_people_of` announces when its People move"
    ),
    "faces.store_tracks.forget_attribution_without_a_person": (
        "the leftovers of a person who was deleted; the delete announces the person going"
    ),
    "faces.store_tracks.restate": (
        "a pass's, a press's or an Undo's rulings on faces; every caller settles the files through"
        " `reconcile_people_of`, a re-match rings the bell at its end, and an Undo (a question"
        " asked again, which moves no person) rings it once when it ends (`WorkbenchService._rung`)"
    ),
    "faces.store_removals.reject_again": "a remembered refusal put back on a face a rescan found again; no screen changes",
    "faces.store_removals.forget_rejection": (
        "part of an Undo of a refusal, reached only through the faces reverser; the settle after"
        " it announces when the name comes back, and the Undo rings the library bell once when it"
        " ends (`WorkbenchService._rung`)"
    ),
    "faces.store_grouping.remember_ignored": (
        "the memory of a set-aside group, so a rescan keeps it aside; the press's"
        " `set_aside` or `set_pile_status` announces the group moving"
    ),
    "faces.store_grouping.remember_grouping": (
        "the memory of a hand-made group, so it outlives a rescan; `move_tracks` announces the move"
    ),
    "faces.store_grouping.forget_grouping": (
        "the hand-made memory cleared before faces move or go aside; the move that follows announces"
    ),
    "faces.store_grouping.group_again": "faces a rescan found put back in their hand-made group, told as `replace_pass` is",
    "faces.store_grouping.forget_ignored": (
        "the set-aside memory cleared when a group is brought back; `set_pile_status` announces"
    ),
    "faces.store_grouping.remember_confirmation": (
        "the memory of a name, so a rescan applies it again; the naming announces through"
        " `reconcile_people_of`"
    ),
    "faces.store_grouping.forget_confirmation": (
        "the memory of a name dropped as the name comes off; the refusal beside it announces"
    ),
    "faces.store_grouping.set_aside_again": "faces a rescan found put back in their set-aside group, told as `replace_pass` is",
    "faces.store_grouping.drop_empty_piles": (
        "empty groups dropped after a press or a grouping, whose own write announced just"
        " before; a beat between the two can draw an empty group once"
    ),
    "faces.store_references.add_reference": (
        "a reference picture learned from a naming or an import; no screen lists the pictures,"
        " and the naming announces through `reconcile_people_of`"
    ),
    "faces.store_references.keep_pack_entry": "somebody a pack named, held without being a person; no screen lists the entries",
    "faces.store_references.keep_entry_face": "one face under a held pack entry; no screen lists them",
    "faces.store_references.add_references": "a pack's reference pictures, which no screen lists",
    "faces.store_references.claim_references_in": "one file's orphaned references handed to the appearance a rescan found; no screen lists them",
    "faces.store_references.retire_starters": "starter pictures retired by an Undo; no screen lists the pictures",
    "faces.store_references.remember_starters_refused": "a note that every starter of one person was refused by a model; nothing draws it",
    "faces.store_references.forget_starters_refused": "that note taken away when something of theirs is filed; nothing draws it",
    "faces.store_references.remove_references_from_track": (
        "the pictures one appearance taught a person, taken back beside a refusal that announces"
    ),
    "faces.store_models.remeasure_file": "one file's faces described again by a new model, a job the queue reports",
    "faces.store_models.forget_reference": "a reference whose picture has gone from the disk, dropped by the remeasure job",
    "faces.store_models.remeasure_reference": "one reference described again by a new model, by the remeasure job",
    "faces.store_models.remove_references": "references dropped by a pack import that replaces them; no screen lists them",
    "faces.store_models.record_pack": "the row of an imported pack, which no screen lists",
    "faces.store_references.decline_entry": "its one caller, the Undo of a facial fingerprints line, tells every admin in the same act",
    "faces.store_models.folder_import_pack": "the standing row folder imports hang held people off; no screen lists it",
    "faces.store_models.own_library": "this library's id for the files it makes, minted once; no screen draws it",
    "faces.store_left_out.begin": "an earlier import's left-out files cleared as the next starts; the queue reports the job",
    "faces.store_left_out.keep": "the files one import left out, read by that job's own report, which the queue reports",
    "faces.store_models.record_weight": "which model files are installed, from the download job the queue reports",
    "faces.store_piles.propose_pile": (
        "a may-be card made by the folder pass per folder; the pass counts the cards it put up"
        " and rings the library bell once when it ends (`SuggestionService.rebuild`), never one"
        " per folder, and Faces > Groups re-reads on it"
    ),
    "faces.store_piles.withdraw_pile_proposals": (
        "a may-be card the same pass takes back, counted and told as `propose_pile` is"
    ),
    "faces.store_piles.settle_pile_proposal": (
        "the answer to a may-be card, beside the naming or refusal of its faces that announces"
    ),
    "faces.service_references._claim_entry": (
        "a held entry's faces given to a person just created, whose creation announces in the"
        " same request; a claim that lands asks for a re-match, which rings the library bell"
        " when it ends"
    ),
    "faces.store_people.add_alias": (
        "its one statement is `add_alias_on`, which announces inside the same transaction"
    ),
    "faces.store_removals.reconcile_people_of": (
        "its one write is `_bring_into_line`, which rings the library bell once at its end over"
        " everybody who moved"
    ),
    # Insights. Drawn from days that are over, added up by the quiet helper once a day.
    "insights.rollup.add_up_one_day": (
        "one finished day's figures, added up once a day by the quiet helper; Insights reads"
        " them when it opens"
    ),
    "insights.rollup.add_up_again": (
        "one finished day's figures added up again by the quiet helper after a later write reached"
        " it; Insights reads them when it opens"
    ),
    "insights.rollup.split_one_stale_day": (
        "one finished day's vault split worked out again by the quiet helper; Insights reads"
        " it when it opens"
    ),
    "insights.capture.record_visits": (
        "the pages one User opened and the sitting they were part of, captured for Insights to"
        " read later; no screen draws a visit or a sitting"
    ),
    # The rest of the slices, one or two writes each.
    "media_edit.provenance.record": (
        "which file a made copy came from; the copy's arrival is announced by the import that"
        " takes it in, and an open file page re-reads its History on the jobs bell"
    ),
    "performance.rates.save": (
        "the machine's measured rates, written as the self-test ends; Settings > Performance"
        " follows that run while it is on screen and reads the rates when it opens"
    ),
    "player.plays.record_play": (
        "one sitting kept whole for the daily adding-up; the counters screens draw were written"
        " just before by `UserStateStore`, which announces them as opinions"
    ),
    "search.jobs.forget_events_older_than": (
        "recorded searches past the horizon, dropped by a daily job; the recent searches list"
        " draws the newest, which the horizon never reaches"
    ),
    "search.service.opened": (
        "a file opened from a typed search, captured for Insights to read later; the recent"
        " searches list draws the searches, never what was opened from them"
    ),
    "theater.sessions.record_session": (
        "one report of a Theater wall opening or ending, read only by the daily adding-up"
    ),
    "tidy.router.optimize_database": (
        "folds the log back in and re-plans the indexes; every row answers the same, and the"
        " new size goes back to the tab that pressed"
    ),
    # Semantic search. The index is built and pruned by jobs the queue reports; no screen lists it.
    "semantic.records.mark": "that one file has been described, by the describing job",
    "semantic.records.forget": (
        "one file's description taken back by the look again at HEIF photos, a job the queue reports"
    ),
    "semantic.records.forget_all": (
        "every record of what was described, dropped with the index; see `semantic.store.clear`"
    ),
    "semantic.records.forget_others": "the records of another model's descriptions, dropped by the describing job",
    "semantic.store.ensure_ready": "makes the index table before its first write; no screen lists it",
    "semantic.store.put": "one file's frames in the index, by the describing job",
    "semantic.store.forget": "one file's frames dropped from the index when it ends",
    "semantic.store.describes": "a pooled vector kept on the way past a lookalike question; stores nothing new",
    "semantic.store.prune": "the vectors of files that left the library, dropped by a job",
    "semantic.store.purge_other_revisions": "another model's frames dropped by the describing job",
    # The stash-box. What a person sees change announces through `_say`; these are its caches.
    "stash_boxes.asking._one": "the cache of a box's answers, read only by its own next lookup; no screen draws it",
    "stash_boxes.configured.forget_answers": "the same cache emptied; the next lookup asks the box again",
    "stash_boxes.configured._answers_read_now": (
        "the same cache emptied once when the adapter reads boxes differently; nothing draws it"
    ),
    "stash_boxes.configured.record_catch_up": (
        "which one-time catch-up pass has run. Nothing draws it, and what the pass"
        " itself wrote announces through the writers it went through"
    ),
    # Importing a Stash library, one job the queue reports.
    "stash_migration.service._ensure": "a waiting person, Site or tag made as its file lands: the record goes through the merge writer, the filing under a parent and the opinions through their own writers, each of which announces, and the pass reports through the queue",
    "stash_migration.service._catalog": "the import's own record of which Stash row became which Sift row; every row it lands goes through a writer that announces",
    "stash_migration.waiting.keep_gallery": "a Stash gallery kept for files still waiting, written by the run; no screen lists it",
    "stash_migration.waiting.keep_group": "a Stash group kept for files still waiting, written by the run; no screen lists it",
    # Folder suggestions. Every press announces: a No and a bring-back to every admin, and a Yes,
    # an Undo or a take-back to the audience of the People and Sites it moved, whose cache stamps
    # move in the same transaction (`SuggestionsService._told`), because the access layer's `_on`
    # writers these go through do neither. The pass rings the library bell ONCE when it ends
    # (`rebuild`), never per folder; what is left here is the pass's own writers.
    "suggestions.service_pictures._record_number_learned": (
        "a Username's receipt of what its numbers taught, written by the pass; the pass rings the"
        " library bell once when it ends (`rebuild`), never per folder"
    ),
    "suggestions.service_filenames._file_under": (
        "one Username's files filed under it by the pass; Sites and the Username's wall re-read"
        " on the one library bell the pass rings when it ends (`rebuild`)"
    ),
    "suggestions.service_filenames._record_set": "a set's receipt beside the pass's filings, told as `_file_under` is",
    "suggestions.service_filenames.group_filings_into_posts": (
        "the post of files the pass filed earlier; the Username's wall re-reads on the pass's one"
        " bell at its end (`rebuild`)"
    ),
    "suggestions.service_folders._carry_answers": (
        "one folder read by the pass, down to what it wrote or filed; People and Sites re-read"
        " on the one bell the pass rings when it ends (`rebuild`), never one per folder"
    ),
    "suggestions.service_folders._attribute": (
        "the pass putting a person on a folder's files; the person's wall and the grid re-read"
        " on the pass's one bell at its end (`rebuild`)"
    ),
    "suggestions.store.close_pass": (
        "questions a pass no longer asks, taken back, and the folders it read remembered;"
        " Organize > Suggestions re-reads on the pass's one bell at its end (`rebuild`)"
    ),
    "suggestions.store.add_claim": (
        "a question filed by the pass, heard on its one bell at its end (`rebuild`), or by a"
        " person naming a folder, whose `confirm` that follows announces"
    ),
    "suggestions.store.remember_waiting_numbers": (
        "the numbers the folder pass saw waiting for a Username, replaced whole by each pass;"
        " no screen draws them"
    ),
    "suggestions.store.write": (
        "hands its guard to the service, whose transactions this walk reads as `store.write()`"
    ),
    # The music feature. What a person SEES change (a song named on a file, a name taken back,
    # the lookup's key, the card put away) announces; these are the rows of the fingerprint and
    # lookup tasks themselves.
    "music.store.keep": "a file's fingerprint row, which no screen lists; the task that writes it is reported by the queue",
    "music.store.keep_pending": "a fingerprint waiting at staging for its file, which no screen lists",
    "music.store.claim": "moves a waiting fingerprint onto its file; no screen lists either row",
    "music.store.forget_pending_before": "drops staging fingerprints whose file never arrived; nothing lists them",
    "music.store.index_keys": "the key index the matcher searches; no screen reads it",
    "music.store.settle": "the stamp saying a file's pairing is done, which no screen draws",
    # The swap. The device row and every write to a session's row announce: the device id on
    # Settings > Privacy > Swaps, and the state, counts and rate the swap screen and Activity
    # draw. These are the manifests: which chunks of a file being received are verified, kept so a
    # dropped connection resumes. No screen lists a manifest; the counts a screen draws are the
    # session row's.
    "swap.store.put_manifest": "a received file's chunk ledger, which no screen lists; the session row carries the counts",
    "swap.store.adopt": "moves an unfinished chunk ledger onto a later session from the same device; no screen lists it",
    "swap.store.mark_done": "one verified chunk appended to a ledger no screen lists; the session row carries the bytes",
    "swap.store.drop_manifest": "a finished or abandoned file's chunk ledger removed; no screen lists it",
    "swap.ingest.record_folder": "the id of the folder a swap made, kept on its session row for the folder pass; no screen draws it",
    # Scheduled tasks.
    # Watermarks. A file gaining a tag announces through the store's `tag_on`.
    "watermarks.service._settle": (
        "one file's reading filed by the read job; a tag it adds announces in `tag_on`, and the"
        " file page re-reads on the jobs bell"
    ),
    "watermarks.store.write": (
        "hands its guard to the service, whose transactions this walk reads as `store.write()`"
    ),
    "watermarks.store.forget_everything": (
        "every reading thrown away so the library is read again; no screen lists the readings,"
        " and the filings they made are left standing"
    ),
    # The decisions list itself.
    "workbench.store.mark_reversed": (
        "a decision claimed for an Undo; the press rings the library bell once, after every"
        " receipt it took back (`WorkbenchService._rung`), so a fold is one re-read"
    ),
    "workbench.store.unmark_reversed": "the claim given back when an Undo did nothing; nothing moved to tell",
    # The kernel and the composition root. Where the kernel knows who a write concerns it says so
    # itself (a Site, a Username or a person made, a folder given or taken back, a file's record,
    # a run closing); what is left here is bookkeeping no screen draws, the helpers whose callers
    # are judged where they stand, and the writes a slice's press tells for.
    "kernel.access.catalog.numbers.set_username_numbers": (
        "the Site's own number filled onto Usernames by the folder pass"
        " (`SuggestionService._learn_username_numbers`); the pass rings the library bell once"
        " when it ends (`rebuild`), never per folder"
    ),
    "kernel.access.catalog.numbers.remember_username_number": (
        "a Username's number learned by the folder pass as it reads one folder"
        " (`SuggestionService._learn_number`), told as `set_username_numbers` is"
    ),
    "kernel.access.repository.store.forget_items": (
        "grants naming files a delete has ended; the files left every listing in the delete's own"
        " write, which announces once for the batch (`Deleter.remove_many`, through"
        " `forget_locations`)"
    ),
    "kernel.access.search_index.index_assets": (
        "the search index for one file or many, which no screen lists; a search reads it afresh"
        " each time it runs, and the edit that changed the text announces"
    ),
    "kernel.access.search_index.index_new_assets": (
        "the files with no index row yet, indexed by the search job; no screen lists the index"
    ),
    "kernel.content.hashing.record_whole_digest": (
        "a whole-file digest taken as a file lands; nothing in Sift reads the column, and the"
        " file's arrival announces"
    ),
    "kernel.content.identity_arrivals.upsert_asset": (
        "no caller in the application: files come in through `ingest`, which announces the arrival"
    ),
    "kernel.content.identity_arrivals.add_location": (
        "no caller in the application: files come in through `ingest`, which announces the arrival"
    ),
    "kernel.content.identity_arrivals.adopt_identity": (
        "one old row brought forward to the sampled identity by the look-again job; the file"
        " draws the same, and the queue reports the job"
    ),
    "kernel.content.identity_verdicts.record_verdict": (
        "the reason a feature could not make something for one file, written per file by the"
        " job that tried; a sweep rings nothing per file, the queue reports the job on the jobs"
        " bell, and a file page reads the reason when it opens"
    ),
    "kernel.content.identity_verdicts.clear_verdicts": (
        "verdicts cleared by a retry press, which queues the work again; each picture the work"
        " makes announces its arrival (`_write_pictures`)"
    ),
    "kernel.content.identity_verdicts.forget_transient_verdicts": (
        "a passing verdict dropped when a scan sees the file again; the work it lets run again"
        " announces what it makes"
    ),
    "kernel.content.identity_probes.record_fingerprints": (
        "a file's near-duplicate fingerprints, written by the fingerprint job; no screen draws"
        " them, and the queue reports the job"
    ),
    "kernel.content.identity_probes.reclassify": (
        "what kind the classifier in use says a file is, stamped on every file once per"
        " classifier by a pass the queue reports; a sweep rings nothing per file"
    ),
    "kernel.content.identity_probes.send_to_classifier": (
        "a row put below the classifier line because its bytes refute its stored kind; the"
        " reclassify pass it asks for writes the kind and announces, this writes nothing a screen"
        " draws"
    ),
    "kernel.content.identity_probes.record_still_moment": (
        "where a tile's still was cut, kept for cutting it again; the still itself announces"
        " its arrival through `add_derivative`"
    ),
    "kernel.content.identity_probes.keep_probe": (
        "a file's kept reading and the shape of its sound, stored by the catch-up job for a file"
        " whose fields are already written; the queue reports the job, and a file page reads"
        " them when it opens"
    ),
    "kernel.content.identity_store._write": (
        "the store's one-statement helper; every caller is read as a write of its own"
        " (`self._write(`) and judged here by name"
    ),
    "kernel.content.library.upsert_folder": (
        "a folder row made as a scan walks; the files under it announce their arrival, and a"
        " press that makes a folder announces (`LibraryService.create_folder`,"
        " `place_folder`)"
    ),
    "kernel.content.library.record_folder_mtime": (
        "what a folder's directory looked like when a scan walked it, so the next scan can skip"
        " it; no screen draws it"
    ),
    "kernel.content.library._write": (
        "the store's one-statement helper; every caller is read as a write of its own"
        " (`self._write(`) and judged here by name"
    ),
    "kernel.content.user_state.add_replay_heat": (
        "time one sitting spent on each slice of a file, added up for the player, which reads it"
        " when a file opens; a bell per sitting would re-read every open wall for one page's line"
    ),
    "kernel.covers.receive": (
        "an uploaded picture's row, which no screen lists; a subject takes it as its cover"
        " through the slice's cover write, which announces"
    ),
    "kernel.covers.forget": (
        "a stored picture nothing points at any more, dropped with its file; no screen lists them"
    ),
    "kernel.db.refresh_statistics": (
        "the query planner's statistics read again; every row answers the same"
    ),
    "kernel.db.copy_the_log_back": (
        "the write-ahead log copied back into the database; every row answers the same"
    ),
    "kernel.db.fold_the_log_back": (
        "the write-ahead log folded back into the database; every row answers the same"
    ),
    "kernel.db.execute": (
        "the database's own one-statement door; every caller is read as a write where it stands"
        " (`_db.execute(`) and judged there"
    ),
    "kernel.db.initialize_schema": (
        "the schema brought up to date at start-up, or under a restore that replaces the"
        " database, with nobody connected to tell"
    ),
    "kernel.jobs.ledger.settle": (
        "writes running passes through `_write` and closes them through `_finish`, which rings"
        " the jobs bell"
    ),
    "kernel.jobs.ledger._write": (
        "a running pass's figures written through while its jobs run; the queue's own writes ring"
        " the jobs bell Activity follows (`JobQueue._writing`)"
    ),
    "kernel.jobs.ledger.start": (
        "closes what the last process left open, at start-up before anything is served"
    ),
    "kernel.jobs.ledger.said": (
        "what a live run's row said this minute, kept to score the run at its end; no screen draws"
        " the minutes, and the score lands through `_finish`"
    ),
    "kernel.jobs.queue_settle.beat": "a running job's heartbeat, several a minute; no screen draws it",
    "kernel.secret_store.seal": (
        "a sealed secret, which no screen may ever draw; the row that names it announces"
    ),
    "kernel.secret_store.forget": "a sealed secret deleted, which no screen draws",
    "kernel.tunnels.store.add": (
        "a tunnel imported; the route that pressed tells every admin on the settings bell"
        " (`download.router.import_tunnel`)"
    ),
    "kernel.tunnels.store.rename": (
        "a tunnel renamed; the route tells every admin on the settings bell"
        " (`download.router.rename_tunnel`)"
    ),
    "kernel.tunnels.store.replace_config": (
        "a tunnel's configuration swapped; the route tells every admin on the settings bell"
        " (`download.router.replace_tunnel_config`)"
    ),
    "kernel.tunnels.store.remove": (
        "a tunnel forgotten; the route tells every admin on the settings bell"
        " (`download.router.delete_tunnel`)"
    ),
    "kernel.tunnels.store.start": (
        "a tunnel started; the route tells every admin on the settings bell"
        " (`download.router.start_tunnel`)"
    ),
    "kernel.tunnels.store.stop": (
        "a tunnel stopped by its own route, or by a replace or a remove, and each of those"
        " routes tells every admin on the settings bell (`download.router.stop_tunnel`)"
    ),
    "kernel.tunnels.store.set_route": (
        "a Site's route chosen; the route tells every admin on the settings bell"
        " (`download.router.set_route`)"
    ),
    "kernel.tunnels.store.clear_route": (
        "a Site put back to the default route; the route tells every admin on the settings bell"
        " (`download.router.clear_route`)"
    ),
}


def _calls(node: ast.AST) -> set[str]:
    """The names a function calls, by the last part of each: `announce` for `announce(...)` and
    `self._say` for `self._say(...)` alike."""
    names: set[str] = set()
    for one in ast.walk(node):
        if isinstance(one, ast.Call):
            called = one.func
            if isinstance(called, ast.Attribute):
                names.add(called.attr)
            elif isinstance(called, ast.Name):
                names.add(called.id)
    return names


def _modules() -> Iterator[tuple[str, Path]]:
    """Every module the walk reads, each with the prefix its functions are named under.

    Every module of every slice, as `slice.module`, and every module of a package inside a slice
    (the downloads feature's sources and Sites), as `slice.package.module`; every module of the
    kernel, as its dotted path from the source root; and the composition root. A `tests/` folder
    is not walked.
    """
    for path in sorted(SLICES.rglob("*.py")):
        parts = path.relative_to(SLICES).with_suffix("").parts
        if len(parts) < 2 or "tests" in parts:
            continue
        yield ".".join(parts), path
    for path in sorted(KERNEL.rglob("*.py")):
        if "tests" in path.relative_to(KERNEL).parts:
            continue
        yield ".".join(path.relative_to(SOURCE).with_suffix("").parts), path
    yield "composition", COMPOSITION


def functions_in(
    prefix: str, source: str
) -> Iterator[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef, str]]:
    """Every function in one module's source, as `prefix.function`, its node and its source.

    Factored out so a fixture module can be run through exactly the reading the walk does."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            yield f"{prefix}.{node.name}", node, ast.get_source_segment(source, node) or ""


def writes_in(prefix: str, source: str) -> list[tuple[str, bool]]:
    """Every function in one module's source that opens a write, and whether it says so."""
    return [
        (name, _says(node))
        for name, node, body in functions_in(prefix, source)
        if any(one in body for one in _WRITES)
    ]


def _functions() -> Iterator[tuple[str, ast.AsyncFunctionDef | ast.FunctionDef, str]]:
    """Every function the walk reads, named, with its node and its source."""
    for prefix, path in _modules():
        yield from functions_in(prefix, path.read_text(encoding="utf-8"))


def _says(node: ast.AST) -> bool:
    return bool(_calls(node) & set(_SAYS))


def _writes() -> list[tuple[str, bool]]:
    """Every function that opens a write, by name, and whether it says so."""
    found: list[tuple[str, bool]] = []
    for prefix, path in _modules():
        found.extend(writes_in(prefix, path.read_text(encoding="utf-8")))
    return found


def _silent_writes() -> list[str]:
    """Every write that tells nobody."""
    return [name for name, says in _writes() if not says]


def test_a_write_either_says_who_it_concerns_or_says_why_it_does_not() -> None:
    undeclared = sorted(set(_silent_writes()) - set(SILENT))

    assert not undeclared, (
        "\nThese write and tell nobody. If a screen lists what they change, it will show the answer\n"
        "from before them until somebody reloads the page, on another tab, on another computer.\n"
        "Announce it, or name it in SILENT with the reason no screen draws it.\n\n  "
        + "\n  ".join(undeclared)
        + "\n"
    )


def test_nothing_is_excused_that_no_longer_needs_excusing() -> None:
    """An exemption for a write that has gone, or has learned to speak, is an exemption quietly
    covering a real one later."""
    stale = sorted(set(SILENT) - set(_silent_writes()))

    assert not stale, (
        "\nThese are excused and do not need to be: they have gone, or they announce now.\n"
        "Take them out of SILENT, or the next write to take one of these names is excused with\n"
        "them.\n\n  " + "\n  ".join(stale) + "\n"
    )


def test_the_walk_finds_the_writes_it_is_about() -> None:
    """A walk that found nothing would pass for ever, and so would one that found everything."""
    speaking = sum(1 for _, says in _writes() if says)

    assert speaking >= 15, f"only {speaking} writes announce; the walk is broken"
    assert speaking > len(SILENT), "more writes are excused than speak; the rule has inverted"


def test_a_slice_walked_whole_is_walked_past_its_service() -> None:
    """The wide walk's known positive: a write in a module that is not `service.py`, spelled only
    the way the wider walk reads, is found."""
    narrow = [one for one in _WRITES if one not in _WIDE]
    found = [
        name
        for name, _node, body in _functions()
        if name.split(".")[1] != "service"
        and any(one in body for one in _WIDE)
        and not any(one in body for one in narrow)
    ]
    written = {name for name, _ in _writes()}

    assert found, "no write outside a service is spelled the wide way; the widening reads nothing"
    assert set(found) <= written, "a write the wide spellings match is missing from the walk"
    assert "faces.forgetting.forget" in found, "the known one (`self._database.write()`) is gone"


def test_a_package_inside_a_slice_is_walked_too() -> None:
    """Every module of every slice means the packages inside one as well: a write added to the
    downloads feature's Site modules is asked the same question as one in its service. A slice's
    tests are not walked, at any depth."""
    walked = {prefix for prefix, _ in _modules()}

    assert "download.sources.registry" in walked, "a slice's own package is not read"
    assert any(prefix.startswith("download.sources.sites.") for prefix in walked)
    assert "download.service" in walked and "faces.forgetting" in walked
    assert not any(".tests" in prefix or prefix.startswith("tests") for prefix in walked)


def test_the_walk_reads_the_kernel_and_the_composition_root() -> None:
    """The wide walk's known positives: a kernel write that speaks, one excused by name, and the
    composition root's, each named the way the table names them."""
    written = dict(_writes())

    assert written.get("kernel.jobs.queue_core._writing") is True, "the queue's door is not read"
    assert written.get("kernel.content.library._write_shared") is True, "a kernel wrapper is missed"
    assert written.get("kernel.db.execute") is False, "the database's own door is not read"
    assert written.get("composition.person_named") is True, "the composition root is not read"
    assert not any(".tests." in name for name in written), "a kernel test module was walked"


_FIXTURE = """
async def ensure_site(db, name):
    async with db.write() as connection:
        await connection.execute(INSERT_SITE, (name,))

async def ensure_site_told(db, name):
    async with telling(db, EVERY_ADMIN, About.LIBRARY) as connection:
        await connection.execute(INSERT_SITE, (name,))

class Store:
    async def _write(self, sql, params):
        async with self._db.write() as connection:
            return await connection.execute(sql, params)

    async def set_title(self, asset_id, title):
        await self._write(SET_TITLE, (title, asset_id))

    async def set_title_told(self, asset_id, title):
        async with self._db.write() as connection:
            await connection.execute(SET_TITLE, (title, asset_id))
            announce(await who_may_see_a_file(connection), About.LIBRARY)

    async def heartbeat(self, job_id):
        await self._db.execute(BEAT, (job_id,))

def a_reader(rows):
    return [row["id"] for row in rows]
"""


def test_a_kernel_module_is_read_by_the_same_rules() -> None:
    """A kernel-shaped fixture module run through exactly the reading the walk does: a write
    handed the database as `db`, a store's one-statement helper and the callers of it, and the
    database's own door are writes; telling and announcing are saying so; a reader is nothing."""
    found = dict(writes_in("kernel.fixture", _FIXTURE))

    assert found == {
        "kernel.fixture.ensure_site": False,
        "kernel.fixture.ensure_site_told": True,
        "kernel.fixture._write": False,
        "kernel.fixture.set_title": False,
        "kernel.fixture.set_title_told": True,
        "kernel.fixture.heartbeat": False,
    }


def test_a_kernel_write_that_tells_nobody_fails_the_rule() -> None:
    """The planted mistake, the way it happens: a catalog writer opens its write and says nothing.
    Unexcused, it is exactly what the first rule refuses."""
    silent = [name for name, says in writes_in("kernel.fixture", _FIXTURE) if not says]

    assert "kernel.fixture.ensure_site" in silent
    assert not set(silent) & set(SILENT), "a fixture name is excused in the real table"


def test_a_sentence_about_announcing_is_not_announcing() -> None:
    """Saying so is a call, never a word in a docstring or a comment."""
    told = ast.parse('async def a():\n    """Nothing announces this."""\n    await x()\n')
    tells = ast.parse("async def a():\n    announce(EVERY_ADMIN, About.LIBRARY)\n")

    assert not _says(told.body[0])
    assert _says(tells.body[0])


def test_every_wrapper_that_counts_as_saying_so_says_so() -> None:
    """A call to a slice's `_say` counts as announcing only while every `_say` in a slice
    announces. A new one of that name that writes and tells nobody would pass every caller."""
    wrappers = [(name, node) for name, node, _ in _functions() if node.name in _WRAPPERS]

    assert wrappers, "no slice defines a wrapper; take the names out of _WRAPPERS"
    for name, node in wrappers:
        assert _calls(node) & set(_ANNOUNCES), f"{name} is counted as saying so and does not"


def test_every_reason_is_a_reason() -> None:
    """An empty excuse is not one, and neither is a name repeated back."""
    for name, reason in SILENT.items():
        assert len(reason.split()) >= 4, f"{name} is excused without a reason"


def test_no_write_is_excused_as_owed() -> None:
    """An excuse saying a screen is left stale until something is built is a fault written down,
    not a reason. Every write a screen draws announces, or names the caller whose bell tells."""
    owed = sorted(name for name, reason in SILENT.items() if re.search(r"\bowed\b", reason))

    assert not owed, "\nThese are excused as owed; announce them or name who tells:\n  " + (
        "\n  ".join(owed)
    )
