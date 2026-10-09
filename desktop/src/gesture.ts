/* Whether somebody has just pressed a key or clicked in a window. */

/** How long a press stays good for. The browser's own user-activation window is about this long. */
export const GESTURE_WINDOW_MS = 5000;

/** The input that counts as somebody acting: a press, not a hover, a scroll or a key coming up. */
const PRESSES = new Set(['mouseDown', 'rawKeyDown', 'keyDown', 'gestureTap', 'touchStart']);

/** The one event this needs from a window's contents, named so a test can hand in its own. */
export interface InputSource {
	on(event: 'input-event', listener: (event: unknown, input: { type: string }) => void): unknown;
}

/* On the monotonic clock: a wall clock that is corrected backwards would otherwise make a press
   look as if it had not happened yet. */
const lastPress = new WeakMap<object, number>();

/** Record a press on this window's contents. Exported for the tests; `watchGestures` is what calls it. */
export function notePress(contents: object, at: number = performance.now()): void {
	lastPress.set(contents, at);
}

/** Start noticing presses in one window. Called once, when the window is made. */
export function watchGestures(contents: InputSource & object): void {
	contents.on('input-event', (_event, input) => {
		if (PRESSES.has(input.type)) notePress(contents);
	});
}

/** Whether a press happened recently enough, and if so, use it up. */
export function takeGesture(contents: object, now: number = performance.now()): boolean {
	const at = lastPress.get(contents);
	if (at === undefined || now - at > GESTURE_WINDOW_MS || now < at) return false;
	lastPress.delete(contents);
	return true;
}
