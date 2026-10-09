/*
 * Which clock and which zone times are written in. STATE, so a change on Appearance redraws every
 * time where it stands; `when.ts` reads it. Filled by the appearance store, no request of its own.
 */

import { onSettingsSaved } from '$lib/settings-ui/settings';
import { session } from '$lib/shell/session.svelte';

/** The server's key (`theming/__init__.py`). */
export const CLOCK_KEY = 'appearance.clock';

export type Clock = '12' | '24';

const DEFAULT_CLOCK: Clock = '12';

function clockOf(value: unknown): Clock {
	return value === '24' ? '24' : DEFAULT_CLOCK;
}

/** `lib/shell/when.ts` decides whether this browser can write in it. */
function zoneOf(value: unknown): string | undefined {
	return typeof value === 'string' && value !== '' ? value : undefined;
}

class ClockPreference {
	hours = $state<Clock>(DEFAULT_CLOCK);
	/** Only a test names one. */
	#named = $state<{ zone: string | undefined } | null>(null);

	/**
	 * The SERVER machine's zone, so a phone elsewhere reads the library's days; the browser's own
	 * until the session says. Read from the session, which cannot import this without a cycle.
	 */
	get zone(): string | undefined {
		return this.#named === null ? zoneOf(session.viewer?.zone) : this.#named.zone;
	}

	takeZone(value: unknown): void {
		this.#named = value === undefined ? null : { zone: zoneOf(value) };
	}

	take(value: unknown): void {
		this.hours = clockOf(value);
	}

	/** Back to the default: the answer held was another account's. */
	reset(): void {
		this.hours = DEFAULT_CLOCK;
	}

	follow(saved: Record<string, unknown>): void {
		if (CLOCK_KEY in saved) this.take(saved[CLOCK_KEY]);
	}
}

export const clock = new ClockPreference();

onSettingsSaved((saved) => clock.follow(saved));
