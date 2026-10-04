/*
 * Add to on a folder: the declared door, every row aimed at the files under the folder.
 *
 * What a pick from a folder's menu must do is what the same pick over a selection does, with the
 * folder's files as the selection: so asserted here is that each row keeps its words and its
 * list, that the list's own write is handed the folder's files (read once, from the folder's
 * whole subtree), and that a folder bigger than one press takes writes nothing and says why
 * rather than writing the first thousand and calling that the folder.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('$lib/api/client', () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
const show = vi.fn();
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: (...args: unknown[]) => show(...args) }
}));
const named = vi.fn();
vi.mock('$lib/search/suggestions.svelte', () => ({
	sayWhoAFolderIs: (...args: unknown[]) => named(...args)
}));

import { addToVerb, favoriteVerb, type Verb, type VerbPick } from '$lib/components/common/verbs';
import { MOST_AT_ONCE } from '$lib/grid/grid.svelte';
import { filesUnder, namingFolder, overFolder } from './folder-add-to';
import { wordsOf } from '$lib/components/common/toast-pieces';

function place(): VerbPick {
	return {
		kind: 'collection',
		plural: 'collections',
		ask: async () => ({ choices: [], more: 0 }),
		pick: vi.fn(async () => 'landed' as const),
		unpick: vi.fn(async () => 'landed' as const),
		already: vi.fn(async () => ({ c1: 'all' as const }))
	};
}

function door(collect: VerbPick, favorite: (ids: string[]) => void): Verb {
	return addToVerb([
		{ id: 'collect', label: 'Collection', icon: 'box', pick: collect },
		favoriteVerb(false, favorite, true)
	]);
}

const CHOICE = { id: 'c1', name: 'Evening walks' };

beforeEach(() => {
	get.mockReset();
	show.mockReset();
	named.mockReset();
});

describe('the files under a folder', () => {
	it('reads every page of the folder, subtree and all', async () => {
		get
			.mockResolvedValueOnce({ items: [{ id: 'a' }, { id: 'b' }], total: 3 })
			.mockResolvedValueOnce({ items: [{ id: 'c' }], total: 3 });

		expect(await filesUnder('f1')).toEqual(['a', 'b', 'c']);
		expect(get).toHaveBeenCalledWith('/assets', {
			query: expect.objectContaining({ in: 'f1', offset: 2 })
		});
	});

	it('refuses a folder over the ceiling rather than answering with part of it', async () => {
		get.mockResolvedValue({ items: [{ id: 'a' }], total: MOST_AT_ONCE + 1 });

		await expect(filesUnder('f1')).rejects.toMatchObject({ total: MOST_AT_ONCE + 1 });
	});
});

describe('Add to aimed at a folder', () => {
	it('keeps every row of the door, in its words and order', () => {
		const original = door(place(), vi.fn());
		const aimed = overFolder(original, 'Holiday', async () => ['a']);

		expect(aimed.label).toBe('Add to');
		expect(aimed.children?.map((row) => [row.id, row.label, row.icon])).toEqual(
			original.children?.map((row) => [row.id, row.label, row.icon])
		);
	});

	it("hands each list's own write the folder's files, read once", async () => {
		const collect = place();
		const favorite = vi.fn();
		const files = vi.fn(async () => ['a', 'b']);
		const aimed = overFolder(door(collect, favorite), 'Holiday', files);
		const [row, heart] = aimed.children ?? [];

		expect(await row.pick?.already?.([])).toEqual({ c1: 'all' });
		expect(await row.pick?.pick([], CHOICE)).toBe('landed');
		expect(await row.pick?.unpick?.([], CHOICE)).toBe('landed');
		heart.run?.([]);
		await vi.waitFor(() => expect(favorite).toHaveBeenCalledWith(['a', 'b']));

		expect(collect.already).toHaveBeenCalledWith(['a', 'b']);
		expect(collect.pick).toHaveBeenCalledWith(['a', 'b'], CHOICE);
		expect(collect.unpick).toHaveBeenCalledWith(['a', 'b'], CHOICE);
		expect(files).toHaveBeenCalledTimes(1);
	});

	it('writes nothing over a folder too big for one press, and says so', async () => {
		const collect = place();
		get.mockResolvedValue({ items: [{ id: 'a' }], total: 4210 });
		const aimed = overFolder(door(collect, vi.fn()), 'Holiday', () => filesUnder('f1'));

		expect(await aimed.children?.[0].pick?.pick([], CHOICE)).toBe('refused');
		expect(collect.pick).not.toHaveBeenCalled();
		expect(wordsOf(show.mock.calls[0][0])).toContain('Holiday holds 4,210 files');
	});

	it('writes nothing for an empty folder, and says so', async () => {
		const collect = place();
		const aimed = overFolder(door(collect, vi.fn()), 'Holiday', async () => []);

		expect(await aimed.children?.[0].pick?.pick([], CHOICE)).toBe('refused');
		expect(collect.pick).not.toHaveBeenCalled();
		expect(wordsOf(show.mock.calls[0][0])).toBe('Holiday has no files to add');
	});
});

describe('Person and Site on a folder', () => {
	/* The folder's own act, through the route the folder's own naming questions press: the
	   pick names the folder, so the reader learns what it missed and a folder of any size is covered. */
	function places(): { door: Verb; filed: VerbPick } {
		const filed = place();
		return {
			filed,
			door: addToVerb([
				{ id: 'assign', label: 'Person', icon: 'person', pick: filed },
				{ id: 'site', label: 'Site', icon: 'public', pick: filed },
				{ id: 'collect', label: 'Collection', icon: 'box', pick: place() }
			])
		};
	}

	it('names the folder with the pick, reading none of its files and filing none of them', async () => {
		const { door, filed } = places();
		const files = vi.fn(async () => ['a']);
		const nameFolder = vi.fn(async () => 'landed' as const);
		const [person, site] = overFolder(door, 'Holiday', files, nameFolder).children ?? [];

		expect(await person.pick?.pick([], CHOICE)).toBe('landed');
		expect(await site.pick?.pick([], CHOICE)).toBe('landed');

		expect(nameFolder.mock.calls).toEqual([
			['person', CHOICE],
			['site', CHOICE]
		]);
		expect(files).not.toHaveBeenCalled();
		expect(filed.pick).not.toHaveBeenCalled();
		// Nothing per file to take off, and no marks read from a capped list of its files.
		expect(person.pick?.unpick).toBeUndefined();
		expect(person.pick?.already).toBeUndefined();
	});

	it('keeps the ceiling off those two: a folder of any size is named', async () => {
		get.mockResolvedValue({ items: [{ id: 'a' }], total: 4210 });
		const { door } = places();
		const nameFolder = vi.fn(async () => 'landed' as const);
		const [person, , collection] =
			overFolder(door, 'Holiday', () => filesUnder('f1'), nameFolder).children ?? [];

		expect(await person.pick?.pick([], CHOICE)).toBe('landed');
		expect(await collection.pick?.pick([], CHOICE)).toBe('refused');
	});

	it("names it through the folder's own route and says what it filed", async () => {
		named.mockResolvedValue({ files: 47 });

		expect(await namingFolder('f1')('site', CHOICE)).toBe('landed');

		expect(named).toHaveBeenCalledWith('f1', 'Evening walks', 'site');
		expect(wordsOf(show.mock.calls[0][0])).toBe('Added 47 files to Evening walks');
		// The Site is the way to it, by its id.
		expect(show.mock.calls[0][0][1]).toMatchObject({ kind: 'site', id: CHOICE.id });
	});

	it("says the server's reason where the naming is refused", async () => {
		named.mockRejectedValue(new Error('That name is a Site, not a person'));

		expect(await namingFolder('f1')('person', CHOICE)).toBe('refused');
		expect(show.mock.calls[0][0]).toBe('That name is a Site, not a person');
	});
});
