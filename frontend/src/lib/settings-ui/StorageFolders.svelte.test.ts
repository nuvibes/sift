/* Where Sift keeps its own two folders, and moving them somewhere else.
 *
 * This is the screen in front of the most destructive thing the shell can be asked to do, and every
 * claim below is decided in the markup where no shell test can see it.
 *
 * The one that matters most is the refusal. `moveStorage` answers three ways and they are three
 * different situations: it worked, it could not (and the sentence says which folder was wrong), or
 * the person closed the picker, which is not a failure and must leave no trace. A refusal that
 * became a toast would be gone by the time somebody was choosing the next folder, which is the one
 * moment it is read.
 */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import StorageFolders from './StorageFolders.svelte';
import { COPY } from './StorageFolders.search';

const canMoveStorage = vi.hoisted(() => vi.fn(() => true));
const storage = vi.hoisted(() => vi.fn());
const moveStorage = vi.hoisted(() => vi.fn());
const onStorageProgress = vi.hoisted(() => vi.fn(() => () => {}));
const canForgetMode = vi.hoisted(() => vi.fn(() => true));
const forgetMode = vi.hoisted(() => vi.fn(async () => true));
const canChooseDownloadFolder = vi.hoisted(() => vi.fn(() => false));
const downloadFolder = vi.hoisted(() => vi.fn());
const show = vi.hoisted(() => vi.fn());

vi.mock('$lib/bridge', () => ({
	bridge: {
		canMoveStorage,
		storage,
		moveStorage,
		onStorageProgress,
		canForgetMode,
		forgetMode,
		canChooseDownloadFolder,
		downloadFolder,
		chooseDownloadFolder: vi.fn()
	}
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show } }));

/* The computer running Sift, as the server describes it: nobody to ask, unless a test says so. */
const readServerDesktop = vi.hoisted(() => vi.fn(async () => null as unknown));
const readServerStorage = vi.hoisted(() => vi.fn());
const moveServerStorage = vi.hoisted(() => vi.fn());
const actThere = vi.hoisted(() => vi.fn());
const browse = vi.hoisted(() => vi.fn());

vi.mock('$lib/desktop/server-shell', () => ({
	offersServer: (desk: { has_app?: boolean } | null) => desk?.has_app === true,
	readServerDesktop,
	readServerStorage,
	moveServerStorage,
	actThere
}));

vi.mock('$lib/api/client', () => ({ ApiError: class extends Error {}, api: { get: browse } }));

const REPORT = {
	dataDir: 'D:\\Sift\\data',
	cacheDir: 'D:\\Sift\\cache',
	/* Round in the units the screen uses: `facts.size` counts in thousands, not in 1024s, so a
	   fixture written in GiB would read as 3.2 GB and the assertion would be about arithmetic
	   that has its own tests. */
	dataBytes: 3_000_000_000,
	cacheBytes: 512_000_000,
	measuredAt: 1_700_000_000_000,
	measuring: false
};

let host: HTMLDivElement;

beforeEach(() => {
	canMoveStorage.mockReturnValue(true);
	canForgetMode.mockReturnValue(true);
	canChooseDownloadFolder.mockReturnValue(false);
	storage.mockResolvedValue(REPORT);
	moveStorage.mockResolvedValue({ ok: true });
	onStorageProgress.mockReturnValue(() => {});
	readServerDesktop.mockResolvedValue(null);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	host.remove();
	vi.clearAllMocks();
});

async function settle() {
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
}

async function draw() {
	mount(StorageFolders, { target: host });
	await settle();
	return host;
}

function button(label: string): HTMLButtonElement | undefined {
	return [...host.querySelectorAll('button')].find((each) => each.textContent?.includes(label));
}

it('draws nothing in a browser, where the folders are on somebody else s computer', async () => {
	canMoveStorage.mockReturnValue(false);
	canForgetMode.mockReturnValue(false);

	await draw();

	expect(host.textContent?.trim()).toBe('');
	expect(storage).not.toHaveBeenCalled();
});

/* Where a file SAVED OUT of Sift lands: a folder on this device that nothing is imported from,
   beside the two Sift keeps for itself. Drawn by Folders, and only where the shell can choose
   one. */
it("draws where saved files go under Sift's own folders, where the shell can choose one", async () => {
	canChooseDownloadFolder.mockReturnValue(true);
	downloadFolder.mockResolvedValue({ path: 'D:\\Saved', chosen: true });

	await draw();

	expect(host.textContent).toContain('Save files to');
	expect(host.textContent).toContain('D:\\Saved');
	expect(host.querySelector('#library\\.save_folder')).not.toBeNull();
	const text = host.textContent ?? '';
	expect(text.indexOf('Generated files')).toBeLessThan(text.indexOf('Save files to'));
});

/* One row like its neighbours: the folder is the row's state, under its name and help, and the
   presses stand alone in the control column, so a long path never pushes Choose onto a line of
   its own above or below it. */
it('draws Save files to as one row: the path under the name, the presses in the control column', async () => {
	canChooseDownloadFolder.mockReturnValue(true);
	downloadFolder.mockResolvedValue({ path: 'D:\\A folder with a long name\\Saved', chosen: true });

	await draw();

	const row = [...host.querySelectorAll<HTMLElement>('.row')].find((one) =>
		one.textContent?.includes('Save files to')
	);
	expect(row, 'no Save files to row').toBeTruthy();
	const named = row!.querySelector('.named');
	const control = row!.querySelector('.control');
	expect(named?.textContent).toContain('D:\\A folder with a long name\\Saved');
	expect(control?.textContent).not.toContain('Saved');
	expect(control?.textContent).toContain('Choose');
	expect(row!.classList.contains('loose')).toBe(false);
});

it('names both folders, what each is for, and how much is in it', async () => {
	await draw();

	expect(host.textContent).toContain('Your library');
	expect(host.textContent).toContain('Generated files');
	expect(host.textContent).toContain(REPORT.dataDir);
	expect(host.textContent).toContain(REPORT.cacheDir);
	/* The sizes are the reason the row exists ("this drive is full" is the question being
	   answered), so they are drawn rather than left to the folder name. */
	expect(host.textContent).toContain('3.0 GB');
	expect(host.textContent).toContain('512 MB');
});

/* A size not measured yet is the word and the working mark, never a 0 B; one measured is drawn
   while a new walk runs. */
it('says Measuring with no size yet, and draws the size it has while measuring again', async () => {
	storage.mockResolvedValue({
		...REPORT,
		dataBytes: 0,
		cacheBytes: 0,
		measuredAt: null,
		measuring: true
	});
	const first = mount(StorageFolders, { target: host });
	await settle();
	expect(host.textContent).toContain(COPY.measuring);
	expect(host.querySelector('.spinner')).not.toBeNull();
	expect(host.textContent).not.toContain('0 B');
	await unmount(first);

	storage.mockResolvedValue({ ...REPORT, measuring: true });
	await draw();
	expect(host.textContent).toContain('3.0 GB');
	expect(host.textContent).not.toContain(COPY.measuring);
});

it('keeps a refusal on the screen rather than throwing it as a toast', async () => {
	moveStorage.mockResolvedValue({ ok: false, reason: 'That folder is not empty.' });

	await draw();
	button('Move both')?.click();
	await settle();

	expect(host.textContent).toContain('That folder is not empty.');
	expect(show).not.toHaveBeenCalled();
});

it('says nothing at all when the picker was simply closed', async () => {
	moveStorage.mockResolvedValue({ ok: false, reason: null });

	await draw();
	button('Move both')?.click();
	await settle();

	expect(show).not.toHaveBeenCalled();
	expect(host.textContent).not.toContain("couldn't");
});

it('re-reads the folders and says so once the move worked', async () => {
	await draw();
	expect(storage).toHaveBeenCalledTimes(1);

	button('Move both')?.click();
	await settle();

	expect(storage).toHaveBeenCalledTimes(2);
	expect(show).toHaveBeenCalledWith('Sift is using the new folder', { tone: 'success' });
});

it('stops listening for progress when the pane goes', async () => {
	const stop = vi.fn();
	onStorageProgress.mockReturnValue(stop);

	const pane = mount(StorageFolders, { target: host });
	await settle();
	expect(onStorageProgress).toHaveBeenCalledTimes(1);
	expect(stop).not.toHaveBeenCalled();

	/* A shell event handler left attached to an unmounted component writes into state nothing is
	   drawing, which is why the listener is returned from `onMount` at all. */
	await unmount(pane);

	expect(stop).toHaveBeenCalledTimes(1);
});

/* Where the library is, and running setup again, are on General: the folders stay put whichever
   way Sift is set up, so the Folders pane draws no way back to setup even where the shell has one. */
it('leaves the way back to setup to General', async () => {
	await draw();

	expect(host.textContent).toContain('Your library');
	expect(host.textContent).not.toContain('Run setup again');
	expect(host.textContent).not.toContain('Library location');
	expect(forgetMode).not.toHaveBeenCalled();
});

/* From ANOTHER computer, where the Sift app on the computer running Sift answers through the
   server: the same two rows about THAT computer's folders, and a move chosen in the server's own
   folder browser over its drives. */
const THERE = {
	data_dir: 'C:\\Sift\\data',
	cache_dir: 'C:\\Sift\\cache',
	data_bytes: 3_000_000_000,
	cache_bytes: 512_000_000,
	measured_at: 1_700_000_000_000,
	measuring: false,
	last_move: { ok: false, refusal: 'The copy is smaller than what it came from.' }
};

function fromAfar() {
	canMoveStorage.mockReturnValue(false);
	readServerDesktop.mockResolvedValue({
		has_app: true,
		machine: 'DESK-ONE',
		starts_with_windows: null,
		sharing: null
	});
	readServerStorage.mockResolvedValue(THERE);
	browse.mockResolvedValue({
		entries: [],
		breadcrumb: [{ name: 'Sift', path: 'E:\\Sift' }],
		path: 'E:\\Sift',
		file_count: 0,
		nothing_granted: false,
		writable: true,
		read_only_mount: false
	});
}

function pressInPage(words: string) {
	const press = [...document.body.querySelectorAll('button')].find((one) =>
		one.textContent?.includes(words)
	);
	if (!press) throw new Error(`no button saying ${words}`);
	press.click();
}

it('draws the folders of the computer running Sift, and says a move there that did not finish', async () => {
	fromAfar();
	await draw();
	await settle();

	expect(host.textContent).toContain(COPY.server.help('DESK-ONE'));
	expect(host.textContent).toContain('C:\\Sift\\data');
	expect(host.textContent).toContain(COPY.server.lastFailed(THERE.last_move.refusal));
	expect(storage).not.toHaveBeenCalled();
});

it('says Measuring for the folders there with no size yet, never 0 B', async () => {
	fromAfar();
	readServerStorage.mockResolvedValue({
		...THERE,
		data_bytes: 0,
		cache_bytes: 0,
		measured_at: null,
		measuring: true
	});
	await draw();
	await settle();
	expect(host.textContent).toContain(COPY.measuring);
	expect(host.textContent).not.toContain('0 B');
});

it('moves them into a folder picked on that computer, and says the refusal of the app there', async () => {
	fromAfar();
	actThere.mockImplementation(async (ask: () => Promise<unknown>) => {
		await ask();
		return { ok: false, refused: true, problem: 'That folder is not empty.' };
	});
	await draw();
	await settle();

	pressInPage(COPY.move);
	await settle();
	expect(browse).toHaveBeenCalledWith('/library/browse', { query: { scope: 'machine' } });
	pressInPage(COPY.server.here);
	await settle();

	expect(moveServerStorage).toHaveBeenCalledWith('E:\\Sift');
	expect(moveStorage).not.toHaveBeenCalled();
	expect(host.textContent).toContain('That folder is not empty.');
});
