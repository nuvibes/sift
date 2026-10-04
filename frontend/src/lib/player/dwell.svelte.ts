/* How long a run rests on something that has no end of its own.
 *
 * A video ends and the run moves on. A photograph never ends, and a GIF ends several times a
 * second, so both need an answer, and only one of them is a preference:
 *
 *   - **A photograph** is held for two seconds while the account's "Include photos on a
 *     playthrough" is on, which it is by default, so a run through a folder shows every picture.
 *     Off, the run stops on a photograph.
 *   - **A GIF** is played through once and then moves on, always. It has a length of its own, so
 *     there is nothing to decide.
 *   - **A video** plays to its end, which is what the player already does.
 *
 * The count of loops is not read off the picture, because it cannot be: a browser plays a GIF in an
 * `<img>`, which reports nothing about where it is or how many times it has been round. The length
 * recorded when the file was imported is what there is, so once through is that long. A GIF whose
 * length was never recorded is treated as a photograph: the only other option is holding it
 * forever.
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { DEFAULT_LOOP_MODE, isLoopMode, loopModeAt, type LoopMode } from './loop-modes';

const DWELL_PICTURES_KEY = 'playback.dwell_pictures';

/* What happens at the end of a file, which decides whether a run is running at all. HELD HERE for
 * every player, from the same request: the clip player, a picture's bar, the corner panel and the
 * phone all show and press this one answer, so a picture's bar can change the answer that is
 * moving the run off it, and stepping from a picture onto a clip finds the answer it left. */
const LOOP_MODE_KEY = 'playback.loop_mode';

/** How long a photograph is held, in seconds. The help on the setting says the same number. */
export const PICTURE_SECONDS = 2;

/** How many times a GIF is played before the run moves on. */
export const ANIMATION_LOOPS = 1;

/** Whether a run holds photographs before the account's answer arrives. The server's default. */
const PICTURES_BY_DEFAULT = true;

/**
 * How long to hold this file before moving on, in milliseconds, or null to stay on it.
 *
 * Null is the ordinary answer for a photograph, and it means the run stops here, which is what
 * "play through" does today for anybody who has not asked for anything else.
 */
export function restFor(
	mediaType: string,
	durationMs: number | null | undefined,
	{ pictures }: { pictures: boolean }
): number | null {
	// A video ends by itself, and the player moves the run on when it does.
	if (mediaType === 'video') return null;
	const length = durationMs ?? 0;
	if (mediaType === 'gif' && length > 0) return length * ANIMATION_LOOPS;
	// A photograph, and a GIF whose length was never recorded: the only other answer for
	// that one is holding it forever.
	return pictures ? PICTURE_SECONDS * 1000 : null;
}

class Dwell {
	/** Whether a run stops on photographs as well. Read from the account, once. */
	pictures = $state(PICTURES_BY_DEFAULT);

	/**
	 * What happens at the end of a file: Stop at the end, Play through or Repeat this
	 * (`$lib/player/loop-modes`). The account's answer, the one every player reads and writes.
	 *
	 * Whether a picture moves the run on is the same rule a clip's end follows,
	 * `run.movesOnAfter(mode)`, asked of this, never a second copy of it beside the clip's: so a
	 * picture under Repeat this stays where it is, and under Play through it rests and moves on.
	 */
	mode = $state<LoopMode>(DEFAULT_LOOP_MODE);

	/** Play through: the run moves on, and goes round again at the end of the list. */
	get playThrough(): boolean {
		return this.mode === 'loop_all';
	}

	/**
	 * Whether the answer is Stop at the end. Without Shuffle that is the end of the file and the run
	 * stops there; under Shuffle it is the end of the SHUFFLED LIST, so the run moves on until the
	 * list has been walked once and stops there rather than going round again (`run.movesOnAfter`,
	 * `playOn`'s `wraps`).
	 */
	get stopsAtTheEnd(): boolean {
		return this.mode === 'once';
	}

	/** Whether the answers above have arrived. Until they have, nothing is held. */
	known = $state(false);

	async load(): Promise<void> {
		if (this.known) return;
		try {
			const values = await fetchSettingValues();
			this.pictures = values.get(DWELL_PICTURES_KEY) === true;
			this.repeats(values.get(LOOP_MODE_KEY));
		} catch {
			// A preference that could not be read is its default: photographs held, and Play
			// through.
		} finally {
			this.known = true;
		}
	}

	/** Take a change made somewhere else. See `Theme.follow`.
	 *
	 * The only way these ever move after the first read. `load` refuses to run twice on purpose, so
	 * without this a run held whatever the preferences were when the first file opened, for the
	 * whole life of the page, and the player writes both of them from its own bar.
	 */
	follow(saved: Record<string, unknown>): void {
		if (DWELL_PICTURES_KEY in saved) this.pictures = saved[DWELL_PICTURES_KEY] === true;
		if (LOOP_MODE_KEY in saved) this.repeats(saved[LOOP_MODE_KEY]);
	}

	/** The account's answer arrived, or changed somewhere else. Anything that is not one of the three
	 *  answers (an older version's value, or none) leaves the one already held. */
	repeats(mode: unknown): void {
		if (isLoopMode(mode)) this.mode = mode;
	}

	/**
	 * Somebody pressed the control, on whichever player. Shown at once and then saved, so every
	 * other player follows (`follow`), and kept for this sitting even if it could not be saved:
	 * snapping the control back to an answer the person did not choose is worse.
	 */
	async choose(mode: LoopMode): Promise<void> {
		this.mode = mode;
		try {
			await saveSettings({ [LOOP_MODE_KEY]: mode });
		} catch {
			// Kept for this sitting.
		}
	}

	/** The phone names the answer it wants by its place in the order the control cycles through.
	 *  False where the place is nobody's answer, so the phone hears that nothing happened. */
	chooseAt(place: number | null): boolean {
		const wanted = loopModeAt(place);
		if (wanted === null) return false;
		if (wanted !== this.mode) void this.choose(wanted);
		return true;
	}
}

export const dwell = new Dwell();

/* Follow both while the app is open, wherever they were changed. See `theme.svelte`. */
onSettingsSaved((saved) => dwell.follow(saved));
export { DWELL_PICTURES_KEY, LOOP_MODE_KEY };
