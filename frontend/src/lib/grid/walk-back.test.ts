import { describe, expect, it } from 'vitest';
import { WalkBack } from './walk-back';

function at(first: string | undefined, offset: number, width = 1200) {
	return {
		items: first === undefined ? [] : [{ id: first }],
		offset,
		nextOffset: offset + 45,
		containerWidth: width,
		rowHeight: 200,
		rowsPerScreen: 4
	};
}

describe('Previous after Next', () => {
	it('returns to each page left, newest first, by its first file', () => {
		const walk = new WalkBack();
		expect(walk.next(at('a0', 0))).toEqual({ at: 45 });
		walk.next(at('a45', 45));

		expect(walk.previous(at('a90', 90), true)).toEqual({ from: 'a45', near: 45 });
		expect(walk.previous(at('a45', 45), true)).toEqual({ from: 'a0', near: 0 });
		expect(walk.previous(at('a0', 0), true)).toEqual({ endingAt: 0 });
	});

	it('returns by position where the list cannot be read from a file', () => {
		const walk = new WalkBack();
		walk.next(at('a12', 12));

		expect(walk.previous(at('a57', 57), false)).toEqual({ at: 12 });
	});

	it('fills backwards after it forgets, after a resize, or from an empty page', () => {
		const walk = new WalkBack();
		walk.next(at('a0', 0));
		walk.forget();
		expect(walk.previous(at('a45', 45), true)).toEqual({ endingAt: 45 });

		walk.next(at('a0', 0));
		expect(walk.previous(at('a45', 45, 900), true)).toEqual({ endingAt: 45 });

		walk.next(at('a0', 0));
		walk.next(at(undefined, 45));
		expect(walk.previous(at('a90', 90), true)).toEqual({ endingAt: 90 });
	});
});
