/* Reading every row a question matches, which is what "select all" has to mean once a wall is
 * paged.
 *
 * The number at the foot of the page counts the whole query and the grid holds one screenful of
 * it, so a selection that could only ever be what is loaded is a selection of the window size
 * rather than of what somebody asked for.
 *
 * Two properties matter more than the paging arithmetic, and both are here: it must not move the
 * wall, and it must never quietly hand back less than the whole list.
 *
 * The file's name comes from `everyId`; the reader answers with the ROWS: on a wall of moments a
 * row is a mark and the file is a field on it, and the caller cannot look that up for a row the
 * page does not hold.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

/** The server's own ceiling on one page, which is what the reader has to work under. */
const PER_PAGE = 200;

/** A library of `total` files, answered a page at a time exactly as the server does. */
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
		// Three requests, not 450. A reader asking per file would be a select-all nobody waits for.
		expect(mocked.get).toHaveBeenCalledTimes(3);
	});

	it('carries the whole question, so it selects what the count was of', async () => {
		const grid = new Grid();
		grid.total = 10;
		mocked.get.mockImplementation(library(10) as never);

		await grid.everyRow({ sort: 'oldest', q: 'beach' });

		// The same query the wall is showing. A reader that dropped the filter would select the
		// library while the screen showed a search, which is the one mistake here that is
		// destructive rather than merely wrong.
		const [, options] = mocked.get.mock.calls[0] as [string, { query: Record<string, unknown> }];
		expect(options.query.sort).toBe('oldest');
		expect(options.query.q).toBe('beach');
	});

	it('does not move the wall', async () => {
		/* The screen behind stays where it was. Somebody who picks everything and changes their mind
		 * must find the page they were looking at, scrolled where they left it. */
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
		// Otherwise the last full block looks like the end of a list that goes on, or the loop stops
		// one block short of a library whose size happens to be a multiple of 200.
		const grid = new Grid();
		grid.total = 400;
		mocked.get.mockImplementation(library(400) as never);

		const rows = await grid.everyRow({});

		expect(rows).toHaveLength(400);
		expect(mocked.get).toHaveBeenCalledTimes(3);
	});

	it('THROWS rather than returning what it managed', async () => {
		/* The property this whole reader exists for. A short answer is indistinguishable from a small
		 * library, and the failure that must not happen is somebody pressing "Select all 9,000",
		 * being handed four thousand, and deleting them believing it was everything. */
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
