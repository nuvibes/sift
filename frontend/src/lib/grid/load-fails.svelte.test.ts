/*
 * What a page that could not be read leaves on screen.
 *
 * A page somebody asked for empties the wall and says why. A quiet catch-up that fails leaves the
 * page as it was, because nobody asked for it and a working page is worth more than a dropped poll.
 * A failure a newer question has already overtaken says nothing at all.
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

function page(ids: string[]) {
	return {
		items: ids.map((id) => ({ id, width: 1920, height: 1080 })),
		total: ids.length,
		offset: 0,
		limit: 96,
		complete: true
	};
}

/** A grid showing two files, read the ordinary way. */
async function showing(): Promise<Grid> {
	mocked.get.mockResolvedValueOnce(page(['a', 'b']) as never);
	const grid = measured();
	await grid.loadAt({}, { at: 0 });
	expect(grid.items.map((item) => item.id)).toEqual(['a', 'b']);
	return grid;
}

beforeEach(() => vi.clearAllMocks());

describe('a page that could not be read', () => {
	it('empties the wall and says why, when somebody asked for it', async () => {
		const grid = await showing();
		mocked.get.mockRejectedValue(new Error('Not found.'));

		await grid.loadAt({ q: 'beach' }, { at: 0 });

		expect(grid.items).toEqual([]);
		expect(grid.failed).toBe('Not found.');
		expect(grid.loading).toBe(false);
		expect(grid.reading).toBe(false);
	});

	it('says something plain when what was thrown is not an error', async () => {
		const grid = await showing();
		mocked.get.mockRejectedValue('dropped');

		await grid.loadAt({ q: 'beach' }, { at: 0 });

		expect(grid.failed).toBe('That did not work.');
	});

	it('leaves the page as it was when the catch-up nobody asked for fails', async () => {
		const grid = await showing();
		mocked.get.mockRejectedValue(new Error('Not found.'));

		await grid.loadAt({}, { at: 0 }, { quiet: true });

		expect(grid.items.map((item) => item.id)).toEqual(['a', 'b']);
		expect(grid.failed).toBeNull();
		expect(grid.reading).toBe(false);
	});

	it('says nothing when a newer question has already been answered', async () => {
		const grid = await showing();
		let refuse!: (reason: Error) => void;
		mocked.get.mockImplementationOnce(
			(() => new Promise((_, reject) => (refuse = reject))) as never
		);
		const overtaken = grid.loadAt({ q: 'the first phrase' }, { at: 0 });
		await vi.waitFor(() => expect(refuse).toBeTypeOf('function'));

		mocked.get.mockResolvedValueOnce(page(['c']) as never);
		await grid.loadAt({ q: 'the second phrase' }, { at: 0 });

		refuse(new Error('Not found.'));
		await overtaken;

		expect(grid.items.map((item) => item.id)).toEqual(['c']);
		expect(grid.failed).toBeNull();
	});
});
