/*
 * How long something WAS, in one shape and one set of words: s, min, h, d, two units at most.
 * `duration-gate.test.ts` refuses a second copy. A moment or an estimate is `$lib/shell/when`.
 */

/** A playhead, FLOORED, so it never reads as a second not yet reached. */
export function clock(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	return shape(Math.floor(seconds));
}

/** A length, ROUNDED: a 9.842-second clip is `0:10`, as its record says. */
export function lengthClock(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	return shape(Math.round(seconds));
}

/** Floored like `clock`, except that at the end it reads the rounded length itself. */
export function playheadClock(seconds: number, length: number): string {
	const ended = Number.isFinite(length) && length > 0 && seconds >= length;
	return ended ? lengthClock(length) : clock(seconds);
}

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
 * How long something TOOK, in words; null for what is not a length. Rounded BEFORE it is split, so
 * 59 min 40 s is "1 h", never "60 min".
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

/** A length somebody CHOOSES, spelled out, since a menu row is read as a sentence. */
export function sayLength(seconds: number): string {
	if (seconds < MINUTE || seconds % MINUTE !== 0)
		return seconds === 1 ? '1 second' : `${seconds} seconds`;
	const minutes = seconds / MINUTE;
	return minutes === 1 ? '1 minute' : `${minutes} minutes`;
}

function pair(big: number, bigUnit: string, small: number, smallUnit: string): string {
	return small === 0 ? `${big} ${bigUnit}` : `${big} ${bigUnit} ${small} ${smallUnit}`;
}
