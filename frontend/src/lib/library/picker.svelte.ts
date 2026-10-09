/* Walking the folders Sift has been given, so one can be chosen by clicking it. */

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
	writable: boolean;
	checked: boolean;
}

/** Which set of folders is being walked. See the note at the top of this file. */
export type Scope = 'granted' | 'machine';

export class Picker {
	/* Held rather than passed, because every step down, back up and the write-check ask it. */
	scope = $state<Scope>('granted');
	entries = $state<Entry[]>([]);
	breadcrumb = $state<Entry[]>([]);
	/** Where the picker is standing. The empty string until the first listing arrives. */
	path = $state('');
	fileCount = $state(0);
	/** Nobody has handed Sift a folder at all, so there is nothing to pick. */
	nothingGranted = $state(false);
	/** Whether Sift could change files in the folder being looked at. */
	writable = $state(false);
	/** Told apart from `writable`: a read-only folder and one with no permission read
	   differently. */
	readOnlyMount = $state(false);
	loading = $state(true);
	failed = $state<string | null>(null);

	/** The folders ticked to be added, so folders in different places can be chosen together. */
	chosen = $state<Chosen[]>([]);

	/** Whether there is anywhere to go back to. False only at the list of granted folders. */
	get canGoUp(): boolean {
		return this.breadcrumb.length > 0;
	}

	/** What is selected: wherever the picker is standing. */
	get selected(): Entry | null {
		return this.breadcrumb.at(-1) ?? null;
	}

	/* Standing on the LIST of folders Sift was given, rather than inside one of them. */
	get atTopLevel(): boolean {
		return this.breadcrumb.length === 0;
	}

	async open(path?: string): Promise<void> {
		this.loading = true;
		try {
			/* The address and what filters it, kept apart. */
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
			// Left where it was rather than emptied.
			this.failed = error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
		} finally {
			this.loading = false;
		}
	}

	/** Back one level: from the outermost granted folder that means the LIST, asked with no path. */
	async up(): Promise<void> {
		// Already at the list of granted folders: there is nothing above it.
		if (this.atTopLevel) return;
		if (this.breadcrumb.length === 1) {
			await this.open();
			return;
		}
		const parent = this.breadcrumb.at(-2);
		if (parent) await this.open(parent.path);
	}

	/** Switch which set is being walked, and go back to the top of it. */
	async look(scope: Scope): Promise<void> {
		if (this.scope === scope) return;
		this.scope = scope;
		await this.open();
	}

	/** Whether this folder is one of the ticked ones. */
	isChosen(path: string): boolean {
		return this.chosen.some((folder) => folder.path === path);
	}

	/** Tick a folder, or untick it if it is already ticked. */
	async choose(entry: Entry): Promise<void> {
		if (this.isChosen(entry.path)) {
			this.unchoose(entry.path);
			return;
		}
		// Unanswered, not read-only. See `Chosen.checked`.
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
			// A folder that will not list cannot be vouched for as writable; the pick stands,
			// read-only.
			writable = false;
			checked = false;
		}
		// It may have been unticked while the request was out.
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
