/* What the Browse address asks, and when a search is one bare word that draws the band. */
import { describe, expect, it } from 'vitest';

import { bareWord, questionIn } from './question';

function asked(search: string): Record<string, string> {
	return questionIn(new URLSearchParams(search), ['folders']);
}

describe('the question in the Browse address', () => {
	it('leaves out where the wall stands, both halves of it', () => {
		expect(asked('?q=wren&from=01ABC&near=48&folders=1')).toEqual({ q: 'wren' });
	});

	it('keeps the band for a search come back to, which carries its position', () => {
		/* Back lands on the address the wall left, and the wall had written where it stood. */
		expect(bareWord(asked('?q=wren&from=01ABC&near=0'))).toBe('wren');
	});

	it('draws no band for a word with a filter beside it, a field, or a refusal', () => {
		expect(bareWord(asked('?q=wren&tags=beach'))).toBe('');
		expect(bareWord(asked('?q=tags:beach'))).toBe('');
		expect(bareWord(asked('?q=-wren'))).toBe('');
		expect(bareWord(asked(''))).toBe('');
	});
});
