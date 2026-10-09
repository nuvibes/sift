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
	byKind: 'Time viewed, by kind',
	/** The press beside a figure, and the heading of the sentence it opens: what the figure counts. */
	defines: 'What counts'
} as const;

/**
 * The press beside Stats that opens this period's recap as its deck of cards (today's, said so),
 * and what it says
 * where there is none: a recap is created the day after its period ends, and only of a period
 * with enough viewing in it.
 */
export const DECK_WORDS: Record<
	'day' | 'week' | 'month' | 'year',
	{ see: string; today?: string; none: string }
> = {
	day: {
		see: "See this day's recap",
		today: "See today's recap",
		none: 'No recap of this day yet. Sift creates one the morning after a day with enough viewing in it.'
	},
	week: {
		see: "See this week's recap",
		none: 'No recap of this week yet. Sift creates one the day after a week with enough viewing in it.'
	},
	month: {
		see: "See this month's recap",
		none: 'No recap of this month yet. Sift creates one the day after a month with enough viewing in it.'
	},
	year: {
		see: "See this year's recap",
		none: 'No recap of this year yet. Sift creates one the day after a year with enough viewing in it.'
	}
};

/**
 * The Stats view's words: the screen's name (and the press on Insights that opens it), its line,
 * its index, and the headings of its tables. The figures and the names in them are the server's.
 */
export const STATS_WORDS = {
	title: 'Stats',
	headLine: 'Every figure behind Insights, with its table. Copy a table to paste it anywhere.',
	/** The index down the side: what it is, and each entry's count. */
	index: 'Stats sections',
	tables: (count: number) => (count === 1 ? '1 table' : `${count} tables`),
	/** Each family's heading, in the board's order. */
	families: {
		viewing: 'Viewing',
		people: 'People',
		sites: 'Sites',
		organizing: 'Organizing',
		theater: 'Theater',
		downloads: 'Library',
		alongside: 'Alongside'
	},
	/** The caption over a block's figures, and the heading of each column. */
	figures: 'Figures',
	what: 'What',
	figure: 'Figure',
	trend: 'Trend',
	defines: 'What counts',
	/** The captions over a chart's table and a calendar's. */
	bars: 'Each bar',
	days: 'Each day',
	/** The caption over one whole split by kind. */
	kinds: 'Each kind',
	/** The heading over a list's names. */
	name: 'Name',
	copy: 'Copy',
	copyLabel: 'Copy this table',
	copied: 'Copied',
	copyFailed: "That table couldn't be copied"
} as const;

/** The board's own words: a tile's title where no block names it, and what its press opens. */
export const BOARD_WORDS = {
	/** The headline tile's title: the period it is the line of. */
	headline: {
		day: 'Your day',
		week: 'Your week',
		month: 'Your month',
		year: 'Your year',
		all: 'All of it'
	} satisfies Record<Span, string>,
	/** The time viewed tile's title: what each of its bars is. */
	bars: {
		day: 'Hour by hour',
		week: 'Day by day',
		month: 'Day by day',
		year: 'Month by month',
		all: 'Month by month'
	} satisfies Record<Span, string>,
	/** The days tile's title: the period's days as a calendar of squares. */
	days: 'Calendar',
	/** The keyboard's way into a tile's table on Stats. */
	open: (title: string) => `${title} in Stats`
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
 * kind (the hours of the day); and `added`, the files imported.
 */
export const KIND_WORDS: Record<string, string> = {
	video: 'Videos',
	image: 'Pictures',
	gif: 'GIFs',
	theater: 'Theater',
	all: 'Viewed',
	added: 'Imported'
};
