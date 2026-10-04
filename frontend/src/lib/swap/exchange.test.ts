/*
 * The picks a guest made in swap mode before it pasted an Exchange token wait for the session it
 * joined, are taken up once, and never for another session.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { keepExchangePicks, takeExchangePicks } from './exchange';

afterEach(() => sessionStorage.clear());

describe('the picks kept for an exchange', () => {
	it('are taken up once, by the session they were kept for', () => {
		const picks = [
			{ kind: 'person' as const, id: 'p-ava', name: 'Ava Example' },
			{ kind: 'asset' as const, id: 'a-1', name: 'holiday.mp4' }
		];
		keepExchangePicks('session-1', picks);

		expect(takeExchangePicks('session-2')).toEqual([]);
		expect(takeExchangePicks('session-1')).toEqual(picks);
		// Taken: a reload of the code step starts from the pickers, not the drawer again.
		expect(takeExchangePicks('session-1')).toEqual([]);
	});

	it('believe nothing that is not a pick', () => {
		sessionStorage.setItem(
			'sift.swap-exchange-picks',
			JSON.stringify({
				session: 's',
				picks: [{ kind: 'filter', id: 'f', name: 'x' }, { kind: 'tag', id: 't' }, 'nonsense']
			})
		);
		expect(takeExchangePicks('s')).toEqual([]);
		sessionStorage.setItem('sift.swap-exchange-picks', '{not json');
		expect(takeExchangePicks('s')).toEqual([]);
	});
});
