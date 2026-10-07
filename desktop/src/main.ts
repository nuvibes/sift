/* The Sift desktop shell.
 *
 * Two lifecycles over one backend:
 *
 *   standalone: start the Python backend on 127.0.0.1:5171 and load it.
 *   client: load a server somebody else is running, and start nothing.
 *
 * Both load one origin and hand over to the ordinary application, whose requests are relative
 * /api paths: no second origin, no CORS, no separate sign-in, so client mode is nearly free.
 */

import { app, BrowserWindow, dialog, net, session, shell, type Tray } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

import type { Settled } from '../../shared/bridge';

import { clearFetchedFiles } from './assets';
import { Backend, BackendStartError, ORIGIN as LOCAL_ORIGIN, PORT } from './backend';
import {
	firstRunRoute,
	offeredDataLocation,
	pickDataLocation,
	refuseLocation,
	suggestedDataLocation
} from './firstrun';
import { isWebAddress, openIn } from './browsers';
import {
	adoptDatabase,
	backUpLibrary,
	find as findLibrary,
	forgotten,
	inspectDatabase,
	inspectLibrary,
	nameFor,
	openingAtStart,
	planForDatabase,
	remembered,
	sameFolder,
	takeSwitchNote,
	unmakeEmpty,
	type DatabasePlan,
	type LibraryReport
} from './libraries';
import { firewallState, openFirewall } from './firewall';
import { StartFrame } from './opening';
import { log, tail as tailShellLog } from './log';
import { registerLogArchive } from './logbundle';
import { saveWithoutAsking } from './saving';
import { registerWindowStage } from './stage';
import { machineName } from './machine';
import { watchGestures } from './gesture';
import {
	isTrusted,
	looksLikeSift,
	normaliseOrigin,
	plainHttpRefusal,
	reachOf,
	shareAddress
} from './origins';
import { appIconFile, trayIconFile, type DataLocations } from './paths';
import { load, locations, mayShare, save, withoutMode, type DesktopSettings } from './settings';
import { showInTray, whatClosingDoes } from './tray';
import { declareShellScheme, serveShellPages, SHELL_ORIGIN } from './shellpage';
import { answerSmokeRun } from './smoke';
import { openShellLink, type Deferred, type ShellLink } from './shelllink';
import { howToAppear, startedAtSignIn, startWithWindows } from './startup';
import {
	describe as describeStorage,
	move,
	refuse as refuseStorageFolder,
	type MoveResult
} from './storage';
import { recordForUninstaller } from './uninstall';
import { feedAddress, type UpdateOutcome } from './update';
import {
	installNewer,
	networkAddress,
	registerVerbs,
	type LibraryList,
	type ReachCheck,
	type Sharing
} from './verbs';
import { wayBack } from './wayback';

/* The Vite dev server, which forwards /api and /health to the backend, so the page sees one origin;
 * behind a flag, so a shipped app is never pointed at it. */
const DEV_ORIGIN = 'http://localhost:5173';
const isDevShell = process.argv.includes('--dev');

/* Before anything else: a privileged scheme cannot be declared once the app is ready, and this one
 * serves the connect screen when there is no server. */
declareShellScheme();

let settings: DesktopSettings = load();
let backend: Backend | null = null;
/* Where the backend asks this shell for acts only it can do, so a window from another computer acts
 * on THIS machine (`shelllink.ts`). Opened at the first backend start and reused, same secret; null
 * in client mode. */
let shellLink: ShellLink | null = null;
let mainWindow: BrowserWindow | null = null;

/* The icon in the notification area, while there is one: it follows the close-to-tray setting. */
let tray: Tray | null = null;

/* WHETHER SIFT IS ON ITS WAY OUT: a window that hides on close would otherwise hide on the tray's
 * own Quit too. Set by `before-quit`, which every route out passes through. */
let quitting = false;

/* WHETHER THIS START IS WINDOWS STARTING SIFT AT SIGN-IN, spent by the first window shown; a later
 * window is somebody at the keyboard. */
let signInStart = startedAtSignIn(process.argv);

/* Whether the running backend is listening to the network, as against what the setting says: the
 * two part while a restart runs and stay parted if it failed, when only this is honest. */
let liveSharing = false;

/* The dialog after Sift has stopped, and the start with the optional features held off. */
const crashDialog = wayBack({
	settings: () => settings,
	save: (next) => {
		settings = next;
		save(settings);
	},
	startAgain: () => void advanceFirstRun()
});

/* When this copy started, and whether its window has been shown, for the first-show time in the log. */
const startedAt = Date.now();
let shownOnce = false;

/* Sift's own frame over the window while the first page loads (`opening.ts`). */
const frame = new StartFrame(startedAt);

/* THE SMOKE RUN COMES FIRST, BEFORE THE LOCK: Sift exits 0 when another copy holds the lock, so a
 * smoke run after it would pass without starting anything (`smoke.ts`; main.test.ts holds the
 * order). It takes no lock and opens nothing. */
if (
	answerSmokeRun(
		process.argv,
		(line) => void process.stdout.write(line),
		(n) => app.exit(n)
	)
) {
	/* Nothing else: the process has already been asked to stop. One Sift per machine otherwise: two
	 * would race for port 5171, and focusing the open one is what every desktop app does. */
} else if (!app.requestSingleInstanceLock()) {
	app.quit();
} else {
	/* `show` as well as `focus`: a window hidden in the tray would otherwise not appear. */
	app.on('second-instance', () => {
		showMainWindow();
	});
	void main();
}

async function main(): Promise<void> {
	await app.whenReady();
	/* The first line of every run in the shell's own log: which Sift, and set up which way. */
	log.info('shell.started', {
		version: app.getVersion(),
		packaged: app.isPackaged,
		mode: settings.mode ?? 'not chosen',
		uptime_ms: Math.round(process.uptime() * 1000)
	});

	/* Anything a previous run fetched to drag is thrown away at STARTUP, since a crash never reaches
	 * a shutdown sweep. */
	clearFetchedFiles();
	serveShellPages();

	/* Before any window exists, so no page is ever shown unwired verbs. Read through functions,
	 * since `settings` changes during first run. */
	/* The shell's own connect screen is this machine's too (static files from the bundle); the dev
	 * server stands in for the local backend. */
	const trust: ReachCheck = (url) => {
		if (url.startsWith(SHELL_ORIGIN)) return 'local';
		if (isDevShell) return url.startsWith(DEV_ORIGIN) ? 'local' : null;
		return reachOf(settings, url);
	};
	registerLogArchive(trust, crashDialog.archive);
	registerWindowStage(trust, frame.drawn);
	registerVerbs(trust, {
		/* Read through a function, as the trust check is. */
		feedUrl: () => settings.feedUrl,
		servers: {
			remember: rememberServer,
			last: () => settings.lastServer,
			problem: () => connectProblem,
			saved: () => settings.servers,
			forget: forgetServer
		},
		sharing: {
			read: (): Sharing => ({
				enabled: settings.shareOnNetwork,
				/* What is TRUE RIGHT NOW, which differs from `enabled` only after a failed restart. */
				live: liveSharing,
				mode: settings.mode,
				address: shareAddress(networkAddress(), PORT),
				/* One declaration, in the file that opens the socket and makes the firewall rule. */
				port: PORT
			}),
			write: writeSharing
		},
		links: {
			chosen: () => settings.browser,
			/* Written straight through: nothing to restart, nothing that can fail. */
			choose: (id: string | null) => {
				settings = { ...settings, browser: id };
				save(settings);
			}
		},
		downloads: {
			/* Always a real path; the machine's own Downloads is resolved here, so it can move. */
			folder: () => settings.downloadDir ?? app.getPath('downloads'),
			chosen: () => settings.downloadDir,
			choose: (dir: string | null) => {
				settings = { ...settings, downloadDir: dir };
				save(settings);
			}
		},
		storage: {
			read: () => describeStorage(locations(settings)),
			/*
			 * THE ORDER IS THE WHOLE OF THE SAFETY, and only the backend's supervisor can keep it:
			 * stop, move, write, start. The database's files are held open until the backend is
			 * down, and the settings change only after the bytes arrive, so no crash leaves them
			 * pointing at an empty folder. A failed move restarts on the intact old folder.
			 */
			move: moveFolders,
			forget: forgetMode
		},
		port: PORT,
		titleBarHeight: TITLE_BAR_HEIGHT,
		setup: {
			/* What each first-run ANSWER does lives here: only the supervising process can write
			 * the setting, decide what is next, start the backend and load the library. */
			mode: async (chosen) => {
				settings = { ...settings, mode: chosen };
				save(settings);
				await advanceFirstRun();
				return { ok: true, refusal: null };
			},
			suggested: async () => {
				const offered = await offeredDataLocation();
				return {
					path: offered.locations.dataDir,
					existing: offered.existing
				};
			},
			library: async (pick, told) => {
				/* Without picking: the folder the screen showed (an earlier library, or the default). */
				const chosen = pick ? await pickDataLocation() : (await offeredDataLocation()).locations;
				/* Closing the picker is neither failure nor answer: the screen stays as it was. */
				if (chosen === null) return { ok: false, refusal: null };
				told('checking');
				const problem = await refuseLocation(chosen);
				if (problem !== null) return { ok: false, refusal: problem };
				settings = {
					...settings,
					dataDir: chosen.dataDir,
					cacheDir: chosen.cacheDir
				};
				save(settings);
				told('starting');
				await advanceFirstRun();
				return { ok: true, refusal: null };
			},
			back: async () => {
				/* The backend is stopped first, cleanly: Back is reachable from the sign-in screen,
				 * and a backend left holding 5171 would refuse the next start. */
				await backend?.stop();
				backend = null;
				/* The settings screen's own "set this up again" write, which leaves the library
				 * folder, so going back and forward loses nothing. */
				forgetMode();
				await advanceFirstRun();
				return { ok: true, refusal: null };
			}
		},
		/* The installer runs only once the backend has been ASKED to stop and has gone, as a quit
		 * does, so no job is cut off. Null in client mode. */
		stopForUpdate: async () => {
			await backend?.stop();
		},
		/* "Restart Sift": only the backend's owner can stop it cleanly and relaunch. */
		restart: restartApp,
		closing: {
			keepRunning: () => settings.keepRunningWhenClosed,
			keep: (on: boolean) => {
				settings = { ...settings, keepRunningWhenClosed: on };
				save(settings);
				/* Applied NOW: it decides what the next press of close means. */
				dressTheTray();
			}
		},
		/* The executable is this process's own, and nothing a page sends can change it. */
		startup: startWithWindows(app, process.execPath),
		libraries: {
			list: libraryList,
			open: openLibrary,
			add: addLibrary,
			forget: (dataDir: string) => {
				settings = {
					...settings,
					libraries: forgotten(settings.libraries, dataDir)
				};
				save(settings);
				return libraryList();
			}
		}
	});

	/* After the verbs and before the window, so the icon is there for the whole run. */
	dressTheTray();

	/* Downloads land in that folder without a dialog. Registered once, on the window's session. */
	saveWithoutAsking(() => settings.downloadDir ?? app.getPath('downloads'));

	/*
	 * The window comes first: the first-run questions are Sift's own screens, answered through verbs
	 * that call `advanceFirstRun`, the shape `/connect` has.
	 */
	mainWindow = createWindow();
	/* On screen immediately in Sift's own frame, unless Windows started Sift at sign-in (`reveal`). */
	const window = mainWindow;
	if (!signInStart) {
		frame.open(window, SHELL_ORIGIN, () => {
			if (shownOnce) return false;
			reveal(window, true);
			return true;
		});
	}
	await advanceFirstRun();
}

/** Load the window's page under the frame. */
async function loadPage(window: BrowserWindow, address: string): Promise<void> {
	await window.loadURL(address);
	frame.loaded(address, SHELL_ORIGIN);
}

/* Put the icon in the notification area, or take it away, to match the setting. Idempotent; an icon
 * is destroyed, never dropped, or the tray collects copies. */
function dressTheTray(): void {
	if (settings.keepRunningWhenClosed && tray === null) {
		tray = showInTray(trayIconFile(), {
			open: () => {
				showMainWindow();
			},
			quit: () => {
				/* `before-quit` sets `quitting`, which lets the window's close through. */
				app.quit();
			}
		});
		return;
	}
	if (!settings.keepRunningWhenClosed && tray !== null) {
		tray.destroy();
		tray = null;
	}
}

/**
 * Show the window a start has just loaded, unless Windows started Sift at sign-in. A screen waiting
 * on a person (`needsSomebody`: a first-run question, an unreachable library) is always shown;
 * only a cleanly loaded library stays out of the way (`howToAppear`).
 */
function reveal(window: BrowserWindow, needsSomebody: boolean): void {
	const how = needsSomebody ? 'show' : howToAppear(signInStart, tray !== null);
	signInStart = false;
	if (!shownOnce) {
		shownOnce = true;
		const frameShown = { frame: frame.framed, uptime_ms: Math.round(process.uptime() * 1000) };
		log.info('window.first_shown', { how, after_ms: Date.now() - startedAt, ...frameShown });
	} else if (frame.takeShown() && how === 'show') return;
	if (how === 'tray') return;
	/* A never-shown window minimised appears minimised on the taskbar, which is what is wanted. */
	if (how === 'taskbar') window.minimize();
	else window.show();
}

/** Bring the window back from the notification area, wherever it was left. */
function showMainWindow(): void {
	const window = mainWindow;
	if (window === null) return;
	if (window.isMinimized()) window.restore();
	window.show();
	window.focus();
}

/**
 * Unsay which way this copy is set up, so first run asks again: the mode and the last server only.
 * `dataDir` stays, so switching back finds the library. Shared by every caller, so none can take
 * the folder with it.
 */
function forgetMode(): void {
	settings = withoutMode(settings);
	save(settings);
}

/** Offer this copy's library to the network or stop: the page's switch, and the backend's ask
 * from another computer (`shelllink.ts`). Restarts the backend on the other address. */
async function writeSharing(on: boolean): Promise<void> {
	/* Refused for a client (see `mayShare`) and cleared by `forgetMode`. */
	if (!mayShare(settings.mode)) {
		log.warning('sharing.refused', { mode: settings.mode });
		return;
	}
	/* The setting first, so a crash before the restart still leaves the next launch as asked. */
	settings = { ...settings, shareOnNetwork: on };
	save(settings);

	/* In client mode there is no backend of ours to re-address; the screen does not offer it either. */
	if (backend === null) {
		liveSharing = on;
		return;
	}

	const took = await backend.listenOnNetwork(on);
	/* The setting is not put back when the restart fails: `enabled` and `live` then disagree, and
	 * the screen says the change is asked for and applies at the next open. */
	liveSharing = took ? on : !on;
}

/** Move both storage folders under `target`: the page's Move, after its picker, and the backend's
 * ask from another computer (`shelllink.ts`). See the order note where the verbs are wired. */
async function moveFolders(
	target: string,
	report: (copied: number, total: number) => void
): Promise<MoveResult> {
	if (backend === null) {
		return {
			ok: false,
			reason: 'Sift is not running its own library on this device.'
		};
	}
	await backend.stop();
	const outcome = await move(locations(settings), target, (progress) =>
		report(progress.copied, progress.total)
	);
	if (outcome.ok) {
		settings = {
			...settings,
			dataDir: outcome.locations.dataDir,
			cacheDir: outcome.locations.cacheDir
		};
		save(settings);
		/* The uninstaller must know where the library is now, or it would offer the wrong folder. */
		recordForUninstaller(locations(settings));
	}
	/* `startBackend`, the one place that knows what to do when the backend gives up. */
	await startBackend();
	return outcome;
}

/* --- Asked by the backend, for an admin on another computer (see `shelllink.ts`) ------------ */

/* Each act here stops the backend whose request is asking, so it is answered FIRST: every refusal is
 * said before anything stops, and the act runs after the answer has left. */

const NOT_RUNNING_HERE: Settled = {
	ok: false,
	refusal: 'Sift is not running its own library on this computer.'
};

/** What the last move asked from another computer came to, for the screen there to say once Sift
 *  is back. Null until one is asked; this launch only. */
let lastMove: Settled | null = null;

async function sharingFromAfar(on: boolean): Promise<Deferred<Settled>> {
	if (backend === null || !mayShare(settings.mode)) return { answer: NOT_RUNNING_HERE };
	return { answer: { ok: true, refusal: null }, after: () => writeSharing(on) };
}

/** A move into `folder`: `refuse` runs before the backend stops, so a bad folder costs nothing. */
async function moveFromAfar(folder: string): Promise<Deferred<Settled>> {
	if (backend === null) return { answer: NOT_RUNNING_HERE };
	const refusal = await refuseStorageFolder(locations(settings), folder);
	if (refusal !== null) return { answer: { ok: false, refusal } };
	return {
		answer: { ok: true, refusal: null },
		after: async () => {
			const outcome = await moveFolders(folder, () => {});
			lastMove = outcome.ok ? { ok: true, refusal: null } : { ok: false, refusal: outcome.reason };
			log.info('storage.moved_for_another_computer', { ok: outcome.ok });
		}
	};
}

/**
 * An update asked from another computer: the check the page's Install makes (`installNewer`), so the
 * signature and "newer than this copy" are one rule. Answered once the installer is verified, with
 * its version, and only then is the backend stopped for it; a refusal is answered as one. The
 * installer opens on THIS screen, where somebody agrees to it.
 */
function updateFromAfar(): Promise<Deferred<UpdateOutcome>> {
	return new Promise((settle) => {
		let stopped = false;
		const beforeLaunch = (version: string) =>
			new Promise<void>((carryOn) => {
				settle({ answer: { ok: true, version }, after: async () => carryOn() });
			}).then(async () => {
				stopped = true;
				await backend?.stop();
			});
		installNewer(feedAddress(settings.feedUrl), beforeLaunch)
			.catch((): UpdateOutcome => ({ ok: false, reason: 'failed' }))
			.then(async (outcome) => {
				settle({ answer: outcome });
				/* The installer did not open after the backend stopped: the library goes back up. */
				if (!outcome.ok && stopped) await startBackend().catch((err) => showStartFailure(err));
			});
	});
}

/**
 * A library this copy has opened before, asked for from another computer: the checks `openLibrary`
 * makes, all before anything stops, except that an older library is refused in words rather than
 * asked about (see `settleSchema`).
 */
async function openLibraryFromAfar(dataDir: string): Promise<Deferred<Settled>> {
	const known = findLibrary(settings.libraries, dataDir);
	if (known === null) {
		return { answer: { ok: false, refusal: 'Sift has not opened that library before.' } };
	}
	if (backend === null) return { answer: NOT_RUNNING_HERE };
	const target: DataLocations = { dataDir: known.dataDir, cacheDir: known.cacheDir };
	if (sameFolder(locations(settings).dataDir, target.dataDir)) {
		return { answer: { ok: false, refusal: 'That library is the one open now.' } };
	}
	const refusal = await settleSchema(target, true, false, true);
	if (refusal !== undefined) return { answer: { ok: false, refusal: refusal ?? OLDER_FROM_AFAR } };
	return {
		answer: { ok: true, refusal: null },
		after: async () => {
			const done = await restartOn(target);
			if (!done.ok) log.warning('library.switch_refused', { refusal: done.refusal ?? '' });
		}
	};
}

/**
 * Close Sift and open it again, so a setup asked for again is reached now. The backend stops as for
 * a quit; Electron relaunches once this copy exits by the ordinary path (`before-quit`), and the new
 * copy takes the single-instance lock.
 */
async function restartApp(): Promise<void> {
	await backend?.stop();
	app.relaunch();
	app.quit();
}

/**
 * Move first run along: draw the next question, or finish and open the library. `firstRunRoute` is
 * the single place that decides whether anything is outstanding.
 */
async function advanceFirstRun(): Promise<void> {
	const window = mainWindow;
	if (window === null) return;

	const asking = firstRunRoute(settings);
	if (asking !== null) {
		await loadPage(window, `${SHELL_ORIGIN}${asking}`);
		/* Shown after the page loads, so no empty frame is seen; idempotent across answers. */
		reveal(window, true);
		return;
	}

	/* After first run, when there is something settled to record; client mode clears it. */
	recordForUninstaller(settings.mode === 'standalone' ? locations(settings) : null);

	try {
		const target = await prepareTarget();
		await loadPage(window, target);
		reveal(window, false);
	} catch (err) {
		/* In client mode a failed load is a server that stopped answering: the connect screen, not
		 * the dialog meant for a local backend that will not start. */
		if (settings.mode === 'client' && !(err instanceof BackendStartError)) {
			connectProblem = `Sift could not reach ${settings.lastServer ?? 'the saved address'}. ${
				err instanceof Error ? err.message : String(err)
			}`;
			await loadPage(window, `${SHELL_ORIGIN}/connect`);
			reveal(window, true);
			return;
		}
		/* No `app.quit()`: the offer decides, and may be "set it up again". */
		frame.failed();
		showStartFailure(err);
	}
}

/*
 * Send a link out of the window, to the browser somebody chose, or the one Windows would use. Only
 * http and https leave: `shell.openExternal` hands any scheme to whatever Windows has registered,
 * and the address comes from a page.
 */
function openOutside(url: string): void {
	// Silently: no legitimate Sift link is anything else, and a dialog would only teach dismissal.
	if (!isWebAddress(url)) return;
	const chosen = settings.browser;
	if (chosen !== null && openIn(chosen, url)) return;
	void shell.openExternal(url);
}

/** Work out which origin to load, starting whatever has to be started to make it real. */
async function prepareTarget(): Promise<string> {
	if (isDevShell) {
		/* Development still runs the real backend; Vite only serves the page and forwards the API. */
		await startBackend();
		await requireSignIn(LOCAL_ORIGIN);
		return DEV_ORIGIN;
	}

	if (settings.mode === 'client') {
		const origin = settings.lastServer ?? settings.servers[0]?.origin ?? null;
		const normalised = origin === null ? null : normaliseOrigin(origin);
		/* No address yet: the connect screen, a real Sift route out of this bundle. */
		if (normalised === null) return `${SHELL_ORIGIN}/connect`;
		settings = { ...settings, lastServer: normalised };
		save(settings);
		/* A plain-http address outside the local network: said on the connect screen. */
		const insecure = plainHttpRefusal(normalised);
		if (insecure !== null) {
			connectProblem = insecure;
			return `${SHELL_ORIGIN}/connect`;
		}
		/* Asked before it is loaded, as a typed address is; a server gone shows the connect screen. */
		const refusal = await answersAsSift(normalised);
		if (refusal !== null) {
			connectProblem = `Sift could not reach ${normalised}. ${refusal}`;
			return `${SHELL_ORIGIN}/connect`;
		}
		connectProblem = null;
		return normalised;
	}

	await startOnTheOpeningLibrary();
	await requireSignIn(LOCAL_ORIGIN);
	return LOCAL_ORIGIN;
}

/**
 * Start the backend on the library chosen to open when Sift starts (`openingAtStart`), or on the
 * one open last. A chosen library that will not start falls back to the last one, so a preference
 * never leaves Sift unable to open.
 */
async function startOnTheOpeningLibrary(): Promise<void> {
	const last = settings;
	const opening = openingAtStart(locations(last).dataDir);
	if (opening === null || sameFolder(opening.dataDir, locations(last).dataDir)) {
		await startBackend();
		return;
	}
	settings = { ...last, dataDir: opening.dataDir, cacheDir: opening.cacheDir };
	try {
		await startBackend();
		recordForUninstaller(locations(settings));
	} catch (err) {
		log.warning('library.opening_refused', {
			reason: err instanceof Error ? err.message : String(err)
		});
		await backend?.stop().catch(() => undefined);
		settings = last;
		await startBackend();
	}
}

/**
 * Try an address, and go there if it answers. A sentence back when it does not. CHECKED before it
 * is saved, so a dead address never strands the next launch away from the screen that fixes it.
 */
async function rememberServer(typed: string): Promise<string | null> {
	const normalised = normaliseOrigin(typed);
	if (normalised === null) {
		return 'That does not look like an address. It should look like http://192.168.1.20:5171.';
	}
	/* Before anything is sent: an insecure address is not even asked whether it is a Sift. */
	const insecure = plainHttpRefusal(normalised);
	if (insecure !== null) return insecure;
	const reachable = await answersAsSift(normalised);
	if (reachable !== null) return reachable;

	const already = settings.servers.some((server) => server.origin === normalised);
	settings = {
		...settings,
		lastServer: normalised,
		servers: already
			? settings.servers
			: [...settings.servers, { label: normalised, origin: normalised }]
	};
	save(settings);
	connectProblem = null;
	/* Loaded from here: the window exists, and now becomes the library. */
	await mainWindow?.loadURL(normalised);
	return null;
}

/**
 * Why the saved address did not answer, for the connect screen to say, or null. Held, not saved: it
 * describes this launch only.
 */
let connectProblem: string | null = null;

/** Take one address off the saved list. The last address goes with it when it is the same one. */
function forgetServer(origin: string): { label: string; origin: string }[] {
	const normalised = normaliseOrigin(origin) ?? origin;
	settings = {
		...settings,
		servers: settings.servers.filter((server) => server.origin !== normalised),
		lastServer: settings.lastServer === normalised ? null : settings.lastServer
	};
	save(settings);
	return settings.servers;
}

/** Null when the address is a Sift; a sentence naming what went wrong when it is not. */
async function answersAsSift(origin: string): Promise<string | null> {
	try {
		const response = await net.fetch(`${origin}/health`, {
			signal: AbortSignal.timeout(10_000)
		});
		/* A 401 is a good answer (Sift is there and wants a sign-in), but only from a Sift: the
		 * response's mark and shape decide (`looksLikeSift`). */
		const body = await response.text();
		return looksLikeSift(response.status, response.headers, body);
	} catch {
		return 'Nothing answered at that address. Check that the device is on and the address is right.';
	}
}

/* --- Switching to another library ---------------------------------------------------------- */

/** Every library this copy has opened, newest first, and which of them this window is looking at. */
function libraryList(): LibraryList {
	return {
		/* Null in client mode, where there is nothing here to switch between. */
		current: backend === null ? null : locations(settings).dataDir,
		libraries: settings.libraries.map((one) => ({
			dataDir: one.dataDir,
			cacheDir: one.cacheDir,
			name: one.name,
			lastOpened: one.lastOpened
		}))
	};
}

/**
 * Open a library this copy has opened before: anything off the list is refused, and only a backend
 * started on a folder granted through the operating system's picker puts one there.
 */
async function openLibrary(dataDir: string): Promise<Settled> {
	const known = findLibrary(settings.libraries, dataDir);
	if (known === null) return { ok: false, refusal: 'Sift has not opened that library before.' };
	/* `mustBeThere`: no database where one was means the library has gone (a drive out, a share
	 * down), and starting anyway would report success over an apparently deleted collection. */
	return switchTo({ dataDir: known.dataDir, cacheDir: known.cacheDir }, { mustBeThere: true });
}

/**
 * Open a library from a database file chosen in the machine's own picker, the only thing that grants
 * a path. A library's own `sift.sqlite3` opens where it is; any other `.sqlite3` becomes a new
 * library from a copy of it (`planForDatabase`). A file, since that is what a backup in hand is.
 */
async function addLibrary(): Promise<Settled> {
	const chosen = await pickDatabaseFile();
	/* Closing the picker is neither a failure nor an answer, exactly as it is on first run. */
	if (chosen === null) return { ok: false, refusal: null };
	const plan = planForDatabase(chosen, settings.libraries);
	if (plan.kind === 'refused') return { ok: false, refusal: plan.refusal };
	if (plan.kind === 'adopt') return adoptAndOpen(plan);
	const problem = await refuseLocation(plan.locations);
	if (problem !== null) return { ok: false, refusal: problem };
	/* The file was just chosen, so `empty` is an interrupted first migration, which the start finishes. */
	return switchTo(plan.locations);
}

/**
 * The machine's own file dialog, filtered to Sift's databases, or null when closed. Kept off the
 * Windows recent list, which every application can read.
 */
async function pickDatabaseFile(): Promise<string | null> {
	const picked = await dialog.showOpenDialog({
		title: 'Choose a Sift database',
		properties: ['openFile', 'dontAddToRecent'],
		filters: [{ name: 'Sift database', extensions: ['sqlite3'] }],
		buttonLabel: 'Open'
	});
	const chosen = picked.filePaths[0];
	if (picked.canceled || chosen === undefined) return null;
	return chosen;
}

/**
 * Make a library from a database file that is not one's own, and open it.
 *
 * Nothing is made before a yes to a dialog naming the new folder; the file is read FIRST, so a
 * newer or foreign file is refused before any folder exists; the backend makes the copy and the
 * chosen file is never written; the switch then opens the copy, with its backup if it is behind.
 * The upgrade question is asked once, here (`agreed`).
 */
async function adoptAndOpen(plan: Extract<DatabasePlan, { kind: 'adopt' }>): Promise<Settled> {
	if (backend === null) {
		return {
			ok: false,
			refusal: 'Sift is not running its own library on this device.'
		};
	}
	const report = await inspectDatabase(plan.source);
	const refusal = refusalForFile(report);
	if (refusal !== undefined) return { ok: false, refusal };

	/* NEVER INTO A FOLDER THAT IS THERE: it most likely holds this file's library from last time. */
	if (fs.existsSync(plan.root)) {
		return {
			ok: false,
			refusal:
				`There is already a folder called ${plan.stem} next to that file, so Sift did not create ` +
				`another. To open the library in it, choose the sift.sqlite3 in its data folder.`
		};
	}

	const older = report.verdict === 'older';
	const MAKE = 0;
	const chosen = await dialog.showMessageBox({
		type: older ? 'warning' : 'question',
		title: 'Open this database?',
		message: `Sift will create a library folder called ${plan.stem} next to this file.`,
		detail:
			'It copies the database into that folder and opens the copy. The file you chose is not ' +
			'changed.' +
			(older
				? ' The copy was last opened by an older Sift, so opening it upgrades it in one ' +
					'direction; that Sift will not read it afterwards. Sift backs it up first.'
				: ''),
		buttons: [older ? 'Upgrade and open' : 'Create folder and open', 'Cancel'],
		defaultId: MAKE,
		cancelId: 1,
		noLink: true
	});
	if (chosen.response !== MAKE) return { ok: false, refusal: null };

	/* The first-run folder checks, which also make the two folders. */
	const problem = await refuseLocation(plan.locations);
	if (problem !== null) {
		unmakeEmpty(plan.root, plan.locations);
		return { ok: false, refusal: problem };
	}
	const made = await adoptDatabase(plan.source, plan.locations);
	if (made === null) {
		unmakeEmpty(plan.root, plan.locations);
		return {
			ok: false,
			refusal:
				'Sift could not copy that database into a new library folder, so nothing has been opened.'
		};
	}
	log.info('library.adopted', { from: plan.source, into: made });
	return switchTo(plan.locations, { mustBeThere: true, agreed: true });
}

/* What each reading of a CHOSEN FILE says, or undefined to carry on: no "new" or "gone" readings for
 * a file just picked, and `newer` is the folder's own sentence. */
function refusalForFile(report: LibraryReport): string | undefined {
	const verdict = report.verdict;
	if (verdict === 'current' || verdict === 'older') return undefined;
	if (verdict === 'newer') return NEWER_REFUSAL;
	if (verdict === 'empty')
		return 'That file is not a Sift library, so there is nothing in it to open.';
	if (verdict === 'unreadable') {
		return report.detail || 'That file is not a Sift library this copy can read.';
	}
	return 'Sift could not read that file to see what is in it.';
}

const NEWER_REFUSAL =
	'That library was last opened by a newer version of Sift than this one. Update Sift, ' +
	'then open it again. Nothing has been changed.';

/* An older library asked for from another computer: its upgrade dialog is on this screen. */
const OLDER_FROM_AFAR =
	'That library was last opened by an older Sift, and opening it upgrades it. Open it in the Sift ' +
	'app on the computer running Sift, which asks first. Nothing has been changed.';

/**
 * Stop the backend, point it at another library, and start it again.
 *
 * THE ORDER IS THE WHOLE OF THE SAFETY, as for `move`: ask, decide, stop, write, start. Asking while
 * the old library is up makes a refusal free; the settings are written only once the new backend
 * answers; a failed start puts the old library back.
 */
async function switchTo(
	target: DataLocations,
	{ mustBeThere = false, agreed = false }: { mustBeThere?: boolean; agreed?: boolean } = {}
): Promise<Settled> {
	if (backend === null) {
		return {
			ok: false,
			refusal: 'Sift is not running its own library on this device.'
		};
	}
	const open = locations(settings);
	if (sameFolder(open.dataDir, target.dataDir)) return { ok: true, refusal: null };

	const refusal = await settleSchema(target, mustBeThere, agreed);
	if (refusal !== undefined) return { ok: false, refusal };
	return restartOn(target);
}

/**
 * Stop the backend if it is running, start one on `target`, and load it, or put the old one back:
 * one copy of what a switch DOES, for this shell's switch and the one the server asked for.
 */
async function restartOn(target: DataLocations): Promise<Settled> {
	if (backend === null) {
		return {
			ok: false,
			refusal: 'Sift is not running its own library on this device.'
		};
	}
	await backend.stop();
	const before = settings;
	settings = {
		...settings,
		dataDir: target.dataDir,
		cacheDir: target.cacheDir
	};
	try {
		await startBackend();
	} catch (err) {
		log.warning('library.switch_failed', {
			library: target.dataDir,
			reason: err instanceof Error ? err.message : String(err)
		});
		/* Back to the library that was open, untouched, so a genuine return. */
		settings = before;
		await startBackend().catch(() => showStartFailure(err));
		return {
			ok: false,
			refusal: `Sift could not open that library. ${err instanceof Error ? err.message : String(err)}`
		};
	}

	/* `startBackend` already wrote them; said again here where the decision is made. */
	save(settings);
	recordForUninstaller(locations(settings));
	log.info('library.switched', { from: before.dataDir ?? '', library: target.dataDir });
	/* The new backend starts with every saved secret sealed, as a launch does, so the stale sign-in
	   goes too (`requireSignIn`). */
	await requireSignIn(LOCAL_ORIGIN);
	await mainWindow?.loadURL(LOCAL_ORIGIN);
	return { ok: true, refusal: null };
}

/**
 * Whether a backend that has just stopped asked for ANOTHER LIBRARY, and if so, start that one; true
 * means this has taken the restart over. The server checked it, backed it up and left a note
 * (`takeSwitchNote`), read synchronously before anything could restart the old library.
 */
function takeOverRestart(): boolean {
	const target = takeSwitchNote(locations(settings).dataDir);
	if (target === null) return false;
	void switchTheServerAsked(target);
	return true;
}

/**
 * Start on the library the server asked for: the schema was settled on the page, so only this
 * machine's checks run (network drive, writable). A refusal restarts the library that was open.
 */
async function switchTheServerAsked(target: DataLocations): Promise<void> {
	log.info('library.switch_asked_by_server', {});
	const problem = await refuseLocation(target);
	const outcome = problem === null ? await restartOn(target) : { ok: false, refusal: problem };
	if (outcome.ok) return;
	log.warning('library.switch_refused', { refusal: outcome.refusal ?? '' });
	/* A refusal before `restartOn` ran leaves no backend, so the old library is started here. */
	if (problem !== null) {
		await startBackend().catch((err) => showStartFailure(err));
		await requireSignIn(LOCAL_ORIGIN);
		await mainWindow?.loadURL(LOCAL_ORIGIN);
	}
}

/**
 * Whether the target's schema lets it be opened, asking first where the answer is a decision:
 * `undefined` carries on, a string is the sentence to show, null is somebody saying no.
 *
 * The upgrade warning shows once with no flag: an upgraded library is no longer behind, and a flag
 * would stay set through a restore of an older backup. `agreed` callers already asked (the backup
 * is still made). `fromAfar` cannot see this screen's dialog, so an older library is refused in
 * words.
 */
async function settleSchema(
	target: DataLocations,
	mustBeThere: boolean,
	agreed = false,
	fromAfar = false
): Promise<string | null | undefined> {
	const report = await inspectLibrary(target);
	if (report.verdict === 'empty') {
		/* Empty is NEW when just pointed at, and GONE when this copy opened it before. */
		return mustBeThere
			? 'That library is not there any more. If it is on a drive or a share, check it is connected.'
			: undefined;
	}
	if (report.verdict === 'current') return undefined;
	if (report.verdict === 'newer') return NEWER_REFUSAL;
	/* The reading's own sentence where it has one: it says how to open an older library. */
	if (report.verdict === 'unreadable') {
		return report.detail || 'There is no Sift library in that folder that this copy can read.';
	}
	if (report.verdict === 'unknown') {
		return 'Sift could not read that folder to see what is in it.';
	}
	if (fromAfar) return OLDER_FROM_AFAR;

	const UPGRADE = 0;
	const chosen = agreed
		? { response: UPGRADE }
		: await dialog.showMessageBox({
				type: 'warning',
				title: 'Open this library?',
				message: 'This library was last opened by an older Sift.',
				detail:
					'Opening it upgrades it in one direction; that Sift will not read it afterwards. Sift ' +
					'backs it up first.',
				buttons: ['Upgrade and open', 'Cancel'],
				defaultId: UPGRADE,
				cancelId: 1,
				noLink: true
			});
	if (chosen.response !== UPGRADE) return null;

	const copy = await backUpLibrary(target);
	/* A REFUSAL, not a warning: an upgrade promised a way back. */
	if (copy === null) {
		return 'Sift could not back up that library, so it has not been opened.';
	}
	log.info('library.backed_up', { copy });
	return undefined;
}

async function startBackend(): Promise<void> {
	/* Read HERE, once: this is when the listening address is decided for this launch. */
	liveSharing = settings.shareOnNetwork;
	shellLink ??= await openShellLink({
		machine: () => machineName(),
		startup: startWithWindows(app, process.execPath),
		/* The same four facts the page's own sharing verb reads, from the same variables. */
		sharing: () => ({
			enabled: settings.shareOnNetwork,
			live: liveSharing,
			address: shareAddress(networkAddress(), PORT),
			port: PORT
		}),
		firewall: () => firewallState(PORT),
		openFirewall: (scope) => openFirewall(PORT, undefined, scope),
		setSharing: sharingFromAfar,
		storage: async () => ({ ...(await describeStorage(locations(settings))), lastMove }),
		moveStorage: moveFromAfar,
		update: updateFromAfar,
		log: (lines) => tailShellLog(lines),
		libraries: libraryList,
		openLibrary: openLibraryFromAfar
	});
	backend = new Backend(
		locations(settings),
		(reason, crashed, code) => {
			crashDialog.offer('Sift has stopped', reason, crashed, code);
		},
		liveSharing,
		takeOverRestart,
		/* The one release feed, so the backend's update check reads this shell's own address. */
		feedAddress(settings.feedUrl),
		shellLink,
		crashDialog.holding()
	);
	const began = Date.now();
	try {
		await backend.start();
	} catch (err) {
		log.error('backend.start_failed', {
			library: locations(settings).dataDir,
			reason: err instanceof Error ? err.message : String(err)
		});
		throw err;
	}
	/* What a slow or wrong start is read back from. */
	log.info('backend.started', {
		library: locations(settings).dataDir,
		port: PORT,
		network: liveSharing,
		took_ms: Date.now() - began
	});
	crashDialog.started();
	/* RECORDED HERE, where a library is OPENED, never where one is chosen: a refused folder must not
	 * become an entry. */
	settings = {
		...settings,
		libraries: remembered(settings.libraries, locations(settings), Date.now())
	};
	save(settings);
}

/* The one that looks like deleted keys.
 *
 * The master key unsealing saved logins, stash-box keys and tunnel settings is held in memory and
 * filled from the password at sign-in, so every launch starts sealed while the browser still says
 * signed in. Discarding the stale sign-in sends the person to the ordinary sign-in, where the
 * password authenticates and unseals in one go. Client mode's backend outlives this window and is
 * left alone.
 */
async function requireSignIn(origin: string): Promise<void> {
	await session.defaultSession.cookies.remove(origin, 'sift_session');
}

/*
 * How tall the strip holding the caption buttons is: `WindowBar`'s height. `--window-chrome` in
 * `app.css` is the same 36; one is read before any page loads and, in client mode, from another
 * computer, so they cannot be one declaration, and a mismatch only nudges the buttons.
 */
const TITLE_BAR_HEIGHT = 36;

function createWindow(): BrowserWindow {
	const icon = appIconFile();
	const window = new BrowserWindow({
		width: 1400,
		height: 900,
		minWidth: 960,
		minHeight: 600,
		show: false,
		// The page's own canvas colour as the last start saw it, so nothing flashes before it paints.
		backgroundColor: frame.look.canvas,
		autoHideMenuBar: true,
		/*
		 * NO OPERATING-SYSTEM TITLE BAR: a fixed-colour caption strip reads as somebody else's window.
		 * `hidden` keeps the three buttons and drops the strip; these colours are the default theme's,
		 * for the first paint, until the page repaints them (`SET_TITLE_BAR` in `verbs.ts`).
		 */
		titleBarStyle: 'hidden',
		titleBarOverlay: {
			color: '#1e2024',
			symbolColor: '#eef1f6',
			height: TITLE_BAR_HEIGHT
		},
		/* Spread: the project forbids passing `undefined` to an optional field. Packaged builds take
		   the icon off the executable. */
		...(icon === undefined ? {} : { icon }),
		webPreferences: {
			preload: path.join(__dirname, 'preload.js'),
			// The three that matter, none inherited silently: no Node, an isolated world, sandboxed.
			nodeIntegration: false,
			contextIsolation: true,
			sandbox: true,
			webSecurity: true
		}
	});

	/* Presses in this window, as the operating system delivers them: a page from another computer
	 * reads the clipboard only just after one (`READ_CLIPBOARD` in verbs.ts). A page that is not
	 * Sift goes to the real browser and never inherits the preload. */
	watchGestures(window.webContents);

	window.webContents.setWindowOpenHandler(({ url }) => {
		openOutside(url);
		return { action: 'deny' };
	});

	window.webContents.on('will-navigate', (event, url) => {
		const allowed = isDevShell ? url.startsWith(DEV_ORIGIN) : isTrusted(settings, url);
		if (allowed) return;
		event.preventDefault();
		openOutside(url);
	});

	/*
	 * THE CLOSE BUTTON IS NOT THE QUIT BUTTON, unless somebody has said it should be: a closed window
	 * may still be scanning, downloading or serving another computer, so the setting defaults on.
	 * `preventDefault`, not `destroy`, so reopening is instant. `tray.ts` decides; main.test.ts
	 * presses the button.
	 */
	window.on('close', (event) => {
		if (whatClosingDoes(settings.keepRunningWhenClosed, quitting) !== 'hide') return;
		event.preventDefault();
		window.hide();
	});

	window.on('closed', () => {
		mainWindow = null;
	});

	/* The page gone (a renderer crash): only the shell's log can say so. */
	window.webContents.on('render-process-gone', (_event, details) => {
		log.error('window.page_gone', { reason: details.reason, code: details.exitCode });
	});

	return window;
}

function showStartFailure(err: unknown): void {
	log.error('shell.start_failed', { error: err instanceof Error ? err.message : String(err) });
	if (err instanceof BackendStartError) {
		crashDialog.offer(err.message, err.detail, err.crashed, err.code);
		return;
	}
	crashDialog.offer(
		'Sift could not start',
		err instanceof Error ? `${err.message}\n\n${err.stack ?? ''}` : String(err)
	);
}

/* Windows quits when the last window closes. Stopping the backend first keeps the database's
 * write-ahead log folded, so the next start is quiet. */
app.on('window-all-closed', () => {
	void (async () => {
		await backend?.stop();
		app.quit();
	})();
});

/* THE ONE PLACE `quitting` IS SET: every route out passes through here, so none can be missed. */
app.on('before-quit', () => {
	if (!quitting) log.info('shell.quitting', {});
	quitting = true;
	void backend?.stop();
});
