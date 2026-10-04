import { describe, expect, it } from 'vitest';
import { marks } from './search-marks';

/** The pieces as one string, with the marked runs bracketed: easier to read than nested objects. */
const shown = (text: string, needle: string) =>
	marks(text, needle)
		.map((part) => (part.hit ? `[${part.text}]` : part.text))
		.join('');

describe('marking what was typed inside a suggestion', () => {
	it('marks the middle of a name, which is where this list matches', () => {
		/* The whole reason it exists: the dropdown matches anywhere in a name, so a row can be on the
		   list for a reason nowhere near its first letter. */
		expect(shown('Reya Solberg', 'solb')).toBe('Reya [Solb]erg');
	});

	it('keeps the capitals the name already had, rather than the typed ones', () => {
		expect(shown('Reya Solberg', 'reya')).toBe('[Reya] Solberg');
	});

	it('marks every occurrence, not the first', () => {
		/* One marked and one not reads as the second word being the reason the row does NOT match. */
		expect(shown('banana', 'an')).toBe('b[an][an]a');
	});

	it('marks nothing when nothing was typed', () => {
		/* An empty box lists the filters that exist. Marking all of each of them is a list in bold. */
		expect(marks('tags:', '')).toEqual([{ text: 'tags:', hit: false }]);
		expect(marks('tags:', '   ')).toEqual([{ text: 'tags:', hit: false }]);
	});

	it('marks nothing when the row matched on something this does not know about', () => {
		/* The server decides what is on the list, and it knows about handles, aliases and folders
		   this only ever sees the NAME of. A row it cannot explain is drawn plainly rather than
		   dropped, because the server offering it is the answer and this is only the annotation. */
		expect(marks('Hollowgrain', 'redgifs')).toEqual([{ text: 'Hollowgrain', hit: false }]);
	});

	it('marks the whole of it when the whole of it was typed', () => {
		expect(shown('beach', 'beach')).toBe('[beach]');
	});

	it('ignores the space either side of what was typed', () => {
		/* The needle is sliced out of a query at an offset the server gave, so it can arrive with the
		   space that separated it from the token in front. */
		expect(shown('beach party', ' party ')).toBe('beach [party]');
	});
});
