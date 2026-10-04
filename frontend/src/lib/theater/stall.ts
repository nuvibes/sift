/*
 * A CELL WHOSE PICTURE HAS STOPPED ARRIVING, and what the wall does about it.
 *
 * ## The hole this closes
 *
 * A cell moves on when its file ENDS or FAILS, and a stalled stream does neither. When the bytes
 * stop coming (a library on a network share that hiccups, a request stuck behind the browser's
 * connection limit, a server read that blocks), the element fires `waiting` (the picture has run
 * out) and `stalled` (the fetch has gone quiet), then sits on its last frame for as long as the
 * wall is open. `ended` never comes and `error` never comes, so nothing in the cell ever learns
 * that anything is wrong. On a wall watched unattended that is a frozen rectangle with no sentence
 * and no way out but a press: a wall that seems to freeze at random.
 *
 * This is the one listener for either event in the client. The ordinary player gets away without
 * one because a person is sitting in front of the one picture; a wall of nine is watched by nobody
 * in particular.
 *
 * ## The two limits, and what they are measured against
 *
 * On a working install the slowest direct stream answers in well under a second and the slowest
 * converted segment in under two. Twelve seconds of no progress is more than six times the worst
 * segment anybody waits for, so a healthy file never trips it, and short enough that a frozen cell
 * recovers before it is noticed.
 *
 * A CONVERTED file gets longer, because `hls.js` has its own patience and its own retry: a piece
 * still being made is allowed thirty seconds (`fragLoadingTimeOut` in `$lib/player/playback`) and a
 * fatal network error is retried by it. The watch steps in only after that has had its chance, and
 * reloading one costs the machine a fresh conversion, which is a reason not to do it early.
 *
 * ## Why a module
 *
 * The clock is the whole of the logic, and a clock is something a test can drive with fake timers
 * where a mounted `<video>` in jsdom cannot be made to stall at all.
 */

import type { PlaybackPlan } from '$lib/player/playback';

/** How long a DIRECT stream may make no progress before the cell acts. See the note above. */
export const STALL_MS = 12_000;

/** The same for a CONVERTED one: past `hls.js`'s own thirty seconds and its own retry. */
export const CONVERTED_STALL_MS = 45_000;

/** What a cell says when it has given up on a file that stopped arriving. */
export const STALLED_WORDS = 'This file stopped arriving from the library.';

/** The limit for one plan: a direct file's, or a converted one's. */
export function stallLimit(plan: Pick<PlaybackPlan, 'route'> | null): number {
	return plan === null || plan.route === 'direct' ? STALL_MS : CONVERTED_STALL_MS;
}

interface StallWatch {
	/** The element says it has run out of picture, or that the fetch has gone quiet. */
	waiting(): void;
	/** The picture moved. Whatever was being waited for has arrived. */
	moving(): void;
	/** Nothing is expected to move: held, ended, emptied, or gone. */
	stop(): void;
}

/**
 * A clock that runs from the first sign of a stall and is stopped by the first sign of progress.
 *
 * A second `waiting` while the clock is running does not restart it: an element that keeps saying
 * it is waiting has been waiting since the first time it said so, and a clock restarted on every
 * report would never run out on exactly the element that reports most.
 */
export function watchForStalls(onstall: () => void, limit: () => number): StallWatch {
	let clock: ReturnType<typeof setTimeout> | null = null;
	const stop = () => {
		if (clock !== null) clearTimeout(clock);
		clock = null;
	};
	return {
		waiting() {
			if (clock !== null) return;
			clock = setTimeout(() => {
				clock = null;
				onstall();
			}, limit());
		},
		moving: stop,
		stop
	};
}
