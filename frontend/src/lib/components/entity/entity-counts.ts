/*
 * Which of an entity's numbers its wall card draws, as cells for `EntityCounts`.
 *
 * The hover card draws every tab of the page it links to. The wall card leaves two out: the files,
 * because the card already says them in words under the name ("1,200 files"), and a person's Seen
 * with.
 *
 * A cell is drawn only for a number the wall's listing actually carries, and only when it is more
 * than nothing. A tab whose count is not on the listing row draws nothing rather than a dash: on a
 * hover card a dash means "not known yet" and the card goes and asks, but on a wall of sixty cards
 * nothing asks, so a dash would be a permanent claim the number is unknown. A nought draws nothing
 * either: a row of five figures where three say 0 is a row somebody reads for the two that matter,
 * and the tab strip of the page follows the same rule (a tab with nothing in it shows no number).
 */
import { DEFAULT_RAIL_ORDER } from '$lib/components/shell/nav';
import { counted, picturesSaid, withSize } from '$lib/entity/entity-counts';
import { nounFor, tabsFor, type EntityKind, type RelatedKind } from '$lib/entity/related.svelte';

/*
 * The cells come in the rail's order, on every card. Each kind's tab strip has its own order, so
 * following it would make the same row read differently from card to card; the rail is the one
 * order everybody has learned, and reading it off the rail's own list keeps the two from drifting.
 * A cell with no rail item (a site's Sites within) sorts with its kind's rail item.
 */
const RAIL_ITEM_OF: Record<string, string> = {
	files: 'browse',
	people: 'people',
	sites: 'sites',
	sites_within: 'sites',
	tags_within: 'tags',
	collections: 'collections',
	photo_sets: 'photo-sets',
	loops: 'loops',
	tags: 'tags',
	songs: 'songs'
};

function railRank(cellId: string): number {
	const at = DEFAULT_RAIL_ORDER.indexOf(RAIL_ITEM_OF[cellId] ?? cellId);
	return at === -1 ? Number.MAX_SAFE_INTEGER : at;
}

/** One cell: exactly what `tabsFor` hands back for one tab. */
export type CountCell = ReturnType<typeof tabsFor>[number];

/** The numbers a listing row carries, keyed the way `tabsFor` reads them. */
type CardCounts = NonNullable<Parameters<typeof tabsFor>[3]>;

export function cardCells(
	kind: EntityKind,
	id: string,
	href: string,
	counts: CardCounts
): CountCell[] {
	return tabsFor(kind, id, href, counts)
		.filter(
			(cell) =>
				cell.id !== 'files' && !(kind === 'person' && cell.id === 'people') && (cell.count ?? 0) > 0
		)
		.sort((a, b) => railRank(a.id) - railRank(b.id));
}

/**
 * A cell's number with the word for what it counts: "1,957 files", "1 Photo Set", "Seen with 2
 * people". Null while the number is not known.
 *
 * The two tabs whose label is not their kind's noun are said by their label: a Photo Set's files
 * are its pictures, and a person's People tab is who they are seen with.
 */
export function cellSaid(cell: CountCell): string | null {
	const count = cell.count;
	if (count === undefined || count === null) return null;
	if (cell.id === 'files' && cell.label === 'Pictures') {
		return withSize(picturesSaid(count), count, cell.size);
	}
	if (cell.id === 'people' && cell.label === 'Seen with') {
		return `Seen with ${counted(count)} ${count === 1 ? 'person' : 'people'}`;
	}
	const noun = nounFor(cell.id as RelatedKind);
	return withSize(`${counted(count)} ${count === 1 ? noun.one : noun.many}`, count, cell.size);
}

/**
 * A cell's figure as the row draws it with no words: the number, and for the Files cell the size
 * of those files beside it ("1,957 \u00b7 23 GB"). Null while the number is not known.
 *
 * The size is said where there is room for it (the hover card, which wraps); the wall card never
 * hands the Files cell in, because its own line under the name already says both.
 */
export function cellFigure(cell: CountCell): string | null {
	const count = cell.count;
	if (count === undefined || count === null) return null;
	return withSize(counted(count), count, cell.size);
}

/** One cell as it was MEASURED: its id and how wide it came out. */
export interface MeasuredCell {
	id: string;
	width: number;
}

/** Which cells a card draws in its one line, and which fold into the "+n" at its end. */
export interface FittedCells {
	shown: string[];
	folded: string[];
}

/**
 * The card's row of numbers is one line, always: which cells stand in it and which fold away.
 *
 * A person's five cells need about 230px, the card's row has about 260 at the default size, and at
 * the smallest size a card is 200 wide, where five cannot fit. Left to wrap, which cards grew a
 * second row would depend on the window and the digits, and a wall of equal cards would lose its
 * shape.
 *
 * The numbers never shrink to make room: one size at every notch is the tiles' own rule, and a
 * figure drawn smaller reads as less. So cells leave instead:
 *
 * 1. Everything fits: everything is drawn. (No nought is ever handed in: `cardCells` leaves them
 *    out, so every cell here is something to open.)
 * 2. It does not: the tail folds into one "+n" cell, keeping the rail's order so what stays in
 *    front is what the rail puts first. Hovering it names what it holds; pressing it opens the
 *    page, where every tab is.
 *
 * `room` is the row's own width, `gap` the space between two cells, `plus` the "+n" cell's width.
 * Pure, so the rule is tested at the card rungs without a browser.
 */
export function fitCells(
	cells: readonly MeasuredCell[],
	room: number,
	gap: number,
	plus: number
): FittedCells {
	const along = (some: readonly MeasuredCell[]): number =>
		some.reduce((sum, one) => sum + one.width, 0) + gap * Math.max(0, some.length - 1);
	/* Half a pixel of slack: sub-pixel layout can measure a row that fits exactly as a fraction
	   wider than its box, which would fold a cell off a card where nothing overflows. */
	const fits = (width: number): boolean => width <= room + 0.5;

	if (fits(along(cells))) return { shown: cells.map((one) => one.id), folded: [] };

	/* As many from the front as fit WITH the "+n" after them. None at all is a real answer on a
	   card too narrow for one cell and the fold: the fold alone still says there is more. */
	let keep = cells.length - 1;
	while (keep > 0 && !fits(along(cells.slice(0, keep)) + gap + plus)) keep -= 1;
	return {
		shown: cells.slice(0, keep).map((one) => one.id),
		folded: cells.slice(keep).map((one) => one.id)
	};
}
