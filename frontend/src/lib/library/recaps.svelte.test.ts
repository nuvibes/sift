/* A period's recap is found by the key the server files it under, and the ISO week is the one
 * Python's `isocalendar` gives, at both ends of a year. */

import { describe, expect, it, vi } from 'vitest';

import { periodKey, recapShelf } from './recaps.svelte';

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async () => ({
			recaps: [
				{
					id: 'r1',
					period: 'week:2026-W41',
					title: 'Your week',
					span: '',
					cards: 9,
					made_at: 1,
					seen_at: null
				}
			],
			announced: null
		})),
		post: vi.fn()
	}
}));

describe('periodKey', () => {
	it('names a day, a month and a year by their first day', () => {
		expect(periodKey('day', '2026-10-07')).toBe('day:2026-10-07');
		expect(periodKey('month', '2026-10-01')).toBe('month:2026-10');
		expect(periodKey('year', '2026-01-01')).toBe('year:2026');
	});

	it('names a week by its ISO year and week, across the turn of a year', () => {
		expect(periodKey('week', '2026-10-05')).toBe('week:2026-W41');
		expect(periodKey('week', '2026-12-28')).toBe('week:2026-W53');
		expect(periodKey('week', '2024-12-30')).toBe('week:2025-W01');
		expect(periodKey('week', '2025-12-29')).toBe('week:2026-W01');
	});

	it('has no key for All', () => {
		expect(periodKey('all', '2026-01-01')).toBeNull();
	});
});

it('finds the recap of one period, and none of another', async () => {
	await recapShelf.load();
	expect(recapShelf.of('week:2026-W41')?.id).toBe('r1');
	expect(recapShelf.of('week:2026-W42')).toBeNull();
});
