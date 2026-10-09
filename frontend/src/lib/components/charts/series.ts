/*
 * What a chart draws its series in: three shades of the accent (each clearing 3:1 on every surface)
 * and a neutral; never red, green or amber, which mean failure, done and warning.
 */

/** Lightest first. */
export const SHADES = [
	'var(--sift-series-1)',
	'var(--sift-series-2)',
	'var(--sift-series-3)'
] as const;

export const NEUTRAL = 'var(--sift-ink-2)';

/** Faint enough that the words over it keep their ink. */
export const BEHIND = 'color-mix(in srgb, var(--sift-series-2) 18%, transparent)';

export const EMPTY = 'var(--sift-surface-3)';

export interface Series {
	id: string;
	label: string;
	paint: string;
}

export interface ChartBar {
	label: string;
	parts: readonly { series: string; value: number }[];
}

export const LEVEL_PAINT = [EMPTY, SHADES[2], SHADES[1], SHADES[0]] as const;

export type Level = 0 | 1 | 2 | 3;

/**
 * Thirds by rank of the person's own non-zero figures, so one outlier does not wash the rest out.
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

/** Whole percents adding to exactly 100, by largest remainders. */
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
