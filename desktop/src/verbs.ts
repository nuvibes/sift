/* The verbs the page may ask the machine to do, on the main process's side.
 *
 * Each is a named, argument-checked request, never a general capability. EVERY HANDLER CHECKS WHO
 * ASKED, at the moment of the call: the preload is attached only to saved origins, but a window
 * keeps its channel while its page can be navigated away, and a capability must not outlive the page
 * it was granted to.
 */

import {
	app,
	BrowserWindow,
	clipboard,
	dialog,
	ipcMain,
	shell,
	type IpcMainInvokeEvent,
	type NativeImage,
	type Rectangle
} from 'electron';
import { stat } from 'node:fs/promises';
import { hostname, networkInterfaces } from 'node:os';
import { basename, win32 } from 'node:path';

import type { CaptureArea, LibraryList, Settled, SetupStep } from '../../shared/bridge';

import {
	alreadyFetched,
	arrivingAt,
	fetchToTemp,
	localFile,
	reachedHere,
	type Arriving
} from './assets';
import { PORT } from './backend';
import { WINDOW_STAGE } from './opening';
import { describe as describeStorage, move as moveStorage, type StorageReport } from './storage';
import type { DataLocations } from './paths';
import { installed as installedBrowsers, type Browser } from './browsers';
import {
	ADD_LIBRARY,
	APPLY_UPDATE,
	CAPTURE_WINDOW,
	CHOOSE_FILE,
	CHOOSE_FOLDER,
	CHOOSE_LIBRARY,
	CHOOSE_MODE,
	SETUP_BACK,
	SETUP_PROGRESS,
	SUGGESTED_LIBRARY,
	DRAG_PROGRESS,
	FORGET_LIBRARY,
	GET_FIREWALL,
	GET_KEEP_RUNNING,
	GET_SHARING,
	GET_START_WITH_WINDOWS,
	LAST_SERVER,
	CONNECT_STATE,
	FORGET_SERVER,
	FORGET_MODE,
	RESTART_APP,
	GET_DOWNLOAD_DIR,
	GET_STORAGE,
	LIST_LIBRARIES,
	MOVE_STORAGE,
	OPEN_LIBRARY,
	STORAGE_PROGRESS,
	LIST_BROWSERS,
	LOCAL_HARDWARE,
	MACHINE_NAME,
	OPEN_FIREWALL,
	READ_CLIPBOARD,
	SAVE_SERVER,
	SET_BROWSER,
	SET_DOWNLOAD_DIR,
	SET_KEEP_RUNNING,
	SET_START_WITH_WINDOWS,
	SET_TITLE_BAR,
	SHOW_IN_FOLDER,
	SET_SHARING,
	SAVE_LOG_ARCHIVE,
	SHELL_LOG,
	SHELL_LOG_DETAIL,
	SHELL_VERSION,
	START_DRAG,
	BRIDGE_VERBS
} from './channels';
import * as channels from './channels';
import { firewallState, openFirewall, type FirewallReport, type FirewallScope } from './firewall';
import { takeGesture } from './gesture';
import { linesAsked, log, setDetail, setHidePersonal, tail as tailShellLog } from './log';
import { isHomeNetworkAddress, type Reach } from './origins';
import { machineName, thisMachine, type LocalMachine } from './machine';
import type { StartWithWindows } from './startup';
import { dragAddonFile } from './paths';
import { applyUpdate, feedAddress, type UpdateOutcome } from './update';

export {
	ADD_LIBRARY,
	APPLY_UPDATE,
	CAPTURE_WINDOW,
	CHOOSE_FILE,
	CHOOSE_FOLDER,
	CHOOSE_LIBRARY,
	CHOOSE_MODE,
	SETUP_BACK,
	SETUP_PROGRESS,
	SUGGESTED_LIBRARY,
	DRAG_PROGRESS,
	FORGET_LIBRARY,
	GET_FIREWALL,
	GET_KEEP_RUNNING,
	GET_SHARING,
	GET_START_WITH_WINDOWS,
	LAST_SERVER,
	CONNECT_STATE,
	FORGET_SERVER,
	LIST_LIBRARIES,
	LOCAL_HARDWARE,
	MACHINE_NAME,
	OPEN_FIREWALL,
	OPEN_LIBRARY,
	READ_CLIPBOARD,
	SAVE_SERVER,
	SET_KEEP_RUNNING,
	SET_SHARING,
	SET_START_WITH_WINDOWS,
	SET_TITLE_BAR,
	SAVE_LOG_ARCHIVE,
	SHELL_LOG,
	SHELL_LOG_DETAIL,
	SHELL_VERSION,
	START_DRAG
};

/** What the folder screen is offered. `existing` says a database is already there. */
export interface Suggested {
	path: string;
	existing: boolean;
}

export interface Setup {
	/** Save which way this copy runs, and go wherever that leads. */
	mode(chosen: 'standalone' | 'client'): Promise<Settled>;
	/** The folder offered first: a library an earlier installation left, or the default. */
	suggested(): Promise<Suggested>;
	/** Settle the library folder: the suggested one, or one chosen from the machine's own dialog.
	 *  `told` hears each step as it starts, for the screen to say. */
	library(pick: boolean, told: (step: SetupStep) => void): Promise<Settled>;
	/** Unsay the mode, so the question before it is asked again: the screen shown is derived from
	 *  which answers exist, so going back removes an answer, by one rule both ways. */
	back(): Promise<Settled>;
}

/**
 * The libraries this copy has opened, and switching to one of them: handed in, since a switch
 * (stop, read, offer an upgrade, write, start) is the backend's supervisor's to do.
 */
export interface LibraryBook {
	/** Every library this copy has opened, newest first, and which of them is open now. */
	list(): LibraryList;
	/** Open one already on that list. The shell decides what a question about its schema costs. */
	open(dataDir: string): Promise<Settled>;
	/** Open one somebody points at in the machine's own picker, and add it to the list. */
	add(): Promise<Settled>;
	/** Take one off the list. The library itself is untouched; this forgets the shortcut. */
	forget(dataDir: string): LibraryList;
}

export type { LibraryList };

/** One address the shell has been told, as the connect screen lists it. */
export interface SavedServer {
	label: string;
	origin: string;
}

/** Where the connect screen stands, in one answer. */
export interface ConnectState {
	/** The address last tried, so a correction starts from it. */
	last: string | null;
	/** Why the last address did not answer, when that is why the screen is up. Null otherwise. */
	problem: string | null;
	/** Every address saved, oldest first. */
	servers: SavedServer[];
}

/**
 * What the connect screen needs from the shell: try an address, remember the last one, say why it
 * did not answer (so a dead server is a screen, not a crash), and forget one that has gone.
 */
export interface ServerBook {
	remember(origin: string): Promise<string | null>;
	last(): string | null;
	problem(): string | null;
	saved(): SavedServer[];
	forget(origin: string): SavedServer[];
}

/** How far a page at this URL may reach: this machine's own Sift, a saved server, or not at all. */
export type ReachCheck = (url: string) => Reach | null;

/**
 * The verbs a page served by ANOTHER computer may use. Everything else is the local backend's alone.
 *
 * A saved server, or anything that can alter a plain-http page on its way here, gets what a window
 * onto a library elsewhere needs and nothing that acts on this machine's own Sift (sharing, the
 * firewall, storage, the library list, the shell's log, first run, the pickers). The clipboard, a
 * screenshot and this copy's own update (no argument: only a release its feed signs) only just
 * after a real press (`pressed`), a drag of only what the server streams. `FORGET_MODE` is how a
 * client returns to the first question. How THIS window behaves (the browser, close-to-tray, start
 * with Windows) is stored by this shell and is all visible and reversible. `MACHINE_NAME` is read,
 * never set, so a phone can tell two desks apart.
 */
export const REMOTE_VERBS: ReadonlySet<string> = new Set([
	START_DRAG,
	DRAG_PROGRESS,
	READ_CLIPBOARD,
	CAPTURE_WINDOW,
	LAST_SERVER,
	CONNECT_STATE,
	SHELL_VERSION,
	LOCAL_HARDWARE,
	MACHINE_NAME,
	LIST_BROWSERS,
	SET_BROWSER,
	GET_DOWNLOAD_DIR,
	SET_DOWNLOAD_DIR,
	SET_TITLE_BAR,
	GET_KEEP_RUNNING,
	SET_KEEP_RUNNING,
	GET_START_WITH_WINDOWS,
	SET_START_WITH_WINDOWS,
	FORGET_MODE,
	SAVE_LOG_ARCHIVE,
	APPLY_UPDATE,
	WINDOW_STAGE
]);

/** Every channel the page can be offered, read off `channels.ts` so a new one cannot be missed. */
const EVERY_VERB: readonly string[] = (Object.values(channels) as string[])
	.filter((name) => name !== channels.BRIDGE_VERBS)
	.concat(WINDOW_STAGE);

/** The channels a page with this reach may use. The preload builds `window.sift` from this list. */
export function verbsFor(reach: Reach | null): string[] {
	if (reach === null) return [];
	return reach === 'local' ? [...EVERY_VERB] : EVERY_VERB.filter((one) => REMOTE_VERBS.has(one));
}

/**
 * What the settings screen needs in order to offer this library to the network. `mode` lets the
 * screen say WHY a client cannot share, rather than hiding the control.
 */
export interface Sharing {
	/** On or off, as the setting says. */
	enabled: boolean;
	/** Whether the backend is actually listening on it. Differs from `enabled` only if the change
	 *  could not be made (see `Backend.listenOnNetwork`). */
	live: boolean;
	mode: 'standalone' | 'client' | null;
	/** The address to type on the other computer, e.g. "http://192.168.1.41:5171". Null if unknown. */
	address: string | null;
	/** The port it answers on, so the screen keeps no copy of its own. */
	port: number;
}

/* Windows' own dialog, deliberately: the security property of a grant. A page cannot open, drive,
 * read or pre-fill it, so the folder is one a person physically pointed at, which makes listing it
 * safe. `dontAddToRecent` keeps media folders off the recent list every application can read. */
export async function chooseFolder(parent: BrowserWindow | null): Promise<string | null> {
	const options = {
		title: 'Choose a folder Sift may look in',
		buttonLabel: 'Let Sift see this folder',
		properties: ['openDirectory', 'dontAddToRecent'] as const
	};
	const picked =
		parent === null
			? await dialog.showOpenDialog({
					...options,
					properties: [...options.properties]
				})
			: await dialog.showOpenDialog(parent, {
					...options,
					properties: [...options.properties]
				});
	const chosen = picked.filePaths[0];
	return picked.canceled || chosen === undefined ? null : chosen;
}

/**
 * The operating system's own file dialog, for one database file (Migrate from Stash's), by the
 * folder dialog's rules. Choosing grants nothing: the server reads it only inside a folder Sift has
 * already been given.
 */
export async function chooseFile(parent: BrowserWindow | null): Promise<string | null> {
	const options = {
		title: "Choose Stash's database file",
		buttonLabel: 'Choose this file',
		filters: [
			{ name: 'Databases', extensions: ['sqlite', 'sqlite3', 'db'] },
			{ name: 'All files', extensions: ['*'] }
		],
		properties: ['openFile', 'dontAddToRecent'] as const
	};
	const picked =
		parent === null
			? await dialog.showOpenDialog({
					...options,
					properties: [...options.properties]
				})
			: await dialog.showOpenDialog(parent, {
					...options,
					properties: [...options.properties]
				});
	const chosen = picked.filePaths[0];
	return picked.canceled || chosen === undefined ? null : chosen;
}

/* --- Taking a file out ------------------------------------------------------------------- */

/**
 * What happened when the page asked to drag something out.
 *
 * OLE's drag loop ends when the button comes up, so a client-mode file on another machine cannot be
 * fetched inside the gesture; Windows' virtual file (a descriptor plus a stream read after the drop,
 * following the download as it arrives) makes it one gesture. See desktop/native/drag/src/drag_win.cc.
 */
export type DragOutcome = 'dragged' | 'unavailable';

/** The end of the shell's log, in the shape of the server's own log route, so one screen draws both. */
export interface ShellLog {
	lines: string[];
	path: string;
	size: number;
	present: boolean;
}

/** Where a file saved out of Sift lands. */
export interface DownloadFolder {
	/** The folder itself, always a real path: the machine's own where none was chosen. */
	path: string;
	/** Whether that path was CHOSEN, rather than being whatever this machine calls Downloads: only
	 * the unchosen one follows the machine if that folder moves. */
	chosen: boolean;
}

/** What the page is told about where links go: the list, and which one is chosen. */
export interface BrowserChoice {
	/** The executable chosen, or null for whatever Windows would have used. */
	chosen: string | null;
	browsers: Browser[];
}

/** The compiled addon, loaded only where it is used: a binary bound to one Electron version, which
 * at the top would stop the whole main process loading if it were missing. */
function addon(): {
	startDrag(path: string): void;
	startStreamedDrag(partial: string, finished: string, name: string, total: number): void;
} {
	// eslint-disable-next-line @typescript-eslint/no-require-imports
	return require(dragAddonFile());
}

/**
 * Drag a file that is already here. Nothing is read and nothing is copied by us. `how` is for the
 * log: a share is the one that can be slow.
 */
function dragFile(filePath: string, how: 'local' | 'cached' | 'share'): void {
	log.info('drag.started', { how, path: filePath });
	addon().startDrag(filePath);
}

/**
 * Drag a file that is still arriving: the download starts NOT awaited, the drag begins in the same
 * tick while the button is down, and the receiver reads the stream after the drop as it arrives.
 */
function dragArriving(at: Arriving, name: string, total: number | null): void {
	log.info('drag.started', {
		how: 'streamed',
		path: at.finished,
		total_bytes: total
	});
	addon().startStreamedDrag(at.partial, at.finished, name, total ?? -1);
}

/**
 * The frame that asked, or null when it may not ask: EVERY HANDLER GOES THROUGH THIS, at the moment
 * of the call. Is the page Sift at all, and may a page of its reach use this channel? The preload
 * leaving a method off is a convenience; this is the check.
 */
/** Whether a page from another computer asks just after a real press here; a refusal is logged. */
function pressed(event: IpcMainInvokeEvent, reachOf: ReachCheck, what: string): boolean {
	const ok = reachOf(event.senderFrame?.url ?? '') !== 'remote' || takeGesture(event.sender);
	if (!ok) log.warning(`${what}.refused`, { why: 'no-recent-press' });
	return ok;
}

export function askingFrame(event: IpcMainInvokeEvent, reachOf: ReachCheck, channel: string) {
	const frame = event.senderFrame;
	if (frame === null || frame.parent !== null) return null;
	const reach = reachOf(frame.url);
	if (reach === null || (reach === 'remote' && !REMOTE_VERBS.has(channel))) return null;
	return frame;
}

/**
 * Whether a share path a remote server named is one this machine may hand to a drag: a server
 * describes ITS files, so only a `\\server\share\...` path elsewhere, never a local drive, a device
 * path, an administrative share or a share on this machine.
 */
export function isShareFromElsewhere(named: string): boolean {
	const match = /^\\\\([^\\/?.:][^\\/:]*)\\([^\\/]+)\\[^/]+$/.exec(named);
	if (match === null) return false;
	const host = (match[1] as string).toLowerCase();
	const share = match[2] as string;
	if (share.endsWith('$')) return false;
	if (host === 'localhost' || /^127\./.test(host) || host === hostname().toLowerCase())
		return false;
	if (ownAddresses().includes(host)) return false;
	return !named.split('\\').some((part) => part === '..' || part === '.');
}

/* Whether a page's path names a Sift backup as Sift names one, a whole path ending in
 * `sift-backup-...` with a backup's extension, checked before anything is touched. */
const BACKUP_NAME = /^sift-backup-[A-Za-z0-9._+-]+\.(zip|sqlite3)$/;

export function isBackupPath(path: unknown): path is string {
	if (typeof path !== 'string' || path === '') return false;
	if (!win32.isAbsolute(path) && !path.startsWith('/')) return false;
	return BACKUP_NAME.test(basename(path)) && BACKUP_NAME.test(win32.basename(path));
}

/* The origin to ask about a file is the origin of the page ASKING, which reaches its own server
 * over relative `/api` paths, in all three shapes this shell runs in. */
function originOf(url: string): string | null {
	try {
		return new URL(url).origin;
	} catch {
		return null;
	}
}

/** A plain CSS hex colour, and nothing else. See `SET_TITLE_BAR`. */
function isHexColor(value: unknown): value is string {
	return typeof value === 'string' && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value);
}

/** What a move came back with. `reason: null` is the picker closed: not a failure. */
export type MoveOutcome =
	{ ok: true; locations: DataLocations; renamed: boolean } | { ok: false; reason: string | null };

/**
 * Install a release NEWER than this copy, from `feed`: the page's Install button, and the backend's
 * ask from another computer (`shelllink.ts`), by one rule. `beforeLaunch` runs once the installer
 * is verified, with the version it installs, and before it opens.
 */
export async function installNewer(
	feed: string,
	beforeLaunch: (version: string) => Promise<void>
): Promise<UpdateOutcome> {
	/* The version this copy IS; a checkout has none, and is not updated by a release. */
	const running = app.isPackaged ? app.getVersion() : null;
	log.info('update.checking', { running: running ?? 'a checkout' });
	const outcome = await applyUpdate(feed, undefined, { running, beforeLaunch });
	/* The line a report of an update that never arrived needs first; a refused release warns. */
	if (outcome.ok) log.info('update.installing', { version: outcome.version });
	else if (outcome.reason === 'unverified' || outcome.reason === 'failed')
		log.warning('update.not_installed', { reason: outcome.reason });
	else log.info('update.not_installed', { reason: outcome.reason });
	return outcome;
}

/**
 * What the shell hands the verbs, by name, so a value cannot land in the wrong slot. Every field is
 * optional: a test names the one hook it exercises and the rest default to "no shell to ask".
 */
export interface ShellHooks {
	feedUrl?: () => string | null;
	/* Close Sift and open it again, for Restart; null where no shell can relaunch itself. */
	restart?: (() => Promise<void>) | null;
	servers?: ServerBook | null;
	/* Read and written through functions, here and below, since the shell's settings change while
	 * it runs. Null where there is no shell to ask: the tests, mostly. */
	sharing?: {
		read: () => Sharing;
		write: (on: boolean) => Promise<void>;
	} | null;
	/* Where links open. */
	links?: {
		chosen: () => string | null;
		choose: (id: string | null) => void;
	} | null;
	/* Where a saved file lands. */
	downloads?: {
		folder: () => string;
		chosen: () => string | null;
		choose: (dir: string | null) => void;
	} | null;
	/* Sift's OWN two folders (the database and the cache). `move` is the whole job (stop, move,
	 * write, start), which only `main` can do; `forget` only clears which way Sift was set up. */
	storage?: {
		read: () => Promise<StorageReport>;
		move: (target: string, report: (copied: number, total: number) => void) => Promise<MoveOutcome>;
		forget: () => void;
	} | null;
	/* The port the backend listens on, so the socket and the firewall rule share one number. */
	port?: number;
	/* The caption-button overlay's height, for the reason the port is passed; null where there is
	 * no overlay, and the repaint verb answers false. */
	titleBarHeight?: number | null;
	/* The two questions asked before a backend exists. Registered always, since forgetting the mode
	 * sends the window back to them. Null in tests and in a shell already set up. */
	setup?: Setup | null;
	/* Stops the backend before an update's installer launches; null in tests and client mode. */
	stopForUpdate?: (() => Promise<void>) | null;
	/* Whether closing the window leaves Sift running. */
	closing?: {
		keepRunning: () => boolean;
		keep: (on: boolean) => void;
	} | null;
	/* The libraries this copy has opened, and switching between them. See `LibraryBook`. */
	libraries?: LibraryBook | null;
	/* Whether Sift starts at Windows sign-in, as WINDOWS says (startup.ts); null where it cannot. */
	startup?: StartWithWindows | null;
}

/** Wire the verbs up. Called once, after the app is ready. */
export function registerVerbs(reachOf: ReachCheck, hooks: ShellHooks = {}): void {
	const {
		feedUrl = () => null,
		restart = null,
		servers = null,
		sharing = null,
		links = null,
		downloads = null,
		storage = null,
		port = PORT,
		titleBarHeight = null,
		setup = null,
		stopForUpdate = null,
		closing = null,
		libraries = null,
		startup = null
	} = hooks;

	/* SYNCHRONOUS, the one verb that is: the preload asks while the page loads, before its scripts
	   run. It only decides which methods are built; every handler still checks its caller. */
	ipcMain.on(BRIDGE_VERBS, (event, href: unknown) => {
		const named = typeof href === 'string' ? reachOf(href) : null;
		const frame = event.senderFrame;
		event.returnValue = verbsFor(
			named ?? (frame !== null && frame.parent === null ? reachOf(frame.url) : null)
		);
	});

	ipcMain.handle(CHOOSE_MODE, async (event, chosen: unknown): Promise<Settled> => {
		const frame = askingFrame(event, reachOf, CHOOSE_MODE);
		if (frame === null || setup === null) return { ok: false, refusal: null };
		/* Checked, not cast: this decides whether a backend starts on this machine. */
		if (chosen !== 'standalone' && chosen !== 'client') return { ok: false, refusal: null };
		return setup.mode(chosen);
	});

	ipcMain.handle(SETUP_BACK, async (event): Promise<Settled> => {
		const frame = askingFrame(event, reachOf, SETUP_BACK);
		if (frame === null || setup === null) return { ok: false, refusal: null };
		return setup.back();
	});

	ipcMain.handle(SUGGESTED_LIBRARY, async (event): Promise<Suggested | null> => {
		const frame = askingFrame(event, reachOf, SUGGESTED_LIBRARY);
		if (frame === null || setup === null) return null;
		return setup.suggested();
	});

	ipcMain.handle(CHOOSE_LIBRARY, async (event, pick: unknown): Promise<Settled> => {
		const frame = askingFrame(event, reachOf, CHOOSE_LIBRARY);
		if (frame === null || setup === null) return { ok: false, refusal: null };
		/* Sent as it goes, like a move's progress, so a long first start does not look stuck. */
		return setup.library(pick === true, (step) => {
			if (!event.sender.isDestroyed()) event.sender.send(SETUP_PROGRESS, step);
		});
	});

	ipcMain.handle(CHOOSE_FOLDER, async (event) => {
		const frame = askingFrame(event, reachOf, CHOOSE_FOLDER);
		if (frame === null) return null;
		return chooseFolder(BrowserWindow.fromWebContents(event.sender));
	});

	ipcMain.handle(CHOOSE_FILE, async (event) => {
		const frame = askingFrame(event, reachOf, CHOOSE_FILE);
		if (frame === null) return null;
		return chooseFile(BrowserWindow.fromWebContents(event.sender));
	});

	ipcMain.handle(START_DRAG, async (event, assetId: unknown): Promise<DragOutcome> => {
		const frame = askingFrame(event, reachOf, START_DRAG);
		if (frame === null || typeof assetId !== 'string' || assetId === '') {
			/* Each reason refused is logged, since from outside they are one word. */
			log.warning('drag.refused', {
				why: frame === null ? 'untrusted-frame' : 'no-asset-id'
			});
			return 'unavailable';
		}
		const origin = originOf(frame.url);
		if (origin === null) {
			log.warning('drag.refused', { why: 'no-origin', url: frame.url });
			return 'unavailable';
		}

		const answered = await localFile(origin, assetId);
		/* A server elsewhere describes its file but never names one of this machine's. */
		const remote = reachOf(frame.url) === 'remote';
		const file =
			answered === null || !remote
				? answered
				: {
						...answered,
						path: null,
						shared_path:
							typeof answered.shared_path === 'string' &&
							answered.size_bytes !== null &&
							isShareFromElsewhere(answered.shared_path)
								? answered.shared_path
								: null
					};
		if (file === null) {
			/* The server would not say where the file is: gone, hidden, or nobody signed in. */
			log.warning('drag.refused', {
				why: 'server-has-no-file',
				asset_id: assetId,
				origin
			});
			return 'unavailable';
		}

		/* The whole file is already here: drag it where it lies, with no copy. EXCEPT A FILE THAT
		 * HOLDS ITS PLACE: no path is ever dragged for it; the copy without the place is fetched. */
		const placed = file.holds_a_place === true;
		if (file.path !== null && !placed) {
			dragFile(file.path, 'local');
			return 'dragged';
		}

		/* And a client-mode file fetched by an earlier drag is here too, so it takes the same path.
		 * Checked before anything is started: it is the difference between a second drag of the same
		 * clip being instant and it being downloaded again. */
		const cached = alreadyFetched(assetId, file);
		if (cached !== null) {
			dragFile(cached, 'cached');
			return 'dragged';
		}

		/* A LIBRARY ON A NETWORK SHARE NEEDS NO TRANSFER AT ALL: both machines see the NAS, so the
		 * share path is handed over and read as at home, at any size. Below the cached copy, which
		 * is faster to read and the same file. */
		const began = Date.now();
		const onTheShare = placed ? null : await reachedHere(file);
		const waited = Date.now() - began;
		if (onTheShare !== null) {
			dragFile(onTheShare, 'share');
			return 'dragged';
		}
		/* How long the share was given: waiting the whole budget means it never answered; a short
		   wait means it answered no, a different problem. */
		log.info('drag.share_not_used', {
			waited_ms: waited,
			timed_out: waited >= 1500,
			shared_path: file.shared_path ?? null
		});

		/* Started and NOT awaited: OLE's drag loop must be entered while the button is down, and the
		 * drag below reads the file as it arrives. */
		void fetchToTemp(origin, assetId, file, (received, total) => {
			/* The fetch can outlive its window. */
			if (!event.sender.isDestroyed()) {
				event.sender.send(DRAG_PROGRESS, { assetId, received, total });
			}
			return true;
		}).catch(() => {
			/* Unawaited, but never unhandled: the receiver learns of a dead transfer from the marker
			 * file beside the partial, written on every failing path. */
		});

		dragArriving(arrivingAt(assetId, file), file.filename, file.size_bytes);
		return 'dragged';
	});

	/* What THIS computer is, answering null on the machine running the library, whose hardware block
	 * already describes it; so a block appears exactly when there is a second computer. */
	ipcMain.handle(LOCAL_HARDWARE, async (event): Promise<LocalMachine | null> => {
		const frame = askingFrame(event, reachOf, LOCAL_HARDWARE);
		if (frame === null) return null;
		if (sharing !== null && sharing.read().mode !== 'client') return null;
		return thisMachine();
	});

	/* What this computer is called, in every mode: the remote's label for this window. */
	ipcMain.handle(MACHINE_NAME, async (event): Promise<string | null> => {
		if (askingFrame(event, reachOf, MACHINE_NAME) === null) return null;
		return machineName();
	});

	/* NO ARGUMENTS: the page says "go"; the shell reads the feed, checks the signature, launches. An
	 * address argument would make it "download this and run it". */
	ipcMain.handle(APPLY_UPDATE, async (event): Promise<UpdateOutcome> => {
		const frame = askingFrame(event, reachOf, APPLY_UPDATE);
		if (frame === null || !pressed(event, reachOf, 'update'))
			return { ok: false, reason: 'failed' };
		return installNewer(feedAddress(feedUrl()), async () => {
			await stopForUpdate?.();
		});
	});

	/* Client mode's first run, answering a SENTENCE, since this side tried the address and saw what
	 * came back; codes would need a second list on the screen. */
	ipcMain.handle(SAVE_SERVER, async (event, origin: unknown): Promise<string | null> => {
		const frame = askingFrame(event, reachOf, SAVE_SERVER);
		if (frame === null || servers === null) return 'This only works in the Sift app.';
		if (typeof origin !== 'string') return 'That is not an address.';
		return servers.remember(origin);
	});

	ipcMain.handle(LAST_SERVER, async (event): Promise<string | null> => {
		const frame = askingFrame(event, reachOf, LAST_SERVER);
		if (frame === null || servers === null) return null;
		return servers.last();
	});

	ipcMain.handle(CONNECT_STATE, async (event): Promise<ConnectState> => {
		const frame = askingFrame(event, reachOf, CONNECT_STATE);
		if (frame === null || servers === null) return { last: null, problem: null, servers: [] };
		return {
			last: servers.last(),
			problem: servers.problem(),
			servers: servers.saved()
		};
	});

	ipcMain.handle(FORGET_SERVER, async (event, origin: unknown): Promise<SavedServer[]> => {
		const frame = askingFrame(event, reachOf, FORGET_SERVER);
		if (frame === null || servers === null) return [];
		if (typeof origin !== 'string') return servers.saved();
		return servers.forget(origin);
	});

	ipcMain.handle(READ_CLIPBOARD, async (event): Promise<ClipboardContents | null> => {
		const frame = askingFrame(event, reachOf, READ_CLIPBOARD);
		if (frame === null || !pressed(event, reachOf, 'clipboard')) return null;
		return readClipboard();
	});

	/* A picture of the window asking, or one area, as PNG bytes; null when there is none. Only the
	 * page's own pixels. */
	ipcMain.handle(CAPTURE_WINDOW, async (event, area: unknown): Promise<Uint8Array | null> => {
		const frame = askingFrame(event, reachOf, CAPTURE_WINDOW);
		if (frame === null || !pressed(event, reachOf, 'capture')) return null;
		return captureWindow(event.sender, area);
	});

	ipcMain.handle(GET_SHARING, async (event): Promise<Sharing | null> => {
		const frame = askingFrame(event, reachOf, GET_SHARING);
		if (frame === null || sharing === null) return null;
		return sharing.read();
	});

	/* Writing this OPENS OR CLOSES A SOCKET: the backend restarts on the other address, so it is
	 * awaited and answers what is true afterwards, the old address included when the change failed. */
	ipcMain.handle(SET_SHARING, async (event, on: unknown): Promise<Sharing | null> => {
		const frame = askingFrame(event, reachOf, SET_SHARING);
		if (frame === null || sharing === null) return null;
		if (typeof on !== 'boolean') return sharing.read();
		await sharing.write(on);
		return sharing.read();
	});

	/* The end of the shell's own log: not admin-only, as the file is on the person's own machine and
	 * redacted where it is written. A count in, nothing else. */
	ipcMain.handle(SHELL_LOG, async (event, lines: unknown): Promise<ShellLog | null> => {
		if (askingFrame(event, reachOf, SHELL_LOG) === null) return null;
		/* A number from the page, and nothing else: the backend's link reads its count from a query. */
		return tailShellLog(linesAsked(typeof lines === 'number' ? lines : null));
	});

	ipcMain.handle(SHELL_LOG_DETAIL, async (event, detailed: unknown, hide?: unknown) => {
		if (askingFrame(event, reachOf, SHELL_LOG_DETAIL) === null) return null;
		if (typeof detailed !== 'boolean') return null;
		setDetail(detailed);
		if (typeof hide === 'boolean') setHidePersonal(hide); // absent from an older page
		return detailed;
	});

	/* WHAT THIS COPY OF SIFT IS, which in client mode is not the library's version: the older of
	 * the two would be invisible. Null on the library's own machine (one install) and from a
	 * checkout, where Electron would answer its own version. */
	ipcMain.handle(SHELL_VERSION, async (event): Promise<string | null> => {
		const frame = askingFrame(event, reachOf, SHELL_VERSION);
		if (frame === null) return null;
		if (sharing !== null && sharing.read().mode !== 'client') return null;
		return app.isPackaged ? app.getVersion() : null;
	});

	/* A PLAIN READ of Windows' rules, its own verb because it costs a PowerShell start. */
	ipcMain.handle(GET_FIREWALL, async (event): Promise<FirewallReport> => {
		const frame = askingFrame(event, reachOf, GET_FIREWALL);
		if (frame === null || sharing === null)
			return { state: 'unknown', networks: null, scope: null };
		return firewallState(port);
	});

	/* THE PAGE SAYS "GO" AND NOTHING ELSE: port and command are this shell's (firewall.ts), or it
	 * would be "run this as administrator". It answers the state afterwards, so a cancelled prompt
	 * and a refusal both get the truth. */
	ipcMain.handle(OPEN_FIREWALL, async (event, scope: unknown): Promise<FirewallReport> => {
		const frame = askingFrame(event, reachOf, OPEN_FIREWALL);
		if (frame === null || sharing === null)
			return { state: 'unknown', networks: null, scope: null };
		/* The one word the page may add; anything else is the narrow rule. */
		const chosen: FirewallScope = scope === 'any' ? 'any' : 'private';
		return openFirewall(port, undefined, chosen);
	});

	ipcMain.handle(LIST_BROWSERS, async (event): Promise<BrowserChoice> => {
		const frame = askingFrame(event, reachOf, LIST_BROWSERS);
		if (frame === null || links === null) return { chosen: null, browsers: [] };
		return { chosen: links.chosen(), browsers: await installedBrowsers() };
	});

	/* The page picks the shade; the operating system keeps the buttons. Only the two colours reach
	 * `setTitleBarOverlay`, as plain `#rgb`/`#rrggbb`, since the string reaches the operating system. */
	ipcMain.handle(SET_TITLE_BAR, async (event, colors: unknown): Promise<boolean> => {
		const frame = askingFrame(event, reachOf, SET_TITLE_BAR);
		if (frame === null) return false;
		const window = BrowserWindow.fromWebContents(event.sender);
		if (window === null) return false;
		const asked = colors as { color?: unknown; symbolColor?: unknown } | null;
		const color = asked?.color;
		const symbolColor = asked?.symbolColor;
		if (titleBarHeight === null) return false;
		if (!isHexColor(color) || !isHexColor(symbolColor)) return false;
		try {
			window.setTitleBarOverlay({
				color,
				symbolColor,
				height: titleBarHeight
			});
			return true;
		} catch {
			/* No overlay on this window: false is honest, and the page is already right. */
			return false;
		}
	});

	/* Where a saved file lands, and whether it was chosen (only the unchosen follows the machine). */
	ipcMain.handle(GET_DOWNLOAD_DIR, async (event): Promise<DownloadFolder | null> => {
		const frame = askingFrame(event, reachOf, GET_DOWNLOAD_DIR);
		if (frame === null || downloads === null) return null;
		return { path: downloads.folder(), chosen: downloads.chosen() !== null };
	});

	/* Choosing opens the operating system's picker, the only grant of a path; `null` means back to
	 * the machine's own. */
	ipcMain.handle(
		SET_DOWNLOAD_DIR,
		async (event, reset: unknown): Promise<DownloadFolder | null> => {
			const frame = askingFrame(event, reachOf, SET_DOWNLOAD_DIR);
			if (frame === null || downloads === null) return null;
			if (reset === null) {
				downloads.choose(null);
			} else {
				const picked = await chooseFolder(BrowserWindow.fromWebContents(event.sender));
				if (picked !== null) downloads.choose(picked);
			}
			return { path: downloads.folder(), chosen: downloads.chosen() !== null };
		}
	);

	/* A backup Sift saved here, shown in its folder (the Backup pane). A PATH FROM A PAGE, so only a
	 * whole path to an existing file named as a backup (`isBackupPath`), local only, and
	 * `showItemInFolder`, which runs nothing, never `openPath`. */
	ipcMain.handle(SHOW_IN_FOLDER, async (event, path: unknown): Promise<boolean> => {
		const frame = askingFrame(event, reachOf, SHOW_IN_FOLDER);
		if (frame === null || !isBackupPath(path)) return false;
		try {
			if (!(await stat(path)).isFile()) return false;
		} catch {
			return false;
		}
		shell.showItemInFolder(path);
		return true;
	});

	ipcMain.handle(GET_STORAGE, async (event): Promise<StorageReport | null> => {
		const frame = askingFrame(event, reachOf, GET_STORAGE);
		if (frame === null || storage === null) return null;
		return storage.read();
	});

	/* The picker, so a page never names where this application writes. */
	ipcMain.handle(MOVE_STORAGE, async (event): Promise<MoveOutcome> => {
		const frame = askingFrame(event, reachOf, MOVE_STORAGE);
		if (frame === null || storage === null) return { ok: false, reason: 'Not available here.' };
		const window = BrowserWindow.fromWebContents(event.sender);
		const picked = await chooseFolder(window);
		if (picked === null) return { ok: false, reason: null };
		return storage.move(picked, (copied, total) => {
			/* Sent as it goes: a copy across drives takes minutes, like a drag-out's progress. */
			if (!event.sender.isDestroyed()) event.sender.send(STORAGE_PROGRESS, { copied, total });
		});
	});

	/* Forgetting is a write and nothing else; the next launch asks the first question again. */
	ipcMain.handle(FORGET_MODE, async (event): Promise<boolean> => {
		const frame = askingFrame(event, reachOf, FORGET_MODE);
		if (frame === null || storage === null) return false;
		storage.forget();
		return true;
	});

	/* Answers before it restarts, or the answer would reach a page already gone. */
	ipcMain.handle(RESTART_APP, async (event): Promise<boolean> => {
		const frame = askingFrame(event, reachOf, RESTART_APP);
		if (frame === null || restart === null) return false;
		setImmediate(() => void restart());
		return true;
	});

	/* THE PAGE MAY ONLY NAME A BROWSER FROM THE MACHINE'S OWN LIST, read fresh: what is chosen is a
	 * program this application will START, so anything else is refused, never sanitised. */
	ipcMain.handle(SET_BROWSER, async (event, id: unknown): Promise<BrowserChoice> => {
		const frame = askingFrame(event, reachOf, SET_BROWSER);
		if (frame === null || links === null) return { chosen: null, browsers: [] };
		const browsers = await installedBrowsers();
		if (id === null) links.choose(null);
		else if (typeof id === 'string' && browsers.some((one) => one.id === id)) links.choose(id);
		return { chosen: links.chosen(), browsers };
	});

	/* Whether the close button leaves Sift running; null draws no switch rather than one that lies. */
	ipcMain.handle(GET_KEEP_RUNNING, async (event): Promise<boolean | null> => {
		const frame = askingFrame(event, reachOf, GET_KEEP_RUNNING);
		if (frame === null || closing === null) return null;
		return closing.keepRunning();
	});

	ipcMain.handle(SET_KEEP_RUNNING, async (event, on: unknown): Promise<boolean | null> => {
		const frame = askingFrame(event, reachOf, SET_KEEP_RUNNING);
		if (frame === null || closing === null) return null;
		/* Checked against `true`, not cast: it keeps this machine working after the window closes. */
		closing.keep(on === true);
		return closing.keepRunning();
	});

	/* Whether Sift starts at sign-in; null where no shell can register itself (a checkout). */
	ipcMain.handle(GET_START_WITH_WINDOWS, async (event): Promise<boolean | null> => {
		const frame = askingFrame(event, reachOf, GET_START_WITH_WINDOWS);
		if (frame === null || startup === null) return null;
		return startup.read();
	});

	ipcMain.handle(SET_START_WITH_WINDOWS, async (event, on: unknown): Promise<boolean | null> => {
		const frame = askingFrame(event, reachOf, SET_START_WITH_WINDOWS);
		if (frame === null || startup === null) return null;
		/* Checked against `true`, like close-to-tray; it answers Windows' state after the write. */
		return startup.write(on === true);
	});

	ipcMain.handle(LIST_LIBRARIES, async (event): Promise<LibraryList | null> => {
		const frame = askingFrame(event, reachOf, LIST_LIBRARIES);
		if (frame === null || libraries === null) return null;
		return libraries.list();
	});

	/* A FOLDER ALREADY ON THE LIST, so the most a page can ask for is a library the person once
	 * granted through the picker, never an arbitrary path. */
	ipcMain.handle(OPEN_LIBRARY, async (event, dataDir: unknown): Promise<Settled> => {
		const frame = askingFrame(event, reachOf, OPEN_LIBRARY);
		if (frame === null || libraries === null) return { ok: false, refusal: null };
		if (typeof dataDir !== 'string' || dataDir === '') return { ok: false, refusal: null };
		return libraries.open(dataDir);
	});

	/* The picker: only the operating system's own dialog can grant a database file. */
	ipcMain.handle(ADD_LIBRARY, async (event): Promise<Settled> => {
		const frame = askingFrame(event, reachOf, ADD_LIBRARY);
		if (frame === null || libraries === null) return { ok: false, refusal: null };
		return libraries.add();
	});

	ipcMain.handle(FORGET_LIBRARY, async (event, dataDir: unknown): Promise<LibraryList | null> => {
		const frame = askingFrame(event, reachOf, FORGET_LIBRARY);
		if (frame === null || libraries === null) return null;
		if (typeof dataDir !== 'string' || dataDir === '') return libraries.list();
		return libraries.forget(dataDir);
	});
}

/* --- Where a second computer would reach this one ----------------------------------------- */

/**
 * This machine's address on the network, or null when it has none worth naming: IPv4, never
 * loopback, since somebody TYPES IT INTO ANOTHER COMPUTER. A HOME-NETWORK ADDRESS IS PREFERRED over
 * the first listed, which with a VPN is often a carrier-range address the other machine cannot
 * reach. One address, since a list does not answer "what do I type".
 */
export function networkAddress(): string | null {
	return preferredAddress(ownAddresses());
}

/** Every IPv4 address this machine has on a network, loopback excluded. */
function ownAddresses(): string[] {
	const found: string[] = [];
	for (const entries of Object.values(networkInterfaces())) {
		for (const entry of entries ?? []) {
			if (entry.family !== 'IPv4' || entry.internal) continue;
			found.push(entry.address);
		}
	}
	return found;
}

/** Which of this machine's addresses to offer, apart from reading them so the CHOICE can be tested. */
export function preferredAddress(candidates: readonly string[]): string | null {
	return candidates.find(isHomeNetworkAddress) ?? candidates[0] ?? null;
}

/* --- The clipboard ----------------------------------------------------------------------- */

/** What was on the clipboard, in the two shapes Sift can take in. */
export interface ClipboardContents {
	/** A link, or empty. Trimmed here so the page does not have to decide what blank means. */
	text: string;
	/** A picture, as a data URL, or null. PNG because that is what a screenshot is. */
	image: string | null;
}

/* Read in the MAIN process: the browser's clipboard read needs a secure context, which Sift over
 * plain http on a LAN lacks, so this is how Paste works there. */
export function readClipboard(): ClipboardContents {
	const image = clipboard.readImage();
	return {
		text: clipboard.readText().trim(),
		/* An empty NativeImage still answers a data URL of nothing. */
		image: image.isEmpty() ? null : image.toDataURL()
	};
}

/* --- A picture of the window ----------------------------------------------------------------- */

/** The one method of a window's contents a capture needs, named so a test can hand in its own. */
export interface Capturable {
	capturePage(rect?: Rectangle): Promise<NativeImage>;
	getZoomFactor(): number;
}

/**
 * The area asked for, in the units `capturePage` takes, or undefined for the whole window: anything
 * but four finite numbers with some size is the whole window. CSS pixels times the zoom.
 */
export function captureRect(area: unknown, zoom: number): Rectangle | undefined {
	if (typeof area !== 'object' || area === null) return undefined;
	const { x, y, width, height } = area as Record<string, unknown>;
	const numbers = [x, y, width, height];
	if (!numbers.every((one) => typeof one === 'number' && Number.isFinite(one))) return undefined;
	const scale = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
	const left = Math.max(0, Math.floor((x as number) * scale));
	const top = Math.max(0, Math.floor((y as number) * scale));
	const right = Math.ceil(((x as number) + (width as number)) * scale);
	const bottom = Math.ceil(((y as number) + (height as number)) * scale);
	if (right <= left || bottom <= top) return undefined;
	return { x: left, y: top, width: right - left, height: bottom - top };
}

/** The window's pixels as a PNG, or null where the capture came back empty or failed. */
export async function captureWindow(
	contents: Capturable,
	area: unknown
): Promise<Uint8Array | null> {
	try {
		const rect = captureRect(area, contents.getZoomFactor());
		const image =
			rect === undefined ? await contents.capturePage() : await contents.capturePage(rect);
		/* An empty image still encodes, to a file that does not open. */
		if (image.isEmpty()) return null;
		return new Uint8Array(image.toPNG());
	} catch {
		return null;
	}
}
