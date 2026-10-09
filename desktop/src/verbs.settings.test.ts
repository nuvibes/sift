/* The verbs about this copy's own settings and files: where a saved file lands, the backup,
 * setting the copy up, the shell's own log, the browser a link opens in, the two folders,
 * closing and starting with Windows, the libraries opened, client mode, and applying an update. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const FAKE_ADDON = path.resolve(__dirname, '..', 'test', 'fake-drag-addon.cjs');
vi.mock('./paths', async (importOriginal) => ({
	...(await importOriginal<typeof import('./paths')>()),
	dragAddonFile: () => FAKE_ADDON
}));

vi.mock('./firewall', () => ({
	firewallState: vi.fn(async (port: number) => `read:${port}` as unknown as string),
	openFirewall: vi.fn(async (port: number) => `open:${port}` as unknown as string)
}));

vi.mock('./transfers', () => ({
	transfers: () => ({
		start: async (
			_url: string,
			_headers: Record<string, string>,
			at: { partial: string; finished: string },
			total: number | null,
			onProgress: (received: number, total: number | null) => void
		) => {
			fs.mkdirSync(path.dirname(at.finished), { recursive: true });
			fs.writeFileSync(at.finished, 'data');
			onProgress(total ?? 4, total);
			return at.finished;
		}
	})
}));

const BROWSERS = [
	{ id: 'C:\\Program Files\\Bluebird\\bluebird.exe', name: 'Bluebird' },
	{ id: 'C:\\Program Files\\Kestrel\\kestrel.exe', name: 'Kestrel' }
];
vi.mock('./browsers', () => ({
	installed: vi.fn(async () => BROWSERS)
}));

const SHELL_LOG_ANSWER = {
	lines: ['one line'],
	path: 'C:\\Sift\\shell.log',
	size: 9,
	present: true
};
vi.mock('./log', async (importOriginal) => ({
	...(await importOriginal<typeof import('./log')>()),
	tail: vi.fn(() => SHELL_LOG_ANSWER)
}));

vi.mock('./update', async (importOriginal) => ({
	...(await importOriginal<typeof import('./update')>()),
	applyUpdate: vi.fn(async () => ({ ok: true, version: '0.1.157' }))
}));

type StubInterface = { family: string; internal: boolean; address: string };
const machineInterfaces: Record<string, StubInterface[] | undefined> = {};
vi.mock('node:os', async (importOriginal) => ({
	...(await importOriginal<typeof import('node:os')>()),
	networkInterfaces: () => machineInterfaces
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const addon = require(FAKE_ADDON) as {
	calls: {
		verb: string;
		path?: string;
		partial?: string;
		finished?: string;
		name?: string;
		total?: number;
	}[];
};

import {
	app,
	folderAnswers,
	ipcRenderer,
	paths,
	resetElectronStub,
	revealed,
	sender,
	senderIsDestroyed,
	sentToPage,
	setVersion
} from '../test/electron-stub';
import * as channels from './channels';
import {
	ADD_LIBRARY,
	APPLY_UPDATE,
	FORGET_LIBRARY,
	FORGET_MODE,
	RESTART_APP,
	GET_DOWNLOAD_DIR,
	GET_KEEP_RUNNING,
	GET_START_WITH_WINDOWS,
	GET_STORAGE,
	LAST_SERVER,
	LIST_BROWSERS,
	LIST_LIBRARIES,
	MOVE_STORAGE,
	OPEN_LIBRARY,
	SAVE_SERVER,
	SETUP_BACK,
	SET_BROWSER,
	SET_DOWNLOAD_DIR,
	SET_KEEP_RUNNING,
	SET_START_WITH_WINDOWS,
	SHELL_LOG,
	SHELL_LOG_DETAIL,
	STORAGE_PROGRESS
} from './channels';
import {
	isDetailed,
	isHidingPersonal,
	log,
	setDetail,
	setHidePersonal,
	tail as tailShellLog
} from './log';
import type { Reach } from './origins';

import { applyUpdate, DEFAULT_FEED_URL } from './update';
import {
	CHOOSE_LIBRARY,
	SETUP_PROGRESS,
	CHOOSE_MODE,
	SUGGESTED_LIBRARY,
	registerVerbs,
	verbsFor
} from './verbs';

const TRUSTED = 'http://127.0.0.1:5171';

const localAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'local' : null;
const remoteAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'remote' : null;

let temp: string;

beforeEach(() => {
	resetElectronStub();
	temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-verbs-'));
	paths.temp = temp;
	addon.calls.length = 0;
	registerVerbs(localAt(TRUSTED));
});

afterEach(() => {
	fs.rmSync(temp, { recursive: true, force: true });
});

/* Where a saved file lands. */
describe('where a saved file lands', () => {
	let chosen: string | null;

	function armed() {
		chosen = null;
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			downloads: {
				folder: () => chosen ?? 'C:\\Users\\somebody\\Downloads',
				chosen: () => chosen,
				choose: (dir: string | null) => {
					chosen = dir;
				}
			},
			port: 5171
		});
	}

	beforeEach(armed);

	it('says both where files land and whether that was chosen', async () => {
		/* "Downloads" and "the folder I chose that happens to be Downloads" are different states
		   and only one of them follows the machine if it is ever moved. */
		expect(await ipcRenderer.invoke(GET_DOWNLOAD_DIR)).toEqual({
			path: 'C:\\Users\\somebody\\Downloads',
			chosen: false
		});
	});

	it('takes a folder only from the operating system-s own picker', async () => {
		/* The security property, and the reason there is no verb that takes a path: a page
		   naming a folder to write into would be a page choosing where this application writes. */
		folderAnswers.push(['D:\\Saved']);

		expect(await ipcRenderer.invoke(SET_DOWNLOAD_DIR, true)).toEqual({
			path: 'D:\\Saved',
			chosen: true
		});
	});

	it('leaves the folder alone when the picker is closed without one', async () => {
		folderAnswers.push(['D:\\Saved']);
		await ipcRenderer.invoke(SET_DOWNLOAD_DIR, true);
		folderAnswers.push(null);

		expect(await ipcRenderer.invoke(SET_DOWNLOAD_DIR, true)).toEqual({
			path: 'D:\\Saved',
			chosen: true
		});
	});

	it('goes back to the machine-s own folder, and that is the one value a page may send', async () => {
		folderAnswers.push(['D:\\Saved']);
		await ipcRenderer.invoke(SET_DOWNLOAD_DIR, true);

		expect(await ipcRenderer.invoke(SET_DOWNLOAD_DIR, null)).toEqual({
			path: 'C:\\Users\\somebody\\Downloads',
			chosen: false
		});
		// And no dialog was opened to do it: clearing a choice is not choosing one.
		expect(folderAnswers.length).toBe(0);
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		folderAnswers.push(['D:\\Saved']);

		expect(await ipcRenderer.invoke(GET_DOWNLOAD_DIR)).toBeNull();
		expect(await ipcRenderer.invoke(SET_DOWNLOAD_DIR, true)).toBeNull();
		// And the picker was never opened, so nothing was shown to the person.
		expect(folderAnswers.length).toBe(1);
	});

	it('answers nothing at all on a shell that has no folder to give', async () => {
		/* Null where there is no shell to ask (the tests of everything else here, and any build
		   where the setting is not wired). */
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(GET_DOWNLOAD_DIR)).toBeNull();
		expect(await ipcRenderer.invoke(SET_DOWNLOAD_DIR, null)).toBeNull();
	});
});

/* The two questions asked before a backend exists. */
/* The Backup pane's Show in folder: a backup Sift saved on this computer, shown in its folder in
 * the system's own file manager. */
describe('showing a saved backup in its folder', () => {
	const NAME = 'sift-backup-0123456789ab-20260720-141500-0400-1.2.3-saved.zip';

	it('shows a Sift backup that is on this disk, and nothing else', async () => {
		const backup = path.join(temp, NAME);
		fs.writeFileSync(backup, 'PK');
		const program = path.join(temp, 'holiday.exe');
		fs.writeFileSync(program, 'MZ');
		const folderNamedLikeOne = path.join(temp, 'sift-backup-a.zip');
		fs.mkdirSync(folderNamedLikeOne);

		expect(await ipcRenderer.invoke(channels.SHOW_IN_FOLDER, backup)).toBe(true);
		expect(revealed).toEqual([backup]);

		for (const refused of [
			program,
			path.join(temp, 'sift-backup-not-there.zip'),
			folderNamedLikeOne,
			NAME,
			'',
			42,
			null
		]) {
			expect(await ipcRenderer.invoke(channels.SHOW_IN_FOLDER, refused)).toBe(false);
		}
		expect(revealed).toEqual([backup]);
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		const backup = path.join(temp, NAME);
		fs.writeFileSync(backup, 'PK');
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(channels.SHOW_IN_FOLDER, backup)).toBe(false);
		expect(revealed).toEqual([]);
	});

	it('is not offered to a page from another computer, whose backups are not on this disk', () => {
		expect(verbsFor('remote')).not.toContain(channels.SHOW_IN_FOLDER);
		expect(verbsFor('local')).toContain(channels.SHOW_IN_FOLDER);
	});
});

describe('setting this copy up', () => {
	function withSetup() {
		const mode = vi.fn().mockResolvedValue({ ok: true, refusal: null });
		const library = vi.fn().mockResolvedValue({ ok: true, refusal: null });
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			setup: {
				mode,
				suggested: async () => ({
					path: 'C:\\Users\\someone\\AppData\\Local\\Sift\\data',
					existing: false
				}),
				library,
				back: async () => ({ ok: true, refusal: null })
			}
		});
		return { mode, library };
	}

	it('passes a mode the shell knows straight through', async () => {
		const { mode } = withSetup();

		expect(await ipcRenderer.invoke(CHOOSE_MODE, 'client')).toEqual({
			ok: true,
			refusal: null
		});
		expect(mode).toHaveBeenCalledWith('client');
	});

	/* The argument decides whether a backend is started on this machine at all, and it arrives
	 * from a page. */
	it('and refuses anything that is not one of the two modes', async () => {
		const { mode } = withSetup();

		expect(await ipcRenderer.invoke(CHOOSE_MODE, 'standalone-ish')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(CHOOSE_MODE, { mode: 'client' })).toEqual({
			ok: false,
			refusal: null
		});
		expect(mode).not.toHaveBeenCalled();
	});

	it('offers the folder the library would go in', async () => {
		withSetup();

		expect(await ipcRenderer.invoke(SUGGESTED_LIBRARY)).toEqual({
			path: expect.stringContaining('Sift'),
			existing: false
		});
	});

	/* `pick` is what decides whether the machine's own folder dialog opens, so it has to arrive
	   as a real boolean rather than as anything truthy a page happened to send. */
	it('opens the machine own dialog only when asked to, and never on a stray value', async () => {
		const { library } = withSetup();

		await ipcRenderer.invoke(CHOOSE_LIBRARY, true);
		expect(library).toHaveBeenCalledWith(true, expect.any(Function));

		await ipcRenderer.invoke(CHOOSE_LIBRARY, 'yes please');
		expect(library).toHaveBeenLastCalledWith(false, expect.any(Function));
	});

	/* The first start sets up the database, and one sentence for that long reads as stuck: each
	   step is sent as it starts, and a window that has closed is told nothing. */
	it('tells the page each step of taking the folder as it starts', async () => {
		const { library } = withSetup();
		library.mockImplementation(async (_pick: boolean, told: (step: string) => void) => {
			told('checking');
			told('starting');
			return { ok: true, refusal: null };
		});

		await ipcRenderer.invoke(CHOOSE_LIBRARY, false);
		expect(sentToPage).toEqual([
			{ channel: SETUP_PROGRESS, payload: 'checking' },
			{ channel: SETUP_PROGRESS, payload: 'starting' }
		]);
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		const { mode, library } = withSetup();
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(CHOOSE_MODE, 'standalone')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(SUGGESTED_LIBRARY)).toBeNull();
		expect(await ipcRenderer.invoke(CHOOSE_LIBRARY, false)).toEqual({
			ok: false,
			refusal: null
		});
		expect(mode).not.toHaveBeenCalled();
		expect(library).not.toHaveBeenCalled();
	});

	/* Registered on every shell, not only a fresh one: the window can be sent back to these
	   screens by forgetting the mode, and a handler that appeared only on a first run would be
	   one nothing ever exercises. */
	it('and answers rather than throwing on a shell with no setup wired', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(CHOOSE_MODE, 'standalone')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(SUGGESTED_LIBRARY)).toBeNull();
		expect(await ipcRenderer.invoke(CHOOSE_LIBRARY, true)).toEqual({
			ok: false,
			refusal: null
		});
	});
});

/* The end of the shell's own log. A count in and nothing else (no path, no offset, no filter),
 * so the whole of what a page can influence is how many lines it is given. */
/* The library's Detail setting, handed to the shell's own log. */
describe('the detail the shell own log writes', () => {
	beforeEach(() => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));
		setDetail(false);
	});

	afterEach(() => {
		setDetail(false);
		setHidePersonal(true);
	});

	it('takes Hide personal details in the log beside it, and keeps it from a page that sends none', async () => {
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, false, false)).toBe(false);
		expect(isHidingPersonal()).toBe(false);
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, false)).toBe(false);
		expect(isHidingPersonal()).toBe(false);
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, false, 'yes')).toBe(false);
		expect(isHidingPersonal()).toBe(false);
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, false, true)).toBe(false);
		expect(isHidingPersonal()).toBe(true);
	});

	it('takes the setting as the page read it', async () => {
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, true)).toBe(true);
		expect(isDetailed()).toBe(true);
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, 'yes')).toBeNull();
		expect(isDetailed()).toBe(true);
		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, false)).toBe(false);
		expect(isDetailed()).toBe(false);
	});

	it('refuses a page that is not Sift, and changes nothing', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(SHELL_LOG_DETAIL, true)).toBeNull();
		expect(isDetailed()).toBe(false);
	});
});

describe('the end of the shell own log', () => {
	beforeEach(() => {
		vi.mocked(tailShellLog).mockClear();
	});

	it('answers the four facts the log screen draws', async () => {
		expect(await ipcRenderer.invoke(SHELL_LOG, 5)).toEqual(SHELL_LOG_ANSWER);
		expect(vi.mocked(tailShellLog)).toHaveBeenCalledWith(5);
	});

	it('asks for two hundred lines when the page names no number', async () => {
		await ipcRenderer.invoke(SHELL_LOG);
		expect(vi.mocked(tailShellLog)).toHaveBeenCalledWith(200);

		await ipcRenderer.invoke(SHELL_LOG, 'lots');
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(200);

		// Infinity is a number and would otherwise pass the type check into the clamp as itself.
		await ipcRenderer.invoke(SHELL_LOG, Number.POSITIVE_INFINITY);
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(200);
	});

	it('never reads more than two thousand lines, nor fewer than one', async () => {
		await ipcRenderer.invoke(SHELL_LOG, 100_000);
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(2000);

		await ipcRenderer.invoke(SHELL_LOG, 0);
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(1);

		await ipcRenderer.invoke(SHELL_LOG, -40);
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(1);
	});

	it('takes the whole number of a fraction rather than handing one on', async () => {
		await ipcRenderer.invoke(SHELL_LOG, 12.7);
		expect(vi.mocked(tailShellLog)).toHaveBeenLastCalledWith(12);
	});

	it('refuses a page that is not Sift, and reads no file for it', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(SHELL_LOG, 5)).toBeNull();
		expect(vi.mocked(tailShellLog)).not.toHaveBeenCalled();
	});
});

/* Which browser a link opens in. */
describe('which browser a link opens in', () => {
	let chosen: string | null;

	function armed() {
		chosen = null;
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			links: {
				chosen: () => chosen,
				choose: (id: string | null) => {
					chosen = id;
				}
			}
		});
	}

	beforeEach(armed);

	it('lists what this machine has, and which of them is chosen', async () => {
		expect(await ipcRenderer.invoke(LIST_BROWSERS)).toEqual({
			chosen: null,
			browsers: BROWSERS
		});
	});

	it('takes an id that is on the list', async () => {
		expect(await ipcRenderer.invoke(SET_BROWSER, BROWSERS[1]?.id)).toEqual({
			chosen: BROWSERS[1]?.id,
			browsers: BROWSERS
		});
		expect(chosen).toBe(BROWSERS[1]?.id);
	});

	/* The one that matters. A verb that took any string would be a verb for "run this
	   executable", reachable by anything that got code onto the page. */
	it('refuses a path that is not one of the machine own browsers', async () => {
		await ipcRenderer.invoke(SET_BROWSER, BROWSERS[0]?.id);

		expect(await ipcRenderer.invoke(SET_BROWSER, 'C:\\Windows\\System32\\cmd.exe')).toEqual({
			chosen: BROWSERS[0]?.id,
			browsers: BROWSERS
		});
		// And a shape that is not even a path leaves the choice exactly as it was.
		expect(await ipcRenderer.invoke(SET_BROWSER, { id: 'anything' })).toMatchObject({
			chosen: BROWSERS[0]?.id
		});
	});

	/* Null is the one value that is not an id, and it means "whatever Windows would have used". */
	it('goes back to the machine own default when the page sends nothing', async () => {
		await ipcRenderer.invoke(SET_BROWSER, BROWSERS[0]?.id);

		expect(await ipcRenderer.invoke(SET_BROWSER, null)).toEqual({
			chosen: null,
			browsers: BROWSERS
		});
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(LIST_BROWSERS)).toEqual({
			chosen: null,
			browsers: []
		});
		expect(await ipcRenderer.invoke(SET_BROWSER, BROWSERS[0]?.id)).toEqual({
			chosen: null,
			browsers: []
		});
		expect(chosen).toBeNull();
	});

	it('answers an empty list on a shell where links are not wired', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(LIST_BROWSERS)).toEqual({
			chosen: null,
			browsers: []
		});
		expect(await ipcRenderer.invoke(SET_BROWSER, null)).toEqual({
			chosen: null,
			browsers: []
		});
	});
});

/* Sift's own two folders, and moving them somewhere else. */
describe('where Sift keeps its own two folders', () => {
	const report = {
		dataDir: 'C:\\Sift\\data',
		cacheDir: 'C:\\Sift\\cache',
		dataBytes: 900,
		cacheBytes: 120
	};
	const forget = vi.fn();
	let moves: string[];

	function armed() {
		moves = [];
		forget.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			storage: {
				read: async () => report,
				move: async (target, tell) => {
					moves.push(target);
					tell(1, 2);
					tell(2, 2);
					return {
						ok: true,
						locations: {
							dataDir: `${target}\\data`,
							cacheDir: `${target}\\cache`
						},
						renamed: false
					};
				},
				forget
			}
		});
	}

	beforeEach(armed);

	it('says where they are and what they hold', async () => {
		expect(await ipcRenderer.invoke(GET_STORAGE)).toEqual(report);
	});

	it('moves them to the folder somebody pointed at, and to nothing else', async () => {
		folderAnswers.push(['E:\\Sift']);

		expect(await ipcRenderer.invoke(MOVE_STORAGE)).toEqual({
			ok: true,
			locations: { dataDir: 'E:\\Sift\\data', cacheDir: 'E:\\Sift\\cache' },
			renamed: false
		});
		expect(moves).toEqual(['E:\\Sift']);
	});

	/* A copy across drives takes minutes, and a screen with nothing on it for minutes is a
	   screen that looks stuck. */
	it('tells the page how far along the copy is, as it goes', async () => {
		folderAnswers.push(['E:\\Sift']);

		await ipcRenderer.invoke(MOVE_STORAGE);

		expect(sentToPage).toEqual([
			{ channel: STORAGE_PROGRESS, payload: { copied: 1, total: 2 } },
			{ channel: STORAGE_PROGRESS, payload: { copied: 2, total: 2 } }
		]);
	});

	/* A move outlives the window that started it (somebody can close it half way through a copy
	   of ninety gigabytes), and sending to a gone WebContents throws. */
	it('says nothing to a window that has closed', async () => {
		folderAnswers.push(['E:\\Sift']);
		senderIsDestroyed.value = true;

		await ipcRenderer.invoke(MOVE_STORAGE);

		expect(sentToPage).toEqual([]);
	});

	/* Closing the picker is not a failure and must not be reported as one: `reason` null leaves
	   the screen exactly as it was, where a sentence would put an error on it. */
	it('treats a closed picker as no answer rather than as a failure', async () => {
		folderAnswers.push(null);

		expect(await ipcRenderer.invoke(MOVE_STORAGE)).toEqual({
			ok: false,
			reason: null
		});
		expect(moves).toEqual([]);
	});

	it('refuses a page that has been navigated somewhere else, and opens no picker', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		folderAnswers.push(['E:\\Sift']);

		expect(await ipcRenderer.invoke(GET_STORAGE)).toBeNull();
		expect(await ipcRenderer.invoke(MOVE_STORAGE)).toEqual({
			ok: false,
			reason: 'Not available here.'
		});
		expect(folderAnswers.length).toBe(1);
	});

	/* Restart answers before it restarts: the page that asked goes down with the application. */
	it('restarts for a local page, once, and says it will', async () => {
		const restart = vi.fn(async () => {});
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), { restart });

		expect(await ipcRenderer.invoke(RESTART_APP)).toBe(true);
		await new Promise((settle) => setImmediate(settle));
		expect(restart).toHaveBeenCalledOnce();
	});

	it('restarts nothing for a page that is not Sift, or a shell that cannot', async () => {
		const restart = vi.fn(async () => {});
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), { restart });
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		expect(await ipcRenderer.invoke(RESTART_APP)).toBe(false);

		armed();
		expect(await ipcRenderer.invoke(RESTART_APP)).toBe(false);
		expect(restart).not.toHaveBeenCalled();
	});

	/* Forgetting is a write and nothing else: no backend to stop, nothing to move. */
	it('forgets which way this copy was set up, and says it did', async () => {
		expect(await ipcRenderer.invoke(FORGET_MODE)).toBe(true);
		expect(forget).toHaveBeenCalledOnce();
	});

	it('forgets nothing for a page that is not Sift', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(FORGET_MODE)).toBe(false);
		expect(forget).not.toHaveBeenCalled();
	});

	it('answers rather than throwing on a shell with no storage wired', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(GET_STORAGE)).toBeNull();
		expect(await ipcRenderer.invoke(FORGET_MODE)).toBe(false);
		expect(await ipcRenderer.invoke(MOVE_STORAGE)).toEqual({
			ok: false,
			reason: 'Not available here.'
		});
	});
});

/* Whether closing the window leaves Sift running. */
describe('whether closing the window leaves Sift running', () => {
	let keeping: boolean;

	function armed() {
		keeping = false;
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			closing: {
				keepRunning: () => keeping,
				keep: (on: boolean) => {
					keeping = on;
				}
			}
		});
	}

	beforeEach(armed);

	it('answers what the close button does today', async () => {
		expect(await ipcRenderer.invoke(GET_KEEP_RUNNING)).toBe(false);
	});

	it('answers what is true after the change, not the word that was sent', async () => {
		expect(await ipcRenderer.invoke(SET_KEEP_RUNNING, true)).toBe(true);
		expect(keeping).toBe(true);
	});

	/* Checked against `true` rather than cast. It decides whether a window somebody closed keeps
	   this machine doing work, and it arrives from a page, so anything else is off. */
	it('reads anything that is not yes as no', async () => {
		await ipcRenderer.invoke(SET_KEEP_RUNNING, true);

		expect(await ipcRenderer.invoke(SET_KEEP_RUNNING, 'yes')).toBe(false);
		expect(keeping).toBe(false);
	});

	it('refuses a page that has been navigated somewhere else, and changes nothing', async () => {
		await ipcRenderer.invoke(SET_KEEP_RUNNING, true);
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(GET_KEEP_RUNNING)).toBeNull();
		expect(await ipcRenderer.invoke(SET_KEEP_RUNNING, false)).toBeNull();
		expect(keeping).toBe(true);
	});

	it('answers nothing on a shell that has no close behaviour to give', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(GET_KEEP_RUNNING)).toBeNull();
		expect(await ipcRenderer.invoke(SET_KEEP_RUNNING, true)).toBeNull();
	});
});

/* Whether Sift starts when this person signs in to Windows. */
describe('whether Sift starts with Windows', () => {
	let windowsSays: boolean | null;
	let asked: boolean[];

	beforeEach(() => {
		windowsSays = false;
		asked = [];
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			startup: {
				read: () => windowsSays,
				/* Windows keeps only what it keeps (here, nothing), and the verb must say so. */
				write: (on: boolean) => {
					asked.push(on);
					return windowsSays;
				}
			}
		});
	});

	it('answers what Windows says today', async () => {
		expect(await ipcRenderer.invoke(GET_START_WITH_WINDOWS)).toBe(false);
	});

	it('answers what the shell read back, not the word that was sent', async () => {
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, true)).toBe(false);
		expect(asked).toEqual([true]);
	});

	/* Checked against `true` rather than cast: it adds a program to every sign-in, and it
	   arrives from a page. */
	it('adds nothing to sign-in for anything that is not yes', async () => {
		await ipcRenderer.invoke(SET_START_WITH_WINDOWS, 'yes');
		await ipcRenderer.invoke(SET_START_WITH_WINDOWS, 1);

		expect(asked).toEqual([false, false]);
	});

	it("passes on a checkout's null rather than inventing an answer", async () => {
		windowsSays = null;

		expect(await ipcRenderer.invoke(GET_START_WITH_WINDOWS)).toBeNull();
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, true)).toBeNull();
	});

	it('refuses a navigated page, and registers nothing at sign-in', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(GET_START_WITH_WINDOWS)).toBeNull();
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, true)).toBeNull();
		expect(asked).toEqual([]);
	});

	it('answers nothing on a shell that was given no switch', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(GET_START_WITH_WINDOWS)).toBeNull();
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, true)).toBeNull();
	});
});

/* The libraries this copy has opened, and switching between them. */
describe('the libraries this copy has opened', () => {
	const list = {
		current: 'C:\\Sift\\data',
		libraries: [
			{
				dataDir: 'C:\\Sift\\data',
				cacheDir: 'C:\\Sift\\cache',
				name: 'Sift',
				lastOpened: 2
			},
			{
				dataDir: 'E:\\Spare\\data',
				cacheDir: 'E:\\Spare\\cache',
				name: 'Spare',
				lastOpened: 1
			}
		]
	};
	const book = {
		list: vi.fn(() => list),
		open: vi.fn(async () => ({ ok: true, refusal: null })),
		add: vi.fn(async () => ({ ok: true, refusal: null })),
		forget: vi.fn(() => ({
			current: list.current,
			libraries: [list.libraries[0]!]
		}))
	};

	function armed() {
		book.list.mockClear();
		book.open.mockClear();
		book.add.mockClear();
		book.forget.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			libraries: book
		});
	}

	beforeEach(armed);

	it('lists every one of them, and which is open now', async () => {
		expect(await ipcRenderer.invoke(LIST_LIBRARIES)).toEqual(list);
	});

	it('opens one the page names', async () => {
		expect(await ipcRenderer.invoke(OPEN_LIBRARY, 'E:\\Spare\\data')).toEqual({
			ok: true,
			refusal: null
		});
		expect(book.open).toHaveBeenCalledWith('E:\\Spare\\data');
	});

	/* The shape is checked here; whether the folder is one this copy has opened is checked
	   behind the door, where the list lives. */
	it('refuses anything that is not a folder name, and asks the book nothing', async () => {
		expect(await ipcRenderer.invoke(OPEN_LIBRARY, '')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(OPEN_LIBRARY, 42)).toEqual({
			ok: false,
			refusal: null
		});
		expect(book.open).not.toHaveBeenCalled();
	});

	/* Adding one takes no argument at all: the picker is the grant, so there is no path for a
	   page to name. */
	it('adds one through the machine own picker, with nothing from the page', async () => {
		expect(await ipcRenderer.invoke(ADD_LIBRARY, 'C:\\Windows')).toEqual({
			ok: true,
			refusal: null
		});
		expect(book.add).toHaveBeenCalledWith();
	});

	it('forgets one and answers with the list afterwards', async () => {
		expect(await ipcRenderer.invoke(FORGET_LIBRARY, 'E:\\Spare\\data')).toEqual({
			current: 'C:\\Sift\\data',
			libraries: [list.libraries[0]]
		});
		expect(book.forget).toHaveBeenCalledWith('E:\\Spare\\data');
	});

	it('forgets nothing for a value that is not a folder name, and answers the list', async () => {
		expect(await ipcRenderer.invoke(FORGET_LIBRARY, '')).toEqual(list);
		expect(book.forget).not.toHaveBeenCalled();
	});

	it('refuses every one of them to a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(LIST_LIBRARIES)).toBeNull();
		expect(await ipcRenderer.invoke(OPEN_LIBRARY, 'E:\\Spare\\data')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(ADD_LIBRARY)).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(FORGET_LIBRARY, 'E:\\Spare\\data')).toBeNull();
		expect(book.open).not.toHaveBeenCalled();
		expect(book.add).not.toHaveBeenCalled();
		expect(book.forget).not.toHaveBeenCalled();
	});

	it('answers rather than throwing on a shell with no library list', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(LIST_LIBRARIES)).toBeNull();
		expect(await ipcRenderer.invoke(OPEN_LIBRARY, 'E:\\Spare\\data')).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(ADD_LIBRARY)).toEqual({
			ok: false,
			refusal: null
		});
		expect(await ipcRenderer.invoke(FORGET_LIBRARY, 'E:\\Spare\\data')).toBeNull();
	});
});

/* The address client mode was told, and the sentence that comes back when it does not answer. */
describe('the address client mode was told', () => {
	const book = {
		remember: vi.fn(async () => null),
		last: () => 'http://10.0.0.5:5171',
		problem: () => null,
		saved: () => [],
		forget: () => []
	};

	function armed() {
		book.remember.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			servers: book
		});
	}

	beforeEach(armed);

	it('hands an address to the shell, which answers nothing when it worked', async () => {
		expect(await ipcRenderer.invoke(SAVE_SERVER, 'http://10.0.0.5:5171')).toBeNull();
		expect(book.remember).toHaveBeenCalledWith('http://10.0.0.5:5171');
	});

	it('says what is wrong in words a person can read, and tries nothing', async () => {
		expect(await ipcRenderer.invoke(SAVE_SERVER, 5171)).toBe('That is not an address.');
		expect(book.remember).not.toHaveBeenCalled();
	});

	it('offers the last address so a correction starts from it', async () => {
		expect(await ipcRenderer.invoke(LAST_SERVER)).toBe('http://10.0.0.5:5171');
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(SAVE_SERVER, 'http://10.0.0.5:5171')).toBe(
			'This only works in the Sift app.'
		);
		expect(await ipcRenderer.invoke(LAST_SERVER)).toBeNull();
		expect(book.remember).not.toHaveBeenCalled();
	});

	it('says so in a browser, where there is no shell to remember anything', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(SAVE_SERVER, 'http://10.0.0.5:5171')).toBe(
			'This only works in the Sift app.'
		);
		expect(await ipcRenderer.invoke(LAST_SERVER)).toBeNull();
	});
});

/* Applying an update. NO ARGUMENTS, and that is the whole design of the verb. */
describe('applying an update', () => {
	const stopForUpdate = vi.fn(async () => {});

	function armed(feed: string | null) {
		vi.mocked(applyUpdate).mockClear();
		stopForUpdate.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			feedUrl: () => feed,
			port: 5171,
			stopForUpdate
		});
	}

	it('reads the feed this shell was given, and nothing the page named', async () => {
		armed('https://updates.example/sift.json');

		expect(await ipcRenderer.invoke(APPLY_UPDATE, 'https://elsewhere.example/evil.json')).toEqual({
			ok: true,
			version: '0.1.157'
		});
		expect(vi.mocked(applyUpdate).mock.calls[0]?.[0]).toBe('https://updates.example/sift.json');
	});

	it('falls back to Sift own feed where the shell was handed no feed at all', async () => {
		vi.mocked(applyUpdate).mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		await ipcRenderer.invoke(APPLY_UPDATE);

		expect(vi.mocked(applyUpdate).mock.calls[0]?.[0]).toBe(DEFAULT_FEED_URL);
	});

	it('falls back to Sift own feed where the shell names none', async () => {
		armed(null);

		await ipcRenderer.invoke(APPLY_UPDATE);

		expect(vi.mocked(applyUpdate).mock.calls[0]?.[0]).toBe(DEFAULT_FEED_URL);
	});

	/* The backend has the library open, and an installer cannot replace files a running process
	   is holding. */
	it('stops the backend before the installer is launched', async () => {
		armed(null);

		await ipcRenderer.invoke(APPLY_UPDATE);
		const options = vi.mocked(applyUpdate).mock.calls[0]?.[2];
		await options?.beforeLaunch?.('0.1.157');

		expect(stopForUpdate).toHaveBeenCalledOnce();
	});

	it('writes the check and what it found to the shell log', async () => {
		armed(null);
		const info = vi.spyOn(log, 'info');
		const warning = vi.spyOn(log, 'warning');
		try {
			await ipcRenderer.invoke(APPLY_UPDATE);
			vi.mocked(applyUpdate).mockResolvedValueOnce({ ok: false, reason: 'unverified' });
			await ipcRenderer.invoke(APPLY_UPDATE);

			expect(info.mock.calls).toEqual([
				['update.checking', { running: 'a checkout' }],
				['update.installing', { version: '0.1.157' }],
				['update.checking', { running: 'a checkout' }]
			]);
			expect(warning.mock.calls).toEqual([['update.not_installed', { reason: 'unverified' }]]);
		} finally {
			info.mockRestore();
			warning.mockRestore();
		}
	});

	it('refuses a page that has been navigated somewhere else, and reads no feed', async () => {
		armed(null);
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(APPLY_UPDATE)).toEqual({
			ok: false,
			reason: 'failed'
		});
		expect(vi.mocked(applyUpdate)).not.toHaveBeenCalled();
	});
});

/* The way back through the first-run questions. */
describe('the way back through the first-run questions', () => {
	const back = vi.fn(async () => ({ ok: true, refusal: null }));

	function armed() {
		back.mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED), {
			port: 5171,
			setup: {
				mode: async () => ({ ok: true, refusal: null }),
				suggested: async () => ({ path: 'C:\\Sift', existing: false }),
				library: async () => ({ ok: true, refusal: null }),
				back
			}
		});
	}

	beforeEach(armed);

	it('unsays the last answer, so the question before it is asked again', async () => {
		expect(await ipcRenderer.invoke(SETUP_BACK)).toEqual({
			ok: true,
			refusal: null
		});
		expect(back).toHaveBeenCalledOnce();
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(SETUP_BACK)).toEqual({
			ok: false,
			refusal: null
		});
		expect(back).not.toHaveBeenCalled();
	});

	it('answers rather than throwing on a shell with no setup wired', async () => {
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		expect(await ipcRenderer.invoke(SETUP_BACK)).toEqual({
			ok: false,
			refusal: null
		});
	});
});

describe('the version an update has to beat', () => {
	it('is this copy own version when installed, and nothing from a checkout', async () => {
		vi.mocked(applyUpdate).mockClear();
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));

		await ipcRenderer.invoke(APPLY_UPDATE);
		expect(vi.mocked(applyUpdate).mock.calls[0]?.[2]?.running).toBeNull();

		app.isPackaged = true;
		setVersion('0.2.0');
		await ipcRenderer.invoke(APPLY_UPDATE);
		expect(vi.mocked(applyUpdate).mock.calls[1]?.[2]?.running).toBe('0.2.0');
	});
});
