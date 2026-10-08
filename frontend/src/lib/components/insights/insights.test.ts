/* The Insights screen's own rules, apart from any drawing: a period is a place with an address, the
 * arrows step one day past the answer's ends, and a figure is worded by the statements' own rule. */

import { describe, expect, it } from 'vitest';

import { clockTime } from '$lib/shell/when';

import {
	clockWords,
	countWords,
	drawsAnything,
	figureWords,
	lengthWords,
	worthACard,
	type Figure
} from './figures';
import { addressOf, dayAway, placeOf, stepsFrom, tabsFor, type InsightsPage } from './period';
import { INSIGHTS_WORDS, PERIOD_WORDS } from './words';

const at = (address: string) => placeOf(new URL(`http://sift.local${address}`));

function answer(over: Partial<InsightsPage>): InsightsPage {
	return {
		period: 'month',
		from: '2026-08-01',
		to: '2026-08-31',
		today_is_live: false,
		first_sentences: [],
		blocks: [],
		...over
	};
}

describe('a period is a place with an address', () => {
	it('reads the span and the day from the address, and writes them back the same', () => {
		const place = at('/insights?period=month&at=2026-09-01');
		expect(place).toEqual({ period: 'month', at: '2026-09-01' });
		expect(addressOf(place)).toBe('/insights?period=month&at=2026-09-01');
	});

	it("is the server's default for a bare address, and today's period without a day", () => {
		expect(at('/insights')).toEqual({ period: 'day', at: null });
		expect(addressOf({ period: 'day', at: null })).toBe('/insights?period=day');
	});

	it('leaves out what it cannot read rather than guessing', () => {
		expect(at('/insights?period=fortnight&at=2026-09-01')).toEqual({
			period: 'day',
			at: '2026-09-01'
		});
		expect(at('/insights?period=month&at=2026-02-30').at).toBeNull();
		expect(at('/insights?period=month&at=yesterday').at).toBeNull();
	});

	it('has no day in the address of All, which is the whole record', () => {
		expect(at('/insights?period=all&at=2026-09-01')).toEqual({ period: 'all', at: null });
		expect(addressOf({ period: 'all', at: '2026-09-01' })).toBe('/insights?period=all');
	});

	it('draws every tab as a link to its span at the same day, in the order of the words', () => {
		const tabs = tabsFor({ period: 'month', at: '2026-08-14' });
		expect(tabs.map((tab) => tab.label)).toEqual(['All', 'Day', 'Week', 'Month', 'Year']);
		expect(tabs.map((tab) => tab.label)).toEqual(Object.values(PERIOD_WORDS));
		expect(tabs.find((tab) => tab.id === 'week')?.href).toBe('/insights?period=week&at=2026-08-14');
		expect(tabs.find((tab) => tab.id === 'all')?.href).toBe('/insights?period=all');
	});
});

describe('the arrows', () => {
	it('step to the day before the first day and the day after the last', () => {
		expect(stepsFrom(answer({}))).toEqual({
			earlier: { period: 'month', at: '2026-07-31' },
			later: { period: 'month', at: '2026-09-01' }
		});
	});

	it('have no later while today is inside the period: it has not happened', () => {
		expect(stepsFrom(answer({ today_is_live: true })).later).toBeNull();
	});

	it('have neither on All', () => {
		expect(stepsFrom(answer({ period: 'all' }))).toEqual({ earlier: null, later: null });
	});

	it('count calendar days across a month, a year, a leap day and a clock change', () => {
		expect(dayAway('2026-03-01', -1)).toBe('2026-02-28');
		expect(dayAway('2028-03-01', -1)).toBe('2028-02-29');
		expect(dayAway('2026-12-31', 1)).toBe('2027-01-01');
		expect(dayAway('2026-10-25', 1)).toBe('2026-10-26');
		expect(dayAway('2026-03-29', -1)).toBe('2026-03-28');
	});
});

describe("a figure, by the statements' own rule", () => {
	/* The short form a length takes on a tile and in a counting card's in-between frames: the
	   server's `figure_said`, pinned in `slices/insights/tests/test_arithmetic.py` to the same
	   examples, and `$lib/shell/duration`'s `sayDuration`. */
	it.each([
		[41 * 3_600_000, '1 d 17 h'],
		[85 * 60_000, '1 h 25 min'],
		[60 * 60_000, '1 h'],
		[12 * 60_000, '12 min'],
		[90_000, '2 min'],
		[20_000, 'under a minute'],
		[0, '0 min']
	])('says %i ms as %s', (ms, said) => {
		expect(lengthWords(ms)).toBe(said);
	});

	it('never says a length with a decimal', () => {
		for (let ms = 0; ms < 200 * 3_600_000; ms += 1_234_567) {
			expect(lengthWords(ms)).not.toMatch(/\d\.\d/);
		}
	});

	it('says a count as a person does', () => {
		expect(countWords(7)).toBe('7');
		expect(countWords(1240)).toBe('1,240');
		expect(countWords(250_400)).toBe('about 250,000');
	});

	it('says a minute of the day as a clock, past midnight wrapping', () => {
		expect(clockWords(430)).toBe('07:10');
		expect(clockWords(1540)).toBe('01:40');
		/* On a card, a time of day is on the reader's own clock, as every clock time a setting holds. */
		expect(figureWords(430, 'minute_of_day')).toBe(clockTime('07:10'));
		expect(figureWords(2_000_000, 'bytes')).toBe('2.0 MB');
	});
});

describe('a figure on a card', () => {
	const figure = (label: string, value: number, unit: Figure['unit']): Figure => ({
		label,
		value,
		unit,
		hidden_part: 0,
		caption: [],
		defines: [],
		said: '',
		hidden_said: '',
		trend: []
	});

	it('is left out when it is nothing, and a time of day never is, midnight included', () => {
		expect(worthACard(figure('Deleted', 0, 'count'))).toBe(false);
		expect(worthACard(figure('Arrived', 3, 'count'))).toBe(true);
		expect(worthACard(figure('First opened at', 0, 'minute_of_day'))).toBe(true);
	});
});

describe('the words', () => {
	it('say the head line the contracts give, word for word', () => {
		expect(INSIGHTS_WORDS.headLine).toBe(
			'Your library, your viewing and your organizing, in numbers. Nothing here leaves this device.'
		);
		expect(INSIGHTS_WORDS.title).toBe('Insights');
	});
});

describe('a block with nothing to draw', () => {
	/* Opinions on a day with no rating and no star would draw its heading over nothing. */
	const bare = {
		id: 'opinions',
		title: 'Opinions',
		floor_reached: true,
		statements: [],
		figures: [{ label: 'Rated', value: 0, unit: 'count' }],
		chart: null,
		calendar: null,
		lists: [],
		notes: []
	} as unknown as Parameters<typeof drawsAnything>[0];

	it('is left off the page', () => {
		expect(drawsAnything(bare)).toBe(false);
	});

	const withAlso = (over: Record<string, unknown>) => ({ ...bare, ...over }) as typeof bare;

	it('is drawn once it has a figure worth a card, a sentence or a list', () => {
		expect(
			drawsAnything(withAlso({ figures: [{ label: 'Rated', value: 4, unit: 'count' }] }))
		).toBe(true);
		expect(drawsAnything(withAlso({ statements: [[{ text: 'You starred 3 files.' }]] }))).toBe(
			true
		);
		expect(drawsAnything(withAlso({ lists: [{ title: 'Files you starred', rows: [] }] }))).toBe(
			true
		);
	});
});
