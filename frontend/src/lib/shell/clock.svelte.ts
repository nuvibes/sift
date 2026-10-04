/*
 * Which clock a time of day is written on: twelve-hour with AM and PM, or twenty-four-hour; and
 * which zone every date and time is written in, the server machine's.
 *
 * Held here rather than in `when.ts` because it has to be STATE: every time on screen is a
 * template calling `when.ts`, and a change made on Appearance has to redraw them where they stand,
 * which a plain variable could not do. `when.ts` reads `clock.hours` and nothing else.
 *
 * Read on the way in by the appearance store, with the other preferences about how things are
 * drawn, so it costs no request of its own; that store hands the answer here, and puts the default
 * back when the account changes. A save made anywhere in the app is followed below.
 */

import { onSettingsSaved } from '$lib/settings-ui/settings';
import { session } from '$lib/shell/session.svelte';

/** Where the choice is stored, per account. The server's key (`theming/__init__.py`). */
export const CLOCK_KEY = 'appearance.clock';

/** The two clocks, in the server's own words. */
export type Clock = '12' | '24';

/** What a fresh install writes a time on, and what the server's registration says. */
const DEFAULT_CLOCK: Clock = '12';

/** Anything that is not the twenty-four-hour answer is the default, which is what an unset value means. */
function clockOf(value: unknown): Clock {
	return value === '24' ? '24' : DEFAULT_CLOCK;
}

/** A zone's name as the server sent it, or undefined for anything that is not one. `lib/shell/when.ts`
 *  decides whether this browser can write in it, since that takes a formatter. */
function zoneOf(value: unknown): string | undefined {
	return typeof value === 'string' && value !== '' ? value : undefined;
}

class ClockPreference {
	hours = $state<Clock>(DEFAULT_CLOCK);
	/** A zone named by hand over the session's, which only a test does. */
	#named = $state<{ zone: string | undefined } | null>(null);

	/**
	 * The zone every date and time is written in: the SERVER machine's (the session's `zone`), not
	 * this browser's, so a phone in another zone reads the days the library keeps. Undefined until
	 * the session says, or where the server cannot name its zone: then the browser's own, as it is
	 * for a name this browser cannot write in (`lib/shell/when.ts`).
	 *
	 * Read from the session rather than handed here by it: the session is read by the settings
	 * module this one listens to, and a session that imported this module would close that circle.
	 */
	get zone(): string | undefined {
		return this.#named === null ? zoneOf(session.viewer?.zone) : this.#named.zone;
	}

	/** Name the zone over the session's; undefined goes back to the session's. */
	takeZone(value: unknown): void {
		this.#named = value === undefined ? null : { zone: zoneOf(value) };
	}

	/** The answer read from the server, or from a save. */
	take(value: unknown): void {
		this.hours = clockOf(value);
	}

	/** Back to the default, because the answer held belonged to another account. */
	reset(): void {
		this.hours = DEFAULT_CLOCK;
	}

	/** Take a change made somewhere else, only if the batch mentions it. */
	follow(saved: Record<string, unknown>): void {
		if (CLOCK_KEY in saved) this.take(saved[CLOCK_KEY]);
	}
}

export const clock = new ClockPreference();

onSettingsSaved((saved) => clock.follow(saved));
