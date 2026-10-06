/* The verbs, and above all who is allowed to call them.
 *
 * `chooseFolder` is the whole of the folder-grant mechanism: whatever comes back from it becomes
 * a folder the server-side picker will then list the contents of. So the tests that matter are
 * not "does it return a path" but "whose call is answered": a handler that answered a subframe,
 * or a page that had been navigated somewhere else, would be handing that page the ability to
 * nominate any folder on the machine.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* The addon is reached through `require(dragAddonFile())`, so pointing that at a stub is all it
 * takes: no module interception, and the real code path is exercised exactly as it ships. */
const FAKE_ADDON = path.resolve(__dirname, '..', 'test', 'fake-drag-addon.cjs');
vi.mock('./paths', async (importOriginal) => ({
	...(await importOriginal<typeof import('./paths')>()),
	dragAddonFile: () => FAKE_ADDON
}));

/* The firewall module really starts PowerShell and really raises an elevation prompt. What these
 * tests are about is the VERB (who is allowed to call it, and which port it passes), so the
 * module itself is stood in for. Its own behaviour is tested in firewall.test.ts. */
vi.mock('./firewall', () => ({
	firewallState: vi.fn(async (port: number) => `read:${port}` as unknown as string),
	openFirewall: vi.fn(async (port: number) => `open:${port}` as unknown as string)
}));

/* The transfer runs in a second process in the application, and a test must never start one.
 *
 * What is stood in for is only WHERE the work happens: the pool's own behaviour has its own tests
 * in transfers.test.ts, and the transfer's in transfer.test.ts. What these tests are about is the
 * verb: that it starts the transfer without waiting for it, hands the drag the two names, and
 * passes what it hears back to the page. */
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

/* The browser list really reads the Windows registry, and the answer is whatever happens to be
 * installed on the machine the test runs on. What these tests are about is the VERB (who may
 * call it, and that the id it accepts has to be one from this list), so the reading is stood in
 * for. Its own behaviour is tested in browsers.test.ts. */
const BROWSERS = [
	{ id: 'C:\\Program Files\\Bluebird\\bluebird.exe', name: 'Bluebird' },
	{ id: 'C:\\Program Files\\Kestrel\\kestrel.exe', name: 'Kestrel' }
];
vi.mock('./browsers', () => ({
	installed: vi.fn(async () => BROWSERS)
}));

/* The log tail really reads the shell's own log file, wherever this machine keeps it. What the
 * verb does with a count is the whole of what is under test (the file it reads is decided by
 * paths.ts and nothing from a page reaches it), so the read is stood in for and the number it was
 * asked for is what the tests look at. `log` itself stays real: the drag path writes to it. */
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

/* Applying an update downloads an installer and runs it. What the verb owes is which feed it reads
 * and that the backend is stopped before the launch, so the doing is stood in for. update.test.ts
 * has the feed, the hash and the signature. */
vi.mock('./update', async (importOriginal) => ({
	...(await importOriginal<typeof import('./update')>()),
	applyUpdate: vi.fn(async () => ({ ok: true, version: '0.1.157' }))
}));

/* This machine's own network interfaces, so the address offered for a second computer is a stated
 * choice rather than whatever the test machine happens to have. Only `networkInterfaces` is
 * replaced: the tests use `os.tmpdir` for a real folder a client-mode drag writes into.
 */
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
	BrowserWindow,
	clipboardContents,
	fetchAnswers,
	fetched,
	folderAnswers,
	handlers,
	ipcRenderer,
	reply,
	paths,
	resetElectronStub,
	sender,
	senderIsDestroyed,
	sentToPage
} from '../test/electron-stub';
import * as channels from './channels';
import {
	ADD_LIBRARY,
	APPLY_UPDATE,
	BRIDGE_VERBS,
	GET_DOWNLOAD_DIR,
	GET_KEEP_RUNNING,
	GET_START_WITH_WINDOWS,
	GET_STORAGE,
	LIST_LIBRARIES,
	MOVE_STORAGE,
	OPEN_LIBRARY,
	SAVE_SERVER,
	SETUP_BACK,
	SET_KEEP_RUNNING,
	SET_SHARING,
	SET_START_WITH_WINDOWS,
	SHELL_LOG
} from './channels';
import { log, tail as tailShellLog } from './log';
import type { Reach } from './origins';

import { notePress } from './gesture';
import { openFirewall as openFirewallCommand } from './firewall';
import { applyUpdate } from './update';
import {
	CAPTURE_WINDOW,
	CHOOSE_FILE,
	CHOOSE_FOLDER,
	CHOOSE_MODE,
	OPEN_FIREWALL,
	READ_CLIPBOARD,
	START_DRAG,
	captureRect,
	captureWindow,
	chooseFile,
	chooseFolder,
	readClipboard,
	registerVerbs,
	isShareFromElsewhere,
	REMOTE_VERBS,
	verbsFor
} from './verbs';
import { screenOf, WINDOW_STAGE } from './opening';
import { registerWindowStage } from './stage';

const TRUSTED = 'http://127.0.0.1:5171';

/* This machine's own Sift gets every verb; a saved server elsewhere only `REMOTE_VERBS`. */
const localAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'local' : null;
const remoteAt =
	(origin: string) =>
	(url: string): Reach | null =>
		url.startsWith(origin) ? 'remote' : null;

/* A real folder of this file's own, because a client-mode drag really writes a file and a copy left
 * by another run in the stub's shared default would be dragged as already fetched. */
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

describe('chooseFolder', () => {
	it('answers the folder somebody picked', async () => {
		folderAnswers.push(['D:\\Media']);
		expect(await chooseFolder(null)).toBe('D:\\Media');
	});

	it('answers nothing when the dialog is cancelled', async () => {
		folderAnswers.push(null);
		expect(await chooseFolder(null)).toBeNull();
	});

	it('works with a parent window as well as without one', async () => {
		folderAnswers.push(['D:\\Media']);
		/* The stub is a window in the ways `chooseFolder` uses one (it is handed straight to the
		   dialog) and in none of the other 170-odd, so it is cast rather than completed. */
		const parent = new BrowserWindow({}) as unknown as import('electron').BrowserWindow;
		expect(await chooseFolder(parent)).toBe('D:\\Media');
	});
});

/* The file dialog Migrate from Stash reads a database through: the folder dialog's shape. */
describe('chooseFile', () => {
	it('answers the file somebody picked, or nothing when cancelled', async () => {
		folderAnswers.push(['D:\\Stash\\stash-go.sqlite']);
		expect(await chooseFile(null)).toBe('D:\\Stash\\stash-go.sqlite');
		folderAnswers.push(null);
		expect(await chooseFile(null)).toBeNull();
	});

	it('opens over the window that asked, and answers what was picked there', async () => {
		folderAnswers.push(['D:\\Stash\\stash-go.sqlite']);
		const parent = new BrowserWindow({}) as unknown as import('electron').BrowserWindow;
		expect(await chooseFile(parent)).toBe('D:\\Stash\\stash-go.sqlite');
	});

	it("is this machine's own, never a page from another computer's", async () => {
		expect(REMOTE_VERBS.has(CHOOSE_FILE)).toBe(false);
		folderAnswers.push(['D:\\Stash\\stash-go.sqlite']);
		expect(await ipcRenderer.invoke(CHOOSE_FILE)).toBe('D:\\Stash\\stash-go.sqlite');
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		folderAnswers.push(['D:\\Stash\\stash-go.sqlite']);
		expect(await ipcRenderer.invoke(CHOOSE_FILE)).toBeNull();
	});
});

describe('the channel', () => {
	it('is registered once the verbs are wired', () => {
		expect(handlers.has(CHOOSE_FOLDER)).toBe(true);
	});

	it('answers the top-level page of a trusted origin', async () => {
		folderAnswers.push(['D:\\Media']);
		expect(await ipcRenderer.invoke(CHOOSE_FOLDER)).toBe('D:\\Media');
	});

	/* The one that matters. The preload is attached only to saved origins, but a window keeps its
	 * channel for as long as it lives and a page can be navigated, so the check has to happen at
	 * the moment of the call, not at the moment the capability was granted. */
	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		folderAnswers.push(['D:\\Media']);

		expect(await ipcRenderer.invoke(CHOOSE_FOLDER)).toBeNull();
		// And the dialog was never even opened, so nothing was shown to the person.
		expect(folderAnswers.length).toBe(1);
	});

	/* A subframe is somebody else's document even when the page around it is Sift's. */
	it('refuses a subframe of a trusted page', async () => {
		sender.senderFrame = {
			url: `${TRUSTED}/browse`,
			parent: { url: `${TRUSTED}/browse` }
		};
		folderAnswers.push(['D:\\Media']);

		expect(await ipcRenderer.invoke(CHOOSE_FOLDER)).toBeNull();
	});

	it('refuses a frame that has gone away', async () => {
		sender.senderFrame = null as unknown as { url: string; parent: unknown };
		folderAnswers.push(['D:\\Media']);

		expect(await ipcRenderer.invoke(CHOOSE_FOLDER)).toBeNull();
	});
});

/* --- Taking a file out ------------------------------------------------------------------- */

/* The OLE drag loop itself is not entered here: it needs a hand on a mouse. What these prove is
 * everything around it: who is answered, which origin is asked, and above all WHICH DRAG a
 * client-mode file gets and with what arguments, which is invisible from outside the addon. */

const AN_ASSET = '01HX0000000000000000000007';

describe('startDrag', () => {
	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('unavailable');
		// Nothing was asked of the server either: the refusal is before the lookup, not after it.
		expect(fetched).toEqual([]);
		expect(addon.calls).toEqual([]);
	});

	/* The page passes an ID. Anything else is not a mistake to tolerate: it is the shape of a
	 * call that was trying to name something other than an asset. */
	it('refuses anything that is not an asset id', async () => {
		for (const nonsense of ['', 42, null, { id: AN_ASSET }, ['a']]) {
			expect(await ipcRenderer.invoke(START_DRAG, nonsense)).toBe('unavailable');
		}
		expect(fetched).toEqual([]);
	});

	/* THE ORIGIN COMES FROM THE ASKING PAGE, not from a value passed in. That is what makes this
	 * correct in all three shapes the shell runs in without anything to keep in step. */
	it('asks the server the page is actually talking to', async () => {
		/* Re-wired for a DIFFERENT trusted origin, which is the whole point: nothing in the handler
		 * names an address, so a shell pointed at another machine asks that machine. */
		const another = 'http://192.168.1.9:5171';
		registerVerbs(remoteAt(another));
		sender.senderFrame = { url: `${another}/browse`, parent: null };
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));
		fetchAnswers.push(reply('data'));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');
		expect(fetched[0]).toBe(`http://192.168.1.9:5171/api/assets/${AN_ASSET}/local-file`);
	});

	/* A file on THIS machine is dragged where it lies. No copy, whatever its size, which is the
	 * case that matters most: a library of large clips on the machine running Sift. */
	it('drags a local file by its path, and copies nothing', async () => {
		fetchAnswers.push(
			reply({
				path: 'D:\\library\\clip.mp4',
				filename: 'clip.mp4',
				size_bytes: 9
			})
		);

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');
		expect(addon.calls).toEqual([{ verb: 'startDrag', path: 'D:\\library\\clip.mp4' }]);
	});

	/* A PICTURE THAT SAYS WHERE IT WAS TAKEN IS NEVER DRAGGED AS IT IS. Handing Windows the
	 * original's path would carry its GPS into whatever chat window it is dropped on. Even a path in
	 * the answer is not dragged: the copy without the place is fetched and streamed instead. */
	it('drags a copy without the place for a file that holds one, never its path', async () => {
		fetchAnswers.push(
			reply({
				path: 'D:\\library\\holiday.jpg',
				filename: 'holiday.jpg',
				size_bytes: 9,
				holds_a_place: true
			})
		);

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.some((call) => call.verb === 'startDrag')).toBe(false);
		const started = addon.calls.at(-1);
		expect(started?.verb).toBe('startStreamedDrag');
		expect(started?.finished).toMatch(/-unplaced[\\/]holiday\.jpg$/);
		expect(started?.total).toBe(9);
	});

	/* A file on another machine is one gesture: the drag is entered straight away with a name, a
	 * size and the file the download is being written into, and the receiving application reads
	 * that file as it arrives.
	 */
	it('drags a file on another machine in ONE gesture, as a file still arriving', async () => {
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));
		fetchAnswers.push(reply('data'));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		const started = addon.calls.at(-1);
		expect(started?.verb).toBe('startStreamedDrag');
		// The name the receiver shows, and the size its own progress bar is drawn from.
		expect(started?.name).toBe('clip.mp4');
		expect(started?.total).toBe(4);
		// The file being written, and where it ends up. Both, because the download may finish before
		// the receiver gets round to opening it.
		expect(started?.partial).toMatch(/clip\.mp4\.partial$/);
		expect(started?.finished).toMatch(/clip\.mp4$/);
	});

	/* The size is what a receiver draws its own progress from, and a wrong one is worse than none:
	 * the descriptor simply leaves the size out when the server would not say. */
	it('says the size is unknown rather than guessing at it', async () => {
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: null }));
		fetchAnswers.push(reply('data'));

		await ipcRenderer.invoke(START_DRAG, AN_ASSET);

		expect(addon.calls.at(-1)?.total).toBe(-1);
	});

	/* A LIBRARY ON A NETWORK SHARE. Both machines can see the file, so the second one hands over the
	   path and nothing crosses the network: the same instant drag as on the machine Sift runs on.
	   This is the ordinary case for anybody keeping their library on a NAS. */
	it('hands over the share path rather than fetching anything', async () => {
		const onShare = path.join(temp, 'on-the-nas.mp4');
		fs.writeFileSync(onShare, 'data');
		fetchAnswers.push(
			reply({
				path: null,
				shared_path: onShare,
				filename: 'on-the-nas.mp4',
				size_bytes: 4
			})
		);

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.at(-1)).toMatchObject({
			verb: 'startDrag',
			path: onShare
		});
	});

	/* A share this machine cannot reach, or one answering with a different file, must not be
	   dragged: it falls back to fetching a copy, which is slower and certainly right. */
	it('falls back to the streamed drag when the share is not reachable here', async () => {
		fetchAnswers.push(
			reply({
				path: null,
				shared_path: path.join(temp, 'no-such-share', 'clip.mp4'),
				filename: 'clip.mp4',
				size_bytes: 4
			})
		);

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.at(-1)?.verb).toBe('startStreamedDrag');
	});

	/* Trusted and still not an address: a trust check a shell was handed can be looser than a URL
	   parser, and the drag must not go on to build a request out of nothing. */
	it('refuses a trusted page whose address has no origin, and asks nobody', async () => {
		registerVerbs(() => 'local');
		sender.senderFrame = { url: 'not an address', parent: null };

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('unavailable');
		expect(fetched).toEqual([]);
	});

	/* The second drag of a clip that has already arrived is instant: the copy is dragged where it
	   lies, and nothing is fetched again. */
	it('drags a file already fetched from where it lies, without fetching it again', async () => {
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));
		await ipcRenderer.invoke(START_DRAG, AN_ASSET);
		const first = addon.calls.at(-1);
		expect(first?.verb).toBe('startStreamedDrag');
		/* What the first drag's transfer leaves behind once it has finished. */
		fs.mkdirSync(path.dirname(first?.finished ?? ''), { recursive: true });
		fs.writeFileSync(first?.finished ?? '', 'data');
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.at(-1)).toEqual({
			verb: 'startDrag',
			path: first?.finished
		});
		expect(addon.calls.filter((one) => one.verb === 'startStreamedDrag')).toHaveLength(1);
	});

	it('says so when there is no such file to drag', async () => {
		fetchAnswers.push(reply(null, { ok: false }));
		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('unavailable');
		expect(addon.calls).toEqual([]);
	});

	it('tells the page how far along the fetch is', async () => {
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));
		fetchAnswers.push(reply('data'));

		await ipcRenderer.invoke(START_DRAG, AN_ASSET);
		// The transfer is deliberately not awaited, so let it run to its end before reading what it
		// announced. In the application this happens while the file is being dropped.
		await new Promise((settle) => setTimeout(settle, 10));

		expect(sentToPage.at(-1)?.payload).toMatchObject({
			assetId: AN_ASSET,
			received: 4
		});
	});

	/* A fetch outlives the window that started it. Without the check this asserts, a large file
	 * with the window closed half way would throw on a WebContents that has gone. */
	it('says nothing to a window that has closed', async () => {
		senderIsDestroyed.value = true;
		fetchAnswers.push(reply({ path: null, filename: 'clip.mp4', size_bytes: 4 }));
		fetchAnswers.push(reply('data'));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');
		await new Promise((settle) => setTimeout(settle, 10));
		expect(sentToPage).toEqual([]);
	});
});

describe('readClipboard', () => {
	it('hands over a link and a picture together', () => {
		clipboardContents.text = '  https://example.com/a  ';
		clipboardContents.image = 'data:image/png;base64,AAAA';

		expect(readClipboard()).toEqual({
			text: 'https://example.com/a',
			image: 'data:image/png;base64,AAAA'
		});
	});

	/* An empty NativeImage still answers `toDataURL`, with a data URL of nothing, which the page
	 * would then try to import as a file. `isEmpty` is what stops that. */
	it('answers no picture rather than an empty one', () => {
		expect(readClipboard().image).toBeNull();
	});

	it('refuses a page that has been navigated somewhere else', async () => {
		sender.senderFrame = { url: 'https://example.com/anything', parent: null };
		clipboardContents.text = 'https://example.com/a';

		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toBeNull();
	});

	it('answers Sift own page through the channel', async () => {
		clipboardContents.text = 'https://example.com/a';

		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toEqual({
			text: 'https://example.com/a',
			image: null
		});
	});
});

/* --- A picture of the window ------------------------------------------------------------- */

/** A NativeImage in the two ways the capture reads one. Empty when there are no bytes. */
function pictureOf(bytes: number[]) {
	return { isEmpty: () => bytes.length === 0, toPNG: () => Buffer.from(bytes) };
}

/** A window's contents in the two ways the capture uses one, recording the area it was asked for. */
function capturable(answer: () => Promise<unknown>, zoom = 1) {
	const asked: unknown[] = [];
	return {
		asked,
		contents: {
			getZoomFactor: () => zoom,
			capturePage: async (...rect: unknown[]) => {
				asked.push(...rect);
				return answer();
			}
		} as unknown as Parameters<typeof captureWindow>[0]
	};
}

describe('captureWindow', () => {
	it('answers the whole window as PNG bytes when no area is asked for', async () => {
		const { asked, contents } = capturable(async () => pictureOf([137, 80, 78, 71]));

		expect(await captureWindow(contents, null)).toEqual(new Uint8Array([137, 80, 78, 71]));
		expect(asked).toEqual([]);
	});

	/* The page measures in CSS pixels and the window in its own, which differ by the zoom. The
	   edges are rounded outwards so a fractional box never loses its last row of pixels. */
	it('turns an area in the page into the same area of the window', async () => {
		const { asked, contents } = capturable(async () => pictureOf([1]), 1.25);

		await captureWindow(contents, { x: 10.5, y: 20, width: 100, height: 50.2 });

		expect(asked).toEqual([{ x: 13, y: 25, width: 126, height: 63 }]);
	});

	it('answers nothing for an empty picture or a capture that failed', async () => {
		expect(await captureWindow(capturable(async () => pictureOf([])).contents, null)).toBeNull();
		const failing = capturable(async () => {
			throw new Error('the window has gone');
		});
		expect(await captureWindow(failing.contents, null)).toBeNull();
	});

	it('answers Sift own page through the channel, and nothing to a page elsewhere', async () => {
		const window = sender.sender as Record<string, unknown>;
		window.getZoomFactor = () => 1;
		window.capturePage = async () => pictureOf([7]);
		try {
			expect(await ipcRenderer.invoke(CAPTURE_WINDOW, null)).toEqual(new Uint8Array([7]));
			sender.senderFrame = {
				url: 'https://example.com/anything',
				parent: null
			};
			expect(await ipcRenderer.invoke(CAPTURE_WINDOW, null)).toBeNull();
		} finally {
			delete window.capturePage;
			delete window.getZoomFactor;
		}
	});
});

describe('captureRect', () => {
	it('is the whole window for anything that is not an area with some size to it', () => {
		expect(captureRect(null, 1)).toBeUndefined();
		expect(captureRect('all', 1)).toBeUndefined();
		expect(captureRect({ x: 0, y: 0, width: 'wide', height: 10 }, 1)).toBeUndefined();
		expect(captureRect({ x: 0, y: 0, width: Number.NaN, height: 10 }, 1)).toBeUndefined();
		expect(captureRect({ x: 5, y: 5, width: 0, height: 10 }, 1)).toBeUndefined();
		expect(captureRect({ x: 5, y: 5, width: -4, height: 10 }, 1)).toBeUndefined();
	});

	it('keeps an area inside the page and reads a nonsense zoom as none', () => {
		expect(captureRect({ x: -20, y: -5, width: 100, height: 40 }, 1)).toEqual({
			x: 0,
			y: 0,
			width: 80,
			height: 35
		});
		expect(captureRect({ x: 2, y: 3, width: 4, height: 5 }, 0)).toEqual({
			x: 2,
			y: 3,
			width: 4,
			height: 5
		});
	});
});

/* --- A server on another computer ------------------------------------------------------- */

/* A saved server is somebody else's program, and anything able to change a plain-http page on the
   way here is too. It gets what a window onto a library elsewhere needs, and nothing that acts on
   this machine's own Sift. */
describe('a page served by another computer', () => {
	const SERVER = 'http://192.168.1.20:5171';

	beforeEach(() => {
		resetElectronStub();
		paths.temp = temp;
		registerVerbs(remoteAt(SERVER));
		sender.senderFrame = { url: `${SERVER}/browse`, parent: null };
	});

	it('is offered exactly the verbs a client needs, and the local backend all of them', () => {
		expect(new Set(verbsFor('remote'))).toEqual(REMOTE_VERBS);
		expect(verbsFor('remote').sort()).toEqual(
			[
				channels.START_DRAG,
				channels.DRAG_PROGRESS,
				channels.READ_CLIPBOARD,
				channels.CAPTURE_WINDOW,
				channels.LAST_SERVER,
				channels.CONNECT_STATE,
				channels.SHELL_VERSION,
				channels.LOCAL_HARDWARE,
				channels.MACHINE_NAME,
				channels.LIST_BROWSERS,
				channels.SET_BROWSER,
				channels.GET_DOWNLOAD_DIR,
				channels.SET_DOWNLOAD_DIR,
				channels.SET_TITLE_BAR,
				channels.GET_KEEP_RUNNING,
				channels.SET_KEEP_RUNNING,
				channels.GET_START_WITH_WINDOWS,
				channels.SET_START_WITH_WINDOWS,
				channels.FORGET_MODE,
				channels.SAVE_LOG_ARCHIVE,
				WINDOW_STAGE
			].sort()
		);
		const every = [...Object.values(channels).filter((one) => one !== BRIDGE_VERBS), WINDOW_STAGE];
		expect(verbsFor('local').sort()).toEqual([...every].sort());
		expect(verbsFor(null)).toEqual([]);
	});

	/* What the preload is told, from the address of the document it is loading into. */
	it('tells the preload which verbs this page gets, by its address', () => {
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, `${SERVER}/browse`)).toEqual(verbsFor('remote'));
		sender.senderFrame = { url: 'https://example.com/', parent: null };
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, 'https://example.com/')).toEqual([]);
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, 42)).toEqual([]);
		/* An address the preload could not name is answered from the frame, and never widened by it. */
		sender.senderFrame = { url: `${SERVER}/browse`, parent: null };
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, 'about:blank')).toEqual(verbsFor('remote'));
		sender.senderFrame = {
			url: `${SERVER}/browse`,
			parent: { url: `${SERVER}/browse` }
		};
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, 'about:blank')).toEqual([]);
		resetElectronStub();
		registerVerbs(localAt(TRUSTED));
		expect(ipcRenderer.sendSync(BRIDGE_VERBS, `${TRUSTED}/browse`)).toEqual(verbsFor('local'));
	});

	/* The preload leaving a method off is a convenience; this is the check. Every verb that acts on
	   this machine's Sift refuses the page even when it calls the channel directly. */
	it('cannot reach a verb that acts on this machine, even by calling the channel', async () => {
		const sharing = {
			read: vi.fn(() => ({
				enabled: false,
				live: false,
				mode: 'client' as const,
				address: null,
				port: 5171
			})),
			write: vi.fn(async () => {})
		};
		const storage = {
			read: vi.fn(async () => ({
				dataDir: 'D:\\d',
				cacheDir: 'D:\\c',
				dataBytes: 0,
				cacheBytes: 0
			})),
			move: vi.fn(),
			forget: vi.fn()
		};
		const libraries = {
			list: vi.fn(() => ({ current: null, libraries: [] })),
			open: vi.fn(),
			add: vi.fn(),
			forget: vi.fn()
		};
		const setup = {
			mode: vi.fn(),
			suggested: vi.fn(),
			library: vi.fn(),
			back: vi.fn()
		};
		const servers = {
			remember: vi.fn(),
			last: vi.fn(() => null),
			problem: vi.fn(() => null),
			saved: vi.fn(() => []),
			forget: vi.fn(() => [])
		};
		vi.mocked(applyUpdate).mockClear();
		vi.mocked(tailShellLog).mockClear();
		vi.mocked(openFirewallCommand).mockClear();
		resetElectronStub();
		registerVerbs(remoteAt(SERVER), {
			sharing,
			storage,
			libraries,
			setup,
			servers
		} as unknown as Parameters<typeof registerVerbs>[1]);
		sender.senderFrame = { url: `${SERVER}/settings`, parent: null };
		folderAnswers.push(['D:\\Media']);

		await ipcRenderer.invoke(SET_SHARING, true);
		await ipcRenderer.invoke(OPEN_FIREWALL, 'any');
		await ipcRenderer.invoke(SHELL_LOG, 200);
		await ipcRenderer.invoke(GET_STORAGE);
		await ipcRenderer.invoke(MOVE_STORAGE);
		await ipcRenderer.invoke(LIST_LIBRARIES);
		await ipcRenderer.invoke(OPEN_LIBRARY, 'D:\\d');
		await ipcRenderer.invoke(ADD_LIBRARY);
		await ipcRenderer.invoke(CHOOSE_MODE, 'standalone');
		await ipcRenderer.invoke(SETUP_BACK);
		await ipcRenderer.invoke(SAVE_SERVER, 'http://192.168.1.30:5171');
		await ipcRenderer.invoke(APPLY_UPDATE);
		expect(await ipcRenderer.invoke(CHOOSE_FOLDER)).toBeNull();

		expect(sharing.write).not.toHaveBeenCalled();
		expect(openFirewallCommand).not.toHaveBeenCalled();
		expect(tailShellLog).not.toHaveBeenCalled();
		expect(storage.read).not.toHaveBeenCalled();
		expect(storage.move).not.toHaveBeenCalled();
		expect(libraries.list).not.toHaveBeenCalled();
		expect(libraries.open).not.toHaveBeenCalled();
		expect(libraries.add).not.toHaveBeenCalled();
		expect(setup.mode).not.toHaveBeenCalled();
		expect(setup.back).not.toHaveBeenCalled();
		expect(servers.remember).not.toHaveBeenCalled();
		expect(applyUpdate).not.toHaveBeenCalled();
		// The folder dialog was never opened.
		expect(folderAnswers.length).toBe(1);
	});

	it('still reaches what a client needs', async () => {
		const closing = { keepRunning: vi.fn(() => true), keep: vi.fn() };
		const downloads = {
			folder: () => 'D:\\Downloads',
			chosen: () => null,
			choose: vi.fn()
		};
		resetElectronStub();
		registerVerbs(remoteAt(SERVER), { closing, downloads });
		sender.senderFrame = { url: `${SERVER}/settings`, parent: null };

		expect(await ipcRenderer.invoke(GET_KEEP_RUNNING)).toBe(true);
		expect(await ipcRenderer.invoke(GET_DOWNLOAD_DIR)).toEqual({
			path: 'D:\\Downloads',
			chosen: false
		});
	});

	/* How THIS window behaves is this computer's own: the close button and starting with Windows
	   are answered and changed by this shell, whichever computer served the page. The server's own
	   answers are asked of the server, never of this bridge. */
	it("keeps this window's close button and sign-in start on this computer", async () => {
		const closing = { keepRunning: vi.fn(() => false), keep: vi.fn() };
		const startup = { read: vi.fn(() => false), write: vi.fn((on: boolean) => on) };
		resetElectronStub();
		registerVerbs(remoteAt(SERVER), { closing, startup } as unknown as Parameters<
			typeof registerVerbs
		>[1]);
		sender.senderFrame = { url: `${SERVER}/settings`, parent: null };

		expect(await ipcRenderer.invoke(GET_START_WITH_WINDOWS)).toBe(false);
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, true)).toBe(true);
		expect(startup.write).toHaveBeenCalledWith(true);
		await ipcRenderer.invoke(SET_KEEP_RUNNING, true);
		expect(closing.keep).toHaveBeenCalledWith(true);
		/* A navigated page is still refused: the reach is the saved server's, not anybody's. */
		sender.senderFrame = { url: 'https://example.com/settings', parent: null };
		expect(await ipcRenderer.invoke(SET_START_WITH_WINDOWS, false)).toBeNull();
		expect(startup.write).toHaveBeenCalledTimes(1);
	});

	/* THE CLIPBOARD, ONLY JUST AFTER A REAL PRESS. The press is what the operating system delivered
	   to this window, never an event the page reports. A script can dispatch a click, and none of
	   those reach the main process. One press buys one read. */
	it('reads the clipboard only just after a press in the window, once per press', async () => {
		clipboardContents.text = 'https://example.com/a';

		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toBeNull();

		notePress(sender.sender as object);
		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toEqual({
			text: 'https://example.com/a',
			image: null
		});
		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toBeNull();
	});

	/* A screenshot too: a page from elsewhere gets a picture of its own window only on a press. */
	it('captures the window only just after a press in it, once per press', async () => {
		const window = sender.sender as Record<string, unknown>;
		window.getZoomFactor = () => 1;
		window.capturePage = async () => pictureOf([1, 2, 3]);
		try {
			expect(await ipcRenderer.invoke(CAPTURE_WINDOW, null)).toBeNull();
			notePress(sender.sender as object);
			expect(await ipcRenderer.invoke(CAPTURE_WINDOW, null)).toEqual(new Uint8Array([1, 2, 3]));
			expect(await ipcRenderer.invoke(CAPTURE_WINDOW, null)).toBeNull();
		} finally {
			delete window.capturePage;
			delete window.getZoomFactor;
		}
	});

	it('does not count a press from longer ago than the window allows', async () => {
		clipboardContents.text = 'https://example.com/a';
		notePress(sender.sender as object, performance.now() - 60_000);

		expect(await ipcRenderer.invoke(READ_CLIPBOARD)).toBeNull();
	});

	/* The server describes its own file; it never names one of this machine's. A `path` it sends
	   is dropped, and the streamed drag (the bytes from the server) is what runs. */
	it('never drags a local path a remote server named', async () => {
		fetchAnswers.push(
			reply({
				path: 'C:\\Users\\someone\\Documents\\taxes.pdf',
				filename: 'clip.mp4',
				size_bytes: 4
			})
		);
		fetchAnswers.push(reply('data'));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.at(-1)?.verb).toBe('startStreamedDrag');
		expect(addon.calls.some((one) => one.path?.includes('taxes'))).toBe(false);
	});

	it('never drags a file on this machine named as a share path', async () => {
		const onThisDisk = path.join(temp, 'private.txt');
		fs.writeFileSync(onThisDisk, 'data');
		fetchAnswers.push(
			reply({
				path: null,
				shared_path: onThisDisk,
				filename: 'clip.mp4',
				size_bytes: 4
			})
		);
		fetchAnswers.push(reply('data'));

		expect(await ipcRenderer.invoke(START_DRAG, AN_ASSET)).toBe('dragged');

		expect(addon.calls.at(-1)?.verb).toBe('startStreamedDrag');
		expect(addon.calls.some((one) => one.path === onThisDisk)).toBe(false);
	});
});

describe('which share paths a remote server may name', () => {
	it.each([
		'\\\\nas\\media\\clips\\clip.mp4',
		'\\\\192.168.1.28\\Library\\clip.mp4',
		'\\\\nas.home\\media\\clip.mp4'
	])('accepts a file on a network share: %s', (named) => {
		expect(isShareFromElsewhere(named)).toBe(true);
	});

	it.each([
		'C:\\Users\\someone\\secret.txt',
		'D:clip.mp4',
		'\\\\?\\C:\\Users\\someone\\secret.txt',
		'\\\\.\\pipe\\anything',
		'\\\\?\\UNC\\nas\\media\\clip.mp4',
		'\\\\nas\\C$\\Users\\someone\\secret.txt',
		'\\\\nas\\ADMIN$\\x.txt',
		'\\\\localhost\\media\\clip.mp4',
		'\\\\127.0.0.1\\media\\clip.mp4',
		`\\\\${os.hostname()}\\media\\clip.mp4`,
		'\\\\nas\\media\\..\\..\\clip.mp4',
		'//nas/media/clip.mp4',
		'\\\\nas\\media',
		''
	])('refuses %s', (named) => {
		expect(isShareFromElsewhere(named)).toBe(false);
	});

	it("refuses a share on one of this machine's own addresses", () => {
		machineInterfaces['Ethernet'] = [{ family: 'IPv4', internal: false, address: '192.168.1.24' }];

		expect(isShareFromElsewhere('\\\\192.168.1.24\\media\\clip.mp4')).toBe(false);
		delete machineInterfaces['Ethernet'];
	});
});

/* The page saying how far it has drawn: the opening frame goes on it, and the look rides along. */
describe('the window stage', () => {
	function armed(): [string, unknown, string][] {
		resetElectronStub();
		const told: [string, unknown, string][] = [];
		registerVerbs(localAt(TRUSTED), {});
		registerWindowStage(localAt(TRUSTED), (stage, look, screen) =>
			told.push([stage, look, screen])
		);
		return told;
	}

	it('hands the stage, the look and the screen on, from a page Sift trusts', async () => {
		const told = armed();
		sender.senderFrame = { url: `${TRUSTED}/people/01HX0000000000000000000007`, parent: null };
		const look = { theme: '{}', canvas: '#0a0b10' };

		expect(await ipcRenderer.invoke(WINDOW_STAGE, 'painted', look)).toBe(true);
		expect(told).toEqual([['painted', look, '/people']]);
	});

	it('refuses a stage it does not know, a frame inside the page, and a page it does not trust', async () => {
		const told = armed();
		expect(await ipcRenderer.invoke(WINDOW_STAGE, 'loaded', null)).toBe(false);
		sender.senderFrame = { url: `${TRUSTED}/browse`, parent: {} };
		expect(await ipcRenderer.invoke(WINDOW_STAGE, 'painted', null)).toBe(false);
		sender.senderFrame = { url: 'https://example.com/browse', parent: null };
		expect(await ipcRenderer.invoke(WINDOW_STAGE, 'painted', null)).toBe(false);
		expect(told).toEqual([]);
	});

	it('names a screen by its first segment only, never a file or a person', () => {
		expect(screenOf('http://127.0.0.1:5171/asset/01HX0000000000000000000007')).toBe('/asset');
		expect(screenOf('sift-shell://app/connect')).toBe('/connect');
		expect(screenOf('not an address')).toBe('/');
	});
});
