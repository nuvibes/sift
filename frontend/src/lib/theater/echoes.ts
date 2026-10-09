/* What a key just did, in the corner of the cell it did it to: the press is otherwise invisible on
 * a wall. The count lets a badge show again for a press that changed nothing. */
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

/** Note one echo against every cell it happened to, by the cell's key, bumping each one's count. */
export function noteEcho(
	into: Record<string, CellEcho>,
	keys: readonly string[],
	echo: Echo
): void {
	for (const key of keys) {
		into[key] = { ...echo, press: (into[key]?.press ?? 0) + 1 };
	}
}

/** A volume change as the step applied and the level it landed on: "+2 (54)", "+0 (100)". */
export function volumeWords(by: number, total: number): string {
	return `${by > 0 ? '+' : ''}${by} (${total})`;
}

/** A rate as players write one, trailing zeroes off: "0.5x", "2x". */
export function rateWords(rate: number): string {
	return `${Number(rate.toFixed(2))}x`;
}

/* The badge words Theater's wall and the full-size Player share. */

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
