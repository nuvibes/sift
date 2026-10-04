/* Where the tile size is kept, and what is refused on the way back in.
 *
 * A `Grid` is built per screen, so a size held on the `Grid` alone would be thrown away by moving
 * the slider and then going anywhere: every tile snapping back, with nothing saying it would,
 * which reads as the control not working.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SIZE_STEPS } from './justify';

const KEY = 'sift.grid.rowHeight';

/** A fresh copy of the module, so the size is read the way a new screen reads it. */
async function freshGrid() {
	vi.resetModules();
	const { Grid } = await import('./grid.svelte');
	return new Grid();
}

beforeEach(() => localStorage.clear());
afterEach(() => {
	vi.restoreAllMocks();
	localStorage.clear();
});

describe('the size a new screen starts at', () => {
	it('is the breakpoint default when nothing has been chosen', async () => {
		const grid = await freshGrid();

		expect(grid.sizeOverride).toBeNull();
	});

	it('and the remembered one when it has', async () => {
		localStorage.setItem(KEY, String(SIZE_STEPS[2]));

		const grid = await freshGrid();

		expect(grid.sizeOverride).toBe(SIZE_STEPS[2]);
	});

	it('refusing a height no notch on the slider corresponds to', async () => {
		/* Only a value the control could have produced. Anything else (another tab, an older
		 * version, somebody in the console) would lay every row out to a height the size control
		 * cannot then show as selected, so it reads as "not set" instead. */
		localStorage.setItem(KEY, '199');

		const grid = await freshGrid();

		expect(grid.sizeOverride).toBeNull();
	});

	it('and refusing something that is not a number at all', async () => {
		localStorage.setItem(KEY, 'large please');

		const grid = await freshGrid();

		expect(grid.sizeOverride).toBeNull();
	});
});

describe('choosing a size', () => {
	it('writes it down, so the next screen starts there', async () => {
		vi.resetModules();
		const { rememberSize } = await import('./grid.svelte');

		rememberSize(SIZE_STEPS[3]);

		expect(localStorage.getItem(KEY)).toBe(String(SIZE_STEPS[3]));
		expect((await freshGrid()).sizeOverride).toBe(SIZE_STEPS[3]);
	});

	it('and does not throw when the browser will not store anything', async () => {
		// Private windows, a full quota and storage turned off all throw from `setItem`. A grid that
		// stopped working because it could not write a preference down would be trading the feature
		// for the memory of it.
		vi.resetModules();
		const { rememberSize } = await import('./grid.svelte');
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('quota');
		});

		expect(() => rememberSize(SIZE_STEPS[1])).not.toThrow();
	});

	it('and starts at the default when even reading is refused', async () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});

		expect((await freshGrid()).sizeOverride).toBeNull();
	});
});

describe('a row the screen is no longer about', () => {
	/** One item, only the fields this behaviour reads. */
	function row(id: string, favorite: boolean) {
		return {
			id,
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 1000,
			favorite,
			pinned: false,
			rating: null,
			concealed: false,
			thumb: true,
			preview: false,
			verdict: null,
			art: null,
			original_filename: `${id}.mp4`,
			hidden: false,
			hidden_here: false,
			shared: false,
			shared_here: false,
			restricted: false,
			restricted_here: false,
			unreachable: false,
			views: 0,
			// Nowhere worth going back to. A row is a whole `AssetSummary`, so every field it
			// declares is one this has to carry, and null is what the server sends for a file
			// nobody is part-way through, which is all of them here.
			resume_ms: null,
			// Nothing counted, which is what every file in a library is until somebody presses it.
			o_count: 0,
			left_out: null,
			// No swap refuses it: nothing is kept local or set to Don't swap here.
			swap_refused: false
		};
	}

	async function gridOf(...items: ReturnType<typeof row>[]) {
		const grid = await freshGrid();
		grid.items = items;
		grid.total = items.length;
		return grid;
	}

	it('leaves at once, and takes the total with it', async () => {
		// Unfavouriting on the Favorites screen must take the tile away, not leave it until a
		// reload.
		const grid = await gridOf(row('a', true), row('b', true));

		grid.setState('a', { favorite: false, rating: null }, (item) => item.favorite);

		expect(grid.items.map((item) => item.id)).toEqual(['b']);
		// A count that outlived the row would describe a set with one more thing in it than the page.
		expect(grid.total).toBe(1);
	});

	it('stays when it still belongs', async () => {
		const grid = await gridOf(row('a', true));

		grid.setState('a', { favorite: true, rating: 4 }, (item) => item.favorite);

		expect(grid.items).toHaveLength(1);
		expect(grid.items[0].rating).toBe(4);
		expect(grid.total).toBe(1);
	});

	it('and stays on a screen that asks no such question', async () => {
		// Browse has no favorite filter, so taking a heart off changes the tile and nothing else.
		const grid = await gridOf(row('a', true));

		grid.setState('a', { favorite: false, rating: null });

		expect(grid.items).toHaveLength(1);
		expect(grid.items[0].favorite).toBe(false);
		expect(grid.total).toBe(1);
	});

	it('never takes the total below zero', async () => {
		const grid = await gridOf(row('a', true));
		grid.total = 0;

		grid.setState('a', { favorite: false, rating: null }, (item) => item.favorite);

		expect(grid.total).toBe(0);
	});
});

describe('the stretch a row names when it is opened', () => {
	/* One file drawn two ways is one of them being wrong.
	 *
	 * A loop cut as a clip covers the whole of itself. Opened from Browse it plays as an ordinary
	 * two-second video; opened from the Loops wall with its bounds handed over, the SAME file would
	 * arrive with the A-B pair armed across its entire bar (two handles pinned at the ends and a
	 * marked-out region behind the scrubber), reading as a piece carved out of something longer,
	 * which is exactly what it is not.
	 */
	async function stretch() {
		vi.resetModules();
		const { stretchOf } = await import('./grid.svelte');
		return stretchOf;
	}

	it('is nothing at all for a row that IS its file', async () => {
		const stretchOf = await stretch();

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		expect(stretchOf({ whole: true, start_ms: 0, end_ms: 2_202 } as any)).toEqual({});
	});

	it('is the two ends for a MARK, which is a piece of something longer', async () => {
		const stretchOf = await stretch();

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		expect(stretchOf({ whole: false, start_ms: 4_371, end_ms: 6_573 } as any)).toEqual({
			at: 4_371,
			until: 6_573
		});
	});

	it('is nothing for an ordinary file, which names no stretch and has no such field', async () => {
		const stretchOf = await stretch();

		// A wall of FILES sends no `whole` and no bounds, so both ends come back undefined, which
		// is what every tile outside the Loops wall has always handed over.
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		expect(stretchOf({ id: 'a1' } as any)).toEqual({ at: undefined, until: undefined });
	});
});

describe('a wall whose rows carry a name', () => {
	it('counts the line under each row when it fits whole rows to a screen', async () => {
		vi.resetModules();
		const { Grid, LOOP_SOURCE, ASSET_SOURCE } = await import('./grid.svelte');
		const { CAPTION_HEIGHT } = await import('./justify');
		const named = new Grid(LOOP_SOURCE);
		const files = new Grid(ASSET_SOURCE);
		for (const grid of [named, files]) {
			// Tall enough for many rows, where a line left out of the count adds up to a whole row.
			grid.containerWidth = 1200;
			grid.screenHeight = 2000;
		}

		expect(named.caption).toBe(CAPTION_HEIGHT);
		expect(files.caption).toBe(0);
		// Rows, their lines and the gutters between them fit the screen, and nothing is cut.
		const used = (grid: typeof named) =>
			grid.rowsPerScreen * (grid.rowHeight + grid.caption) + (grid.rowsPerScreen - 1) * grid.gutter;
		expect(used(named)).toBeLessThanOrEqual(2000);
		expect(used(files)).toBeLessThanOrEqual(2000);
	});

	it('takes the line on a wall of files asked why each was left out, and only there', async () => {
		/* The Importing pane's "24 files ... are left out" opens the Files wall filtered to them,
		   and each says WHY on the line a mark's name is drawn on. Asked by the link, the wall has
		   the line from its first page; typed into the box, the rows say it as they land. */
		vi.resetModules();
		const { Grid, ASSET_SOURCE } = await import('./grid.svelte');
		const { CAPTION_HEIGHT } = await import('./justify');
		const { asksWhyLeftOut } = await import('$lib/library/left-out');

		const asked = new Grid(ASSET_SOURCE);
		asked.leftOutAsked = asksWhyLeftOut({ left_out: 'thumbnails' });
		expect(asked.caption).toBe(CAPTION_HEIGHT);

		const typed = new Grid(ASSET_SOURCE);
		expect(typed.caption).toBe(0);
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		typed.items = [{ id: 'a1', left_out: "It wouldn't open." } as any];
		expect(typed.caption).toBe(CAPTION_HEIGHT);

		// Not a wall of left-out files: excluding them, or any other filter, carries no line.
		expect(asksWhyLeftOut({ left_out: '-thumbnails' })).toBe(false);
		expect(asksWhyLeftOut({ tags: 'beach' })).toBe(false);
		const plain = new Grid(ASSET_SOURCE);
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		plain.items = [{ id: 'a1', left_out: null } as any];
		expect(plain.caption).toBe(0);
	});
});

describe('what every row of a wall is by definition', () => {
	/* The bar leaves a column out where every file on the wall has one value of it, and these two
	   walls are the ones made of such a value: the hearted files and the files with a Loop. */
	it('says Favorites is the hearted files and Loops the files with a Loop', async () => {
		const { FAVORITES_SOURCE, LOOP_SOURCE, ASSET_SOURCE } = await import('./grid.svelte');

		expect(FAVORITES_SOURCE.files).toEqual({ fav: 'yes' });
		expect(LOOP_SOURCE.files).toEqual({ loops: 'any' });
		expect(ASSET_SOURCE.files, 'Browse leaves out no column').toBeUndefined();
	});
});
