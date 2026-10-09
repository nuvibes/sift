/* The Sift Downloads folder proposed when a library gains its first folder. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { words } from '$lib/design/testing.svelte';

const get = vi.fn();
const post = vi.fn();
const put = vi.fn();

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: {
		get: (path: string, options?: unknown) => get(path, options),
		post: (path: string, options?: unknown) => post(path, options),
		put: (path: string, options?: unknown) => put(path, options),
		del: vi.fn()
	}
}));

const openSettings = vi.hoisted(() => vi.fn());
vi.mock('$lib/settings-ui/settings-view', () => ({ openSettings }));

import DownloadFolderOffer from './DownloadFolderOffer.svelte';
import { Library, type Root } from './library.svelte';

const ROOT = { id: 'r1', name: 'Videos', path: 'C:\\Users\\someone\\Videos' } as Root;
const TOP = {
	id: 'top',
	root_id: 'r1',
	parent_id: null,
	name: 'Videos',
	rel_path: '',
	hidden: false,
	restricted: false,
	restricted_here: false,
	shared: false,
	shared_here: false,
	keep_local: false,
	keep_from_swaps: false,
	writable: null
};

function siteOptions(folder: string | null) {
	return {
		default: {
			scope: '*default*',
			naming: '{site} - {name}',
			dest_folder_id: folder,
			downloader: 'ytdlp'
		},
		sites: [],
		tokens: {},
		downloaders: []
	};
}

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;
let library: Library;

beforeEach(() => {
	get.mockReset().mockResolvedValue(siteOptions(null));
	post
		.mockReset()
		.mockResolvedValue({ ...TOP, id: 'made', parent_id: 'top', name: 'Sift Downloads' });
	put.mockReset().mockResolvedValue(undefined);
	openSettings.mockReset();
	library = new Library();
	library.load = vi.fn(async () => {});
	library.loading = false;
	library.canManage = true;
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host?.remove();
	document.body.innerHTML = '';
});

async function settle() {
	for (let turn = 0; turn < 4; turn += 1) {
		flushSync();
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
	flushSync();
}

/** Draw on a library with no folders, then add its first one. */
async function firstFolderAdded() {
	library.roots = [];
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(DownloadFolderOffer, { target: host, props: { library } });
	await settle();
	library.roots = [ROOT];
	library.folders = [TOP];
	await settle();
}

const buttonSaying = (saying: string) =>
	[...host.querySelectorAll('button')].find((one) => words(one) === saying);

it('asks about a Sift Downloads folder inside the first library folder, and writes nothing yet', async () => {
	await firstFolderAdded();
	expect(words(host)).toContain(
		'Would you like to create a default folder at C:\\Users\\someone\\Videos\\Sift Downloads to store things you download using Sift?'
	);
	expect(post).not.toHaveBeenCalled();
	expect(put).not.toHaveBeenCalled();
});

it('creates the folder and sets it as the default, carrying the rest of the row', async () => {
	await firstFolderAdded();
	buttonSaying('Create this folder')?.click();
	await settle();
	expect(post).toHaveBeenCalledWith('/library/folders/placed', {
		body: { parent_id: 'top', name: 'Sift Downloads' }
	});
	expect(put).toHaveBeenCalledWith('/site-options/*default*', {
		body: { naming: '{site} - {name}', dest_folder_id: 'made', downloader: 'ytdlp' }
	});
	expect(buttonSaying('Create this folder')).toBeUndefined();
});

it('leaves the folder not set on Skip', async () => {
	await firstFolderAdded();
	buttonSaying('Skip')?.click();
	await settle();
	expect(post).not.toHaveBeenCalled();
	expect(put).not.toHaveBeenCalled();
	expect(buttonSaying('Create this folder')).toBeUndefined();
});

it('sends Choose another to the one control for the setting, writing nothing', async () => {
	await firstFolderAdded();
	buttonSaying('Choose another')?.click();
	await settle();
	expect(openSettings).toHaveBeenCalledWith('downloads', 'downloads.name_template');
	expect(put).not.toHaveBeenCalled();
});

it('proposes nothing where a default folder is already set', async () => {
	get.mockResolvedValue(siteOptions('f1'));
	await firstFolderAdded();
	expect(buttonSaying('Create this folder')).toBeUndefined();
});

/* A library that already had a folder when the screen opened is not the first-run moment. */
it('proposes nothing on a library that already had a folder', async () => {
	library.roots = [ROOT];
	library.folders = [TOP];
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(DownloadFolderOffer, { target: host, props: { library } });
	await settle();
	expect(get).not.toHaveBeenCalled();
	expect(buttonSaying('Create this folder')).toBeUndefined();
});

/* The server writes a marker where the profile folder's name is hidden. */
it('draws a hidden profile folder as the blur, never as the marker word', async () => {
	library.roots = [];
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(DownloadFolderOffer, { target: host, props: { library } });
	await settle();
	library.roots = [{ ...ROOT, path: 'C:\\Users\\[redacted]\\Videos' } as Root];
	library.folders = [TOP];
	await settle();

	const hidden = host.querySelector('.path .hidden-name');
	expect(hidden, 'the profile folder is drawn as the blur').not.toBeNull();
	expect(hidden?.querySelector('.stand-in')?.getAttribute('aria-hidden')).toBe('true');
	expect(words(host)).toContain('Videos\\Sift Downloads to store things you download');
});
