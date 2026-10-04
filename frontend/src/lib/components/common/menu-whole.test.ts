/*
 * A chooser's side comes from the room around its press, never from how many rows it holds.
 */
import { describe, expect, it } from 'vitest';

import { chooserSide } from './menu-whole';

const room = (top: number, ceiling = 480) => ({
	top,
	bottom: top + 32,
	window: 1000,
	chrome: 0,
	ceiling
});

describe('chooserSide', () => {
	it('opens below when the room below holds the whole ceiling', () => {
		expect(chooserSide(room(400))).toBe('bottom');
	});

	it('opens above when the room below is short and there is more above', () => {
		expect(chooserSide(room(785))).toBe('top');
	});

	it('stays below when the room below is short but still the larger side', () => {
		expect(chooserSide(room(300))).toBe('bottom');
	});

	it("counts the window's title strip out of the room above", () => {
		expect(chooserSide({ ...room(500), chrome: 60 })).toBe('bottom');
		expect(chooserSide({ ...room(500), chrome: 0 })).toBe('top');
	});

	it('compares the two sides when the ceiling cannot be read', () => {
		expect(chooserSide(room(785, Infinity))).toBe('top');
		expect(chooserSide(room(100, Infinity))).toBe('bottom');
	});
});
