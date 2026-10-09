/* The People store: the parts that decide what a screen shows. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { People, Sites, type Person, type Site } from '$lib/people/people.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';

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

/* The list route answers a page rather than an array. */
function page(items: Person[], total = items.length) {
	return { items, total, limit: 60, offset: 0 };
}

function person(overrides: Partial<Person> = {}): Person {
	return {
		size_bytes: null,
		counts: {},
		id: 'p1',
		name: 'Jane Doe',
		locked: false,
		vault: false,
		source_name: null,
		notes: null,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		cover_track_id: null,
		asset_count: 3,
		// This account's own O tally over their files. Only the person's own page fills it, so
		// nought is what a row off the wall carries.
		o_count: 0,
		favorite: false,
		pinned: false,
		// Off, which is what a person nobody has ticked is, and a real answer rather than an
		// absence: the column refuses NULL, so the wall always has one to draw a mark from or not.
		pmv_creator: false,
		rating: null,
		// Off, which is what everything not deliberately kept local is.
		keep_local: false,
		// And "Don't swap", the refusal's other mark, off for the same reason.
		keep_from_swaps: false,
		automatic: false,
		// Which pass named them, where one did. Null is what a person doing it looks like.
		source: null,
		faces_claimed: 0,
		shared: false,
		restricted: false,
		art: null,
		record: null,
		...overrides
	};
}

beforeEach(() => {
	vi.resetAllMocks();
});

describe('the people list', () => {
	it('holds what the server sent', async () => {
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();

		await store.load();

		expect(store.items).toEqual([person()]);
		expect(store.loaded).toBe(true);
		expect(store.failed).toBe(false);
	});

	it('says so rather than showing a stale list when the load fails', async () => {
		// Loaded once first, on purpose. Without that the items assertion passes on a store that
		// was never filled, and the stale-list claim in the name is not exercised at all.
		mocked.get.mockResolvedValueOnce(page([person()]));
		const store = new People();
		await store.load();
		expect(store.items).toHaveLength(1);

		mocked.get.mockRejectedValueOnce(new Error('offline'));
		await store.load();

		expect(store.failed).toBe(true);
	});

	it('lets a newer response win over one that was slower', async () => {
		// Typing quickly fires several searches. Without the generation counter the one that comes
		// back last wins, which is not the one that was asked for last.
		const store = new People();
		let releaseFirst: (value: ReturnType<typeof page>) => void = () => {};
		mocked.get
			.mockReturnValueOnce(
				new Promise<ReturnType<typeof page>>((resolve) => {
					releaseFirst = resolve;
				})
			)
			.mockResolvedValueOnce(page([person({ id: 'p2', name: 'Second' })]));

		const first = store.load('ja');
		const second = store.load('jan');
		await second;
		releaseFirst(page([person({ id: 'p1', name: 'First' })]));
		await first;

		expect(store.items.map((p) => p.id)).toEqual(['p2']);
	});

	it('does not put a person created straight into the vault on the list', async () => {
		mocked.get.mockResolvedValue(page([]));
		const store = new People();
		await store.load();
		mocked.post.mockResolvedValue(person({ vault: true }));

		await store.create('Hidden One', true);

		expect(store.items).toEqual([]);
	});

	it('asks the server again when somebody goes into the vault, rather than guessing', async () => {
		/* Whether they leave the list has TWO answers and only the server holds them: with the
		 * vault shut they are concealed from every route that names them, and with it open they
		 * are still listed and simply marked. */
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();
		await store.load();
		mocked.put.mockResolvedValue(person({ vault: true }));
		// The vault is shut, so the server stops listing them.
		mocked.get.mockResolvedValue(page([]));

		await store.update('p1', 'Jane Doe', true, null);

		expect(store.items).toEqual([]);
	});

	it('hides somebody through their own route rather than through the admin edit', async () => {
		/* Hiding has its own route. `update` carries the NAME and the record with it, and those
		 * belong to the whole install, so that route is an admin's. */
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();
		await store.load();
		mocked.put.mockResolvedValue(undefined);
		const rung = libraryChanges.generation;

		await store.setVault('p1', true);

		expect(mocked.put).toHaveBeenCalledWith('/people/p1/vault', { body: { vault: true } });
		expect(mocked.put, 'the name went back to the server with the flag').not.toHaveBeenCalledWith(
			'/people/p1',
			expect.anything()
		);
		expect(libraryChanges.generation, 'nothing was told what you may see had moved').toBe(rung + 1);
	});

	it('keeps them when the server still lists them, which is what an open vault means', async () => {
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();
		await store.load();
		mocked.put.mockResolvedValue(person({ vault: true }));
		// The vault is open, so they are still there, and now say so.
		mocked.get.mockResolvedValue(page([person({ vault: true })]));

		await store.update('p1', 'Jane Doe', true, null);

		expect(
			store.items.map((one) => one.vault),
			'hiding them emptied a screen that can see them'
		).toEqual([true]);
	});

	it('asks for the page it was told to, not always the first', async () => {
		mocked.get.mockResolvedValue(page([person()], 600));
		const store = new People();

		await store.load('', 120, 60);

		expect(mocked.get).toHaveBeenCalledWith('/people', {
			/* 'largest' is what the wall opens in: the same order the server falls through to,
			   under the name every wall in the app calls it: how much of the library each one
			   accounts for, counted in FILES. */
			query: { prefix: '', limit: '60', offset: '120', sort: 'largest' }
		});
	});

	it('asks for the order it is showing, so a reload from anywhere keeps it', async () => {
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();

		await store.load('', 0, 60, 'favorite');
		// A reload triggered by something else (a share moving, the vault opening) passes no order
		// at all.
		await store.load();

		expect(mocked.get).toHaveBeenLastCalledWith('/people', {
			query: { prefix: '', limit: '60', offset: '0', sort: 'favorite' }
		});
	});

	it('holds the scoped total, which is what a paginator is drawn from', async () => {
		// The page it holds is 60 rows and the wall has to say 600.
		mocked.get.mockResolvedValue(page([person()], 600));
		const store = new People();

		await store.load();

		expect(store.total).toBe(600);
	});

	it('forgets the total along with the rows', async () => {
		// Otherwise the vault shutting empties the wall and leaves a paginator over nothing.
		mocked.get.mockResolvedValue(page([person()], 600));
		const store = new People();
		await store.load();

		store.forget();

		expect(store.total).toBe(0);
	});

	it('keeps the count it already had when a name is edited', async () => {
		// The server does not recount on an edit, so taking its zero would blank a number that was
		// correct a moment ago.
		mocked.get.mockResolvedValue(page([person({ asset_count: 7 })]));
		const store = new People();
		await store.load();
		mocked.put.mockResolvedValue(person({ name: 'Jane D', asset_count: 0 }));

		const updated = await store.update('p1', 'Jane D', false, 'kept');

		expect(updated.asset_count).toBe(7);
	});

	it('asks the server who a term names rather than filtering what it holds', async () => {
		// The store knows names. The server also knows the aliases somebody answers to, so a local
		// filter would miss half the ways a person can be found.
		mocked.get.mockResolvedValue({ term: 'JD', people: [person()] });
		const store = new People();

		const match = await store.resolve('JD');

		expect(mocked.get).toHaveBeenCalledWith('/people/resolve', { query: { term: 'JD' } });
		expect(match.people).toEqual([person()]);
	});

	it('matches a typed prefix without regard to case', async () => {
		mocked.get.mockResolvedValue(page([person(), person({ id: 'p2', name: 'Alex' })]));
		const store = new People();
		await store.load();

		expect(store.matching('ja').map((p) => p.id)).toEqual(['p1']);
		expect(store.matching('JA').map((p) => p.id)).toEqual(['p1']);
		expect(store.matching('').length).toBe(2);
	});
});

describe('deleting a site', () => {
	it('deletes a site without asking about usernames, because there are none to ask about', async () => {
		// A username is not a thing anybody manages, so there is nothing for a confirmation to
		// offer and nothing a refusal could tell them.
		mocked.del.mockResolvedValue(undefined);
		const store = new Sites();

		await store.remove('s1');

		expect(mocked.del).toHaveBeenCalledWith('/sites/s1');
	});
});

describe('the total, and the rows it has to agree with', () => {
	/* The wall reads the total to decide whether it has anything on it at all, so a store that adds
	   a row and leaves the total alone draws "Nobody yet" over the person somebody just made. */
	it('moves with a person being made', async () => {
		mocked.get.mockResolvedValue(page([], 0));
		const store = new People();
		await store.load();

		mocked.post.mockResolvedValue(person({ id: 'p9', name: 'New Face' }));
		await store.create('New Face');

		expect(store.items.map((one) => one.id)).toEqual(['p9']);
		expect(store.total).toBe(1);
	});

	it('does not move for one made straight into the vault, which never joins the list', async () => {
		mocked.get.mockResolvedValue(page([], 0));
		const store = new People();
		await store.load();

		mocked.post.mockResolvedValue(person({ id: 'p9', name: 'Concealed', vault: true }));
		await store.create('Concealed', true);

		expect(store.items).toEqual([]);
		expect(store.total).toBe(0);
	});

	it('comes down with a row this page was holding', async () => {
		mocked.get.mockResolvedValue(page([person()], 31));
		const store = new People();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('p1');

		expect(store.total).toBe(30);
	});

	it('and stays put for a row this page never held', async () => {
		mocked.get.mockResolvedValue(page([person()], 31));
		const store = new People();
		await store.load();

		mocked.del.mockResolvedValue(undefined);
		await store.remove('somewhere-else');

		expect(store.total).toBe(31);
	});
});

describe('the sites wall and a slow answer', () => {
	/* The stale-response guard every list store carries. */
	function site(overrides: Partial<Site> = {}): Site {
		return {
			id: 's1',
			name: 'A site',
			kind: null,
			asset_count: 2,
			people_count: 1,
			site_url: null,
			notes: null,
			cover_asset_id: null,
			cover_upload_id: null,
			favorite: false,
			rating: null,
			vault: false,
			shared: false,
			restricted: false,
			...overrides
		} as Site;
	}

	it('lets a newer response win over one that was slower', async () => {
		let release: (value: unknown) => void = () => {};
		const slow = new Promise((resolve) => {
			release = resolve;
		});
		mocked.get.mockReturnValueOnce(slow as never);
		mocked.get.mockResolvedValueOnce({
			items: [site({ id: 's2', name: 'The newer answer' })],
			total: 1,
			limit: 60,
			offset: 60
		});

		const store = new Sites();
		const first = store.load('largest', 0);
		const second = store.load('largest', 60);
		await second;
		release({ items: [site()], total: 9, limit: 60, offset: 0 });
		await first;

		expect(store.items.map((one) => one.id)).toEqual(['s2']);
		expect(store.total).toBe(1);
	});

	it('and a site being made is not undone by a page already in the air', async () => {
		let release: (value: unknown) => void = () => {};
		const inFlight = new Promise((resolve) => {
			release = resolve;
		});
		mocked.get.mockReturnValueOnce(inFlight as never);

		const store = new Sites();
		const loading = store.load();

		mocked.post.mockResolvedValue(site({ id: 's9', name: 'Just made' }));
		await store.create('Just made');

		release({ items: [site()], total: 1, limit: 60, offset: 0 });
		await loading;

		expect(store.items.map((one) => one.id)).toEqual(['s9']);
	});
});

/* WHAT THE WALL IS FILTERED BY, held on the store beside the order. */
describe('the narrowing a wall was loaded with', () => {
	it('sends every value of a repeated facet, not just the last', async () => {
		/* Repeated keys are "either of these" to the entity routes. */
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();

		await store.load('', 0, 60, 'largest', null, { hair_color: ['BLONDE', 'RED'] });

		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({
			hair_color: ['BLONDE', 'RED']
		});
	});

	it('keeps it for a reload that was not told about it', async () => {
		/* Changing the order is the call site this is for: it passes a page and an order and
		   nothing else, and it must not quietly widen the wall underneath somebody. */
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();
		await store.load('', 0, 60, 'largest', null, { linked: ['yes'] });

		await store.load('', 0, 60, 'name_az');

		expect(mocked.get.mock.calls[1][1]?.query).toMatchObject({ linked: ['yes'] });
	});

	it('cannot have its paging displaced by a facet of the same name', async () => {
		/* The filtering is spread FIRST. Where the wall starts and how much of it is asked for
		   are the store's to say; an address is not allowed a vote on either. */
		mocked.get.mockResolvedValue(page([person()]));
		const store = new People();

		await store.load('', 120, 60, 'largest', null, {
			offset: ['0'],
			limit: ['1']
		} as Record<string, string[]>);

		expect(mocked.get.mock.calls[0][1]?.query).toMatchObject({ offset: '120', limit: '60' });
	});
});
