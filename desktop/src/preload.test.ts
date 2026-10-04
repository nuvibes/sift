/* What the page can reach, asserted as a list.
 *
 * This file's value is that it is SHORT. The preload is the whole security boundary: anything on it
 * is reachable by anything running on the page, so the test that matters is not "does isDesktop
 * work" but "is there nothing else on here". A verb added without a decision fails this.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as channels from './channels';

/* The stub is re-imported INSIDE the reset, not at the top of the file.
 *
 * `resetModules` throws away every loaded module, the stub included, so a stub imported up here
 * would be a DIFFERENT object from the one the freshly loaded preload writes into, and every
 * assertion below would read an empty bridge and fail for a reason that has nothing to do with the
 * preload. */
let exposed: Record<string, unknown>;
let stub: typeof import('../test/electron-stub');

/* Every channel, which is what the main process answers for this machine's own Sift. */
const EVERY_CHANNEL = Object.values(channels).filter((one) => one !== channels.BRIDGE_VERBS);

/** Load the preload into a page the main process answers `permitted` for. Returns what it asked. */
async function load(permitted: unknown, href?: string): Promise<unknown[][]> {
	vi.resetModules();
	stub = await import('../test/electron-stub');
	stub.resetElectronStub();
	const asked: unknown[][] = [];
	stub.ipcMain.on(channels.BRIDGE_VERBS, (event, ...args) => {
		asked.push(args);
		event.returnValue = permitted;
	});
	const had = (globalThis as { location?: unknown }).location;
	if (href !== undefined) (globalThis as { location?: unknown }).location = { href };
	try {
		// Imported for its side effect: the module IS the call.
		await import('./preload');
	} finally {
		(globalThis as { location?: unknown }).location = had;
	}
	exposed = stub.contextBridge.exposed;
	return asked;
}

beforeEach(async () => {
	await load(EVERY_CHANNEL);
});

describe('the preload', () => {
	it('exposes exactly one name, and it is `sift`', () => {
		expect(Object.keys(exposed)).toEqual(['sift']);
	});

	it('says it is the desktop application', () => {
		expect((exposed.sift as { isDesktop: boolean }).isDesktop).toBe(true);
	});

	/* The list, not the presence. Each capability arrives as it is actually built,
	 * and this is the record of what has. If it fails, something was added to the security boundary:
	 * add it here deliberately, with the same care the ones already on it got. */
	it('carries exactly the verbs that have been built', () => {
		expect(Object.keys(exposed.sift as object)).toEqual([
			'isDesktop',
			'chooseFolder',
			/* One database file, for Migrate from Stash. Takes nothing, as the folder dialog. */
			'chooseFile',
			'startDrag',
			'onDragProgress',
			'readClipboard',
			/* A picture of the window or one area of it, for the player's screenshot. The area is
			   four numbers; the picture comes back as bytes and the page saves it itself. */
			'captureWindow',
			'saveServer',
			'lastServer',
			'connectState',
			'forgetServer',
			'chooseMode',
			'suggestedLibrary',
			'chooseLibrary',
			/* Each step of taking the library folder, pushed while it runs: the screen says what
			   the wait is instead of one sentence for all of it. It carries a step's name only. */
			'onSetupProgress',
			'setupBack',
			'applyUpdate',
			'localHardware',
			'machineName',
			'getSharing',
			'setSharing',
			'shellVersion',
			'shellLog',
			'shellLogDetail',
			'firewall',
			'openFirewall',
			'listBrowsers',
			'setBrowser',
			/* Where a saved file lands, read and chosen. Two verbs and not one: the page draws what
			   the folder currently is on every visit to the setting, and only asks for the picker
			   when somebody presses Change, so a read that opened a dialog would be a dialog
			   nobody asked for. */
			'downloadFolder',
			'chooseDownloadFolder',
			/* A backup Sift saved on this computer, shown in its folder. A path crosses, and the
			   shell shows only a Sift backup on its own disk; a page from another computer never
			   gets it, since that backup is on another computer. */
			'showInFolder',
			'storage',
			'moveStorage',
			'onStorageProgress',
			'forgetMode',
			/* Close Sift and open it again, for the settings screen's Restart. It takes nothing, and
			   a page from another computer never gets it: that page has no business closing this
			   machine's application. */
			'restartApp',
			/* What the close button does, and which library this copy is looking at. Two shell
			   settings rather than the library's, because both are about THIS machine: one decides
			   what one press of the window's close button means, and the other is a list of folders
			   on this disk that the process starting a backend is the only thing able to act on. */
			'keepRunningWhenClosed',
			'keepRunning',
			/* Whether Sift starts when this person signs in to Windows, read and written. A plain
			   boolean each way: the shell registers its own executable and nothing a page names. */
			'startsWithWindows',
			'startWithWindows',
			'libraries',
			'openLibrary',
			'addLibrary',
			'forgetLibrary',
			'setTitleBar'
		]);
		/* And each one is a verb, not a name left over from a rename. `offered` is built from the
		   channel list, so an entry of `api` renamed by mistake leaves its key here with nothing
		   behind it, and a page calling it would throw where the key promised a function. */
		for (const [name, verb] of Object.entries(exposed.sift as object)) {
			if (name !== 'isDesktop') expect(typeof verb, name).toBe('function');
		}
	});

	/* A page served by another computer gets the methods the main process names for it, and the
	   screens that would offer the rest draw nothing: they feature-detect each method. */
	it('offers a page from another computer only what a client needs', async () => {
		const { verbsFor } = await import('./verbs');
		await load(verbsFor('remote'));

		expect(Object.keys(exposed.sift as object)).toEqual([
			'isDesktop',
			'startDrag',
			'onDragProgress',
			'readClipboard',
			'captureWindow',
			'lastServer',
			'connectState',
			'localHardware',
			'machineName',
			'shellVersion',
			'listBrowsers',
			'setBrowser',
			'downloadFolder',
			'chooseDownloadFolder',
			'forgetMode',
			'keepRunningWhenClosed',
			'keepRunning',
			'startsWithWindows',
			'startWithWindows',
			'setTitleBar'
		]);
	});

	it('asks with the address of the document it is loading into', async () => {
		const asked = await load(EVERY_CHANNEL, 'http://192.168.1.20:5171/browse');

		expect(asked).toEqual([['http://192.168.1.20:5171/browse']]);
	});

	/* An answer it cannot read (or no answer at all) is no methods, never all of them. */
	it.each([[null], ['everything'], [[42]]])(
		'offers nothing but its name for the answer %j',
		async (answer) => {
			await load(answer);

			expect(Object.keys(exposed.sift as object)).toEqual(['isDesktop']);
		}
	);

	/* The question itself failing (no main process answering it) is no methods too. */
	it('offers nothing but its name when the question cannot be asked', async () => {
		vi.resetModules();
		stub = await import('../test/electron-stub');
		stub.resetElectronStub();
		await import('./preload');

		expect(Object.keys(stub.contextBridge.exposed.sift as object)).toEqual(['isDesktop']);
	});

	/* It takes nothing. A folder dialog that accepted a starting path would let a page steer
	 * somebody towards a folder, and one that accepted a filter would let it learn about the disk
	 * by asking narrower and narrower questions. */
	it('asks for a folder with no arguments at all', () => {
		const verbs = exposed.sift as {
			chooseFolder: (...args: unknown[]) => unknown;
		};
		expect(verbs.chooseFolder.length).toBe(0);
	});

	/* The file dialog the same: no starting path, no filter, no title from the page. */
	it('asks for a file with no arguments at all', () => {
		const verbs = exposed.sift as {
			chooseFile: (...args: unknown[]) => unknown;
		};
		expect(verbs.chooseFile.length).toBe(0);
	});
});

/* Each verb is a channel name and the payload it forwards, and nothing else crosses: no event
 * object, no extra argument, no path where an id was meant. The channel names are declared twice
 * on purpose (an isolated preload cannot import `./channels`), and this is where the two
 * declarations are checked against each other, one verb at a time.
 */
describe('what each verb sends across', () => {
	type Verbs = Record<string, (...args: unknown[]) => Promise<unknown>>;

	const cases: [verb: string, channel: string, args: unknown[], forwarded?: unknown[]][] = [
		['chooseFolder', channels.CHOOSE_FOLDER, []],
		['chooseFile', channels.CHOOSE_FILE, []],
		['startDrag', channels.START_DRAG, ['01HX0000000000000000000001']],
		['readClipboard', channels.READ_CLIPBOARD, []],
		['captureWindow', channels.CAPTURE_WINDOW, [null]],
		['captureWindow', channels.CAPTURE_WINDOW, [{ x: 1, y: 2, width: 3, height: 4 }]],
		/* Only the four numbers cross: anything else riding on the area is left behind, and
		   something that is not an area at all is the whole window. */
		[
			'captureWindow',
			channels.CAPTURE_WINDOW,
			[{ x: 1, y: 2, width: 3, height: 4, element: 'body' }],
			[{ x: 1, y: 2, width: 3, height: 4 }]
		],
		['captureWindow', channels.CAPTURE_WINDOW, [{ x: '1', y: 2, width: 3, height: 4 }], [null]],
		['saveServer', channels.SAVE_SERVER, ['http://10.0.0.5:5171']],
		['lastServer', channels.LAST_SERVER, []],
		['connectState', channels.CONNECT_STATE, []],
		['forgetServer', channels.FORGET_SERVER, ['http://10.0.0.5:5171']],
		['chooseMode', channels.CHOOSE_MODE, ['client']],
		['suggestedLibrary', channels.SUGGESTED_LIBRARY, []],
		['chooseLibrary', channels.CHOOSE_LIBRARY, [true]],
		['setupBack', channels.SETUP_BACK, []],
		['applyUpdate', channels.APPLY_UPDATE, []],
		['localHardware', channels.LOCAL_HARDWARE, []],
		['machineName', channels.MACHINE_NAME, []],
		['getSharing', channels.GET_SHARING, []],
		['setSharing', channels.SET_SHARING, [true]],
		['shellVersion', channels.SHELL_VERSION, []],
		['shellLog', channels.SHELL_LOG, [200]],
		['shellLogDetail', channels.SHELL_LOG_DETAIL, [true, false]],
		['firewall', channels.GET_FIREWALL, []],
		['openFirewall', channels.OPEN_FIREWALL, ['private']],
		['listBrowsers', channels.LIST_BROWSERS, []],
		['setBrowser', channels.SET_BROWSER, ['firefox']],
		['downloadFolder', channels.GET_DOWNLOAD_DIR, []],
		/* Only "start again" crosses as itself; anything else is the null that means "choose". */
		['chooseDownloadFolder', channels.SET_DOWNLOAD_DIR, [true]],
		['chooseDownloadFolder', channels.SET_DOWNLOAD_DIR, [null]],
		['chooseDownloadFolder', channels.SET_DOWNLOAD_DIR, ['C:\\anywhere'], [null]],
		['showInFolder', channels.SHOW_IN_FOLDER, ['C:\\Sift\\backups\\sift-backup-a.zip']],
		/* Anything that is not a path is the empty one, which the shell refuses. */
		['showInFolder', channels.SHOW_IN_FOLDER, [{ path: 'C:\\x' }], ['']],
		['storage', channels.GET_STORAGE, []],
		['moveStorage', channels.MOVE_STORAGE, []],
		['forgetMode', channels.FORGET_MODE, []],
		['restartApp', channels.RESTART_APP, []],
		['startsWithWindows', channels.GET_START_WITH_WINDOWS, []],
		['startWithWindows', channels.SET_START_WITH_WINDOWS, [true]],
		['keepRunningWhenClosed', channels.GET_KEEP_RUNNING, []],
		['keepRunning', channels.SET_KEEP_RUNNING, [false]],
		['libraries', channels.LIST_LIBRARIES, []],
		['openLibrary', channels.OPEN_LIBRARY, ['D:\\Other\\data']],
		['addLibrary', channels.ADD_LIBRARY, []],
		['forgetLibrary', channels.FORGET_LIBRARY, ['D:\\Other\\data']],
		['setTitleBar', channels.SET_TITLE_BAR, [{ color: '#111', symbolColor: '#eee' }]]
	];

	it.each(cases)(
		'%s asks %s with its payload and nothing else',
		async (verb, channel, args, forwarded) => {
			const crossed: unknown[][] = [];
			stub.ipcMain.handle(channel, (_event, ...got) => {
				crossed.push(got);
				return 'the answer';
			});

			await expect((exposed.sift as Verbs)[verb](...args)).resolves.toBe('the answer');

			expect(crossed).toEqual([forwarded ?? args]);
		}
	);

	/* The three listeners: the payload alone reaches the page (the event object carries a `sender`
	 * that would be the whole IPC surface), and the function handed back is how it stops. */
	it.each([
		['onDragProgress', channels.DRAG_PROGRESS],
		['onStorageProgress', channels.STORAGE_PROGRESS],
		['onSetupProgress', channels.SETUP_PROGRESS]
	])('%s relays the payload alone, until told to stop', (verb, channel) => {
		const heard: unknown[] = [];
		const verbs = exposed.sift as Record<string, (listen: (p: unknown) => void) => () => void>;
		const stop = verbs[verb]((progress) => heard.push(progress));

		for (const listener of stub.listeners.get(channel) ?? []) {
			listener({ sender: 'the whole of ipc' }, { done: 3, total: 9 });
		}
		expect(heard).toEqual([{ done: 3, total: 9 }]);

		stop();
		expect(stub.listeners.get(channel)?.size ?? 0).toBe(0);
	});
});
