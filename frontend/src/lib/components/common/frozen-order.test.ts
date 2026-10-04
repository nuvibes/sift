import { describe, expect, it } from 'vitest';
import { applyFrozenOrder } from './frozen-order';

/* The bug this exists to prevent, stated once:
 *
 * A queue sorts itself live. You put the pointer on the third row and reach for Cancel. A job above
 * it finishes, the list re-sorts, and the row under your cursor is now a different job. You cancel
 * that one instead. Nothing on screen afterwards explains it, and you did nothing wrong.
 *
 * So these are not tests about ordering. They are tests about the click landing on the row the
 * person was looking at.
 */

interface Job {
	id: string;
	name: string;
}

const job = (id: string): Job => ({ id, name: `job ${id}` });
const key = (j: Job) => j.id;
const ids = (rows: Job[]) => rows.map((j) => j.id);

describe('while nobody is pointing at the list', () => {
	it('the live order is the order', () => {
		// The freeze is not a sort of its own. With nothing held, whatever the screen sorted is what
		// it gets back, untouched.
		const live = [job('c'), job('a'), job('b')];

		expect(ids(applyFrozenOrder(live, null, key))).toEqual(['c', 'a', 'b']);
	});
});

describe('while a row is held', () => {
	it('a re-sort underneath does not move anything', () => {
		// The exact failure. The list was [a, b, c] when the pointer arrived. The server now says the
		// order is [c, b, a]. The rows must not move: the person is still reaching for whichever one
		// they were reaching for.
		const held = ['a', 'b', 'c'];
		const resorted = [job('c'), job('b'), job('a')];

		expect(ids(applyFrozenOrder(resorted, held, key))).toEqual(['a', 'b', 'c']);
	});

	it('the row under the cursor is still the same job after the list churns', () => {
		// Said as the thing that actually matters rather than as an order. Row index 2 was job c when
		// the pointer landed; a click on row index 2 has to still be job c.
		const held = ['a', 'b', 'c', 'd'];
		const churned = [job('d'), job('c'), job('b'), job('a')];

		const rows = applyFrozenOrder(churned, held, key);

		expect(rows[2].id).toBe('c');
	});

	it('a job that finished and left is gone rather than held on screen', () => {
		// The freeze holds an order, not a list. A job that is no longer in the queue cannot be kept
		// on screen by pretending it is: the row would have nothing behind it to act on.
		const held = ['a', 'b', 'c'];
		const oneLeft = [job('a'), job('c')];

		expect(ids(applyFrozenOrder(oneLeft, held, key))).toEqual(['a', 'c']);
	});

	it('a job that arrived goes to the end, where nobody is reaching', () => {
		// New work must not be inserted into the middle: inserting is moving, and moving is the whole
		// thing being prevented. It goes after everything held, and it can sort itself out on leave.
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
		// It sorts, and sort is in place. Handed the live array directly, an in-place sort would
		// reorder the caller's state and freeze the queue for good.
		const live = [job('c'), job('a')];

		applyFrozenOrder(live, ['a', 'c'], key);

		expect(ids(live)).toEqual(['c', 'a']);
	});
});
