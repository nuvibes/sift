/*
 * Telling a tap, a run of taps and a held key apart on one key, as a clock tests can drive: press
 * again for the next file, a third time for the one before, hold to change the rate. The hold is
 * shorter than the tap window so a hold answers promptly; auto-repeat (`repeat` set) is ignored.
 */

/** A press, or a run of them, and the key it happened on. */
export interface Run {
	/** The key, spelled exactly as the browser spelled it. */
	key: string;
	/** How many presses the run had reached when this was reported. One-based. */
	count: number;
}

/** What a recogniser tells whoever it is driven by. */
interface Watcher {
	/** A run of taps that ended without any of them being held. */
	tap(run: Run): void;
	/** The press at `count` has been held down past the threshold. */
	hold(run: Run): void;
	/** That held press has been let go. Always follows a `hold` carrying the same run. */
	release(run: Run): void;
}

/** How long a press has to be down before it is a hold rather than a tap. */
export const HOLD_MS = 300;

/** How long after the last release a run of taps waits before it is over. */
export const TAP_MS = 350;

/** The two, where a caller wants something other than the numbers above. Tests do; nothing else. */
interface Timings {
	holdMs?: number;
	tapMs?: number;
}

/** What a key handler drives. Nothing here reads an event: it is told the key and whether it repeated. */
interface TapHold {
	/** A key went down. `repeat` is the browser's own flag, and a true one is ignored. */
	down(key: string, repeat?: boolean): void;
	/** A key came up. */
	up(key: string): void;
	/** Give up what is in progress: a held press is released, a waiting run of taps dropped. */
	cancel(): void;
}

export function tapHold(watcher: Watcher, timings: Timings = {}): TapHold {
	const holdMs = timings.holdMs ?? HOLD_MS;
	const tapMs = timings.tapMs ?? TAP_MS;

	let key: string | null = null;
	let count = 0;
	let down = false;
	let held = false;
	/** The run waiting out its window, so that a flush knows there is one without reading a timer. */
	let pending: Run | null = null;
	let holdTimer: ReturnType<typeof setTimeout> | undefined;
	let tapTimer: ReturnType<typeof setTimeout> | undefined;

	function forget() {
		clearTimeout(holdTimer);
		clearTimeout(tapTimer);
		key = null;
		count = 0;
		down = false;
		held = false;
		pending = null;
	}

	// The state is cleared before the watcher is called: a watcher may press something of its own.
	function settle() {
		clearTimeout(holdTimer);
		clearTimeout(tapTimer);
		if (held && key !== null) {
			const run = { key, count };
			forget();
			watcher.release(run);
			return;
		}
		const waiting = pending;
		forget();
		if (waiting) watcher.tap(waiting);
	}

	return {
		down(pressed, repeat = false) {
			if (repeat) return;
			/* A key already down without the repeat flag is auto-repeat arriving unlabelled. */
			if (down && pressed === key) return;
			if (key !== null && pressed !== key) settle();
			if (key === null) {
				key = pressed;
				count = 0;
			}
			clearTimeout(tapTimer);
			pending = null;
			count += 1;
			down = true;
			held = false;
			const at = count;
			clearTimeout(holdTimer);
			holdTimer = setTimeout(() => {
				held = true;
				watcher.hold({ key: pressed, count: at });
			}, holdMs);
		},

		up(released) {
			if (released !== key || !down) return;
			down = false;
			clearTimeout(holdTimer);
			if (held) {
				settle();
				return;
			}
			pending = { key: released, count };
			tapTimer = setTimeout(() => {
				const run = pending;
				forget();
				if (run) watcher.tap(run);
			}, tapMs);
		},

		cancel() {
			clearTimeout(holdTimer);
			clearTimeout(tapTimer);
			if (held && key !== null) {
				const run = { key, count };
				forget();
				watcher.release(run);
				return;
			}
			forget();
		}
	};
}
