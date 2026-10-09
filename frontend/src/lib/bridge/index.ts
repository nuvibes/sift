/* The desktop bridge: what only the desktop client can do. In a browser every method is a no-op
 * and every capability false, so a feature lights up where the bridge is present. */

/** One gesture wherever the file is: the shell offers a virtual file that follows the download. */
export type DragOutcome = 'dragged' | 'unavailable';

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

/** The server's shape for an update asked of the computer running Sift, every failure named. */
type UpdateTaken = components['schemas']['UpdateTaken'];
export type UpdateOutcome = Pick<UpdateTaken, 'ok'> &
	Partial<Pick<UpdateTaken, 'version' | 'reason'>>;

/** The computer a client-mode window is running on, which is not the one holding the library. */
export interface LocalMachine {
	cpu_model: string | null;
	/** Logical processors: threads, not cores. */
	thread_count: number;
	installed_ram_bytes: number | null;
	gpu_cards: { name: string | null; vram_bytes: number | null }[];
}

/** The end of the shell's own log in `/logs`' shape, so one screen draws either. */
type ShellLog = components['schemas']['AppLog'];

export interface DragProgress {
	assetId: string;
	received: number;
	total: number | null;
}

interface NativeBridge {
	canNativeDrag(): boolean;
	startDrag(assetId: string): Promise<DragOutcome>;
	/** Watch a fetch for a drag; returns the function that stops watching. */
	onDragProgress(listen: (progress: DragProgress) => void): () => void;
	canReadClipboard(): boolean;
	/** A link, a picture, or neither; null in a browser, where plain http has no clipboard read. */
	readClipboard(): Promise<{ text: string; image: string | null } | null>;
	canCaptureWindow(): boolean;
	/** This window as a PNG, or one area of it in page pixels; a browser cannot read the page. */
	captureWindow(area: CaptureArea | null): Promise<Blob | null>;
	canSaveServer(): boolean;
	/** Save an address and go there: null when it worked, else the sentence the shell saw. */
	saveServer(origin: string): Promise<string | null>;
	/** The address last tried, so a correction starts from it. */
	lastServer(): Promise<string | null>;
	/** The last address, why it did not answer, and every address saved. */
	connectState(): Promise<ConnectState>;
	forgetServer(origin: string): Promise<SavedServer[]>;
	/** Whether the two first-run questions can be asked; false in a browser, so no dead buttons. */
	canSetUp(): boolean;
	/** Save which way this copy runs; the shell moves the window on. */
	chooseMode(mode: 'standalone' | 'client'): Promise<Settled>;
	suggestedLibrary(): Promise<SuggestedLibrary | null>;
	/** Settle the library folder: the suggestion, or (`pick`) the machine's own dialog. */
	chooseLibrary(pick: boolean): Promise<Settled>;
	/** Hear each step of taking that folder; returns how to stop listening. */
	onSetupProgress(listen: (step: SetupStep) => void): () => void;
	/** Go back one question; the shell decides which from the answers that exist. */
	setupBack(): Promise<Settled>;
	canApplyUpdate(): boolean;
	/** Download, verify and launch the newest release; takes nothing, so no page names one. */
	applyUpdate(): Promise<UpdateOutcome>;
	/** The computer this window runs on, or null where it is the library's own (or a browser). */
	localHardware(): Promise<LocalMachine | null>;
	/** This computer's name, for the phone's remote, in every mode. */
	machineName(): Promise<string | null>;
	/** Inside the desktop application at all, which `localHardware`'s null cannot tell. */
	isDesktop(): boolean;
	canChooseFolder(): boolean;
	/** A folder the person chose in the system dialog, the only grant; null if cancelled. */
	chooseFolder(): Promise<string | null>;
	canChooseFile(): boolean;
	/** One database file chosen in the system dialog; the path grants nothing. */
	chooseFile(): Promise<string | null>;
	canShareOnNetwork(): boolean;
	/** Whether the library is offered, and its address. */
	sharing(): Promise<Sharing | null>;
	/** Turn the offer on or off: a boolean only, so no page chooses an address or network card. */
	setSharing(on: boolean): Promise<Sharing | null>;
	/** This copy's version, which in client mode may differ from the library's. */
	shellVersion(): Promise<string | null>;
	/** The end of the shell's own log, this machine's where `/logs` is the server's. */
	shellLog(lines: number): Promise<ShellLog | null>;
	/** Hand the shell the library's log settings; answers whether it now writes the detail. */
	shellLogDetail(detailed: boolean, hidePersonal: boolean): Promise<boolean | null>;
	/** Whether the shell has the log channel: an older shell answers `isDesktop()` without it. */
	canReadShellLog(): boolean;
	/** Save every log, redacted, as one archive in the `Save files to` folder. */
	saveLogArchive(name: string): Promise<{ file: string } | { reason: string }>;
	canSaveLogArchive(): boolean;
	/** Whether Windows lets other computers through to Sift, and on which networks. */
	firewall(): Promise<FirewallReport>;
	/** Ask Windows to let them through; nothing is passed, and its own prompt decides. */
	openFirewall(scope?: FirewallScope): Promise<FirewallReport>;
	canChooseBrowser(): boolean;
	/** Which browsers this machine has, and which one links go to. */
	browsers(): Promise<BrowserChoice>;
	/** Choose one by an id from the list, or null for the system's: the shell starts it. */
	setBrowser(id: string | null): Promise<BrowserChoice>;

	/** Where Sift keeps its database and cache, and how big; null in a browser and client mode. */
	storage(): Promise<StorageReport | null>;
	/** Move both under a chosen folder; takes no path, and the old folder stays until done. */
	moveStorage(): Promise<MoveOutcome>;
	onStorageProgress(listen: (progress: MoveProgress) => void): () => void;
	canMoveStorage(): boolean;
	canForgetMode(): boolean;
	/** Forget which way Sift was set up, keeping the library folder. */
	forgetMode(): Promise<boolean>;

	canRestartApp(): boolean;

	/** Close Sift and open it again; false where the shell cannot. */
	restartApp(): Promise<boolean>;

	/** Whether closing the window keeps Sift running; null where nothing true can be said. */
	keepRunningWhenClosed(): Promise<boolean | null>;
	setKeepRunningWhenClosed(on: boolean): Promise<boolean | null>;
	canKeepRunningWhenClosed(): boolean;

	/** Whether Sift starts at sign-in to Windows; null as for `keepRunningWhenClosed`. */
	startsWithWindows(): Promise<boolean | null>;
	setStartsWithWindows(on: boolean): Promise<boolean | null>;
	canStartWithWindows(): boolean;

	/** Every library this copy has opened, and the one in view (null in client mode). */
	libraries(): Promise<LibraryList | null>;
	/** Open a library already on that list, so no page can aim the backend elsewhere. */
	openLibrary(dataDir: string): Promise<Settled>;
	/** Open a library from a database file chosen in the system picker; the shell decides how. */
	addLibrary(): Promise<Settled>;
	/** Take one off the list; the library itself is untouched. */
	forgetLibrary(dataDir: string): Promise<LibraryList | null>;
	canSwitchLibrary(): boolean;

	downloadFolder(): Promise<DownloadFolder | null>;
	/** The folder picker's answer, or nothing for Downloads; the page names no path. */
	chooseDownloadFolder(reset?: true): Promise<DownloadFolder | null>;
	canChooseDownloadFolder(): boolean;

	/** Show a Sift backup on this computer's disk in the system file manager. */
	showInFolder(path: string): Promise<boolean>;
	/** Only on the computer running Sift. */
	canShowInFolder(): boolean;
	canDressTitleBar(): boolean;
	/** Paint the caption buttons in the page's colours: two hex colours only. */
	dressTitleBar(colors: { color: string; symbolColor: string }): Promise<boolean>;
	/** Tell the window how far its page has drawn, with the look the next start opens in. */
	tellDrawn(stage: WindowStage): void;
}

type WindowStage = 'painted' | 'usable';

/** What the opening frame needs of the page: never anything of the library. */
interface Look {
	theme: string | null;
	canvas: string;
}

export function canvasHex(computed: string): string | null {
	const parts = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(computed);
	if (parts === null) return null;
	return `#${parts
		.slice(1, 4)
		.map((one) => Number(one).toString(16).padStart(2, '0'))
		.join('')}`;
}

function look(): Look | null {
	const canvas = canvasHex(getComputedStyle(document.body).backgroundColor);
	if (canvas === null) return null;
	let theme: string | null = null;
	try {
		theme = localStorage.getItem('sift.theme');
	} catch {
		/* storage refused: the frame draws the default theme */
	}
	return { theme, canvas };
}

export type { LibraryList };

export interface BrowserChoice {
	chosen: string | null;
	browsers: { id: string; name: string }[];
}

export interface DownloadFolder {
	path: string;
	/** Chosen, not this machine's Downloads; only a chosen one follows a move. */
	chosen: boolean;
}

/** Whether the library is offered; `enabled` and `live` differ until Sift opens again. */
export interface Sharing {
	enabled: boolean;
	live: boolean;
	mode: 'standalone' | 'client' | null;
	address: string | null;
	/** Sent, as the shell binds it and makes the firewall rule from the same number. */
	port: number;
}

export interface SavedServer {
	label: string;
	origin: string;
}

interface ConnectState {
	last: string | null;
	problem: string | null;
	servers: SavedServer[];
}

type FirewallState = FirewallReport['state'];

export type FirewallScope = 'private' | 'any';

/** The rule and the networks it reaches: Windows files a new network as Public. */
export type FirewallReport = components['schemas']['FirewallView'];

/** An older shell's answer, the word alone. */
function asReport(answer: FirewallReport | FirewallState | null | undefined): FirewallReport {
	if (answer === null || answer === undefined)
		return { state: 'unknown', networks: null, scope: null };
	if (typeof answer === 'string') return { state: answer, networks: null, scope: null };
	return answer;
}

interface InjectedBridge {
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
	saveLogArchive?: (name: string) => Promise<{ file: string } | { reason: string }>;
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
	/* The login item; absent until the shell has the verb, and the row is not drawn. */
	startsWithWindows?: () => Promise<boolean | null>;
	startWithWindows?: (on: boolean) => Promise<boolean | null>;
	libraries?: () => Promise<LibraryList | null>;
	openLibrary?: (dataDir: string) => Promise<Settled>;
	addLibrary?: () => Promise<Settled>;
	forgetLibrary?: (dataDir: string) => Promise<LibraryList | null>;
	windowStage?: (stage: WindowStage, look: Look) => Promise<boolean>;
}

declare global {
	interface Window {
		sift?: InjectedBridge;
	}
}

function injected(): InjectedBridge | undefined {
	// Outside a browser (a unit test, a build step) there is no `window`.
	return typeof window === 'undefined' ? undefined : window.sift;
}

export interface StorageReport {
	dataDir: string;
	cacheDir: string;
	dataBytes: number;
	cacheBytes: number;
	measuredAt: number | null;
	measuring: boolean;
}

/** How far a move has got; `total` is measured before it starts. */
export interface MoveProgress {
	copied: number;
	total: number;
}

/** `reason: null` is the folder picker closed, not a failure. */
type MoveOutcome =
	| { ok: true; locations: { dataDir: string; cacheDir: string }; renamed: boolean }
	| { ok: false; reason: string | null };

// Each capability asks whether its method is there, so a shell reports exactly what it has.
export const bridge: NativeBridge = {
	canNativeDrag: () => typeof injected()?.startDrag === 'function',

	async startDrag(assetId: string): Promise<DragOutcome> {
		const answered = await injected()?.startDrag?.(assetId);
		/* Checked, not cast: a newer shell could answer a word this page does not know. */
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
		/* `isView` because the bytes arrive from another realm; an empty picture is none. */
		if (!ArrayBuffer.isView(bytes) || bytes.byteLength === 0) return null;
		return new Blob([new Uint8Array(bytes)], { type: 'image/png' });
	},

	canSaveServer: () => typeof injected()?.saveServer === 'function',

	async saveServer(origin: string) {
		/* A browser refuses in words, since the screen has a form on it. */
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
		return { last: await bridge.lastServer(), problem: null, servers: [] };
	},

	async forgetServer(origin: string) {
		return (await injected()?.forgetServer?.(origin)) ?? [];
	},

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

	/* Asked of the write half, the half a switch needs. */
	canKeepRunningWhenClosed: () => typeof injected()?.keepRunning === 'function',
	async keepRunningWhenClosed() {
		return (await injected()?.keepRunningWhenClosed?.()) ?? null;
	},
	async setKeepRunningWhenClosed(on: boolean) {
		return (await injected()?.keepRunning?.(on)) ?? null;
	},

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

	tellDrawn(stage: WindowStage) {
		const tell = injected()?.windowStage;
		if (typeof tell !== 'function') return;
		const seen = look();
		if (seen !== null) void tell(stage, seen).catch(() => false);
	},

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
		return (await injected()?.saveLogArchive?.(name)) ?? { reason: 'this app has no log maker' };
	},

	canSaveLogArchive: () => typeof injected()?.saveLogArchive === 'function',

	async firewall() {
		return asReport(await injected()?.firewall?.());
	},

	async openFirewall(scope: FirewallScope = 'private') {
		return asReport(await injected()?.openFirewall?.(scope));
	}
};
