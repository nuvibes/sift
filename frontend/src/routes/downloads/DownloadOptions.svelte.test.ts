/* The Downloads page's Options: the door every entity page wears, holding the page's three doors
 * and the paste's two choices as its rows. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({
	get: vi.fn(),
	put: vi.fn(),
	goto: vi.fn(),
	openSettings: vi.fn(),
	admin: { value: true }
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, put: mocks.put, post: vi.fn(), del: vi.fn() }
}));

vi.mock('$app/navigation', () => ({ goto: mocks.goto }));

vi.mock('$lib/settings-ui/settings-view', () => ({ openSettings: mocks.openSettings }));

vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return mocks.admin.value;
		}
	}
}));

import DownloadOptions from './DownloadOptions.svelte';

const SETTINGS = {
	sections: [
		{
			name: 'Downloads',
			settings: [
				{ key: 'download.remember', value: true, label: 'Skip links you have already downloaded' },
				// Another switch of the same pane, which the menu must not offer for one paste.
				{ key: 'download.people_from_usernames', value: false, label: 'Add creators to People' }
			]
		}
	]
};

const FOLDERS = [
	{ id: 'f-1', name: 'Sift Downloads', rel_path: 'Sift Downloads' },
	{ id: 'f-2', name: 'Clips', rel_path: 'Clips' }
];

beforeEach(() => {
	vi.clearAllMocks();
	mocks.admin.value = true;
	mocks.get.mockImplementation((path: string) => {
		if (path === '/settings') return Promise.resolve(SETTINGS);
		if (path === '/site-options')
			return Promise.resolve({
				default: { naming: '{site}', dest_folder_id: 'f-1', downloader: 'ytdlp' },
				sites: []
			});
		if (path === '/library/folders') return Promise.resolve({ folders: FOLDERS });
		if (path.startsWith('/interface-state')) return Promise.resolve({ values: {} });
		return Promise.reject(new Error(`unexpected ${path}`));
	});
	mocks.put.mockResolvedValue(undefined);
});

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

interface Chosen {
	dest: string;
	remember: boolean | null;
}

async function draw(start: Chosen = { dest: '', remember: null }) {
	// Reactive, as the page's own `$state` is, so a flip is drawn back into the row's tick.
	const chosen = $state(start);
	const oncookies = vi.fn();
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(DownloadOptions, {
		target: host,
		props: {
			get dest() {
				return chosen.dest;
			},
			set dest(next: string) {
				chosen.dest = next;
			},
			get remember() {
				return chosen.remember;
			},
			set remember(next: boolean | null) {
				chosen.remember = next;
			},
			cookiesWanted: 2,
			oncookies
		}
	});
	flushSync();
	// The switch starts from the setting, and the folder row waits for the folders.
	await vi.waitFor(() => expect(chosen.remember).toBe(true));
	await vi.waitFor(() =>
		expect(mocks.get).toHaveBeenCalledWith('/library/folders', expect.anything())
	);
	await new Promise((settle) => setTimeout(settle, 0));
	flushSync();
	return { oncookies, chosen };
}

/** Open the menu and answer its rows, as their words. */
async function open(): Promise<HTMLElement[]> {
	host.querySelector('button')?.click();
	await vi.waitFor(() => expect(document.querySelector('[role="menu"]')).not.toBeNull());
	await vi.waitFor(() =>
		expect(document.querySelectorAll('[role="menu"] [role^="menuitem"]').length).toBe(5)
	);
	return [...document.querySelectorAll<HTMLElement>('[role="menu"] [role^="menuitem"]')];
}

const rowNamed = (rows: HTMLElement[], label: string) => {
	const row = rows.find((one) => words(one).includes(label));
	expect(row, `no row called ${label}`).toBeTruthy();
	return row as HTMLElement;
};

it('is the Options door every entity page wears', async () => {
	await draw();
	const door = host.querySelector('button');
	expect(words(door)).toBe('Options');
	expect(door?.getAttribute('aria-haspopup')).toBe('menu');
});

it('holds the three doors and the two choices as its five rows, in the screen words', async () => {
	await draw();
	const rows = await open();
	expect(rows.map((row) => words(row))).toEqual([
		expect.stringContaining('Edit cookies'),
		expect.stringContaining('Open settings'),
		expect.stringContaining('Start or join a swap'),
		expect.stringContaining('Skip links you have already downloaded'),
		expect.stringContaining('Download folder')
	]);
	// The cookies row says how many are asking, as the count on the door does.
	expect(words(rows[0])).toContain('2 are waiting for cookies');
});

it('opens the cookies, the settings and the swap', async () => {
	const { oncookies } = await draw();
	rowNamed(await open(), 'Edit cookies').click();
	flushSync();
	expect(oncookies).toHaveBeenCalledOnce();

	rowNamed(await open(), 'Open settings').click();
	flushSync();
	expect(mocks.openSettings).toHaveBeenCalledWith('downloads');

	rowNamed(await open(), 'Start or join a swap').click();
	flushSync();
	expect(mocks.goto).toHaveBeenCalledWith('/swap');
});

it('offers a swap to an admin only', async () => {
	mocks.admin.value = false;
	await draw();
	host.querySelector('button')?.click();
	await vi.waitFor(() =>
		expect(document.querySelectorAll('[role="menu"] [role^="menuitem"]').length).toBe(4)
	);
	expect(words(document.querySelector('[role="menu"]'))).not.toContain('swap');
});

it('holds the switch as a row ticked the way the setting is, and a press flips it for this paste only', async () => {
	const { chosen } = await draw();
	const rows = await open();
	const skip = rowNamed(rows, 'Skip links you have already downloaded');
	expect(skip.getAttribute('role')).toBe('menuitemcheckbox');
	expect(skip.getAttribute('aria-checked')).toBe('true');
	expect(rows.filter((row) => row.getAttribute('role') === 'menuitemcheckbox')).toHaveLength(1);

	skip.click();
	flushSync();
	expect(chosen.remember).toBe(false);
	expect(skip.getAttribute('aria-checked')).toBe('false');
	// The menu stays open, so the tick is seen to move, and no setting is written.
	expect(document.querySelector('[role="menu"]')).not.toBeNull();
	expect(mocks.put).not.toHaveBeenCalled();
});

it('names the folder the next download goes to on the Download folder row', async () => {
	await draw({ dest: 'f-2', remember: null });
	const rows = await open();
	expect(words(rowNamed(rows, 'Download folder'))).toContain('Clips');
});

it('opens Download folder onto the Add list, the default named first, and a pick becomes the default', async () => {
	const { chosen } = await draw();
	const rows = await open();
	expect(words(rowNamed(rows, 'Download folder'))).toContain('Sift Downloads (default)');
	// The way a pointer does it, as `ContextMenuItem.svelte.test.ts` opens a flyout: the sub row
	// answers the pointer sequence, and a bare `click()` is enough only once the module graph is
	// warm.
	const folderRow = rowNamed(rows, 'Download folder');
	folderRow.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	folderRow.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	folderRow.click();
	flushSync();
	await vi.waitFor(() => expect(document.querySelectorAll('[role="menu"]').length).toBe(2), {
		timeout: 3000
	});
	const flyout = [...document.querySelectorAll('[role="menu"]')][1];
	const folders = [...flyout.querySelectorAll<HTMLElement>('[role="menuitemradio"]')];
	expect(folders.map((one) => words(one))).toEqual([
		expect.stringContaining('Sift Downloads (default)'),
		expect.stringContaining('Clips'),
		expect.stringContaining('Sift Downloads')
	]);
	expect(folders[0].getAttribute('aria-checked')).toBe('true');
	folders[1].click();
	flushSync();
	expect(chosen.dest).toBe('f-2');
	// The default row is written whole, with only the folder replaced.
	await vi.waitFor(() =>
		expect(mocks.put).toHaveBeenCalledWith('/site-options/*default*', {
			body: { naming: '{site}', dest_folder_id: 'f-2', downloader: 'ytdlp' }
		})
	);
	await vi.waitFor(() => expect(chosen.dest).toBe(''));
	/* A folder is one choice of several, so the pick finishes the menu: it closes, rather than
	   resting open until somebody presses elsewhere. */
	await vi.waitFor(() => expect(document.querySelectorAll('[role="menu"]').length).toBe(0), {
		timeout: 3000
	});
});
