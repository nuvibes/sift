/*
 * HOW LOUD SIFT IS: one level for every picture in the window, on the ACCOUNT, written once a
 * gesture ends. Which picture is audible is a separate question (`Wall.setMuted`, `Cell.muted`).
 */

import { onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';

const VOLUME_KEY = 'playback.volume';

const SAVE_MS = 400;

function clamped(level: number): number {
	return Math.min(100, Math.max(0, Math.round(level)));
}

class Loudness {
	/**
	 * Whole percent, so a float that reads back as 0.30000000000000004 is not a write per frame.
	 */
	level = $state(100);

	#saved = 100;
	#timer: ReturnType<typeof setTimeout> | null = null;
	/**
	 * A level from the account, not written back. It does not read for itself: the player and the
	 * wall fetch the settings already and hand it over.
	 */
	heard(value: unknown): void {
		const level = Number(value);
		if (!Number.isFinite(level)) return;
		this.level = clamped(level);
		this.#saved = this.level;
	}

	set(level: number): void {
		this.level = clamped(level);
		this.#remember();
	}

	/** Flushed by `settle` too: the commonest level is the one set just before a player closes. */
	#remember(): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = setTimeout(() => {
			this.#timer = null;
			this.#write();
		}, SAVE_MS);
	}

	settle(): void {
		if (!this.#timer) return;
		clearTimeout(this.#timer);
		this.#timer = null;
		this.#write();
	}

	#write(): void {
		if (this.level === this.#saved) return;
		const level = this.level;
		this.#saved = level;
		void saveSettings({ [VOLUME_KEY]: level }).catch(() => {
			// Kept for this sitting even if it could not be saved.
		});
	}
}

export const loudness = new Loudness();

/* A level set elsewhere, another browser included, takes effect here too. */
onSettingsSaved((saved) => {
	if (VOLUME_KEY in saved) loudness.heard(saved[VOLUME_KEY]);
});
