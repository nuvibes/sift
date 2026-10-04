/*
 * The preferences about how things are DRAWN that are not the theme, held once for the whole app.
 *
 * One preference: which system a measurement is read in. It is a store rather than a read per
 * screen because the reason for the file is the REQUEST, not the preference: a store per key would
 * be a settings fetch per key on every page load, and each of them would need its own copy of the
 * discard-on-account-change guard below.
 *
 * It is read by every record that draws a height and by the settings pane that changes it, and a
 * preference each of those fetched for itself would be several requests and several answers that
 * drift apart the moment one of them is changed. So it is loaded once and shared, and the pane
 * writes through this rather than around it, which is what makes a change take effect on the
 * screen behind the settings sheet instead of at the next reload.
 *
 * The sharing badge is one of the marks a tile can carry (see `tile-marks.svelte.ts`), not a
 * preference here.
 *
 * Defaults match the server's. Until the answer lands, the app draws what a fresh install draws.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { DEFAULT_UNITS, UNITS_KEY, unitSystem, type UnitSystem } from '$lib/shell/measure';
import { CLOCK_KEY, clock } from '$lib/shell/clock.svelte';

export { UNITS_KEY };

class Appearance {
	/* Which system a measurement is READ in. What is stored is centimetres either way. See
	   `$lib/shell/measure`, which is the one place the arithmetic happens. */
	units = $state<UnitSystem>(DEFAULT_UNITS);
	/** Whether the server has answered yet. The panes wait for it before drawing their switches. */
	loaded = $state(false);

	#loading: Promise<void> | null = null;

	/* How many times these answers have been thrown away. Only ever compared with itself.
	 *
	 * The same guard `Theme` carries, for the same reason: a read that was in the air when the
	 * account changed describes the PREVIOUS account's preferences, and letting it land would show
	 * one person's answers on another person's screen until the second read finished.
	 */
	#discarded = 0;

	/** Read it, once per page load. Repeated calls join the request already in flight. */
	load(): Promise<void> {
		if (this.#loading) return this.#loading;
		const asked = this.#discarded;
		this.#loading = (async () => {
			try {
				const values = await fetchSettingValues();
				if (this.#discarded !== asked) return;
				this.units = unitSystem(values.get(UNITS_KEY));
				clock.take(values.get(CLOCK_KEY));
			} catch {
				// A preference that could not be read is not worth a message. The default above is
				// what a fresh install has, and the screen is usable either way.
			} finally {
				// Guarded like the assignment above: a read discarded halfway must not announce that
				// this account's answers have arrived, or the settings pane draws the defaults as
				// though they were somebody's choice until the real read lands.
				if (this.#discarded === asked) this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Drop what was read, because it belonged to somebody else.
	 *
	 * Called from `account-scoped.ts` when the signed-in account changes, and nowhere else. Without
	 * it the memo above means "read once per PAGE" rather than once per account, and signing out
	 * and back in without a reload (which is what the desktop shell always does, because its page
	 * never reloads) would leave the previous account's answers in place for good.
	 */
	forget(): void {
		this.#discarded += 1;
		this.#loading = null;
		this.units = DEFAULT_UNITS;
		clock.reset();
		this.loaded = false;
	}

	/** Take a change made somewhere else. Only if the batch mentions it (see `Theme.follow`). */
	follow(saved: Record<string, unknown>): void {
		if (UNITS_KEY in saved) this.units = unitSystem(saved[UNITS_KEY]);
	}

	/** Change which system measurements are read in, on the screen first and then on the server.
	 *  Put back if the server refuses. */
	async setUnits(value: UnitSystem): Promise<void> {
		const previous = this.units;
		this.units = value;
		try {
			await saveSettings({ [UNITS_KEY]: value });
		} catch (error) {
			this.units = previous;
			throw error;
		}
	}
}

export const appearance = new Appearance();

/* Follow the preference while the app is open, wherever it was changed. See `theme.svelte`. */
onSettingsSaved((saved) => appearance.follow(saved));
