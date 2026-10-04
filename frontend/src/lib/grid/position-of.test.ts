/* Where one row sits in a query: the grid asks the page that starts at that row and reads its
 * offset, and answers null for a query that does not hold the row or a request that fails. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return { ...actual, api: { ...actual.api, get: mocks.get } };
});

import { Grid } from './grid.svelte';

beforeEach(() => mocks.get.mockReset());

describe('where a row sits in a query', () => {
	it('reads the offset of the page that starts at the row', async () => {
		mocks.get.mockResolvedValue({ items: [{ id: 'a7' }], total: 9, offset: 6, limit: 1 });
		const grid = new Grid();
		expect(await grid.positionOf({ sort: 'newest' }, 'a7')).toBe(6);
		expect(mocks.get.mock.calls[0][1]).toEqual({ query: { sort: 'newest', from: 'a7', limit: 1 } });
	});

	it('answers null when the query does not hold the row, and when the request fails', async () => {
		mocks.get.mockResolvedValue({ items: [{ id: 'other' }], total: 9, offset: 0, limit: 1 });
		const grid = new Grid();
		expect(await grid.positionOf({}, 'a7')).toBeNull();
		mocks.get.mockImplementationOnce(async () => {
			throw new Error('down');
		});
		expect(await grid.positionOf({}, 'a7')).toBeNull();
	});
});
