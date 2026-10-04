/* A box's constant said as a word: the client's half of the one rule the server keeps in
 * `records.value_said`. The same cases the server's test holds it to. */

import { expect, it } from 'vitest';
import { constantSaid } from './constant-word';

it('says a constant as the word it stands for, and leaves anything else as it is', () => {
	expect(constantSaid('BLONDE')).toBe('Blonde');
	expect(constantSaid('NON_BINARY')).toBe('Non binary');
	expect(constantSaid('FAKE')).toBe('Fake');
	// Typed in ordinary case, or not a constant's shape: exactly as written.
	expect(constantSaid('Blonde')).toBe('Blonde');
	expect(constantSaid('mp4')).toBe('mp4');
	expect(constantSaid('SITE-1234')).toBe('SITE-1234');
});

it('says a constant that is no word as what it stands for, never "Na"', () => {
	expect(constantSaid('NA')).toBe('Not applicable');
});
