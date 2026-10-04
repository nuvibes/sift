/* Backup, as the settings screen asks the server about it.
 *
 * Three things a person can do here, and they are not equally reversible. Taking a backup costs
 * nothing. Changing where they go costs nothing. Restoring one replaces the library's records with
 * whatever is in the file, and there is no undoing that from this screen, so the screen asks
 * first, and this module never restores except when told to by that answer.
 *
 * Save a backup writes into the backup folder on the computer running Sift and answers where it
 * went. A copy for this device is asked for afterwards by its name (`copyOf`), straight to the
 * browser's own download, never through the shared client: that helper parses a JSON body, and a
 * backup is a database.
 */

import { API_PREFIX, ApiError, api, type ApiPath } from '$lib/api/client';
import { UNREACHABLE } from '$lib/shell/unreachable';
import { onRecord } from '$lib/shell/when';
import { keptSaid } from './Backup.search';
import type { components } from '$lib/api/schema';

/* LIVE: followed by lib/settings-ui/Backup.svelte (load and loadContents on the settings bell) */

export type ScheduleView = components['schemas']['ScheduleView'];

/**
 * A backup in the backup folder no rule ever deletes: one whose name carries no library's mark, or
 * one this library saved by hand (`saved`).
 */
export type UnmarkedBackup = components['schemas']['UnmarkedBackupView'];

/** The list, and whether a Delete moves a file to a Recycle Bin or deletes it permanently. */
type UnmarkedBackups = components['schemas']['UnmarkedBackupsView'];

/* The list's address, and one backup's: its name is part of the path, so that one is built. */
const UNMARKED = '/backup/unmarked';
const unmarkedOne = (name: string) => `/backup/unmarked/${encodeURIComponent(name)}` as ApiPath;

/** What a new library keeps its automatic backups for, in days, before the server has said. */
const DEFAULT_KEEP_DAYS = 7;

export type RestoreResult = components['schemas']['RestoreResult'];

/**
 * The backup Save a backup wrote, and where: its folder as the computer running Sift names it, and
 * its whole path there, for the Sift app on that computer to show it in its folder.
 */
type SavedBackup = components['schemas']['SavedBackupView'];

/** The address a copy of one listed backup is handed out at. */
export const copyAddress = (name: string) =>
	`${API_PREFIX}/backup/saved/${encodeURIComponent(name)}`;

export type ContentsView = components['schemas']['ContentsView'];
export type ContentPart = components['schemas']['ContentPart'];

/* The endpoints behind this screen are written for the person reading them: "That folder is not
   one Sift can reach", "This backup was made by a newer version of Sift". Those are the whole
   answer, so the detail is shown rather than the flat one-liner. */
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
	problem = $state<string | null>(null);
	/** What just went right, so a click that produces a file somewhere else is not silent. */
	done = $state<string | null>(null);
	/** The backup the last press of Save a backup wrote, while the pane says where it went. */
	saved = $state<SavedBackup | null>(null);

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
			/* The sentence about the file's contents is a courtesy; the buttons work without it. */
		}
	}

	/** The backups in the folder whose names carry no library's mark. Read again after a save,
	 * which may have moved the folder. */
	async loadUnmarked(): Promise<void> {
		try {
			this.applyUnmarked(await api.get<UnmarkedBackups>(UNMARKED));
		} catch {
			/* A list of files nobody's rule touches is a courtesy; the pane works without it. */
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
	}

	/** Take a backup now, into the backup folder, and keep where it went for the pane to say. */
	async exportNow(): Promise<void> {
		this.busy = true;
		this.problem = null;
		this.done = null;
		this.saved = null;
		try {
			this.saved = await api.post<SavedBackup>('/backup/export');
			/* The list of backups no rule takes holds this one now. */
			await this.loadUnmarked();
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.busy = false;
		}
	}

	/** Hand a copy of one listed backup to this device's own downloads, by its name. */
	copyOf(name: string): void {
		const anchor = document.createElement('a');
		anchor.href = copyAddress(name);
		anchor.download = name;
		anchor.click();
	}

	/** Save where the backups go and how many to keep. The server checks the folder before storing
	 * anything. How often and the time of day are not sent: they are the task's own rows on Tasks,
	 * and a body without them keeps what is stored, so this pane never puts back what it loaded. */
	async saveSchedule(): Promise<void> {
		this.busy = true;
		this.problem = null;
		this.done = null;
		try {
			this.apply(
				await api.put<ScheduleView>('/backup/schedule', {
					body: { keep: this.keep, keep_days: this.keepDays, folder: this.folder }
				})
			);
			/* Only what this screen saved. The cadence is set on Tasks, where the task's When decides
			   whether it starts on its own at all, so a sentence here that promised a daily backup
			   could promise one the When never runs. */
			this.done = `Saved. ${keptSaid(this.keep, this.keepDays)}`;
			await this.loadUnmarked();
		} catch (error) {
			this.problem = refusal(error);
		} finally {
			this.busy = false;
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

/* --- duplicating this library ------------------------------------------------------------ */

export type DuplicatePlan = components['schemas']['DuplicatePlan'];
export type DuplicateStarted = components['schemas']['DuplicateStarted'];

/** The task that makes a duplicate. `LIBRARY_DUPLICATE` in sift/slices/backup/libraries.py. */
export const DUPLICATE_JOB = 'library_duplicate';

/* What the Duplicate form says before anything is pressed. Asked when the form opens rather than
   with the pane: the server walks the cache to measure the pictures. */
export function readDuplicatePlan(): Promise<DuplicatePlan> {
	return api.get<DuplicatePlan>('/libraries/duplicate');
}

/* Queue the copy. The server refuses a taken name, no room, and other work on the library with
   a sentence of its own, which the form shows as it is. */
export function startDuplicate(name: string, pictures: boolean): Promise<DuplicateStarted> {
	return api.post<DuplicateStarted>('/libraries/duplicate', { body: { name, pictures } });
}
