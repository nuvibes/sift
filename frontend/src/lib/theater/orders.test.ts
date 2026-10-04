/*
 * The orders a Theater cell may be put in.
 *
 * Worth a file of its own because the list is a SUBSET taken by key, and a subset taken by key is
 * the kind of thing that goes quietly wrong: a key renamed in `SORT_OPTIONS` leaves this filtering
 * nothing out, and the control would then offer an order a cell cannot hold with nothing saying so.
 */
import { describe, expect, it } from 'vitest';

import {
	RANDOM,
	RELEVANCE,
	RESHUFFLE,
	SIMILARITY,
	SIMILARITY_NEEDS,
	SIMILAR_TO_FILE,
	SIMILAR_TO_WORDS,
	SORT_OPTIONS
} from '$lib/grid/sort-state.svelte';
import {
	anotherRound,
	applyOrder,
	CELL_DEFAULT_ORDER,
	CELL_ORDERS,
	cellOrders,
	NO_CELL_TO_ORDER,
	type Orderable,
	orderPressed,
	pressShuffle,
	showingOrder
} from './orders';
import { WALL_ONLY_ORDERS } from '$lib/grid/sort-state.svelte';

describe('what a cell can be ordered by', () => {
	it('leaves out the one a cell cannot hold, and keeps the rest', () => {
		const offered = CELL_ORDERS.map((one) => one.value);
		expect(offered).not.toContain(RELEVANCE);
		expect(offered).toEqual(
			SORT_OPTIONS.map((one) => one.value).filter(
				(value) => value !== RELEVANCE && !WALL_ONLY_ORDERS.has(value)
			)
		);
	});

	it('offers Random, which a cell can hold for the life of its run', () => {
		/* Random is a cell order: a cell keeps a seed in memory for the life of its run, which
		   is all a seed has to survive. See `Cell.seed`. Held on its own because the filter
		   above would keep passing if it went. */
		expect(CELL_ORDERS.map((one) => one.value)).toContain(RANDOM);
	});

	it('spells every order the way the file wall spells it', () => {
		// Taken out of one list rather than written again: `Newest first` here and `Newest first` on
		// Browse have to be one string, not two that happen to match today.
		for (const order of CELL_ORDERS) {
			const wall = SORT_OPTIONS.find((one) => one.value === order.value);
			expect(wall?.label).toBe(order.label);
		}
	});

	it('opens in an order the list actually offers', () => {
		expect(CELL_ORDERS.map((one) => one.value)).toContain(CELL_DEFAULT_ORDER);
	});

	it('says why the control is dim in words about a CELL, not about the screen', () => {
		// The general sentence is "nothing to order on this screen", which is false above a wall of
		// files: there is plainly something to order. See the module.
		expect(NO_CELL_TO_ORDER).toContain('cell');
		expect(NO_CELL_TO_ORDER).not.toContain('screen');
	});
});

/*
 * SIMILARITY IN A CELL: close to what the cell draws from, and dimmed where that is nothing.
 */
describe('Similarity in a cell', () => {
	const row = (source: string) =>
		cellOrders('newest', source).find((one) => one.value === SIMILARITY);

	it('is dimmed with its reason where the cell draws from nothing to compare with', () => {
		for (const source of ['', 'tags:beach', '-people:"Bryn Calloway" media:image']) {
			expect(row(source)?.disabled).toBe(true);
			expect(row(source)?.note).toBe(SIMILARITY_NEEDS);
		}
	});

	it('is close to the words, and words win over a file as they do on the server', () => {
		expect(row('red car')).toMatchObject({ note: SIMILAR_TO_WORDS });
		expect(row('like:01HX0000000000000000000001 red car')?.note).toBe(SIMILAR_TO_WORDS);
		expect(row('red car')?.disabled).toBeUndefined();
	});

	it('is close to the file a like: names', () => {
		expect(row('like:01HX0000000000000000000001 tags:beach')).toMatchObject({
			note: SIMILAR_TO_FILE
		});
	});
});

/*
 * ASKING FOR THE SHUFFLE AGAIN, which is the one press the chooser cannot deliver on its own.
 *
 * It refuses a re-selection of the order already showing, which is right for every other order and
 * wrong for this one: a shuffle is a draw, and asking for it again is asking for a different one.
 * So the menu carries a second row while the cell is shuffled. Held here rather than in the screen
 * because a decision written inside a route file is a decision nothing can test.
 */
describe('asking a shuffled cell to shuffle again', () => {
	it('offers the extra row only while the cell is already shuffled', () => {
		expect(cellOrders(RANDOM).map((one) => one.value)).toContain(RESHUFFLE);
		expect(cellOrders('newest').map((one) => one.value)).not.toContain(RESHUFFLE);
		expect(cellOrders(null).map((one) => one.value)).not.toContain(RESHUFFLE);
	});

	it('leaves the orders themselves alone', () => {
		// The extra row is an extra row, not a replacement: everything else on the menu is still
		// there and still in the order the file wall shows it in.
		const values = CELL_ORDERS.map((one) => one.value);
		expect(
			cellOrders(RANDOM)
				.slice(0, CELL_ORDERS.length)
				.map((one) => one.value)
		).toEqual(values);
		expect(cellOrders('newest').map((one) => one.value)).toEqual(values);
	});

	it('turns the press back into Random, because nothing may store it as an order', () => {
		expect(orderPressed(RESHUFFLE)).toBe(RANDOM);
		expect(orderPressed(RANDOM)).toBe(RANDOM);
		expect(orderPressed('newest')).toBe('newest');
	});

	it('is not an order, so no cell can be put in it', () => {
		expect(CELL_ORDERS.map((one) => one.value)).not.toContain(RESHUFFLE);
	});

	it('is offered as a PRESS, so the chooser never keeps it as the cell order', () => {
		/* The row is an action, not an option: as an ordinary option the first press would make
		   it the chosen value and every press after it would be swallowed. The flag is what the
		   chooser reads; see `action` in `common/Select.svelte`. */
		const row = cellOrders(RANDOM).find((one) => one.value === RESHUFFLE);
		expect(row?.action).toBe(true);
		expect(cellOrders(RANDOM).filter((one) => one.action)).toHaveLength(1);
	});
});

/*
 * WHAT THE PRESS DOES TO THE CELLS THE BAR HAS ADDRESSED.
 *
 * Held here for the reason the list above is: it is a decision about orders, and a decision written
 * inside a route file is a decision nothing can test. The bar handing over the right cells is the
 * wall's own property and is checked there (`wall.addressed`); what this answers is what happens to
 * whichever cells arrive.
 */
describe('putting the addressed cells in an order', () => {
	/** A cell that only records what was asked of it. See `Orderable`. */
	function fake() {
		const put: (string | null)[] = [];
		let reordered = 0;
		let restarted = 0;
		const cell: Orderable = {
			orderBy: (next) => void put.push(next),
			reorder: async () => void (reordered += 1),
			restart: async () => void (restarted += 1)
		};
		return {
			cell,
			put,
			get reordered() {
				return reordered;
			},
			get restarted() {
				return restarted;
			}
		};
	}

	it('touches the cells it was handed and nothing else', () => {
		// One cell picked on the bar is one cell ordered. The wall decides WHICH; this only ever
		// reaches the list it is given, which is what keeps that decision the wall's alone.
		const one = fake();
		const other = fake();

		applyOrder([one.cell], 'name_az');

		expect(one.put).toEqual(['name_az']);
		expect(other.put).toEqual([]);
		expect(other.reordered + other.restarted).toBe(0);
	});

	it('reaches every cell when the bar has addressed all of them', () => {
		const cells = [fake(), fake(), fake()];

		applyOrder(
			cells.map((one) => one.cell),
			RANDOM
		);

		for (const one of cells) {
			expect(one.put).toEqual([RANDOM]);
			expect(one.restarted).toBe(1);
		}
	});

	it('rebuilds the run for an ordinary order, and leaves what is playing alone', () => {
		// An order is a fact about what comes next. `restart` throws the file on screen away, which is
		// what the casino control is for. See `Cell.reorder`.
		const one = fake();

		applyOrder([one.cell], 'newest');

		expect(one.reordered).toBe(1);
		expect(one.restarted).toBe(0);
	});

	it('LANDS the cell on the draw it just asked for', () => {
		/* A shuffle is a draw, and a draw that leaves the front of the run alone changes nothing
		   anybody can see: a request under a fresh seed and a picture that never moves. See
		   `applyOrder`. */
		const one = fake();

		applyOrder([one.cell], RANDOM);

		expect(one.restarted).toBe(1);
		expect(one.reordered).toBe(0);
	});

	it('lands it again on Shuffle again, which is the only thing that press can change', () => {
		// The order is already Random, so the row means a DIFFERENT draw and nothing else. It arrives
		// as Random (nothing stores `reshuffle`), and it has to land, or it is a control that
		// provably does nothing.
		const one = fake();

		applyOrder([one.cell], RANDOM);
		applyOrder([one.cell], RESHUFFLE);

		expect(one.put).toEqual([RANDOM, RANDOM]);
		expect(one.restarted).toBe(2);
		expect(one.reordered).toBe(0);
	});

	it('draws again on every press, not only on the second', () => {
		/* Three deep, because two can pass on a control that merely alternates, and once on and
		   once off is exactly what that fault looks like from the screen. */
		const one = fake();

		applyOrder([one.cell], RANDOM);
		applyOrder([one.cell], RESHUFFLE);
		applyOrder([one.cell], RESHUFFLE);

		expect(one.put).toEqual([RANDOM, RANDOM, RANDOM]);
		expect(one.restarted).toBe(3);
	});
});

describe("the drawer's Shuffle", () => {
	function fake() {
		const put: (string | null)[] = [];
		let reordered = 0;
		const cell: Orderable = {
			orderBy: (next) => void put.push(next),
			reorder: async () => void (reordered += 1),
			restart: async () => undefined
		};
		return {
			cell,
			put,
			get reordered() {
				return reordered;
			}
		};
	}

	it('puts a cell in Random order, afresh on every press that turns it on', () => {
		/* Turning it back on must not resume the previous order from its top; `orderBy(RANDOM)`
		   is what mints the new seed, so each press on has to reach it. */
		const one = fake();

		pressShuffle([one.cell], false);
		pressShuffle([one.cell], true);
		pressShuffle([one.cell], false);

		expect(one.put).toEqual([RANDOM, null, RANDOM]);
		expect(one.reordered, 'the file on screen was cut off rather than the run rebuilt').toBe(3);
	});
});

describe('the order a small source comes round in', () => {
	const run = (...ids: string[]) => ids.map((id) => ({ id }));
	const ids = (files: { id: string }[]) => files.map((one) => one.id);

	it('keeps a fresh shuffle that already differs and opens on something else', () => {
		expect(ids(anotherRound(['a', 'b', 'c'], run('b', 'a', 'c'), 'c'))).toEqual(['b', 'a', 'c']);
	});

	it('turns round a shuffle that dealt the round just played', () => {
		expect(ids(anotherRound(['a', 'b', 'c'], run('a', 'b', 'c'), 'c'))).toEqual(['b', 'c', 'a']);
	});

	it('with two files, not repeating the clip that just ended wins over a different order', () => {
		expect(ids(anotherRound(['a', 'b'], run('a', 'b'), 'b'))).toEqual(['a', 'b']);
		expect(ids(anotherRound(['a', 'b'], run('b', 'a'), 'b'))).toEqual(['a', 'b']);
	});

	it('never deals the round just played again, whatever the shuffle gave', () => {
		const orders = [
			['a', 'b', 'c'],
			['a', 'c', 'b'],
			['b', 'a', 'c'],
			['b', 'c', 'a'],
			['c', 'a', 'b'],
			['c', 'b', 'a']
		];
		for (const before of orders) {
			for (const fresh of orders) {
				const next = ids(anotherRound(before, run(...fresh), before[2]));
				expect(next, `${fresh} after ${before}`).not.toEqual(before);
				expect(next[0], `${fresh} after ${before} opened on the file that ended`).not.toBe(
					before[2]
				);
				expect([...next].sort()).toEqual(['a', 'b', 'c']);
			}
		}
	});

	it('leaves one file as it is', () => {
		expect(ids(anotherRound(['a'], run('a'), 'a'))).toEqual(['a']);
	});

	it('orders the files it plays, and puts the ones it stepped over last', () => {
		const passed = new Set(['x', 'y']);
		// Shown last time: a b c. The page differs, the files shown in it do not.
		const next = ids(
			anotherRound(['x', 'a', 'y', 'b', 'c'], run('a', 'x', 'b', 'y', 'c'), 'c', passed)
		);

		expect(
			next.filter((id) => !passed.has(id)),
			'the shown round was the last again'
		).not.toEqual(['a', 'b', 'c']);
		expect(next[0], 'the round opened on the file that ended').not.toBe('c');
		expect(next.slice(-2).sort(), 'a stepped-over file stood in front').toEqual(['x', 'y']);
	});

	/*
	 * Two deals that SHOW as one. Direct play goes first, so [b, a, c] with only `a` converted is
	 * shown b c a (the round just played), although it was dealt in another order.
	 */
	it('judges a round on the order it will be shown in, not the order it was dealt in', () => {
		const direct = (id: string) => id !== 'a';
		const showing = (order: readonly string[]) => showingOrder(order, direct, 4, 'a');
		const next = ids(anotherRound(['b', 'c', 'a'], run('b', 'a', 'c'), 'a', new Set(), showing));

		expect(showing(next), 'the round shown was the last again').not.toEqual(['b', 'c', 'a']);
		expect(showing(next)[0], 'the round opened on the file that ended').not.toBe('a');
	});
});

describe('the order a cell shows a dealt run in', () => {
	const direct = (id: string) => id.startsWith('d');

	it('shows what plays as it is first, and the rest wait their turn in the order they were in', () => {
		expect(showingOrder(['t1', 'd1', 't2', 'd2'], direct, 4)).toEqual(['d1', 'd2', 't1', 't2']);
	});

	it('looks only a few files ahead, and takes the first of them where none plays as it is', () => {
		expect(showingOrder(['t1', 't2', 't3', 't4', 'd1'], direct, 4)).toEqual([
			't1',
			'd1',
			't2',
			't3',
			't4'
		]);
	});

	it('opens on the file on screen only where there is nothing else', () => {
		expect(showingOrder(['d1', 'd2'], direct, 4, 'd1')).toEqual(['d2', 'd1']);
		expect(showingOrder(['d1'], direct, 4, 'd1')).toEqual(['d1']);
	});

	it('counts a file nobody has asked about as one that plays as it is', () => {
		expect(showingOrder(['t1', 'u1'], (id) => id !== 't1', 4)).toEqual(['u1', 't1']);
	});
});
