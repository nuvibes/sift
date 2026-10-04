import { describe, expect, it } from 'vitest';

import { continuable } from './grid.svelte';

/*
 * Whether a wall may be continued from its last row is the CLIENT's copy of a rule the server
 * enforces with a 422. The grid writes its pin flag as '1' and a typed address may say 'true';
 * a copy reading only one spelling would have every entity wall's first top-up ask to continue a
 * pinned order and be refused: "Some of those details were not valid" under an empty wall.
 */
describe('continuable', () => {
	it('continues a plain wall on a seekable order', () => {
		expect(continuable({ sort: 'newest' })).toBe(true);
		expect(continuable({})).toBe(true);
		expect(continuable({ sort: 'name_az' })).toBe(true);
	});

	it('continues a Random wall from its last row, as the server offers', () => {
		expect(continuable({ sort: 'random' })).toBe(true);
	});

	it('does not continue an order the server cannot seek', () => {
		expect(continuable({ sort: 'relevance' })).toBe(false);
		expect(continuable({ sort: 'similarity' })).toBe(false);
	});

	it('does not continue a wall with a pin in front, however the flag is spelled', () => {
		expect(continuable({ pinned_first: '1' })).toBe(false);
		expect(continuable({ pinned_first: 'true' })).toBe(false);
		expect(continuable({ pinned_first: '0' })).toBe(true);
		expect(continuable({ pinned_first: 'false' })).toBe(true);
	});

	it("does not continue a photo set's own order", () => {
		expect(continuable({ photo_set: 'PS1' })).toBe(false);
	});
});
