/*
 * What the screen says after faces are named from a group: who they were added to.
 *
 * "4 faces have been named" says nothing about who, on the one press where the name was typed a
 * moment before and may have landed on an existing person spelled another way. The name comes
 * from the answer, so it is the row's own spelling.
 */
import { describe, expect, it } from 'vitest';

import { namedSaid } from './PileDetail.svelte';
import { wordsOf } from '$lib/components/common/toast-pieces';

const WREN = { id: 'p1', name: 'Wren Halloway' };

describe('the sentence after naming faces', () => {
	it('says how many were added and to whom', () => {
		expect(wordsOf(namedSaid(4, WREN))).toBe('4 faces have been added to Wren Halloway');
		expect(wordsOf(namedSaid(1_204, WREN))).toBe('1,204 faces have been added to Wren Halloway');
		expect(wordsOf(namedSaid(1, WREN))).toBe('That face has been added to Wren Halloway');
		// The person is the way to them, by id.
		expect(namedSaid(4, WREN)).toContainEqual({ text: 'Wren Halloway', kind: 'person', id: 'p1' });
	});

	it('says the count alone where the answer gives no name', () => {
		expect(namedSaid(4, null)).toBe('4 faces have been named');
		expect(namedSaid(1, undefined)).toBe('That face has been named');
	});
});
