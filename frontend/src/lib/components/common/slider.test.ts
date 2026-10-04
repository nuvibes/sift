import { describe, expect, it } from 'vitest';
import { alongTrack, fractionAt } from './slider';

/** The fraction the expression carries, which is the whole of what it says. */
function fractionIn(expression: string): number {
	const found = /\*\s*([0-9.]+)\s*\)$/.exec(expression.trim());
	if (!found) throw new Error(`no fraction in ${expression}`);
	return Number(found[1]);
}

describe('where a moment sits along a track', () => {
	it('leaves room for half a handle at each end', () => {
		// The property this exists for. A native range handle's CENTRE travels from half a thumb in
		// to half a thumb from the end, so anything drawn beside it has to do the same or the two
		// agree at the midpoint and nowhere else.
		expect(alongTrack(0)).toContain('var(--slider-thumb) / 2');
		expect(alongTrack(0)).toContain('100% - var(--slider-thumb)');
	});

	it('carries the fraction it was given', () => {
		expect(fractionIn(alongTrack(0))).toBe(0);
		expect(fractionIn(alongTrack(0.25))).toBe(0.25);
		expect(fractionIn(alongTrack(1))).toBe(1);
	});

	it('refuses to place anything off either end of the track', () => {
		// A moment past the end of a file, or a negative one, is a caller bug or a rounding error,
		// and either way the answer is the end of the track rather than somewhere off it.
		expect(fractionIn(alongTrack(-0.5))).toBe(0);
		expect(fractionIn(alongTrack(1.5))).toBe(1);
	});
});

describe('reading a moment back off the track', () => {
	it('is the inverse of placing one', () => {
		// The property that matters: the frame shown under the pointer has to be the moment a press
		// in the same place seeks to. Six hundred wide, twelve of thumb, so the travel is 588 and
		// the handle's centre at nought sits at 6.
		expect(fractionAt(6, 600, 12)).toBeCloseTo(0);
		expect(fractionAt(300, 600, 12)).toBeCloseTo(0.5);
		expect(fractionAt(594, 600, 12)).toBeCloseTo(1);
	});

	it('reads the ends of the track as the ends of the file', () => {
		// Outside the handle's travel, which is where a press near either edge lands.
		expect(fractionAt(0, 600, 12)).toBe(0);
		expect(fractionAt(600, 600, 12)).toBe(1);
	});

	it('answers nothing rather than dividing by nothing on a track with no width', () => {
		// A timeline measured before it has been laid out, which is what a hover during the first
		// frame gets. Zero is the start of the file, which is a place; a NaN is not.
		expect(fractionAt(50, 12, 12)).toBe(0);
		expect(fractionAt(50, 0, 12)).toBe(0);
	});
});
