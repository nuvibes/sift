/*
 * The media grid, coming back to a page whose first file is gone.
 *
 * The address names the first file of the page it was left on (`from`) and the offset that file
 * was at (`near`), and the server serves `near` when the file is gone. Two things are the grid's to
 * get right: sending `near` beside the file, and a `near` past the end of a list that shrank:
 * delete the only file on the last page and `near` is the length of the list, so the answer is no
 * files at all. That page steps back to the last page, the same rule the walls of cards apply.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

function measured(): Grid {
	const grid = new Grid();
	grid.containerWidth = 1600;
	grid.screenHeight = 1000;
	return grid;
}

type Asked = { limit?: number; offset?: number; from?: string; near?: number };

/**
 * A library of `length` files that has lost the one the address names. `window` counts the way a
 * listing with `COUNT(*) OVER ()` does: nothing on the page, nothing counted.
 */
function library(length: number, window = false): Asked[] {
	const asks: Asked[] = [];
	mocked.get.mockImplementation((async (_path: string, options?: { query?: Asked }) => {
		const query = options?.query ?? {};
		asks.push(query);
		const limit = Number(query.limit ?? 0);
		const offset = query.from !== undefined ? Number(query.near ?? 0) : Number(query.offset ?? 0);
		const items = Array.from(
			{ length: Math.max(0, Math.min(limit, length - offset)) },
			(_, at) => ({
				id: `a${offset + at}`,
				width: 1920,
				height: 1080
			})
		);
		const total = window && items.length === 0 ? 0 : length;
		return { items, total, offset, limit };
	}) as never);
	return asks;
}

beforeEach(() => vi.clearAllMocks());

describe('a file the address names that is gone', () => {
	it('is asked for with where the page was beside it', async () => {
		const asks = library(500);
		const grid = measured();

		await grid.loadAt({}, { from: 'gone', near: 120 });

		expect(asks[0]).toMatchObject({ from: 'gone', near: 120 });
		expect(grid.offset).toBe(120);
		expect(grid.items[0]?.id).toBe('a120');
	});

	it('steps a page past the end back to the last page', async () => {
		const asks = library(30);
		const grid = measured();

		await grid.loadAt({}, { from: 'gone', near: 30 });

		expect(grid.items.length).toBeGreaterThan(0);
		expect(grid.items.at(-1)?.id).toBe('a29');
		expect(asks.length).toBeGreaterThan(1);
	});

	it('does the same where the count past the end is a window over no rows', async () => {
		library(30, true);
		const grid = measured();

		await grid.loadAt({}, { from: 'gone', near: 30 });

		expect(grid.total).toBe(30);
		expect(grid.items.at(-1)?.id).toBe('a29');
	});

	it('is an empty page at the top when the list really is empty now', async () => {
		library(0, true);
		const grid = measured();

		await grid.loadAt({}, { from: 'gone', near: 30 });

		expect(grid.items).toEqual([]);
		expect(grid.offset).toBe(0);
	});
});
