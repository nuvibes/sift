/* How long something is, in the one shape and the one set of words a screen uses for it.
 *
 * ## Why one file
 *
 * Copies of an m:ss clock written per screen disagree: one that knows about hours says "1:30:00"
 * for a film and one that does not says "90:00" for the same film. The words for a duration drift
 * the same way ("45 min" beside "3.0 h" and "hr" on one screen). Each copy is right on its own
 * terms and the reader sees one length two ways. So
 * the clock and the words are written here, and `duration-gate.test.ts` refuses a second copy of
 * either anywhere in the client.
 *
 * ## The two kinds of time this is NOT
 *
 * A moment ("Today 3:04:36 PM", "10 minutes ago") is `$lib/shell/when`. An estimate of what is still to
 * go ("about 30 to 45 minutes") is `sayWindow` in `$lib/shell/when` too, and is a window on purpose: what
 * is ahead was not measured. This file is for a length that WAS measured: how long a file runs,
 * where a playhead is, how long a finished run took.
 *
 * ## The words
 *
 * One short form per unit, a space before it: s, min, h, d. "3 min", "2 h 10 min", "3 d 4 h".
 * Never "hr", never "m" for minutes, never a decimal hour ("3.0 h" reads as a measurement nobody
 * took). Two units at most, because the third is noise at that size: nobody reading "3 d 4 h"
 * wants the minutes.
 */

/**
 * A playhead: where in a file somebody is. `4:07`, and `1:02:05` once there is an hour in front.
 * FLOORS, so a position never reads as a second it has not reached yet. Anything that is not a
 * real position (not yet known, negative) is `0:00`.
 */
export function clock(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	return shape(Math.floor(seconds));
}

/**
 * How long a file or a stretch of it RUNS, in the clock's shape. ROUNDS, because a length is a
 * measurement rather than a place: a 9.842-second clip is `0:10`, as its record says. A position
 * beside it is `clock`, which floors.
 */
export function lengthClock(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	return shape(Math.round(seconds));
}

/**
 * A playhead drawn beside the length it moves along (`4:07 / 5:02`): floored like `clock`, except
 * that at the end it reads the length itself. The length beside it rounds, so a finished 301.9 s
 * file would otherwise stop at `5:01 / 5:02`, a second short of the length its record says.
 */
export function playheadClock(seconds: number, length: number): string {
	const ended = Number.isFinite(length) && length > 0 && seconds >= length;
	return ended ? lengthClock(length) : clock(seconds);
}

/* Minutes are padded once there is an hour in front of them and not before, because `0:04:07` is a
   clock face and `4:07` is a length. */
function shape(whole: number): string {
	const hours = Math.floor(whole / 3600);
	const minutes = Math.floor((whole % 3600) / 60);
	const seconds = whole % 60;
	const pad = (value: number) => String(value).padStart(2, '0');
	return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;

/**
 * How long something TOOK, in words: "under a minute", "45 min", "2 h 10 min", "3 d 4 h". Null for
 * something that is not a length (negative, not a number), so a caller draws nothing rather than a
 * confident wrong figure.
 *
 * Rounded to the smaller unit shown, and rounded BEFORE it is split, so 59 min 40 s is "1 h" and
 * never "60 min". Under a minute is said in words rather than seconds: it is the same phrase the
 * estimate formatter uses, so a run that was expected to take under a minute and did reads the
 * same both times.
 */
export function sayDuration(seconds: number): string | null {
	if (!Number.isFinite(seconds) || seconds < 0) return null;
	if (seconds < MINUTE) return 'under a minute';
	const minutes = Math.round(seconds / MINUTE);
	if (minutes < 60) return `${minutes} min`;
	if (minutes < 24 * 60) return pair(Math.floor(minutes / 60), 'h', minutes % 60, 'min');
	const hours = Math.round(seconds / HOUR);
	return pair(Math.floor(hours / 24), 'd', hours % 24, 'h');
}

/**
 * A length somebody CHOOSES, in whole words: "30 seconds", "1 minute", "5 minutes". A choice on a
 * menu is read as a sentence ("After 5 minutes"), so it is spelled out where a length that was
 * measured is abbreviated (`sayDuration`). Whole minutes from a minute up; seconds under one.
 */
export function sayLength(seconds: number): string {
	if (seconds < MINUTE) return seconds === 1 ? '1 second' : `${seconds} seconds`;
	const minutes = Math.round(seconds / MINUTE);
	return minutes === 1 ? '1 minute' : `${minutes} minutes`;
}

/* "2 h 10 min", or "2 h" when the smaller unit comes to nothing: "2 h 0 min" is a figure nobody
   would say. */
function pair(big: number, bigUnit: string, small: number, smallUnit: string): string {
	return small === 0 ? `${big} ${bigUnit}` : `${big} ${bigUnit} ${small} ${smallUnit}`;
}
