/*
 * ONE FIRST PAGE: what a wall of cards asks the server for when it arrives.
 *
 * A wall that asks at the count it uses before anything is measured, and then again, whole, at the
 * measured size, asks for its first page twice on every visit, because each visit is a new wall.
 *
 * Two halves, both driven here through `fill`, which is what the walls call:
 * - the FIRST visit asks once, and then either trims what it holds or asks for the remainder only;
 * - a LATER visit to the same wall asks once, at the size it measured last time.
 */

import { flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import type { PageAsk } from './anchor';
import { CardPaging, fillHeld, forgetMeasurements, type HeldWall } from './cards.svelte';

/** A list of this many rows, on a stand-in server that writes down every request. */
function server(length: number) {
	const rows = Array.from({ length }, (_, at) => `row-${at}`);
	const asks: PageAsk[] = [];
	return {
		asks,
		ask: async (query: PageAsk) => {
			asks.push(query);
			const offset = 'offset' in query ? query.offset : 0;
			return { rows: rows.slice(offset, offset + query.limit), total: rows.length, offset };
		},
		read: (answer: { rows: string[]; total: number; offset: number }) => answer
	};
}

/**
 * A wall on a page, measured through the attachment and a stand-in ResizeObserver: the path the
 * app takes. The box is ten cards wide; `draws(n)` and `observes(n)` make it `n` rows of cards tall.
 */
function wall(paging: CardPaging) {
	const box = document.createElement('div');
	box.style.overflowY = 'auto';
	const cards = document.createElement('div');
	const card = document.createElement('div');
	cards.append(card);
	box.append(cards);
	document.body.append(box);

	let height = 0;
	Object.defineProperty(box, 'clientWidth', { get: () => 1000, configurable: true });
	box.getBoundingClientRect = () => ({ height }) as DOMRect;
	card.getBoundingClientRect = () => ({ width: 100, height: 100 }) as DOMRect;
	cards.style.rowGap = '0px';

	let deliver: (() => void) | null = null;
	const had = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
		constructor(callback: () => void) {
			deliver = callback;
		}
		observe() {}
		disconnect() {}
	};

	return {
		/** The first card drew: the attachment runs and reads the box. */
		draws(rowsTall: number) {
			height = rowsTall * 100;
			paging.cards(cards);
		},
		/** The browser laid the page out again and the observer says so. */
		observes(rowsTall: number) {
			height = rowsTall * 100;
			deliver?.();
		},
		done() {
			box.remove();
			(globalThis as { ResizeObserver?: unknown }).ResizeObserver = had;
		}
	};
}

afterEach(() => forgetMeasurements());

describe('the first visit asks for its first page once', () => {
	it('and TRIMS what it holds when the measured page is smaller', async () => {
		const list = server(500);
		const paging = new CardPaging(50, 'test.smaller');
		const first = await paging.fill('', () => [], list.ask, list.read);
		expect(first?.rows).toHaveLength(50);

		const on = wall(paging);
		on.draws(1);
		expect(paging.size, 'the measured page must be the smaller one for this case').toBeLessThan(50);
		const second = await paging.fill('', () => first!.rows, list.ask, list.read);

		expect(list.asks, 'a second whole first page was asked for').toEqual([
			{ limit: 50, offset: 0 }
		]);
		expect(second?.rows).toEqual(first!.rows.slice(0, paging.size));
		expect(second?.total).toBe(500);
		expect(second?.answer, 'a trim hands back no answer, since nothing was asked').toBeNull();
		on.done();
	});

	it('and asks only for the REMAINDER when the measured page is larger', async () => {
		const list = server(500);
		const paging = new CardPaging(60, 'test.larger');
		const first = await paging.fill('', () => [], list.ask, list.read);

		const on = wall(paging);
		on.draws(2);
		const size = paging.size;
		expect(size, 'the measured page must be the larger one for this case').toBeGreaterThan(60);
		const second = await paging.fill('', () => first!.rows, list.ask, list.read);

		expect(list.asks).toEqual([
			{ limit: 60, offset: 0 },
			{ limit: size - 60, offset: 60 }
		]);
		expect(second?.rows).toEqual(Array.from({ length: size }, (_, at) => `row-${at}`));
		on.done();
	});

	it('asks nothing more when what it holds is already the whole list', async () => {
		const list = server(30);
		const paging = new CardPaging(60, 'test.short');
		const first = await paging.fill('', () => [], list.ask, list.read);

		const on = wall(paging);
		on.draws(2);
		const second = await paging.fill('', () => first!.rows, list.ask, list.read);

		expect(list.asks).toEqual([{ limit: 60, offset: 0 }]);
		expect(second?.rows).toHaveLength(30);
		on.done();
	});

	it('asks once for the remainder when the size moves twice while it is on its way', async () => {
		/* As on the face groups wall: the attachment reads a box a frame stale (larger), then the
		   observer the real one. The second size is inside the first remainder, so it waits for that answer and
		   cuts it, rather than asking a third time. */
		const list = server(500);
		const paging = new CardPaging(60, 'test.twice');
		const first = await paging.fill('', () => [], list.ask, list.read);

		const on = wall(paging);
		on.draws(4);
		const stale = paging.size;
		const overtaken = paging.fill('', () => first!.rows, list.ask, list.read);
		on.observes(3);
		const size = paging.size;
		expect(size).toBeLessThan(stale);
		expect(size).toBeGreaterThan(60);
		const landed = paging.fill('', () => first!.rows, list.ask, list.read);

		expect(await overtaken, 'the overtaken read must not be drawn').toBeNull();
		expect((await landed)?.rows).toHaveLength(size);
		expect(list.asks).toEqual([
			{ limit: 60, offset: 0 },
			{ limit: stale - 60, offset: 60 }
		]);
		on.done();
	});
});

describe('a later visit to the same wall', () => {
	it('asks once, at the size it measured last time', async () => {
		const before = new CardPaging(60, 'test.again');
		const on = wall(before);
		on.draws(2);
		const measuredSize = before.size;
		on.done();

		const list = server(500);
		const paging = new CardPaging(60, 'test.again');
		expect(paging.size, 'the second visit started from the fallback again').toBe(measuredSize);
		await paging.fill('', () => [], list.ask, list.read);

		const back = wall(paging);
		back.draws(1);
		expect(paging.size, 'the attachment re-read a stale box over a remembered measurement').toBe(
			measuredSize
		);
		expect(list.asks).toEqual([{ limit: measuredSize, offset: 0 }]);
		back.done();
	});

	it('is remembered per wall, not shared between walls', () => {
		const one = new CardPaging(60, 'test.one');
		const on = wall(one);
		on.draws(2);
		on.done();

		expect(new CardPaging(60, 'test.other').size).toBe(60);
		expect(new CardPaging(60).size, 'a wall with no name keeps no memory').toBe(60);
	});
});

describe('what still asks for the whole page', () => {
	async function served() {
		const list = server(500);
		const paging = new CardPaging(60, 'test.whole');
		const first = await paging.fill('a', () => [], list.ask, list.read);
		return { list, paging, rows: first!.rows };
	}

	it('a re-read at the same size, which must see what a verb changed', async () => {
		const { list, paging, rows } = await served();
		await paging.fill('a', () => rows, list.ask, list.read);
		expect(list.asks).toEqual([
			{ limit: 60, offset: 0 },
			{ limit: 60, offset: 0 }
		]);
	});

	it('another question, even when only the size also moved', async () => {
		const { list, paging, rows } = await served();
		const on = wall(paging);
		on.draws(1);
		await paging.fill('b', () => rows, list.ask, list.read);
		expect(list.asks.at(-1)).toEqual({ limit: paging.size, offset: 0 });
		on.done();
	});

	it('rows edited on screen since they were served', async () => {
		const { list, paging, rows } = await served();
		const on = wall(paging);
		on.draws(1);
		await paging.fill('a', () => rows.slice(1), list.ask, list.read);
		expect(list.asks.at(-1)).toEqual({ limit: paging.size, offset: 0 });
		on.done();
	});
});

describe('called from the effect that draws the wall', () => {
	it('does not make that effect depend on the rows it hands in', async () => {
		/* Every wall calls `fill` from the effect watching the page's size and place, and then writes
		   the rows it gets back. Were the rows read tracked, each answer would start the effect again
		   and the wall would ask for ever, hanging the test. */
		const list = server(500);
		const paging = new CardPaging(10);
		const shown = $state({ rows: [] as string[] });
		let runs = 0;
		const stop = $effect.root(() => {
			$effect(() => {
				void paging.size;
				runs += 1;
				void paging
					.fill('', () => shown.rows, list.ask, list.read)
					.then((page) => {
						// Capped, so a fault FAILS here instead of asking for ever and hanging.
						if (page && runs < 3) shown.rows = page.rows;
					});
			});
		});
		flushSync();
		for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();
		flushSync();

		expect(shown.rows).toHaveLength(10);
		expect(runs, 'the answer started the effect again').toBe(1);
		expect(list.asks).toHaveLength(1);
		stop();
	});
});

describe('an overtaken read', () => {
	it('is not drawn, whichever order the answers land in', async () => {
		const paging = new CardPaging(10);
		const releases: Array<() => void> = [];
		const ask = (query: PageAsk) =>
			new Promise<{ rows: string[]; total: number; offset: number }>((resolve) => {
				releases.push(() => resolve({ rows: [`at-${query.limit}`], total: 1, offset: 0 }));
			});
		const read = (answer: { rows: string[]; total: number; offset: number }) => answer;

		const older = paging.fill('', () => [], ask, read);
		const newer = paging.fill('', () => [], ask, read);
		releases[1]();
		releases[0]();

		expect(await older).toBeNull();
		expect((await newer)?.rows).toEqual(['at-10']);
	});

	it('and its failure is not a fault on screen', async () => {
		const paging = new CardPaging(10);
		let fail: (error: Error) => void = () => {};
		const older = paging.fill(
			'',
			() => [],
			() => new Promise<never>((_, reject) => (fail = reject)),
			(answer: { rows: string[]; total: number; offset: number }) => answer
		);
		const newer = paging.fill(
			'',
			() => [],
			async () => ({ rows: ['x'], total: 1, offset: 0 }),
			(answer) => answer
		);
		fail(new Error('gone'));

		expect(await older).toBeNull();
		expect((await newer)?.rows).toEqual(['x']);
	});
});

/*
 * AN ANCHORED ARRIVAL ASKS ONCE. The page is asked for by a row, and only the answer says where
 * that row is, so the offset moves AFTER the rows land, and every wall watches the offset. Moved
 * plainly, the wall's effect would run again, find the same question at the same size, and ask for
 * the page it had just been handed. `land` moves it so the next `fill` answers from the rows held:
 * the NEXT one only.
 */
describe('landing an anchored page', () => {
	/** A server that resolves a row named `row-N` to offset N. */
	function anchored(length: number) {
		const rows = Array.from({ length }, (_, at) => `row-${at}`);
		const asks: PageAsk[] = [];
		return {
			asks,
			ask: async (query: PageAsk) => {
				asks.push(query);
				const offset = 'from' in query ? Number(query.from.slice(4)) : query.offset;
				return { rows: rows.slice(offset, offset + query.limit), total: rows.length, offset };
			},
			read: (answer: { rows: string[]; total: number; offset: number }) => answer
		};
	}

	it('asks nothing for the re-run its own landing causes', async () => {
		const paging = new CardPaging(10);
		const stand = anchored(100);
		paging.arrive({ from: 'row-40', near: null });
		let held: string[] = [];
		const first = await paging.fill('', () => held, stand.ask, stand.read);
		held = first?.rows ?? [];
		paging.land(first?.offset ?? 0);
		expect(paging.offset).toBe(40);
		expect(paging.anchor).toBeNull();

		const again = await paging.fill('', () => held, stand.ask, stand.read);
		expect(stand.asks).toHaveLength(1);
		expect(again?.rows).toEqual(held);
	});

	it('but the fill after that is a real question again, as a re-read after a verb must be', async () => {
		const paging = new CardPaging(10);
		const stand = anchored(100);
		paging.arrive({ from: 'row-40', near: null });
		let held: string[] = [];
		const first = await paging.fill('', () => held, stand.ask, stand.read);
		held = first?.rows ?? [];
		paging.land(first?.offset ?? 0);
		await paging.fill('', () => held, stand.ask, stand.read);
		await paging.fill('', () => held, stand.ask, stand.read);
		expect(stand.asks).toEqual([
			{ limit: 10, from: 'row-40' },
			{ limit: 10, offset: 40 }
		]);
	});

	it('leaves no mark when the offset did not move, so a later re-read is not served stale', async () => {
		const paging = new CardPaging(10);
		const stand = anchored(100);
		paging.arrive({ from: 'row-0', near: null });
		let held: string[] = [];
		const first = await paging.fill('', () => held, stand.ask, stand.read);
		held = first?.rows ?? [];
		paging.land(first?.offset ?? 0);
		await paging.fill('', () => held, stand.ask, stand.read);
		expect(stand.asks).toHaveLength(2);
	});
});

describe('a store filled through `fillHeld`', () => {
	/* A landing or a trim asks nothing, and must not say "Looking..." for a frame anyway: every wall
	   swaps its cards for the loading line, so the wall would blink away and back for a request
	   that never went. */
	it('says it is looking only while a request is actually out', async () => {
		const paging = new CardPaging(100);
		const stand = server(1000);
		const store: HeldWall<string> = {
			items: [],
			total: 0,
			at: 0,
			loaded: false,
			loading: false,
			failed: false
		};
		const seen: boolean[] = [];
		const watched = async (query: PageAsk) => {
			seen.push(store.loading);
			return stand.ask(query);
		};
		await fillHeld(store, paging, '', watched, stand.read, () => true);
		expect(seen).toEqual([true]);
		expect(store.loading).toBe(false);

		// The same page again at a smaller size: trimmed from the rows held, nothing asked.
		const view = wall(paging);
		view.draws(1);
		let raised = false;
		const before = Object.getOwnPropertyDescriptor(store, 'loading');
		let loading = store.loading;
		Object.defineProperty(store, 'loading', {
			configurable: true,
			get: () => loading,
			set: (value: boolean) => {
				if (value) raised = true;
				loading = value;
			}
		});
		try {
			await fillHeld(store, paging, '', watched, stand.read, () => true);
			expect(stand.asks).toHaveLength(1);
			expect(store.items).toHaveLength(paging.size);
			expect(raised).toBe(false);
		} finally {
			view.done();
			if (before) Object.defineProperty(store, 'loading', before);
		}
	});
});

/*
 * THE ROW THE ADDRESS NAMES IS GONE. Discarding the group that was a page's first card takes it off
 * the list, and it is exactly the row the address names, so the address carries the offset it
 * was at (`near`) as well, and the server serves that page when the row is gone. Two things are the
 * wall's to get right: sending `near` beside the row, and a `near` that now sits past the end of a
 * list that shrank. Most walls count with a window over the page's own rows, so a page past the end
 * says its list holds NOTHING, and the wall would draw "No ... waiting" over a list with cards in it.
 */
describe('coming back to a page whose first card is gone', () => {
	/** A server that has lost `gone`, serves `near` for it, and counts like a window: 0 past the end. */
	function shrunk(length: number) {
		const rows = Array.from({ length }, (_, at) => `row-${at}`);
		const asks: PageAsk[] = [];
		return {
			asks,
			ask: async (query: PageAsk) => {
				asks.push(query);
				const offset = 'from' in query ? (query.near ?? 0) : query.offset;
				const page = rows.slice(offset, offset + query.limit);
				return { rows: page, total: page.length > 0 ? rows.length : 0, offset };
			},
			read: (answer: { rows: string[]; total: number; offset: number }) => answer
		};
	}

	it('sends where the page was beside the row it began at', async () => {
		const paging = new CardPaging(10);
		const stand = shrunk(100);
		paging.arrive({ from: 'gone', near: 40 });

		const page = await paging.fill('', () => [], stand.ask, stand.read);

		expect(stand.asks[0]).toEqual({ limit: 10, from: 'gone', near: 40 });
		expect(page?.offset).toBe(40);
		expect(page?.rows[0]).toBe('row-40');
	});

	it('steps a page past the end back to the last page, though its count said nothing', async () => {
		/* The last page held one card, and it was the one discarded: `near` is now the length. */
		const paging = new CardPaging(10);
		const stand = shrunk(25);
		paging.arrive({ from: 'gone', near: 25 });

		const page = await paging.fill('', () => [], stand.ask, stand.read);

		expect(page?.offset).toBe(20);
		expect(page?.rows).toEqual(['row-20', 'row-21', 'row-22', 'row-23', 'row-24']);
		expect(page?.total).toBe(25);
	});

	it('is the empty front when the list really is empty now', async () => {
		const paging = new CardPaging(10);
		const stand = shrunk(0);
		paging.arrive({ from: 'gone', near: 30 });

		const page = await paging.fill('', () => [], stand.ask, stand.read);

		expect(page?.offset).toBe(0);
		expect(page?.rows).toEqual([]);
		expect(page?.total).toBe(0);
	});
});
