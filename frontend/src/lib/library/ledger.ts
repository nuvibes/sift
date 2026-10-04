// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE RECORD OF EVERYTHING THIS LIBRARY HAS DONE: reading it, and the two filters over it.
 *
 * Every line is built on the server, by the one builder per act every History screen uses
 * (`kernel/access/sentences.py`), and arrives as PIECES (plain words and the things they name,
 * each where it sits), which `HistorySentence` draws. Nothing here assembles a sentence, names a
 * thing or says who did it, and nothing here searches a sentence for a name: a second word table in
 * the client is a second copy that drifts, and one act would read differently on different screens.
 *
 * What is left is what the two filters need: what a filter over each KIND of thing is called, and
 * what a filter over each ACT is called.
 *
 * ## No "your year" here
 *
 * A record of what the installation did and a story about what one PERSON did are different
 * surfaces for different audiences: this one is admin-only by its nature and that one must not be.
 */
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/**
 * One event, exactly as the server describes it: its line as pieces, and the facts it was built from.
 *
 * Taken from the generated schema rather than written out again. A copy beside it does not fail
 * when the server moves. It goes quietly wrong, and the screen is where somebody finds out.
 */
export type LedgerEvent = components['schemas']['LedgerEventView'];

/** A page of the record. */
export type LedgerPage = components['schemas']['LedgerPage'];

/** How many events one page holds. The server's own default, said here because the pane pages. */
export const PAGE = 50;

/* What the feed was filtered to, or nothing at all. Not exported: callers write the object inline
   and never name the type, and a name nothing reads is refused by `public-surface.test.ts`. */
interface LedgerAsk {
	limit?: number;
	offset?: number;
	/** A subject kind. See `KINDS`. */
	kind?: string;
	/** One act. See `VERBS`. */
	verb?: string;
	/**
	 * Only the decisions: what a queue can take back, Organize's answers and Sift's own filings,
	 * each with its Undo. A narrowing of this one feed and not a list beside it.
	 */
	decisions?: boolean;
}

/** Read a page of the record. Admin-only on the server; nothing here is the control. */
export async function readLedger(ask: LedgerAsk = {}): Promise<LedgerPage> {
	return await api.get<LedgerPage>('/ledger', {
		query: {
			limit: ask.limit ?? PAGE,
			offset: ask.offset ?? 0,
			kind: ask.kind || undefined,
			verb: ask.verb || undefined,
			decisions: ask.decisions || undefined
		}
	});
}

/** What an Undo all did: how many decisions moved back, out of how many the line stood for. */
type UndoneAll = components['schemas']['UndoneFoldView'];

/**
 * UNDO ALL on a folded line: every decision the press it stands for took, each through its own undo.
 *
 * The filtering goes back with it because the server reads the press again the way the line was
 * drawn: a press is a reading of the record under that filter, and what is undone must be
 * exactly what the line said (`ledger_router.undo_all`).
 */
export async function undoAll(
	eventId: string,
	narrowing: { kind?: string; verb?: string; decisions?: boolean } = {}
): Promise<UndoneAll> {
	return await api.post<UndoneAll>(`/ledger/${encodeURIComponent(eventId)}/undo-all`, {
		query: {
			kind: narrowing.kind || undefined,
			verb: narrowing.verb || undefined,
			decisions: narrowing.decisions || undefined
		}
	});
}

/* --- the two filters --------------------------------------------------------------------------- */

/**
 * WHAT A FILTER OVER EACH KIND OF THING IS CALLED, in the plural, as the walls already name them.
 *
 * The same closed list the kernel's `SubjectKind` holds; `test_the_feed_says_every_word.py` holds the
 * two together, because a kind with no label is a filter nobody can choose.
 */
export const KINDS: Record<string, { many: string }> = {
	asset: { many: 'Files' },
	person: { many: 'People' },
	folder: { many: 'Folders' },
	pile: { many: 'Groups of faces' },
	username: { many: 'Usernames' },
	login: { many: 'Users' },
	tag: { many: 'Tags' },
	site: { many: 'Sites' },
	collection: { many: 'Collections' },
	photo_set: { many: 'Photo Sets' },
	song: { many: 'Songs' },
	shoot: { many: 'Shoots' },
	box: { many: 'Stash-boxes' },
	grant: { many: 'Shares' },
	setting: { many: 'Settings' },
	download: { many: 'Downloads' },
	run: { many: 'Tasks' },
	swap: { many: 'Swaps' },
	backup: { many: 'Backups' },
	database_file: { many: 'Database files' },
	computer: { many: 'The computer running Sift' }
};

/**
 * WHAT A FILTER OVER EACH ACT IS CALLED. Every verb the kernel can record has a row, and nothing
 * else does (`test_the_feed_says_every_word.py`); the LINE for each act is the server's.
 */
export const VERBS: Record<string, { label: string }> = {
	added: { label: 'Added' },
	removed: { label: 'Removed' },
	renamed: { label: 'Renamed' },
	named: { label: 'Named' },
	filed: { label: 'Filed' },
	moved: { label: 'Moved' },
	linked: { label: 'Linked' },
	unlinked: { label: 'Removed a link' },
	hidden: { label: 'Hidden' },
	revealed: { label: 'Unhidden' },
	shared: { label: 'Shared' },
	unshared: { label: 'Stopped sharing' },
	kept_local: { label: 'Stash-box lookups turned off' },
	allowed: { label: 'Stash-box lookups turned on' },
	kept_from_swaps: { label: 'Kept out of swaps' },
	allowed_in_swaps: { label: 'Let into swaps again' },
	merged: { label: 'Merged' },
	edited: { label: 'Edited' },
	enriched: { label: 'Filled in from a stash-box' },
	asked: { label: 'Asked a stash-box' },
	scanned: { label: 'Fingerprinted' },
	face_run: { label: 'Looked for faces' },
	produced: { label: 'Created from another file' },
	deleted: { label: 'Deleted' },
	forgot: { label: 'Deleted what Sift knew' },
	downloaded: { label: 'Downloaded' },
	download_failed: { label: 'Could not download' },
	paused: { label: 'Paused' },
	resumed: { label: 'Resumed' },
	cookies_saved: { label: 'Cookies saved' },
	cookies_replaced: { label: 'Cookies replaced' },
	cookies_forgotten: { label: 'Cookies deleted' },
	canceled: { label: 'Canceled' },
	saved: { label: 'Saved to a device' },
	ran: { label: 'Ran a task' },
	pressed: { label: 'Ran a task on a file' },
	decided: { label: 'Decided in Organize' },
	song_named: { label: 'Named a song' },
	swap_started: { label: 'Started or joined a swap' },
	swap_ended: { label: 'Swap ended' },
	restored: { label: 'Restored from a backup' },
	adopted: { label: 'Created from a database file' },
	sharing_turned_on: { label: 'Network sharing turned on' },
	sharing_turned_off: { label: 'Network sharing turned off' },
	start_with_windows_on: { label: 'Set to start with Windows' },
	start_with_windows_off: { label: 'No longer starts with Windows' },
	firewall_opened: { label: 'Firewall port opened' },
	storage_moved: { label: 'Sift data moved' },
	update_started: { label: 'Update started' },
	library_opened: { label: 'Another library opened' },
	restarted: { label: 'Restarted' },
	wall_sent: { label: 'Sent a Theater wall' }
};

/* --- the filter row ---------------------------------------------------------------------------- */

/** What "no filter" is called on the wire and in the two choosers. */
export const ANY = 'all';

/**
 * THE TWO FILTERS, BUILT FROM THE TABLES THEMSELVES rather than from a list beside them.
 *
 * A hand-written list of choices is the thing that goes one line short: a verb added to `VERBS`
 * with no row in a filter list is an act the record holds and nobody can ask for, and nothing would
 * say so. These cannot be short, because they ARE the tables.
 */
export function kindChoices(): { value: string; label: string }[] {
	return [
		{ value: ANY, label: 'Everything' },
		...Object.entries(KINDS).map(([value, said]) => ({ value, label: said.many }))
	];
}

/** Every act, in the order the table declares them, with "Everything" first, as Type has. */
export function verbChoices(): { value: string; label: string }[] {
	return [
		{ value: ANY, label: 'Everything' },
		...Object.entries(VERBS).map(([value, act]) => ({ value, label: act.label }))
	];
}
