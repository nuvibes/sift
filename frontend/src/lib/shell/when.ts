import { clock, type Clock } from '$lib/shell/clock.svelte';
import { counted } from '$lib/entity/entity-counts';

/*
 * When something happened, or will, in words: one ladder of thresholds read both ways, coarser the
 * further away. A RECORD says when with the day (`onRecord`); a LIVE LIST says how far (`sayWhen`),
 * with `exactly` on the hover. Every time carries its seconds. Every date in the client is written
 * here (`check_one_time_format.js`), in the SERVER machine's zone; a calendar day and a setting's
 * clock time are written in UTC so no zone moves them.
 */

const JUST_NOW_SECONDS = 90;

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** `at` and `now` in unix SECONDS; `now` is a parameter so a test names its moment. */
export function sayWhen(at: number, now: number): string {
	const away = at - now;
	const seconds = Math.abs(away);
	/* "Next now" reads wrongly; a due job is "any moment". */
	if (seconds < JUST_NOW_SECONDS) return away < 0 ? 'just now' : 'any moment';
	if (seconds < HOUR) {
		const minutes = Math.round(seconds / MINUTE);
		return away < 0 ? `${minutes} minutes ago` : `in ${minutes} minutes`;
	}
	if (seconds < DAY) {
		const hours = Math.round(seconds / HOUR);
		if (hours === 1) return away < 0 ? 'an hour ago' : 'in an hour';
		return away < 0 ? `${hours} hours ago` : `in ${hours} hours`;
	}
	/* Past a day, CALENDAR days, never a rounded span. */
	const days = Math.max(1, away < 0 ? daysBack(at, now) : daysBack(now, at));
	if (days === 1) return away < 0 ? 'yesterday' : 'tomorrow';
	return away < 0 ? `${days} days ago` : `in ${days} days`;
}

/*
 * --- HOW LONG SOMETHING STILL HAS TO GO: a window in words, never a figure, both ends rounded OUT
 * to a step that grows with the wait. `check_one_estimate_format.js` refuses one worded elsewhere.
 */

export const NOT_ENOUGH_TO_SAY = 'Not enough to say yet';

const STEPS: readonly { upTo: number; step: number; unit: number; name: string }[] = [
	{ upTo: 20 * MINUTE, step: 5 * MINUTE, unit: MINUTE, name: 'minute' },
	{ upTo: HOUR, step: 15 * MINUTE, unit: MINUTE, name: 'minute' },
	{ upTo: 6 * HOUR, step: HOUR, unit: HOUR, name: 'hour' },
	{ upTo: 2 * DAY, step: 3 * HOUR, unit: HOUR, name: 'hour' },
	{ upTo: Number.POSITIVE_INFINITY, step: DAY, unit: DAY, name: 'day' }
];

function outOfOrder(low: number, high: number): boolean {
	return !Number.isFinite(low) || !Number.isFinite(high) || low < 0 || high < low;
}

/** In SECONDS; missing, reversed or negative bounds are `NOT_ENOUGH_TO_SAY`. */
export function sayWindow(low: number | null | undefined, high: number | null | undefined): string {
	if (low === null || low === undefined || high === null || high === undefined) {
		return NOT_ENOUGH_TO_SAY;
	}
	if (outOfOrder(low, high)) {
		return NOT_ENOUGH_TO_SAY;
	}
	if (high < MINUTE) return 'under a minute';
	if (high <= 5 * MINUTE) return 'a few minutes';
	const band = STEPS.find((one) => high <= one.upTo) ?? STEPS[STEPS.length - 1];
	const top = Math.ceil(high / band.step) * band.step;
	const bottom = Math.floor(low / band.step) * band.step;
	const count = (seconds: number) => Math.round(seconds / band.unit);
	const units = (n: number) => `${counted(n)} ${band.name}${n === 1 ? '' : 's'}`;
	if (bottom === 0) return top === HOUR ? 'under an hour' : `under ${units(count(top))}`;
	if (top === HOUR) {
		return bottom === HOUR ? 'about an hour' : `about ${counted(count(bottom))} minutes to an hour`;
	}
	if (bottom === top) return `about ${units(count(top))}`;
	if (band.unit === HOUR && bottom >= 2 * HOUR && top <= 5 * HOUR && top - bottom >= 2 * HOUR) {
		return 'a few hours';
	}
	return `about ${counted(count(bottom))} to ${units(count(top))}`;
}

export function sayAtLeast(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
		return NOT_ENOUGH_TO_SAY;
	}
	if (seconds < 5 * MINUTE) return NOT_ENOUGH_TO_SAY;
	const band = STEPS.find((one) => seconds < one.upTo) ?? STEPS[STEPS.length - 1];
	const n = Math.round((Math.floor(seconds / band.step) * band.step) / band.unit);
	return n === 1 ? 'at least an hour' : `at least ${counted(n)} ${band.name}s`;
}

/** A moment KNOWN TO BE PAST, never "any moment" when the clock beside it lags a little. */
export function sayAgo(at: number, now: number): string {
	return at >= now ? 'just now' : sayWhen(at, now);
}

/*
 * Made once per clock and zone: building a formatter is the expensive half. `h23` is the 24-hour
 * clock.
 */
function shapesFor(hours: Clock, timeZone: string | undefined) {
	const hourCycle = hours === '24' ? 'h23' : 'h12';
	return {
		moment: new Intl.DateTimeFormat(undefined, {
			dateStyle: 'medium',
			timeStyle: 'medium',
			hourCycle,
			timeZone
		}),
		time: new Intl.DateTimeFormat(undefined, { timeStyle: 'medium', hourCycle, timeZone }),
		clockTime: new Intl.DateTimeFormat(undefined, {
			timeStyle: 'short',
			hourCycle,
			timeZone: 'UTC'
		}),
		logTime: new Intl.DateTimeFormat(undefined, {
			hour: 'numeric',
			minute: '2-digit',
			second: '2-digit',
			fractionalSecondDigits: 3,
			hourCycle,
			timeZone
		}),
		day: new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone }),
		parts: new Intl.DateTimeFormat('en-US', {
			year: 'numeric',
			month: 'numeric',
			day: 'numeric',
			hour: 'numeric',
			minute: 'numeric',
			second: 'numeric',
			hourCycle: 'h23',
			timeZone
		})
	};
}

const SHAPES = new Map<string, ReturnType<typeof shapesFor>>();

function shapes(): ReturnType<typeof shapesFor> {
	const key = `${clock.hours} ${clock.zone ?? ''}`;
	let made = SHAPES.get(key);
	if (made === undefined) {
		made = shapesFor(clock.hours, readable(clock.zone));
		SHAPES.set(key, made);
	}
	return made;
}

/** The zone if this browser's `Intl` can write in it, else the browser's own. */
function readable(zone: string | undefined): string | undefined {
	if (zone === undefined) return undefined;
	try {
		new Intl.DateTimeFormat(undefined, { timeZone: zone });
		return zone;
	} catch {
		return undefined;
	}
}

const CALENDAR_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' });

function wallClock(at: number): {
	year: number;
	month: number;
	day: number;
	hour: number;
	minute: number;
	second: number;
} {
	const found: Record<string, number> = {};
	for (const part of shapes().parts.formatToParts(at * 1000)) {
		if (part.type !== 'literal') found[part.type] = Number(part.value);
	}
	return {
		year: found.year,
		month: found.month,
		day: found.day,
		hour: found.hour,
		minute: found.minute,
		second: found.second
	};
}

function rightNow(): number {
	return Date.now() / 1000;
}

/** "Sep 25, 2026, 3:04:36 PM": the hover, and a record past yesterday. */
export function exactly(at: number): string {
	return shapes().moment.format(at * 1000);
}

export function timeOfDay(at: number): string {
	return shapes().time.format(at * 1000);
}

/** To the millisecond, for the Log only; `at` keeps its fraction. */
export function logTime(at: number): string {
	return shapes().logTime.format(at * 1000);
}

/** "HH:MM" as the reader writes it; anything else is handed back as stored. */
export function clockTime(value: string): string {
	const parts = value.match(/^(\d{1,2}):(\d{2})$/);
	if (!parts) return value;
	return shapes().clockTime.format(Date.UTC(2000, 0, 1, Number(parts[1]), Number(parts[2])));
}

export function dayOf(at: number): string {
	return shapes().day.format(at * 1000);
}

/**
 * A CALENDAR day ("YYYY-MM-DD") built from its parts, never parsed: `new Date('1991-07-09')` is
 * midnight UTC and draws the eighth west of Greenwich. Anything else is read as a moment.
 */
export function calendarDay(value: string): string {
	const parts = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
	if (parts) {
		return CALENDAR_DAY.format(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])));
	}
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? value : dayOf(parsed / 1000);
}

/** Counted by date on the zone's wall clock, so a day with a clock change is still one day. */
function daysBack(at: number, now: number): number {
	const dayCount = (seconds: number) => {
		const wall = wallClock(seconds);
		return Date.UTC(wall.year, wall.month - 1, wall.day) / (DAY * 1000);
	};
	return dayCount(now) - dayCount(at);
}

/** "Today", "Yesterday", or the day: the calendar day, not a span of hours. */
export function dayHeading(at: number, now: number = rightNow()): string {
	const back = daysBack(at, now);
	if (back === 0) return 'Today';
	if (back === 1) return 'Yesterday';
	return dayOf(at);
}

/** Null for every record that does not start a new day; a record with no moment heads nothing. */
export function dayStarts(
	moments: readonly (number | null)[],
	now: number = rightNow()
): (string | null)[] {
	let current: string | null = null;
	return moments.map((at) => {
		if (at === null) return null;
		const day = dayHeading(at, now);
		if (day === current) return null;
		current = day;
		return day;
	});
}

/** `inline` keeps a capital out of the middle of a sentence; a LATER day is said in full. */
export function onRecord(
	at: number,
	{ now = rightNow(), inline = false }: { now?: number; inline?: boolean } = {}
): string {
	const back = daysBack(at, now);
	if (back !== 0 && back !== 1) return exactly(at);
	const day = back === 0 ? 'Today' : 'Yesterday';
	return `${inline ? day.toLowerCase() : day} ${timeOfDay(at)}`;
}

export function span(since: number, at: number, now: number = rightNow()): string {
	if (since >= at) return onRecord(at, { now });
	const end = daysBack(since, at) === 0 ? timeOfDay(at) : onRecord(at, { now });
	return `${onRecord(since, { now })} \u2014 ${end}`;
}

/** "YYYY-MM-DD HH-MM-SS": sorts in every locale, with no colon for Windows. */
export function stampForAFileName(when: Date): string {
	const two = (part: number) => String(part).padStart(2, '0');
	const wall = wallClock(when.getTime() / 1000);
	return (
		`${wall.year}-${two(wall.month)}-${two(wall.day)}` +
		` ${two(wall.hour)}-${two(wall.minute)}-${two(wall.second)}`
	);
}
