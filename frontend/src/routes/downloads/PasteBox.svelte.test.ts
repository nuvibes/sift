/* The box a download starts in: what the button says it will do, and how much room it gives. */
import { readFileSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

/* The settings under the box read the server when they are drawn. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: {
		get: vi.fn(() => Promise.reject(new Error('not in this test'))),
		post: vi.fn(),
		put: vi.fn(),
		del: vi.fn()
	}
}));

import PasteBox from './PasteBox.svelte';
import type { components } from '$lib/api/schema';
import { api } from '$lib/api/client';
import { Destinations, NO_DOWNLOAD_FOLDER } from '$lib/library/destinations.svelte';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
});

type SupportedSite = components['schemas']['SupportedSite'];

function site(key: string, name: string, hosts: string[]): SupportedSite {
	return {
		key,
		name,
		hosts,
		media: ['video'],
		bulk: true,
		supported: true,
		tested: true,
		names_creators: true,
		default_naming: '',
		name_words: [],
		walls: [],
		cookies: 'not_needed',
		cookies_why: 'Everything downloads without them.',
		cookies_with_a_tool: null
	};
}

const SITES = [
	site('tiktok', 'TikTok', ['tiktok.com']),
	site('reddit', 'Reddit', ['reddit.com', 'redd.it'])
];

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(PasteBox, {
		target: host,
		props: {
			value: '',
			busy: false,
			error: undefined,
			sites: SITES,
			found: null,
			narrow: false,
			onsubmit: () => {},
			onall: () => {},
			onjustone: () => {},
			...props
		}
	});
	flushSync();
	return host;
}

const buttonSaying = (saying: string) =>
	[...host.querySelectorAll('button')].find((one) => words(one) === saying);

it('names the act plainly for one link', () => {
	draw({ value: 'https://www.tiktok.com/@someone/video/1' });
	expect(buttonSaying('Download')).toBeDefined();
});

/* The count is the only warning somebody gets that a stray line came along with the paste. */
it('counts the links it would queue', () => {
	draw({ value: 'https://a.example/1\n\nhttps://b.example/2\nhttps://c.example/3  ' });
	expect(buttonSaying('Download 3')).toBeDefined();
});

/** The Site pills, and nothing else drawn under the box. */
const pills = () => [...host.querySelectorAll('[aria-label="Sites this paste is from"] li')];

it('names each site the paste is from, once', () => {
	draw({
		value: 'https://www.tiktok.com/@a/video/1\nhttps://tiktok.com/@b/video/2\nhttps://redd.it/xyz'
	});
	expect(pills().map((one) => words(one))).toEqual(['TikTok', 'Reddit']);
	// The same reading of the paste names the Sites the connection line under the box speaks for,
	// so a pill and the line cannot name different Sites.
	expect(
		[...host.querySelectorAll('.lines li')].map((one) => words(one).split(' uses ')[0])
	).toEqual(['TikTok', 'Reddit']);
});

it('names no site for an address Sift has no record of', () => {
	draw({ value: 'https://nowhere.example/clip' });
	expect(pills()).toHaveLength(0);
});

/* ONE HEIGHT: the box and the button read the same block-size token, and the row stretches the
   button to the box. */
it('gives the box and its button one height, the control height, and stretches the button to it', () => {
	const source = readFileSync('src/routes/downloads/PasteBox.svelte', 'utf8');
	const style = source.slice(source.lastIndexOf('<style>'));
	expect(style).toMatch(
		/\.row :global\(\.link\),\s*\.row :global\(\.send\) \{\s*min-block-size: var\(--control-height\);/
	);
	expect(style).toMatch(/\.row :global\(\.link\) \{\s*block-size: var\(--control-height\);/);
	expect(style).toMatch(/\.row \{[^}]*align-items: stretch;/);
	expect(style).not.toMatch(/align-items: flex-end/);
	draw({});
	expect(host.querySelector('textarea')?.getAttribute('rows')).toBe('1');
	expect(host.querySelector('button[type="submit"]')?.classList.contains('send')).toBe(true);
});

/* The switches and the folder are the page's Options; the box keeps only the line saying which
   way the paste goes out. */
it('draws the connection line under the box, and no settings panel', () => {
	draw({});
	expect(host.querySelector('.choices .way')).not.toBeNull();
	expect(host.querySelector('[aria-label="Download settings"]')).toBeNull();
	expect(host.querySelector('[role="switch"]')).toBeNull();
});

it('asks the playlist question with the number in front of it', () => {
	const onall = vi.fn();
	draw({
		value: 'https://www.youtube.com/playlist?list=PL1',
		found: { count: 42, site: 'YouTube', truncated: false, limit: 500 },
		onall
	});
	expect(words(host.querySelector('.count'))).toBe('42 files on YouTube.');
	buttonSaying('Download all')?.click();
	expect(onall).toHaveBeenCalled();
	expect(buttonSaying('Download only this one')).toBeDefined();
});

/* What is left over is still sitting in the box, so the cap is said where the decision is made. */
it('says how many one paste takes when the site held more', () => {
	draw({
		value: 'https://www.youtube.com/playlist?list=PL1',
		found: { count: 500, site: 'YouTube', truncated: true, limit: 500 }
	});
	expect(words(host.querySelector('.count'))).toContain('Up to 500 at a time.');
});

/* THE CAP IS THE SERVER'S, and this is the half that matters: a number written into this file
   would be a second copy of a limit the server enforces, and a server that took a hundred at a
   time would be contradicted by a screen that went on being believed. */
it('says the cap the server sent, not a number of its own', () => {
	draw({
		value: 'https://www.youtube.com/playlist?list=PL1',
		found: { count: 100, site: 'YouTube', truncated: true, limit: 100 }
	});
	expect(words(host.querySelector('.count'))).toContain('Up to 100 at a time.');
	expect(words(host.querySelector('.count'))).not.toContain('500');
});

/* The box and its button stack on a window too small to hold both. */
it('puts the button under the box on a narrow window', () => {
	draw({ narrow: true });
	expect(host.querySelector('.row')?.classList.contains('narrow')).toBe(true);
	// The box's width there, as it is the box's height beside it.
	expect(host.querySelector('button[type="submit"]')?.classList.contains('full')).toBe(true);
});

it('keeps them on one line on a window with room', () => {
	draw({});
	expect(host.querySelector('.row')?.classList.contains('narrow')).toBe(false);
	expect(host.querySelector('button[type="submit"]')?.classList.contains('full')).toBe(false);
});

/* ---- With no download folder set, the first paste ASKS
 * ------------------------------------------ */

/** What the server holds: the folders a download can go to, and which one is the default. */
function holds(
	defaultFolder: string | null,
	sites: { scope: string; dest_folder_id: string }[] = [],
	foldersFail = false
) {
	vi.mocked(api.get).mockImplementation(async (path: string) => {
		if (path === '/library/folders') {
			if (foldersFail) throw new Error('500');
			return { folders: [{ id: 'f1', name: 'Clips', rel_path: 'Clips' }] };
		}
		if (path === '/site-options') {
			return {
				default: {
					scope: '*default*',
					naming: null,
					dest_folder_id: defaultFolder,
					downloader: null
				},
				sites: sites.map((one) => ({ naming: null, downloader: null, ...one })),
				tokens: {},
				downloaders: []
			};
		}
		throw new Error('not in this test');
	});
}

/** Draw, let the reads land, and press Download. */
async function press(props: Record<string, unknown>) {
	const onsubmit = vi.fn();
	draw({ onsubmit, ...props });
	await vi.waitFor(() => {
		flushSync();
		expect(vi.mocked(api.get).mock.calls.some(([path]) => path === '/site-options')).toBe(true);
	});
	await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
	host.querySelector('form')?.requestSubmit();
	await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
	return onsubmit;
}

const ASKS = 'No download folder is set. Choose one under Options, Download folder.';

it('asks where a paste goes instead of sending it when no folder is set', async () => {
	holds(null);
	const onask = vi.fn();
	const onsubmit = await press({ value: 'https://www.tiktok.com/@someone/video/1', onask });
	expect(onsubmit).not.toHaveBeenCalled();
	expect(words(host)).toContain(ASKS);
	// And the page is told, so it opens its Options where Download folder is.
	expect(onask).toHaveBeenCalledOnce();
	// The unset default is said in the chooser's own words, the same phrase Settings uses.
	expect(NO_DOWNLOAD_FOLDER).toBe('Not set, so each download asks');
});

it('sends a paste once a folder is chosen for it', async () => {
	holds(null);
	const onsubmit = await press({ value: 'https://www.tiktok.com/@someone/video/1', dest: 'f1' });
	expect(onsubmit).toHaveBeenCalledOnce();
	expect(words(host)).not.toContain(ASKS);
});

it('sends a paste when a default folder is set', async () => {
	holds('f1');
	const onsubmit = await press({ value: 'https://www.tiktok.com/@someone/video/1' });
	expect(onsubmit).toHaveBeenCalledOnce();
});

/* A Site given a folder of its own has somewhere to land with no default at all. */
it('sends a paste from a Site that has a folder of its own', async () => {
	holds(null, [{ scope: 'tiktok', dest_folder_id: 'f1' }]);
	const onsubmit = await press({ value: 'https://www.tiktok.com/@someone/video/1' });
	expect(onsubmit).toHaveBeenCalledOnce();
});

it('still asks when one line of the paste is from a Site with no folder', async () => {
	holds(null, [{ scope: 'tiktok', dest_folder_id: 'f1' }]);
	const onsubmit = await press({
		value: 'https://www.tiktok.com/@someone/video/1\nhttps://nowhere.example/clip'
	});
	expect(onsubmit).not.toHaveBeenCalled();
});

it('says the folders could not be read, not that there are none, and reads them again', async () => {
	vi.mocked(api.get).mockClear();
	holds(null, [], true);
	const onsubmit = await press({ value: 'https://www.tiktok.com/@someone/video/1' });
	expect(onsubmit).not.toHaveBeenCalled();
	expect(words(host)).toContain("Sift couldn't read your folders.");
	expect(words(host)).not.toContain('Sift has no folder');
	await vi.waitFor(() =>
		expect(
			vi.mocked(api.get).mock.calls.filter(([path]) => path === '/library/folders')
		).toHaveLength(2)
	);
});

it('asks for a folder while the list is still being read, rather than say there is none', async () => {
	holds(null);
	await new Destinations().load();
	vi.mocked(api.get).mockImplementation(async (path: string) =>
		path === '/library/folders' ? new Promise(() => {}) : undefined
	);
	draw({ value: 'https://www.tiktok.com/@someone/video/1' });
	host.querySelector('form')?.requestSubmit();
	await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
	expect(words(host)).toContain(ASKS);
});
