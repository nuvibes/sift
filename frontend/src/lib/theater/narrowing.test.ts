/*
 * THE FILTER PANEL READS ONE CELL AND WRITES ALL OF THEM.
 *
 * The asymmetry is deliberate and it is the kind that gets straightened out later by somebody who
 * reads only one half of it: the panel counts against a query and a wall of nine sources has no
 * single query to count against, so the columns show one cell, while applying lands on every cell
 * the wall is addressing, which is the whole of what the All chip means.
 *
 * With one cell selected the two are identical, which is every case but the deliberate one. It is
 * a function of the wall rather than of `+page.svelte`, where reaching it would need the router,
 * the shell stores and a mounted screen bar.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ items: [], total: 0 })), post: vi.fn(async () => ({})) }
}));

import { editingCell, narrowingFor, narrowingName } from './narrowing';
import { Wall } from './wall.svelte';
import type { Cell } from './cell.svelte';

let wall: Wall;

beforeEach(() => {
	wall = new Wall();
	wall.setLayout('grid');
});

describe('which cell the panel is editing', () => {
	it('is the one the keyboard is on', () => {
		wall.focus(2);

		expect(editingCell(wall)).toBe(2);
		expect(narrowingName(wall)).toBe('Cell 3');
	});

	it('says every cell while the wall is addressed as a whole', () => {
		wall.focusEvery();

		expect(narrowingName(wall), 'the chips are about all of them').toBe('All cells');
	});

	/* A wall can SHRINK under an open panel (a layout with fewer places, a preview dropped), and
	   `focused` is allowed to be past the end for the moment before the shape pulls it back. Read
	   off the end, the panel gets an empty filter and draws it as if it were the cell's own. */
	it('never points past the end of a wall that has just got smaller', () => {
		wall.focus(3);
		wall.focused = 8;

		expect(editingCell(wall)).toBe(wall.cells.length - 1);
		expect(narrowingFor(wall).read().toString(), 'and it read a real cell').toBe(
			wall.cells[wall.cells.length - 1].narrowing.toString()
		);
	});
});

describe('reading one and writing all', () => {
	it('reads the cell the keyboard is on, not the whole wall', () => {
		wall.cells[0].narrowTo(new URLSearchParams({ q: 'first' }));
		wall.cells[2].narrowTo(new URLSearchParams({ q: 'third' }));
		wall.focus(2);

		expect(narrowingFor(wall).read().get('q')).toBe('third');
	});

	it('writes only that cell while one cell is chosen', () => {
		wall.focus(1);

		narrowingFor(wall).write(new URLSearchParams({ q: 'cats' }));

		expect(wall.cells.map((cell) => cell.source)).toEqual(['', 'cats', '', '']);
	});

	/* THE DELIBERATE CASE, and the only one where the two halves differ. */
	it('writes every cell while the whole wall is addressed, and still reads one', () => {
		wall.cells[0].narrowTo(new URLSearchParams({ q: 'the one on screen' }));
		wall.focus(0);
		wall.focusEvery();

		const panel = narrowingFor(wall);
		expect(panel.read().get('q'), 'the columns count against ONE query').toBe('the one on screen');

		panel.write(new URLSearchParams({ q: 'all of them' }));

		expect(
			wall.cells.map((cell) => cell.source),
			'applying landed on every cell the wall is addressing'
		).toEqual(['all of them', 'all of them', 'all of them', 'all of them']);
	});
});

/* A tick waits for each playing file to end; a saved filter is a finished choice and lands now. */
describe('a saved filter chosen with every cell addressed', () => {
	it('starts on every cell immediately, where a tick waits for each file', () => {
		wall.cells.forEach((cell, at) => {
			cell.playing = { id: `file-${at}`, media_type: 'video' } as Cell['playing'];
		});
		wall.togglePause();
		wall.focusEvery();
		const panel = narrowingFor(wall);

		panel.write(new URLSearchParams({ q: 'ticked' }));
		expect(wall.cells.map((cell) => cell.narrowingWaits)).toEqual([true, true, true, true]);

		panel.choose(new URLSearchParams({ q: 'kept' }));
		expect(
			wall.cells.map((cell) => [cell.source, cell.narrowingWaits]),
			'the saved filter reached one cell, or waited'
		).toEqual(Array(4).fill(['kept', false]));
	});
});

/*
 * POINTING AT A FACET LIGHTS THE CELLS IT WILL FILTER, the same wash as pointing at the play or
 * skip button. The panel spreads what the filtering hands it (`FacetPanel`'s `pointing`), and what
 * it hands is `aims` aimed at the cells the write above lands on, so the wash and the write are
 * one answer.
 */
describe('pointing at something that narrows', () => {
	it('lights the one cell being edited, and lets go', () => {
		wall.focus(2);
		const { pointing } = narrowingFor(wall);

		pointing?.onmouseenter();
		expect(wall.aiming, 'pointing at a facet lit nothing on the wall').toBe(2);
		expect(wall.addressedAt, 'and it lit the cell the write lands on').toEqual([2]);

		pointing?.onmouseleave();
		expect(wall.aiming).toBeNull();
	});

	it('lights every cell while the whole wall is addressed', () => {
		wall.focusEvery();
		const { pointing } = narrowingFor(wall);

		pointing?.onmouseenter();
		expect(wall.aiming).toBe('every');
	});

	it('asks which cell at the moment the pointer lands, not when the panel was built', () => {
		const { pointing } = narrowingFor(wall);
		wall.focus(1);

		pointing?.onmouseenter();
		expect(wall.aiming, 'it marked the cell chosen when the narrowing was made').toBe(1);
	});
});

/*
 * THE PANEL COUNTS WITHIN WHAT THE CELL PLAYS. A cell that plays video and GIF offers no
 * photographs to pick, since picking one would empty the cell. The kinds are the cell's own
 * control, so they travel as `within`: counted, never written.
 */
describe('what the panel counts within', () => {
	it("is the edited cell's own media kinds, and nothing it would write", () => {
		wall.cells[1].mediaKind = 'all';
		wall.focus(0);
		const panel = narrowingFor(wall);

		expect(panel.within?.().get('media')).toBe('video|gif');
		expect(panel.read().has('media'), 'not a pick the panel holds').toBe(false);

		wall.focus(1);
		expect(panel.within?.().toString(), 'a cell that plays everything narrows nothing').toBe('');
	});
});
