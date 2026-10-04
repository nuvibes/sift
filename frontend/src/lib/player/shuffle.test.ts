/* The one shuffle, which a Theater cell and the player's Shuffle both read. */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { shuffled } from './shuffle';

afterEach(() => {
	vi.restoreAllMocks();
});

describe('shuffling', () => {
	it('keeps everything it was given', () => {
		const ids = ['a', 'b', 'c', 'd', 'e'];
		expect([...shuffled(ids)].sort()).toEqual([...ids].sort());
	});

	it('leaves what it was given alone', () => {
		const ids = ['a', 'b', 'c'];
		shuffled(ids);
		expect(ids).toEqual(['a', 'b', 'c']);
	});

	it('really moves things, rather than handing the list back in its own order', () => {
		// Zero every draw: Fisher-Yates then swaps each slot with the first, which rotates the list.
		// A version that forgot to swap would hand back a-b-c-d unchanged.
		vi.spyOn(Math, 'random').mockReturnValue(0);
		expect(shuffled(['a', 'b', 'c', 'd'])).toEqual(['b', 'c', 'd', 'a']);
	});
});
