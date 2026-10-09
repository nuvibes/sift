/*
 * The Stats view's panels: every table of a period's answer, each in the family the board puts it
 * in, with the address a board tile opens it at.
 *
 * A panel is one table (a block's figures, its chart's bars, its calendar's days, or one of its
 * lists) or, for a block under its floor, the block's one line. Its family is its block's, except
 * that a list of People or of Sites among the viewing figures is that family's own, as on the board.
 */
import type { TableRow } from '$lib/components/charts/table';
import { CHART_WORDS } from '$lib/components/charts/words';
import { figureWords, saidOf, wordsOf, type Figure } from '$lib/components/insights/figures';
import { chartWords } from '$lib/components/insights/KindBars.svelte';
import type { InsightsBlock as Block } from '$lib/components/insights/period';
import { seriesOf } from '$lib/components/insights/series';
import { INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
import { calendarDay } from '$lib/shell/when';

export type Family = keyof typeof STATS_WORDS.families;

/** The families in the board's order, which is the index's and the page's. */
export const FAMILIES = Object.keys(STATS_WORDS.families) as Family[];

const BLOCK_FAMILY: Record<string, Family> = {
	opinions: 'organizing',
	organizing: 'organizing',
	theater: 'theater',
	arrived: 'downloads',
	machine: 'downloads',
	alongside: 'alongside'
};

/* A list that is its own family's: the People and the Sites viewed, and the People opened first. */
const LIST_FAMILY: Record<string, Family> = {
	'most_viewed:person': 'people',
	'most_viewed:site': 'sites',
	'sittings:person': 'people'
};

/* Drawn from other answers on Insights, and not figures of the period. */
const DRAWN_ELSEWHERE = new Set(['recaps', 'path']);

/** One table as the page draws it and as Copy puts it on the clipboard. */
export interface Table {
	caption: string;
	head: string;
	columns: string[];
	rows: TableRow[];
	ranked?: boolean;
	/** The figures a figures table is of, for the trend drawn in its cell. */
	figures?: Figure[];
	/** What each bar of a trend is: the period's own bars. */
	labels?: string[];
	/** Each row's figure, for the bar behind its name. */
	shares?: number[];
}

export type Part =
	| { kind: 'figures' }
	| { kind: 'bars' }
	| { kind: 'days' }
	| { kind: 'list'; index: number }
	| { kind: 'words' };

export interface Panel {
	/** Its address on the page: `<block>-<part>`. A tile asks for the block, or for one of these. */
	id: string;
	block: Block;
	part: Part;
	family: Family;
	table: Table | null;
	/** The whole row of the page: a long chart, or a calendar. */
	wide: boolean;
	/** Two rows of the page, beside two short panels: a chart over its table. */
	tall: boolean;
	/** The block's first sentence, on its first panel. A chart says its own under its bars. */
	sentence: Block['statements'][number] | null;
	/** What its figures count, each with its label. */
	defines: { label: string; pieces: Figure['defines'] }[];
}

const hasDefinition = (figure: Figure) => figure.defines.length > 0;

/* More marks than a year's months draw too close in one column: a month's days, a day's hours. */
const LONG = 12;

function figuresOf(block: Block, labels: string[]): Table | null {
	if (block.figures.length === 0) return null;
	const hidden = block.figures.some((figure) => figure.hidden_part > 0);
	const trend = block.figures.some((figure) => (figure.trend ?? []).length > 0);
	return {
		figures: block.figures,
		labels,
		caption: STATS_WORDS.figures,
		head: STATS_WORDS.what,
		columns: [
			STATS_WORDS.figure,
			...(hidden ? [INSIGHTS_WORDS.hidden] : []),
			...(trend ? [STATS_WORDS.trend] : [])
		],
		rows: block.figures.map((figure) => ({
			label: figure.label,
			cells: [
				saidOf(figure.said, figure.value, figure.unit),
				...(hidden
					? [
							figure.hidden_part > 0
								? saidOf(figure.hidden_said, figure.hidden_part, figure.unit)
								: ''
						]
					: []),
				...(trend
					? [(figure.trend ?? []).map((value) => figureWords(value, figure.unit)).join(', ')]
					: [])
			]
		}))
	};
}

function barsOf(block: Block): Table | null {
	const chart = block.chart;
	if (!chart || chart.bars.length === 0) return null;
	const series = seriesOf(chart.bars.flatMap((bar) => bar.parts.map((part) => part.kind)));
	const words = chartWords(chart);
	/* One whole split by kind is not a row of bars over time. */
	const share = chart.kind === 'share';
	return {
		caption: share ? STATS_WORDS.kinds : STATS_WORDS.bars,
		head: share ? STATS_WORDS.what : CHART_WORDS.when,
		columns: series.map((one) => one.label),
		rows: chart.bars.map((bar, index) => ({
			label: index === chart.today ? `${bar.label}, ${CHART_WORDS.soFar}` : bar.label,
			cells: series.map((one) => words(bar.parts.find((p) => p.kind === one.id)?.value ?? 0))
		}))
	};
}

function daysOf(block: Block): Table | null {
	const calendar = block.calendar;
	if (!calendar || calendar.days.length === 0) return null;
	const words = wordsOf(calendar.days, calendar.unit);
	return {
		caption: STATS_WORDS.days,
		head: CHART_WORDS.when,
		columns: [block.title],
		rows: calendar.days.map((one) => ({ label: calendarDay(one.day), cells: [words(one.value)] }))
	};
}

function listOf(list: Block['lists'][number]): Table {
	return {
		caption: list.title,
		head: STATS_WORDS.name,
		columns: [STATS_WORDS.figure],
		ranked: true,
		shares: list.rows.map((row) => row.value),
		rows: list.rows.map((row) => ({
			label: row.piece.text,
			cells: [saidOf(row.said, row.value, row.unit)]
		}))
	};
}

/** A list's part of the address: what its rows are, or its place where they are nothing named. */
function listName(list: Block['lists'][number], index: number): string {
	return list.rows[0]?.piece.kind ?? `list-${index + 1}`;
}

/** Every panel of one block, in the order it is read. */
function panelsOf(block: Block, labels: string[]): Panel[] {
	const family = BLOCK_FAMILY[block.id] ?? 'viewing';
	const panel = (part: Part, name: string, table: Table | null, wide = false, tall = false) => ({
		id: `${block.id}-${name}`,
		block,
		part,
		family,
		table,
		wide,
		tall,
		sentence: null,
		defines: []
	});
	const words = [panel({ kind: 'words' }, 'words', null)];
	if (!block.floor_reached) return words;

	const figures = figuresOf(block, labels);
	const bars = barsOf(block);
	const days = daysOf(block);
	const unitDefined = (unit: string) =>
		block.figures
			.filter((figure) => figure.unit === unit && hasDefinition(figure))
			.slice(0, 1)
			.map((figure) => ({ label: figure.label, pieces: figure.defines }));
	const drawn: Panel[] = [];
	if (figures)
		drawn.push({
			...panel({ kind: 'figures' }, 'figures', figures),
			defines: block.figures
				.filter(hasDefinition)
				.map((figure) => ({ label: figure.label, pieces: figure.defines }))
		});
	if (bars && block.chart)
		drawn.push({
			...panel(
				{ kind: 'bars' },
				'bars',
				bars,
				block.chart.bars.length > LONG,
				block.chart.kind !== 'share'
			),
			defines: unitDefined(block.chart.unit)
		});
	if (days && block.calendar)
		drawn.push({
			...panel({ kind: 'days' }, 'days', days, true, true),
			defines: unitDefined(block.calendar.unit)
		});
	block.lists.forEach((list, index) => {
		const name = listName(list, index);
		drawn.push({
			...panel({ kind: 'list', index }, name, listOf(list)),
			family: LIST_FAMILY[`${block.id}:${name}`] ?? family
		});
	});
	/* Past its floor with no table, a block is still its sentences. */
	if (drawn.length === 0) return block.statements.length > 0 ? words : [];
	if (block.statements[0]) drawn[0].sentence = block.statements[0];
	return drawn;
}

/** Every panel of an answer, grouped by family in the board's order; a family with none is left out. */
export function familiesOf(blocks: readonly Block[]): { family: Family; panels: Panel[] }[] {
	/* A trend has a bar for each of the period's own bars, which Overview's chart names. */
	const labels = (blocks.find((block) => block.id === 'overview')?.chart?.bars ?? []).map(
		(bar) => bar.label
	);
	const panels = blocks
		.filter((block) => !DRAWN_ELSEWHERE.has(block.id))
		.flatMap((block) => panelsOf(block, labels));
	return FAMILIES.map((family) => ({
		family,
		panels: panels.filter((one) => one.family === family)
	})).filter((one) => one.panels.length > 0);
}

/** Whether a panel answers an address: its block's id lights every panel of the block. */
export function answers(panel: Panel, hash: string): boolean {
	return hash !== '' && (panel.id === hash || panel.block.id === hash);
}
