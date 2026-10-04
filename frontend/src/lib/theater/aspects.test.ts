import { describe, expect, it } from 'vitest';
import { ASPECTS, DEFAULT_ASPECT, aspectLabel, isAspect, ratioOf } from './aspects';

/*
 * The shapes a cell can be held to.
 *
 * Small, and worth having anyway: everything here is a fallback, and a fallback is the code that
 * only runs on the day something is wrong. The interesting cases are all "a word this version does
 * not know" (a wall saved by a later Sift, a row edited by hand), which is exactly the shape
 * nobody exercises by using the app.
 */

describe('what a shape means', () => {
	it('gives Dynamic no ratio at all, because there is no number to give', () => {
		// Null is the vocabulary the wall's own arithmetic already speaks (see `Aspect` in `fit.ts`),
		// so the shape of a cell and the shape of a file are one answer rather than two.
		expect(ratioOf('dynamic')).toBeNull();
	});

	it('gives every other shape the ratio its name says', () => {
		expect(ratioOf('wide')).toBeCloseTo(16 / 9);
		expect(ratioOf('tall')).toBeCloseTo(9 / 16);
		expect(ratioOf('square')).toBe(1);
	});

	it('opens on the wall Sift shipped with', () => {
		expect(DEFAULT_ASPECT).toBe('dynamic');
		expect(ASPECTS[0].id).toBe('dynamic');
	});
});

describe('a word out of the database', () => {
	it('is taken when this version knows it', () => {
		for (const one of ASPECTS) expect(isAspect(one.id)).toBe(true);
	});

	it('is refused when it is not one of ours', () => {
		// A wall saved by a later version, or a row edited by hand. Both reach `Cell.adopt`, which
		// falls back to Dynamic: the shape a wall saved with no shape is drawn in.
		expect(isAspect('anamorphic')).toBe(false);
		expect(isAspect('')).toBe(false);
		expect(isAspect(null)).toBe(false);
		expect(isAspect(16 / 9)).toBe(false);
	});

	it('falls back to a name rather than showing an empty label', () => {
		expect(aspectLabel('anamorphic' as never)).toBe('Dynamic');
	});
});
