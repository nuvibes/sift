/*
 * The words the chart primitives draw around a figure: the table under a chart, the top of a
 * scale, a heat-map's key, an hour's mark. The figures themselves, and what each series is called,
 * are the caller's.
 */
import { clock } from '$lib/shell/clock.svelte';
import { clockTime } from '$lib/shell/when';

export const CHART_WORDS = {
	/** The table under a drawing that says every mark in words. */
	table: 'Each bar, in figures',
	/** The same table under a heat-map, a day to a row. */
	days: 'Each day, in figures',
	/** The same table under the hour ring. */
	hours: 'Each hour, in figures',
	/** The column naming the mark: a day, a month, an hour. */
	when: 'When',
	/** The top of a scale: the tallest mark's figure. */
	top: 'Most',
	/** After the mark that is still counting: today, or this month. */
	soFar: 'so far',
	/** Either end of a heat-map's key. */
	less: 'Less',
	more: 'More'
} as const;

/**
 * An hour as a mark on a chart, on the reader's clock: "6 AM" or "06". The hour alone, because the
 * marks stand a bar or a quarter of a ring apart; the server marks an hour's bars the same way.
 */
export function hourMark(hour: number): string {
	const at = ((hour % 24) + 24) % 24;
	if (clock.hours === '24') return String(at).padStart(2, '0');
	return `${at % 12 || 12} ${at < 12 ? 'AM' : 'PM'}`;
}

/** An hour said in full, on the reader's clock, as every time of day on screen is: "10:00 PM". */
export function hourWords(hour: number): string {
	return clockTime(`${String(((hour % 24) + 24) % 24).padStart(2, '0')}:00`);
}
