/* The photo-set list the FILE side reads, and the two writes a file's verbs make against it. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { photoSets, type PhotoSet } from '$lib/library/photo-sets.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

function aSet(overrides: Partial<PhotoSet> = {}): PhotoSet {
	return {
		id: 's1',
		name: 'A shoot',
		cover_asset_id: null,
		cover_upload_id: null,
		item_count: 0,
		favorite: false,
		pinned: false,
		rating: null,
		shared: false,
		restricted: false,
		vault: false,
		...overrides
	} as PhotoSet;
}

function page(items: PhotoSet[]) {
	return { items, total: items.length, limit: 200, offset: 0 };
}

beforeEach(() => {
	vi.clearAllMocks();
	photoSets.forget();
	photoSets.failed = false;
	photoSets.loading = false;
});

describe('the list the verbs choose from', () => {
	it('comes back in name order whatever order the server sent', async () => {
		/* The pick sheet filters what is already loaded rather than re-asking as somebody types,
		   so the order it is drawn in is the order this puts it in and nothing else. */
		mocked.get.mockResolvedValue(
			page([aSet({ id: 's2', name: 'Zoetrope' }), aSet({ id: 's1', name: 'Ada' })])
		);

		await photoSets.load();

		expect(photoSets.items.map((one) => one.name)).toEqual(['Ada', 'Zoetrope']);
		expect(photoSets.loaded).toBe(true);
		expect(photoSets.loading).toBe(false);
	});

	it('asks for enough to choose from, and from the beginning', async () => {
		mocked.get.mockResolvedValue(page([]));

		await photoSets.load();

		expect(mocked.get).toHaveBeenCalledWith('/photo-sets', {
			query: { limit: 200, offset: 0 }
		});
	});

	it('says it failed rather than showing a list it does not have', async () => {
		mocked.get.mockRejectedValue(new Error('offline'));

		await photoSets.load();

		expect(photoSets.failed).toBe(true);
		expect(photoSets.loaded).toBe(false);
		expect(photoSets.loading).toBe(false);
	});

	it('lets a slow answer be overtaken rather than letting it overwrite a newer one', async () => {
		/* The rising counter every list store in this client carries. */
		let settleFirst: (value: unknown) => void = () => {};
		mocked.get.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					settleFirst = resolve;
				})
		);
		const slow = photoSets.load();

		mocked.get.mockResolvedValue(page([aSet({ id: 's9', name: 'The newer answer' })]));
		await photoSets.load();

		settleFirst(page([aSet({ id: 's1', name: 'The older answer' })]));
		await slow;

		expect(photoSets.items.map((one) => one.name)).toEqual(['The newer answer']);
	});

	it('forgetting empties it, so the next screen that wants it asks again', async () => {
		mocked.get.mockResolvedValue(page([aSet()]));
		await photoSets.load();

		photoSets.forget();

		expect(photoSets.items).toEqual([]);
		expect(photoSets.loaded).toBe(false);
	});

	it('a page already in the air is discarded by a forget', async () => {
		/* What `forget` is FOR: the vault. */
		let settle: (value: unknown) => void = () => {};
		mocked.get.mockImplementation(
			() =>
				new Promise((resolve) => {
					settle = resolve;
				})
		);
		const reading = photoSets.load();

		photoSets.forget();
		settle(page([aSet({ name: 'Something concealed' })]));
		await reading;

		expect(photoSets.items).toEqual([]);
	});
});

describe('the two writes', () => {
	it('a new set goes into the list in its place rather than at the end', async () => {
		mocked.get.mockResolvedValue(page([aSet({ id: 's1', name: 'Ada' })]));
		await photoSets.load();
		mocked.post.mockResolvedValue(aSet({ id: 's2', name: 'Aardvark' }));

		const made = await photoSets.create('Aardvark');

		expect(mocked.post).toHaveBeenCalledWith('/photo-sets', { body: { name: 'Aardvark' } });
		expect(made.id).toBe('s2');
		expect(photoSets.items.map((one) => one.name)).toEqual(['Aardvark', 'Ada']);
	});

	it('hands back the WHOLE answer, not just the count off the front of it', async () => {
		/* A picture already in the set is not written twice, so `changed` and "how many I asked
		   about" differ whenever a selection overlaps what is there, and the sentence on screen
		   is built from the first. */
		mocked.post.mockResolvedValue({
			changed: 2,
			skipped: 1,
			reason: 'It is in your vault. Unlock the vault to include it.',
			vault_locked: true
		});

		const done = await photoSets.add('s1', ['a1', 'a2', 'a3']);

		expect(done.changed).toBe(2);
		expect(done.skipped).toBe(1);
		expect(done.vault_locked).toBe(true);
		expect(mocked.post).toHaveBeenCalledWith('/photo-sets/s1/items', {
			body: { asset_ids: ['a1', 'a2', 'a3'] }
		});
	});

	it('escapes the id it puts in the address', async () => {
		/* WITH SOMETHING TO SEND. `overChunks` slices the ids into batches, so an EMPTY list
		   means no batches and no call. */
		mocked.post.mockResolvedValue({ changed: 1, skipped: 0, reason: null, vault_locked: false });

		await photoSets.add('a/b c', ['a1']);

		expect(mocked.post).toHaveBeenCalledWith('/photo-sets/a%2Fb%20c/items', {
			body: { asset_ids: ['a1'] }
		});
	});

	it('asks for nothing at all when there is nothing to add', async () => {
		/* The empty case, pinned on its own: an empty selection is not a write, and a request
		   that changes nothing is still a round trip. */
		await photoSets.add('s1', []);

		expect(mocked.post).not.toHaveBeenCalled();
	});
});
