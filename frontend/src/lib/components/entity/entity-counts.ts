/* Which of an entity's numbers its wall card draws: the hover card's cells less the files and
 * Seen with, and only numbers the listing carries that are more than nought. */
import { DEFAULT_RAIL_ORDER } from '$lib/components/shell/nav';
import { counted, picturesSaid, withSize } from '$lib/entity/entity-counts';
import { nounFor, tabsFor, type EntityKind, type RelatedKind } from '$lib/entity/related.svelte';

/* Cells in the rail's order on every card, read off the rail's own list. */
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

/** A cell's number with the word for what it counts ("1,957 files"); null while unknown. */
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

/** A cell's figure without words, the Files cell with its size; null while unknown. */
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

/** Which cells stand in the card's one line and which fold into a "+n": numbers never shrink,
 * and the tail folds in rail order. Pure, so it is tested without a browser. */
export function fitCells(
	cells: readonly MeasuredCell[],
	room: number,
	gap: number,
	plus: number
): FittedCells {
	const along = (some: readonly MeasuredCell[]): number =>
		some.reduce((sum, one) => sum + one.width, 0) + gap * Math.max(0, some.length - 1);
	/* Half a pixel of slack for sub-pixel layout. */
	const fits = (width: number): boolean => width <= room + 0.5;

	if (fits(along(cells))) return { shown: cells.map((one) => one.id), folded: [] };

	/* As many as fit with the "+n" after them; none is a real answer. */
	let keep = cells.length - 1;
	while (keep > 0 && !fits(along(cells.slice(0, keep)) + gap + plus)) keep -= 1;
	return {
		shown: cells.slice(0, keep).map((one) => one.id),
		folded: cells.slice(keep).map((one) => one.id)
	};
}
