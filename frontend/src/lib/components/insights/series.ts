/*
 * What each kind of viewing is drawn in, on every chart of Insights and every recap.
 *
 * Videos, pictures and GIFs are the accent's three shades, lightest first because videos are
 * usually the most; Theater, which is a way of viewing rather than a kind of file, is the one
 * neutral. A bar that is not split (the hours, the files that arrived) is one series in the middle
 * shade. Fixed, so a colour means the same kind on every chart and in every period.
 */
import { NEUTRAL, SHADES, type Series } from '$lib/components/charts/series';

import { KIND_WORDS } from '$lib/components/insights/words';

const PAINT: Record<string, string> = {
	video: SHADES[0],
	image: SHADES[1],
	gif: SHADES[2],
	theater: NEUTRAL,
	all: SHADES[1],
	added: SHADES[1]
};

/** The order kinds stack from the baseline and are read in a key: never by size. */
const ORDER = ['video', 'image', 'gif', 'theater', 'all', 'added'];

/** The series a chart's parts name, in the fixed order. A kind nobody named is drawn neutral. */
export function seriesOf(kinds: Iterable<string>): Series[] {
	const named = new Set(kinds);
	const known = ORDER.filter((kind) => named.has(kind));
	const other = [...named].filter((kind) => !ORDER.includes(kind)).sort();
	return [...known, ...other].map((kind) => ({
		id: kind,
		label: KIND_WORDS[kind] ?? kind,
		paint: PAINT[kind] ?? NEUTRAL
	}));
}
