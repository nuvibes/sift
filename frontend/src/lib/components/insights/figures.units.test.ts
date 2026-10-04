/* A count on a ranked list says what it counts. */
import { expect, it } from 'vitest';
import { figureWords, saidOf } from './figures';

it('says the noun a count counts, one and many', () => {
	expect(figureWords(13, 'views')).toBe('13 views');
	expect(figureWords(1, 'views')).toBe('1 view');
	expect(figureWords(2, 'times')).toBe('2 times');
	expect(figureWords(3, 'presses')).toBe('3 presses');
	expect(figureWords(1, 'files')).toBe('1 file');
	expect(figureWords(7, 'count')).toBe('7');
});

it("draws the server's words for a figure where it sent some, and the reader's own where not", () => {
	expect(saidOf('41 hours', 148_000_000, 'ms')).toBe('41 hours');
	expect(saidOf('', 2_000_000, 'bytes')).toBe(figureWords(2_000_000, 'bytes'));
	expect(saidOf(undefined, 13, 'views')).toBe('13 views');
});
