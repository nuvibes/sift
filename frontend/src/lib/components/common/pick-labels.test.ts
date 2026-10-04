import { describe, expect, it } from 'vitest';

import { startsWithAVerb } from '../../../../scripts/lib/vocabulary.js';
import * as labels from './pick-labels';
import { PARTLY_APPLIED, tagConfirmLabel } from './pick-labels';

/*
 * The three sentences on a pick sheet's finishing button, written once. The empty case matters: the
 * button is unpressable with nothing ticked, but its words are still on screen, and "Add these 0
 * tags" is a number nobody chose against a plural nothing is.
 */

describe('the words on the finishing button', () => {
	it('names no number when nothing is ticked', () => {
		expect(tagConfirmLabel(0)).toBe('Add tags');
	});

	it('is singular for one', () => {
		expect(tagConfirmLabel(1)).toBe('Add this tag');
	});

	it('counts the rest', () => {
		expect(tagConfirmLabel(4)).toBe('Add these 4 tags');
	});

	it('never says zero of anything', () => {
		// The whole failure in one assertion: a "0" in the sentence.
		expect(tagConfirmLabel(0)).not.toContain('0');
	});
});

describe('the words when a tick has been CLEARED', () => {
	/* The sheet shows what the files are already on now, so finishing can mean taking something off.
	   A button that still said "Add to 2 collections" while it was about to remove one would be the
	   one control on the sheet that lies about what it is about to do. */
	it('says the taking-off on its own', () => {
		expect(tagConfirmLabel(0, 1)).toBe('Remove tag');
		expect(tagConfirmLabel(0, 3)).toBe('Remove these 3 tags');
	});

	it('says both halves when there are both', () => {
		expect(tagConfirmLabel(2, 1)).toBe('Add 2, remove 1');
	});

	it('leaves the adding-only sentence exactly where it was', () => {
		// Most callers of this sheet never pass a removal, so the second argument defaults to none
		// and their words must not have moved a character.
		expect(tagConfirmLabel(4)).toBe(tagConfirmLabel(4, 0));
	});

	it('never says zero of anything, with either number', () => {
		for (const label of [tagConfirmLabel]) {
			expect(label(0, 0)).not.toContain('0');
			expect(label(0, 2)).not.toContain('0');
			expect(label(2, 0)).not.toContain('0');
		}
	});

	it('names one act with one verb: Add to join, Remove to leave, on every sheet', () => {
		/*
		 * Leaving a membership is one act on every sheet, and so is joining: Remove and Add. The
		 * verb list is the vocabulary's own, so a button that drifts to another word fails here as
		 * well as on the ratchet.
		 */
		for (const label of [tagConfirmLabel]) {
			for (const [on, off] of [
				[0, 0],
				[1, 0],
				[3, 0],
				[0, 1],
				[0, 3],
				[2, 1]
			]) {
				const said = label(on, off);
				expect(startsWithAVerb(said), said).toBe(true);
				expect(said.split(' ')[0], said).toBe(on === 0 && off > 0 ? 'Remove' : 'Add');
				if (on > 0 && off > 0) expect(said, said).toMatch(/, remove\b/);
			}
		}
	});
});

describe('the sheet words that are kept', () => {
	it('are the tag sheet only: the other places finish on the pick in their Add to lists', () => {
		expect(Object.keys(labels).sort()).toEqual(['PARTLY_APPLIED', 'tagConfirmLabel']);
	});
});

describe('what a half tick says', () => {
	it('says what is already true of part of the selection, not what the mark looks like', () => {
		/*
		 * The half tick's sentence says what pressing means for the tag about to be pressed, not
		 * merely what the mark depicts.
		 */
		expect(PARTLY_APPLIED).toBe('Already applied to some of the selected files');
	});
});
