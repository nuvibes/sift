import { afterEach, describe, expect, it } from 'vitest';
import { clock } from './clock.svelte';
import { session, type Viewer } from './session.svelte';
import {
	calendarDay,
	clockTime,
	dayHeading,
	dayOf,
	dayStarts,
	exactly,
	logTime,
	NOT_ENOUGH_TO_SAY,
	onRecord,
	sayAgo,
	sayWhen,
	sayWindow,
	span,
	stampForAFileName,
	timeOfDay
} from './when';

/* The ladder reads the same in both directions, which is the whole reason it is one function.
   Written as pairs on purpose: a threshold that moves has to move for the past and the future
   together, and a test that checked only one half would let them drift. */

const NOW = 1_700_000_000;

describe('sayAgo', () => {
	/* A backup that has just finished must not read "Last ran any moment" for half a minute: the
	   row's clock ticks every thirty seconds, so the moment it ended can be ahead of the "now"
	   it is read against. */
	it('says a past moment as past even when the clock beside it is behind it', () => {
		expect(sayAgo(NOW + 20, NOW)).toBe('just now');
		expect(sayAgo(NOW, NOW)).toBe('just now');
		expect(sayAgo(NOW - 30, NOW)).toBe('just now');
		expect(sayAgo(NOW - 600, NOW)).toBe('10 minutes ago');
	});
});

describe('sayWhen', () => {
	it('says nothing precise about the minute either side of now', () => {
		expect(sayWhen(NOW - 30, NOW)).toBe('just now');
		expect(sayWhen(NOW + 30, NOW)).toBe('any moment');
	});

	it('counts minutes up to an hour', () => {
		expect(sayWhen(NOW - 600, NOW)).toBe('10 minutes ago');
		expect(sayWhen(NOW + 600, NOW)).toBe('in 10 minutes');
	});

	it('names one hour rather than counting it', () => {
		expect(sayWhen(NOW - 3600, NOW)).toBe('an hour ago');
		expect(sayWhen(NOW + 3600, NOW)).toBe('in an hour');
	});

	it('counts hours up to a day', () => {
		expect(sayWhen(NOW - 3 * 3600, NOW)).toBe('3 hours ago');
		expect(sayWhen(NOW + 3 * 3600, NOW)).toBe('in 3 hours');
	});

	it('names one day rather than counting it', () => {
		expect(sayWhen(NOW - 86400, NOW)).toBe('yesterday');
		expect(sayWhen(NOW + 86400, NOW)).toBe('tomorrow');
	});

	it('counts days after that', () => {
		expect(sayWhen(NOW - 5 * 86400, NOW)).toBe('5 days ago');
		expect(sayWhen(NOW + 5 * 86400, NOW)).toBe('in 5 days');
	});
	it('counts calendar days past a day, so 11 PM two days back is not yesterday', () => {
		const morning = new Date(2026, 8, 28, 7, 44).getTime() / 1000;
		const twoBack = new Date(2026, 8, 26, 23, 0).getTime() / 1000;
		const oneBack = new Date(2026, 8, 27, 1, 0).getTime() / 1000;
		expect(sayWhen(twoBack, morning)).toBe('2 days ago');
		expect(sayWhen(oneBack, morning)).toBe('yesterday');
		expect(sayWhen(morning, twoBack)).toBe('in 2 days');
	});
});

describe('exactly', () => {
	it('is a real date and time, so the hover says more than the words do', () => {
		const said = exactly(NOW);
		expect(said).not.toBe('');
		expect(said).toBe(
			new Date(NOW * 1000).toLocaleString(undefined, {
				dateStyle: 'medium',
				timeStyle: 'medium',
				hourCycle: 'h12'
			})
		);
	});
});

/* Every moment below is built from LOCAL parts, so the day each one falls on is the same in every
   zone the suite runs in. The fault these guard against is exactly a day that moves with the
   zone. And each expectation is built from the formatter's own smaller pieces rather than a
   literal "3:04 PM", so the suite does not hard-code one locale's spelling while still pinning the
   SHAPE. (Two cases do assume a locale that writes the day before the time, as English does.) */
const local = (month: number, day: number, hour: number, minute = 0) =>
	new Date(2026, month - 1, day, hour, minute).getTime() / 1000;
const AFTERNOON = local(9, 25, 18);

describe('a record says when with the day', () => {
	it('names today and yesterday, with the time', () => {
		expect(onRecord(local(9, 25, 15, 4), { now: AFTERNOON })).toBe(
			`Today ${timeOfDay(local(9, 25, 15, 4))}`
		);
		expect(onRecord(local(9, 24, 9, 4), { now: AFTERNOON })).toBe(
			`Yesterday ${timeOfDay(local(9, 24, 9, 4))}`
		);
	});

	it('says the whole moment past yesterday', () => {
		const earlier = local(9, 12, 23, 37);
		expect(onRecord(earlier, { now: AFTERNOON })).toBe(exactly(earlier));
		expect(exactly(earlier)).toContain('2026');
	});

	it('keeps one minute past midnight on the day it happened', () => {
		expect(onRecord(local(9, 25, 0, 1), { now: AFTERNOON })).toMatch(/^Today /);
		expect(onRecord(local(9, 24, 23, 59), { now: AFTERNOON })).toMatch(/^Yesterday /);
	});

	it('does not call a later day today: a clock that stepped is said in full', () => {
		const tomorrow = local(9, 26, 9);
		expect(onRecord(tomorrow, { now: AFTERNOON })).toBe(exactly(tomorrow));
	});

	it('lowers the day inside a sentence, and only the day', () => {
		const at = local(9, 25, 15, 4);
		expect(onRecord(at, { now: AFTERNOON, inline: true })).toBe(`today ${timeOfDay(at)}`);
		const earlier = local(9, 12, 23, 37);
		expect(onRecord(earlier, { now: AFTERNOON, inline: true })).toBe(exactly(earlier));
	});
});

describe('a run of records', () => {
	it('says the day once when both ends fall on it', () => {
		const said = span(local(9, 25, 14, 2), local(9, 25, 18, 40), AFTERNOON);
		expect(said).toBe(
			`Today ${timeOfDay(local(9, 25, 14, 2))} \u2014 ${timeOfDay(local(9, 25, 18, 40))}`
		);
	});

	it('says both ends in full across midnight', () => {
		const said = span(local(9, 24, 23, 50), local(9, 25, 0, 10), AFTERNOON);
		expect(said).toBe(
			`Yesterday ${timeOfDay(local(9, 24, 23, 50))} \u2014 Today ${timeOfDay(local(9, 25, 0, 10))}`
		);
	});

	it('is one moment where it does not run forwards', () => {
		const at = local(9, 25, 14);
		expect(span(at, at, AFTERNOON)).toBe(onRecord(at, { now: AFTERNOON }));
	});
});

describe('a day', () => {
	it('heads a day of records with the two words people have, then the date', () => {
		expect(dayHeading(local(9, 25, 9), AFTERNOON)).toBe('Today');
		expect(dayHeading(local(9, 24, 23), AFTERNOON)).toBe('Yesterday');
		expect(dayHeading(local(9, 12, 9), AFTERNOON)).toBe(dayOf(local(9, 12, 9)));
	});

	it('always carries the year, and is the day part of the whole moment', () => {
		const at = local(9, 12, 9);
		expect(dayOf(at)).toContain('2026');
		expect(exactly(at).startsWith(dayOf(at))).toBe(true);
	});

	it('reads a calendar day from its parts, so it is the same day in every zone', () => {
		/* `new Date('1991-07-09')` is midnight UTC: the EIGHTH everywhere west of Greenwich. */
		expect(calendarDay('1991-07-09')).toBe(dayOf(new Date(1991, 6, 9).getTime() / 1000));
		expect(calendarDay('1991-07-09')).toContain('9');
	});

	it('hands back a value that is not a day as it came', () => {
		expect(calendarDay('sometime')).toBe('sometime');
	});

	it('heads a run of records once per day, wherever the day changes', () => {
		/* The Log's lines, oldest first, crossing two midnights: the case a time of day alone
		   read as one evening. A line with no moment heads nothing and does not end its day. */
		const run = [local(9, 12, 9), local(9, 12, 23), local(9, 24, 23), null, local(9, 25, 1)];

		expect(dayStarts(run, AFTERNOON)).toEqual([
			dayOf(local(9, 12, 9)),
			null,
			'Yesterday',
			null,
			'Today'
		]);
	});

	it('heads nothing twice when the run stays on one day', () => {
		expect(dayStarts([local(9, 25, 9), local(9, 25, 10)], AFTERNOON)).toEqual(['Today', null]);
	});
});

describe('a time of day', () => {
	it('is the time part of the whole moment', () => {
		const at = local(9, 12, 23, 37);
		expect(exactly(at).endsWith(timeOfDay(at))).toBe(true);
	});

	it("writes a setting's HH:MM the way the reader writes a clock, with no seconds to show", () => {
		expect(clockTime('23:00')).toBe(
			new Date(2026, 8, 25, 23).toLocaleTimeString(undefined, {
				timeStyle: 'short',
				hourCycle: 'h12'
			})
		);
		expect(clockTime('not a time')).toBe('not a time');
	});
});

describe('seconds and the clock', () => {
	afterEach(() => clock.reset());

	const at = new Date(2026, 8, 25, 15, 4, 36).getTime() / 1000;

	it('carries the seconds in every time a person reads', () => {
		expect(timeOfDay(at)).toContain(':36');
		expect(exactly(at)).toContain(':36');
		expect(onRecord(at, { now: AFTERNOON })).toContain(':36');
	});

	it('writes a twelve-hour time until somebody chooses the twenty-four-hour clock', () => {
		expect(timeOfDay(at)).not.toContain('15:04');
		clock.take('24');
		expect(timeOfDay(at)).toContain('15:04:36');
		expect(exactly(at)).toContain('15:04:36');
		expect(clockTime('23:00')).toContain('23:00');
	});

	it("writes a log line's time to the millisecond, and nothing else that way", () => {
		expect(logTime(at + 0.125)).toContain('36.125');
		expect(timeOfDay(at + 0.125)).not.toContain('.125');
	});

	it('takes anything but the twenty-four-hour answer as the default', () => {
		clock.take('24');
		clock.take('something a later Sift wrote');
		expect(clock.hours).toBe('12');
	});
});

describe('a stamp for a file name', () => {
	it('sorts in the order things were made and holds no colon', () => {
		expect(stampForAFileName(new Date(2026, 8, 5, 7, 4, 3))).toBe('2026-09-05 07-04-03');
	});
});

describe("the server machine's zone, never the browser's", () => {
	afterEach(() => {
		clock.takeZone(undefined);
		clock.reset();
	});

	/* 23:30 on 15 September on a server in New York is 03:30 on the 16th in UTC and 12:30 on the
	   16th in Tokyo. Two server zones are asked about, so the test reads the same whatever zone the
	   machine running it is in. */
	const lateEvening = Date.UTC(2026, 8, 16, 3, 30) / 1000;

	it('writes the day a moment fell on in the server zone', () => {
		clock.takeZone('America/New_York');
		expect(dayOf(lateEvening)).toContain('15');
		clock.takeZone('Asia/Tokyo');
		expect(dayOf(lateEvening)).toContain('16');
	});

	it("groups a filing at 23:30 under that evening's day, not the next", () => {
		clock.takeZone('America/New_York');
		const laterThatEvening = Date.UTC(2026, 8, 16, 3, 50) / 1000;
		const nextMorning = Date.UTC(2026, 8, 16, 13, 0) / 1000;
		expect(dayHeading(lateEvening, laterThatEvening)).toBe('Today');
		expect(dayHeading(lateEvening, nextMorning)).toBe('Yesterday');
		expect(dayStarts([lateEvening, nextMorning], nextMorning)).toEqual(['Yesterday', 'Today']);
	});

	it("writes the time of day on the server's clock", () => {
		clock.take('24');
		clock.takeZone('America/New_York');
		expect(timeOfDay(lateEvening)).toContain('23:30:00');
		expect(onRecord(lateEvening, { now: lateEvening + 60 })).toContain('Today 23:30:00');
		clock.takeZone('Asia/Tokyo');
		expect(timeOfDay(lateEvening)).toContain('12:30:00');
	});

	it("stamps a file name on the server's clock", () => {
		clock.takeZone('America/New_York');
		expect(stampForAFileName(new Date(lateEvening * 1000))).toBe('2026-09-15 23-30-00');
		clock.takeZone('Asia/Tokyo');
		expect(stampForAFileName(new Date(lateEvening * 1000))).toBe('2026-09-16 12-30-00');
	});

	it('counts a day that holds a clock change as one day', () => {
		clock.takeZone('America/New_York');
		/* The clocks go back at 2 AM on 1 November 2026 there: that day is 25 hours long. */
		const saturdayNoon = Date.UTC(2026, 9, 31, 16) / 1000;
		const sundayNoon = Date.UTC(2026, 10, 1, 17) / 1000;
		expect(dayHeading(saturdayNoon, sundayNoon)).toBe('Yesterday');
	});

	it('moves no day and no setting time with the zone', () => {
		clock.takeZone('America/Los_Angeles');
		const west = [calendarDay('1991-07-09'), clockTime('23:00')];
		clock.takeZone('Asia/Tokyo');
		expect([calendarDay('1991-07-09'), clockTime('23:00')]).toEqual(west);
		expect(calendarDay('1991-07-09')).toContain('9');
	});

	it("reads the zone from the session's answer", () => {
		session.viewer = { zone: 'Asia/Tokyo' } as Viewer;
		expect(clock.zone).toBe('Asia/Tokyo');
		clock.takeZone('America/New_York');
		expect(clock.zone).toBe('America/New_York');
		clock.takeZone(undefined);
		expect(clock.zone).toBe('Asia/Tokyo');
		session.viewer = undefined;
		expect(clock.zone).toBeUndefined();
	});

	it("keeps the browser's own zone for a name it cannot read", () => {
		const own = [dayOf(lateEvening), timeOfDay(lateEvening)];
		clock.takeZone('Not/A_Zone');
		expect([dayOf(lateEvening), timeOfDay(lateEvening)]).toEqual(own);
		clock.takeZone(null);
		expect(clock.zone).toBeUndefined();
		clock.takeZone('Asia/Tokyo');
		expect(clock.zone).toBe('Asia/Tokyo');
	});
});

describe('sayWindow', () => {
	const MINUTE = 60;
	const HOUR = 3600;
	const DAY = 86_400;

	/* The shapes the rule names, each from the bounds that should produce it. */
	it('says a window in words, rounded out to a step that grows with the wait', () => {
		expect(sayWindow(20, 50)).toBe('under a minute');
		expect(sayWindow(70, 4 * MINUTE)).toBe('a few minutes');
		expect(sayWindow(12 * MINUTE, 12 * MINUTE)).toBe('about 10 to 15 minutes');
		expect(sayWindow(33 * MINUTE, 44 * MINUTE)).toBe('about 30 to 45 minutes');
		expect(sayWindow(8 * MINUTE, 50 * MINUTE)).toBe('under an hour');
		expect(sayWindow(40 * MINUTE, 55 * MINUTE)).toBe('about 30 minutes to an hour');
		expect(sayWindow(HOUR, HOUR)).toBe('about an hour');
		expect(sayWindow(2.2 * HOUR, 4.6 * HOUR)).toBe('a few hours');
		expect(sayWindow(100 * MINUTE, 130 * MINUTE)).toBe('about 1 to 3 hours');
		expect(sayWindow(18 * HOUR, 26 * HOUR)).toBe('about 18 to 27 hours');
		expect(sayWindow(2.5 * DAY, 2.9 * DAY)).toBe('about 2 to 3 days');
	});

	it('says one figure only where both ends land on the same step', () => {
		expect(sayWindow(2 * HOUR, 2 * HOUR)).toBe('about 2 hours');
		expect(sayWindow(2 * HOUR, 2.1 * HOUR)).toBe('about 2 to 3 hours');
	});

	it('never says a figure to the minute, and never "0 minutes"', () => {
		for (const seconds of [1, 59, 61, 7 * MINUTE, 19 * MINUTE, 41 * MINUTE, 5 * HOUR, 3 * DAY]) {
			const said = sayWindow(seconds, seconds);
			expect(said).not.toMatch(/\b0 minutes\b/);
			expect(said).not.toMatch(/\b(1[1-4]|1[6-9]|[2-9][1-4]|[2-9][6-9]) minutes\b/);
		}
	});

	it('has nothing to say without both bounds, or with bounds that are not a window', () => {
		expect(sayWindow(null, 60)).toBe(NOT_ENOUGH_TO_SAY);
		expect(sayWindow(60, undefined)).toBe(NOT_ENOUGH_TO_SAY);
		expect(sayWindow(10 * HOUR, HOUR)).toBe(NOT_ENOUGH_TO_SAY);
		expect(sayWindow(-1, 10)).toBe(NOT_ENOUGH_TO_SAY);
		expect(sayWindow(0, Number.POSITIVE_INFINITY)).toBe(NOT_ENOUGH_TO_SAY);
	});
});
