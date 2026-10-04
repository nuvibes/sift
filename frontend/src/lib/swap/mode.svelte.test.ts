/*
 * Swap mode's picks: one list for the window, a pick made again takes it out, leaving the mode
 * abandons the offer, and the server's ceiling is kept here too.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { KEPT_AS, MOST_PICKS, swapMode } from './mode.svelte';

afterEach(() => {
	swapMode.leave();
	swapMode.adopt(null, false);
	sessionStorage.clear();
});

describe('swapMode', () => {
	it('picks, unpicks, and builds what the server is sent', () => {
		swapMode.enter();
		swapMode.toggle({ kind: 'person', id: 'p1', name: 'Ava Example' });
		swapMode.toggle({ kind: 'asset', id: 'a1', name: 'harbor-walk.mp4' });
		expect(swapMode.has('person', 'p1')).toBe(true);
		// The same id as another kind is another thing.
		expect(swapMode.has('site', 'p1')).toBe(false);
		expect(swapMode.chosen()).toEqual([
			{ kind: 'person', id: 'p1' },
			{ kind: 'asset', id: 'a1' }
		]);

		swapMode.toggle({ kind: 'person', id: 'p1', name: 'Ava Example' });
		expect(swapMode.has('person', 'p1')).toBe(false);
		expect(swapMode.chosen()).toEqual([{ kind: 'asset', id: 'a1' }]);
	});

	it('leaving the mode abandons the picks', () => {
		swapMode.enter();
		swapMode.toggle({ kind: 'tag', id: 't1', name: 'beach' });
		swapMode.leave();
		expect(swapMode.on).toBe(false);
		expect(swapMode.picks).toEqual([]);
	});

	it('refuses a pick past the ceiling the server holds a swap to', () => {
		for (let at = 0; at < MOST_PICKS; at++) {
			swapMode.toggle({ kind: 'asset', id: `a${at}`, name: `file ${at}` });
		}
		expect(swapMode.toggle({ kind: 'asset', id: 'one-more', name: 'one more' })).toBe(false);
		expect(swapMode.picks).toHaveLength(MOST_PICKS);
	});

	it('comes back after a full page load, for the admin who made the picks', async () => {
		swapMode.adopt('admin-1', true);
		swapMode.enter();
		swapMode.toggle({ kind: 'person', id: 'p1', name: 'Ava Example' });
		swapMode.toggle({ kind: 'asset', id: 'a1', name: 'harbor-walk.mp4' });

		// A full load runs the module again from nothing; only the tab's storage is left.
		vi.resetModules();
		const fresh = (await import('./mode.svelte')).swapMode;
		expect(fresh.on).toBe(false);
		fresh.adopt('admin-1', true);

		expect(fresh.on).toBe(true);
		expect(fresh.chosen()).toEqual([
			{ kind: 'person', id: 'p1' },
			{ kind: 'asset', id: 'a1' }
		]);
	});

	it('writes each pick down as it is made, and leaving throws it away', () => {
		swapMode.adopt('admin-1', true);
		swapMode.enter();
		swapMode.toggle({ kind: 'tag', id: 't1', name: 'beach' });

		const kept = JSON.parse(sessionStorage.getItem(KEPT_AS) ?? 'null');
		expect(kept).toEqual({ user: 'admin-1', picks: [{ kind: 'tag', id: 't1', name: 'beach' }] });

		swapMode.leave();
		expect(sessionStorage.getItem(KEPT_AS)).toBeNull();
	});

	it('is never taken up by another account or a user who is not an admin', () => {
		const kept = JSON.stringify({
			user: 'admin-1',
			picks: [{ kind: 'person', id: 'p1', name: 'Ava Example' }]
		});
		sessionStorage.setItem(KEPT_AS, kept);
		swapMode.adopt('admin-2', true);
		expect(swapMode.on).toBe(false);
		expect(sessionStorage.getItem(KEPT_AS)).toBeNull();

		sessionStorage.setItem(KEPT_AS, kept);
		swapMode.adopt(null, false);
		swapMode.adopt('admin-1', false);
		expect(swapMode.on).toBe(false);
		expect(swapMode.picks).toEqual([]);
	});

	it('believes nothing in storage that is not a pick', () => {
		sessionStorage.setItem(
			KEPT_AS,
			JSON.stringify({
				user: 'admin-1',
				picks: [
					{ kind: 'filter', id: 'f1', name: 'Saved' },
					{ kind: 'person', id: 7, name: 'Ava Example' },
					{ kind: 'site', id: 's1', name: 'Harbor', extra: 'dropped' }
				]
			})
		);
		swapMode.adopt('admin-1', true);

		expect(swapMode.picks).toEqual([{ kind: 'site', id: 's1', name: 'Harbor' }]);
	});
});
