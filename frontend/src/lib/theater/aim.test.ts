/*
 * Marking the cell a control is about, while the pointer is on it.
 *
 * Four handlers is not much to prove. What is worth proving is the part easiest to get wrong:
 * WHEN the question is asked. A control that acts on
 * whatever is selected has to ask at the moment the pointer arrives, or it marks the cell that was
 * chosen when the component was built, which is a wash on the wrong rectangle, and a wash on the
 * wrong rectangle is worse than none at all.
 */
import { describe, expect, it } from 'vitest';

import { aims, aimsAtChosen } from './aim';
import type { Wall } from './wall.svelte';

/** Just enough wall for the two things this reads. */
function aWall(everyCell = false): Wall {
	return { aiming: null, everyCell } as unknown as Wall;
}

describe('pointing at a control that names a cell', () => {
	it('marks that cell, and lets it go again', () => {
		const wall = aWall();
		const handlers = aims(wall, 2);

		handlers.onmouseenter();

		expect(wall.aiming).toBe(2);

		handlers.onmouseleave();

		expect(wall.aiming).toBeNull();
	});

	it('answers the keyboard the same way it answers the pointer', () => {
		// The whole row is reachable by tab, and somebody moving through it is asking the same
		// question about the same cells.
		const wall = aWall();
		const handlers = aims(wall, 0);

		handlers.onfocus();

		expect(wall.aiming).toBe(0);

		handlers.onblur();

		expect(wall.aiming).toBeNull();
	});

	it('marks the whole wall for a control that means all of them', () => {
		const wall = aWall();

		aims(wall, 'every').onmouseenter();

		expect(wall.aiming).toBe('every');
	});
});

describe('a control that acts on whatever is chosen', () => {
	it('asks when the pointer arrives, not when it was built', () => {
		let chosen = 1;
		const wall = aWall();
		const handlers = aims(
			wall,
			aimsAtChosen(wall, () => chosen)
		);

		chosen = 4;
		handlers.onmouseenter();

		expect(wall.aiming, 'the mark was captured at setup and is on a stale cell').toBe(4);
	});

	it('marks every cell while the whole wall is selected', () => {
		// The verb really does land on all of them then, and a wash on one would be a lie about
		// what is about to happen.
		const wall = aWall(true);

		aims(
			wall,
			aimsAtChosen(wall, () => 2)
		).onmouseenter();

		expect(wall.aiming).toBe('every');
	});
});

/*
 * A CONTROL THAT LEAVES THE PAGE WHILE POINTED AT LETS GO.
 *
 * An element taken out of the page fires no `mouseleave` and no `blur`, so a panel shut with Escape
 * over "Cell 2" would leave the wash on the wall with nothing on screen able to take it off.
 */
describe('a control taken off the page while it is pointed at', () => {
	/** Spread onto an element, pointed at, and the element's attachment run and torn down. */
	function onto(handlers: ReturnType<typeof aims>) {
		const [key] = Object.getOwnPropertySymbols(handlers);
		const node = document.createElement('button');
		const cleanup = handlers[key](node);
		return {
			point: () => handlers.onmouseenter({ currentTarget: node } as unknown as Event),
			remove: () => {
				if (typeof cleanup === 'function') cleanup();
			}
		};
	}

	it('takes its mark with it', () => {
		const wall = aWall();
		const control = onto(aims(wall, 1));
		control.point();

		control.remove();

		expect(wall.aiming, 'the wash outlived the control that put it there').toBeNull();
	});

	it("leaves another control's mark alone", () => {
		const wall = aWall();
		const going = onto(aims(wall, 1));
		const staying = onto(aims(wall, 'every'));
		going.point();
		staying.point();

		going.remove();

		expect(wall.aiming).toBe('every');
	});

	it('leaves a neighbour spreading the SAME handlers alone', () => {
		/* A cell's bar spreads one object over ten controls. One of them going while the pointer is on
		   another is not the pointed-at control leaving. */
		const wall = aWall();
		const shared = aims(wall, 2);
		const pointed = onto(shared);
		const neighbour = onto(shared);
		pointed.point();

		neighbour.remove();

		expect(wall.aiming, 'a neighbour going took the mark off the control still pointed at').toBe(2);
	});
});
