/*
 * The sideways strip's state machine, on its own.
 *
 * It is tested here rather than through either strip that uses it, and that is the point of it
 * being a module: jsdom lays nothing out: every box is zero wide and its `ResizeObserver` reports
 * nothing (see `test-setup.ts`), so a test mounting `LooksLikeThis` or `FacesInThis` can never
 * have an arrow on screen to press. A box stood in for is a box whose scroll extents can be stated,
 * which is the only way to check the two answers this owns: whether there is strip each way, and
 * what one nudge is worth.
 *
 * What is NOT here is the scrolling itself or the sideways wheel. Those are `Scroller`'s, and it
 * has its own tests.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { SideScroll } from './side-scroll.svelte';
import { NUDGE_FIRST_MS } from './scroll-nudge';

/** A scrolling box with a run of items in it, at sizes jsdom will not compute. */
function strip({ items = 4, item = 100, wide = 250 } = {}): HTMLElement {
	const box = document.createElement('div');
	const list = document.createElement('ul');
	for (let at = 0; at < items; at += 1) {
		const one = document.createElement('li');
		Object.defineProperty(one, 'offsetWidth', { value: item, configurable: true });
		list.append(one);
	}
	box.append(list);
	Object.defineProperty(box, 'clientWidth', { value: wide, configurable: true });
	Object.defineProperty(box, 'scrollWidth', { value: items * item, configurable: true });
	// jsdom has no scrolling, so the position is a plain writable number on the element.
	box.scrollLeft = 0;
	document.body.append(box);
	return box;
}

let box: HTMLElement;

beforeEach(() => {
	vi.useFakeTimers();
	box = strip();
});

afterEach(() => {
	vi.useRealTimers();
	box.remove();
});

it('offers a way on and no way back at the start of a strip', () => {
	const side = new SideScroll();
	side.take(box);

	side.measure();

	expect(side.canBack).toBe(false);
	expect(side.canOn).toBe(true);
});

it('offers neither when the whole strip fits', () => {
	/* The ordinary case on a wide window, and the one where a pair of arrows would be two controls
	   that do nothing, so each exists only while it is true. */
	const narrow = strip({ items: 2, item: 100, wide: 400 });
	const side = new SideScroll();
	side.take(narrow);

	side.measure();

	expect(side.canBack).toBe(false);
	expect(side.canOn).toBe(false);
	narrow.remove();
});

it('takes half a pixel of slack at the far end', () => {
	/* A scroll position is fractional on a display that is not at 100%, and an arrow that will not
	   go away at the end of a strip reads as broken. */
	const side = new SideScroll();
	side.take(box);
	box.scrollLeft = 400 - 250 - 0.4;

	side.measure();

	expect(side.canOn).toBe(false);
	expect(side.canBack).toBe(true);
});

it('moves by one ITEM, measured off the strip rather than named here', () => {
	/* The items are one size within a strip and a different size between strips: a face crop is
	   square and a lookalike tile is as wide as its file's own shape, so the strip is asked. */
	const side = new SideScroll();
	side.take(box);

	side.nudge(1);

	expect(box.scrollLeft).toBe(100);
});

it('moves by the whole box when there is no item to measure', () => {
	/* A strip whose items have not laid out yet, which is what jsdom is permanently. A nudge of
	   nought would be an arrow that does nothing rather than one that does something approximate. */
	const empty = document.createElement('div');
	Object.defineProperty(empty, 'clientWidth', { value: 250, configurable: true });
	Object.defineProperty(empty, 'scrollWidth', { value: 900, configurable: true });
	empty.scrollLeft = 0;
	const side = new SideScroll();
	side.take(empty);

	side.nudge(1);

	expect(empty.scrollLeft).toBe(250);
});

it('goes on moving while the arrow is held, and stops at the end on its own', () => {
	/* One item on arrival so that a pointer crossing the arrow moves the strip once and no more,
	   then the eased wait: `nudgeDelay`, which is what every arrow in the app accelerates by. */
	const long = strip({ items: 8, item: 100, wide: 250 });
	const side = new SideScroll();
	side.take(long);

	side.nudge(1);
	expect(long.scrollLeft).toBe(100);
	vi.advanceTimersByTime(NUDGE_FIRST_MS);
	expect(long.scrollLeft).toBe(200);

	/* 800 wide in a 250 box, so 550 is the end. The nudge that passes it is the last one: the timer
	   is not set again, and running every remaining timer is what proves it STOPPED rather than
	   merely paused: a held arrow that goes on ringing a timer at the end of a strip is a timer
	   nobody can see. */
	vi.runOnlyPendingTimers();
	vi.runOnlyPendingTimers();
	vi.runOnlyPendingTimers();
	vi.runOnlyPendingTimers();
	expect(long.scrollLeft).toBe(600);
	vi.runOnlyPendingTimers();
	expect(long.scrollLeft).toBe(600);
	long.remove();
});

it('stops a hold when the pointer leaves', () => {
	const side = new SideScroll();
	side.take(box);

	side.nudge(1);
	side.stop();
	vi.runOnlyPendingTimers();

	expect(box.scrollLeft).toBe(100);
});

it('does nothing at all until a viewport has been handed over', () => {
	/* `Scroller` hands its box over after the first paint, so every one of these runs at least once
	   against nothing. None of them may throw, and none may leave a timer behind. */
	const side = new SideScroll();

	side.measure();
	side.nudge(1);
	side.stop();

	expect(side.canBack).toBe(false);
	expect(side.canOn).toBe(false);
	expect(side.watch()).toBeUndefined();
});

it('re-measures when the strip is scrolled, and lets go of it afterwards', () => {
	/* The strip moves under this from three directions: the wheel and the keyboard move it, a
	   different subject is a different number of items, and the window resizes the box. The scroll
	   is the one of the three jsdom can actually deliver. */
	const side = new SideScroll();
	side.take(box);
	const stopWatching = side.watch();

	box.scrollLeft = 120;
	box.dispatchEvent(new Event('scroll'));
	expect(side.canBack).toBe(true);

	stopWatching?.();
	box.scrollLeft = 0;
	box.dispatchEvent(new Event('scroll'));
	expect(side.canBack).toBe(true);
});
