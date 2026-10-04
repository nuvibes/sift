/* The root address: a redirect into Browse that keeps what it was asked for. */
import { describe, expect, it } from 'vitest';

import { load } from './+page';

describe('the root address', () => {
	it('sends a bare visit to Browse', () => {
		expect(() => load({ url: new URL('http://sift.local/') })).toThrow();
		try {
			load({ url: new URL('http://sift.local/') });
		} catch (thrown) {
			expect((thrown as { location: string }).location).toBe('/browse');
		}
	});

	it('keeps the query on the way, so a narrowed link into Browse stays narrowed', () => {
		/* "/?enriched=stash" keeps its filter through the redirect. */
		try {
			load({ url: new URL('http://sift.local/?enriched=stash&sort=newest') });
		} catch (thrown) {
			expect((thrown as { location: string }).location).toBe('/browse?enriched=stash&sort=newest');
		}
	});
});
