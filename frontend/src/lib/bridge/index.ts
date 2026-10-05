/* The desktop bridge.
 *
 * The same code runs in a browser and in the desktop client; a few things only the desktop can do
 * (dragging a file out, opening a folder), and this is where the app asks whether it can. In a
 * browser every method is a no-op and every capability false, so a feature simply lights up where
 * the bridge is present.
 */

/**
 * What came of asking to drag something out: ONE GESTURE wherever the file is, since the shell
 * offers a virtual file whose stream follows the download. A page has nothing to do differently.
 */
export type DragOutcome = 'dragged' | 'unavailable';

/** What the folder screen is offered: a path, and whether a library is already there. */
interface SuggestedLibrary {
	path: string;
	existing: boolean;
}

import type { components } from '$lib/api/schema';
import type {
	CaptureArea,
	KnownLibrary,
	LibraryList,
	Settled,
	SetupStep
} from '../../../../shared/bridge';

export type { CaptureArea };
export type { Settled, SetupStep };

/** What came of asking to update, every failure named: the server's shape for an update asked of
 *  the computer running Sift, less two fields this shell leaves out. */
type UpdateTaken = components['schemas']['UpdateTaken'];
export type UpdateOutcome = Pick<UpdateTaken, 'ok'> &
	Partial<Pick<UpdateTaken, 'version' | 'reason'>>;

/** How a fetch for a drag is getting on. `total` is null until the server says how big it is. */
/** The computer a client-mode window is running on, which is not the one holding the library. */
export interface LocalMachine {
	cpu_model: string | null;
	/** Logical processors: THREADS, not cores. A twelve-core chip with two threads each answers 24. */
	thread_count: number;
	installed_ram_bytes: number | null;
	/** Every graphics adapter in it. None of them does work for Sift; the block says so. */
	gpu_cards: { name: string | null; vram_bytes: number | null }[];
}

/**
 * The end of the shell's own log, in `/logs`' four facts, so one screen draws either; `lines` are raw
 * JSON records, matched on purpose by `desktop/src/log.ts`, so there is one parser.
 */
type ShellLog = components['schemas']['AppLog'];

export interface DragProgress {
	assetId: string;
	received: number;
	total: number | null;
}

interface NativeBridge {
	/** Can a file be dragged out of the window into another application? */
	canNativeDrag(): boolean;
	/** Begin a native drag of an asset's file. Always `unavailable` in a browser. */
	startDrag(assetId: string): Promise<DragOutcome>;
	/** Watch a fetch for a drag. Returns the function that stops watching; a no-op in a browser. */
	onDragProgress(listen: (progress: DragProgress) => void): () => void;
	/** Can the clipboard be read without a secure context? Only the desktop client can. */
	canReadClipboard(): boolean;
	/**
	 * What is on the clipboard: a link, a picture, or neither. Null in a browser, whose clipboard read
	 * needs a secure context that plain http lacks; Ctrl-V still works there, and this adds a button.
	 */
	readClipboard(): Promise<{ text: string; image: string | null } | null>;
	/** Can this shell take a picture of its own window? Only the desktop client can. */
	canCaptureWindow(): boolean;
	/**
	 * A picture of this window as a PNG, or of one area of it given in the page's own pixels (a
	 * `getBoundingClientRect`). Null in a browser, which can read the pixels of a video or a
	 * canvas but never of the page around them.
	 */
	captureWindow(area: CaptureArea | null): Promise<Blob | null>;
	/** Can this shell be told which computer the library is on? Only the desktop client can. */
	canSaveServer(): boolean;
	/**
	 * Save an address and go there. Null when it worked; a sentence to show when it did not, since
	 * the shell tried the address and saw the answer, and codes would need a second list here.
	 */
	saveServer(origin: string): Promise<string | null>;
	/** The address last tried, so a correction starts from it. Null in a browser and on a fresh run. */
	lastServer(): Promise<string | null>;
	/**
	 * Where the connect screen stands: the address last tried, why it did not answer when that is
	 * why the screen is up, and every address saved. A shell from before this verb answers the
	 * last address alone.
	 */
	connectState(): Promise<ConnectState>;
	/** Take one saved address off the list. What comes back is the list afterwards. */
	forgetServer(origin: string): Promise<SavedServer[]>;
	/**
	 * Whether this shell can be set up at all: the two questions asked before a backend exists. False
	 * in a browser, so those screens say so rather than drawing dead buttons.
	 */
	canSetUp(): boolean;
	/** Save which way this copy runs. The shell moves the window on from here. */
	chooseMode(mode: 'standalone' | 'client'): Promise<Settled>;
	/** The folder offered first, and whether a library is already there to carry on with. Null in
	 *  a browser. */
	suggestedLibrary(): Promise<SuggestedLibrary | null>;
	/** Settle the library folder: `pick` false takes the suggestion, true opens the machine's own
	 *  dialog. The shell moves the window on from here too. */
	chooseLibrary(pick: boolean): Promise<Settled>;
	/** Be told each step of taking that folder as it starts. Hands back how to stop listening; in a
	 *  browser, and on a shell that sends no steps, nothing is ever heard. */
	onSetupProgress(listen: (step: SetupStep) => void): () => void;
	/**
	 * Go back one question, and let the shell redraw whichever that turns out to be: it decides from
	 * the answers that exist, so no screen name is passed.
	 */
	setupBack(): Promise<Settled>;
	/** Can this application update itself on one click? Only the desktop client can. */
	canApplyUpdate(): boolean;
	/**
	 * Download the newest release, prove it is Sift's, and launch it. Takes NOTHING: the shell reads
	 * the feed and checks the signed hash itself; a method taking an address would be "download this
	 * and run it".
	 */
	applyUpdate(): Promise<UpdateOutcome>;
	/**
	 * What the computer this window is running on is, or null when there is nothing to add (the
	 * library's own machine, or a browser), so a block appears exactly when there is a SECOND
	 * computer.
	 */
	localHardware(): Promise<LocalMachine | null>;
	/**
	 * What the computer this window is on is called, for the phone's remote; every mode, unlike
	 * `localHardware`; null in a browser or an older shell.
	 */
	machineName(): Promise<string | null>;
	/**
	 * Whether this page is inside the desktop application at all: `localHardware`'s null means the
	 * same on the library's machine and in a browser, which want different words.
	 */
	isDesktop(): boolean;
	/** Can the operating system's own folder dialog be opened? */
	canChooseFolder(): boolean;
	/**
	 * Ask the person to choose a folder, and answer where they chose. Null if they cancelled. The
	 * operating system's dialog is the grant: no page can open, drive, read or pre-fill it. Always
	 * null in a browser, rightly.
	 */
	chooseFolder(): Promise<string | null>;
	/** Can the operating system's own file dialog be opened, for one database file? */
	canChooseFile(): boolean;
	/**
	 * Ask the person to choose one database file (Migrate from Stash's), and answer where they
	 * chose. Null if they cancelled, and always null in a browser, where the Stash folder is picked
	 * with the folder picker and the server finds the database inside it. The path grants nothing:
	 * the server reads it only inside a folder Sift has been given.
	 */
	chooseFile(): Promise<string | null>;
	/** Can this shell offer the library to the rest of the network? Only the desktop client can. */
	canShareOnNetwork(): boolean;
	/** Whether it is being offered, and the address to reach it at. Null in a browser. */
	sharing(): Promise<Sharing | null>;
	/**
	 * Turn the offer on or off, and answer the state that follows: a BOOLEAN only, so no page can
	 * choose an address, port or network card.
	 */
	setSharing(on: boolean): Promise<Sharing | null>;
	/**
	 * What version THIS copy of the application is: in client mode, not the library's, and possibly
	 * older. Null in a browser and from a checkout, where Electron would answer its own version.
	 */
	shellVersion(): Promise<string | null>;
	/**
	 * The end of the SHELL's own log (the drag, the transfers, the update, the window): this machine's,
	 * where `/logs` is the server's, another computer in client mode.
	 */
	shellLog(lines: number): Promise<ShellLog | null>;
	/**
	 * Hand the shell's own log the library's `logs.detail` and `logs.hide_personal`, so it writes
	 * what the library's log writes. The answer is whether the shell now writes the detail; null in a
	 * browser and from a shell too old to be told, which then keeps what it started with.
	 */
	shellLogDetail(detailed: boolean, hidePersonal: boolean): Promise<boolean | null>;
	/**
	 * Whether this shell can be asked for its own log AT ALL: not `isDesktop()`, which an older shell
	 * answers truthfully without having this channel.
	 */
	canReadShellLog(): boolean;
	/**
	 * Save every log of this app and of the library it runs, redacted, as one archive named `name`
	 * in its `Save files to` folder. The answer is where it went; null where it couldn't be made.
	 */
	saveLogArchive(name: string): Promise<string | null>;
	/** Whether this shell can make that archive at all. */
	canSaveLogArchive(): boolean;
	/** Whether Windows is letting other computers through to Sift, and on which networks. Unknown
	 *  outside the app. */
	firewall(): Promise<FirewallReport>;
	/**
	 * Ask Windows to let them through, and answer what is true afterwards. NOTHING IS PASSED: port
	 * and rule are the shell's. Windows' own administrator prompt decides, so a refusal is the state
	 * unchanged.
	 */
	openFirewall(scope?: FirewallScope): Promise<FirewallReport>;
	/** Can this shell choose where links open? Only the desktop client can. */
	canChooseBrowser(): boolean;
	/** Which browsers this machine has, and which one links go to. Empty in a browser. */
	browsers(): Promise<BrowserChoice>;
	/**
	 * Choose one, or null for whatever the system would have used: an ID OUT OF THE LIST, checked by
	 * the shell, since the choice is a program it will start.
	 */
	setBrowser(id: string | null): Promise<BrowserChoice>;

	/**
	 * Where Sift keeps its OWN two folders (the database and the cache) and how big they are.
	 *
	 * Null in a browser and in client mode: neither is looking at a library this computer holds.
	 */
	storage(): Promise<StorageReport | null>;
	/**
	 * Move both of them under a folder somebody chooses. TAKES NO PATH: only the picker grants one.
	 * The backend is down meanwhile; the old folder is untouched until the new one is complete.
	 */
	moveStorage(): Promise<MoveOutcome>;
	/** Told how far a move has got. Returns the way to stop listening. */
	onStorageProgress(listen: (progress: MoveProgress) => void): () => void;
	/** Whether this shell can answer the three above at all. */
	canMoveStorage(): boolean;
	/** Whether this shell offers the way back to the first-run question. */
	canForgetMode(): boolean;
	/**
	 * Forget which way Sift was set up, so the next launch asks again; the library folder is kept.
	 */
	forgetMode(): Promise<boolean>;

	/** Whether this copy of Sift can close and open itself again: the desktop app, on the device it runs on. */
	canRestartApp(): boolean;

	/** Close Sift and open it again. False where the shell cannot, and the page then says to do it by hand. */
	restartApp(): Promise<boolean>;

	/**
	 * Whether closing the window leaves Sift running in the notification area. Null where nothing
	 * true can be said (a browser, an older shell), so no switch shows a false "off".
	 */
	keepRunningWhenClosed(): Promise<boolean | null>;
	/** Say which it should be, and answer what is true afterwards. */
	setKeepRunningWhenClosed(on: boolean): Promise<boolean | null>;
	/** Whether this shell can answer the two above at all. */
	canKeepRunningWhenClosed(): boolean;

	/**
	 * Whether Sift starts when this person signs in to Windows. Off unless they turned it on; null as
	 * for `keepRunningWhenClosed`.
	 */
	startsWithWindows(): Promise<boolean | null>;
	/** Say which it should be, and answer what is true afterwards. */
	setStartsWithWindows(on: boolean): Promise<boolean | null>;
	/** Whether this shell can answer the two above at all. */
	canStartWithWindows(): boolean;

	/**
	 * Every library this copy of Sift has opened, and which of them this window is looking at
	 * (`current` null in client mode). Null in a browser.
	 */
	libraries(): Promise<LibraryList | null>;
	/**
	 * Open one of them: a folder ALREADY ON THAT LIST, so no page can aim the backend elsewhere. "Not
	 * ok" with nothing to say is somebody declining the shell's upgrade question.
	 */
	openLibrary(dataDir: string): Promise<Settled>;
	/**
	 * Open a library from a database file somebody chooses in the machine's own file picker. Takes
	 * nothing; the shell decides what the file is (its own `sift.sqlite3` opens in place, any other
	 * is copied into a new folder after a dialog). "Not ok" alone is a closed picker or a no.
	 */
	addLibrary(): Promise<Settled>;
	/** Take one off the list. The library itself is untouched; this forgets the shortcut. */
	forgetLibrary(dataDir: string): Promise<LibraryList | null>;
	/** Whether this shell can answer the four above at all. */
	canSwitchLibrary(): boolean;

	/** Where a file saved out of Sift lands on this machine. Null in a browser, which has no say. */
	downloadFolder(): Promise<DownloadFolder | null>;
	/** Open the folder picker and take what it answers, or pass nothing to go back to the
	 *  machine's own Downloads. The page never names the path; only the picker can grant one. */
	chooseDownloadFolder(reset?: true): Promise<DownloadFolder | null>;
	/** Whether this shell can answer the two above at all. */
	canChooseDownloadFolder(): boolean;

	/**
	 * Show a backup Sift saved on this computer in its folder, in the system's own file manager.
	 * True when it was shown. The shell shows only a Sift backup that is on its own disk.
	 */
	showInFolder(path: string): Promise<boolean>;
	/**
	 * Whether this window can show one: the Sift app on the computer running Sift. A browser, and
	 * the app onto a library on another computer, cannot reach that computer's folders.
	 */
	canShowInFolder(): boolean;
	/** Are the window's own caption buttons drawn over the page, and can they be repainted? */
	canDressTitleBar(): boolean;
	/**
	 * Paint the window's minimise, maximise and close in the colours this page is drawing itself in:
	 * TWO plain-hex COLOURS AND NOTHING ELSE, so a page cannot move or remove them. False in a
	 * browser and on a window with a real title bar.
	 */
	dressTitleBar(colors: { color: string; symbolColor: string }): Promise<boolean>;
}

export type { LibraryList };

/** Where links open: everything this machine offers, and which of them is chosen. */
export interface BrowserChoice {
	chosen: string | null;
	browsers: { id: string; name: string }[];
}

/** Where a file saved out of Sift lands. */
export interface DownloadFolder {
	/** The folder itself, always a real path: the machine's own where none was chosen. */
	path: string;
	/** Whether it was CHOSEN rather than being whatever this machine calls Downloads. The two look
	 *  the same when the chosen one happens to be Downloads, and only one follows a move. */
	chosen: boolean;
}

/**
 * Whether the library is offered to the network, and where a second computer would reach it.
 * `enabled` and `live` are two facts: the listening address is fixed at start, so the screen can
 * say "not until you open Sift again".
 */
export interface Sharing {
	enabled: boolean;
	live: boolean;
	mode: 'standalone' | 'client' | null;
	address: string | null;
	/** The port the library answers on. Sent rather than written down here: the shell is what binds
	 *  the socket, and the firewall rule it offers to create is made from the same number. */
	port: number;
}

/** One address the shell has been told, as the connect screen lists it. */
export interface SavedServer {
	label: string;
	origin: string;
}

/** Where the connect screen stands. See `Bridge.connectState`. */
interface ConnectState {
	last: string | null;
	problem: string | null;
	servers: SavedServer[];
}

type FirewallState = FirewallReport['state'];

/** Which networks the rule opens the port on: home networks only, or every one. */
export type FirewallScope = 'private' | 'any';

/**
 * The rule, the networks the machine is on, and which of them the rule reaches: Windows files a new
 * network as Public, where a private-only rule opens nothing. `networks` null when Windows could not
 * be asked; `scope` null unless open. The server's shape, so both routes agree.
 */
export type FirewallReport = components['schemas']['FirewallView'];

/** The answer a shell from before the networks were read gives: the word alone. */
function asReport(answer: FirewallReport | FirewallState | null | undefined): FirewallReport {
	if (answer === null || answer === undefined)
		return { state: 'unknown', networks: null, scope: null };
	if (typeof answer === 'string') return { state: answer, networks: null, scope: null };
	return answer;
}

/** What the desktop client puts on `window` when it is the one running the page. */
interface InjectedBridge {
	/** Present only inside the desktop application. A browser has no bridge at all. */
	isDesktop?: true;
	startDrag?: (assetId: string) => Promise<string>;
	onDragProgress?: (listen: (progress: DragProgress) => void) => () => void;
	chooseFolder?: () => Promise<string | null>;
	chooseFile?: () => Promise<string | null>;
	applyUpdate?: () => Promise<UpdateOutcome>;
	localHardware?: () => Promise<LocalMachine | null>;
	machineName?: () => Promise<string | null>;
	saveServer?: (origin: string) => Promise<string | null>;
	lastServer?: () => Promise<string | null>;
	connectState?: () => Promise<ConnectState>;
	forgetServer?: (origin: string) => Promise<SavedServer[]>;
	chooseMode?: (mode: 'standalone' | 'client') => Promise<Settled>;
	suggestedLibrary?: () => Promise<SuggestedLibrary | null>;
	chooseLibrary?: (pick: boolean) => Promise<Settled>;
	onSetupProgress?: (listen: (step: SetupStep) => void) => () => void;
	setupBack?: () => Promise<Settled>;
	readClipboard?: () => Promise<{ text: string; image: string | null } | null>;
	captureWindow?: (area: CaptureArea | null) => Promise<Uint8Array | null>;
	getSharing?: () => Promise<Sharing | null>;
	setSharing?: (on: boolean) => Promise<Sharing | null>;
	shellVersion?: () => Promise<string | null>;
	shellLog?: (lines: number) => Promise<ShellLog | null>;
	shellLogDetail?: (detailed: boolean, hidePersonal?: boolean) => Promise<boolean | null>;
	saveLogArchive?: (name: string) => Promise<string | null>;
	firewall?: () => Promise<FirewallReport | FirewallState>;
	openFirewall?: (scope?: FirewallScope) => Promise<FirewallReport | FirewallState>;
	listBrowsers?: () => Promise<BrowserChoice>;
	setBrowser?: (id: string | null) => Promise<BrowserChoice>;
	storage?: () => Promise<StorageReport | null>;
	moveStorage?: () => Promise<MoveOutcome>;
	onStorageProgress?: (listen: (progress: MoveProgress) => void) => () => void;
	forgetMode?: () => Promise<boolean>;
	restartApp?: () => Promise<boolean>;
	downloadFolder?: () => Promise<DownloadFolder | null>;
	chooseDownloadFolder?: (reset: null | true) => Promise<DownloadFolder | null>;
	showInFolder?: (path: string) => Promise<boolean>;
	setTitleBar?: (colors: { color: string; symbolColor: string }) => Promise<boolean>;
	keepRunningWhenClosed?: () => Promise<boolean | null>;
	keepRunning?: (on: boolean) => Promise<boolean | null>;
	/* The login item: whether Sift starts when this person signs in to Windows. The shell answers
	   these two once it has the verb; until then both are absent and the row is not drawn. */
	startsWithWindows?: () => Promise<boolean | null>;
	startWithWindows?: (on: boolean) => Promise<boolean | null>;
	libraries?: () => Promise<LibraryList | null>;
	openLibrary?: (dataDir: string) => Promise<Settled>;
	addLibrary?: () => Promise<Settled>;
	forgetLibrary?: (dataDir: string) => Promise<LibraryList | null>;
}

declare global {
	interface Window {
		sift?: InjectedBridge;
	}
}

function injected(): InjectedBridge | undefined {
	// `typeof window` guards the case where this is evaluated outside a browser at all: a unit
	// test, or a build step touching the module.
	return typeof window === 'undefined' ? undefined : window.sift;
}

/** Sift's own two folders on this machine, and what they hold. */
export interface StorageReport {
	dataDir: string;
	cacheDir: string;
	dataBytes: number;
	cacheBytes: number;
}

/** How far a move has got. `total` is measured before it starts, so it does not move. */
export interface MoveProgress {
	copied: number;
	total: number;
}

/** `reason: null` is the person closing the folder picker, not a failure. Not exported: read through
 *  `moveStorage`'s return type. */
type MoveOutcome =
	| { ok: true; locations: { dataDir: string; cacheDir: string }; renamed: boolean }
	| { ok: false; reason: string | null };

// Each capability asks whether its method is there, never a user agent or one desktop flag, so a
// shell reports exactly what it has, whatever its version.
export const bridge: NativeBridge = {
	canNativeDrag: () => typeof injected()?.startDrag === 'function',

	async startDrag(assetId: string): Promise<DragOutcome> {
		const answered = await injected()?.startDrag?.(assetId);
		/* Checked, not cast: a shell a version ahead could answer a word this page does not know. */
		return answered === 'dragged' ? answered : 'unavailable';
	},

	onDragProgress(listen: (progress: DragProgress) => void): () => void {
		return injected()?.onDragProgress?.(listen) ?? (() => {});
	},

	canReadClipboard: () => typeof injected()?.readClipboard === 'function',

	async readClipboard() {
		return (await injected()?.readClipboard?.()) ?? null;
	},

	canCaptureWindow: () => typeof injected()?.captureWindow === 'function',

	async captureWindow(area: CaptureArea | null) {
		const bytes = await injected()?.captureWindow?.(area);
		/* Checked rather than cast: a shell of another version could answer something else, and a
		 * picture of nothing is no picture. `isView` because the bytes arrive from another realm. */
		if (!ArrayBuffer.isView(bytes) || bytes.byteLength === 0) return null;
		return new Blob([new Uint8Array(bytes)], { type: 'image/png' });
	},

	canSaveServer: () => typeof injected()?.saveServer === 'function',

	async saveServer(origin: string) {
		/* A browser answers the refusal itself rather than silently doing nothing: the screen has a
		 * form on it, and a form that submits to nowhere is the worst of the three outcomes. */
		const answered = injected()?.saveServer;
		if (answered === undefined) return 'This only works in the Sift app.';
		return answered(origin);
	},

	async lastServer() {
		return (await injected()?.lastServer?.()) ?? null;
	},

	async connectState() {
		const asked = injected()?.connectState;
		if (asked !== undefined) return asked();
		/* A shell from before this verb knows the last address and nothing else about the screen. */
		return { last: await bridge.lastServer(), problem: null, servers: [] };
	},

	async forgetServer(origin: string) {
		return (await injected()?.forgetServer?.(origin)) ?? [];
	},

	/* One capability for both screens, two halves of one sequence. */
	canSetUp: () => typeof injected()?.chooseMode === 'function',

	async chooseMode(mode: 'standalone' | 'client') {
		const answered = injected()?.chooseMode;
		if (answered === undefined) return { ok: false, refusal: 'This only works in the Sift app.' };
		return answered(mode);
	},

	async suggestedLibrary() {
		return (await injected()?.suggestedLibrary?.()) ?? null;
	},

	async chooseLibrary(pick: boolean) {
		const answered = injected()?.chooseLibrary;
		if (answered === undefined) return { ok: false, refusal: 'This only works in the Sift app.' };
		return answered(pick);
	},

	onSetupProgress(listen: (step: SetupStep) => void) {
		return injected()?.onSetupProgress?.(listen) ?? (() => {});
	},

	async setupBack() {
		const answered = injected()?.setupBack;
		if (answered === undefined) return { ok: false, refusal: 'This only works in the Sift app.' };
		return answered();
	},

	canApplyUpdate: () => typeof injected()?.applyUpdate === 'function',

	async applyUpdate() {
		return (await injected()?.applyUpdate?.()) ?? { ok: false, reason: 'failed' };
	},

	isDesktop: () => injected()?.isDesktop === true,

	async localHardware() {
		return (await injected()?.localHardware?.()) ?? null;
	},

	async machineName() {
		return (await injected()?.machineName?.()) ?? null;
	},

	canChooseFolder: () => typeof injected()?.chooseFolder === 'function',

	async chooseFolder() {
		return (await injected()?.chooseFolder?.()) ?? null;
	},

	canChooseFile: () => typeof injected()?.chooseFile === 'function',

	async chooseFile() {
		return (await injected()?.chooseFile?.()) ?? null;
	},

	canShareOnNetwork: () => typeof injected()?.setSharing === 'function',

	async sharing() {
		return (await injected()?.getSharing?.()) ?? null;
	},

	canChooseBrowser: () => typeof injected()?.setBrowser === 'function',
	canMoveStorage: () => typeof injected()?.moveStorage === 'function',
	canForgetMode: () => typeof injected()?.forgetMode === 'function',
	canRestartApp: () => typeof injected()?.restartApp === 'function',
	async storage() {
		return (await injected()?.storage?.()) ?? null;
	},
	async moveStorage() {
		return (
			(await injected()?.moveStorage?.()) ?? {
				ok: false as const,
				reason: "This copy of Sift can't move its own folders."
			}
		);
	},
	onStorageProgress(listen: (progress: MoveProgress) => void) {
		return injected()?.onStorageProgress?.(listen) ?? (() => {});
	},
	async forgetMode() {
		return (await injected()?.forgetMode?.()) ?? false;
	},
	async restartApp() {
		return (await injected()?.restartApp?.()) ?? false;
	},

	/* Asked of the WRITE half, like every pair here: the half a switch needs. */
	canKeepRunningWhenClosed: () => typeof injected()?.keepRunning === 'function',
	async keepRunningWhenClosed() {
		return (await injected()?.keepRunningWhenClosed?.()) ?? null;
	},
	async setKeepRunningWhenClosed(on: boolean) {
		return (await injected()?.keepRunning?.(on)) ?? null;
	},

	/* Asked of the WRITE half, like the pair above. */
	canStartWithWindows: () => typeof injected()?.startWithWindows === 'function',
	async startsWithWindows() {
		return (await injected()?.startsWithWindows?.()) ?? null;
	},
	async setStartsWithWindows(on: boolean) {
		return (await injected()?.startWithWindows?.(on)) ?? null;
	},

	canSwitchLibrary: () => typeof injected()?.openLibrary === 'function',
	async libraries() {
		return (await injected()?.libraries?.()) ?? null;
	},
	async openLibrary(dataDir: string) {
		const answered = injected()?.openLibrary;
		if (answered === undefined) return { ok: false, refusal: 'This only works in the Sift app.' };
		return answered(dataDir);
	},
	async addLibrary() {
		const answered = injected()?.addLibrary;
		if (answered === undefined) return { ok: false, refusal: 'This only works in the Sift app.' };
		return answered();
	},
	async forgetLibrary(dataDir: string) {
		return (await injected()?.forgetLibrary?.(dataDir)) ?? null;
	},

	canChooseDownloadFolder: () => typeof injected()?.chooseDownloadFolder === 'function',
	async downloadFolder() {
		return (await injected()?.downloadFolder?.()) ?? null;
	},
	async chooseDownloadFolder(reset?: true) {
		return (await injected()?.chooseDownloadFolder?.(reset ?? null)) ?? null;
	},

	canShowInFolder: () => typeof injected()?.showInFolder === 'function',
	async showInFolder(path: string) {
		return (await injected()?.showInFolder?.(path)) ?? false;
	},

	async browsers() {
		return (await injected()?.listBrowsers?.()) ?? { chosen: null, browsers: [] };
	},

	async setBrowser(id: string | null) {
		return (await injected()?.setBrowser?.(id)) ?? { chosen: null, browsers: [] };
	},

	canDressTitleBar: () => typeof injected()?.setTitleBar === 'function',

	async dressTitleBar(colors: { color: string; symbolColor: string }) {
		return (await injected()?.setTitleBar?.(colors)) ?? false;
	},

	async setSharing(on: boolean) {
		return (await injected()?.setSharing?.(on)) ?? null;
	},

	async shellVersion() {
		return (await injected()?.shellVersion?.()) ?? null;
	},

	async shellLog(lines: number) {
		return (await injected()?.shellLog?.(lines)) ?? null;
	},

	async shellLogDetail(detailed: boolean, hidePersonal: boolean) {
		return (await injected()?.shellLogDetail?.(detailed, hidePersonal)) ?? null;
	},

	canReadShellLog: () => typeof injected()?.shellLog === 'function',

	async saveLogArchive(name: string) {
		return (await injected()?.saveLogArchive?.(name)) ?? null;
	},

	canSaveLogArchive: () => typeof injected()?.saveLogArchive === 'function',

	async firewall() {
		/* A shell from before the networks were read answers the word alone; it is read as an
		 * answer that knows nothing about the networks, not as a fault. */
		return asReport(await injected()?.firewall?.());
	},

	async openFirewall(scope: FirewallScope = 'private') {
		/* A shell too old to have this verb answers `unknown`, which is the same thing the screen
		 * shows when the question cannot be put: what to run by hand. */
		return asReport(await injected()?.openFirewall?.(scope));
	}
};
