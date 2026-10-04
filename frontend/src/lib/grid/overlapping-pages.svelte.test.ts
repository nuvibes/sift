/*
 * A page filled from two requests, where the second request answers from a DIFFERENT set.
 *
 * How many files fill a row cannot be known before they arrive, so a page is topped up: ask, lay
 * out what came back, ask again for what is still missing. Every one of those rounds would assume
 * that block N+1 holds nothing block N did: true of a list, and false of a search by meaning,
 * where the model is asked for as many neighbours as the page reaches and a deeper request comes
 * back from a larger set in a different order.
 *
 * So two blocks can overlap: a first answer counting 200 and a top-up counting 500. The wall is a
 * keyed each, so a repeated file throws `each_key_duplicate`, and an error thrown during a render
 * abandons it, which freezes the tiles and the pager's total while the query, the request and the
 * answer are all correct.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

/** A window wide and tall enough to want more than one block. */
function measured(): Grid {
	const grid = new Grid();
	grid.containerWidth = 1600;
	grid.screenHeight = 1000;
	return grid;
}

/* A file of a fixed shape, so the layout arithmetic is predictable, and a TALL one, because a
   page of tall files takes far more of them than the first estimate allows for, which is exactly
   the case that asks a second time. */
function file(id: string) {
	return { id, width: 608, height: 1080 };
}

beforeEach(() => vi.clearAllMocks());

describe('a page topped up from a set that moved under it', () => {
	it('holds each file once, however much the blocks overlap', async () => {
		/* Every request answers from the beginning: the worst case of the real fault, where a
		 * deeper ask is served out of a set that now starts somewhere else. */
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number } }
		) => ({
			items: Array.from({ length: Number(options?.query?.limit ?? 0) }, (_, at) => file(`a${at}`)),
			total: 500,
			offset: 0,
			limit: Number(options?.query?.limit ?? 0)
		})) as never);

		const grid = measured();
		await grid.loadAt({ q: 'blue sky', meaning: '1' }, { at: 0 });

		const ids = grid.items.map((item) => item.id);
		expect(new Set(ids).size).toBe(ids.length);
	});

	it('still takes what is new in the block that overlapped', async () => {
		let round = 0;
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number } }
		) => {
			const limit = Number(options?.query?.limit ?? 0);
			// The second block repeats its first ten files and then goes on.
			const from = round === 0 ? 0 : limit - 10;
			round += 1;
			return {
				items: Array.from({ length: limit }, (_, at) => file(`a${from + at}`)),
				total: 500,
				offset: 0,
				limit
			};
		}) as never);

		const grid = measured();
		await grid.loadAt({ q: 'blue sky', meaning: '1' }, { at: 0 });

		const ids = grid.items.map((item) => item.id);
		// The page really was filled from more than one request, or this proves nothing.
		expect(mocked.get.mock.calls.length).toBeGreaterThan(1);
		expect(new Set(ids).size).toBe(ids.length);
		// The overlap is dropped and the rest of the block is kept, so the page is not cut short.
		expect(ids.length).toBeGreaterThan(Number(mocked.get.mock.calls[0][1]?.query?.limit ?? 0));
	});

	it('and the ordinary case is untouched: a list pages exactly as it did', async () => {
		mocked.get.mockImplementation((async (
			_path: string,
			options?: { query?: { limit?: number; offset?: number } }
		) => {
			const limit = Number(options?.query?.limit ?? 0);
			const offset = Number(options?.query?.offset ?? 0);
			return {
				items: Array.from({ length: limit }, (_, at) => file(`a${offset + at}`)),
				total: 5000,
				offset,
				limit
			};
		}) as never);

		const grid = measured();
		await grid.loadAt({ sort: 'newest' }, { at: 0 });

		const ids = grid.items.map((item) => item.id);
		expect(ids[0]).toBe('a0');
		expect(new Set(ids).size).toBe(ids.length);
	});
});
