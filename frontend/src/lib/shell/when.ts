import { clock, type Clock } from '$lib/shell/clock.svelte';
import { counted } from '$lib/entity/entity-counts';

/* When something happened, or is going to, in the words a person reads.
 *
 * ## Why it is one function and not two
 *
 * The Updates screen needs the past half of this ("10 minutes ago", "yesterday") and the
 * Scheduled tasks screen the future half. A second copy written for the future would be the same
 * ladder of thresholds with the words turned round, and the two would disagree the first time
 * one of them was tuned. So there is one ladder, read in whichever direction the moment lies, and
 * `describeLastChecked` reads it too rather than keeping its own.
 *
 * ## The rungs
 *
 * Coarse on purpose, and coarser the further away the moment is. Nobody reading "the next backup"
 * wants it to the second, and a screen that says "in 3 hours" is telling them the thing they came
 * to find out. The exact moment is always available (`exactly`), and every place that shows
 * one of these puts it on the hover, so precision is one gesture away and never in the way.
 *
 * ## The one rule for which form a screen gets
 *
 * A RECORD says when, absolutely, with the day: a file's record, a History line, a finished
 * download, a backup, a stash-box's last answer. "Today 3:04:36 PM", "Yesterday 9:04:12 AM",
 * "Sep 12, 2026, 11:37:05 PM" (`onRecord`). The two named days are the ones a person has words
 * for; past them the date is the word, and the hover gives the whole moment.
 *
 * ## Seconds, and which clock
 *
 * Every time a person reads carries its seconds, because two things that happened in one minute
 * are otherwise told apart by nothing. The Log's lines carry milliseconds as well (`logTime`),
 * because a log is read to put events in order. The one time without seconds is a clock time a
 * setting holds ("HH:MM", `clockTime`), which has none to show. Whether a time is written on the
 * twelve-hour or the twenty-four-hour clock is the reader's choice under Appearance (`clock`).
 *
 * A LIVE LIST says how far away: Activity, a download in flight, the next scheduled run, the
 * update check. "4 minutes ago", "in 2 hours" (`sayWhen`), with `exactly` on the hover,
 * because the question somebody brings to a list that is moving is how long, not what time.
 *
 * ## Why every date in the application is written in this file
 *
 * Screens that write their own disagree: "September 25, 2026" beside "Sep 25, 2026", the
 * browser's bare default ("9/25/2026", which reads as a different date for half the world), a
 * chip hard-wired to American English, and more ladders of relative words ("3m ago", "starts in
 * 2h") saying the same thing differently. `scripts/check_one_time_format.js` refuses a date
 * written anywhere else, so the next screen reaches for these instead of for the browser.
 *
 * `undefined` as the locale everywhere below is deliberate: it means the browser's, which is the
 * reader's, rather than one Sift has decided for them.
 *
 * ## Which zone
 *
 * The SERVER machine's (`clock.zone`, from the session), never the browser's. A library keeps one
 * clock: the History groups a day's filings by the machine's day, and a date opens that day's
 * files, so a phone in another zone that wrote moments in its own would put a filing made at 9 PM
 * under the next day and open a day the library never counted. Every shape below is made in that
 * zone; the browser's own is used only until the session answers, or where the server cannot
 * name its zone. A CALENDAR day (`calendarDay`) and a clock time a setting holds (`clockTime`)
 * have no zone at all, and are written in UTC so no zone can move them.
 */

/** A minute and a half either side of now. Below this, saying a number is noise. */
const JUST_NOW_SECONDS = 90;

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * A moment, relative to now, in words. `at` and `now` are both unix SECONDS; `now` is a parameter
 * so a test can name its moment rather than lean on a clock that may be corrected while it runs.
 */
export function sayWhen(at: number, now: number): string {
	const away = at - now;
	const seconds = Math.abs(away);
	/* "now" would be the obvious word for the forward half and it does not read: these words go
	   after "Next", and "Next now" is a sentence nobody writes. A queued job that is due reads as
	   about to happen, which is what "any moment" says. */
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
	/* Past a day the words are CALENDAR days, the rule the Log's day headings keep: a backup at
	   11 PM two days back is "2 days ago" the next morning, never "yesterday" by a rounded span. */
	const days = Math.max(1, away < 0 ? daysBack(at, now) : daysBack(now, at));
	if (days === 1) return away < 0 ? 'yesterday' : 'tomorrow';
	return away < 0 ? `${days} days ago` : `in ${days} days`;
}

/* ------------------------------------------------------------------------------------------------
 * HOW LONG SOMETHING STILL HAS TO GO, which is the one other kind of time a screen says.
 *
 * An estimate is a window in words ("about 30 to 45 minutes", "under an hour", "a few hours")
 * and never a figure: what is ahead is not what was measured, and "19 minutes" is read as a
 * promise. Both ends are rounded OUT to a step that grows with the wait, so even a single figure
 * comes out as the window it honestly stands for, and "about 2 hours" is said only where the two
 * ends land on the same step. Below its sample an estimate has nothing to say, and says so.
 * `scripts/check_one_estimate_format.js` refuses an estimate worded anywhere else.
 * --------------------------------------------------------------------------------------------- */

/** What an estimate says while there is too little measured to price the rest. */
export const NOT_ENOUGH_TO_SAY = 'Not enough to say yet';

/* The step each end of a window is rounded out to, by how long the slow end is. */
const STEPS: readonly { upTo: number; step: number; unit: number; name: string }[] = [
	{ upTo: 20 * MINUTE, step: 5 * MINUTE, unit: MINUTE, name: 'minute' },
	{ upTo: HOUR, step: 15 * MINUTE, unit: MINUTE, name: 'minute' },
	{ upTo: 6 * HOUR, step: HOUR, unit: HOUR, name: 'hour' },
	{ upTo: 2 * DAY, step: 3 * HOUR, unit: HOUR, name: 'hour' },
	{ upTo: Number.POSITIVE_INFINITY, step: DAY, unit: DAY, name: 'day' }
];

/**
 * An estimate between two bounds, in SECONDS, as a window in words. A single figure is the
 * window with both ends at it. Missing, reversed or negative bounds are `NOT_ENOUGH_TO_SAY`.
 */
export function sayWindow(low: number | null | undefined, high: number | null | undefined): string {
	if (low === null || low === undefined || high === null || high === undefined) {
		return NOT_ENOUGH_TO_SAY;
	}
	if (!Number.isFinite(low) || !Number.isFinite(high) || low < 0 || high < low) {
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

/** The least the work takes, rounded down to its step; under five minutes it is too little to say. */
export function sayAtLeast(seconds: number | null | undefined): string {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
		return NOT_ENOUGH_TO_SAY;
	}
	if (seconds < 5 * MINUTE) return NOT_ENOUGH_TO_SAY;
	const band = STEPS.find((one) => seconds < one.upTo) ?? STEPS[STEPS.length - 1];
	const n = Math.round((Math.floor(seconds / band.step) * band.step) / band.unit);
	return n === 1 ? 'at least an hour' : `at least ${counted(n)} ${band.name}s`;
}

/**
 * A moment KNOWN TO BE PAST (a run that ended, a file that was saved, a check that was made),
 * said as past whatever the clock beside it reads.
 *
 * `sayWhen` reads the sign of the difference, so a past moment handed a `now` that is a little
 * behind it (a row's clock that ticks every half a minute, a server a second ahead of this
 * device, the two moments equal) would come out in the forward half's words: "Last ran any
 * moment" on the Backup pane for half a minute after a backup finished. A thing that has happened
 * is never "any moment"; a moment at or after `now` is "just now".
 */
export function sayAgo(at: number, now: number): string {
	return at >= now ? 'just now' : sayWhen(at, now);
}

/* The shapes, made once for each clock and zone. `Intl.DateTimeFormat` with no locale reads the
   browser's, and building one is the expensive half of formatting, so a History page of fifty
   lines builds none. `hourCycle` rather than `hour12`, because it names the clock exactly: h23
   runs from 0 to 23, which is the twenty-four-hour clock a reader means. `timeZone` is the server
   machine's (undefined is the browser's own, until the session names it). */
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
		/* A clock time a setting holds has no zone: made at that hour in UTC, written in UTC. */
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
		/* The numbers of a moment on the zone's wall clock, for a day's count and a file name. */
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

/**
 * The shapes for the clock the reader chose, in the server machine's zone. Read at the call, so a
 * template redraws on a change of either.
 */
function shapes(): ReturnType<typeof shapesFor> {
	const key = `${clock.hours} ${clock.zone ?? ''}`;
	let made = SHAPES.get(key);
	if (made === undefined) {
		made = shapesFor(clock.hours, readable(clock.zone));
		SHAPES.set(key, made);
	}
	return made;
}

/**
 * The zone if this browser can write in it, else undefined (the browser's own). Asked of `Intl`
 * itself rather than of a list: a zone this browser's data does not know is refused there, and
 * the browser's own zone is then the best there is. Asked once per zone, with the shapes.
 */
function readable(zone: string | undefined): string | undefined {
	if (zone === undefined) return undefined;
	try {
		new Intl.DateTimeFormat(undefined, { timeZone: zone });
		return zone;
	} catch {
		return undefined;
	}
}

/** A calendar day, which has no zone: made at UTC midnight and written in UTC. */
const CALENDAR_DAY = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' });

/** A moment's numbers on the server machine's wall clock: year, month (1 to 12), day, and time. */
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

/** Now, in unix SECONDS: the unit every moment on the wire is in. */
function rightNow(): number {
	return Date.now() / 1000;
}

/**
 * The moment itself: "Sep 25, 2026, 3:04:36 PM". What goes on the hover beside the words above,
 * and what a record says once the moment is further back than yesterday.
 */
export function exactly(at: number): string {
	return shapes().moment.format(at * 1000);
}

/** The time of day alone: "3:04:36 PM". For a line that already sits under its day. */
export function timeOfDay(at: number): string {
	return shapes().time.format(at * 1000);
}

/**
 * A log line's time, to the millisecond: "3:04:36.125 PM". Only the Log's lines are written this
 * way; `at` is unix SECONDS with the fraction kept.
 */
export function logTime(at: number): string {
	return shapes().logTime.format(at * 1000);
}

/**
 * A clock time a setting holds as "HH:MM" (the start of Quiet hours), as the reader writes one.
 * Anything that is not that shape is handed back as it came: it is what is stored, and a wrong
 * value is worth seeing rather than hiding behind a guess.
 */
export function clockTime(value: string): string {
	const parts = value.match(/^(\d{1,2}):(\d{2})$/);
	if (!parts) return value;
	return shapes().clockTime.format(Date.UTC(2000, 0, 1, Number(parts[1]), Number(parts[2])));
}

/** The day a moment fell on, in the server machine's zone: "Sep 25, 2026". Always with the year. */
export function dayOf(at: number): string {
	return shapes().day.format(at * 1000);
}

/**
 * A CALENDAR day a field holds as "YYYY-MM-DD" (a birthdate, a release date), as "Jul 9, 1991".
 *
 * Built from its parts and never parsed out of the string. `new Date('1991-07-09')` is not the
 * ninth of July: the language reads a date-only string as midnight UTC, and formatting that
 * instant in a zone west of Greenwich draws the EIGHTH. A day has no zone, so it is made at UTC
 * midnight and written in UTC, where nothing can move it.
 *
 * Anything else is read as a moment if it can be (and is then the day it fell on, in the server
 * machine's zone), and handed back as it came if it cannot.
 */
export function calendarDay(value: string): string {
	const parts = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
	if (parts) {
		return CALENDAR_DAY.format(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])));
	}
	const parsed = Date.parse(value);
	return Number.isNaN(parsed) ? value : dayOf(parsed / 1000);
}

/**
 * How many calendar days, in the server machine's zone, lie between the day of `at` and the day of
 * `now`. Each day is counted by its date on the zone's wall clock, so a day that holds a clock
 * change (23 or 25 hours long) is still one day.
 */
function daysBack(at: number, now: number): number {
	const dayCount = (seconds: number) => {
		const wall = wallClock(seconds);
		return Date.UTC(wall.year, wall.month - 1, wall.day) / (DAY * 1000);
	};
	return dayCount(now) - dayCount(at);
}

/**
 * The heading a day's worth of records sits under: "Today", "Yesterday", or the day itself.
 *
 * The boundary is the local calendar day and not a span of hours: "yesterday" at one minute past
 * midnight means yesterday.
 */
export function dayHeading(at: number, now: number = rightNow()): string {
	const back = daysBack(at, now);
	if (back === 0) return 'Today';
	if (back === 1) return 'Yesterday';
	return dayOf(at);
}

/**
 * The heading each record in a run opens a new day with, and null for every record that does not:
 * the Log's "Today", "Yesterday", "Sep 12, 2026" over the times of day under them. The run is in
 * whatever order it is drawn in; a new heading is wherever the day changes. A record with no moment
 * heads nothing and does not end the day it sits in.
 */
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

/**
 * A RECORD's moment: "Today 3:04:36 PM", "Yesterday 9:04:12 AM", else `exactly`.
 *
 * `inline` is for the one place the words sit inside a sentence ("asked today 3:04:36 PM"), where
 * a capital in the middle of a line reads as a new sentence starting. A moment on a LATER day (a
 * clock that stepped) is said in full rather than given a name that would be wrong.
 */
export function onRecord(
	at: number,
	{ now = rightNow(), inline = false }: { now?: number; inline?: boolean } = {}
): string {
	const back = daysBack(at, now);
	if (back !== 0 && back !== 1) return exactly(at);
	const day = back === 0 ? 'Today' : 'Yesterday';
	return `${inline ? day.toLowerCase() : day} ${timeOfDay(at)}`;
}

/**
 * A run of records from its first moment to its last: "Today 2:02:10 PM \u2014 6:40:55 PM" where
 * both fall on one day, which is then said once; both ends in full where they do not.
 */
export function span(since: number, at: number, now: number = rightNow()): string {
	if (since >= at) return onRecord(at, { now });
	const end = daysBack(since, at) === 0 ? timeOfDay(at) : onRecord(at, { now });
	return `${onRecord(since, { now })} \u2014 ${end}`;
}

/**
 * A moment as part of a FILE NAME: "YYYY-MM-DD HH-MM-SS", on the server machine's clock like every
 * other time. Not words anybody reads as a date on screen: a name that sorts in the order the
 * things were made, in every locale, with no colon (which a Windows file name cannot hold). Here so
 * that no date is built by hand anywhere else.
 */
export function stampForAFileName(when: Date): string {
	const two = (part: number) => String(part).padStart(2, '0');
	const wall = wallClock(when.getTime() / 1000);
	return (
		`${wall.year}-${two(wall.month)}-${two(wall.day)}` +
		` ${two(wall.hour)}-${two(wall.minute)}-${two(wall.second)}`
	);
}
