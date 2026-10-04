/* The Files wall's address: the old `?account=` word moved to `?username=`, nothing else touched. */
import { describe, expect, it } from 'vitest';

import { load } from './+page';

function whereTo(search: string): string | null {
	try {
		load({ url: new URL(`http://sift.local/browse${search}`) });
	} catch (thrown) {
		return (thrown as { location: string }).location;
	}
	return null;
}

describe('the Files wall address', () => {
	it('moves an old username narrowing to the new word', () => {
		expect(whereTo('?account=ac1')).toBe('/browse?username=ac1');
	});

	it('keeps every other filter in the address as it was', () => {
		expect(whereTo('?account=ac1&tags=beach&sort=newest')).toBe(
			'/browse?tags=beach&sort=newest&username=ac1'
		);
	});

	it('leaves an address that already says username alone', () => {
		expect(whereTo('?username=ac1&tags=beach')).toBeNull();
		expect(whereTo('')).toBeNull();
	});
});
