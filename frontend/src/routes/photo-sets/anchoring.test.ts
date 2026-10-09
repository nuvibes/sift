// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Photo Sets wall keeps the page it was left on. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Wall from './+page.svelte';
import { photoSetsSort } from './sort.svelte';
import { LAST_PAGE, MEASURED, SHORT, arrival, wallHarness } from '$lib/design/testing-walls';

/* The fewest cards that show it, the arithmetic the round trip rests on (8 to a page, a list of
 * 17, Last landing on row 16) and why the arrival carries `near`: all in
 * `$lib/design/testing-walls`, once. */
const NAMED = LAST_PAGE;
const ARRIVAL = arrival('/photo-sets', 'set', NAMED);

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[], whole: 0 }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/photo-sets') }));
const router = vi.hoisted(() => ({ replaced: [] as string[] }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			if (path !== '/photo-sets') return {};
			const query = options?.query ?? {};
			server.asks.push(query);
			// The server resolves a row into its position in the scoped, ordered list.
			const named = String(query.from ?? '');
			const offset = named ? Number(named.replace('set', '')) : Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			return {
				items: Array.from(
					{ length: Math.max(0, Math.min(limit, server.whole - offset)) },
					(_x, index) => ({
						id: `set${offset + index}`,
						name: `Shoot ${offset + index}`,
						cover_asset_id: 'cover1',
						cover_at_ms: 4200,
						art: 'stamp',
						cover_upload_id: null,
						item_count: 3,
						origin: null,
						origin_url: null,
						vault: false,
						favorite: false,
						pinned: false,
						rating: 0,
						shared: false,
						restricted: false
					})
				),
				total: server.whole,
				limit,
				offset
			};
		}),
		post: vi.fn(async () => undefined),
		put: vi.fn(async () => undefined),
		del: vi.fn(async () => undefined)
	},
	ApiError: class extends Error {}
}));

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn((url: string) => {
		router.replaced.push(url);
		// The half that matters: the bar moves, and `page.url` does not.
		window.history.replaceState({}, '', url);
	})
}));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: {
		state: {},
		get url() {
			return at.url;
		}
	}
}));

// The rows live in the page, not in a store, so unmounting the wall already drops them.
const { wall, measuredWall, leave, press } = wallHarness(Wall, { server, router, at }, null);

beforeEach(() => {
	photoSetsSort.value = 'largest';
});

describe('the Photo Sets wall and the row in its address', () => {
	it('asks the server for the row the address names, not for an offset', async () => {
		const host = await wall(ARRIVAL);
		const first = server.asks[0];
		expect(first.from).toBe(`set${NAMED}`);
		expect(first.offset).toBeUndefined();
		expect(host.textContent).toContain(`Shoot ${NAMED}`);
	});

	it('leaves the address naming the page it is on, even the one it opened at', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		expect(window.location.search).toBe('?from=set0&near=0');
		await press('last');
		// Back where it started, and the address has to say so: the way back reads the address the
		// wall was LEFT at, so an address still naming the front page opens the wall there.
		expect(window.location.search).toBe(`?from=set${NAMED}&near=${NAMED}`);
	});

	it('never writes that row onto anything but the wall', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		for (const written of router.replaced) {
			expect(written.split('?')[0]).toBe('/photo-sets');
		}
	});

	it('links to a set without the query the wall itself is using', async () => {
		const drawn = await wall(ARRIVAL);
		const links = [...drawn.querySelectorAll('a[href^="/photo-sets/"]')];
		expect(links.length).toBeGreaterThan(0);
		for (const link of links) {
			expect(link.getAttribute('href')).not.toContain('?');
		}
	});
});

/* ONE FIRST PAGE. */
describe('the Photo Sets wall asks for its first page once', () => {
	const PAST_THE_FIRST_PAGE = 25;

	it('asks for the remainder only when the first card is measured bigger than the fallback', async () => {
		server.whole = PAST_THE_FIRST_PAGE;
		await measuredWall('/photo-sets', { width: 150, height: 300 });
		expect(server.asks.map((ask) => [Number(ask.offset), Number(ask.limit)])).toEqual([
			[0, 24],
			[24, 8]
		]);
	});

	it('asks at the measured size on the next visit, and only once', async () => {
		server.whole = SHORT;
		await measuredWall('/photo-sets');
		leave();
		await wall('/photo-sets');
		expect(server.asks.map((ask) => Number(ask.limit))).toEqual([MEASURED]);
	});

	it('asks once for an anchored arrival: landing on the row it named asks nothing', async () => {
		await wall(`/photo-sets?from=set${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(server.asks[0].from).toBe(`set${NAMED}`);
	});
});

/* THE CARD'S COVER ADDRESS CARRIES THE ROW'S MOMENT AND TOKEN. */
describe('the Photo Sets wall hands each card its cover moment', () => {
	it("puts the row's moment and token on the cover's address", async () => {
		const host = await wall('/photo-sets');
		const src = host.querySelector('img.picture')?.getAttribute('src') ?? '';
		expect(src).toMatch(/\?v=stamp\.cover1\.4200$/);
	});
});

/* A COLD LINK KEEPS ITS PLACE. A fresh load of `?from=` must not re-ask for offset 0 after the
 * first card measures and before the wall has moved to the offset the anchor resolved to. */
describe('the Photo Sets wall opened cold at a row', () => {
	it('keeps the place the link names when the first card measures, and asks once', async () => {
		await measuredWall(`/photo-sets?from=set${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(window.location.search).toBe(`?from=set${NAMED}&near=${NAMED}`);
	});
});

describe('the words in the address', () => {
	it('asks for the Photo Sets whose names hold the words anywhere', async () => {
		await wall('/photo-sets?q=dusk');
		const asked = server.asks.find((one) => one.prefix === 'dusk');
		expect(asked, 'the words never reached the list route').toBeTruthy();
		expect(asked?.anywhere).toBe('true');
	});
});
