/* Keeping a thing at the top of its wall, on all five named kinds and on a file.
 *
 * The write lives in one place because a pin is the same write on all five walls and only the word
 * in the address differs. The heart beside it is written four different ways across those same
 * walls, which is how the four came to differ in whether they put a failed write back, whether they
 * say anything, and whether they clear the selection: drift nobody can see until they press the
 * one that behaves differently.
 *
 * So what these hold is the part that is shared: the address, the body, and what a set that fails
 * part-way leaves on screen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	allPinned,
	pinAll,
	setFilePinned,
	setFilesPinned,
	type Pinnable
} from '$lib/library/pinning.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/* The REQUESTS are stood in for and nothing else is. A mock built from scratch would leave out
   `ApiError`, which the refusal path reaches for the moment a write fails, so the one test about a
   failing write would die on the mock rather than on the behaviour. */
vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

beforeEach(() => {
	vi.clearAllMocks();
	toasts.clear();
});

describe('the address a pin is written to', () => {
	const KINDS: Pinnable[] = ['people', 'sites', 'collections', 'tags', 'photo-sets'];

	it.each(KINDS)('is the wall-s own word for %s', async (kind) => {
		/* Five walls and one write. A sixth cannot arrive by accident: a username carries no pin
		   for the same reason it carries no heart, and loops, Favorites and Hidden are walls of
		   media rather than of named things. */
		mocked.put.mockResolvedValue({ pinned: true });

		await pinAll(kind, ['x1'], true, () => {});

		expect(mocked.put).toHaveBeenCalledWith(`/${kind}/x1/pin`, { body: { pinned: true } });
	});

	it('a file has an address of its own, and answers the whole opinion', async () => {
		/* The whole opinion rather than just the pin, because that is what the route replies with
		   and it is the same shape this account's other tabs are told: one description, so the tab
		   that pressed the control and the tab that did not are served by one piece of code. */
		mocked.put.mockResolvedValue({
			asset_id: 'a1',
			favorite: true,
			rating: 4,
			views: 2,
			pinned: true
		});

		const held = await setFilePinned('a1', true);

		expect(mocked.put).toHaveBeenCalledWith('/assets/a1/pin', { body: { pinned: true } });
		expect(held).toEqual({ asset_id: 'a1', favorite: true, rating: 4, views: 2, pinned: true });
	});
});

describe('a whole selection', () => {
	it('moves at once and settles onto what the server ended up holding', async () => {
		/* Optimistic, like every other opinion in Sift: a control that waits for a round trip before
		   it changes reads as broken, and the round trip is nearly always a success. */
		const settled: [string, boolean][] = [];
		mocked.put.mockResolvedValue({ pinned: true });

		expect(await pinAll('tags', ['t1', 't2'], true, (id, on) => settled.push([id, on]))).toBe(true);

		// Every row guessed first, then every row settled from the server's own answer.
		expect(settled).toEqual([
			['t1', true],
			['t2', true],
			['t1', true],
			['t2', true]
		]);
	});

	it('puts every row back when any one of them fails, and says so once', async () => {
		/* All or nothing ON SCREEN, deliberately: a wall showing three of five pinned after one
		   press is a wall nobody can reason about. The rows that DID land stay landed on the server
		   (the next read shows them), because a second write undoing somebody's data because a
		   third one timed out is worse than a screen that is briefly behind. */
		const settled: [string, boolean][] = [];
		mocked.put.mockResolvedValueOnce({ pinned: true }).mockRejectedValueOnce(new Error('no'));

		expect(await pinAll('people', ['p1', 'p2'], true, (id, on) => settled.push([id, on]))).toBe(
			false
		);

		expect(settled.slice(-2)).toEqual([
			['p1', false],
			['p2', false]
		]);
		expect(toasts.items).toHaveLength(1);
		expect(toasts.items[0].tone).toBe('error');
	});
});

describe('what the verb offers over a set of rows', () => {
	it('says pinned only when every one of them is', () => {
		expect(allPinned([true, true])).toBe(true);
	});

	it('reads a MIXED set as not pinned, so the press pins the lot', () => {
		/* The other reading (unpin, because one of them is pinned) takes away something somebody
		   set, from a press whose label said nothing about it. And toggling each into whatever it
		   was not leaves the selection more mixed than it started. */
		expect(allPinned([true, false])).toBe(false);
		expect(allPinned([false, true])).toBe(false);
	});

	it('reads a row that has no answer as not pinned', () => {
		// A wall holding a row it has not loaded the state for, which is `undefined` rather than
		// false, and the two must not be told apart here, or the verb reads a gap as a decision.
		expect(allPinned([undefined])).toBe(false);
		expect(allPinned([true, undefined])).toBe(false);
	});

	it('offers nothing over an empty set', () => {
		// `every` is true of an empty list, so without the length check a bar with nothing picked
		// would say Unpin.
		expect(allPinned([])).toBe(false);
	});
});

describe('a selection of files', () => {
	it('goes in ONE request, not one per file', async () => {
		/* One request, not one per file: a hundred and thirty-four pins awaited one after
		   another would be a hundred and thirty-four round trips. The single write above stays
		   for one tile: it has nothing to batch and settles onto that file's own opinion. */
		mocked.post.mockResolvedValue({
			changed: 2,
			skipped: 0,
			reason: null,
			reason_many: null,
			vault_locked: false
		});

		const done = await setFilesPinned(['a1', 'a2'], true);

		expect(mocked.post.mock.calls).toEqual([
			['/assets/pin', { body: { asset_ids: ['a1', 'a2'], pinned: true } }]
		]);
		expect(done.changed).toBe(2);
		expect(mocked.put).not.toHaveBeenCalled();
	});
});
