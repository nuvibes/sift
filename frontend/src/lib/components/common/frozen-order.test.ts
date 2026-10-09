import { describe, expect, it } from 'vitest';
import { applyFrozenOrder } from './frozen-order';

/* A live queue re-sorting under the pointer makes a click land on another row; these test that
 * the click lands on the row the person was looking at. */

interface Job {
	id: string;
	name: string;
}

const job = (id: string): Job => ({ id, name: `job ${id}` });
const key = (j: Job) => j.id;
const ids = (rows: Job[]) => rows.map((j) => j.id);

describe('while nobody is pointing at the list', () => {
	it('the live order is the order', () => {
		// With nothing held, the screen's order comes back untouched.
		const live = [job('c'), job('a'), job('b')];

		expect(ids(applyFrozenOrder(live, null, key))).toEqual(['c', 'a', 'b']);
	});
});

describe('while a row is held', () => {
	it('a re-sort underneath does not move anything', () => {
		// The order changed under the pointer; the rows must not move.
		const held = ['a', 'b', 'c'];
		const resorted = [job('c'), job('b'), job('a')];

		expect(ids(applyFrozenOrder(resorted, held, key))).toEqual(['a', 'b', 'c']);
	});

	it('the row under the cursor is still the same job after the list churns', () => {
		// The row index still names the job it named when the pointer landed.
		const held = ['a', 'b', 'c', 'd'];
		const churned = [job('d'), job('c'), job('b'), job('a')];

		const rows = applyFrozenOrder(churned, held, key);

		expect(rows[2].id).toBe('c');
	});

	it('a job that finished and left is gone rather than held on screen', () => {
		// A job that left the queue is not kept on screen.
		const held = ['a', 'b', 'c'];
		const oneLeft = [job('a'), job('c')];

		expect(ids(applyFrozenOrder(oneLeft, held, key))).toEqual(['a', 'c']);
	});

	it('a job that arrived goes to the end, where nobody is reaching', () => {
		// New work goes after everything held, never into the middle.
		const held = ['a', 'b'];
		const withNew = [job('a'), job('z'), job('b')];

		expect(ids(applyFrozenOrder(withNew, held, key))).toEqual(['a', 'b', 'z']);
	});

	it('several new jobs keep the order they arrived in', () => {
		const held = ['a'];
		const withNew = [job('y'), job('a'), job('z')];

		expect(ids(applyFrozenOrder(withNew, held, key))).toEqual(['a', 'y', 'z']);
	});

	it('does not mutate the list it was handed', () => {
		// Sorted on a copy, or the caller's state would be reordered.
		const live = [job('c'), job('a')];

		applyFrozenOrder(live, ['a', 'c'], key);

		expect(ids(live)).toEqual(['c', 'a']);
	});
});
