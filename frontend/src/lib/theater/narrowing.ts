/*
 * WHAT "FILTER THIS" MEANS ON A WALL, and why the answer is not symmetrical.
 *
 * A wall has up to nine sources and none is the address, so the shared panel filters a CELL. It
 * READS from the cell the keyboard is on (a count needs one query) and WRITES to every cell the
 * wall is addressing (the All chip). With one cell chosen the two agree. A function of the wall, so
 * this surprising asymmetry is held by a test without a mounted page.
 */

import type { Narrowing } from '$lib/components/shell/screen-bar.svelte';
import { aims, aimsAtChosen } from './aim';
import type { Wall } from './wall.svelte';

/**
 * Which cell the panel is EDITING: the one the keyboard is on, clamped, because the wall can
 * shrink under an open panel before `reshape` pulls `focused` back.
 */
export function editingCell(wall: Wall): number {
	return Math.min(wall.focused, wall.cells.length - 1);
}

/** The panel's two halves, for the wall it is drawn over. See this file's own note for the shape. */
export function narrowingFor(wall: Wall): Narrowing {
	return {
		read: () => wall.cells[editingCell(wall)]?.narrowing ?? new URLSearchParams(),
		/* The wall's hold travels with the filter, which a cell cannot see (`Cell.narrowTo`). */
		write: (next) =>
			wall.addressed.forEach((cell) => cell.narrowTo(next, { playing: !wall.paused })),
		/*
		 * WHAT POINTING AT A FACET LIGHTS: the cells the write above lands on, by the bar's own
		 * `aimsAtChosen`, so the wash and the write cannot name different cells. Built once, since
		 * `aims` remembers which element put the mark up.
		 */
		pointing: aims(
			wall,
			aimsAtChosen(wall, () => editingCell(wall))
		),
		/* The cell's media kinds are its own control's: every column is counted within them. */
		within: () => withinOf(wall.cells[editingCell(wall)]?.query ?? {})
	};
}

/** The part of a cell's own query the panel counts within and never writes: its media kinds. */
function withinOf(query: Record<string, string>): URLSearchParams {
	return new URLSearchParams(query.media ? { media: query.media } : {});
}

/** What the chips above the wall are about: one cell by number, or all of them. */
export function narrowingName(wall: Wall): string {
	return wall.everyCell ? 'Every cell' : `Cell ${editingCell(wall) + 1}`;
}
