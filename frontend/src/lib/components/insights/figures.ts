/*
 * A figure's number, as the screen lays it out: a tile's value, a list row's figure, a bar's
 * height.
 *
 * The server sends a figure as a number and its unit (`slices/insights/models.py`, `Figure`), and
 * every figure it says, it says in a statement. What is drawn here is the bare number beside a
 * label ("Daily average  3 hours"), never a sentence.
 *
 * Every figure at rest is worded by the SERVER: `Figure.said`, `NamedRow.said`, and a chart's
 * `BarPart.said`, `Bar.said` and a calendar's `DayValue.said`, all by one rule
 * (`statements.figure_said`), so a tile, a bar and a statement cannot give two answers. `saidOf`
 * draws a figure's; `wordsOf` gathers a chart's or a calendar's for the primitive drawing it. Empty
 * `said` is a time of day or a size, which the screen words on the reader's own settings.
 *
 * What the screen still words itself is a card's in-between frames while its figure counts up:
 * the server sends the final figure's words, not the ones on the way to it. Those step through
 * words by the same short form (`$lib/shell/duration`'s `sayDuration`, the server's `duration_short` in
 * the other language), so a counting card reads "41 min", "1 h 5 min" ... "1 d 17 h" and lands on
 * the server's own words, never on a number with no unit.
 */
import { sayDuration } from '$lib/shell/duration';
import { size } from '$lib/library/facts';
import { clockTime } from '$lib/shell/when';

import type { components } from '$lib/api/schema';

export type Figure = components['schemas']['Figure'];
export type Unit = Figure['unit'];

/** Past this a count is said to the nearest thousand, as the server says it. */
const ABOUT_OVER = 100_000;

/** A half going up, in whole numbers, as the server rounds. */
function nearest(numerator: number, denominator: number): number {
	return Math.floor((2 * numerator + denominator) / (2 * denominator));
}

/** "7", "1,240", and past 100,000 "about 250,000". */
export function countWords(value: number): string {
	if (value > ABOUT_OVER) return `about ${(nearest(value, 1000) * 1000).toLocaleString('en-US')}`;
	return value.toLocaleString('en-US');
}

function counted(value: number, one: string, more: string): string {
	return value === 1 ? `1 ${one}` : `${countWords(value)} ${more}`;
}

/** A length of time as a fact on a tile or a bar: the short form, "1 h 12 min". Zero is "0 min":
 *  a tile of nothing is a fact. The server's `figure_said` says the same. */
export function lengthWords(ms: number): string {
	if (ms <= 0) return '0 min';
	return sayDuration(ms / 1000) ?? '0 min';
}

/** A minute of the local day as a clock says it: "07:10". Past midnight wraps: 1540 is "01:40". */
export function clockWords(minute: number): string {
	const wrapped = ((minute % 1440) + 1440) % 1440;
	const two = (part: number) => String(part).padStart(2, '0');
	return `${two(Math.floor(wrapped / 60))}:${two(wrapped % 60)}`;
}

/** A minute of the local day as a figure on a card: on the reader's own clock, "7:10 AM". */
export function timeWords(minute: number): string {
	return clockTime(clockWords(minute));
}

/**
 * Whether a figure earns a card: a figure of nothing is left out, because a card reading "0" is a
 * card nobody needed. A time of day is never nothing, midnight included.
 */
export function worthACard(figure: Figure): boolean {
	return figure.unit === 'minute_of_day' || figure.value !== 0;
}

/** A figure's number in its unit. */
/**
 * Whether a block past its floor has anything to draw: a figure worth a card, a chart, a calendar
 * with days in it, a list, a sentence or a note. A block with none of them is left off the page,
 * rather than drawn as its heading over nothing.
 */
export function drawsAnything(block: components['schemas']['InsightsBlock']): boolean {
	return (
		block.figures.some(worthACard) ||
		Boolean(block.chart) ||
		(block.calendar?.days.length ?? 0) > 0 ||
		block.lists.length > 0 ||
		block.statements.length > 0 ||
		(block.notes?.length ?? 0) > 0
	);
}

/** A count that says what it counts, one and many, for the units that name a noun. */
const NOUNS: Partial<Record<Unit, readonly [string, string]>> = {
	views: ['view', 'views'],
	times: ['time', 'times'],
	presses: ['press', 'presses'],
	files: ['file', 'files']
};

export function figureWords(value: number, unit: Unit): string {
	if (unit === 'ms') return lengthWords(value);
	if (unit === 'bytes') return size(value) ?? '0 B';
	if (unit === 'minute_of_day') return timeWords(value);
	const noun = NOUNS[unit];
	if (noun) return counted(value, noun[0], noun[1]);
	return countWords(value);
}

/** A figure's words at rest: the server's `said` where it sent some, else the reader's own. */
export function saidOf(said: string | undefined, value: number, unit: Unit): string {
	return said ? said : figureWords(value, unit);
}

/**
 * The server's words for every value it said, by value, and a formatter for a chart primitive that
 * reads them: a value the server said is drawn in its words, anything else (a time of day, a size)
 * in the reader's own. Keyed by value because one value is one set of words under one rule.
 */
export function wordsOf(
	said: Iterable<{ value: number; said?: string }>,
	unit: Unit
): (value: number) => string {
	const known = new Map<number, string>();
	for (const one of said) if (one.said) known.set(one.value, one.said);
	return (value) => known.get(value) ?? figureWords(value, unit);
}
