/* What the page is allowed to ask the machine to do.
 *
 * This runs with access to Node, in the same world as the page, so everything exposed here is
 * reachable by anything running on that page. It is therefore deliberately a list of named verbs
 * and never a general capability: no file paths in, no arbitrary commands, nothing that takes a
 * function.
 *
 * It is attached only to origins the person has saved (see origins.ts). On any other page there is
 * no preload at all, so `window.sift` is simply absent and every capability in
 * frontend/src/lib/bridge reports false, which is exactly what it does in an ordinary browser.
 *
 * And a page gets only the methods its origin may use: all of them on this machine's own Sift, a
 * few on a server elsewhere (see `REMOTE_VERBS` in verbs.ts). A method left off reads as a
 * capability this copy does not have, so the screens that offer it draw nothing.
 *
 * Each capability appears here as it is actually built, one method at a time, and the
 * page feature-detects each one independently, so a version that has one and not another reports
 * exactly what it has.
 */

import type { CaptureArea, SetupStep } from '../../shared/bridge';
import { contextBridge, ipcRenderer } from 'electron';

/* This file imports nothing but `electron`, and that is a hard constraint.
 *
 * A preload in an isolated renderer has no module loader for the application's own files.
 * `require('./channels')` is not available to it: only `electron` and a handful of Node
 * built-ins are. So a relative import here does not fail loudly: the preload throws while
 * loading, nothing surfaces, and `window.sift` is undefined. The application opens, reports every
 * capability as false, and has quietly lost the entire bridge.
 *
 * That is why the channel names are written out here instead of imported from the module that
 * implements them. `preload.contract.test.ts` compares the two copies and fails the build if they
 * ever differ.
 */
const BRIDGE_VERBS = 'sift:bridgeVerbs';
const CHOOSE_FOLDER = 'sift:chooseFolder';
const CHOOSE_FILE = 'sift:chooseFile';
const START_DRAG = 'sift:startDrag';
const READ_CLIPBOARD = 'sift:readClipboard';
const CAPTURE_WINDOW = 'sift:captureWindow';
const DRAG_PROGRESS = 'sift:dragProgress';
const SETUP_PROGRESS = 'sift:setupProgress';
const APPLY_UPDATE = 'sift:applyUpdate';
const LOCAL_HARDWARE = 'sift:localHardware';
const MACHINE_NAME = 'sift:machineName';
const SAVE_SERVER = 'sift:saveServer';
const LAST_SERVER = 'sift:lastServer';
const CONNECT_STATE = 'sift:connectState';
const FORGET_SERVER = 'sift:forgetServer';
const GET_SHARING = 'sift:getSharing';
const SET_SHARING = 'sift:setSharing';
const SHELL_VERSION = 'sift:shellVersion';
const SHELL_LOG = 'sift:shellLog';
const SHELL_LOG_DETAIL = 'sift:shellLogDetail';
const SAVE_LOG_ARCHIVE = 'sift:saveLogArchive';
const GET_FIREWALL = 'sift:getFirewall';
const OPEN_FIREWALL = 'sift:openFirewall';
const LIST_BROWSERS = 'sift:listBrowsers';
const SET_BROWSER = 'sift:setBrowser';
const GET_STORAGE = 'sift:getStorage';
const MOVE_STORAGE = 'sift:moveStorage';
const STORAGE_PROGRESS = 'sift:storageProgress';
const FORGET_MODE = 'sift:forgetMode';
const GET_DOWNLOAD_DIR = 'sift:getDownloadDir';
const SET_DOWNLOAD_DIR = 'sift:setDownloadDir';
const SHOW_IN_FOLDER = 'sift:showInFolder';
const SET_TITLE_BAR = 'sift:setTitleBar';
const WINDOW_STAGE = 'sift:windowStage';
const GET_KEEP_RUNNING = 'sift:getKeepRunning';
const SET_KEEP_RUNNING = 'sift:setKeepRunning';
const GET_START_WITH_WINDOWS = 'sift:startsWithWindows';
const SET_START_WITH_WINDOWS = 'sift:startWithWindows';
const LIST_LIBRARIES = 'sift:libraries';
const OPEN_LIBRARY = 'sift:openLibrary';
const ADD_LIBRARY = 'sift:addLibrary';
const FORGET_LIBRARY = 'sift:forgetLibrary';
const CHOOSE_MODE = 'sift:chooseMode';
const SUGGESTED_LIBRARY = 'sift:suggestedLibrary';
const CHOOSE_LIBRARY = 'sift:chooseLibrary';
const SETUP_BACK = 'sift:setupBack';
const RESTART_APP = 'sift:restartApp';

/** The end of the shell's own log. Declared here rather than imported, for the reason every other
 *  shape in this file is: a preload may import nothing but `electron`. */
interface ShellLog {
	lines: string[];
	path: string;
	size: number;
	present: boolean;
}

/** How a long fetch reports itself while it runs. */
interface DragProgress {
	assetId: string;
	received: number;
	total: number | null;
}

/** Whether the library is offered to the network, and the address to type on the other computer. */
/** Sift's own two folders, and what they hold. Written out here rather than imported (see the
 *  note above about what a relative import costs an isolated preload). */
interface StorageReport {
	dataDir: string;
	cacheDir: string;
	dataBytes: number;
	cacheBytes: number;
	measuredAt: number | null;
	measuring: boolean;
}

/** How far a move has got. `total` is measured before it starts, so it does not move. */
interface MoveProgress {
	copied: number;
	total: number;
}

/** `reason: null` is the person closing the folder picker, which is not a failure. */
type MoveOutcome =
	| {
			ok: true;
			locations: { dataDir: string; cacheDir: string };
			renamed: boolean;
	  }
	| { ok: false; reason: string | null };

/** One library this copy has opened, as the Library screen lists it. Written out here rather than
 *  imported (see the note above about what a relative import costs an isolated preload). */

interface Sharing {
	enabled: boolean;
	live: boolean;
	mode: 'standalone' | 'client' | null;
	address: string | null;
	/** The port the library answers on, so the screen names one number rather than keeping its own. */
	port: number;
}

/** Whether Windows lets other computers reach that port. `unknown` means it could not be asked. */
type SavedServer = { label: string; origin: string };
type ConnectState = {
	last: string | null;
	problem: string | null;
	servers: SavedServer[];
};

type FirewallReport = {
	state: 'open' | 'closed' | 'unknown';
	networks: ('Private' | 'Public' | 'Domain')[] | null;
	scope: 'private' | 'any' | null;
};

/* What the computer this window is running on is.
 *
 * DECLARED HERE rather than imported, like everything else in this file: an isolated preload cannot
 * import any of the project's own modules, so the shapes it carries are written out and the main
 * process's copy is what they are checked against at the far end. */
interface LocalMachine {
	cpu_model: string | null;
	thread_count: number;
	installed_ram_bytes: number | null;
	gpu_cards: { name: string | null; vram_bytes: number | null }[];
}

/** An area of the page in its own CSS pixels, as `getBoundingClientRect` gives one. */
/** An area as four plain numbers, copied out of whatever the page passed, or null for none. */
function areaOf(area: unknown): CaptureArea | null {
	if (typeof area !== 'object' || area === null) return null;
	const { x, y, width, height } = area as Record<string, unknown>;
	if (![x, y, width, height].every((one) => typeof one === 'number')) return null;
	return {
		x: x as number,
		y: y as number,
		width: width as number,
		height: height as number
	};
}

/* The shape frontend/src/lib/bridge/index.ts feature-detects against. Every method is optional
 * THERE, so adding one here is the whole of shipping a capability: nothing has to be told. */
const api = {
	/** Present only inside the desktop shell. Nothing branches on it today; it is what makes a
	 *  "you are running the app" statement checkable rather than guessed. */
	isDesktop: true as const,

	/* The folder dialog that grants a library root. It takes NOTHING (no starting
	 * path, no filter, no title), so a page cannot use it to steer somebody towards a particular
	 * folder or to learn anything about the disk. It answers a path or nothing at all. */
	chooseFolder: (): Promise<string | null> => ipcRenderer.invoke(CHOOSE_FOLDER),

	/* The file dialog Migrate from Stash reads a database through. It takes NOTHING, as the folder
	 * dialog does: the kind of file offered is fixed on the other side, so a page cannot steer the
	 * dialog or learn about the disk by asking. It answers a path or nothing at all, and the server
	 * reads it only inside a folder Sift has been given. */
	chooseFile: (): Promise<string | null> => ipcRenderer.invoke(CHOOSE_FILE),

	/* Drag one asset out of the window. AN ID, NEVER A PATH.
	 *
	 * That is the whole security design of this verb: a page that could name a file could name any
	 * file on the machine, and anything that got code into the page could then drag somebody's
	 * documents into a chat window. The main process resolves the id against the server the page is
	 * already signed in to, so a drag reaches exactly what the person could already watch. */
	startDrag: (assetId: string): Promise<string> => ipcRenderer.invoke(START_DRAG, assetId),

	/* How a client-mode fetch is getting on. Wrapped rather than handed `ipcRenderer.on` directly:
	 * the event object carries a `sender` that would give the page the whole IPC surface. Only the
	 * payload crosses, and the returned function is how a component stops listening. */
	onDragProgress: (listen: (progress: DragProgress) => void): (() => void) => {
		const forward = (_event: unknown, progress: DragProgress) => listen(progress);
		ipcRenderer.on(DRAG_PROGRESS, forward);
		return () => {
			ipcRenderer.removeListener(DRAG_PROGRESS, forward);
		};
	},

	/* What is on the clipboard, asked of the operating system rather than of the browser. The
	 * browser's own clipboard read needs a secure context, which a Sift reached over plain http on
	 * a LAN does not have, so this is the verb that makes a Paste button possible there. */
	readClipboard: (): Promise<{ text: string; image: string | null } | null> =>
		ipcRenderer.invoke(READ_CLIPBOARD),

	/* A picture of this window, or of one area of it, as PNG bytes. The area is four numbers in the
	 * page's own pixels and nothing else crosses: anything that is not one is the whole window. The
	 * page saves the picture itself, through the same download door as every other file. */
	captureWindow: (area: CaptureArea | null): Promise<Uint8Array | null> =>
		ipcRenderer.invoke(CAPTURE_WINDOW, areaOf(area)),

	/* Client mode's first run: which computer the library is on. It answers null when the address
	 * was saved and a sentence when it was not: the shell is the side that knows why. */
	saveServer: (origin: string): Promise<string | null> => ipcRenderer.invoke(SAVE_SERVER, origin),
	lastServer: (): Promise<string | null> => ipcRenderer.invoke(LAST_SERVER),
	/* The whole of what the connect screen draws: the address last tried, the sentence saying why
	 * it did not answer (when that is why the screen is up), and every address saved. And taking
	 * one off that list: the way back from a server that has moved or gone. */
	connectState: (): Promise<ConnectState> => ipcRenderer.invoke(CONNECT_STATE),
	forgetServer: (origin: string): Promise<SavedServer[]> =>
		ipcRenderer.invoke(FORGET_SERVER, origin),

	/* THE TWO QUESTIONS BEFORE THERE IS A BACKEND, asked on Sift's own screens rather than in an
	 * operating-system message box. Each answers whether it took, and a sentence when it did not:
	 * the shell is the side that knows why, exactly as with the address above. */
	chooseMode: (mode: 'standalone' | 'client'): Promise<{ ok: boolean; refusal: string | null }> =>
		ipcRenderer.invoke(CHOOSE_MODE, mode),
	/* The folder offered first, and whether a library is already there to carry on with. */
	suggestedLibrary: (): Promise<{ path: string; existing: boolean } | null> =>
		ipcRenderer.invoke(SUGGESTED_LIBRARY),
	/* `pick` false takes the suggested folder; true opens the machine's own dialog, which is the one
	 * part of first run that stays in the operating system: a page cannot open, drive or read it,
	 * and that is what makes the folder one somebody physically pointed at. */
	chooseLibrary: (pick: boolean): Promise<{ ok: boolean; refusal: string | null }> =>
		ipcRenderer.invoke(CHOOSE_LIBRARY, pick),
	/* Each step of taking that folder as it starts, so the screen says what the wait is. */
	onSetupProgress: (listen: (step: SetupStep) => void): (() => void) => {
		const relay = (_event: unknown, step: SetupStep) => listen(step);
		ipcRenderer.on(SETUP_PROGRESS, relay);
		return () => ipcRenderer.removeListener(SETUP_PROGRESS, relay);
	},
	/* Back a question. Offered only by the screens that HAVE one before them, which is why there is
	 * no screen name in the call: the shell works out what is outstanding, exactly as it does going
	 * forwards. */
	setupBack: (): Promise<{ ok: boolean; refusal: string | null }> => ipcRenderer.invoke(SETUP_BACK),

	/* Update this application. IT TAKES NOTHING: no address, no version, no file.
	 *
	 * That is deliberate and it is the difference between an update button and remote code
	 * execution. The shell reads the release feed itself and refuses to launch anything whose
	 * SHA-256 does not match a hash file signed by Sift's own key, so the worst a page can do here
	 * is ask for the update it would have got anyway. */
	applyUpdate: (): Promise<{
		ok: boolean;
		version?: string;
		reason?: string;
	}> => ipcRenderer.invoke(APPLY_UPDATE),

	/* What the computer this window is running on is. Answers null where there is nothing to add:
	 * on the machine running the library, the hardware block already describes it. Read-only, and
	 * about the machine rather than the person: a processor name, a thread count and a memory size.
	 */
	localHardware: (): Promise<LocalMachine | null> => ipcRenderer.invoke(LOCAL_HARDWARE),

	/* What this computer is called, for the name a phone's remote lists this window under. Takes
	 * nothing and sets nothing: it is the name the computer already gives its network. */
	machineName: (): Promise<string | null> => ipcRenderer.invoke(MACHINE_NAME),

	/* Whether this library is offered to the rest of the network, and where to reach it.
	 *
	 * Reading it is harmless; SETTING it is the one verb here that changes what the machine exposes
	 * to anything outside it, which is why it takes a plain boolean and nothing else. There is no
	 * address to pass, no port, no interface to name. The shell decides all of that, so the worst
	 * a page can ask for is the switch a person could have flicked themselves. */
	getSharing: (): Promise<Sharing | null> => ipcRenderer.invoke(GET_SHARING),
	setSharing: (on: boolean): Promise<Sharing | null> => ipcRenderer.invoke(SET_SHARING, on),

	/* What version THIS copy of Sift is, which in client mode the server cannot say; null from a
	 * checkout. */
	shellVersion: (): Promise<string | null> => ipcRenderer.invoke(SHELL_VERSION),
	shellLog: (lines: number): Promise<ShellLog | null> => ipcRenderer.invoke(SHELL_LOG, lines),
	/* The library's Detail and Hide personal details settings, handed to the shell's own log so it
	 * writes what the library's log writes. The answer is what the log now does. */
	shellLogDetail: (detailed: boolean, hidePersonal?: boolean): Promise<boolean | null> =>
		ipcRenderer.invoke(SHELL_LOG_DETAIL, detailed, hidePersonal),
	/* Download log: the path of the archive made, or null. Only the name crosses. */
	saveLogArchive: (name: string): Promise<{ file: string } | { reason: string }> =>
		ipcRenderer.invoke(SAVE_LOG_ARCHIVE, name),

	/* Whether Windows is letting anything through to that port, and asking it to. BOTH TAKE
	 * NOTHING: the port is the shell's own and the rule is written in the shell, so the most this
	 * can ask for is the one rule Sift would ever create. Opening it raises Windows' own
	 * administrator prompt, which this application cannot answer on somebody's behalf. */
	firewall: (): Promise<FirewallReport> => ipcRenderer.invoke(GET_FIREWALL),
	openFirewall: (scope?: string): Promise<FirewallReport> =>
		ipcRenderer.invoke(OPEN_FIREWALL, scope),

	/* Where links open. Reading the list is a question about this machine; choosing takes an id out
	 * of that same list and nothing else: the page cannot name an arbitrary program to run, which
	 * is the property that makes "let the page pick a browser" safe to offer at all. */
	listBrowsers: (): Promise<{
		chosen: string | null;
		browsers: { id: string; name: string }[];
	}> => ipcRenderer.invoke(LIST_BROWSERS),
	setBrowser: (
		id: string | null
	): Promise<{
		chosen: string | null;
		browsers: { id: string; name: string }[];
	}> => ipcRenderer.invoke(SET_BROWSER, id),

	/* Where a saved file lands. Reading it is a question about this machine; changing it opens the
	 * operating system's folder picker, so the page cannot name a folder for this application to
	 * write into. It can only ask for the picker, exactly as the library-folder grant does. The
	 * one value it may pass is `null`, meaning "back to the machine's own Downloads". */
	downloadFolder: (): Promise<{ path: string; chosen: boolean } | null> =>
		ipcRenderer.invoke(GET_DOWNLOAD_DIR),
	chooseDownloadFolder: (reset: null | true): Promise<{ path: string; chosen: boolean } | null> =>
		ipcRenderer.invoke(SET_DOWNLOAD_DIR, reset === true ? true : null),

	/* A backup Sift saved on this computer, shown in its folder in the system's own file manager.
	 * Showing a file opens nothing and runs nothing, and the shell shows only a Sift backup that is
	 * on this disk. Only a window onto this computer's own Sift is offered it: a backup saved on
	 * another computer is not in any folder here. */
	showInFolder: (path: string): Promise<boolean> =>
		ipcRenderer.invoke(SHOW_IN_FOLDER, typeof path === 'string' ? path : ''),

	/* The window's own caption buttons, painted in the colours the page is painting itself in.
	 *
	 * TWO COLOURS AND NOTHING ELSE, and that is the whole of what makes it safe to offer. The
	 * caption buttons belong to the operating system; a verb that could move them, resize them, or
	 * hide them would be a verb for making a window that cannot be closed. This can change what
	 * shade they are drawn in, which is a thing the person could achieve by picking a different
	 * theme, and both values are refused at the far end unless they are plain hex colours. */
	/* Sift's own two folders: where they are, how big they are, and moving them.
	 *
	 * `moveStorage` takes NO path. The folder is chosen in the operating system's own picker, on the
	 * other side of this bridge: a page that could name the folder a library moves into would be a
	 * page choosing where this application writes, which is the property the whole bridge is built
	 * on. */
	storage: (): Promise<StorageReport | null> => ipcRenderer.invoke(GET_STORAGE),
	moveStorage: (): Promise<MoveOutcome> => ipcRenderer.invoke(MOVE_STORAGE),
	onStorageProgress: (listen: (progress: MoveProgress) => void): (() => void) => {
		const relay = (_event: unknown, progress: MoveProgress) => listen(progress);
		ipcRenderer.on(STORAGE_PROGRESS, relay);
		return () => ipcRenderer.removeListener(STORAGE_PROGRESS, relay);
	},
	/** Forget which way Sift was set up, so the next launch asks again. The library folder stays. */
	forgetMode: (): Promise<boolean> => ipcRenderer.invoke(FORGET_MODE),
	/** Close Sift and open it again. True once it is on its way; the page goes with it. */
	restartApp: (): Promise<boolean> => ipcRenderer.invoke(RESTART_APP),

	/* Whether closing the window leaves Sift running in the notification area. A plain boolean in
	 * each direction: there is no window to name, nothing to hide and nothing to show, so the most
	 * a page can ask for is the switch a person could have flicked themselves. */
	keepRunningWhenClosed: (): Promise<boolean | null> => ipcRenderer.invoke(GET_KEEP_RUNNING),
	keepRunning: (on: boolean): Promise<boolean | null> => ipcRenderer.invoke(SET_KEEP_RUNNING, on),

	/* Whether Sift starts when this person signs in to Windows. A plain boolean in each direction,
	 * like the switch above: no program to name, no argument to pass. The shell registers its own
	 * executable and nothing else, so the most a page can ask for is the switch a person could have
	 * flicked in Task Manager. Null from a checkout, which has no installed program to register. */
	startsWithWindows: (): Promise<boolean | null> => ipcRenderer.invoke(GET_START_WITH_WINDOWS),
	startWithWindows: (on: boolean): Promise<boolean | null> =>
		ipcRenderer.invoke(SET_START_WITH_WINDOWS, on),

	/* The libraries this copy has opened, and switching to one.
	 *
	 * `openLibrary` names a folder ALREADY ON THAT LIST and the shell refuses anything else, so a
	 * page cannot point this application's backend at a folder of its choosing. `addLibrary` takes
	 * NOTHING and opens the operating system's own file picker for a Sift database, the only thing
	 * that can hand over a path. */
	libraries: (): Promise<{
		current: string | null;
		libraries: {
			dataDir: string;
			cacheDir: string;
			name: string;
			lastOpened: number;
		}[];
	} | null> => ipcRenderer.invoke(LIST_LIBRARIES),
	openLibrary: (dataDir: string): Promise<{ ok: boolean; refusal: string | null }> =>
		ipcRenderer.invoke(OPEN_LIBRARY, dataDir),
	addLibrary: (): Promise<{ ok: boolean; refusal: string | null }> =>
		ipcRenderer.invoke(ADD_LIBRARY),
	forgetLibrary: (
		dataDir: string
	): Promise<{
		current: string | null;
		libraries: {
			dataDir: string;
			cacheDir: string;
			name: string;
			lastOpened: number;
		}[];
	} | null> => ipcRenderer.invoke(FORGET_LIBRARY, dataDir),
	setTitleBar: (colors: { color: string; symbolColor: string }): Promise<boolean> =>
		ipcRenderer.invoke(SET_TITLE_BAR, colors),
	windowStage: (
		stage: 'painted' | 'usable',
		look: { theme: string | null; canvas: string }
	): Promise<boolean> => ipcRenderer.invoke(WINDOW_STAGE, stage, look)
};

/* The channel each method speaks on, so the list the main process answers can be applied here. */
const CHANNEL_OF: Record<Exclude<keyof typeof api, 'isDesktop'>, string> = {
	chooseFolder: CHOOSE_FOLDER,
	chooseFile: CHOOSE_FILE,
	startDrag: START_DRAG,
	onDragProgress: DRAG_PROGRESS,
	readClipboard: READ_CLIPBOARD,
	captureWindow: CAPTURE_WINDOW,
	saveServer: SAVE_SERVER,
	lastServer: LAST_SERVER,
	connectState: CONNECT_STATE,
	forgetServer: FORGET_SERVER,
	chooseMode: CHOOSE_MODE,
	suggestedLibrary: SUGGESTED_LIBRARY,
	chooseLibrary: CHOOSE_LIBRARY,
	onSetupProgress: SETUP_PROGRESS,
	setupBack: SETUP_BACK,
	applyUpdate: APPLY_UPDATE,
	localHardware: LOCAL_HARDWARE,
	machineName: MACHINE_NAME,
	getSharing: GET_SHARING,
	setSharing: SET_SHARING,
	shellVersion: SHELL_VERSION,
	shellLog: SHELL_LOG,
	shellLogDetail: SHELL_LOG_DETAIL,
	saveLogArchive: SAVE_LOG_ARCHIVE,
	firewall: GET_FIREWALL,
	openFirewall: OPEN_FIREWALL,
	listBrowsers: LIST_BROWSERS,
	setBrowser: SET_BROWSER,
	downloadFolder: GET_DOWNLOAD_DIR,
	chooseDownloadFolder: SET_DOWNLOAD_DIR,
	showInFolder: SHOW_IN_FOLDER,
	storage: GET_STORAGE,
	moveStorage: MOVE_STORAGE,
	onStorageProgress: STORAGE_PROGRESS,
	forgetMode: FORGET_MODE,
	restartApp: RESTART_APP,
	keepRunningWhenClosed: GET_KEEP_RUNNING,
	keepRunning: SET_KEEP_RUNNING,
	startsWithWindows: GET_START_WITH_WINDOWS,
	startWithWindows: SET_START_WITH_WINDOWS,
	libraries: LIST_LIBRARIES,
	openLibrary: OPEN_LIBRARY,
	addLibrary: ADD_LIBRARY,
	forgetLibrary: FORGET_LIBRARY,
	setTitleBar: SET_TITLE_BAR,
	windowStage: WINDOW_STAGE
};

/* Which channels this page may use, asked once, as the page loads and before its own scripts run.
 * The address sent is this document's own. An answer that is not a list is no methods at all. */
function permitted(): Set<string> {
	try {
		const href = typeof location === 'undefined' ? '' : location.href;
		const answer: unknown = ipcRenderer.sendSync(BRIDGE_VERBS, href);
		return new Set(Array.isArray(answer) ? answer.filter((one) => typeof one === 'string') : []);
	} catch {
		return new Set();
	}
}

const allowed = permitted();
const offered: Record<string, unknown> = { isDesktop: api.isDesktop };
for (const [method, channel] of Object.entries(CHANNEL_OF)) {
	if (allowed.has(channel)) offered[method] = api[method as keyof typeof CHANNEL_OF];
}

contextBridge.exposeInMainWorld('sift', offered);
