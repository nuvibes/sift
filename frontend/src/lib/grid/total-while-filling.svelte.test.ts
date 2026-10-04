/*
 * What the pager says WHILE a page is being filled.
 *
 * A page is filled from up to three requests (how many files fill a row cannot be known before
 * they arrive), and on a loaded server those rounds are seconds apart. With the total assigned
 * only after the last of them, the search box would hold the new question and the pager underneath
 * it the old question's count: "1-96 of 100,000" against a phrase matching a few dozen files.
 *
 * The number a fill has already been told is published at once, and every later answer of the same
 * fill may refine it. A fill a newer question has overtaken publishes nothing at all.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

/** A window wide and tall enough that one block cannot fill it, so the fill really tops up. */
function measured(): Grid {
	const grid = new Grid();
	grid.containerWidth = 1600;
	grid.screenHeight = 1000;
	return grid;
}

/* A TALL file, because a page of tall files takes far more of them than the first ask allows for,
   which is what makes a second request happen at all. */
function file(id: string) {
	return { id, width: 608, height: 1080 };
}

/** A promise with its resolver, so a request can be held open for as long as a test wants. */
function held<T>(): { promise: Promise<T>; settle: (value: T) => void } {
	let settle!: (value: T) => void;
	const promise = new Promise<T>((resolve) => (settle = resolve));
	return { promise, settle };
}

function block(limit: number, from: number, total: number) {
	return {
		items: Array.from({ length: limit }, (_, at) => file(`a${from + at}`)),
		total,
		offset: from,
		limit
	};
}

beforeEach(() => vi.clearAllMocks());

describe('a fill whose second request is slow', () => {
	it('publishes the new question total on the first answer, not at the end', async () => {
		const later = held<unknown>();
		let round = 0;
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number } }
		) => {
			const limit = Number(options?.query?.limit ?? 0);
			round += 1;
			if (round === 1) return block(limit, 0, 42);
			return later.promise;
		}) as never);

		const grid = measured();
		// The previous question's count, standing where the pager reads it.
		grid.total = 97825;

		const filling = grid.loadAt({ q: 'a rarer phrase' }, { at: 0 });
		// Let the first answer resolve, and no further.
		await vi.waitFor(() => expect(mocked.get.mock.calls.length).toBeGreaterThan(1));

		// The point of the whole test: the second request has not answered and the pager is already
		// right.
		expect(grid.total).toBe(42);

		later.settle(block(96, 0, 42));
		await filling;
		expect(grid.total).toBe(42);
	});

	it('and a later answer of the same fill may correct it', async () => {
		/* Asking by meaning answers a deeper request out of a larger pool, so the count a top-up
		   carries can be bigger than the one the first block carried. The last word wins. */
		let round = 0;
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number } }
		) => {
			const limit = Number(options?.query?.limit ?? 0);
			round += 1;
			return block(limit, round === 1 ? 0 : 900, round === 1 ? 200 : 500);
		}) as never);

		const grid = measured();
		await grid.loadAt({ q: 'blue bikini', meaning: '1' }, { at: 0 });

		expect(mocked.get.mock.calls.length).toBeGreaterThan(1);
		expect(grid.total).toBe(500);
	});

	it('and a fill the next question has overtaken publishes nothing', async () => {
		const first = held<unknown>();
		let round = 0;
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number } }
		) => {
			const limit = Number(options?.query?.limit ?? 0);
			round += 1;
			if (round === 1) return first.promise;
			return block(limit, 0, 7);
		}) as never);

		const grid = measured();
		grid.total = 97825;

		const abandoned = grid.loadAt({ q: 'the first phrase' }, { at: 0 });
		await vi.waitFor(() => expect(mocked.get.mock.calls.length).toBe(1));
		// The person typed again before the first answer arrived.
		const wanted = grid.loadAt({ q: 'the second phrase' }, { at: 0 });
		await wanted;
		expect(grid.total).toBe(7);

		// The overtaken fill answers afterwards, carrying a count nobody is asking for.
		first.settle(block(96, 0, 97825));
		await abandoned;
		expect(grid.total).toBe(7);
	});
});
