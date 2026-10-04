/* Walking the folders Sift has been given, so one can be chosen by clicking it.
 *
 * The top of the walk is the list of folders somebody handed over through Windows' own dialog.
 * Below that it is folders all the way down. There is nothing sensible to TYPE either way: the server says what is there and this walks
 * it.
 *
 * IT WALKS ONE OF TWO SETS. `granted` is that list, and is what almost every screen wants. `machine`
 * starts at the server's own drives instead, and exists because otherwise a Sift reached from
 * another device could never be pointed at a folder it had not already been given: somebody had to
 * walk to the computer and open the operating system's dialog. The server decides what either scope
 * may show; this only says which question is being asked.
 *
 * The paths are never assembled here. Every path this sends is one the server handed back (from
 * the listing or from the breadcrumb), so the client never builds a path of its own and there is
 * nothing here for a `..` to be smuggled through. The server proves the path is inside a granted
 * folder regardless, which is the control; this is just not duplicating a job it does better.
 */

import { api, ApiError } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import type { components } from '$lib/api/schema';

/* LIVE: nothing moves it (a folder on the server's disk, read again at every step of the browse; the disk announces nothing) */

export type Entry = components['schemas']['BrowseEntry'];

export type Listing = components['schemas']['BrowseView'];

export interface Chosen {
	path: string;
	/** The folder's own name on disk. Sift uses it as-is: there is nothing to type. */
	name: string;
	/**
	 * Whether Sift may write here. Checked once, the moment the folder is ticked, and never for
	 * every folder in a listing: that would be a write-check per row on every click, and on a
	 * network share, the one place it is slow, the browse walker already refuses to do it. So
	 * the cost is paid only for the handful of folders somebody actually picks.
	 */
	writable: boolean;
	/**
	 * Whether that check actually got an answer.
	 *
	 * False means the request failed, not that the folder is read-only, and the two must not be
	 * drawn the same way: a failed check stating, as a fact, that the folder had been handed over
	 * read-only would tell somebody with full write access that Sift could not change anything in
	 * it, with a greyed switch and no way to argue.
	 *
	 * Read-only is still the SAFE answer to an unknown, so the switch stays off either way. What
	 * changes is what the screen claims: a fact when it knows one, and that it could not tell
	 * otherwise.
	 */
	checked: boolean;
}

/** Which set of folders is being walked. See the note at the top of this file. */
export type Scope = 'granted' | 'machine';

export class Picker {
	/* Held rather than passed, because it has to survive a walk: every step down, every step back up
	   and the write-check on a tick all ask the same question, and one of them forgetting would be a
	   folder that lists on the way in and is refused on the way out. */
	scope = $state<Scope>('granted');
	entries = $state<Entry[]>([]);
	breadcrumb = $state<Entry[]>([]);
	/** Where the picker is standing. The empty string until the first listing arrives. */
	path = $state('');
	/**
	 * How many files are in the folder being looked at.
	 *
	 * The picker lists folders and never files, which is deliberate: it is not a way to read
	 * somebody's filenames. The cost of that would be a folder full of media drawing as an empty
	 * box, so a person standing in their own library would be looking at a screen that appeared to
	 * say it was empty. The number says there is something here without saying what.
	 */
	fileCount = $state(0);
	/**
	 * Nobody has handed Sift a folder at all, so there is nothing to pick.
	 *
	 * A fact about what Sift can see RIGHT NOW, not about how this install was set up. The two look
	 * the same on a fresh install and come apart the moment a disk goes away, so a screen deciding
	 * whether somebody is a new user must not read this alone: ask whether any folder was ever
	 * added. Trusting this one would draw the fresh-install wizard over a library whose disk is
	 * merely away.
	 */
	nothingGranted = $state(false);
	/** Whether Sift could change files in the folder being looked at. */
	writable = $state(false);
	/** Told apart from `writable`, because a read-only folder and a folder Sift has no permission
	 * in need different sentences to explain them. */
	readOnlyMount = $state(false);
	loading = $state(true);
	failed = $state<string | null>(null);

	/** The folders ticked to be added, gathered as the picker is walked, so folders in different
	 * places can be chosen together, not one per trip through the wizard. Each carries its own
	 * writability and its own managed choice. */
	chosen = $state<Chosen[]>([]);

	/** Whether there is anywhere to go back to. False only at the list of granted folders, which is
	 * the top: it is not a folder and has no parent. */
	get canGoUp(): boolean {
		return this.breadcrumb.length > 0;
	}

	/** What is selected: wherever the picker is standing. Clicking a folder both enters and picks
	 * it, which is one gesture instead of the two that "open" and "choose" would need. */
	get selected(): Entry | null {
		return this.breadcrumb.at(-1) ?? null;
	}

	/* Standing on the LIST of folders Sift was given, rather than inside one of them.
	 *
	 * There is nothing to pick here, because this level is not a folder: it is the set of folders
	 * somebody handed over. Each of them individually can be chosen: a person who granted
	 * `D:\\Media` and wants exactly that indexed should not have to go a level deeper.
	 */
	get atTopLevel(): boolean {
		return this.breadcrumb.length === 0;
	}

	async open(path?: string): Promise<void> {
		this.loading = true;
		try {
			/* The address and what filters it, kept apart. Written into the path by hand it
			   would be a call the client cannot check, and it would encode the value itself
			   wrongly: a folder name with a `#` in it would make a request for a different
			   folder. */
			const listing = await api.get<Listing>('/library/browse', {
				query: path ? { path, scope: this.scope } : { scope: this.scope }
			});
			this.entries = listing.entries;
			this.breadcrumb = listing.breadcrumb;
			this.path = listing.path;
			this.fileCount = listing.file_count ?? 0;
			this.nothingGranted = listing.nothing_granted;
			this.writable = listing.writable;
			this.readOnlyMount = listing.read_only_mount;
			this.failed = null;
		} catch (error) {
			// Left where it was rather than emptied. Somebody four folders deep whose next click
			// failed still has the four folders and the way back out; clearing them would drop them
			// at the top with no explanation of why.
			this.failed = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
		} finally {
			this.loading = false;
		}
	}

	/** Back one level. From inside the outermost granted folder that means the LIST of them, which is
	 * asked for by sending no path at all, so there is a case here rather than only a parent crumb.
	 */
	async up(): Promise<void> {
		// Already at the list of granted folders: there is nothing above it, so asking would be one
		// pointless request that also redraws the screen somebody is already looking at.
		if (this.atTopLevel) return;
		if (this.breadcrumb.length === 1) {
			await this.open();
			return;
		}
		const parent = this.breadcrumb.at(-2);
		if (parent) await this.open(parent.path);
	}

	/**
	 * Switch which set is being walked, and go back to the top of it.
	 *
	 * Back to the top on purpose: the two sets do not overlap in any way a breadcrumb could survive,
	 * so keeping the old one would leave a trail of folders that the new scope may refuse to list.
	 */
	async look(scope: Scope): Promise<void> {
		if (this.scope === scope) return;
		this.scope = scope;
		await this.open();
	}

	/** Whether this folder is one of the ticked ones. */
	isChosen(path: string): boolean {
		return this.chosen.some((folder) => folder.path === path);
	}

	/**
	 * Tick a folder, or untick it if it is already ticked.
	 *
	 * Ticking asks the browse endpoint about this one folder, only to learn whether Sift may write
	 * in it, so the screen beside it in the list knows whether it has a fact to state. That is
	 * the whole cost of a write-check on a network share, paid once per folder somebody picks
	 * rather than once per row every time the list is drawn.
	 *
	 * ## The tick lands FIRST, and the answer fills it in afterwards
	 *
	 * Adding the row only once the write-check came back would leave the box somebody pressed empty
	 * for as long as the request took. On the one kind of storage this check is slow on (a
	 * network share, which is exactly what this question is asked about), that is a control that
	 * does nothing when pressed, and the thing a person does next is press it again, which means
	 * untick.
	 *
	 * So the folder is ticked in the same breath as the press, and `checked: false` says the write
	 * question has not been answered yet. That is the state this type already had a name for, and
	 * the screen already draws nothing for it: no claim is made about the folder until there is one
	 * to make. Nothing is lost and the press is honoured immediately.
	 */
	async choose(entry: Entry): Promise<void> {
		if (this.isChosen(entry.path)) {
			this.unchoose(entry.path);
			return;
		}
		// Unanswered, not read-only. See `Chosen.checked`. Read-only is the safe assumption while
		// the question is out, and the screen says nothing at all until it has an answer.
		this.chosen = [
			...this.chosen,
			{ path: entry.path, name: entry.name, writable: false, checked: false }
		];
		let writable = false;
		let checked = true;
		try {
			const listing = await api.get<Listing>('/library/browse', {
				query: { path: entry.path, scope: this.scope }
			});
			writable = listing.writable;
		} catch {
			// A folder that will not even list is one this cannot vouch for as writable; the pick
			// stands, read-only, rather than being swallowed. Recorded as UNANSWERED rather than as
			// a no, so the screen does not state a fact it does not have.
			writable = false;
			checked = false;
		}
		// It may have been unticked while the request was out, in which case the answer is about a
		// folder nobody is adding any more. Untick-then-retick leaves a second check in flight
		// asking the same question about the same path, so whichever lands last wins and both say
		// the same thing.
		if (!this.isChosen(entry.path)) return;
		this.chosen = this.chosen.map((folder) =>
			folder.path === entry.path ? { ...folder, writable, checked } : folder
		);
	}

	/** Untick a folder, from the list or from the picker. */
	unchoose(path: string): void {
		this.chosen = this.chosen.filter((folder) => folder.path !== path);
	}
}
