/*
 * THE STALL CLOCK: it runs from the first sign of a stall and the first sign of progress stops it.
 *
 * The component half (what a cell DOES when the clock runs out) is in `CellView.svelte.test.ts`.
 * This is the clock alone, on fake timers, because a `<video>` in jsdom cannot be made to stall.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CONVERTED_STALL_MS, STALL_MS, stallLimit, watchForStalls } from './stall';

beforeEach(() => {
	vi.useFakeTimers();
});

afterEach(() => {
	vi.useRealTimers();
});

describe('the stall clock', () => {
	it('runs out once the limit has passed with no progress', () => {
		const onstall = vi.fn();
		const watch = watchForStalls(onstall, () => STALL_MS);

		watch.waiting();
		vi.advanceTimersByTime(STALL_MS - 1);
		expect(onstall, 'the clock ran out early').not.toHaveBeenCalled();
		vi.advanceTimersByTime(1);
		expect(onstall, 'a picture that stopped arriving was never noticed').toHaveBeenCalledTimes(1);
	});

	it('is stopped by the picture moving', () => {
		const onstall = vi.fn();
		const watch = watchForStalls(onstall, () => STALL_MS);

		watch.waiting();
		vi.advanceTimersByTime(STALL_MS / 2);
		watch.moving();
		vi.advanceTimersByTime(STALL_MS * 2);
		expect(
			onstall,
			'a file that recovered on its own was treated as stalled'
		).not.toHaveBeenCalled();
	});

	it('is not restarted by a second report of the same stall', () => {
		/* `waiting` and `stalled` both arrive for one stall, and an element keeps saying it is
		   waiting. A clock restarted on every report would never run out on the one that says so
		   most. */
		const onstall = vi.fn();
		const watch = watchForStalls(onstall, () => STALL_MS);

		watch.waiting();
		vi.advanceTimersByTime(STALL_MS - 100);
		watch.waiting();
		vi.advanceTimersByTime(100);
		expect(onstall).toHaveBeenCalledTimes(1);
	});

	it('gives a converted file longer than the converter is itself allowed', () => {
		expect(stallLimit({ route: 'direct' })).toBe(STALL_MS);
		expect(stallLimit({ route: 'transcode' })).toBe(CONVERTED_STALL_MS);
		expect(stallLimit({ route: 'remux' })).toBe(CONVERTED_STALL_MS);
		// Past hls.js's own thirty seconds for a piece still being made, and its own retry.
		expect(CONVERTED_STALL_MS).toBeGreaterThan(30_000);
	});
});
