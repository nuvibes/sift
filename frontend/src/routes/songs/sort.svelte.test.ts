// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Music wall's order, and the one property it exists for: it OUTLIVES the screen. */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const KEY = 'sift.songs.sort';

/** The module as a newly opened screen sees it: imported again, with nothing carried over. */
async function opened() {
	const { songsSort, SONGS_DEFAULT_SORT } = await import('./sort.svelte');
	return { sort: songsSort, fallback: SONGS_DEFAULT_SORT };
}

/* The module graph compiled once, before any test is timed. */
beforeAll(async () => {
	await import('./sort.svelte');
}, 30_000);

beforeEach(() => {
	localStorage.clear();
	// A module imported once is the same instance for ever, which is exactly the thing under test:
	// without this, "opened again" would be the same object and the check would be vacuous.
	vi.resetModules();
});

afterEach(() => {
	localStorage.clear();
});

describe('the order the Music wall is in', () => {
	it('starts at the songs on the most files when nothing has been chosen', async () => {
		const { sort, fallback } = await opened();
		expect(sort.value).toBe(fallback);
		expect(fallback).toBe('largest');
	});

	it('survives the screen being built again', async () => {
		const first = await opened();
		first.sort.set('smallest');
		expect(first.sort.value).toBe('smallest');

		// Opening a song unmounts the wall; coming back builds it from scratch. THIS is the check.
		const again = await opened();
		expect(again.sort.value).toBe('smallest');
	});

	it('ignores an order no wall offers, rather than sorting by it', async () => {
		const { sort, fallback } = await opened();
		sort.set('sideways');
		expect(sort.value).toBe(fallback);
		expect(localStorage.getItem(KEY)).toBeNull();
	});

	it('reads a stale stored key as the default', async () => {
		// An older build's key, or a hand-edited store.
		localStorage.setItem(KEY, 'longest');
		const { sort, fallback } = await opened();
		expect(sort.value).toBe(fallback);
	});

	it('offers the order by artist, and keeps it when chosen', async () => {
		const { SONG_ORDERS } = await import('./sort.svelte');
		expect(SONG_ORDERS.map((one) => one.value)).toContain('artist');
		expect(SONG_ORDERS.find((one) => one.value === 'artist')?.label).toBe('Artist A-Z');
		const first = await opened();
		first.sort.set('artist');
		const again = await opened();
		expect(again.sort.value).toBe('artist');
	});
});
