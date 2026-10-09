/*
 * How long a run rests on a photograph (two seconds, if photos are included) or a GIF (once
 * through).
 */

import { fetchSettingValues, onSettingsSaved, saveSettings } from '$lib/settings-ui/settings';
import { DEFAULT_LOOP_MODE, isLoopMode, loopModeAt, type LoopMode } from './loop-modes';

const DWELL_PICTURES_KEY = 'playback.dwell_pictures';

/* Held here for every player. */
const LOOP_MODE_KEY = 'playback.loop_mode';

export const PICTURE_SECONDS = 2;

export const ANIMATION_LOOPS = 1;

const PICTURES_BY_DEFAULT = true;

export function restFor(
	mediaType: string,
	durationMs: number | null | undefined,
	{ pictures }: { pictures: boolean }
): number | null {
	if (mediaType === 'video') return null;
	const length = durationMs ?? 0;
	if (mediaType === 'gif' && length > 0) return length * ANIMATION_LOOPS;
	return pictures ? PICTURE_SECONDS * 1000 : null;
}

class Dwell {
	pictures = $state(PICTURES_BY_DEFAULT);

	/** The account's answer; a picture moves on by the clip's rule (`run.movesOnAfter`). */
	mode = $state<LoopMode>(DEFAULT_LOOP_MODE);

	get playThrough(): boolean {
		return this.mode === 'loop_all';
	}

	/** Under Shuffle, the end of the shuffled list (`playOn`'s `wraps`). */
	get stopsAtTheEnd(): boolean {
		return this.mode === 'once';
	}

	known = $state(false);

	async load(): Promise<void> {
		if (this.known) return;
		try {
			const values = await fetchSettingValues();
			this.pictures = values.get(DWELL_PICTURES_KEY) === true;
			this.repeats(values.get(LOOP_MODE_KEY));
		} catch {
			// A preference that could not be read is its default.
		} finally {
			this.known = true;
		}
	}

	follow(saved: Record<string, unknown>): void {
		if (DWELL_PICTURES_KEY in saved) this.pictures = saved[DWELL_PICTURES_KEY] === true;
		if (LOOP_MODE_KEY in saved) this.repeats(saved[LOOP_MODE_KEY]);
	}

	repeats(mode: unknown): void {
		if (isLoopMode(mode)) this.mode = mode;
	}

	/** Shown immediately, saved, and kept for this sitting even if the save fails. */
	async choose(mode: LoopMode): Promise<void> {
		this.mode = mode;
		try {
			await saveSettings({ [LOOP_MODE_KEY]: mode });
		} catch {
			// Kept for this sitting.
		}
	}

	/** False where the place is nobody's answer, so the phone hears that nothing happened. */
	chooseAt(place: number | null): boolean {
		const wanted = loopModeAt(place);
		if (wanted === null) return false;
		if (wanted !== this.mode) void this.choose(wanted);
		return true;
	}
}

export const dwell = new Dwell();

onSettingsSaved((saved) => dwell.follow(saved));
export { DWELL_PICTURES_KEY, LOOP_MODE_KEY };
