/*
 * Telling a tap, a run of taps and a held key apart, on one key.
 *
 * ## What this is for
 *
 * Theater's number keys address a cell: 3 means "talk to cell three". That is one verb on nine keys
 * and it leaves the most reachable row on the keyboard doing almost nothing, while the acts anybody
 * actually repeats (next file, the one before, and moving through a clip fast) need a modifier
 * and an arrow. The keys the hand is already on now carry all of it: press again for the next file,
 * a third time for the one before, and HOLD a press to change the rate for as long as it is down.
 *
 * ## Why it is a module of its own
 *
 * Because it is a clock, and a clock written inside a key handler is a clock nothing can test. Two
 * timers, four pieces of state and every one of the awkward cases (a second key pressed mid-run, a
 * key let go after the hold has already fired, a keyup that never arrives because the window lost
 * the focus) are decided here, once, against fake timers, rather than being reasoned about in a
 * component where the only way to try them is by hand on a wall of nine videos.
 *
 * ## The two numbers, and what they mean
 *
 * A press becomes a HOLD when it has been down longer than `HOLD_MS` without being let go. A run of
 * taps ENDS when nothing has been pressed for `TAP_MS` after the last release. The hold is the
 * shorter of the two on purpose: a press held past the point where it can still be a tap has already
 * stopped being one, and waiting the full tap window before answering a hold would put a third of a
 * second of nothing between the key going down and the picture changing.
 *
 * ## Auto-repeat is not a second press, and the browser is the one that knows
 *
 * Holding a key down makes the operating system send keydown after keydown, forever. Every one of
 * them arrives with `repeat` set, which is the only honest way to tell them from a finger going up
 * and down fast: the timings overlap, so a clock cannot. They are ignored outright: a held key is
 * ONE press that has not finished yet.
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
	/**
	 * Give up whatever is in progress.
	 *
	 * For the window losing the focus and for the screen going away. A held press is RELEASED first,
	 * because the watcher has changed something (a rate, a direction) that only the release undoes;
	 * a run of taps waiting out its window is simply dropped, because acting on a key after the
	 * keyboard has gone elsewhere is acting on something nobody pressed.
	 */
	cancel(): void;
}

export function tapHold(watcher: Watcher, timings: Timings = {}): TapHold {
	const holdMs = timings.holdMs ?? HOLD_MS;
	const tapMs = timings.tapMs ?? TAP_MS;

	/** The key the run in progress belongs to, or none. */
	let key: string | null = null;
	/** How many presses that run has had. */
	let count = 0;
	/** Whether the key is down right now. */
	let down = false;
	/** Whether `hold` has been reported for the press that is down. */
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

	/*
	 * End the run in progress and report what it was.
	 *
	 * The state is cleared BEFORE the watcher is called, in both branches. A watcher is free to press
	 * something of its own (Theater's does, by stepping a cell), and a recogniser that still had
	 * half a run in it while that ran would answer the next press as a continuation of one that is
	 * already over.
	 */
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
			/* A key we already believe is down, without the repeat flag. It cannot be a second press
			   (nothing has been let go), so it is auto-repeat arriving unlabelled, and counting it
			   would turn one held key into three taps. */
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
