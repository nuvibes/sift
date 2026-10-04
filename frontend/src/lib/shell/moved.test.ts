import { describe, expect, it } from 'vitest';

import { movedAddress } from './moved';

describe('a renamed top-level address', () => {
	it('leads to the word that answers for it now', () => {
		// `/platforms` is the old word for Sites: a bookmark to it must reach the same screen, not
		// the page drawn for an address this application has never heard of.
		expect(movedAddress('platforms')).toBe('sites');
	});

	it('is null where nothing moved, so a page that redirects does not invent a destination', () => {
		expect(movedAddress('sites')).toBeNull();
		expect(movedAddress('people')).toBeNull();
		// An inherited name on a plain object is not a moved address.
		expect(movedAddress('toString')).toBeNull();
	});
});
