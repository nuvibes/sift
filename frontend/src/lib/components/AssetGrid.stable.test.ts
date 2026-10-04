/*
 * A file arriving must not disturb the wall somebody is reading.
 *
 * The wall is one flat keyed list, so a tile that changes row keeps its element (Svelte cannot move
 * a node between two each-block instances). A greedy pack still re-partitions every row behind an
 * insertion at the front, so the wall does not take arrivals by itself: it counts what has landed
 * above it and moves when asked. Both halves are asserted here.
 *
 * Asserted on element identity (`toBe` on the node, never `toEqual`): the same picture is drawn
 * either way, and only identity separates a tile that stayed put from an identical replacement.
 *
 * The layout is arithmetic over the container's width, and jsdom reports every width as zero. The
 * width is stubbed below; the rows, the pack and the keyed each are real.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AssetGrid from './AssetGrid.svelte';
import { arrivals } from '$lib/library/changes.svelte';
import { TILE_ID } from '$lib/components/common';

const page = vi.hoisted(() => ({ items: [] as Record<string, unknown>[] }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/browse') }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path === '/assets') {
				/*
				 * `from` is honoured, which is what lets this file tell the behaviour from its
				 * absence: the wall's catch-up is anchored on the file at the top of the page, so a
				 * mock ignoring the anchor would hand back a page shifted by the arrival and every
				 * assertion about the wall holding still would pass against the fault.
				 *
				 * A `from` naming a file that is not there serves the beginning, as the real route
				 * does for a file deleted since the page was drawn.
				 */
				const asked = options?.query ?? {};
				const anchor = asked.from as string | undefined;
				const offset =
					anchor === undefined
						? Number(asked.offset ?? 0)
						: Math.max(
								0,
								page.items.findIndex((one) => one.id === anchor)
							);
				const limit = Number(asked.limit ?? page.items.length);
				return {
					items: page.items.slice(offset, offset + limit),
					total: page.items.length,
					limit,
					offset,
					complete: true
				};
			}
			if (path === '/search/parse') return { text: '', clauses: [], terms: {}, problems: [] };
			if (path === '/assets/facets') return { facet: 'media', values: [] };
			return {};
		}),
		post: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

/* `replaceState` as well as `goto`: the wall remembers where it is in the address as tiles land,
   and a mock without it throws from a promise nobody awaits: three passing tests beside three
   unhandled errors, which is a suite that has stopped being able to say anything. */
vi.mock('$app/navigation', () => ({ goto: vi.fn(), replaceState: vi.fn() }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		get url() {
			return at.url;
		}
	}
}));

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

/** A file, in whatever shape the wall needs to lay one out and draw it. */
function file(id: string, shape: { width: number; height: number }) {
	return {
		id,
		media_type: 'video',
		duration_ms: 4000,
		thumb: true,
		favorite: false,
		rating: 0,
		concealed: false,
		shared: false,
		...shape
	};
}

/*
 * Mixed proportions on purpose.
 *
 * A wall of identical shapes packs the same number into every row, so inserting one at the front
 * would shift every tile by exactly one place and the fault would still show, but a real library
 * is mixed, and mixing them is what makes the row boundaries move to different files rather than
 * just along by one. It is the harder case and it is the ordinary one.
 */
function library(count: number, from = 0) {
	const shapes = [
		{ width: 1920, height: 1080 },
		{ width: 1080, height: 1920 },
		{ width: 1440, height: 1080 }
	];
	return Array.from({ length: count }, (_, index) =>
		file(`a${from + index}`, shapes[(from + index) % shapes.length])
	);
}

beforeEach(() => {
	// jsdom reports every element as zero wide, and a wall of no width lays out no rows at all,
	// which would make every assertion below pass against a component that drew nothing.
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get: () => 1200
	});
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get: () => 900
	});
});

afterEach(() => {
	if (mounted) void unmount(mounted);
	mounted = null;
	host?.remove();
	page.items = [];
});

async function settle() {
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

async function wall() {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(AssetGrid, {
		target: host,
		props: { query: {}, title: 'Files', empty: 'Nothing here' }
	});
	await settle();
	await settle();
	return host;
}

/** Every tile on screen, by the file it is a picture of. */
function tiles(where: HTMLElement): Map<string, Element> {
	const found = new Map<string, Element>();
	for (const element of where.querySelectorAll(`[${TILE_ID}]`)) {
		const id = element.getAttribute(TILE_ID);
		if (id) found.set(id, element);
	}
	return found;
}

describe('a file arriving at the front of the wall', () => {
	it('draws the tiles at all', async () => {
		// The known positive. Without it every assertion below is satisfied by a wall that draws
		// nothing, which is exactly what a zero-width container would produce.
		page.items = library(12);

		const drawn = tiles(await wall());

		expect(drawn.size, 'no tiles were drawn, so nothing below proves anything').toBe(12);
	});

	/*
	 * Driven by the press rather than the arrival. An arrival does not re-row anything, so element
	 * identity across one proves nothing; the wall re-lays-out on a resize, a re-sort, a page
	 * turned, and somebody taking the files that arrived while they were reading, and that last is
	 * where tiles move.
	 *
	 * Identity, never appearance: the same picture is drawn either way. `toBe` on the node.
	 */
	it('leaves every tile that was already there in its own element when the new ones are taken', async () => {
		page.items = library(12);
		const before = tiles(await wall());

		// One new file at the front, exactly as a newest-first wall receives one during an import.
		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		const take = [...host.querySelectorAll('button')].find((one) =>
			/\bnew\b/.test(one.textContent ?? '')
		);
		expect(take, 'there was nothing to press, so nothing below is being tested').toBeDefined();
		take?.click();
		await settle();
		await settle();

		const after = tiles(host);
		expect(after.get('new'), 'pressing it did not bring the new file in').toBeDefined();

		// Not every tile: the page holds as many as fill its rows, so one arriving at the front
		// pushes one off the end. Those that are still drawn must be the SAME elements.
		let kept = 0;
		for (const [id, element] of before) {
			const now = after.get(id);
			if (now === undefined) continue;
			kept += 1;
			expect(now, `${id} was destroyed and rebuilt rather than moved`).toBe(element);
		}
		expect(kept, 'almost nothing survived, so identity was never really tested').toBeGreaterThan(8);
	});

	/*
	 * The page holds the files it was asked for, and what arrived above it is counted. A justified
	 * wall packs greedily from the front, so re-laying it out on each arrival moves every tile, a
	 * strobe made of the same nodes even with every element kept. The positive control is that the
	 * count went up, which separates a page holding still from one that has stopped listening.
	 */
	it('leaves every tile exactly where it was', async () => {
		page.items = library(12);
		const before = tiles(await wall());
		const first = before.get('a0') as HTMLElement;
		const was = first.style.transform;

		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		expect(
			was,
			'the tile was never placed, so holding still cannot be told from not drawing'
		).not.toBe('');
		expect(first.style.transform, 'the wall re-laid-out under somebody reading it').toBe(was);
	});

	it('says how many arrived rather than showing them', async () => {
		/* The positive control for the assertion above, and it has to be here: a wall that never
		 * re-read anything at all would also leave every tile where it was. This is what says the
		 * grid heard the arrival, counted it, and offered it, while moving nothing. */
		page.items = library(12);
		const where = await wall();

		expect(where.textContent, 'a count was already showing before anything arrived').not.toMatch(
			/\bnew\b/
		);

		page.items = [file('new', { width: 1920, height: 1080 }), ...library(12)];
		arrivals.changed();
		await settle();
		await settle();

		expect(
			tiles(host).get('new'),
			'the new file was drawn into the wall after all'
		).toBeUndefined();
		expect(host.textContent, 'nothing offered the file that arrived').toMatch(/1\s*new/);
	});

	/*
	 * The count is the distance between where the page was asked to start and where it sits now, so
	 * it only holds while a catch-up leaves the first number alone. The steady state never reaches
	 * that line; it matters when a catch-up finds the page itself changed (a file favorited,
	 * hidden, renamed or deleted), where moving the mark would silently zero the untaken arrivals.
	 */
	it('keeps counting the new ones when something on the page changes too', async () => {
		page.items = library(12);
		await wall();

		const changed = library(12);
		changed[0] = { ...changed[0], favorite: true };
		page.items = [
			file('n1', { width: 1920, height: 1080 }),
			file('n2', { width: 1080, height: 1920 }),
			...changed
		];
		arrivals.changed();
		await settle();
		await settle();

		expect(host.textContent, 'the count reset when the page was rewritten under it').toMatch(
			/2\s*new/
		);
	});
});
