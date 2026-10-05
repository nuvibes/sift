/* The collections store: one page of the shelf, in the order the server put it in.
 *
 * The same shape as the tag store and for the same reasons. See its file for the argument about
 * why a wall that pages cannot also order itself in the browser. What is different here is the
 * default order, which is by name, and the fact that what is inside a collection is arranged by hand
 * and never touched by any of this. This orders the shelf, never the books.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	COLLECTION_ORDERS,
	Collections,
	contentsAsked,
	contentsSource,
	type Collection
} from '$lib/library/collections.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {
		status: number;
		constructor(status: number) {
			super('failed');
			this.status = status;
		}
	}
}));

const mocked = vi.mocked(api);

function page(items: Collection[], total = items.length, offset = 0) {
	return { items, total, limit: 60, offset };
}

function shelf(overrides: Partial<Collection> = {}): Collection {
	return {
		size_bytes: null,
		counts: {},
		id: 'c1',
		name: 'Summer',
		locked: false,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		art: null,
		vault: false,
		item_count: 4,
		/* Nought on a wall row: the tally is filled only by the route that answers about ONE
		   collection, so a list's rows carry the field at its default. */
		o_count: 0,
		favorite: false,
		pinned: false,
		rating: null,
		shared: false,
		restricted: false,
		...overrides
	};
}

beforeEach(() => {
	vi.resetAllMocks();
});

describe('one page of collections', () => {
	it('asks the server for the page and the order, and keeps the total beside the rows', async () => {
		mocked.get.mockResolvedValue(page([shelf()], 140, 60));
		const store = new Collections();

		await store.load(60, 60, 'oldest');

		expect(mocked.get).toHaveBeenCalledWith('/collections', {
			query: { limit: 60, offset: 60, sort: 'oldest', prefix: '' }
		});
		expect(store.items).toEqual([shelf()]);
		expect(store.total).toBe(140);
		expect(store.loaded).toBe(true);
	});

	it('opens on name order, which is the order the server falls back on too', async () => {
		mocked.get.mockResolvedValue(page([shelf()]));
		const store = new Collections();

		await store.load();

		expect(mocked.get).toHaveBeenCalledWith('/collections', {
			query: { limit: 200, offset: 0, sort: 'name_az', prefix: '' }
		});
	});

	it('remembers the order it was asked for', async () => {
		mocked.get.mockResolvedValue(page([shelf()]));
		const store = new Collections();

		await store.load(0, 60, 'largest');
		expect(store.sort).toBe('largest');

		await store.load(0, 60);
		expect(mocked.get).toHaveBeenLastCalledWith('/collections', {
			query: { limit: 60, offset: 0, sort: 'largest', prefix: '' }
		});
	});

	it('says so rather than showing a stale list when the load fails', async () => {
		mocked.get.mockResolvedValueOnce(page([shelf()]));
		const store = new Collections();
		await store.load();
		expect(store.items).toHaveLength(1);

		mocked.get.mockRejectedValueOnce(new Error('offline'));
		await store.load();

		expect(store.failed).toBe(true);
	});

	it('lets a newer response win over one that was slower', async () => {
		let releaseFirst: (value: unknown) => void = () => {};
		const first = new Promise((resolve) => {
			releaseFirst = resolve;
		});
		mocked.get.mockReturnValueOnce(first as never);
		mocked.get.mockResolvedValueOnce(page([shelf({ id: 'c2', name: 'Winter' })], 2));

		const store = new Collections();
		const slow = store.load(0);
		const quick = store.load(60);
		await quick;
		releaseFirst(page([shelf()], 1));
		await slow;

		expect(store.items.map((one) => one.id)).toEqual(['c2']);
		expect(store.total).toBe(2);
	});

	it('drops the total as well as the rows when the list is forgotten', async () => {
		mocked.get.mockResolvedValue(page([shelf()], 9));
		const store = new Collections();
		await store.load();

		store.forget();

		expect(store.items).toEqual([]);
		expect(store.total).toBe(0);
		expect(store.loaded).toBe(false);
	});
});

describe('the heart and the stars on a shelf', () => {
	it('settle from the server answer rather than from what was asked for', async () => {
		mocked.get.mockResolvedValue(page([shelf()]));
		const store = new Collections();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: true, rating: 3 });
		await store.setFavorite('c1', true);

		expect(mocked.put).toHaveBeenCalledWith('/collections/c1/favorite', {
			body: { favorite: true }
		});
		expect(store.items[0].favorite).toBe(true);
		expect(store.items[0].rating).toBe(3);
	});

	it('reach the row they name and no other', async () => {
		mocked.get.mockResolvedValue(page([shelf(), shelf({ id: 'c2', name: 'Winter' })]));
		const store = new Collections();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: true, rating: null });
		await store.setFavorite('c2', true);

		expect(store.items.map((one) => one.favorite)).toEqual([false, true]);
	});

	it('leave the rest of the row alone', async () => {
		mocked.get.mockResolvedValue(page([shelf({ item_count: 21, cover_asset_id: 'a9' })]));
		const store = new Collections();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: false, rating: 5 });
		await store.setRating('c1', 5);

		expect(store.items[0].name).toBe('Summer');
		expect(store.items[0].item_count).toBe(21);
		expect(store.items[0].cover_asset_id).toBe('a9');
	});

	it('clear the stars with a null rather than with a zero', async () => {
		mocked.get.mockResolvedValue(page([shelf({ rating: 5 })]));
		const store = new Collections();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: false, rating: null });
		await store.setRating('c1', null);

		expect(mocked.put).toHaveBeenCalledWith('/collections/c1/rating', { body: { rating: null } });
		expect(store.items[0].rating).toBeNull();
	});
});

describe('the orders this wall offers', () => {
	it('are the eleven every wall shares, then the two that need an opinion', () => {
		expect(COLLECTION_ORDERS.map((one) => one.value)).toEqual([
			'newest',
			'oldest',
			'edited',
			'name_az',
			'name_za',
			'largest',
			'smallest',
			'largest_total',
			'smallest_total',
			'longest_total',
			'shortest_total',
			'favorite',
			'rating'
		]);
	});

	it('carry no comparison, because the server does the ordering', () => {
		for (const order of COLLECTION_ORDERS) {
			expect(Object.keys(order).sort()).toEqual(['label', 'value']);
		}
	});
});

describe('a collection longer than a page', () => {
	it('is paged by the walls of files from its own route, which names no row', () => {
		expect(contentsSource('c1')).toMatchObject({ path: '/collections/c1/items', anchored: false });
	});

	it('moves a file by one request naming it and its direction, and reads nothing', async () => {
		mocked.post.mockResolvedValue({ changed: 2, skipped: 0, reason: null });
		await new Collections().move('c1', 'a599', -1);
		await new Collections().move('c1', 'a1', 1);

		expect(mocked.post.mock.calls).toEqual([
			[
				'/collections/c1/items',
				{ body: { asset_ids: ['a599'], action: 'move', direction: 'earlier' } }
			],
			['/collections/c1/items', { body: { asset_ids: ['a1'], action: 'move', direction: 'later' } }]
		]);
		expect(mocked.get).not.toHaveBeenCalled();
	});
});

describe('making and unmaking', () => {
	it('puts a new collection on the page AND moves the total with it', async () => {
		mocked.get.mockResolvedValue(page([], 0));
		const store = new Collections();
		await store.load();
		expect(store.total).toBe(0);

		mocked.post.mockResolvedValue(shelf({ id: 'c9', name: 'New', item_count: 0 }));
		await store.create('New');

		expect(store.items.map((one) => one.id)).toEqual(['c9']);
		expect(store.total).toBe(1);
	});

	it('takes one off the total when a row on this page goes', async () => {
		mocked.get.mockResolvedValue(page([shelf()], 12));
		const store = new Collections();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('c1');

		expect(store.total).toBe(11);
	});

	it('and leaves the total alone for a row this page never held', async () => {
		mocked.get.mockResolvedValue(page([shelf()], 12));
		const store = new Collections();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('somewhere-else');

		expect(store.total).toBe(12);
	});
});

describe('a write while a page is still in the air', () => {
	it('keeps the new collection rather than letting the older answer land on top of it', async () => {
		/* The race that would make a wall lose the thing somebody had just made, and only under load:
		   the page is asked for, the create lands first, and the page's own answer arrives afterwards
		   and replaces the rows, the new one among them. */
		let release: (value: unknown) => void = () => {};
		const inFlight = new Promise((resolve) => {
			release = resolve;
		});
		mocked.get.mockReturnValueOnce(inFlight as never);

		const store = new Collections();
		const loading = store.load();

		mocked.post.mockResolvedValue(shelf({ id: 'c9', name: 'Just made' }));
		await store.create('Just made');
		expect(store.items.map((one) => one.id)).toEqual(['c9']);

		release(page([shelf({ id: 'c1', name: 'Older' })], 1));
		await loading;

		expect(store.items.map((one) => one.id)).toEqual(['c9']);
	});
});

describe('what is in one collection', () => {
	it('asks for the whole collection when nothing narrows it', async () => {
		mocked.get.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });

		await new Collections().contents('c1');

		expect(mocked.get).toHaveBeenCalledWith('/collections/c1/items', {
			query: { limit: 200, offset: 0 }
		});
	});

	it('sends every value of the narrowing fields, each its own parameter', async () => {
		mocked.get.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });

		await new Collections().contents('c1', 200, 0, {
			people: ['Jane Else'],
			tags: ['beach', '-dusk']
		});

		expect(mocked.get).toHaveBeenCalledWith('/collections/c1/items', {
			query: { people: ['Jane Else'], tags: ['beach', '-dusk'], limit: 200, offset: 0 }
		});
	});

	it('asks with the words and every filter in the address, and nothing else', () => {
		const url = new URL('http://sift/collections/c1?show=files&q=tags:beach&rating=4&q=&media=');
		url.searchParams.append('tags', 'dusk');
		url.searchParams.append('tags', '-rain');

		expect(contentsAsked(url)).toEqual({
			q: ['tags:beach'],
			rating: ['4'],
			tags: ['dusk', '-rain']
		});
	});
});
