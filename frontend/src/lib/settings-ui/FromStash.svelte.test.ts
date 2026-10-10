/* Importing a Stash library: what the row offers while a new read is on its way. */

import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const calls = vi.hoisted(() => ({
	get: vi.fn(),
	post: vi.fn()
}));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: calls.get, post: calls.post, put: vi.fn(), del: vi.fn() }
}));

const switching = vi.hoisted(() => ({ follow: vi.fn(async () => true) }));

vi.mock('./follow-switch', () => ({ followSwitch: switching.follow }));

/* The desktop application's file dialog, when a test says the shell is there. */
const shell = vi.hoisted(() => ({ file: null as string | null, present: false }));
vi.mock('$lib/bridge', async (importOriginal) => {
	const real = await importOriginal<typeof import('$lib/bridge')>();
	return {
		...real,
		bridge: {
			...real.bridge,
			canChooseFile: () => shell.present,
			chooseFile: async () => shell.file
		}
	};
});
vi.mock('$lib/shell/health', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/shell/health')>()),
	serverBootId: async () => 'before'
}));

import FromStash from './FromStash.svelte';

const EARLIER = {
	summary: { version: 85, people: 3, folders: [] },
	mapping: {}
};

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	shell.present = false;
	shell.file = null;
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

it('stops offering to import the last read once a new read starts', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration' ? EARLIER : null
	);
	calls.post.mockImplementation(() => new Promise(() => {}));
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();
	expect(host.textContent).toContain('Import into this library');

	shell.present = true;
	shell.file = 'C:\\stash\\stash-go.sqlite';
	const choose = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Choose')
	) as HTMLButtonElement;
	choose.click();
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.textContent).not.toContain('Import into this library');
});

/* The database is chosen, never typed: in the desktop application its own file dialog answers
   the path, and the read is asked for that file immediately. */
it('reads the file chosen in the desktop file dialog, with no box to type in', async () => {
	calls.post.mockClear();
	calls.get.mockImplementation(async () => null);
	calls.post.mockImplementation(async () => EARLIER);
	shell.present = true;
	shell.file = 'D:\\Stash\\stash-go.sqlite';
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();
	expect(host.querySelector('input#stash-path')).toBeNull();
	expect(host.textContent).toContain('Nothing chosen yet');

	const choose = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Choose')
	) as HTMLButtonElement;
	choose.click();
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(calls.post).toHaveBeenCalledWith('/stash-migration/read', {
		body: { path: 'D:\\Stash\\stash-go.sqlite' }
	});
	// Chosen in the system's own dialog, its folder is given to Sift first, as Add a folder does.
	expect(calls.post.mock.calls[0]).toEqual(['/library/grants', { body: { path: 'D:\\Stash' } }]);
});

/* In a browser the chooser is Add a folder's: the folders Sift has and this device, walked down to
   the database FILE, which is what is read; a folder is never what is chosen. */
it('chooses the database file in the chooser, never a folder, and grants a folder off the device', async () => {
	const database = { name: 'stash-go.sqlite', path: 'D:\\stash\\stash-go.sqlite' };
	calls.get.mockImplementation(
		async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path !== '/library/browse') return null;
			const at = options?.query?.path;
			return {
				path: at ?? '',
				entries: at ? [] : [{ name: 'stash', path: 'D:\\stash' }],
				breadcrumb: at ? [{ name: 'stash', path: 'D:\\stash' }] : [],
				file_count: at ? 3 : 0,
				nothing_granted: false,
				writable: true,
				read_only_mount: false,
				files: at ? [database] : []
			};
		}
	);
	calls.post.mockImplementation(async () => EARLIER);
	calls.post.mockClear();
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	const settle = async () => {
		for (let turn = 0; turn < 6; turn++) await tick();
		flushSync();
	};
	await settle();
	const press = (label: string) =>
		(
			[...document.querySelectorAll('button')].find((one) =>
				one.textContent?.includes(label)
			) as HTMLButtonElement
		).click();

	press('Choose');
	await settle();
	press('Browse this device');
	await settle();
	expect(calls.get).toHaveBeenLastCalledWith('/library/browse', {
		query: { scope: 'machine', files: expect.arrayContaining(['stash-go.sqlite*', 'config.yml']) }
	});
	press('stash');
	await settle();
	press('Use this file');
	await settle();
	expect(document.body.textContent).toContain('Choose a file first');
	expect(calls.post).not.toHaveBeenCalled();

	(document.querySelector('.file') as HTMLButtonElement).click();
	await settle();
	press('Use this file');
	await settle();

	expect(calls.post.mock.calls).toEqual([
		['/library/grants', { body: { path: 'D:\\stash' } }],
		['/stash-migration/read', { body: { path: database.path } }]
	]);
});

it('says a library already came across, and offers to bring it in again', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration'
			? { ...EARLIER, ran: { ended_at: 1_790_000_000, said: 'Imported 2 of 3 files.' } }
			: null
	);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.textContent).toContain('It came across');
	expect(host.textContent).toContain('Imported 2 of 3 files.');
	expect(host.textContent).toContain('Import again');
	expect(host.textContent).not.toContain('Import into this library');
});

it('names the database the kept read came from when it opens again, and reads it again from there', async () => {
	const source = 'D:\\Stash\\stash-go.sqlite';
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration' ? { ...EARLIER, source } : null
	);
	calls.post.mockResolvedValue({ ...EARLIER, source });
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.querySelector('#stash-path')?.textContent).toContain('stash-go.sqlite');
	const again = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Read it')
	) as HTMLButtonElement;
	expect(again.disabled).toBe(false);
	again.click();
	await tick();
	expect(calls.post).toHaveBeenCalledWith('/stash-migration/read', { body: { path: source } });
});

it('says what a run did from the record once the run it was following ends', async () => {
	vi.useFakeTimers();
	try {
		let ended = false;
		calls.get.mockImplementation(async (path: string) => {
			if (path === '/stash-migration') {
				return ended
					? { ...EARLIER, ran: { ended_at: 1_790_000_000, said: 'Imported 2 of 3 files.' } }
					: EARLIER;
			}
			if (path === '/jobs') {
				const state = ended ? 'done' : 'running';
				return { jobs: [{ id: 'job-1', state, progress: 0.5, note: null, position: null }] };
			}
			return null;
		});
		const host = document.createElement('div');
		document.body.append(host);
		drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
		for (let turn = 0; turn < 6; turn++) await tick();
		flushSync();
		expect(host.textContent).toContain('Importing');

		ended = true;
		await vi.advanceTimersByTimeAsync(2100);
		for (let turn = 0; turn < 6; turn++) await tick();
		flushSync();

		expect(host.textContent).toContain('It came across');
		expect(host.textContent).toContain('Imported 2 of 3 files.');
		expect(host.textContent).not.toContain('Finished.');
	} finally {
		vi.useRealTimers();
	}
});

function press(host: HTMLElement, words: string): void {
	const button = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes(words)
	) as HTMLButtonElement;
	button.click();
	flushSync();
}

it('makes a new library for the read by name, then follows Sift onto it', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration' ? EARLIER : null
	);
	calls.post.mockImplementation(async () => ({ switching: true, library: 'made' }));
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	press(host, 'Import into a new library');
	const name = host.querySelector('#stash-new-name') as HTMLInputElement;
	expect(name.value).toBe('From Stash');
	name.value = 'Stash at home';
	name.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	press(host, 'Create and import');
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	// No pictures in this read, so there is no choice to make and none is sent.
	expect(calls.post).toHaveBeenCalledWith('/stash-migration/new-library', {
		body: { name: 'Stash at home', pictures: false, blobs: null }
	});
	expect(switching.follow).toHaveBeenCalledWith('before');
	expect(host.textContent).toContain('Opening Stash at home');
});

it('says what else the read found: favorites, stash-box ids and saved filters', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration'
			? {
					...EARLIER,
					summary: {
						...EARLIER.summary,
						favorite_people: 120,
						people_with_a_box_id: 1_300,
						saved_filters: 30,
						filters_over_files: 11
					}
				}
			: null
	);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.textContent).toContain('Favorites on 120 People');
	expect(host.textContent).toContain('1,300 People');
	expect(host.textContent).toContain('30 saved filters, 11 of them over files');
});

it('lists what waits for its file under a fold: by name, by Stash path, with what waits on it', async () => {
	calls.get.mockImplementation(async (path: string, options?: { query?: { offset?: number } }) => {
		if (path === '/stash-migration') {
			return {
				...EARLIER,
				ran: { ended_at: 1_790_000_000, said: 'Imported 2 of 3 files. The list is below.' },
				waiting: 2
			};
		}
		if (path === '/stash-migration/waiting') {
			expect(options?.query?.offset).toBe(0);
			return {
				total: 2,
				offset: 0,
				rows: [
					{
						id: 'w1',
						kind: 'scene',
						label: 'Waiting on the roof',
						paths: ['/media/stash/Clips/third.mp4'],
						rating: 10,
						markers: [{ title: 'Elsewhere', start_ms: 5_000, end_ms: 9_000 }],
						people: ['Jane Moe'],
						sites: ['Moe Studio'],
						tags: ['Rooftop']
					},
					{
						id: 'w2',
						kind: 'image',
						label: 'A still',
						paths: ['/media/stash/Clips/still.jpg'],
						rating: null,
						markers: [],
						people: [],
						sites: [],
						tags: []
					}
				]
			};
		}
		return null;
	});
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 6; turn++) await tick();
	flushSync();

	const fold = host.querySelector('#stash-waiting') as HTMLDetailsElement;
	expect(fold.querySelector('summary')?.textContent).toBe('Show all 2 waiting for their files');
	const rows = [...fold.querySelectorAll('li')].map((one) => one.textContent ?? '');
	expect(rows[0]).toContain('Waiting on the roofFile');
	expect(rows[0]).toContain('/media/stash/Clips/third.mp4');
	expect(rows[0]).toContain(
		'Markers: Elsewhere at 0:05. People: Jane Moe. Sites: Moe Studio. Tags: Rooftop'
	);
	expect(rows[1]).toContain('A stillPicture');
	expect(rows[1]).toContain('/media/stash/Clips/still.jpg');
	// Two rows fit on one page, so there is nothing to page through.
	expect(fold.textContent).not.toContain('of 2 waiting');
});

it('draws no list when nothing waits, and never asks for one', async () => {
	calls.get.mockReset();
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration' ? { ...EARLIER, waiting: 0 } : null
	);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 6; turn++) await tick();
	flushSync();

	expect(host.querySelector('#stash-waiting')).toBeNull();
	expect(calls.get.mock.calls.map(([path]) => path)).not.toContain('/stash-migration/waiting');
});

it('offers the pictures with the press, and asks for the blobs folder only when Stash needs it', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration'
			? {
					...EARLIER,
					summary: {
						...EARLIER.summary,
						people_with_a_picture: 1_200,
						sites_with_a_picture: 150,
						pictures_in_a_folder: 12
					},
					blobs: 'D:\\Stash\\blobs'
				}
			: null
	);
	calls.post.mockImplementation(async () => ({ job_id: 'job' }));
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.textContent).toContain('The pictures Stash kept on 1,200 People and 150 Sites.');
	// Offered as the folder beside the database, shown chosen rather than in a box to type in.
	const folder = host.querySelector('#stash-blobs') as HTMLElement;
	expect(folder.textContent).toContain('blobs');
	expect(host.querySelector('input#stash-blobs')).toBeNull();

	press(host, 'Import into this library');
	press(host, 'Import');
	for (let turn = 0; turn < 4; turn++) await tick();
	expect(calls.post).toHaveBeenCalledWith('/stash-migration/run', {
		body: { pictures: true, blobs: 'D:\\Stash\\blobs' }
	});
});

it('links each kind Stash attached to nothing to its own wall, filtered to exactly those', async () => {
	calls.get.mockImplementation(async (path: string) =>
		path === '/stash-migration'
			? {
					...EARLIER,
					ran: { ended_at: 1_790_000_000, said: 'Imported 2 of 3 files.' },
					unattached: { people: 12, sites: 0, tags: 40 }
				}
			: null
	);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(FromStash, { target: host }) as Record<string, unknown>;
	for (let turn = 0; turn < 4; turn++) await tick();
	flushSync();

	expect(host.textContent).toContain('12 People and 40 Tags had nothing attached in Stash.');
	const links = [...host.querySelectorAll('a[data-unattached]')].map((one) => [
		one.textContent,
		one.getAttribute('href')
	]);
	expect(links).toEqual([
		['People', '/people?created=stash_unattached'],
		['Tags', '/tags?created=stash_unattached']
	]);
});
