/*
 * What this account has picked, where it is kept, and the cap that stops it growing for ever.
 *
 * Three properties are worth a test here and the rest is bookkeeping.
 *
 * The ORDER, because it is the whole reason the module exists: the last picked first, and only the
 * last five of them handed out. What goes UNDER those rows is not this module's (it is a page
 * from the server, alphabetical), so the tail is asserted in `PickMenu.svelte.test.ts` and
 * `PickDialog.svelte.test.ts`, which is where the two halves meet.
 *
 * The CAP, in the direction that actually bites: the id just picked must never be the one dropped.
 *
 * And THIS BROWSER: a record kept in the browser's own storage is never read or written. A test says
 * so, so a browser key cannot quietly become a second source of picks.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	FREQUENT_KEPT,
	RECENT_SHOWN,
	forgetUse,
	noteUse,
	recallPicks,
	remembered,
	type FrequentKind
} from './frequent.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

const KIND: FrequentKind = 'collection';
const KEY = 'frequent.collection';
const OLD_KEY = 'sift.frequent.collection';

function names(): string[] {
	return remembered(KIND).map((one) => one.name);
}

/** What the last write to the account actually sent for this kind, parsed. */
function written(): { id: string; name: string; used: number }[] {
	const wrote = mocked.put.mock.calls
		.map((call) => (call[1]?.body as { state: Record<string, string | null> } | undefined)?.state)
		.filter((state) => state !== undefined && typeof state[KEY] === 'string');
	const last = wrote[wrote.length - 1];
	return last ? JSON.parse(last[KEY] as string) : [];
}

/** A fresh module state, because the read happens once per page and this file has many pages. */
async function hydrate(state: Record<string, string>): Promise<void> {
	mocked.get.mockResolvedValue({ state });
	await recallPicks();
}

beforeEach(() => {
	// The implementations go on BEFORE the forgetting, because forgetting writes: a bare mock
	// answers `undefined` and the `.catch` on it would throw inside the setup rather than in a test.
	mocked.get.mockResolvedValue({ state: {} });
	mocked.put.mockResolvedValue(undefined);
	localStorage.clear();
	forgetUse(KIND);
	// ...and the history is cleared AFTER it, so the setup's own write is not mistaken for a test's.
	vi.clearAllMocks();
	mocked.get.mockResolvedValue({ state: {} });
	mocked.put.mockResolvedValue(undefined);
});

describe('the order what this account has picked comes back in', () => {
	it('is empty until something has been picked', async () => {
		await hydrate({});
		expect(names()).toEqual([]);
	});

	it('is the last picked first, however often the others were picked', async () => {
		// A pick has to go to the top: under a count it could not. Evening picked three times
		// would outrank Dune picked a moment ago.
		await hydrate({});
		noteUse(KIND, { id: 'c-evening', name: 'Evening' });
		noteUse(KIND, { id: 'c-evening', name: 'Evening' });
		noteUse(KIND, { id: 'c-evening', name: 'Evening' });
		noteUse(KIND, { id: 'c-dune', name: 'Dune' });

		expect(names()).toEqual(['Dune', 'Evening']);
	});

	it('moves a row picked again back to the head', async () => {
		await hydrate({});
		noteUse(KIND, { id: 'c-dune', name: 'Dune' });
		noteUse(KIND, { id: 'c-evening', name: 'Evening' });
		noteUse(KIND, { id: 'c-dune', name: 'Dune' });

		expect(names()).toEqual(['Dune', 'Evening']);
	});

	it('hands out only the last five, while remembering more', async () => {
		// Only the recent head is drawn in front, so a picker never opens on ten or twenty rows in
		// count order before the alphabetical list begins.
		await hydrate({});
		for (let at = 0; at < 8; at += 1) noteUse(KIND, { id: `c-${at}`, name: `Pick ${at}` });

		expect(RECENT_SHOWN).toBe(5);
		expect(names()).toEqual(['Pick 7', 'Pick 6', 'Pick 5', 'Pick 4', 'Pick 3']);
		expect(written()).toHaveLength(8);
	});

	it('reads a stored record in the order it was stored, which is the recency', async () => {
		await hydrate({
			[KEY]: JSON.stringify([
				{ id: 'c-dune', name: 'Dune', used: 1 },
				{ id: 'c-amber', name: 'Amber', used: 9 }
			])
		});

		expect(names()).toEqual(['Dune', 'Amber']);
	});

	it('keeps the freshest name and never blanks one it already holds', async () => {
		// A row stored with no name at all (the stored shape allows one). Picking it from a page
		// writes one in; nothing may ever take it back out.
		await hydrate({ [KEY]: JSON.stringify([{ id: 'c-dune', name: '', used: 3 }]) });
		noteUse(KIND, { id: 'c-dune', name: 'Dune' });
		expect(names()).toEqual(['Dune']);

		noteUse(KIND, { id: 'c-dune', name: '' });
		expect(names()).toEqual(['Dune']);
	});
});

describe('what the account remembers', () => {
	it('reads back what was stored before', async () => {
		await hydrate({ [KEY]: JSON.stringify([{ id: 'c-dune', name: 'Dune', used: 9 }]) });
		expect(names()).toEqual(['Dune']);
	});

	it('treats anything it cannot read as never written', async () => {
		// Text another version of Sift wrote, or a half-written value. Every one of them has to come
		// out as an empty record rather than as an exception on the way into a menu.
		await hydrate({ [KEY]: 'not json at all' });
		expect(names()).toEqual([]);
	});

	it('drops an entry whose count is not a positive number', async () => {
		await hydrate({
			[KEY]: JSON.stringify([
				{ id: 'c-dune', name: 'Dune', used: -4 },
				{ id: 'c-amber', name: 'Amber', used: 'lots' },
				{ id: 'c-blue', name: 'Blue hour', used: 2 }
			])
		});

		expect(names()).toEqual(['Blue hour']);
	});

	it('asks the server once however many pickers open', async () => {
		await hydrate({});
		await recallPicks();
		await recallPicks();

		expect(mocked.get).toHaveBeenCalledTimes(1);
	});

	it('still works when the account cannot be read', async () => {
		// A picker with no memory is a picker showing the server's page, which is the whole list.
		// It must never be a picker that throws on the way open.
		mocked.get.mockRejectedValue(new Error('no'));
		await recallPicks();

		expect(names()).toEqual([]);
		noteUse(KIND, { id: 'c-dune', name: 'Dune' });
		expect(names()).toEqual(['Dune']);
	});
});

describe("this browser's own storage", () => {
	it('never reads or writes a browser record', async () => {
		localStorage.setItem(OLD_KEY, JSON.stringify({ 'c-dune': 4, 'c-amber': 1 }));

		await hydrate({});
		await Promise.resolve();
		await Promise.resolve();

		expect(names()).toEqual([]);
		expect(mocked.put).not.toHaveBeenCalled();
		expect(localStorage.getItem(OLD_KEY)).not.toBe(null);
	});
});

describe('the cap', () => {
	function fill(times: number): void {
		// Every one of these is picked twice, so a newcomer at one is the least used of them all:
		// the case a cap by count would lose.
		for (let at = 0; at < times; at += 1) {
			noteUse(KIND, { id: `old-${at}`, name: `Old ${at}` });
			noteUse(KIND, { id: `old-${at}`, name: `Old ${at}` });
		}
	}

	function entry(id: string): { used: number } | undefined {
		return written().find((one) => one.id === id);
	}

	it('never remembers more than it says it will', async () => {
		await hydrate({});
		fill(FREQUENT_KEPT + 20);

		expect(written()).toHaveLength(FREQUENT_KEPT);
	});

	it('keeps the id just picked even when it is the least used of them all', async () => {
		// The one that matters. Dropped, a new collection could never climb out of one pick, and a
		// library that had been used for a while would show the same fifty rows for ever.
		await hydrate({});
		fill(FREQUENT_KEPT);

		noteUse(KIND, { id: 'brand-new', name: 'Brand new' });

		expect(entry('brand-new')?.used).toBe(1);
		expect(written()[0]?.id).toBe('brand-new');
		expect(written()).toHaveLength(FREQUENT_KEPT);
	});

	it('lets a newcomer climb by picking it again', async () => {
		await hydrate({});
		fill(FREQUENT_KEPT);

		noteUse(KIND, { id: 'brand-new', name: 'Brand new' });
		noteUse(KIND, { id: 'brand-new', name: 'Brand new' });
		noteUse(KIND, { id: 'brand-new', name: 'Brand new' });

		expect(entry('brand-new')?.used).toBe(3);
	});

	it('loses the one picked longest ago, however busy it once was', async () => {
		await hydrate({});
		fill(FREQUENT_KEPT);
		// old-0 is the oldest pick and, after this, the busiest by far. Recency drops it anyway;
		// old-1, picked more recently and only twice, stays.
		for (let at = 0; at < 5; at += 1) noteUse(KIND, { id: 'old-0', name: 'Old 0' });
		for (let at = 1; at < FREQUENT_KEPT; at += 1)
			noteUse(KIND, { id: `old-${at}`, name: `Old ${at}` });

		noteUse(KIND, { id: 'brand-new', name: 'Brand new' });

		expect(entry('old-0')).toBeUndefined();
		expect(entry('old-1')?.used).toBe(3);
	});
});
