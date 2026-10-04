/*
 * THE INSIGHTS SCREEN'S OWN WORDS: the furniture around the server's sentences, and nothing else.
 *
 * Every SENTENCE on the screen is the server's: the first sentences, each block's statements, "Not
 * enough yet to say." below a floor, the empty library's line and the guest's all arrive as pieces
 * (`slices/insights/statements.py`) and are drawn by `HistorySentence`, which builds nothing. So is
 * every block's title and every figure's label. What is written here is what only the screen has:
 * its name, the line under it, the period tabs, the arrows, and what each kind is called in a
 * chart's key. The words around a chart itself are the chart primitives' (`charts/words.ts`).
 *
 * Here rather than inline so the words are in one place the vocabulary gate reads, and so the tests
 * name the words instead of retyping them.
 */
import type { Span } from '$lib/components/insights/period';

export const INSIGHTS_WORDS = {
	/** The rail row and the screen's heading. */
	title: 'Insights',
	/** The one line under the heading. "this device" is Sift's word for the machine it runs on. */
	headLine:
		'Your library, your viewing and your organizing, in numbers. Nothing here leaves this device.',
	/** What the row of tabs is, for anybody who cannot see that the words belong together. */
	periods: 'Which period to show',
	/** The arrows either side of the tabs: the period before this one, and the one after. */
	earlier: 'Earlier',
	later: 'Later',
	/** When the answer could not be read. The words every other screen says for it. */
	failed: "That couldn't be loaded. Try again in a moment.",
	/** Beside a figure while the vault is open: how much of it is hidden things. */
	hidden: 'Hidden',
	/** The heading over the recaps this account has. */
	recaps: 'Recaps',
	/** What the time by kind adds up to, for the key under the By kind bar. */
	byKind: 'Time viewed, by kind'
} as const;

/** What each period tab says. */
export const PERIOD_WORDS: Record<Span, string> = {
	all: 'All',
	day: 'Day',
	week: 'Week',
	month: 'Month',
	year: 'Year'
};

/**
 * What each kind of viewing is called in a chart's key: the words the server's own statements use
 * ("29 of videos, 9 of pictures, 3 of GIFs"); `all`, the one part of a bar that is not split by
 * kind (the hours of the day); and `added`, the files that arrived.
 */
export const KIND_WORDS: Record<string, string> = {
	video: 'Videos',
	image: 'Pictures',
	gif: 'GIFs',
	theater: 'Theater',
	all: 'Viewed',
	added: 'Arrived'
};
