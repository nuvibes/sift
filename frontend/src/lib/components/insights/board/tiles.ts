/*
 * THE BOARD'S TILES: which tiles a period's answer is drawn as, where each stands and what card
 * each one is.
 *
 * Every tile is a `RecapCard` filled from the answer (`GET /api/insights`), so a tile on the board
 * and a card in a deck are one drawing. The first screen is the same slots for every answer of a
 * period, drawn as skeletons before it lands, so nothing moves when it does; the families' tiles
 * follow it. The order is the reading order: the grid places each tile in the first room it fits.
 */
import type { components } from '$lib/api/schema';
import type { Family } from '$lib/components/insights/cards/family';
import { drawsAnything } from '$lib/components/insights/figures';
import {
	dayAway,
	type InsightsBlock,
	type InsightsPage,
	type Span
} from '$lib/components/insights/period';
import { BOARD_WORDS } from '$lib/components/insights/words';
import type { RecapCard } from '$lib/library/recaps.svelte';

type Figure = components['schemas']['Figure'];
type NamedList = components['schemas']['NamedList'];
type NamedRow = components['schemas']['NamedRow'];
type Kind = RecapCard['kind'];

/** A tile's size in grid units, columns by rows. */
export type Shape = '1x1' | '2x1' | '2x2' | '4x2' | '6x2';

export interface Tile {
	/** Which tile this is, unique on the board. */
	slot: string;
	shape: Shape;
	family: Family;
	/** Where on Stats the tile opens: its block, or one table of it (`most_viewed-person`). */
	block: string;
	/** The tile's own title. */
	title: string;
	/** Null while the answer is on its way: a skeleton of the same size. */
	card: RecapCard | null;
}

/** The first screen's slots, by period: the same before and after the answer lands. */
const FIRST: readonly { slot: string; shape: Shape; family: Family; block: string }[] = [
	{ slot: 'headline', shape: '2x2', family: 'viewing', block: 'overview' },
	{ slot: 'viewed', shape: '4x2', family: 'viewing', block: 'overview' },
	{ slot: 'people', shape: '2x2', family: 'people', block: 'most_viewed-person' },
	{ slot: 'top_file', shape: '2x2', family: 'viewing', block: 'most_viewed-asset' },
	{ slot: 'sites', shape: '2x1', family: 'sites', block: 'most_viewed-site' },
	{ slot: 'days', shape: '2x1', family: 'viewing', block: 'overview-days' },
	{ slot: 'imported', shape: '1x1', family: 'downloads', block: 'arrived' },
	{ slot: 'visits', shape: '1x1', family: 'viewing', block: 'sittings' },
	{ slot: 'sessions', shape: '1x1', family: 'viewing', block: 'overview' },
	{ slot: 'third', shape: '1x1', family: 'viewing', block: 'overview' }
];

/** A day and the whole record have no days to draw: the 2x1 beside the Sites holds When instead. */
const HAS_DAYS: ReadonlySet<Span> = new Set(['week', 'month', 'year']);

function slotsOf(period: Span) {
	return FIRST.map((one) => {
		if (one.slot !== 'days') return one;
		if (!HAS_DAYS.has(period)) return { ...one, slot: 'when', block: 'when' };
		/* A week's days are its bars: it has no calendar of its own on Stats. */
		return period === 'week' ? { ...one, block: 'overview-bars' } : one;
	});
}

/** The board before its answer: the first screen's tiles, empty. */
export function skeletonOf(period: Span): Tile[] {
	return slotsOf(period).map((one) => ({ ...one, title: '', card: null }));
}

function cardOf(id: string, kind: Kind, over: Partial<RecapCard> = {}): RecapCard {
	return {
		id,
		kind,
		headline: [],
		context: [],
		wall: null,
		accent_hue: null,
		statement: [],
		figure: null,
		figures: [],
		cover: null,
		rows: [],
		chart: null,
		calendar: null,
		hidden: false,
		hidden_things: [],
		...over
	};
}

/** A row's figure as a card's figure, under `label`. */
function figureOfRow(row: NamedRow, label: string): Figure {
	return {
		label,
		value: row.value,
		unit: row.unit,
		said: row.said,
		hidden_said: '',
		hidden_part: 0,
		caption: [],
		defines: [],
		trend: []
	};
}

/** The list whose rows are all of one kind of thing: `person`, `site`, `photo_set`, `file`. */
function listOf(block: InsightsBlock | undefined, cover: string): NamedList | null {
	return block?.lists.find((list) => list.rows[0]?.cover?.startsWith(cover)) ?? null;
}

/** A week's or a month's days from its bars, one bar a day from the first; a year's as sent. */
function daysOf(answer: InsightsPage, overview: InsightsBlock | undefined) {
	if (!overview) return null;
	if (answer.period === 'year') return overview.calendar ?? null;
	const bars = overview.chart?.bars ?? [];
	if (bars.length === 0) return null;
	return {
		unit: overview.chart?.unit ?? 'ms',
		days: bars.map((bar, at) => ({
			day: dayAway(answer.from, at),
			said: bar.said,
			value: bar.parts.reduce((sum, part) => sum + part.value, 0)
		}))
	} satisfies RecapCard['calendar'];
}

type Line = RecapCard['statement'];

/** Whether two sentences read the same: the headline's tile already says it. */
function sameLine(one: Line | undefined, other: Line): boolean {
	const text = (line: Line) => line.map((piece) => piece.text).join('');
	return one !== undefined && text(one) === text(other);
}

/** What a block says below its floor, as a tile's card: its one line. */
function quiet(id: string, kind: Kind, block: InsightsBlock): RecapCard {
	return cardOf(id, kind, { statement: block.statements[0] ?? [] });
}

/** The overview's tiles: the time viewed with its bars, its other two figures and its days. */
function overviewCards(
	answer: InsightsPage,
	overview: InsightsBlock,
	headline: Line,
	out: Map<string, RecapCard>
): void {
	const said = overview.statements[0];
	const viewed = cardOf('viewed', 'compared', {
		figure: overview.figures[0] ?? null,
		chart: overview.chart,
		statement: sameLine(said, headline) ? (overview.chart?.caption ?? []) : (said ?? [])
	});
	out.set('viewed', overview.floor_reached ? viewed : quiet('viewed', 'compared', overview));
	const [, second, third] = overview.figures;
	if (second) out.set('sessions', cardOf('sessions', 'headline', { figure: second }));
	if (third) out.set('third', cardOf('third', 'headline', { figure: third }));
	const days = daysOf(answer, overview);
	if (days) out.set('days', cardOf('days', 'heatmap', { calendar: days }));
}

/** The most-viewed lists' tiles: the top People, the top file, the Sites. */
function listCards(most: InsightsBlock | undefined, out: Map<string, RecapCard>): void {
	const people = listOf(most, '/api/people/');
	if (people) out.set('people', cardOf('people', 'top_five', { rows: people.rows.slice(0, 5) }));
	const top = listOf(most, '/api/assets/')?.rows[0];
	if (top && most) {
		const figure = figureOfRow(top, most.title);
		out.set(
			'top_file',
			cardOf('top_file', 'top_file', { cover: top.cover, figure, statement: [top.piece] })
		);
	}
	const sites = listOf(most, '/api/sites/');
	if (sites) {
		const rows = sites.rows.slice(0, 5);
		out.set('sites', cardOf('sites', 'top_site', { rows, cover: rows[0]?.cover }));
	}
}

/** A block's first figure as a small tile's card. */
function counted(
	slot: string,
	kind: Kind,
	block: InsightsBlock | undefined,
	out: Map<string, RecapCard>
) {
	const figure = block?.figures[0];
	if (figure) out.set(slot, cardOf(slot, kind, { figure }));
}

/** When, as the 2x1 a day and the whole record stand beside the Sites. */
function whenCard(when: InsightsBlock): RecapCard {
	if (!when.floor_reached) return quiet('when', 'when', when);
	const [figure, ...figures] = when.figures;
	return cardOf('when', 'when', { figure: figure ?? null, figures, chart: when.chart });
}

/** The first screen's cards, by slot. */
function firstCards(answer: InsightsPage, by: Map<string, InsightsBlock>): Map<string, RecapCard> {
	const overview = by.get('overview');
	const out = new Map<string, RecapCard>();
	const headline = answer.first_sentences[0] ?? overview?.statements[0] ?? [];
	out.set('headline', cardOf('headline', 'headline', { statement: headline }));
	if (overview) overviewCards(answer, overview, headline, out);
	listCards(by.get('most_viewed'), out);
	counted('imported', 'sift_did', by.get('arrived'), out);
	counted('visits', 'session', by.get('sittings'), out);
	const when = by.get('when');
	if (when) out.set('when', whenCard(when));
	return out;
}

/** The families' tiles, in the board's order: viewing, People, Sites, organizing, Theater, the
 *  library, then Alongside. A block with nothing to draw has no tile. */
function familyTiles(answer: InsightsPage, by: Map<string, InsightsBlock>): Tile[] {
	const out: Tile[] = [];
	const add = (
		block: InsightsBlock | undefined,
		tile: Omit<Tile, 'block' | 'title'> & { title?: string; table?: string }
	) => {
		if (!block || !tile.card) return;
		const { table, ...rest } = tile;
		out.push({
			...rest,
			block: table ? `${block.id}-${table}` : block.id,
			title: tile.title ?? block.title
		});
	};
	const drawn = (id: string) => {
		const block = by.get(id);
		return block && block.floor_reached && drawsAnything(block) ? block : undefined;
	};
	const facts = (id: string, kind: Kind, block: InsightsBlock | undefined, over = {}) =>
		block
			? cardOf(id, kind, {
					figures: block.figures,
					chart: block.chart,
					statement: block.statements[0] ?? [],
					...over
				})
			: null;

	const byKind = drawn('by_kind');
	add(byKind, {
		slot: 'by_kind',
		shape: '4x2',
		family: 'viewing',
		card: facts('by_kind', 'headline', byKind)
	});
	const when = HAS_DAYS.has(answer.period) ? drawn('when') : undefined;
	add(when, { slot: 'when', shape: '4x2', family: 'viewing', card: facts('when', 'when', when) });
	const most = drawn('most_viewed');
	const sets = listOf(most, '/api/photo-sets/');
	if (sets) {
		add(most, {
			slot: 'photo_sets',
			table: 'photo_set',
			title: sets.title,
			shape: '2x2',
			family: 'viewing',
			card: cardOf('photo_sets', 'top_five', { rows: sets.rows.slice(0, 5) })
		});
	}
	const sittings = drawn('sittings');
	const first = sittings?.lists[0];
	if (first && first.rows.length > 0) {
		add(sittings, {
			slot: 'first_opened',
			table: 'person',
			title: first.title,
			shape: '2x2',
			family: 'people',
			card: cardOf('first_opened', 'top_five', { rows: first.rows.slice(0, 5) })
		});
	}
	const organizing = drawn('organizing');
	add(organizing, {
		slot: 'organizing',
		shape: '4x2',
		family: 'organizing',
		card: facts('organizing', 'sift_did', organizing, { rows: organizing?.lists[0]?.rows ?? [] })
	});
	const opinions = drawn('opinions');
	add(opinions, {
		slot: 'opinions',
		shape: '2x2',
		family: 'organizing',
		card: facts('opinions', 'rated', opinions)
	});
	const theater = drawn('theater');
	add(theater, {
		slot: 'theater',
		shape: '4x2',
		family: 'theater',
		card: facts('theater', 'theater', theater, {
			figure: theater?.figures[0] ?? null,
			figures: theater?.figures.slice(1) ?? []
		})
	});
	const layouts = theater?.lists[0];
	if (layouts && layouts.rows.length > 0) {
		add(theater, {
			slot: 'theater_files',
			table: 'wall',
			title: layouts.title,
			shape: '2x2',
			family: 'theater',
			card: cardOf('theater_files', 'theater_files', { rows: layouts.rows.slice(0, 5) })
		});
	}
	const arrived = drawn('arrived');
	add(arrived, {
		slot: 'library',
		shape: '4x2',
		family: 'downloads',
		card: facts('library', 'downloads', arrived, { rows: arrived?.lists[0]?.rows ?? [] })
	});
	const machine = drawn('machine');
	add(machine, {
		slot: 'machine',
		shape: '4x2',
		family: 'downloads',
		card: facts('machine', 'sift_did', machine, { rows: machine?.lists[0]?.rows ?? [] })
	});
	/* Alongside is looked for by name while the viewing is past its floor, so below its own floor
	   it says so in its one tile. */
	const alongside = by.get('overview')?.floor_reached ? by.get('alongside') : undefined;
	if (alongside && !alongside.floor_reached) {
		add(alongside, {
			slot: 'alongside',
			shape: '6x2',
			family: 'alongside',
			card: quiet('alongside', 'alongside', alongside)
		});
	} else if (alongside) {
		alongside.statements.forEach((line, at) => {
			const pair = alongside.figures.length === 2 * alongside.statements.length;
			add(alongside, {
				slot: `alongside_${at}`,
				shape: '6x2',
				family: 'alongside',
				card: cardOf(`alongside_${at}`, 'alongside', {
					statement: line,
					figures: pair ? alongside.figures.slice(2 * at, 2 * at + 2) : []
				})
			});
		});
	}
	return out;
}

/** The board for a period's answer: the first screen, then the families. A guest's answer has no
 *  What Sift did, and so no tile for it. */
export function tilesOf(answer: InsightsPage): Tile[] {
	const by = new Map(answer.blocks.map((block) => [block.id, block]));
	const first = firstCards(answer, by);
	const titles: Record<string, string> = {
		headline: BOARD_WORDS.headline[answer.period],
		viewed: BOARD_WORDS.bars[answer.period],
		days: BOARD_WORDS.days,
		top_file: by.get('most_viewed')?.title ?? '',
		people: listOf(by.get('most_viewed'), '/api/people/')?.title ?? '',
		sites: listOf(by.get('most_viewed'), '/api/sites/')?.title ?? ''
	};
	const screen = slotsOf(answer.period).map((one) => {
		const block = by.get(one.block.split('-')[0]);
		return {
			...one,
			title: titles[one.slot] ?? block?.title ?? '',
			card:
				first.get(one.slot) ??
				(block ? quiet(one.slot, 'headline', block) : cardOf(one.slot, 'headline'))
		};
	});
	return [...screen, ...familyTiles(answer, by)];
}
