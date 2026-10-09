/*
 * Select all reads every ROW the query matches, without moving the wall, and never answers short.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

const PER_PAGE = 200;

function library(total: number) {
	return async (_path: string, options?: { query?: Record<string, number> }) => {
		const offset = Number(options?.query?.offset ?? 0);
		const limit = Number(options?.query?.limit ?? PER_PAGE);
		const items = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, at) => ({
			id: `a${offset + at}`
		}));
		return { items, total, offset, limit };
	};
}

beforeEach(() => vi.clearAllMocks());

describe('every row of the current question', () => {
	it('reads past the page on screen, in blocks of the server ceiling', async () => {
		const grid = new Grid();
		grid.total = 450;
		mocked.get.mockImplementation(library(450) as never);

		const rows = await grid.everyRow({ sort: 'newest' });

		expect(rows).toHaveLength(450);
		expect(rows[0].id).toBe('a0');
		expect(rows[449].id).toBe('a449');
		// Three requests, not 450.
		expect(mocked.get).toHaveBeenCalledTimes(3);
	});

	it('carries the whole question, so it selects what the count was of', async () => {
		const grid = new Grid();
		grid.total = 10;
		mocked.get.mockImplementation(library(10) as never);

		await grid.everyRow({ sort: 'oldest', q: 'beach' });

		// The wall's own query: dropping the filter would select the library during a search.
		const [, options] = mocked.get.mock.calls[0] as [string, { query: Record<string, unknown> }];
		expect(options.query.sort).toBe('oldest');
		expect(options.query.q).toBe('beach');
	});

	it('does not move the wall', async () => {
		const grid = new Grid();
		grid.total = 450;
		grid.offset = 200;
		grid.items = [{ id: 'whatever' }] as never;
		mocked.get.mockImplementation(library(450) as never);

		await grid.everyRow({});

		expect(grid.offset).toBe(200);
		expect(grid.items).toHaveLength(1);
	});

	it('asks once more when the list divides exactly by the page size', async () => {
		// An exact multiple of 200 must not stop a block short.
		const grid = new Grid();
		grid.total = 400;
		mocked.get.mockImplementation(library(400) as never);

		const rows = await grid.everyRow({});

		expect(rows).toHaveLength(400);
		expect(mocked.get).toHaveBeenCalledTimes(3);
	});

	it('THROWS rather than returning what it managed', async () => {
		/*
		 * A short answer looks like a small library, and somebody would delete it believing it was
		 * all.
		 */
		const grid = new Grid();
		grid.total = 450;
		let calls = 0;
		mocked.get.mockImplementation((async (path: string, options?: never) => {
			calls += 1;
			if (calls === 2) throw new Error('the server went away');
			return library(450)(path, options);
		}) as never);

		await expect(grid.everyRow({})).rejects.toThrow();
	});

	it('answers nothing for a question that matches nothing', async () => {
		const grid = new Grid();
		grid.total = 0;
		mocked.get.mockImplementation(library(0) as never);

		expect(await grid.everyRow({})).toEqual([]);
	});
});
