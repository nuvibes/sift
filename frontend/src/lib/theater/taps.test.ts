/*
 * Tapping and holding one key, told apart.
 *
 * Every case here is one that cannot be tried by hand: the whole thing is two timers, and "press it
 * twice quickly" is not a thing a person can perform reproducibly in front of a wall of nine videos.
 * So the clock is fake and the awkward cases are the point: a hold that starts while a tap window
 * is open, a second key arriving mid-run, a keyup that never comes, and auto-repeat, which is the
 * one that turns a held key into a run of taps if nothing refuses it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HOLD_MS, TAP_MS, tapHold, type Run } from './taps';

/** What a recogniser said, in the order it said it. */
function watching() {
	const said: string[] = [];
	const note =
		(what: string) =>
		(run: Run): void => {
			said.push(`${what} ${run.key} ${run.count}`);
		};
	return {
		said,
		watcher: { tap: note('tap'), hold: note('hold'), release: note('release') }
	};
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('taps', () => {
	it('reports one tap once the window has run out, and not before', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('3');
		keys.up('3');
		vi.advanceTimersByTime(TAP_MS - 1);

		expect(said, 'a run was called over while a second press could still arrive').toEqual([]);

		vi.advanceTimersByTime(1);

		expect(said).toEqual(['tap 3 1']);
	});

	it('counts two presses inside the window as one run of two', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('2');
		keys.up('2');
		vi.advanceTimersByTime(TAP_MS - 50);
		keys.down('2');
		keys.up('2');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 2 2']);
	});

	it('counts three, and reports the run once rather than at every press', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		for (let at = 0; at < 3; at += 1) {
			keys.down('1');
			keys.up('1');
			vi.advanceTimersByTime(50);
		}
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 1 3']);
	});

	it('starts a new run once the window has closed', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('4');
		keys.up('4');
		vi.advanceTimersByTime(TAP_MS);
		keys.down('4');
		keys.up('4');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 4 1', 'tap 4 1']);
	});
});

describe('holds', () => {
	it('reports a hold on the first press, and the release that ends it', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('5');
		vi.advanceTimersByTime(HOLD_MS);

		expect(said).toEqual(['hold 5 1']);

		keys.up('5');

		expect(said).toEqual(['hold 5 1', 'release 5 1']);
	});

	it('knows WHICH press is being held, which is what picks the verb', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('6');
		keys.up('6');
		vi.advanceTimersByTime(50);
		keys.down('6');
		vi.advanceTimersByTime(HOLD_MS);
		keys.up('6');

		expect(said).toEqual(['hold 6 2', 'release 6 2']);
	});

	it('does not also report a tap for the press that was held', () => {
		// Two answers for one press is a cell put into slow motion AND stepped to its next file.
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('7');
		keys.up('7');
		vi.advanceTimersByTime(50);
		keys.down('7');
		vi.advanceTimersByTime(HOLD_MS);
		keys.up('7');
		vi.advanceTimersByTime(TAP_MS * 2);

		expect(said).toEqual(['hold 7 2', 'release 7 2']);
	});

	it('is a tap when the key is let go before the threshold', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('8');
		vi.advanceTimersByTime(HOLD_MS - 1);
		keys.up('8');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 8 1']);
	});
});

describe('the awkward cases', () => {
	it('does not count auto-repeat as more presses', () => {
		/* THE ONE THAT MATTERS MOST. Holding a key makes the operating system send keydown after
		   keydown for as long as it is down; counted, a held key would be a run of forty taps and
		   the hold would never be reached at all. */
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('9');
		for (let at = 0; at < 20; at += 1) {
			vi.advanceTimersByTime(30);
			keys.down('9', true);
		}
		keys.up('9');

		expect(said).toEqual(['hold 9 1', 'release 9 1']);
	});

	it('ignores a keydown for a key it already believes is down', () => {
		// Auto-repeat arriving without its flag. It cannot be a second press: nothing was let go.
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('1');
		keys.down('1');
		keys.down('1');
		vi.advanceTimersByTime(HOLD_MS);
		keys.up('1');

		expect(said).toEqual(['hold 1 1', 'release 1 1']);
	});

	it('ends the run in progress when a different key is pressed', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('2');
		keys.up('2');
		vi.advanceTimersByTime(50);
		keys.down('3');
		keys.up('3');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 2 1', 'tap 3 1']);
	});

	it('releases a held key before answering a different one', () => {
		// The rate a hold took has to be given back, or the cell is left in slow motion by a press
		// that was about a different cell altogether.
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('2');
		vi.advanceTimersByTime(HOLD_MS);
		keys.down('3');
		keys.up('3');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['hold 2 1', 'release 2 1', 'tap 3 1']);
	});

	it('ignores a keyup for a key it is not following', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher);

		keys.down('2');
		vi.advanceTimersByTime(HOLD_MS);
		keys.up('5');

		expect(said).toEqual(['hold 2 1']);
	});

	it('releases a held key when the keyboard goes away, and drops a run of taps', () => {
		/* The two halves are deliberately different. A hold has CHANGED something that only the
		   release undoes, so giving up has to undo it; a run of taps has changed nothing, and acting
		   on it after the focus has left is acting on a press nobody made. */
		const held = watching();
		const keys = tapHold(held.watcher);
		keys.down('4');
		vi.advanceTimersByTime(HOLD_MS);
		keys.cancel();

		expect(held.said).toEqual(['hold 4 1', 'release 4 1']);

		const tapped = watching();
		const more = tapHold(tapped.watcher);
		more.down('4');
		more.up('4');
		more.cancel();
		vi.advanceTimersByTime(TAP_MS * 2);

		expect(tapped.said).toEqual([]);
	});

	it('starts clean after a run it reported, so the next press is a first press', () => {
		/* A recogniser still holding half a run while the watcher does its work would answer the
		   next press as a continuation of one that is already over. */
		const said: string[] = [];
		const keys: { down(key: string): void } = {
			down: () => {}
		};
		const recogniser = tapHold({
			tap: (run) => {
				said.push(`tap ${run.count}`);
				// Exactly what Theater's watcher does: acts, which can take another press with it.
				keys.down('1');
			},
			hold: () => {},
			release: () => {}
		});
		Object.assign(keys, { down: (key: string) => recogniser.down(key) });

		recogniser.down('1');
		recogniser.up('1');
		vi.advanceTimersByTime(TAP_MS);
		recogniser.up('1');
		vi.advanceTimersByTime(TAP_MS);

		expect(said).toEqual(['tap 1', 'tap 1']);
	});

	it('takes timings of its own, which is what every case above is written against', () => {
		const { said, watcher } = watching();
		const keys = tapHold(watcher, { holdMs: 10, tapMs: 20 });

		keys.down('1');
		vi.advanceTimersByTime(10);

		expect(said).toEqual(['hold 1 1']);
	});
});
