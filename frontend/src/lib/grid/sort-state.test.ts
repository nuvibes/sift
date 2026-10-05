/* The vocabulary of orders, and the one place it is written down.
 *
 * Six of the file grid's orders apply to every kind of thing in the library, and four walls offer
 * them. They are TAKEN OUT of the grid's own list rather than written beside it, so the label
 * somebody reads on Browse and the label they read on Tags are one string rather than two that
 * happen to agree today.
 *
 * That is the whole thing under test here. It is not a test of arithmetic, it is a test that two
 * lists which must agree cannot drift: the failure that puts three names on one idea ("Most
 * seen", "Most used", "Most items" all meaning how many files this accounts for, the first of
 * them a wrong word besides).
 */

import { describe, expect, it } from 'vitest';

import {
	ARTIST_ORDER,
	DEFAULT_SORT,
	ENTITY_OPINION_SORTS,
	RESHUFFLE,
	RESHUFFLE_OPTION,
	SHUFFLE_SEED_LIMIT,
	SITE_ORDER,
	SORT_OPTIONS,
	UNIVERSAL_SORTS,
	mintSeed,
	sortIcon
} from '$lib/grid/sort-state.svelte';
import {
	NOTE_FITS,
	SIMILAR_TO_FILE,
	SIMILAR_TO_WORDS,
	SIMILARITY_NEEDS
} from '$lib/grid/sort-state.svelte';
import { LOOP_SOURCE } from '$lib/grid/grid.svelte';

describe('the orders every wall shares', () => {
	it('are entries taken out of the grid list, not a second copy', () => {
		/* Four of the six carry the grid's own label, so `Newest first` on the People wall is
		   literally the same string as `Newest first` on Browse rather than two that happen to
		   agree today. The two SIZE orders are the exception and they are checked by name below:
		   they measure bytes on a file and a count everywhere else, so one word for both would
		   tell neither wall what it sorts by. */
		const COUNTED = new Set(['largest', 'smallest']);
		const SUMMED = new Set(['largest_total', 'smallest_total', 'longest_total', 'shortest_total']);
		for (const shared of UNIVERSAL_SORTS) {
			if (SUMMED.has(shared.value)) continue;
			const grid = SORT_OPTIONS.find((option) => option.value === shared.value);
			expect(grid, `the grid has no order spelled ${shared.value}`).toBeDefined();
			if (!COUNTED.has(shared.value)) expect(shared.label).toBe(grid!.label);
		}
	});

	it('are the eleven that every kind of thing can answer, and only those', () => {
		// Written out, deliberately. Deriving this list the same way the module does would make the
		// test a second copy of the module and it could never disagree with it.
		expect(UNIVERSAL_SORTS.map((one) => one.value)).toEqual([
			'newest',
			'oldest',
			'edited',
			'name_az',
			'name_za',
			'largest',
			'smallest',
			'largest_total',
			'smallest_total',
			'longest_total',
			'shortest_total'
		]);
	});

	it('sum the bytes under a thing under their own words, and never on the file grid', () => {
		expect(UNIVERSAL_SORTS.find((one) => one.value === 'largest_total')?.label).toBe(
			'Largest in total'
		);
		expect(UNIVERSAL_SORTS.find((one) => one.value === 'smallest_total')?.label).toBe(
			'Smallest in total'
		);
		const grid = SORT_OPTIONS.map((one) => one.value as string);
		expect(grid).not.toContain('largest_total');
		expect(sortIcon('largest_total')).toBe('data_usage');
		expect(sortIcon('smallest_total')).toBe('data_usage');
	});

	it('sum the running time under a thing under their own words and the hourglass', () => {
		const label = (key: string) => UNIVERSAL_SORTS.find((one) => one.value === key)?.label;
		expect(label('longest_total')).toBe('Longest in total');
		expect(label('shortest_total')).toBe('Shortest in total');
		expect(SORT_OPTIONS.map((one) => one.value as string)).not.toContain('longest_total');
		expect(sortIcon('longest_total')).toBe('hourglass_arrow_up');
		expect(sortIcon('shortest_total')).toBe('hourglass_arrow_down');
	});

	it('offer Recently edited on every wall, under the one name and the pencil History draws', () => {
		/* The server keeps when each thing was last edited and every wall orders by it under the
		   one key, so the files, People, Sites, Tags, Collections and Photo Sets menus all carry
		   the row, spelled once. */
		const grid = SORT_OPTIONS.find((one) => one.value === 'edited');
		expect(grid?.label).toBe('Recently edited');
		expect(UNIVERSAL_SORTS.find((one) => one.value === 'edited')?.label).toBe('Recently edited');
		expect(sortIcon('edited')).toBe('edit');
		// The wall of marks declares its own orders rather than taking the whole list.
		expect(LOOP_SOURCE.sorts).toContain('edited');
	});

	it('leave duration out, because nothing totals the length behind a person or a tag', () => {
		const values = UNIVERSAL_SORTS.map((one) => one.value);
		expect(values).not.toContain('longest');
		expect(values).not.toContain('shortest');
	});

	it('word the size order for what the wall is actually counting', () => {
		/* "Largest file" is bytes and is exactly right on a wall of files. On a wall of tags the
		   same KEY orders by how many files are under each row, so that name would describe
		   something the server is not doing.

		   One vague word for both (`Biggest first`) would keep the vocabulary to one list and cost
		   both walls a name that says what they sort by. Two exact names for two measurements is
		   the answer. */
		expect(SORT_OPTIONS.find((one) => one.value === 'largest')?.label).toBe('Largest file');
		expect(UNIVERSAL_SORTS.find((one) => one.value === 'largest')?.label).toBe('Most files');
		expect(UNIVERSAL_SORTS.find((one) => one.value === 'smallest')?.label).toBe('Fewest files');
	});

	it('gives every order it offers a mark, and only the two name rows share one', () => {
		/* A menu of twelve words is read from the top every time. The glyph is what makes a row
		   findable by shape, and `sortIcon` is the one table that says which, so the Sort menu on
		   a wall of files and the one on a wall of people cannot draw different marks for the same
		   word. */
		for (const option of SORT_OPTIONS) {
			expect(sortIcon(option.value), `${option.value} has no mark`).toBeTruthy();
		}
		for (const option of [...ENTITY_OPINION_SORTS, ...UNIVERSAL_SORTS]) {
			expect(sortIcon(option.value), `${option.value} has no mark`).toBeTruthy();
		}
		/* The names have no direction to draw, so one alphabet serves both rows. */
		expect(sortIcon('name_az')).toBe(sortIcon('name_za'));
	});

	it('gives the orders one wall alone offers a mark too', () => {
		/* The Site order (the download queue) and the artist order (the Music wall) live outside
		   the two lists above, so the loop there never reaches them: an order with no mark would
		   stand bare at the foot of its menu, its words out of line with every row above it. The
		   artist order wears the Artists column's mark. */
		for (const option of [SITE_ORDER, ARTIST_ORDER]) {
			expect(sortIcon(option.value), `${option.value} has no mark`).toBeTruthy();
		}
		expect(sortIcon(ARTIST_ORDER.value)).toBe('person');
	});

	it('draws the four time orders as one family, told apart by shape and by direction', () => {
		/* Two rules in one. A CLOCK is when a file arrived and an HOURGLASS is how long it runs,
		   so the two questions are told apart before the words are read; the arrow says which
		   end, so no two of the four can be confused for each other.

		   `longest` and `shortest` sharing one glyph is the case this pins hardest: a mark that
		   is identical on two opposite rows is a mark that says nothing. */
		expect(sortIcon('newest')).toBe('clock_arrow_up');
		expect(sortIcon('oldest')).toBe('clock_arrow_down');
		expect(sortIcon('longest')).toBe('hourglass_arrow_up');
		expect(sortIcon('shortest')).toBe('hourglass_arrow_down');

		const time = ['newest', 'oldest', 'longest', 'shortest'].map(sortIcon);
		expect(new Set(time).size, 'two time orders share a mark').toBe(4);
	});
});

describe('the orders that need an opinion', () => {
	it('are declared once, beside the universal six, rather than in each wall', () => {
		expect(ENTITY_OPINION_SORTS.map((one) => one.value)).toEqual(['favorite', 'rating']);
	});

	it('are taken from the file grid, so Browse and every wall say them in one string', () => {
		for (const opinion of ENTITY_OPINION_SORTS) {
			const grid = SORT_OPTIONS.find((one) => one.value === opinion.value);
			expect(grid?.label, `the grid has no ${opinion.value}`).toBe(opinion.label);
		}
		expect(ENTITY_OPINION_SORTS.map((one) => one.label)).toEqual([
			'Favorites first',
			'Highest rated'
		]);
	});

	it('are kept out of the universal list, since a wall with no opinions cannot answer them', () => {
		const universal = UNIVERSAL_SORTS.map((one) => one.value);
		for (const opinion of ENTITY_OPINION_SORTS) expect(universal).not.toContain(opinion.value);
	});
});

describe('the whole list', () => {
	it('spells each order once', () => {
		const values = SORT_OPTIONS.map((one) => one.value);
		expect(new Set(values).size).toBe(values.length);
	});

	it('offers no two orders under one label', () => {
		const labels = SORT_OPTIONS.map((one) => one.label);
		expect(new Set(labels).size).toBe(labels.length);
	});

	it('contains the order the grid opens on', () => {
		expect(SORT_OPTIONS.map((one) => one.value)).toContain(DEFAULT_SORT);
	});
});

describe('asking to be shuffled again', () => {
	it('is not an order, so no list of orders carries it', () => {
		/* It is a press: the chooser refuses to deliver a press of the order already showing, and for
		   the one order that is a DRAW that press means something. Nothing may remember it as an
		   order, no other wall may offer it, and the server has never heard of it. */
		expect(SORT_OPTIONS.map((one) => one.value)).not.toContain(RESHUFFLE);
		expect(UNIVERSAL_SORTS.map((one) => one.value)).not.toContain(RESHUFFLE);
		expect(RESHUFFLE_OPTION.value).toBe(RESHUFFLE);
	});

	it('is a row the chooser PRESSES rather than one it keeps', () => {
		/* The row is an action, not an option: as an ordinary option the first press would make
		   it the chosen value and the chooser would then refuse it exactly as it refuses a
		   re-selection of Random. The flag is what stops it ever becoming the value. See
		   `action` in `common/Select.svelte`. Every real order is unflagged, or it could never
		   be chosen. */
		expect(RESHUFFLE_OPTION.action).toBe(true);
		expect(SORT_OPTIONS.some((one) => 'action' in one)).toBe(false);
	});

	it('wears the same mark as the order it repeats, because it IS that order', () => {
		expect(sortIcon(RESHUFFLE)).toBe(sortIcon('random'));
	});

	it('mints a whole number inside the space the server folds onto', () => {
		/* Larger than the modulus is not refused there, it is folded, so two addresses that look
		   different would be one shuffle with nothing saying so. */
		for (let press = 0; press < 200; press += 1) {
			const seed = mintSeed();
			expect(Number.isInteger(seed)).toBe(true);
			expect(seed).toBeGreaterThanOrEqual(0);
			expect(seed).toBeLessThan(SHUFFLE_SEED_LIMIT);
		}
	});
});

/* A note is one line under its label. A longer one makes its row three lines, and on a plain wall
   the list then passes the menu's height and its last order goes under the scroll arrow. */
describe('the words under Similarity', () => {
	it('each fit one line of the Sort menu', () => {
		for (const note of [SIMILAR_TO_WORDS, SIMILAR_TO_FILE, SIMILARITY_NEEDS]) {
			expect(note.length, note).toBeLessThanOrEqual(NOTE_FITS);
		}
	});
});
