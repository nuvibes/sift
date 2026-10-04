/*
 * What a chart draws its series in, and how a heat-map shades a value.
 *
 * Three shades of the accent's own hue, lightest first, and one neutral. The token layer solves
 * the three per accent so each clears 3:1 against every surface a chart stands on, which is what
 * keeps two neighbouring marks separate: each stands apart from the strip of ground between them.
 * Red, green and amber are not series colours, because on this screen they would read as failure,
 * done and warning. A value is never told by colour alone: every chart has its words beside it.
 */

/** The three accent shades, lightest first. In a dark theme the lightest carries the most. */
export const SHADES = [
	'var(--sift-series-1)',
	'var(--sift-series-2)',
	'var(--sift-series-3)'
] as const;

/** The one series that is not a shade of the accent. */
export const NEUTRAL = 'var(--sift-ink-2)';

/** A bar drawn BEHIND words: the middle shade, faint enough that the words over it keep their ink. */
export const BEHIND = 'color-mix(in srgb, var(--sift-series-2) 18%, transparent)';

/** The ground of a heat-map cell or an hour with nothing in it. */
export const EMPTY = 'var(--sift-surface-3)';

/** One series a chart draws: named in code, called something on screen, and painted. */
export interface Series {
	id: string;
	label: string;
	paint: string;
}

/** One bar of a bar chart: what it is, and each series' figure in it. */
export interface ChartBar {
	label: string;
	parts: readonly { series: string; value: number }[];
}

/** A shade for each level a value can take: nothing, then the least to the most. */
export const LEVEL_PAINT = [EMPTY, SHADES[2], SHADES[1], SHADES[0]] as const;

export type Level = 0 | 1 | 2 | 3;

/**
 * Which level a value takes, measured against the person's own figures rather than a fixed scale:
 * nothing is 0, and the non-zero values split into thirds by rank, so one outlier day does not
 * wash every other day out to the palest shade.
 */
export function levels(values: readonly number[]): (value: number) => Level {
	const ranked = values.filter((value) => value > 0).sort((a, b) => a - b);
	const at = (share: number) =>
		ranked[Math.min(ranked.length - 1, Math.floor(ranked.length * share))];
	const low = ranked.length > 0 ? at(1 / 3) : 0;
	const high = ranked.length > 0 ? at(2 / 3) : 0;
	return (value) => {
		if (!(value > 0)) return 0;
		if (value < low) return 1;
		if (value < high) return 2;
		return 3;
	};
}

/**
 * Each part's share of the whole in whole percents that add up to exactly 100: the parts are
 * rounded down and the points left over go to the largest remainders, so a key never reads 101.
 */
export function shares(values: readonly number[]): number[] {
	const whole = values.reduce((sum, value) => sum + Math.max(0, value), 0);
	if (whole <= 0) return values.map(() => 0);
	const exact = values.map((value) => (Math.max(0, value) / whole) * 100);
	const floored = exact.map((share) => Math.floor(share));
	let left = 100 - floored.reduce((sum, share) => sum + share, 0);
	const order = exact
		.map((share, index) => ({ index, rest: share - floored[index] }))
		.sort((a, b) => b.rest - a.rest || a.index - b.index);
	for (const { index } of order) {
		if (left <= 0) break;
		floored[index] += 1;
		left -= 1;
	}
	return floored;
}
