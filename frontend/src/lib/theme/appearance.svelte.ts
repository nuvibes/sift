/* The drawing preferences that are not the theme, loaded once and shared, so the pane writes
 * through this and a change shows behind the settings sheet. Defaults match the server's. */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { DEFAULT_UNITS, UNITS_KEY, unitSystem, type UnitSystem } from '$lib/shell/measure';
import { CLOCK_KEY, clock } from '$lib/shell/clock.svelte';

export { UNITS_KEY };

class Appearance {
	/* The unit system a measurement is read in; stored as centimetres either way. */
	units = $state<UnitSystem>(DEFAULT_UNITS);
	/** Whether the server has answered yet. The panes wait for it before drawing their switches. */
	loaded = $state(false);

	#loading: Promise<void> | null = null;

	/* Bumped on an account change, so a read in flight for the previous account is dropped. */
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
				// Not worth a message: the default is what a fresh install has.
			} finally {
				// Guarded too, or the pane shows the defaults as this account's choice.
				if (this.#discarded === asked) this.loaded = true;
			}
		})();
		return this.#loading;
	}

	/** Drop what was read on an account change: the desktop page never reloads. */
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

	/** Change the unit system on screen, then on the server; put back if the server refuses. */
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
