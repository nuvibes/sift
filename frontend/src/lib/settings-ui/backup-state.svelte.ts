/* Backup, as the settings screen asks the server. */

import { API_PREFIX, ApiError, api, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { onRecord } from '$lib/shell/when';
import { toasts } from '$lib/shell/toasts.svelte';
import type { components } from '$lib/api/schema';

/* LIVE: followed by lib/settings-ui/Backup.svelte (load and loadContents on the settings bell) */

export type ScheduleView = components['schemas']['ScheduleView'];

/** The server's word for a save running (`BACKING_UP` in sift/slices/backup/service.py). */
export const SAVING = 'backup';

/** A backup no rule deletes: its name carries no library's mark, or it was saved by hand. */
export type UnmarkedBackup = components['schemas']['UnmarkedBackupView'];

/** The list, and whether a Delete moves a file to a Recycle Bin or deletes it permanently. */
type UnmarkedBackups = components['schemas']['UnmarkedBackupsView'];

/* The list's address, and one backup's: its name is part of the path, so that one is built. */
const UNMARKED = '/backup/unmarked';
const unmarkedOne = (name: string) => `/backup/unmarked/${encodeURIComponent(name)}` as ApiPath;

/** What a new library keeps its automatic backups for, in days, before the server has said. */
const DEFAULT_KEEP_DAYS = 7;

export type RestoreResult = components['schemas']['RestoreResult'];

/** The backup Save a backup wrote: its folder and whole path on the computer running Sift. */
type SavedBackup = components['schemas']['SavedBackupView'];

/** The address a copy of one listed backup is handed out at. */
export const copyAddress = (name: string) =>
	`${API_PREFIX}/backup/saved/${encodeURIComponent(name)}`;

export type ContentsView = components['schemas']['ContentsView'];
export type ContentPart = components['schemas']['ContentPart'];

/* The server's sentences here are written for the person, so the detail is shown as it is. */
function refusal(error: unknown): string {
	return error instanceof ApiError ? (error.detail ?? error.message) : UNREACHABLE;
}

export class Backup {
	keep = $state(7);
	/** How many days an automatic backup is kept, beside the count; zero is never. */
	keepDays = $state(DEFAULT_KEEP_DAYS);
	folder = $state('');
	/** The backups no rule deletes, newest first, and what a Delete of one does. */
	unmarked = $state<UnmarkedBackup[]>([]);
	recycleBin = $state(false);
	besideSiftData = $state(true);
	/** What the next backup holds beside the database, with sizes. */
	parts = $state<ContentPart[]>([]);

	loaded = $state(false);
	loading = $state(false);
	busy = $state(false);
	/** Save a backup is running: its press turns its arc while the rest of the pane waits. */
	exporting = $state(false);
	/** Whole-library work running anywhere, by the server's word, or null. */
	working = $state<string | null>(null);
	problem = $state<string | null>(null);
	/** What just went right, so a click that produces a file somewhere else is not silent. */
	done = $state<string | null>(null);
	/** The backup the last press of Save a backup wrote, while the pane says where it went. */
	saved = $state<SavedBackup | null>(null);

	/** The pane opened: read it all again; the last visit's notes go unless a save runs. */
	arrive(): void {
		this.done = null;
		if (!this.exporting) this.saved = null;
		void this.load();
		void this.loadContents();
		void this.loadUnmarked();
	}

	async load(): Promise<void> {
		this.loading = true;
		this.problem = null;
		try {
			this.apply(await api.get<ScheduleView>('/backup/schedule'));
			this.loaded = true;
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.loading = false;
		}
	}

	/** What the next backup would hold beside the database. Read again after the switch moves. */
	async loadContents(): Promise<void> {
		try {
			this.parts = (await api.get<ContentsView>('/backup/contents')).parts;
		} catch {
			/* A courtesy; the buttons work without it. */
		}
	}

	/** The backups whose names carry no library's mark; read again after a save. */
	async loadUnmarked(): Promise<void> {
		try {
			this.applyUnmarked(await api.get<UnmarkedBackups>(UNMARKED));
		} catch {
			/* A courtesy; the pane works without it. */
		}
	}

	/** Delete one of them by the name the list showed. Only ever called from its confirm. */
	async deleteUnmarked(name: string): Promise<void> {
		this.busy = true;
		this.problem = null;
		this.done = null;
		try {
			const binned = this.recycleBin;
			this.applyUnmarked(await api.del<UnmarkedBackups>(unmarkedOne(name)));
			this.done = binned ? `${name} is in the Recycle Bin.` : `${name} is deleted.`;
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.busy = false;
		}
	}

	private applyUnmarked(view: UnmarkedBackups): void {
		this.unmarked = view.backups;
		this.recycleBin = view.recycle_bin;
	}

	private apply(view: ScheduleView): void {
		this.keep = view.keep;
		this.keepDays = view.keep_days ?? DEFAULT_KEEP_DAYS;
		this.folder = view.folder;
		this.besideSiftData = view.beside_sift_data;
		this.working = view.working ?? null;
	}

	/** Save a backup into the backup folder; a toast says where, since it outlives the pane. */
	async exportNow(): Promise<void> {
		this.busy = true;
		this.exporting = true;
		this.problem = null;
		this.done = null;
		this.saved = null;
		try {
			this.saved = await api.post<SavedBackup>('/backup/export');
			toasts.show(`Saved a backup in ${this.saved.folder}`);
			await this.loadUnmarked();
		} catch (error) {
			toasts.show(refusal(error), { tone: 'error' });
		} finally {
			this.busy = false;
			this.exporting = false;
		}
	}

	/** Hand a copy of one listed backup to this device's own downloads, by its name. */
	copyOf(name: string): void {
		const anchor = document.createElement('a');
		anchor.href = copyAddress(name);
		anchor.download = name;
		anchor.click();
	}

	/** Change one rule and save all three, the moment it changes. */
	saveRules(change: { keep?: number; keepDays?: number; folder?: string }): Promise<void> {
		if (change.keep !== undefined) this.keep = change.keep;
		if (change.keepDays !== undefined) this.keepDays = change.keepDays;
		if (change.folder !== undefined) this.folder = change.folder;
		return this.saveSchedule();
	}

	#saves: Promise<void> = Promise.resolve();
	#waiting = 0;

	/** Save the three rules in order, each with the values of its own moment; the server checks the
	 * folder first. How often and the time of day are left out, so Tasks keeps them. */
	saveSchedule(): Promise<void> {
		const body = { keep: this.keep, keep_days: this.keepDays, folder: this.folder };
		this.#waiting += 1;
		this.#saves = this.#saves.then(() => this.#put(body));
		return this.#saves;
	}

	async #put(body: { keep: number; keep_days: number; folder: string }): Promise<void> {
		try {
			const stored = await api.put<ScheduleView>('/backup/schedule', { body });
			// A later change is already on its way; its answer is the one to show.
			if (this.#waiting === 1) this.apply(stored);
			await this.loadUnmarked();
		} catch (error) {
			toasts.show(refusal(error), { tone: 'error' });
			// Back to what is stored, so the rows show the rules that still apply.
			try {
				this.apply(await api.get<ScheduleView>('/backup/schedule'));
			} catch {
				/* The toast has said it; the rows keep the refused values until the next read. */
			}
		} finally {
			this.#waiting -= 1;
		}
	}

	/** Replace the library's records with a backup file. Only ever called from the confirm. */
	async restore(file: File): Promise<void> {
		this.busy = true;
		this.problem = null;
		this.done = null;
		try {
			const form = new FormData();
			form.append('file', file);
			const result = await api.post<RestoreResult>('/backup/restore', { body: form });
			this.done =
				`Restored from a backup taken ${onRecord(result.created_at, { inline: true })}. ` +
				'Re-scan your folders to bring back thumbnails and previews.';
			await this.load();
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.busy = false;
		}
	}
}

/* One per window, so a save or a restore pressed on the pane is still drawn when somebody comes back. */
export const backup = new Backup();

/* --- duplicating this library ------------------------------------------------------------ */

export type DuplicatePlan = components['schemas']['DuplicatePlan'];
export type DuplicateStarted = components['schemas']['DuplicateStarted'];

/** The task that makes a duplicate. `LIBRARY_DUPLICATE` in sift/slices/backup/libraries.py. */
export const DUPLICATE_JOB = 'library_duplicate';

/* What the Duplicate form says before anything is pressed. */
export function readDuplicatePlan(): Promise<DuplicatePlan> {
	return api.get<DuplicatePlan>('/libraries/duplicate');
}

/* Queue the copy. The server refuses a taken name, no room, and other work on the library with a
   sentence of its own, which the form shows as it is. */
export function startDuplicate(name: string, pictures: boolean): Promise<DuplicateStarted> {
	return api.post<DuplicateStarted>('/libraries/duplicate', { body: { name, pictures } });
}
