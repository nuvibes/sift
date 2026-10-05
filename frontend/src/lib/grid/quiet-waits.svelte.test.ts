/*
 * A re-read nobody asked for (a change announcement) arriving while a page somebody turned to is
 * still loading must not replace that page with the one they left.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);
const held: (() => void)[] = [];
const asked: number[] = [];

beforeEach(() => {
	vi.clearAllMocks();
	held.length = 0;
	asked.length = 0;
	type Asked = { offset?: number; limit?: number; after?: string };
	mocked.get.mockImplementation((async (_path: string, options?: { query?: Asked }) => {
		const query = options?.query ?? {};
		const offset =
			query.after === undefined ? Number(query.offset ?? 0) : Number(query.after.slice(1)) + 1;
		const limit = Number(query.limit ?? 0);
		asked.push(offset);
		if (offset > 0) await new Promise<void>((release) => held.push(release));
		const items = Array.from({ length: limit }, (_, at) => ({
			id: `a${offset + at}`,
			width: 1920,
			height: 1080
		}));
		return { items, total: 5000, offset, limit };
	}) as never);
});

describe('a quiet re-read during a turn', () => {
	it('waits for the turned page, then re-reads it where it landed', async () => {
		const grid = new Grid();
		grid.containerWidth = 1600;
		grid.screenHeight = 1000;
		await grid.loadAt({}, { at: 0 });
		const next = grid.nextOffset;

		const turning = grid.loadAt({}, { at: next });
		const quiet = grid.loadAt({}, { at: 0 }, { quiet: true });
		await quiet;
		while (held.length > 0) {
			held.shift()?.();
			await new Promise((resolve) => setTimeout(resolve, 0));
		}
		await turning;
		while (held.length > 0) {
			held.shift()?.();
			await new Promise((resolve) => setTimeout(resolve, 0));
		}

		expect(grid.offset, 'the re-read put back the page that was left').toBe(next);
		expect(grid.items[0]?.id).toBe(`a${next}`);
		expect(asked.filter((offset) => offset === next).length, 'the change was never re-read').toBe(
			2
		);
	});
});
