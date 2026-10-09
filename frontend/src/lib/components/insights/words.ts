/* Words only the screen has, around the server's sentences, in one place for the vocabulary gate
 * and the tests. */
import type { Span } from '$lib/components/insights/period';

export const INSIGHTS_WORDS = {
	/** The rail row and the screen's heading. */
	title: 'Insights',
	/** "this device" is Sift's word for the machine it runs on. */
	headLine:
		'Your library, your viewing and your organizing, in numbers. Nothing here leaves this device.',
	/** What the row of tabs is, for anybody who cannot see that the words belong together. */
	periods: 'Which period to show',
	/** The arrows either side of the tabs: the period before this one, and the one after. */
	earlier: 'Earlier',
	later: 'Later',
	failed: "That couldn't be loaded. Try again in a moment.",
	/** Beside a figure while the vault is open: how much of it is hidden things. */
	hidden: 'Hidden',
	recaps: 'Recaps',
	/** What the time by kind adds up to, for the key under the By kind bar. */
	byKind: 'Time viewed, by kind',
	/** The press beside a figure, and the heading of what it opens. */
	defines: 'What counts'
} as const;

/** The press opening this period's recap deck, and what it says where there is none. */
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

/** Words of the Stats view; the figures and names in its tables are the server's. */
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

/** Each kind in a chart's key, in the server's statements' words; `all` is an unsplit bar. */
export const KIND_WORDS: Record<string, string> = {
	video: 'Videos',
	image: 'Pictures',
	gif: 'GIFs',
	theater: 'Theater',
	all: 'Viewed',
	added: 'Imported'
};
