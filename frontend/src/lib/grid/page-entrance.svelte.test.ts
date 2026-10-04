/*
 * What a wall's entrance is keyed on: the page somebody asked for, never the answer's size.
 *
 * Both walls replay a fade and a small rise when their key changes. Keyed on the total, a task
 * running in the background (every library change re-reads the page on screen) would replay it
 * over the same page every time a count moved, which reads as the whole screen flashing.
 */

import type { components } from '$lib/api/schema';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { CardPaging } from './cards.svelte';
import { Grid } from './grid.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

const mocked = vi.mocked(api);

beforeEach(() => vi.clearAllMocks());

type Answer = Pick<components['schemas']['AssetPageResponse'], 'total' | 'offset'> & {
	items: Pick<components['schemas']['AssetSummary'], 'id'>[];
};

function answer(ids: string[], total: number, offset = 0): Answer {
	return { items: ids.map((id) => ({ id })), total, offset };
}

describe('a wall of cards', () => {
	let rows: { id: string }[] = [];

	async function fill(paging: CardPaging, question: string, got: Answer): Promise<void> {
		const page = await paging.fill(
			question,
			() => rows,
			async () => got,
			(one: Answer) => ({ rows: one.items, total: one.total, offset: one.offset })
		);
		if (page) {
			rows = page.rows;
			paging.land(page.offset);
		}
	}

	beforeEach(() => {
		rows = [];
	});

	it('names nothing before the first page has landed', () => {
		expect(new CardPaging(24).showing).toBeNull();
	});

	it('keeps its key when the same page is read again and a count has moved', async () => {
		const paging = new CardPaging(24);
		await fill(paging, 'largest', answer(['s1', 's2'], 2));
		const first = paging.showing;

		await fill(paging, 'largest', answer(['s1', 's2', 's3'], 3));

		expect(first).not.toBeNull();
		expect(paging.showing).toBe(first);
	});

	it('moves its key for a page turned and for a different question', async () => {
		const paging = new CardPaging(2);
		await fill(paging, 'largest', answer(['s1', 's2'], 4));
		const first = paging.showing;

		paging.offset = 2;
		await fill(paging, 'largest', answer(['s3', 's4'], 4, 2));
		const turned = paging.showing;

		paging.offset = 0;
		await fill(paging, 'name_az', answer(['s4', 's3'], 4));

		expect(turned).not.toBe(first);
		expect(paging.showing).not.toBe(turned);
		expect(paging.showing).not.toBe(first);
	});
});

describe('the wall of files', () => {
	function grid(): Grid {
		const one = new Grid();
		one.containerWidth = 1600;
		one.screenHeight = 1000;
		return one;
	}

	function files(ids: string[], total: number, offset = 0) {
		return {
			items: ids.map((id) => ({ id, width: 1920, height: 1080 })),
			total,
			offset,
			limit: ids.length
		};
	}

	it('keeps its key through a catch-up that finds more files and a moved offset', async () => {
		const wall = grid();
		mocked.get.mockResolvedValueOnce(files(['a1', 'a2'], 2) as never);
		await wall.loadAt({ sort: 'newest' }, { at: 0 });
		const first = wall.showing;

		// Anchored on the top file: two arrived above it, so the same page now starts further down.
		mocked.get.mockResolvedValueOnce(files(['a1', 'a2', 'a3'], 5, 2) as never);
		await wall.loadAt({ sort: 'newest' }, { from: 'a1', near: 0 }, { quiet: true });

		expect(first).not.toBeNull();
		expect(wall.offset).toBe(2);
		expect(wall.items.map((one) => one.id)).toEqual(['a1', 'a2', 'a3']);
		expect(wall.showing).toBe(first);
	});

	it('moves its key when somebody asks a different question', async () => {
		const wall = grid();
		mocked.get.mockResolvedValueOnce(files(['a1', 'a2'], 2) as never);
		await wall.loadAt({ sort: 'newest' }, { at: 0 });
		const first = wall.showing;

		mocked.get.mockResolvedValueOnce(files(['a2'], 1) as never);
		await wall.loadAt({ sort: 'newest', q: 'beach' }, { at: 0 });

		expect(wall.showing).not.toBe(first);
	});
});
