/*
 * Paging a wall of uniform cards by whole rows.
 *
 * A fixed count (24, 60, 50) is the same on an 11-inch laptop as on a 32-inch monitor: on the
 * laptop the page overflows and scrolls; on the monitor it leaves half the screen empty. These are
 * the sums that page by whole rows instead.
 *
 * The card is MEASURED off a real one rather than declared, so most of what is worth testing here
 * is what happens before anything has been measured, which is every first render.
 */

import { describe, expect, it } from 'vitest';

import { CardPaging } from './cards.svelte';
import { PAGE_SCREENS } from './justify';

/**
 * A `CardPaging` with its private measurements filled in, as the ResizeObserver would.
 *
 * Reached through the attachment rather than by poking at the fields, so the test exercises the
 * path the app actually uses, including the walk up to the scrolling box, which is the part most
 * likely to be wrong.
 */
function measured(options: {
	fallback: number;
	area: { width: number; height: number };
	card: { width: number; height: number };
	gap: number;
}): CardPaging {
	const paging = new CardPaging(options.fallback);

	const area = document.createElement('div');
	area.style.overflowY = 'auto';
	const wall = document.createElement('div');
	const card = document.createElement('div');
	wall.append(card);
	area.append(wall);
	document.body.append(area);

	// jsdom has no layout engine, so every box is zero unless it is told otherwise. Stubbed at the
	// level the code reads: the two geometry calls and the computed gap.
	Object.defineProperty(area, 'clientWidth', { value: options.area.width, configurable: true });
	area.getBoundingClientRect = () => ({ height: options.area.height }) as DOMRect;
	card.getBoundingClientRect = () => options.card as DOMRect;
	wall.style.rowGap = `${options.gap}px`;

	paging.cards(wall);
	area.remove();
	return paging;
}

describe('before anything has been measured', () => {
	it('asks for the count the screen has always used', () => {
		// A first request of nothing is an empty screen that fills in a moment later, which reads as
		// the page having failed and then changed its mind.
		expect(new CardPaging(24).size).toBe(24);
	});

	it('and keeps asking for it while a card has no size yet', () => {
		// Between the first paint and the first card, `getBoundingClientRect` is all zeros. Dividing
		// by that is Infinity, and `Math.floor(Infinity)` is a request for every row in the library.
		const paging = measured({
			fallback: 24,
			area: { width: 1600, height: 900 },
			card: { width: 0, height: 0 },
			gap: 16
		});
		expect(paging.size).toBe(24);
	});
});

describe('how many cards fill a page', () => {
	it('is whole rows of the window, one page-depth over', () => {
		/* 1600 wide with 16px gaps holds 9 cards of 160; 900 tall holds 4 rows of 200.
		 *
		 * `PAGE_SCREENS` rather than the number it happens to be. A depth written out by hand
		 * here would turn a deliberate change to it into a red test that says nothing about
		 * whether the arithmetic is right. What is being tested here is whole ROWS times the
		 * depth; the depth itself is decided in `justify.ts`.
		 */
		const paging = measured({
			fallback: 24,
			area: { width: 1600, height: 900 },
			card: { width: 160, height: 200 },
			gap: 16
		});
		expect(paging.size).toBe(9 * 4 * PAGE_SCREENS);
	});

	it('grows with the screen, which is the whole point', () => {
		const laptop = measured({
			fallback: 24,
			area: { width: 1100, height: 700 },
			card: { width: 160, height: 200 },
			gap: 16
		});
		const monitor = measured({
			fallback: 24,
			area: { width: 3000, height: 1600 },
			card: { width: 160, height: 200 },
			gap: 16
		});
		expect(monitor.size).toBeGreaterThan(laptop.size);
	});

	it('never asks the server for more than it will hand back', () => {
		// The ceiling is not a suggestion: it is what stops one screenful becoming a whole library.
		const huge = measured({
			fallback: 24,
			area: { width: 3840, height: 2160 },
			card: { width: 90, height: 90 },
			gap: 4
		});
		expect(huge.size).toBeLessThanOrEqual(200);
	});

	it('is at least one row on a window too short for one', () => {
		const cramped = measured({
			fallback: 24,
			area: { width: 300, height: 80 },
			card: { width: 160, height: 200 },
			gap: 16
		});
		expect(cramped.size).toBeGreaterThanOrEqual(1);
	});
});

describe('moving through the list', () => {
	const wall = () =>
		measured({
			fallback: 24,
			area: { width: 1600, height: 900 },
			card: { width: 160, height: 200 },
			gap: 16
		});

	it('steps forward by exactly a page', () => {
		const paging = wall();
		paging.step(1, 1000);
		expect(paging.offset).toBe(paging.size);
	});

	it('cannot step back past the beginning', () => {
		const paging = wall();
		paging.step(-1, 1000);
		expect(paging.offset).toBe(0);
	});

	it('cannot step past the end', () => {
		const paging = wall();
		paging.goTo(10_000, 1000);
		expect(paging.offset).toBe(999);
	});

	it('lands the last page on a page boundary, not on the last card', () => {
		/*
		 * The pages tile the list from the front, so the boundaries do not move.
		 *
		 * Ending the last page exactly at the final card is the obvious alternative and it is worse:
		 * every boundary in the list shifts each time one card is added, so the page somebody is
		 * reading changes under them when an import lands.
		 */
		const paging = wall();
		paging.last(1000);
		expect(paging.offset % paging.size).toBe(0);
		expect(paging.offset).toBeLessThan(1000);
		expect(paging.offset + paging.size).toBeGreaterThanOrEqual(1000);
	});

	it('has nowhere to go in an empty list', () => {
		const paging = wall();
		paging.last(0);
		expect(paging.offset).toBe(0);
		paging.step(1, 0);
		expect(paging.offset).toBe(0);
	});
});

describe('the row the address opened this wall at', () => {
	/*
	 * The half of paging that lives in an address rather than in memory: what gets SENT, and when
	 * the anchor stops applying. A browser spec asserting that a `?from=` appears and goes away
	 * again covers none of it.
	 */

	it('is not honoured until the address says so', () => {
		const paging = new CardPaging(24);

		expect(paging.query).toEqual({ limit: 24, offset: 0 });
	});

	it('is what the next request asks by, instead of a place in the list', () => {
		const paging = new CardPaging(24);
		paging.offset = 96;

		paging.arrive({ from: 'p7', near: null });

		expect(paging.query).toEqual({ limit: 24, from: 'p7' });
	});

	it('never goes out beside an offset', () => {
		/* Both together asks two questions, and which one the server honours would then be a detail
		   of the server rather than a decision. */
		const paging = new CardPaging(24);
		paging.arrive({ from: 'p7', near: null });

		expect(paging.query).not.toHaveProperty('offset');
	});

	it('starts from the top when the address named nothing', () => {
		const paging = new CardPaging(24);
		paging.offset = 96;

		paging.arrive(null);

		expect(paging.query).toEqual({ limit: 24, offset: 0 });
	});

	it('stops applying once the question changes', () => {
		const paging = new CardPaging(24);
		paging.arrive({ from: 'p7', near: null });

		paging.forget();

		expect(paging.query).toEqual({ limit: 24, offset: 0 });
	});

	it('starts a different question at its top, with no anchor carried across', () => {
		/* A kind chosen, a state switched: another list. `forget` alone would keep the offset,
		   opening the new list on whatever page the old one had been turned to. */
		const paging = new CardPaging(24);
		paging.arrive({ from: 'p7', near: 48 });
		paging.offset = 48;

		expect(paging.restart()).toBe(true);
		expect(paging.query).toEqual({ limit: 24, offset: 0 });

		// Already at the top: nothing moved, so the caller reads the new list itself.
		paging.arrive({ from: 'p7', near: 0 });
		expect(paging.restart()).toBe(false);
		expect(paging.query).toEqual({ limit: 24, offset: 0 });
	});

	it('stops applying the moment a page is turned', () => {
		// Left set, every press of Next would re-serve the page the link pointed at.
		const paging = new CardPaging(24);
		paging.arrive({ from: 'p7', near: null });

		paging.step(1, 500);

		expect(paging.query).toEqual({ limit: 24, offset: 24 });
	});

	it('stops applying on a jump to the last page', () => {
		const paging = new CardPaging(24);
		paging.arrive({ from: 'p7', near: null });

		paging.last(100);

		expect(paging.query).toEqual({ limit: 24, offset: 96 });
	});
});

/*
 * The reading that never happens again, and the two that refuse a zero.
 *
 * These three lines are the difference between a quiet tab and a request storm: fifty-odd a
 * second for as long as a tab is open, with nothing on screen looking wrong and every test passing.
 * Every screen using this replaces its wall while it loads ("Looking...", a skeleton), so the
 * observed element is REMOVED for the length of each fetch and the box comes back as nothing.
 * Written down, the page size would fall to the unmeasured default, the effect watching the size
 * would fetch again, and the fetch would take the wall down again.
 *
 * The attachment's own synchronous read is the same fault from the other end: it runs before the
 * browser has laid the page out, so on a REMOUNT it reads the frame in which the wall was still
 * replaced: 48px short, which is less than a card and enough to cross a whole-row boundary.
 */
describe('a box that is not there is not a measurement', () => {
	/*
	 * Driven through a stand-in ResizeObserver, because that is the only path these guards are on.
	 *
	 * The attachment reads the box once, ever; every reading after that arrives from the observer.
	 * jsdom has no ResizeObserver at all, so a test that only calls the attachment twice never
	 * reaches them, and both guards could be deleted with it green.
	 */
	function attached(area: { width: number; height: number }) {
		const paging = new CardPaging(24);
		const box = document.createElement('div');
		box.style.overflowY = 'auto';
		const wall = document.createElement('div');
		const card = document.createElement('div');
		wall.append(card);
		box.append(wall);
		document.body.append(box);

		let width = area.width;
		let height = area.height;
		Object.defineProperty(box, 'clientWidth', { get: () => width, configurable: true });
		box.getBoundingClientRect = () => ({ height }) as DOMRect;
		card.getBoundingClientRect = () => ({ width: 200, height: 260 }) as DOMRect;
		wall.style.rowGap = '10px';

		let deliver: (() => void) | null = null;
		const had = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
		(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
			constructor(callback: () => void) {
				deliver = callback;
			}
			observe() {}
			disconnect() {}
		};

		const detach = paging.cards(wall);

		return {
			paging,
			wall,
			/** What the observer would say once the browser has laid the page out again. */
			observes(next: { width: number; height: number }) {
				this.becomes(next);
				deliver?.();
			},
			/** The box changes and nobody is told, which is the frame an attachment reads. */
			becomes(next: { width: number; height: number }) {
				width = next.width;
				height = next.height;
			},
			done() {
				detach?.();
				box.remove();
				(globalThis as { ResizeObserver?: unknown }).ResizeObserver = had;
			}
		};
	}

	it('keeps the last real width when the box comes back with none', () => {
		const set = attached({ width: 1200, height: 900 });
		const measuredSize = set.paging.size;

		// The wall is replaced while it fetches, so the observer reports a box of nothing.
		set.observes({ width: 0, height: 900 });

		expect(
			set.paging.size,
			'a width of nothing was written down, so the page size changed and asked again'
		).toBe(measuredSize);
		set.done();
	});

	it('and the last real height too', () => {
		const set = attached({ width: 1200, height: 900 });
		const measuredSize = set.paging.size;

		set.observes({ width: 1200, height: 0 });

		expect(
			set.paging.size,
			'a height of nothing was written down, so the page size changed and asked again'
		).toBe(measuredSize);
		set.done();
	});

	it('and the size it keeps is a measured one, not the fallback', () => {
		// The half that makes the two above mean something: if the measured size happened to equal
		// the fallback, keeping it would be indistinguishable from losing it.
		const set = attached({ width: 1200, height: 900 });

		expect(set.paging.size).not.toBe(24);
		set.done();
	});

	it('takes a real measurement from the observer, so the guards are not simply ignoring it', () => {
		/* The other direction, and without it every assertion above is satisfied by a `read` that
		   never writes anything at all. */
		const set = attached({ width: 1200, height: 900 });
		const measuredSize = set.paging.size;

		set.observes({ width: 1200, height: 2000 });

		expect(set.paging.size).toBeGreaterThan(measuredSize);
		set.done();
	});

	it('does not re-read the box synchronously on a later attachment', () => {
		/* The remount. On the FIRST attachment there is nothing else to go on and a stale box beats
		   no box; on every one after it a real measurement already exists, and only the observer
		   (which delivers AFTER layout) may change it. Driven here by making the second reading
		   smaller, which is exactly what the frame without the wall in it looks like. */
		const set = attached({ width: 1200, height: 900 });
		const measuredSize = set.paging.size;

		/* The box is short and NOBODY HAS BEEN TOLD, which is exactly the state an attachment runs in:
		   the element is in the document and the browser has not laid the page out again, so what is
		   there to read is the previous frame: the one where the wall was still replaced by
		   "Looking...". A remount now must change nothing. */
		set.becomes({ width: 1200, height: 400 });
		set.paging.cards(set.wall);

		expect(
			set.paging.size,
			'the second attachment measured the page again, before the browser had laid it out'
		).toBe(measuredSize);

		// And the observer, which arrives AFTER layout, is still allowed to change it.
		set.observes({ width: 1200, height: 400 });
		expect(set.paging.size, 'the observer was refused as well').toBeLessThan(measuredSize);
		set.done();
	});
});

describe('a page emptied under the wall', () => {
	/* A queue of `size`, answered as the server answers: the rows from `offset`, and the total. */
	function queue(size: () => number) {
		const asked: { limit: number; offset?: number; from?: string }[] = [];
		const ask = async (query: { limit: number; offset?: number; from?: string }) => {
			asked.push(query);
			const offset = Number(query.offset ?? 0);
			const count = Math.max(0, Math.min(query.limit, size() - offset));
			return {
				rows: Array.from({ length: count }, (_x, i) => offset + i),
				total: size(),
				offset
			};
		};
		return { asked, ask };
	}
	const read = (answer: { rows: number[]; total: number; offset: number }) => answer;

	it('steps back to the new last page when its last card is answered', async () => {
		const paging = new CardPaging(20);
		let size = 41;
		const { asked, ask } = queue(() => size);
		paging.goTo(40, size);
		expect((await paging.fill('', () => [], ask, read))?.rows).toEqual([40]);

		size = 40;
		const page = await paging.fill('', () => [40], ask, read);
		expect(asked.at(-1)).toEqual({ limit: 20, offset: 20 });
		expect(page?.offset).toBe(20);
		expect(page?.total).toBe(40);
		expect(page?.rows[0]).toBe(20);
	});

	it('goes to the top when nothing is left, asking the top once to be sure', async () => {
		/* A zero on an empty page past the end is not a count on most walls (they count with a
		   window over the page's own rows, which says 0 for a list that still holds cards), so
		   the top is asked, once, because its count is real. The price is one request on a queue
		   that has just been emptied; not asking would draw "No ... waiting" over cards that are
		   there. */
		const paging = new CardPaging(20);
		let size = 21;
		const { asked, ask } = queue(() => size);
		paging.goTo(20, size);
		await paging.fill('', () => [], ask, read);

		size = 0;
		const page = await paging.fill('', () => [20], ask, read);
		expect(asked).toHaveLength(3);
		expect(asked.at(-1)).toEqual({ limit: 20, offset: 0 });
		expect(page?.offset).toBe(0);
		expect(page?.total).toBe(0);
		expect(page?.rows).toEqual([]);
	});

	it('leaves the first page alone when it is empty', async () => {
		const paging = new CardPaging(20);
		const { asked, ask } = queue(() => 0);
		const page = await paging.fill('', () => [], ask, read);
		expect(asked).toHaveLength(1);
		expect(page?.offset).toBe(0);
	});
});
