/* The line under the paste box: which way the pasted Site goes out. The switch and where
 * downloads land are the page's Options rows (`DownloadOptions.svelte.test.ts`).
 *
 * What is guarded is that the connection line says what actually happens to a Site's traffic, including the two states a glance would get wrong: a Site with no
 * choice of its own following a tunnel, and a Site pointed at a tunnel that has since been deleted.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), locked: { value: false } }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, put: mocks.put, post: vi.fn(), del: vi.fn() }
}));

vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get secretsLocked() {
			return mocks.locked.value;
		}
	}
}));

import DownloadChoices from './DownloadChoices.svelte';

const SETTINGS = {
	sections: [
		{
			name: 'Downloads',
			settings: [
				{ key: 'download.remember', value: true, label: 'Skip links you have already downloaded' }
			]
		}
	]
};

const FOLDERS = [
	{ id: 'f-1', name: 'Sift Downloads', rel_path: 'Sift Downloads' },
	{ id: 'f-2', name: 'Clips', rel_path: 'Clips' }
];

const TUNNELS = [
	{ id: 't-1', name: 'Harborline', enabled: true, running: true, up: true, draining: false },
	{ id: 't-2', name: 'Stillwater', enabled: true, running: false, up: false, draining: false }
];

let routes: Record<string, unknown>;

beforeEach(() => {
	vi.clearAllMocks();
	mocks.locked.value = false;
	routes = {
		default: 'direct',
		sites: { sunhollow: 't-1', tidewater: 't-2', marchfield: 't-gone' },
		available: [
			{ key: 'sunhollow', name: 'Sunhollow' },
			{ key: 'tidewater', name: 'Tidewater' },
			{ key: 'marchfield', name: 'Marchfield' },
			{ key: 'quillbrook', name: 'Quillbrook' }
		]
	};
	mocks.get.mockImplementation((path: string) => {
		if (path === '/settings') return Promise.resolve(SETTINGS);
		if (path === '/site-options')
			return Promise.resolve({ default: { dest_folder_id: 'f-1' }, sites: [] });
		if (path === '/library/folders') return Promise.resolve({ folders: FOLDERS });
		if (path === '/tunnels') return Promise.resolve(TUNNELS);
		if (path === '/download-routes') return Promise.resolve(routes);
		return Promise.reject(new Error(`unexpected ${path}`));
	});
	mocks.put.mockResolvedValue(undefined);
});

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(props: { pastedSites?: { key: string; name: string }[] } = {}) {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(DownloadChoices, { target: host, props });
	flushSync();
	// The tunnels: the line reads "your own connection" until their read lands, so a test that
	// looked before then would be reading the empty state rather than the one it set up.
	await vi.waitFor(() => expect(mocks.get).toHaveBeenCalledWith('/download-routes'));
	await new Promise((settle) => setTimeout(settle, 0));
	flushSync();
}

/** The connection lines, as their words. */
const lines = () => [...host.querySelectorAll('.lines li')].map((one) => words(one));

/* With a link in the box, ONE line for the Site it is from: a table of every routed Site would be
   Settings, Sites drawn a second time above the queue. The rule is not what is written beside a
   Site: with no route of its own it follows the default. */
it('says which way the pasted Site goes, in one line', async () => {
	await draw({ pastedSites: [{ key: 'sunhollow', name: 'Sunhollow' }] });
	expect(lines()).toEqual([expect.stringMatching(/^Sunhollow uses Harborline .*Connected$/)]);
	expect(words(host)).not.toContain('Tidewater');
});

it('says a pasted Site with no tunnel uses your own connection, and one whose tunnel is gone', async () => {
	await draw({
		pastedSites: [
			{ key: 'quillbrook', name: 'Quillbrook' },
			{ key: 'marchfield', name: 'Marchfield' }
		]
	});
	expect(lines()).toEqual([
		'Quillbrook uses your own connection',
		expect.stringMatching(/^Marchfield Its tunnel was deleted$/)
	]);
});

/* With nothing pasted, the glance at what is being tunnelled: each tunnel carrying Sites, whether
   it is up, and who is on it, and the Sites stranded on a deleted one, which refuse to download. */
it('glances at every tunnel in use when nothing is pasted', async () => {
	await draw();
	expect(lines()).toEqual([
		expect.stringMatching(/^Harborline .*Connected for Sunhollow$/),
		expect.stringMatching(/^Stillwater .*Not connected for Tidewater$/),
		'Its tunnel was deleted for Marchfield'
	]);
});

it('says every Site follows the default tunnel, rather than listing them', async () => {
	routes = { ...routes, default: 't-1', sites: {} };
	await draw();
	expect(lines()).toEqual([expect.stringMatching(/^Harborline .*Connected for Every Site$/)]);
});

it('says every Site uses your own connection when nothing is tunnelled', async () => {
	routes = { ...routes, sites: {} };
	await draw();
	expect(lines()).toEqual(['Every Site uses your own connection.']);
});

it('says the tunnels are locked rather than drawing lines that cannot be true', async () => {
	mocks.locked.value = true;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(DownloadChoices, { target: host, props: {} });
	flushSync();
	await vi.waitFor(() => expect(words(host)).toContain('Tunnels are locked'));
	expect(host.querySelector('.lines')).toBeNull();
});

/* The partial, covered IP next to the tunnel name: the value Settings shows, by the one rule
   Settings uses (only while the tunnel is up) in the same control, which paints over
   everything after the first part rather than blurring it. */
it('puts the address a connected tunnel is on beside its name, covered as Settings covers it', async () => {
	const withAddresses = [
		{ ...TUNNELS[0], endpoint: '198.51.100.23' },
		// Not connected: the server it was last on is not where anything is going, so none is shown.
		{ ...TUNNELS[1], endpoint: '203.0.113.9' }
	];
	mocks.get.mockImplementation((path: string) => {
		if (path === '/tunnels') return Promise.resolve(withAddresses);
		if (path === '/settings') return Promise.resolve(SETTINGS);
		if (path === '/site-options')
			return Promise.resolve({ default: { dest_folder_id: 'f-1' }, sites: [] });
		if (path === '/library/folders') return Promise.resolve({ folders: FOLDERS });
		if (path === '/download-routes') return Promise.resolve(routes);
		return Promise.reject(new Error(`unexpected ${path}`));
	});
	await draw();
	const items = [...host.querySelectorAll('.lines li')];
	const harborline = items.find((one) => words(one).includes('Harborline')) as Element;
	expect(harborline.querySelector('.covered')?.textContent).toBe('51.100.23');
	expect(harborline.querySelector('button')?.getAttribute('aria-label')).toBe(
		'Show the whole of the address of the server Harborline is connected to'
	);
	const stillwater = items.find((one) => words(one).includes('Stillwater')) as Element;
	expect(stillwater.querySelector('.covered')).toBeNull();
	expect(words(stillwater)).not.toContain('203');
	// The number is the SERVER's address, and says so. Only where there is an address to label.
	expect(words(harborline.querySelector('.server') as Element)).toMatch(/^Tunnel server 198\./);
	expect(words(stillwater)).not.toContain('Tunnel server');
});
