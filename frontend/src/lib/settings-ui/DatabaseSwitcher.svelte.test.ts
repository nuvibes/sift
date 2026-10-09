/* The Database Switcher under Backup and restore. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { abandonSwitches } from './follow-switch';
import { flushSync, mount } from 'svelte';
import DatabaseSwitcher from './DatabaseSwitcher.svelte';
import switcherSource from './DatabaseSwitcher.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import type { LibraryList } from '$lib/bridge';
import { COPY as PANE } from './Backup.search';

const COPY = PANE.switcher;

const canSwitchLibrary = vi.hoisted(() => vi.fn(() => true));
const libraries = vi.hoisted(() => vi.fn());
const openLibrary = vi.hoisted(() => vi.fn());
const addLibrary = vi.hoisted(() => vi.fn());
const forgetLibrary = vi.hoisted(() => vi.fn());
const machineName = vi.hoisted(() => vi.fn(async (): Promise<string | null> => null));
const get = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
const serverBootId = vi.hoisted(() => vi.fn());

vi.mock('$lib/bridge', () => ({
	bridge: { canSwitchLibrary, libraries, openLibrary, addLibrary, forgetLibrary, machineName }
}));

vi.mock('$lib/api/client', () => {
	class ApiError extends Error {
		constructor(
			readonly status: number,
			readonly detail: string | null
		) {
			super(detail ?? 'refused');
		}
	}
	return { ApiError, api: { get, post, put } };
});

vi.mock('$lib/shell/health', () => ({ serverBootId }));

const OPEN = 'C:\\Sift\\data';
const WORK = 'C:\\Sift\\libraries\\Work\\data';
const OLD = 'C:\\Sift\\libraries\\Old\\data';
const ARCHIVE = 'D:\\Archive\\data';

const SERVER = {
	folder: 'C:\\Sift\\libraries',
	can_switch: true,
	libraries: [
		{
			id: 'id-open',
			name: 'Sift',
			data_dir: OPEN,
			in_folder: false,
			current: true,
			verdict: 'current',
			detail: ''
		},
		{
			id: 'id-work',
			name: 'Work',
			data_dir: WORK,
			in_folder: true,
			current: false,
			verdict: 'current',
			detail: ''
		},
		{
			id: 'id-old',
			name: 'Old',
			data_dir: OLD,
			in_folder: true,
			current: false,
			verdict: 'older',
			detail: ''
		}
	]
};

/** What the server says of a library an older Sift has to bring up to date first. */
const TOO_OLD =
	'This library was last opened by a version of Sift too old for this one to bring forward. ' +
	'Open it once with an older Sift, then open it here. Nothing has been changed.';

function shellList(): LibraryList {
	return {
		current: OPEN,
		libraries: [
			{ dataDir: OPEN, cacheDir: 'C:\\Sift\\cache', name: 'Sift', lastOpened: 2 },
			{ dataDir: ARCHIVE, cacheDir: 'D:\\Archive\\cache', name: 'Archive', lastOpened: 1 }
		]
	};
}

let host: HTMLDivElement;
let wentTo: string[];

beforeEach(() => {
	canSwitchLibrary.mockReturnValue(true);
	libraries.mockResolvedValue(shellList());
	openLibrary.mockResolvedValue({ ok: false, refusal: null });
	addLibrary.mockResolvedValue({ ok: false, refusal: null });
	forgetLibrary.mockResolvedValue({ current: OPEN, libraries: [shellList().libraries[0]] });
	get.mockResolvedValue(structuredClone(SERVER));
	post.mockResolvedValue({ switching: true, library: 'id-work' });
	/* One run before the ask, another after it. */
	serverBootId.mockReset().mockResolvedValueOnce('run-1').mockResolvedValue('run-2');
	wentTo = [];
	vi.stubGlobal('location', { assign: (to: string) => wentTo.push(to) });
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(async () => {
	/* A test that pressed a switch leaves its wait polling. */
	vi.useRealTimers();
	abandonSwitches();
	await new Promise((settle) => setTimeout(settle, 520));
	host.remove();
	vi.clearAllMocks();
	vi.unstubAllGlobals();
});

function said(): string {
	return (host.textContent ?? '').replace(/\s+/g, ' ').trim();
}

async function settle() {
	for (let turn = 0; turn < 12; turn += 1) await Promise.resolve();
	flushSync();
}

async function draw() {
	mount(DatabaseSwitcher, { target: host });
	await settle();
	return host;
}

/** A button's words, without the icon drawn inside it: an icon is a ligature, so its NAME is
 *  text too, and a button that says "Open" reads as "folderOpen" to `textContent`. */
function words(one: HTMLElement): string {
	const copy = one.cloneNode(true) as HTMLElement;
	for (const icon of copy.querySelectorAll('[aria-hidden="true"]')) icon.remove();
	return (copy.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function buttons(named: string | RegExp): HTMLButtonElement[] {
	return [...host.querySelectorAll('button')].filter((one) =>
		typeof named === 'string'
			? words(one) === named || one.getAttribute('aria-label') === named
			: named.test(one.textContent ?? '')
	);
}

function button(named: string | RegExp, index = 0): HTMLButtonElement {
	const found = buttons(named)[index];
	if (found === undefined) throw new Error(`no button ${String(named)} in: ${said()}`);
	return found;
}

function type(into: HTMLInputElement, text: string) {
	into.value = text;
	into.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

it('carries the heading a settings search points at, in the app and in a browser alike', async () => {
	await draw();
	expect(host.querySelector('[id="backup.switcher"]')?.textContent).toBe('Libraries');

	host.replaceChildren();
	canSwitchLibrary.mockReturnValue(false);
	await draw();
	expect(host.querySelector('[id="backup.switcher"]')?.textContent).toBe('Libraries');
});

it('lists the server libraries with the open one marked and an older one said so', async () => {
	await draw();

	expect(get).toHaveBeenCalledWith('/libraries');
	expect(said()).toContain('Work');
	expect(said()).toContain('Created by an older Sift');
	// The open library's own row: marked as open right now, its Open button off.
	expect(button('Open', 0).disabled).toBe(true);
});

it("says why a library can't be read where its database says, and only the mark where it can't", async () => {
	const server = structuredClone(SERVER);
	server.libraries.push(
		{
			id: 'id-far',
			name: 'Far',
			data_dir: 'C:\\Sift\\libraries\\Far\\data',
			in_folder: true,
			current: false,
			verdict: 'unreadable',
			detail: TOO_OLD
		},
		{
			id: 'id-odd',
			name: 'Odd',
			data_dir: 'C:\\Sift\\libraries\\Odd\\data',
			in_folder: true,
			current: false,
			verdict: 'unreadable',
			detail: ''
		}
	);
	get.mockResolvedValue(server);
	await draw();

	expect(said()).toContain(TOO_OLD);
	expect(said().match(/Can't be read/g)).toHaveLength(2);
	expect([...host.querySelectorAll('.foot .why')].map((one) => one.textContent?.trim())).toEqual([
		TOO_OLD
	]);
	// Every library's folder is its row's foot line, drawn as a path.
	expect(host.querySelectorAll('.foot .path').length).toBeGreaterThanOrEqual(
		server.libraries.length
	);
});

it('says where a new library is made as one sentence, the path inside it', async () => {
	await draw();
	const foot = [...host.querySelectorAll<HTMLElement>('.foot')].find((one) =>
		one.textContent?.startsWith('Created in')
	);
	expect(foot?.textContent).toBe('Created in C:\\Sift\\libraries.');
	const path = foot!.querySelector<HTMLElement>('span')!;
	applyStyles(switcherSource, path);
	// A block would put the path on a line of its own and the full stop on a third.
	expect(getComputedStyle(path).display).toBe('inline');
	removeStyles();
});

it('opens a library by the id the server handed out, then follows the server to its next run', async () => {
	vi.useFakeTimers();
	await draw();

	button('Open', 1).click();
	await settle();

	expect(post).toHaveBeenCalledWith('/libraries/open', {
		body: { library: 'id-work', upgrade: false }
	});
	expect(said()).toContain('Sift is starting on Work');
	await vi.advanceTimersByTimeAsync(600);
	await settle();
	expect(wentTo).toEqual(['/']);
});

it('says it is still switching when no new run of the server answers within the limit', async () => {
	/* The follow gives up after its limit and the row says so, instead of a spinner for ever. */
	vi.useFakeTimers();
	serverBootId.mockReset().mockResolvedValue('run-1');
	await draw();

	button('Open', 1).click();
	await settle();
	expect(said()).toContain('Sift is starting on Work');

	await vi.advanceTimersByTimeAsync(91_000);
	await settle();
	expect(wentTo).toEqual([]);
	expect(said()).toContain(COPY.stillSwitching);
	expect(button('Open', 1).disabled).toBe(false);
});

it('names the device it was asked from, so History can say which computer switched', async () => {
	machineName.mockResolvedValueOnce('LAPTOP-TWO');
	await draw();

	button('Open', 1).click();
	await settle();

	expect(post).toHaveBeenCalledWith('/libraries/open', {
		body: { library: 'id-work', upgrade: false },
		query: { device: 'LAPTOP-TWO' }
	});
});

it('asks about an older library before anything is sent, and sends the agreement with it', async () => {
	await draw();

	button('Open', 2).click();
	await settle();
	expect(post).not.toHaveBeenCalled();
	expect(said()).toContain('upgrades it in one direction');

	button('Open and upgrade').click();
	await settle();
	expect(post).toHaveBeenCalledWith('/libraries/open', {
		body: { library: 'id-old', upgrade: true }
	});
});

it('shows what the server refused and stays where it is', async () => {
	const { ApiError } = await import('$lib/api/client');
	post.mockRejectedValue(
		new ApiError(422, 'That library was last opened by a newer version of Sift.')
	);
	await draw();

	button('Open', 1).click();
	await settle();

	expect(said()).toContain('newer version of Sift');
	expect(wentTo).toEqual([]);
});

it('makes a new library by NAME, never by a folder', async () => {
	await draw();

	type(host.querySelector<HTMLInputElement>('#switcher-new-name')!, '  Evenings ');
	button('Create and open').click();
	await settle();

	expect(post).toHaveBeenCalledWith('/libraries', { body: { name: 'Evenings' } });
});

it('turns everything off, and says why, where nothing would start the server again', async () => {
	get.mockResolvedValue({ ...structuredClone(SERVER), can_switch: false });
	await draw();

	expect(said()).toContain('started by hand');
	expect(button('Open', 1).disabled).toBe(true);
	expect(button('Create and open').disabled).toBe(true);
});

it('lists what the app remembers from elsewhere once, and opens that through the app', async () => {
	await draw();

	/* The open library is on both lists and is drawn once. */
	expect(said().match(/C:\\Sift\\data/g)).toHaveLength(1);
	expect(said()).toContain('Archive');
	button('Open', 3).click();
	await settle();
	expect(openLibrary).toHaveBeenCalledWith(ARCHIVE);
});

it('forgets a remembered library through the app', async () => {
	await draw();

	button('Remove from this list').click();
	await settle();

	expect(forgetLibrary).toHaveBeenCalledWith(ARCHIVE);
	expect(said()).not.toContain('Archive');
});

it('asks the app for its picker with NO path, and says what it refused', async () => {
	addLibrary.mockResolvedValue({ ok: false, refusal: 'That file is not a Sift library.' });
	await draw();

	button(/Choose a database file/).click();
	await settle();

	expect(addLibrary).toHaveBeenCalledWith();
	expect(said()).toContain('That file is not a Sift library.');
});

it('gives a browser the upload instead of the picker, and asks the app for nothing', async () => {
	canSwitchLibrary.mockReturnValue(false);
	await draw();

	expect(libraries).not.toHaveBeenCalled();
	expect(buttons(/Choose a database file/)).toHaveLength(0);
	expect(said()).toContain('Import a database file');
});

it('uploads a chosen file with the name given, as a NEW library', async () => {
	canSwitchLibrary.mockReturnValue(false);
	await draw();

	const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
	const file = new File(['PK'], 'sift-backup-20260901.zip');
	Object.defineProperty(input, 'files', { value: [file], configurable: true });
	input.dispatchEvent(new Event('change', { bubbles: true }));
	flushSync();

	const name = host.querySelector<HTMLInputElement>('#switcher-import-name')!;
	expect(name.value).toBe('sift-backup-20260901');
	type(name, 'September');
	button('Import and open').click();
	await settle();

	const [path, options] = post.mock.calls[0] as [string, { body: FormData }];
	expect(path).toBe('/libraries/import');
	expect(options.body.get('name')).toBe('September');
	expect(options.body.get('file')).toBe(file);
});

/* --- duplicate this library ------------------------------------------------------------------ */

const PLAN = {
	folder: 'C:\\Sift\\libraries',
	records_bytes: 800_000_000,
	pictures_bytes: 12_400_000_000,
	free_bytes: 200_000_000_000,
	refusal: null
};

function answering(plan: object = PLAN) {
	get.mockImplementation(async (url: string) => {
		if (url === '/libraries/duplicate') return structuredClone(plan);
		if (url === '/jobs') return { jobs: [], total: 0 };
		return structuredClone(SERVER);
	});
}

it('duplicates by NAME, with the pictures as asked, and stays on this library', async () => {
	answering();
	post.mockResolvedValue({ job_id: 'job-1' });
	await draw();

	button('Duplicate\u2026').click();
	await settle();
	expect(said()).toContain('12 GB. Without them, Sift generates the pictures again');
	expect(said()).toContain('The copy uses the same folders of files.');

	type(host.querySelector<HTMLInputElement>('#switcher-duplicate-name')!, ' Trial ');
	host.querySelector<HTMLButtonElement>('[role="switch"]')!.click();
	flushSync();
	button('Duplicate').click();
	await settle();

	expect(post).toHaveBeenCalledWith('/libraries/duplicate', {
		body: { name: 'Trial', pictures: false }
	});
	// No switch is asked for or followed: the copy is opened from the list later.
	expect(serverBootId).not.toHaveBeenCalled();
	expect(wentTo).toEqual([]);
	expect(said()).toMatch(/Waiting to start|Duplicating/);
});

it('puts the pictures switch on the control column, the row as wide as the pane', async () => {
	answering();
	await draw();
	button('Duplicate\u2026').click();
	await settle();

	// The form is a column that keeps each line at its own width; the row holding the switch has to
	// stretch across it, or its control column ends where the row's words end.
	let line: HTMLElement = host.querySelector<HTMLElement>('[role="switch"]')!;
	while (!line.parentElement!.classList.contains('make')) line = line.parentElement!;
	applyStyles(switcherSource, line.parentElement);
	try {
		expect(getComputedStyle(line).alignSelf).toBe('stretch');
	} finally {
		removeStyles();
	}
});

it('says how the last copy went from the queue, so a reload does not forget it', async () => {
	const ended = Math.floor(Date.now() / 1000) - 120;
	const note = 'Trial is ready. Open it from the list whenever you want.';
	get.mockImplementation(async (url: string, options?: { query?: { type?: string } }) => {
		if (url !== '/jobs') return structuredClone(SERVER);
		expect(options?.query?.type).toBe('library_duplicate');
		const job = { id: 'job-1', state: 'done', updated_at: ended, note, error: null };
		return { jobs: [job], total: 1 };
	});
	await draw();
	expect(said()).toContain(`The last copy ran 2 minutes ago. ${note}`);
});

it('says why a duplicate cannot start now, and offers no press', async () => {
	answering({
		...PLAN,
		refusal: 'A backup is being restored right now. Try again when it has finished.'
	});
	await draw();

	button('Duplicate\u2026').click();
	await settle();
	type(host.querySelector<HTMLInputElement>('#switcher-duplicate-name')!, 'Trial');

	expect(said()).toContain('A backup is being restored right now.');
	expect(button('Duplicate').disabled).toBe(true);
});

/* --- which opens at start, and deleting one ------------------------------------------------- */

async function doorOf(name: string): Promise<HTMLElement[]> {
	button(`More for ${name}`).click();
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('[role="menu"]')) throw new Error('the menu has not opened');
	});
	return [...document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]')];
}

it('says which library opens when Sift starts, on that row', async () => {
	const listed = structuredClone(SERVER);
	listed.libraries[1] = { ...listed.libraries[1], opens_at_start: true } as never;
	get.mockResolvedValue(listed);
	await draw();

	expect(said()).toContain('Opens when Sift starts');
	expect(said().match(/Opens when Sift starts/g)).toHaveLength(1);
});

it('deletes a library in the folder only once its name is typed exactly, and by its id', async () => {
	post.mockResolvedValue({
		...structuredClone(SERVER),
		libraries: SERVER.libraries.filter((one) => one.name !== 'Work')
	});
	await draw();

	const rows = await doorOf('Work');
	rows.find((one) => (one.textContent ?? '').includes('Delete'))?.click();
	await settle();
	/* The question is the shared confirm dialog, drawn over the page rather than in the row. */
	const confirm = [...document.querySelectorAll('button')].find(
		(one) => words(one) === 'Delete library'
	);
	if (confirm === undefined) throw new Error('no Delete library button in the confirm');
	expect(confirm.disabled).toBe(true);
	const typed = document.querySelector<HTMLInputElement>('#switcher-delete-name');
	if (typed === null) throw new Error('no box to type the name in');
	type(typed, 'work');
	await settle();
	expect(confirm.disabled).toBe(true);
	type(typed, 'Work');
	await settle();
	expect(confirm.disabled).toBe(false);
	confirm.click();
	await settle();

	expect(post).toHaveBeenCalledWith('/libraries/delete', {
		body: { library: 'id-work', name: 'Work' }
	});
	expect(forgetLibrary).toHaveBeenCalledWith(WORK);
	expect(said()).toContain('Work is in the Recycle Bin');
});

it('offers no delete for the open library, only which opens at start', async () => {
	await draw();

	const rows = await doorOf('Sift');
	expect(rows.map((one) => words(one))).toEqual(['Open this one when Sift starts']);
	rows[0].click();
	await settle();
	expect(put).toHaveBeenCalledWith('/libraries/opens-at-start', { body: { library: 'id-open' } });
});

/* From ANOTHER computer: the libraries the Sift app on the computer running Sift remembers, read
   and opened through the server, which asks that app. */
function fromAfar(opened: { ok: boolean; refusal: string | null }) {
	canSwitchLibrary.mockReturnValue(false);
	get.mockImplementation(async (path: string) => {
		if (path === '/desktop') {
			return { has_app: true, machine: 'DESK-ONE', starts_with_windows: null, sharing: null };
		}
		if (path === '/desktop/libraries') {
			return {
				current: OPEN,
				libraries: [
					{ data_dir: ARCHIVE, cache_dir: 'D:\\Archive\\cache', name: 'Archive', last_opened: 1 }
				]
			};
		}
		return structuredClone(SERVER);
	});
	post.mockImplementation(async (path: string) =>
		path === '/desktop/libraries/open' ? opened : { switching: true, library: 'id-work' }
	);
}

it('lists what the app on the computer running Sift remembers, with no forget, and opens it there', async () => {
	fromAfar({ ok: true, refusal: null });
	await draw();

	expect(said()).toContain('Archive');
	expect(buttons(COPY.forget)).toEqual([]);
	expect(said()).toContain(COPY.pickThere('DESK-ONE'));
	expect(libraries).not.toHaveBeenCalled();

	button('Open', buttons('Open').length - 1).click();
	await settle();
	await vi.waitFor(() => expect(wentTo).toEqual(['/']));

	expect(post).toHaveBeenCalledWith('/desktop/libraries/open', { body: { data_dir: ARCHIVE } });
	expect(openLibrary).not.toHaveBeenCalled();
});

it('says the refusal of the app there in its words, and waits for nothing', async () => {
	const older =
		'That library was last opened by an older Sift, and opening it upgrades it. Open it in the ' +
		'Sift app on the computer running Sift, which asks first. Nothing has been changed.';
	fromAfar({ ok: false, refusal: older });
	await draw();

	button('Open', buttons('Open').length - 1).click();
	await settle();

	expect(said()).toContain(older);
	expect(wentTo).toEqual([]);
});
