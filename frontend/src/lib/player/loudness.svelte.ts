/* HOW LOUD SIFT IS. One number, for every picture in the window at once.
 *
 * A number per picture, none of them able to see the others (the player's own, a second copy in
 * the corner panel, one per Theater cell), each reading the account's `playback.volume` once
 * when built and writing it back on its own, means turning a cell down leaves the player where it
 * was, a wall opening at whatever the last read said, and a level set on one reaching another
 * only if that one happened to be built afterwards: volumes neither synced nor remembered.
 *
 * So the LEVEL lives here, once, and everything that can make a noise reads it. What stays per
 * picture is which of them is AUDIBLE: a wall is a room of screens with one of them unmuted, and
 * that is a different question from how loud the room is (see `Wall.setMuted` and `Cell.muted`).
 * The master level is one number; who is listening is not.
 *
 * ## Remembered on the ACCOUNT, not in this browser
 *
 * The same row the settings screen writes, so somebody who turns Sift down on the laptop finds it
 * turned down on the desktop. It is handed in by whoever read the preferences (see `heard`) and
 * written back once a gesture: a drag is one decision and a hundred input events, and saving on
 * each would be a hundred writes for one decision.
 */

import { onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';

/** The row this is stored in, which the settings screen and the player share. */
const VOLUME_KEY = 'playback.volume';

/** How long to wait after the last move before writing it down. See the note above about drags. */
const SAVE_MS = 400;

/** A level is a slider position in whole percent, so it is compared for equality before saving. */
function clamped(level: number): number {
	return Math.min(100, Math.max(0, Math.round(level)));
}

class Loudness {
	/**
	 * How loud, in whole percent.
	 *
	 * Whole percent rather than the element's own 0-1 float, because it is a slider position and it
	 * is compared with what the server holds before anything is written: a float that reads back
	 * as 0.30000000000000004 is a write on every frame of a drag.
	 */
	level = $state(100);

	/** What the account currently holds, so a drag that ends where it started writes nothing. */
	#saved = 100;
	#timer: ReturnType<typeof setTimeout> | null = null;
	/**
	 * Take a level that arrived from the account, without writing it back.
	 *
	 * The distinction that matters: `set` is somebody MOVING the slider and is remembered, this is
	 * the account saying what it already holds. Writing back what was just read would be a save on
	 * every page load, and a race with a slider somebody is holding.
	 *
	 * IT DOES NOT READ FOR ITSELF, and that is deliberate. Both surfaces that can make a noise
	 * (the player and a Theater wall) already fetch the whole settings map when they open, for the
	 * loop mode, the mute, the layout and the rest; a read of its own here would be a second request
	 * for a row that is already on its way. So they hand it over, and anything changed afterwards,
	 * anywhere, arrives through `onSettingsSaved` at the foot of this file.
	 */
	heard(value: unknown): void {
		const level = Number(value);
		if (!Number.isFinite(level)) return;
		this.level = clamped(level);
		this.#saved = this.level;
	}

	/** Somebody moved it. Applies at once, everywhere, and is written down once they stop. */
	set(level: number): void {
		this.level = clamped(level);
		this.#remember();
	}

	/**
	 * Write it down, once the moving stops.
	 *
	 * Flushed by `settle` below rather than only by this timer, because the commonest moment for a
	 * level to be set is the last half second before a player closes, and that is exactly the one
	 * somebody expects to have stuck.
	 */
	#remember(): void {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = setTimeout(() => {
			this.#timer = null;
			this.#write();
		}, SAVE_MS);
	}

	/** Write anything still waiting on the timer. For a player on its way out. */
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
			// Kept for this sitting even if it could not be saved. Snapping the slider back to a
			// value nobody chose is worse than a preference that did not persist.
		});
	}
}

export const loudness = new Loudness();

/*
 * A level set somewhere else takes effect here too.
 *
 * The settings screen writes this row, and so does a second browser signed in to the same account.
 * Without this the slider on screen keeps the value it was built with and the change reads as one
 * that did nothing: the settings sheet sits over the very screen it is about to change.
 *
 * At module scope and never unsubscribed, which is what `onSettingsSaved` is for: there is one of
 * these for the life of the page.
 */
onSettingsSaved((saved) => {
	if (VOLUME_KEY in saved) loudness.heard(saved[VOLUME_KEY]);
});
