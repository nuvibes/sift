import { describe, expect, it } from 'vitest';
import { ICON_NAMES } from '$lib/design/icons';
import { ICON_USES } from './icon-uses';

/* Every icon says what it is for, and says something worth reading. */

describe('every icon is accounted for', () => {
	it('has an entry for each name, and no more', () => {
		const described = Object.keys(ICON_USES).sort();
		expect(described).toEqual([...ICON_NAMES].sort());
	});

	it('is describing a real list, not an empty one', () => {
		// A sweep over nothing passes forever. There are around a hundred glyphs; a handful means
		// the list moved and this is reading an empty room.
		expect(ICON_NAMES.length).toBeGreaterThan(80);
	});

	for (const name of ICON_NAMES) {
		it(`${name} says what it means and where it is`, () => {
			const use = ICON_USES[name];

			/* Long enough to be a sentence rather than a restatement of the file name. */
			expect(use.what.length, `${name}: "what" is too short to mean anything`).toBeGreaterThan(3);
			expect(use.where.length, `${name}: "where" is too short to be a place`).toBeGreaterThan(20);

			// Not just the name again with the underscores taken out, which is a description that
			// tells a reader exactly what they could already see.
			const spelled = name.replaceAll('_', ' ');
			expect(use.what.toLowerCase(), `${name}: "what" is only the name again`).not.toBe(spelled);

			// A sentence, because these are read one after another down a long list and a column of
			// fragments is harder to read than a column of sentences.
			expect(use.where.endsWith('.'), `${name}: "where" is not a sentence`).toBe(true);
		});
	}
});

describe('the turned face glyph', () => {
	it('says the face is turned, never that Sift only asks about it', () => {
		/* A turned face is named as any other face is, so a gallery line saying Sift only asks
		   about it describes a rule the matcher does not have. */
		expect(ICON_USES.face_left.what).toBe('Turned away from the camera');
		expect(`${ICON_USES.face_left.what} ${ICON_USES.face_left.where}`).not.toMatch(/only asks/);
	});
});
