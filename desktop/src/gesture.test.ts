/* Presses in a window, as the main process sees them.
 *
 * What is asserted is which input counts, how long it counts for, and that one press buys one read:
 * a page from another computer reads the clipboard only on the strength of these.
 */

import { describe, expect, it } from 'vitest';

import { GESTURE_WINDOW_MS, notePress, takeGesture, watchGestures, type InputSource } from './gesture';

/** A window's contents in the one way this module uses them: an `input-event` to listen to. */
function contents(): InputSource & { fire(type: string): void } {
	let listener: ((event: unknown, input: { type: string }) => void) | null = null;
	return {
		on(_event, listen) {
			listener = listen;
			return this;
		},
		fire(type) {
			listener?.({}, { type });
		}
	};
}

describe('a press', () => {
	it.each(['mouseDown', 'rawKeyDown', 'keyDown', 'gestureTap', 'touchStart'])('counts for %s', (type) => {
		const window = contents();
		watchGestures(window);

		window.fire(type);

		expect(takeGesture(window)).toBe(true);
	});

	/* Hovering, scrolling and letting go are not somebody asking for anything. */
	it.each(['mouseMove', 'mouseWheel', 'mouseUp', 'keyUp', 'mouseEnter'])('does not count for %s', (type) => {
		const window = contents();
		watchGestures(window);

		window.fire(type);

		expect(takeGesture(window)).toBe(false);
	});

	it('is used up by the read it allows', () => {
		const window = {};
		notePress(window, 1000);

		expect(takeGesture(window, 1100)).toBe(true);
		expect(takeGesture(window, 1200)).toBe(false);
	});

	it('lasts the window and no longer', () => {
		const window = {};
		notePress(window, 1000);
		expect(takeGesture(window, 1000 + GESTURE_WINDOW_MS)).toBe(true);

		notePress(window, 1000);
		expect(takeGesture(window, 1001 + GESTURE_WINDOW_MS)).toBe(false);
	});

	/* A window nobody watched has no presses, so a wiring mistake refuses rather than allows. */
	it('is never found in a window nobody watched', () => {
		expect(takeGesture({})).toBe(false);
	});

	it('belongs to the window it happened in', () => {
		const one = {};
		const other = {};
		notePress(one, 1000);

		expect(takeGesture(other, 1100)).toBe(false);
		expect(takeGesture(one, 1100)).toBe(true);
	});
});
