/*
 * WHAT A KEY JUST DID, in the corner of the cell it did it to.
 *
 * ## The failure this is for
 *
 * Press R on a wall of nine and the only other sign of which repeat answer is now true is a button
 * inside a drawer that is shut, so without a badge the press reads as nothing happening. Every
 * Theater key has the same problem: two louder moves a slider nobody can see; muting a cell changes
 * a glyph on a bar that is not up; the next file arrives with no word about which file it is.
 *
 * So there is ONE shape for "what a key just did" and every Theater shortcut fills it in. A caller
 * hands the glyph, the words, and, where there is a number worth knowing, the DETAIL: "two
 * louder" is useless without saying two louder than what.
 *
 * ## Why the count is part of it
 *
 * Because a badge has to appear again for a press that changed nothing, and a state it watched
 * itself could never tell that from silence. `KeyEcho` records the argument in full; this carries
 * the count so that it reaches it.
 */
import type { IconName } from '$lib/design/icons';
import { ACTS } from '$lib/player/acts';

/** What a key just did: the glyph, the words, and the number worth knowing. */
export interface Echo {
	/** The glyph for the state that is true now. */
	icon: IconName;
	/** The same in words, which is what a screen reader is given. */
	label: string;
	/** The number or the name behind it: "+2 (54)", "2x", a filename. */
	detail?: string;
	/** Whether this is the OFF answer, drawn dimmer. */
	muted?: boolean;
}

/** One cell's echo, with the count that makes the badge show itself again. */
export interface CellEcho extends Echo {
	press: number;
}

/**
 * Note one echo against every cell it happened to, bumping each cell's own count.
 *
 * By the CELL's key rather than one echo for the wall: a press reaches the addressed cell or all of
 * them, and a single wall-wide record would draw a badge on eight cells the press never touched.
 */
export function noteEcho(
	into: Record<string, CellEcho>,
	keys: readonly string[],
	echo: Echo
): void {
	for (const key of keys) {
		into[key] = { ...echo, press: (into[key]?.press ?? 0) + 1 };
	}
}

/**
 * A volume change, as the step and the level it landed on: "+2 (54)", "-10 (44)".
 *
 * BOTH numbers, because either alone leaves the question half answered. The step is what the key
 * did and is the only thing that says which of the four volume keys was pressed; the level is where
 * that left it, which is what somebody is actually aiming at. The step is the one that was applied
 * rather than the one that was asked for, so a press against the top of the range says "+0 (100)"
 * instead of claiming a rise that did not happen.
 */
export function volumeWords(by: number, total: number): string {
	return `${by > 0 ? '+' : ''}${by} (${total})`;
}

/**
 * A rate, the way every player writes one: "0.5x", "2x".
 *
 * Trailing zeroes off, so a doubled rate is not "2.00x". `Number(...)` on the fixed string is what
 * drops them, and two places is enough for every rate this screen can be put in.
 */
export function rateWords(rate: number): string {
	return `${Number(rate.toFixed(2))}x`;
}

/*
 * A step key shows no filename: the file it names is already on screen at the size of the cell. So
 * there is nothing to cut a filename down for, and no helper kept for a caller that may come back:
 * a helper nobody calls is a helper nobody can delete later.
 *
 * `detail` stays on `Echo` and is what the volume and the rate keys fill in: those are numbers
 * with nowhere else to appear, which is the case the field is for.
 */

/*
 * THE WORDS A KEY'S BADGE SAYS, written once for every player that draws one.
 *
 * Theater's wall and the full-size Player answer the same keys, and a volume key that said
 * "+2 (54)" on a wall and nothing in the Player would be one press read two ways. So the shapes the
 * two share are built here, and both callers raise the same badge (`KeyEcho`) from them.
 */

/** A volume key: where the level landed, and the step that was applied to get there. */
export function volumeEcho(before: number, now: number): Echo {
	return {
		icon: now === 0 ? 'volume_off' : 'volume_up',
		label: 'Volume',
		detail: volumeWords(now - before, now),
		muted: now === 0
	};
}

/** The mute key, as the state it left. */
export function muteEcho(silent: boolean): Echo {
	return {
		icon: silent ? 'volume_off' : 'volume_up',
		label: silent ? 'Muted' : 'Sound on',
		muted: silent
	};
}

/** Play and pause, as the state the press left. */
export function playEcho(paused: boolean): Echo {
	return { icon: paused ? 'pause' : 'play_arrow', label: paused ? 'Stopped' : 'Playing' };
}

/** The seek keys: which way, and how far. */
export function skipEcho(by: number): Echo {
	return {
		icon: by < 0 ? 'replay_5' : 'forward_5',
		label: by < 0 ? 'Back' : 'On',
		detail: `${by > 0 ? '+' : ''}${by}s`
	};
}

/** Full screen, as what the press asked for. */
export function fillEcho(filling: boolean): Echo {
	return filling
		? { icon: 'fullscreen', label: ACTS.fullScreen }
		: { icon: 'fullscreen_exit', label: ACTS.leaveFullScreen };
}
