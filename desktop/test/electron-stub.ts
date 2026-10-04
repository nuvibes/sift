/* Electron, as a test can hold it.
 *
 * The main process cannot be imported in a test without this: `electron` is not an ordinary package
 * but a binary that only resolves inside a running Electron, so a plain `import` of anything in
 * `src/` fails at the first line. Vitest aliases the name to this file instead.
 *
 * It is a stub with settable answers, not a mock framework. A test that wants `app.getPath` to
 * return a temporary folder sets it and reads what the code did with it, which is the only kind of
 * assertion worth making about a path helper.
 *
 * It is TYPE-CHECKED, by `tsconfig.test.json`, and that is the point of keeping it in a file of
 * its own. A hand-written double that nothing checks drifts from the real interface silently, and
 * then the test passes while the shipped call does not exist.
 */

import type { Dialog } from 'electron';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/* The two RESULT shapes are pinned to Electron's own types, not re-declared.
 *
 * `tsc` resolves `electron` to the real package (only the test runner aliases it here), so a
 * hand-written double is otherwise checked against nothing at all, and drifts from the interface
 * silently. Naming the return types means an Electron upgrade that renames `filePaths` fails the
 * typecheck rather than passing every test while the shipped call is broken. */
type MessageBoxResult = Awaited<ReturnType<Dialog['showMessageBox']>>;
type OpenDialogResult = Awaited<ReturnType<Dialog['showOpenDialog']>>;

type PathName = 'appData' | 'userData' | 'home' | 'temp';

interface AppStub {
	isPackaged: boolean;
	/** What a packaged application answers for its own version. Settable, because the one verb that
	 *  reads it answers differently packaged and not, and both answers are load-bearing. */
	getVersion(): string;
	getPath(name: PathName): string;
	whenReady(): Promise<void>;
	quit(): void;
	exit(code: number): void;
	requestSingleInstanceLock(): boolean;
	on(event: string, listener: (...args: unknown[]) => void): AppStub;
}

/* What the application was told, and what it is listening for.
 *
 * Kept because `main` is driven through them: the second copy, the last window closing and the way
 * out are all `app.on` events, and the only way a test can press one is to hold its listener. */
export const appListeners = new Map<string, (...args: unknown[]) => void>();
export const appCalls = { quit: 0, exit: [] as number[], locked: true };

/** What `getPath` will answer, per name. Set it in a test; reset between them. */
export const paths: Record<string, string> = {};

/** What `app.getVersion()` answers. A test that cares sets it; the default is Electron's own
 *  shape of answer, which is exactly what the verb must not pass off as Sift's. */
export let version = '43.4.1';

export function setVersion(next: string): void {
	version = next;
}

export const app: AppStub = {
	isPackaged: false,
	getVersion: () => version,
	/* Under the machine's own temporary folder, never a folder of its own at the root of a drive: a
	   test that writes through a path it did not set still writes somewhere that gets cleaned. */
	getPath: (name) => paths[name] ?? join(tmpdir(), 'sift-desktop-stub', name),
	whenReady: () => Promise.resolve(),
	quit: () => {
		appCalls.quit += 1;
	},
	exit: (code) => {
		appCalls.exit.push(code);
	},
	requestSingleInstanceLock: () => appCalls.locked,
	on: (event, listener) => {
		appListeners.set(event, listener);
		return app;
	}
};

/* The dialog answers a test has queued, oldest first, and the options each call was made with.
 *
 * Both halves matter. The answers are what drives the code down a branch; the recorded options are
 * how a test can assert that the DEFAULT button is the settled one: a dialog whose
 * buttons are in the wrong order is a first-run screen that quietly steers somebody into client
 * mode, and nothing about the code would look wrong. */
export const dialogAnswers: number[] = [];
export const folderAnswers: (string[] | null)[] = [];
export const dialogCalls: MessageBoxOptions[] = [];

interface MessageBoxOptions {
	buttons?: string[];
	defaultId?: number;
	cancelId?: number;
	message?: string;
	detail?: string;
	title?: string;
}

export const dialog = {
	showMessageBox: async (options: MessageBoxOptions): Promise<MessageBoxResult> => {
		dialogCalls.push(options);
		return { response: dialogAnswers.shift() ?? 0, checkboxChecked: false };
	},
	showOpenDialog: async (): Promise<OpenDialogResult> => {
		const picked = folderAnswers.shift() ?? null;
		return picked === null
			? { canceled: true, filePaths: [] }
			: { canceled: false, filePaths: picked };
	},
	/* The blocking form, which is what the start-failure offer uses: the window behind it may have
	   nothing loaded, and a dialog that cannot fail to appear is the point. Same queue. */
	showMessageBoxSync: (options: MessageBoxOptions): number => {
		dialogCalls.push(options);
		return dialogAnswers.shift() ?? 0;
	},
	showErrorBox: (): void => {}
};

/* The two halves of one channel, joined by this object so a test can call what the page calls and
 * see what the main process would have answered. Nothing here crosses a process boundary (there
 * is only one process in a test), which is exactly why the handler's own trust check has to be
 * exercised rather than assumed. */
type Handler = (event: unknown, ...args: unknown[]) => unknown;
export const handlers = new Map<string, Handler>();

/* The one synchronous channel: its listener writes `returnValue` on the event it is handed. */
type SyncEvent = { senderFrame: unknown; sender: unknown; returnValue: unknown };
type SyncHandler = (event: SyncEvent, ...args: unknown[]) => void;
export const syncHandlers = new Map<string, SyncHandler>();

export const ipcMain = {
	handle(channel: string, listener: Handler): void {
		handlers.set(channel, listener);
	},
	on(channel: string, listener: SyncHandler): void {
		syncHandlers.set(channel, listener);
	}
};

/** Listeners the preload put on a main-to-page channel, so a test can fire one. */
export const listeners = new Map<string, Set<(event: unknown, ...args: unknown[]) => void>>();

export const ipcRenderer = {
	invoke(channel: string, ...args: unknown[]): Promise<unknown> {
		const found = handlers.get(channel);
		if (found === undefined) return Promise.reject(new Error(`no handler for ${channel}`));
		return Promise.resolve(found(sender, ...args));
	},
	sendSync(channel: string, ...args: unknown[]): unknown {
		const found = syncHandlers.get(channel);
		if (found === undefined) throw new Error(`no handler for ${channel}`);
		const event: SyncEvent = { ...sender, returnValue: undefined };
		found(event, ...args);
		return event.returnValue;
	},
	on(channel: string, listener: (event: unknown, ...args: unknown[]) => void): void {
		const set = listeners.get(channel) ?? new Set();
		set.add(listener);
		listeners.set(channel, set);
	},
	removeListener(channel: string, listener: (event: unknown, ...args: unknown[]) => void): void {
		listeners.get(channel)?.delete(listener);
	}
};

/** Everything the main process sent back to the page, in order. */
export const sentToPage: { channel: string; payload: unknown }[] = [];

/** What `event.senderFrame` will look like. A test sets the url to drive the trust check. */
export const sender = {
	senderFrame: { url: 'http://127.0.0.1:5171/browse', parent: null as unknown },
	sender: {
		isDestroyed: (): boolean => senderIsDestroyed.value,
		send: (channel: string, payload: unknown): void => {
			sentToPage.push({ channel, payload });
		}
	} as unknown
};

/** Whether the window has gone. A fetch can outlive it, and the code has to survive that. */
export const senderIsDestroyed = { value: false };

/** Where `shell.showItemInFolder` was pointed. A test reads it to prove reveal used the path. */
export const revealed: string[] = [];

/** Everything `shell.openPath` was pointed at. The update chain's last step, and the one that
 *  must never happen for a file that failed verification. */
export const openedPaths: string[] = [];

/** Everything `shell.openExternal` was handed: the default browser's door out of the window. */
export const openedExternally: string[] = [];

export const shell = {
	openExternal: async (url: string): Promise<void> => {
		openedExternally.push(url);
	},
	openPath: async (target: string): Promise<string> => {
		openedPaths.push(target);
		return '';
	},
	showItemInFolder: (path: string): void => {
		revealed.push(path);
	}
};

/* The clipboard, with settable contents. `readImage` answers an object shaped like a NativeImage
 * in the two ways the code actually uses (`isEmpty` and `toDataURL`) because those are the two
 * that decide whether a picture is offered at all. */
export const clipboardContents = { text: '', image: null as string | null };

export const clipboard = {
	readText: (): string => clipboardContents.text,
	readImage: () => ({
		isEmpty: (): boolean => clipboardContents.image === null,
		toDataURL: (): string => clipboardContents.image ?? ''
	})
};

/* What `session.defaultSession.fetch` will answer, oldest first, keyed by nothing: a test queues
 * the replies in the order its code will ask for them. Requests are recorded so a test can prove
 * the shell asked the PAGE'S origin rather than a hard-coded one. */
export const fetchAnswers: (Response | Error)[] = [];
export const fetched: string[] = [];

/** Build a reply out of plain values, so a test never has to construct a real Response. */
export function reply(
	body: unknown,
	{
		ok = true,
		status,
		headers = {}
	}: { ok?: boolean; status?: number; headers?: Record<string, string> } = {}
): Response {
	const text = typeof body === 'string' ? body : JSON.stringify(body);
	const bytes = new TextEncoder().encode(text);
	return {
		ok,
		status: status ?? (ok ? 200 : 500),
		/* Real Headers, because what decides whether an answer came from a Sift is a header on it. */
		headers: new Headers(headers),
		json: async () => body,
		text: async () => text,
		/* All THREE shapes, because the code under test reads a small document one way and a large
		 * one another: `arrayBuffer` for a feed or a hash file, the stream for an installer. A stub
		 * that answered only one of them would pass a test of the half it covered. */
		arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
		body:
			typeof body === 'string'
				? new ReadableStream<Uint8Array>({
						start(controller) {
							controller.enqueue(bytes);
							controller.close();
						}
					})
				: null
	} as unknown as Response;
}

/* `net.fetch` is Chromium's own network stack, which the update chain uses rather than Node's:
 * it follows the application's proxy settings and certificate store, which is what a person on a
 * corporate network expects. Answered from the same queue as the session's fetch. */
export const net = {
	fetch: async (url: string): Promise<Response> => {
		fetched.push(url);
		const next = fetchAnswers.shift();
		if (next instanceof Error) throw next;
		return next ?? reply(null, { ok: false });
	}
};

/* The scheme that serves the connect screen. The declaration is recorded rather than performed:
 * what a test can prove about it is that it happens at all, and in the right order, since declaring a
 * privileged scheme after the app is ready silently does nothing.
 *
 * The HANDLER is kept as well as recorded, and that is not symmetry. It is the one place in the
 * shell where a URL becomes a path on the disk, so what it refuses is worth asking directly rather
 * than only through the pure function it calls. */
export const schemesDeclared: unknown[] = [];
export const protocolsHandled: string[] = [];
export const protocolHandlers = new Map<string, (request: Request) => Promise<Response>>();

export const protocol = {
	registerSchemesAsPrivileged: (schemes: unknown[]): void => {
		schemesDeclared.push(...schemes);
	},
	handle: (scheme: string, handler?: (request: Request) => Promise<Response>): void => {
		protocolsHandled.push(scheme);
		if (handler !== undefined) protocolHandlers.set(scheme, handler);
	}
};

/* The notification-area icon, as a test can hold it.
 *
 * What is worth asserting about a tray is what it OFFERS and what pressing one of those does, so
 * the menu template is kept rather than the Menu object: a real `Menu` hides its items behind
 * Electron's own API, and a test that could only count them would prove nothing about the labels.
 */
export const trays: TrayStub[] = [];

export class TrayStub {
	tooltip = '';
	menu: MenuTemplate = [];
	destroyed = false;
	readonly listeners = new Map<string, () => void>();
	constructor(readonly icon: string) {
		trays.push(this);
	}
	setToolTip(text: string): void {
		this.tooltip = text;
	}
	setContextMenu(menu: MenuTemplate): void {
		this.menu = menu;
	}
	on(event: string, listener: () => void): this {
		this.listeners.set(event, listener);
		return this;
	}
	destroy(): void {
		this.destroyed = true;
	}
	/** Press an item by its label, the way somebody would. Throws rather than passing silently. */
	press(label: string): void {
		const item = this.menu.find((one) => one.label === label);
		if (item?.click === undefined) throw new Error(`no menu item called ${label}`);
		item.click();
	}
}

type MenuTemplate = { label?: string; type?: string; click?: () => void }[];

export const Tray = TrayStub;

/* `buildFromTemplate` hands the template straight back, which is what makes the tray's menu
 * readable above. Electron's own returns an opaque Menu; nothing in this shell asks it anything. */
export const Menu = {
	buildFromTemplate: (template: MenuTemplate): MenuTemplate => template
};

export const contextBridge = {
	exposed: {} as Record<string, unknown>,
	exposeInMainWorld(key: string, value: unknown): void {
		contextBridge.exposed[key] = value;
	}
};

/* Forking is REFUSED rather than faked.
 *
 * A test that reached this would be starting a real second process, and the thing it would be
 * testing is not the code but the machine. Every test that gets near a transfer supplies its own
 * child instead, so an exception here means a test lost its double, which is worth failing over
 * rather than passing slowly. */
export const utilityProcess = {
	fork: (): never => {
		throw new Error('a test must never fork a real utility process');
	}
};

/** Every cookie the shell threw away, as [origin, name]. Sign-in state is what these are. */
export const cookiesRemoved: [string, string][] = [];

/** Listeners on the default session: `will-download` is the one the shell uses. */
export const sessionListeners = new Map<string, (...args: never[]) => void>();

export const session = {
	defaultSession: {
		cookies: {
			get: async () => [],
			remove: async (origin: string, name: string) => {
				cookiesRemoved.push([origin, name]);
			}
		},
		on: (event: string, listener: (...args: never[]) => void): void => {
			sessionListeners.set(event, listener);
		},
		fetch: async (url: string): Promise<Response> => {
			fetched.push(url);
			const next = fetchAnswers.shift();
			if (next instanceof Error) throw next;
			return next ?? reply(null, { ok: false });
		}
	}
};

export class BrowserWindow {
	static instances: BrowserWindow[] = [];
	/** The main process asks this to find the window a call came from. */
	static fromWebContents(): BrowserWindow | null {
		return BrowserWindow.instances[0] ?? null;
	}
	/* The handlers the window's page is policed by, kept so a test can put a link or a navigation
	   to them the way Chromium would. */
	openHandler: ((details: { url: string }) => { action: string }) | null = null;
	readonly pageListeners = new Map<string, (...args: never[]) => void>();
	readonly listeners = new Map<string, (...args: never[]) => void>();
	webContents = {
		setWindowOpenHandler: (handler: (details: { url: string }) => { action: string }): void => {
			this.openHandler = handler;
		},
		on: (event: string, listener: (...args: never[]) => void): void => {
			this.pageListeners.set(event, listener);
		},
		session: session.defaultSession
	};
	constructor(readonly options: unknown) {
		BrowserWindow.instances.push(this);
	}
	/** Every address the window was pointed at, in order. */
	loaded: string[] = [];
	/** Loads that will FAIL, oldest first: a server that stopped answering between check and load. */
	static loadFailures: Error[] = [];
	shown = 0;
	hidden = 0;
	focused = 0;
	restored = 0;
	minimized = false;
	/** Every repaint of the caption buttons this window was asked for, in order. */
	titleBarOverlays: { color?: string; symbolColor?: string; height?: number }[] = [];
	setTitleBarOverlay(options: { color?: string; symbolColor?: string; height?: number }): void {
		this.titleBarOverlays.push(options);
	}
	loadURL(url: string): Promise<void> {
		this.loaded.push(url);
		const failure = BrowserWindow.loadFailures.shift();
		return failure === undefined ? Promise.resolve() : Promise.reject(failure);
	}
	on(event: string, listener: (...args: never[]) => void): this {
		this.listeners.set(event, listener);
		return this;
	}
	show(): void {
		this.shown += 1;
	}
	hide(): void {
		this.hidden += 1;
	}
	focus(): void {
		this.focused += 1;
	}
	isMinimized(): boolean {
		return this.minimized;
	}
	restore(): void {
		this.restored += 1;
		this.minimized = false;
	}
	/** How many times the window was minimised: a sign-in start with no tray icon to go to. */
	minimizes = 0;
	minimize(): void {
		this.minimizes += 1;
		this.minimized = true;
	}
}

/** Put every stub back to its opening state. Called from a test's own setup. */
export function resetElectronStub(): void {
	for (const key of Object.keys(paths)) delete paths[key];
	dialogAnswers.length = 0;
	folderAnswers.length = 0;
	dialogCalls.length = 0;
	handlers.clear();
	syncHandlers.clear();
	listeners.clear();
	revealed.length = 0;
	openedPaths.length = 0;
	fetchAnswers.length = 0;
	fetched.length = 0;
	sentToPage.length = 0;
	senderIsDestroyed.value = false;
	schemesDeclared.length = 0;
	protocolsHandled.length = 0;
	protocolHandlers.clear();
	clipboardContents.text = '';
	clipboardContents.image = null;
	sender.senderFrame = { url: 'http://127.0.0.1:5171/browse', parent: null };
	contextBridge.exposed = {};
	trays.length = 0;
	BrowserWindow.instances = [];
	BrowserWindow.loadFailures = [];
	appListeners.clear();
	appCalls.quit = 0;
	appCalls.exit = [];
	appCalls.locked = true;
	openedExternally.length = 0;
	cookiesRemoved.length = 0;
	sessionListeners.clear();
	app.isPackaged = false;
	version = '43.4.1';
}
