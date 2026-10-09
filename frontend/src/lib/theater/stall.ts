/*
 * A cell whose picture has stopped arriving: a stalled stream fires neither `ended` nor `error`, so
 * without a clock a wall watched unattended freezes on its last frame. Twelve seconds is over six
 * times the slowest converted segment; a converted file waits past `hls.js`'s own retry.
 */

import type { PlaybackPlan } from '$lib/player/playback';

/** How long a DIRECT stream may make no progress before the cell acts. See the note above. */
export const STALL_MS = 12_000;

/** The same for a CONVERTED one: past `hls.js`'s own thirty seconds and its own retry. */
export const CONVERTED_STALL_MS = 45_000;

/** What a cell says when it has given up on a file that stopped arriving. */
export const STALLED_WORDS = 'The library stopped sending this file.';

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

/** A clock from the first sign of a stall to the first of progress; a repeat never restarts it. */
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
