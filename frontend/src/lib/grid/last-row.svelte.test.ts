/* The end of a list is never a page of its own when it is one row or less. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);
const asked: { offset: number; limit: number }[] = [];
let total = 5000;

beforeEach(() => {
	vi.clearAllMocks();
	asked.length = 0;
	type Asked = { offset?: number; limit?: number; after?: string };
	mocked.get.mockImplementation((async (_path: string, options?: { query?: Asked }) => {
		const query = options?.query ?? {};
		const offset =
			query.after === undefined ? Number(query.offset ?? 0) : Number(query.after.slice(1)) + 1;
		const limit = Number(query.limit ?? 0);
		asked.push({ offset, limit });
		const count = Math.max(0, Math.min(limit, total - offset));
		const items = Array.from({ length: count }, (_, at) => ({
			id: `a${offset + at}`,
			width: 1920,
			height: 1080
		}));
		return { items, total, offset, limit };
	}) as never);
});

async function firstPage(): Promise<Grid> {
	const grid = new Grid();
	grid.containerWidth = 1600;
	grid.screenHeight = 1000;
	await grid.loadAt({}, { at: 0 });
	return grid;
}

describe('the last row of a list', () => {
	it('is drawn on the page before it', async () => {
		total = 5000;
		const page = (await firstPage()).filled.used;
		total = page + 1;

		const grid = await firstPage();

		expect(grid.wall.tiles.length).toBe(page + 1);
		expect(grid.nextOffset, 'a page holding one file is left after this one').toBe(total);
	});

	it('is fetched when the page stopped short of it', async () => {
		total = 5000;
		const fetched = (await firstPage()).items.length;
		total = fetched + 1;
		asked.length = 0;

		const grid = await firstPage();

		expect(asked.at(-1), 'the one file left was never asked for').toEqual({
			offset: fetched,
			limit: 1
		});
		expect(grid.items.length).toBe(total);
	});

	it('is a page of its own when it is more than a row', async () => {
		total = 5000;
		const grid = await firstPage();
		const page = grid.filled.used;
		const row = grid.rows[0]!.tiles.length;
		total = page + row + 1;

		const longer = await firstPage();

		expect(longer.nextOffset).toBe(page);
	});
});
