/* The way back after Sift has stopped: what went wrong and what to do about it, a start with the
 * optional features held off, the log archive, which needs no backend, and choosing the library's
 * place again only when the library isn't where this copy was told. */

import { app, dialog, shell } from 'electron';

import { startFacts } from './facts';
import { refuseLocation } from './firstrun';
import { find, holdsLibrary } from './libraries';
import { log } from './log';
import { bundleLogs, type Bundled } from './logbundle';
import { bundlePaths } from './paths';
import { locations, type DesktopSettings } from './settings';

const HOLD = 'Start without them';
const LOG = 'Download log';
const AGAIN = 'Start again';
const CHOOSE = 'Choose again';
const QUIT = 'Quit';

/** Windows' statuses for a program that couldn't start: `could_not_start` in the Python tree, and
 *  0xC0000139, a library too old for what asks it. */
export const NEVER_STARTED = new Set([0xc0000043, 0xc0000135, 0xc0000139, 0xc000007b, 0xc0000142]);
/** Windows' statuses for a program stopped by a fault inside a library it runs. */
export const FAULTED = new Set([0xc0000005, 0xc0000409, 0xc000001d]);

export const ISSUES = 'github.com/nuvibes/sift/issues';

/** What an exit code usually means and what to do, in a sentence; empty for any other code. */
export function whatItMeans(code: number | null): string {
	const status = code === null ? null : code >>> 0;
	if (status !== null && NEVER_STARTED.has(status)) {
		return "It couldn't start: a file it needs is in use or missing. Installing Sift again puts its own files back. ";
	}
	if (status !== null && FAULTED.has(status)) {
		return 'It stopped inside one of the libraries it runs on. Starting without face recognition, Smart Search and watermark reading often gets past that. ';
	}
	return '';
}

/** Why the library can't be opened where this copy was told, or null. A library this copy never
 *  opened is new, not missing: its database comes with its first start. */
export async function libraryMissing(settings: DesktopSettings): Promise<string | null> {
	if (settings.mode !== 'standalone' || settings.dataDir === null) return null;
	const where = locations(settings);
	const opened = find(settings.libraries, where.dataDir) !== null;
	if (opened && !holdsLibrary(where.dataDir)) return `There's no Sift library in ${where.dataDir}.`;
	return refuseLocation(where);
}

export interface WayBack {
	/** What went wrong and the ways out, in a system dialog: the window may have nothing loaded. */
	offer(message: string, detail: string, crashed?: boolean, code?: number | null): void;
	/** Whether this launch holds the optional features off. */
	holding(): boolean;
	/** Said once a backend is up, if it is the start without them. */
	started(): void;
	/** The archive under this name in the save folder: its path, or null. */
	archive(name: string): Promise<string | null>;
}

export interface Ways {
	settings: () => DesktopSettings;
	save: (next: DesktopSettings) => void;
	/** Open the library again, or ask what is still unanswered, as after first run. */
	startAgain: () => void;
}

export function wayBack(ways: Ways): WayBack {
	/* The optional features held off from the dialog, for this launch only. */
	let holding = false;
	/* Where the log was saved, said once by the dialog that comes back after it. */
	let saved = '';

	/** The one way the log archive is made, for the dialog and the page alike. */
	async function makeLogArchive(name?: string): Promise<Bundled> {
		const settings = ways.settings();
		const { python } = bundlePaths();
		const made = await bundleLogs({
			python,
			appLogs: app.getPath('userData'),
			libraryLogs: settings.mode === 'standalone' ? locations(settings).dataDir : null,
			facts: startFacts(python, holding),
			into: settings.downloadDir ?? app.getPath('downloads'),
			...(name === undefined ? {} : { name })
		});
		if (made.ok) log.info('shell.log_downloaded', { file: made.file });
		else log.error('shell.log_download_failed', { reason: made.reason });
		return made;
	}

	/** The dialog's Download log, then the dialog again; the file's folder opens only once that is
	 *  answered, so Explorer never comes up over a dialog still waiting. */
	async function downloadLog(again: () => Promise<void>): Promise<void> {
		const made = await makeLogArchive();
		if (made.ok) {
			saved = `\n\nThe log is saved as ${made.file}.`;
			await again();
			shell.showItemInFolder(made.file);
			return;
		}
		dialog.showMessageBoxSync({
			type: 'error',
			title: 'Sift',
			message: "Sift couldn't make the log file",
			detail: made.reason,
			buttons: ['OK'],
			noLink: true
		});
		await again();
	}

	function ask(message: string, said: string, buttons: string[]): string | undefined {
		const detail = `${said}${saved}`;
		saved = '';
		return buttons[
			dialog.showMessageBoxSync({
				type: 'error',
				title: 'Sift has stopped',
				message,
				detail,
				buttons,
				defaultId: 0,
				cancelId: buttons.length - 1,
				noLink: true
			})
		];
	}

	/** The library isn't where this copy was told: plug its drive back in, or choose again. */
	function offerThePlace(missing: string, again: () => Promise<void>): void {
		const settings = ways.settings();
		const chosen = ask(
			`Sift can't open your library at ${locations(settings).dataDir}`,
			`${missing}\n\nIf it's on a drive that isn't plugged in, plug the drive in and press ${AGAIN}. ` +
				`${CHOOSE} asks where your library is. A folder with no library in it starts a new, ` +
				'empty library there, and your library stays where it is, untouched.',
			[AGAIN, CHOOSE, LOG, QUIT]
		);
		if (chosen === CHOOSE) {
			ways.save({ ...settings, dataDir: null, cacheDir: null });
			ways.startAgain();
		} else if (chosen === AGAIN) ways.startAgain();
		else if (chosen === LOG) void downloadLog(again);
		else app.quit();
	}

	/** Sift itself stopped: what happened, what the exit code usually means, and what to do. */
	function offerTheFix(
		message: string,
		detail: string,
		crashed: boolean,
		code: number | null
	): void {
		const holdOffered = crashed && !holding;
		const offered = holdOffered
			? "You can start Sift this once with face recognition, Smart Search and watermark reading held off. Your settings don't change. "
			: '';
		const chosen = ask(
			message,
			`${detail}\n\n${whatItMeans(code)}${offered}If it keeps stopping, download the log and add it to an issue at ${ISSUES}.`,
			[...(holdOffered ? [HOLD] : []), LOG, QUIT]
		);
		if (chosen === HOLD) {
			holding = true;
			ways.startAgain();
		} else if (chosen === LOG) void downloadLog(() => offer(message, detail, crashed, code));
		else app.quit();
	}

	/* A reason the shell has in words (the port taken, no Python, a refused library) is said as it
	 * is; only a backend that died is checked for a library that is missing. */
	function offer(
		message: string,
		detail: string,
		crashed = false,
		code: number | null = null
	): Promise<void> {
		log.error('shell.stopped', { message });
		const missing = crashed ? libraryMissing(ways.settings()) : Promise.resolve(null);
		return missing.then((reason) => {
			if (reason === null) offerTheFix(message, detail, crashed, code);
			else offerThePlace(reason, () => offer(message, detail, crashed, code));
		});
	}

	function started(): void {
		if (!holding) return;
		log.info('backend.optional_held', {});
		void dialog.showMessageBox({
			type: 'info',
			title: 'Sift',
			message: 'Sift started without face recognition, Smart Search and watermark reading',
			detail: "They're still on in Settings. The next time you open Sift, it uses them again.",
			buttons: ['OK'],
			noLink: true
		});
	}

	return {
		offer,
		holding: () => holding,
		started,
		archive: async (name) => {
			const made = await makeLogArchive(name);
			return made.ok ? made.file : null;
		}
	};
}
