// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The wall keeps the page it was left on.
 *
 * The position is the half that is written into the address; a wall that writes nothing there loses
 * its place on the way back from a row. The test beside the Photo Sets wall carries the whole of
 * the reasoning; this is the same four properties, one wall over.
 *
 * The stand-in for `replaceState` does BOTH halves the router does (it records, and it moves the
 * browser's address), because a mock that only records cannot show what the fault costs: a wall
 * that decided it had nothing to write by comparing against the address it ARRIVED at.
 *
 * The first test is the known positive. Every assertion under it is also satisfied by a wall that
 * asks the server for nothing at all.
 */
import { readFileSync } from 'node:fs';

import { beforeEach, describe, expect, it, vi } from 'vitest';
import Wall from './+page.svelte';
import { SITES_PER_PAGE, sites } from '$lib/people/people.svelte';
import { LAST_PAGE, MEASURED, SHORT, arrival, wallHarness } from '$lib/design/testing-walls';

/*
 * The fewest cards that show it, the arithmetic the round trip rests on (8 to a page, a list of 17,
 * Last landing on row 16) and why the arrival carries `near`: all in `$lib/design/testing-walls`, once.
 */
const NAMED = LAST_PAGE;
const ARRIVAL = arrival('/sites', 'place', NAMED);

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[], whole: 0 }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/sites') }));
const router = vi.hoisted(() => ({ replaced: [] as string[] }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/sites') return { items: [], total: 0, limit: 50, offset: 0 };
			server.asks.push(query);
			// The server resolves a row into its position in the scoped, ordered list. Here the row
			// is named after its position, so the stand-in can answer the same question.
			const named = String(query.from ?? '');
			const offset = named ? Number(named.replace('place', '')) : Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			return {
				items: Array.from(
					{ length: Math.max(0, Math.min(limit, server.whole - offset)) },
					(_x, index) => ({
						id: `place${offset + index}`,
						name: `Outlet ${offset + index}`,
						cover_asset_id: 'cover1',
						cover_at_ms: 4200,
						art: 'stamp',
						cover_upload_id: null,
						icon: null,
						site_url: null,
						notes: null,
						asset_count: 3,
						people_count: 7,
						counts: { people: 7 },
						o_count: 0,
						favorite: false,
						pinned: false,
						hidden: false,
						keep_local: false,
						rating: 0,
						record: null,
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

const { wall, measuredWall, leave, press } = wallHarness(Wall, { server, router, at }, sites);

beforeEach(() => {
	sites.sort = 'largest';
});

describe('the Sites wall and the row in its address', () => {
	it('asks the server for the row the address names, not for an offset', async () => {
		const host = await wall(ARRIVAL);
		const first = server.asks[0];
		expect(first.from).toBe(`place${NAMED}`);
		expect(first.offset).toBeUndefined();
		expect(host.textContent).toContain(`Outlet ${NAMED}`);
	});

	it('leaves the address naming the page it is on, even the one it opened at', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		expect(window.location.search).toBe('?from=place0&near=0');
		await press('last');
		// Back where it started, and the address has to say so: the way back reads the address the
		// wall was LEFT at, so an address still naming the front page opens the wall there.
		expect(window.location.search).toBe(`?from=place${NAMED}&near=${NAMED}`);
	});

	it('never writes that row onto anything but the wall', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		for (const written of router.replaced) {
			expect(written.split('?')[0]).toBe('/sites');
		}
	});
});

/* THE WORDS IN THE ADDRESS ARE THE WALL'S SEARCH: what the search band's See all opens, and what
   Back comes to. */
describe('the Sites wall and the words in its address', () => {
	it('asks for the Sites the words name, with the words in the box', async () => {
		const host = await wall('/sites?q=outlet');
		expect(server.asks[0].prefix).toBe('outlet');
		expect(server.asks[0].anywhere, 'the words matched only the start of a name').toBe('true');
		expect((host.querySelector('input') as HTMLInputElement).value).toBe('outlet');
	});
});

/* NO MARK IN THE CORNER OF A SITE'S OWN TILE.
 *
 * Read out of the wall's source rather than out of a rendered card, and deliberately: what has to
 * hold is that this wall passes `EntityCard` NO marks at all. A rendered assertion would pass
 * just as well for a wall that passed an empty list on every card while still computing one.
 *
 * The prop stays on `EntityCard` for a tile about something that is ON several sites, where the
 * marks are the only place that fact appears. This wall would be the one caller for which it
 * meant "this thing is itself".
 */
it('passes no marks to the cards, because a Site tile already says which site it is', () => {
	const source = readFileSync('src/routes/sites/+page.svelte', 'utf8');

	expect(source).not.toContain('sites={');
	expect(source).not.toContain('markFor');
	// And nothing is left importing the two addresses a mark was built from.
	expect(source).not.toContain('site-art.svelte');
});

/*
 * EACH FIGURE ONCE. A Site's card says its files in words under the name, as every wall's card
 * does, and its people as the People figure in the row under them, which opens that tab. The words
 * saying "7 people" as well would be the same number twice on one card.
 */
it('says each figure on a Site card once: the files in words, the people as a figure', async () => {
	const host = await wall('/sites');
	const card = host.querySelector('.card');
	expect(card?.querySelector('.detail')?.textContent?.trim()).toBe('3 files');
	expect(card?.textContent?.match(/\b7\b/g) ?? []).toHaveLength(1);
	expect(card?.querySelector('[data-cell="people"] .figure')?.textContent?.trim()).toBe('7');
});

/*
 * ONE FIRST PAGE. The first card's measurement trims the rows it holds (see `CardPaging.fill`),
 * and the next visit asks at the measured size straight away, because the wall is named, so the
 * first page is asked for once. `measuredWall` stands in for the browser laying the cards out,
 * which jsdom never does; the library the first two tests open is `SHORT`, a dozen rows.
 */
describe('the Sites wall asks for its first page once', () => {
	it('trims the page it holds when the first card is measured, and asks nothing more', async () => {
		server.whole = SHORT;
		await measuredWall('/sites');
		expect(server.asks.map((ask) => [Number(ask.offset), Number(ask.limit)])).toEqual([
			[0, SITES_PER_PAGE]
		]);
	});

	it('asks at the measured size on the next visit, and only once', async () => {
		server.whole = SHORT;
		await measuredWall('/sites');
		// The rows go with the wall (`leave`), or the next visit measures a held card at once.
		leave();
		await wall('/sites');
		expect(server.asks.map((ask) => Number(ask.limit))).toEqual([MEASURED]);
	});

	it('asks once for an anchored arrival: landing on the row it named asks nothing', async () => {
		await wall(`/sites?from=place${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(server.asks[0].from).toBe(`place${NAMED}`);
	});
});

/*
 * THE CARD'S COVER ADDRESS CARRIES THE ROW'S MOMENT AND TOKEN. The listing row names the moment a
 * video cover was taken at (`cover_at_ms`) and a token that changes whenever the picture does
 * (`art`); a card handed neither asks for an address that never moves when the cover does. Every
 * row of this stand-in server carries both, so the first card's picture must name both.
 */
describe('the Sites wall hands each card its cover moment', () => {
	it("puts the row's moment and token on the cover's address", async () => {
		const host = await wall('/sites');
		const src = host.querySelector('img.picture')?.getAttribute('src') ?? '';
		expect(src).toMatch(/\?v=stamp\.cover1\.4200$/);
	});
});

/*
 * A COLD LINK KEEPS ITS PLACE. A fresh load of `?from=` must not re-ask for offset 0 after the
 * first card measures and before the wall has moved to the offset the anchor resolved to. The rows
 * and the landing are one synchronous turn (`fillHeld`, `CardPaging.land`).
 *
 * The link names row 16 of 17 (`LAST_PAGE` of `WHOLE`): the only row the first request answers, at
 * the unmeasured size, and the first row of the last page once the first card measures 8 to a page.
 */
describe('the Sites wall opened cold at a row', () => {
	it('keeps the place the link names when the first card measures, and asks once', async () => {
		await measuredWall(`/sites?from=place${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(window.location.search).toBe(`?from=place${NAMED}&near=${NAMED}`);
	});
});
