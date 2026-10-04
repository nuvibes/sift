import { describe, expect, it } from 'vitest';

import { clock, lengthClock, playheadClock, sayDuration, sayLength } from './duration';

describe('clock', () => {
	it('is minutes and seconds under an hour, with the seconds padded', () => {
		expect(clock(0)).toBe('0:00');
		expect(clock(65)).toBe('1:05');
		expect(clock(59 * 60 + 59)).toBe('59:59');
	});

	it('says the hours past an hour, with the minutes padded behind them', () => {
		expect(clock(60 * 60)).toBe('1:00:00');
		expect(clock(90 * 60)).toBe('1:30:00');
		expect(clock(3600 + 2 * 60 + 5)).toBe('1:02:05');
	});

	it('floors, because a playhead has not reached the next second yet', () => {
		expect(clock(9.842)).toBe('0:09');
	});

	it('answers 0:00 for a position that is not one yet', () => {
		expect(clock(Number.NaN)).toBe('0:00');
		expect(clock(-1)).toBe('0:00');
		expect(clock(Number.POSITIVE_INFINITY)).toBe('0:00');
	});
});

describe('playheadClock', () => {
	it('floors like a playhead until the end, and reads the length there', () => {
		expect(playheadClock(301.6, 301.9)).toBe('5:01');
		expect(playheadClock(301.9, 301.9)).toBe(lengthClock(301.9));
		expect(playheadClock(4, 0)).toBe('0:04');
	});
});

describe('lengthClock', () => {
	it('rounds, because a length is a measurement and reads as the record does', () => {
		expect(lengthClock(9.842)).toBe('0:10');
		expect(lengthClock(5399.6)).toBe('1:30:00');
	});

	it('answers 0:00 for a length that is not one', () => {
		expect(lengthClock(Number.NaN)).toBe('0:00');
		expect(lengthClock(-5)).toBe('0:00');
	});
});

describe('sayDuration', () => {
	it('is one short form per unit, a space before it', () => {
		expect(sayDuration(3 * 60)).toBe('3 min');
		expect(sayDuration(2 * 3600 + 10 * 60)).toBe('2 h 10 min');
		expect(sayDuration(3 * 86400 + 4 * 3600)).toBe('3 d 4 h');
	});

	it('leaves off a smaller unit that comes to nothing', () => {
		expect(sayDuration(2 * 3600)).toBe('2 h');
		expect(sayDuration(3 * 86400)).toBe('3 d');
	});

	it('rounds before it splits, so the minutes never read 60 and the hours never 24', () => {
		expect(sayDuration(59 * 60 + 40)).toBe('1 h');
		expect(sayDuration(86400 - 20)).toBe('1 d');
	});

	it('says under a minute in words, as the estimates do', () => {
		expect(sayDuration(0)).toBe('under a minute');
		expect(sayDuration(59)).toBe('under a minute');
		expect(sayDuration(60)).toBe('1 min');
	});

	it('says nothing for what is not a length', () => {
		expect(sayDuration(-1)).toBeNull();
		expect(sayDuration(Number.NaN)).toBeNull();
		expect(sayDuration(Number.POSITIVE_INFINITY)).toBeNull();
	});
});

describe('sayLength', () => {
	it('spells a chosen length out, seconds under a minute and whole minutes from one', () => {
		expect(sayLength(30)).toBe('30 seconds');
		expect(sayLength(1)).toBe('1 second');
		expect(sayLength(60)).toBe('1 minute');
		expect(sayLength(300)).toBe('5 minutes');
	});
});
