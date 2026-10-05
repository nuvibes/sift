/* The one writer of the default download folder, and the one copy of it every chooser reads. */
import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get, put }
}));

import { Destinations, saveDownloadFolder } from './destinations.svelte';

const ROW = {
	scope: '*default*',
	naming: '{site} - {name}',
	dest_folder_id: 'f1',
	downloader: 'ytdlp'
};

beforeEach(() => {
	vi.restoreAllMocks();
	get
		.mockReset()
		.mockImplementation(async (path: string) =>
			path === '/site-options'
				? { default: ROW, sites: [] }
				: { folders: [{ id: 'f2', name: 'Clips', rel_path: 'Clips' }] }
		);
	put.mockReset().mockResolvedValue(undefined);
});

it('writes the default row whole with only the folder replaced, and every chooser names it', async () => {
	const add = new Destinations();
	const downloads = new Destinations();
	await add.load();
	await downloads.load();
	const said = vi.spyOn(toasts, 'show');

	expect(await add.makeDefault('f2')).toBe(true);

	expect(put).toHaveBeenCalledWith('/site-options/*default*', {
		body: { naming: '{site} - {name}', dest_folder_id: 'f2', downloader: 'ytdlp' }
	});
	expect(downloads.defaultFolderId).toBe('f2');
	expect(downloads.defaultOption.label).toBe('Clips (default)');
	expect(said.mock.calls[0][1]).toEqual({ tone: 'success' });
});

it('says a refusal in the server-s words and keeps the default it had', async () => {
	const chooser = new Destinations();
	await chooser.load();
	put.mockRejectedValue(
		new ApiError(400, 'That folder is read-only.', 'That folder is read-only.')
	);
	const said = vi.spyOn(toasts, 'show');

	expect(await saveDownloadFolder('f2', 'Clips')).toBe(false);

	expect(said).toHaveBeenCalledWith('That folder is read-only.', { tone: 'error' });
	expect(chooser.defaultFolderId).toBe('f1');
});

it('lists only the folders Sift may write in, as the server answers when asked', async () => {
	get.mockImplementation(async (path: string) =>
		path === '/site-options'
			? { default: ROW, sites: [] }
			: {
					folders: [
						{ id: 'f2', name: 'Clips', rel_path: 'Clips', writable: true },
						{ id: 'f3', name: 'Archive', rel_path: 'Archive', writable: false }
					]
				}
	);
	const chooser = new Destinations();
	await chooser.load();

	expect(get).toHaveBeenCalledWith('/library/folders', { query: { writable: true } });
	expect(chooser.folders.map((one) => one.id)).toEqual(['f2']);
});

it('says whether the list was read, so a failed read is not taken for no folder', async () => {
	const chooser = new Destinations();
	expect(chooser.foldersRead).toBe('unread');
	get.mockImplementationOnce(async () => {
		throw new Error('unreachable');
	});
	await chooser.load();
	expect(chooser.foldersRead).toBe('failed');
	expect(chooser.folders).toEqual([]);

	await chooser.load();
	expect(chooser.foldersRead).toBe('read');
	expect(chooser.folders.map((one) => one.id)).toEqual(['f2']);
});
