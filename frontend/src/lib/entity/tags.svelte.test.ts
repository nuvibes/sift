/* The tag store: the parts that decide what the wall shows. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { TAG_ORDERS, Tags, underParents, type Tag } from '$lib/entity/tags.svelte';
import { pickRow } from '$lib/entity/entity-picture';

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

/* The list route answers a page rather than an array, and the shape lives here once. */
function page(items: Tag[], total = items.length, offset = 0) {
	return { items, total, limit: 60, offset };
}

function tag(overrides: Partial<Tag> = {}): Tag {
	return {
		size_bytes: null,
		parent_id: null,
		parent_name: null,
		counts: {},
		id: 't1',
		name: 'beach',
		locked: false,
		asset_count: 3,
		/* Nought on a wall row: the tally is filled only by the route that answers about ONE tag. */
		o_count: 0,
		shared: false,
		restricted: false,
		hidden: false,
		favorite: false,
		pinned: false,
		rating: null,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		art: null,
		record: null,
		// Off, which is what everything not deliberately kept local is.
		keep_local: false,
		keep_from_swaps: false,
		...overrides
	};
}

beforeEach(() => {
	vi.resetAllMocks();
});

describe('one page of tags', () => {
	it('asks the server for the page and the order, and keeps the total beside the rows', async () => {
		mocked.get.mockResolvedValue(page([tag()], 97, 60));
		const store = new Tags();

		await store.load('be', 60, 60, 'name_az');

		expect(mocked.get).toHaveBeenCalledWith('/tags', {
			query: { prefix: 'be', limit: 60, offset: 60, sort: 'name_az' }
		});
		expect(store.items).toEqual([tag()]);
		// The total is the whole scoped list, not the row in hand.
		expect(store.total).toBe(97);
		expect(store.loaded).toBe(true);
	});

	it('remembers the order it was asked for, so the next load keeps it', async () => {
		mocked.get.mockResolvedValue(page([tag()]));
		const store = new Tags();

		await store.load('', 0, 60, 'oldest');
		expect(store.sort).toBe('oldest');

		await store.load();
		expect(mocked.get).toHaveBeenLastCalledWith('/tags', {
			query: { prefix: '', limit: 200, offset: 0, sort: 'oldest' }
		});
	});

	it('says so rather than showing a stale list when the load fails', async () => {
		// Loaded once first, on purpose: without that the claim in the name is not exercised at
		// all, because an empty list is what a store that never loaded already holds.
		mocked.get.mockResolvedValueOnce(page([tag()]));
		const store = new Tags();
		await store.load();
		expect(store.items).toHaveLength(1);

		mocked.get.mockRejectedValueOnce(new Error('offline'));
		await store.load();

		expect(store.failed).toBe(true);
		expect(store.items).toHaveLength(1);
	});

	it('lets a newer response win over one that was slower', async () => {
		let releaseFirst: (value: unknown) => void = () => {};
		const first = new Promise((resolve) => {
			releaseFirst = resolve;
		});
		mocked.get.mockReturnValueOnce(first as never);
		mocked.get.mockResolvedValueOnce(page([tag({ id: 't2', name: 'sunset' })], 2));

		const store = new Tags();
		const slow = store.load('', 0, 60, 'name_az');
		const quick = store.load('', 60, 60, 'name_az');
		await quick;
		releaseFirst(page([tag()], 1));
		await slow;

		expect(store.items.map((one) => one.id)).toEqual(['t2']);
		expect(store.total).toBe(2);
	});

	it('drops the total as well as the rows when the list is forgotten', async () => {
		// The vault is what does this. A count held from while it was open describes a library that
		// is not being shown any more, and a pager drawn from it offers pages that are not there.
		mocked.get.mockResolvedValue(page([tag()], 12));
		const store = new Tags();
		await store.load();

		store.forget();

		expect(store.items).toEqual([]);
		expect(store.total).toBe(0);
		expect(store.loaded).toBe(false);
	});
});

describe('the heart and the stars', () => {
	it('settle from the server answer rather than from what was asked for', async () => {
		mocked.get.mockResolvedValue(page([tag(), tag({ id: 't2', name: 'sunset' })]));
		const store = new Tags();
		await store.load();

		// Deliberately not what was asked for. The row must take what the server ended up holding,
		// which is the whole reason the write replies with a state at all.
		mocked.put.mockResolvedValue({ favorite: true, rating: 4 });
		await store.setFavorite('t1', true);

		expect(mocked.put).toHaveBeenCalledWith('/tags/t1/favorite', { body: { favorite: true } });
		expect(store.items[0].favorite).toBe(true);
		expect(store.items[0].rating).toBe(4);
	});

	it('reach the row they name and no other', async () => {
		mocked.get.mockResolvedValue(page([tag(), tag({ id: 't2', name: 'sunset' })]));
		const store = new Tags();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: true, rating: null });
		await store.setFavorite('t2', true);

		expect(store.items.map((one) => one.favorite)).toEqual([false, true]);
	});

	it('leave the rest of the row alone', async () => {
		// The reply carries two fields and the row carries nine.
		mocked.get.mockResolvedValue(page([tag({ asset_count: 12 })]));
		const store = new Tags();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: true, rating: null });
		await store.setFavorite('t1', true);

		expect(store.items[0].name).toBe('beach');
		expect(store.items[0].asset_count).toBe(12);
	});

	it('clear the stars with a null rather than with a zero', async () => {
		mocked.get.mockResolvedValue(page([tag({ rating: 5 })]));
		const store = new Tags();
		await store.load();

		mocked.put.mockResolvedValue({ favorite: false, rating: null });
		await store.setRating('t1', null);

		expect(mocked.put).toHaveBeenCalledWith('/tags/t1/rating', { body: { rating: null } });
		expect(store.items[0].rating).toBeNull();
	});
});

describe('the orders this wall offers', () => {
	it('are the eleven every wall shares, then the two that need an opinion', () => {
		expect(TAG_ORDERS.map((one) => one.value)).toEqual([
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
		// A comparison here would sort the rows in hand and call it the order of the library.
		for (const order of TAG_ORDERS) expect(Object.keys(order).sort()).toEqual(['label', 'value']);
	});
});

describe('making and unmaking', () => {
	it('puts a new tag on the page AND moves the total with it', async () => {
		/* The pager is drawn from the total and so is whether the wall believes it has anything
		   on it at all. */
		mocked.get.mockResolvedValue(page([], 0));
		const store = new Tags();
		await store.load();
		expect(store.total).toBe(0);

		mocked.post.mockResolvedValue(tag({ id: 't9', name: 'new', asset_count: 0 }));
		await store.create('new');

		expect(store.items.map((one) => one.id)).toEqual(['t9']);
		expect(store.total).toBe(1);
	});

	it('takes one off the total when a row on this page goes', async () => {
		mocked.get.mockResolvedValue(page([tag()], 41));
		const store = new Tags();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('t1');

		expect(store.items).toEqual([]);
		expect(store.total).toBe(40);
	});

	it('and leaves the total alone for a row this page never held', async () => {
		// Deleting from a menu over a row on another page.
		mocked.get.mockResolvedValue(page([tag()], 41));
		const store = new Tags();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('somewhere-else');

		expect(store.total).toBe(41);
	});
});

describe('a rename', () => {
	it('sends the name alone, and reads nothing first', async () => {
		/* A tag has no colour to carry, so a rename is one write with the name in it and nothing
		   is read back first. */
		mocked.get.mockResolvedValueOnce(page([tag({ id: 't1' })]));
		const store = new Tags();
		await store.load();
		mocked.get.mockClear();

		mocked.put.mockResolvedValue(tag({ id: 't7', name: 'sand' }));
		await store.rename('t7', 'sand');

		expect(mocked.get).not.toHaveBeenCalled();
		expect(mocked.put).toHaveBeenCalledWith('/tags/t7', { body: { name: 'sand' } });
	});

	it('draws the new name on the press, and puts the old one back on a refusal', async () => {
		mocked.get.mockResolvedValueOnce(page([tag({ id: 't1', name: 'beach' })]));
		const store = new Tags();
		await store.load();
		let refuse: (error: unknown) => void = () => {};
		mocked.put.mockReturnValueOnce(new Promise((_, reject) => (refuse = reject)) as never);

		const renaming = store.rename('t1', 'shore');
		expect(store.items.map((one) => one.name)).toEqual(['shore']);
		refuse(new Error('taken'));
		await expect(renaming).rejects.toThrow('taken');
		expect(store.items.map((one) => one.name)).toEqual(['beach']);
	});
});

describe('a write while a page is still in the air', () => {
	it('keeps the new tag rather than letting the older answer land on top of it', async () => {
		/* The race that would lose the thing somebody had just made, and only ever under load:
		   the page is asked for, the create lands first, and the page's own answer arrives
		   afterwards and replaces the rows, including the one that had just been added. */
		let release: (value: unknown) => void = () => {};
		const inFlight = new Promise((resolve) => {
			release = resolve;
		});
		mocked.get.mockReturnValueOnce(inFlight as never);

		const store = new Tags();
		const loading = store.load();

		mocked.post.mockResolvedValue(tag({ id: 't9', name: 'just-made' }));
		await store.create('just-made');
		expect(store.items.map((one) => one.id)).toEqual(['t9']);

		release(page([tag({ id: 't1', name: 'older' })], 1));
		await loading;

		expect(store.items.map((one) => one.id)).toEqual(['t9']);
	});

	it('and keeps the removal rather than having the row put back', async () => {
		mocked.get.mockResolvedValueOnce(page([tag(), tag({ id: 't2', name: 'sunset' })], 2));
		const store = new Tags();
		await store.load();

		let release: (value: unknown) => void = () => {};
		const inFlight = new Promise((resolve) => {
			release = resolve;
		});
		mocked.get.mockReturnValueOnce(inFlight as never);
		const loading = store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('t1');

		release(page([tag(), tag({ id: 't2', name: 'sunset' })], 2));
		await loading;

		expect(store.items.map((one) => one.id)).toEqual(['t2']);
	});
});

describe('a picker draws a branch under its parent', () => {
	const row = (id: string, parent: string | null = null, parentName: string | null = null) => ({
		id,
		name: id,
		parent_id: parent,
		parent_name: parentName
	});

	it('moves a tag to just under its parent on the page, one step in', () => {
		const page = [
			row('Beach'),
			row('Armchair', 'Furniture', 'Furniture'),
			row('Furniture'),
			row('Sofa')
		];
		const placed = underParents(page).map((one) => [one.id, one.depth, one.within]);
		expect(placed).toEqual([
			['Beach', 0, null],
			['Furniture', 0, null],
			['Armchair', 1, null],
			['Sofa', 0, null]
		]);
	});

	it('names the parent of a tag whose parent is not on the page', () => {
		const placed = underParents([row('Armchair', 'Furniture', 'Furniture')]);
		expect(placed.map((one) => [one.id, one.depth, one.within])).toEqual([
			['Armchair', 0, 'Furniture']
		]);
		expect(pickRow('tag', placed[0])).toMatchObject({ id: 'Armchair', within: 'Furniture' });
	});

	it('draws every tag once even when the tags on the page form a loop', () => {
		const placed = underParents([row('A', 'B', 'B'), row('B', 'A', 'A')]);
		expect(placed.map((one) => one.id).sort()).toEqual(['A', 'B']);
	});

	it('says the branch of a row that was never placed in a tree, and nothing for a nested one', () => {
		expect(
			pickRow('tag', { id: 'Armchair', name: 'Armchair', parent_name: 'Furniture' })
		).toMatchObject({
			within: 'Furniture'
		});
		expect(
			pickRow('tag', { id: 'Armchair', name: 'Armchair', depth: 1, within: null })
		).not.toHaveProperty('within');
	});
});
