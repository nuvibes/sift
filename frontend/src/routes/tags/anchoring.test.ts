// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Tags wall keeps the page it was left on.
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
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Wall from './+page.svelte';
import { tagsSort } from './sort.svelte';
import { TAGS_PER_PAGE, tags } from '$lib/entity/tags.svelte';
import { LAST_PAGE, MEASURED, SHORT, arrival, wallHarness } from '$lib/design/testing-walls';

/*
 * The fewest cards that show it, the arithmetic the round trip rests on (8 to a page, a list of 17,
 * Last landing on row 16) and why the arrival carries `near`: all in `$lib/design/testing-walls`, once.
 */
const NAMED = LAST_PAGE;
const ARRIVAL = arrival('/tags', 'tag', NAMED);

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[], whole: 0 }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/tags') }));
const router = vi.hoisted(() => ({ replaced: [] as string[] }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/tags') return { items: [], total: 0, limit: 50, offset: 0 };
			server.asks.push(query);
			// The server resolves a row into its position in the scoped, ordered list. Here the row
			// is named after its position, so the stand-in can answer the same question.
			const named = String(query.from ?? '');
			const offset = named ? Number(named.replace('tag', '')) : Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			return {
				items: Array.from(
					{ length: Math.max(0, Math.min(limit, server.whole - offset)) },
					(_x, index) => ({
						id: `tag${offset + index}`,
						name: `Word ${offset + index}`,
						cover_asset_id: 'cover1',
						cover_at_ms: 4200,
						art: 'stamp',
						cover_upload_id: null,
						asset_count: 3,
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

const { wall, measuredWall, leave, press } = wallHarness(Wall, { server, router, at }, tags);

beforeEach(() => {
	tagsSort.value = 'largest';
});

describe('the Tags wall and the row in its address', () => {
	it('asks the server for the row the address names, not for an offset', async () => {
		const host = await wall(ARRIVAL);
		const first = server.asks[0];
		expect(first.from).toBe(`tag${NAMED}`);
		expect(first.offset).toBeUndefined();
		expect(host.textContent).toContain(`Word ${NAMED}`);
	});

	it('leaves the address naming the page it is on, even the one it opened at', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		expect(window.location.search).toBe('?from=tag0&near=0');
		await press('last');
		// Back where it started, and the address has to say so: the way back reads the address the
		// wall was LEFT at, so an address still naming the front page opens the wall there.
		expect(window.location.search).toBe(`?from=tag${NAMED}&near=${NAMED}`);
	});

	it('never writes that row onto anything but the wall', async () => {
		await measuredWall(ARRIVAL);
		await press('first');
		for (const written of router.replaced) {
			expect(written.split('?')[0]).toBe('/tags');
		}
	});
});

/*
 * ONE FIRST PAGE. The first card's measurement trims the rows it holds (see `CardPaging.fill`),
 * and the next visit asks at the measured size straight away, because the wall is named, so the
 * first page is asked for once. `measuredWall` stands in for the browser laying the cards out, which
 * jsdom never does.
 *
 * A DOZEN ROWS (`SHORT`), for the reason `$lib/design/testing-walls` gives: what the first two tests
 * below count is requests, and a first page of 60 cards ran past the five-second limit on a slow
 * runner.
 */
describe('the Tags wall asks for its first page once', () => {
	it('trims the page it holds when the first card is measured, and asks nothing more', async () => {
		server.whole = SHORT;
		await measuredWall('/tags');
		expect(server.asks.map((ask) => [Number(ask.offset), Number(ask.limit)])).toEqual([
			[0, TAGS_PER_PAGE]
		]);
	});

	it('asks at the measured size on the next visit, and only once', async () => {
		server.whole = SHORT;
		await measuredWall('/tags');
		// The rows go with the wall (`leave`), or the next visit measures a held card at once.
		leave();
		await wall('/tags');
		expect(server.asks.map((ask) => Number(ask.limit))).toEqual([MEASURED]);
	});

	it('asks once for an anchored arrival: landing on the row it named asks nothing', async () => {
		await wall(`/tags?from=tag${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(server.asks[0].from).toBe(`tag${NAMED}`);
	});
});

/*
 * THE CARD'S COVER ADDRESS CARRIES THE ROW'S MOMENT AND TOKEN. The listing row names the moment a
 * video cover was taken at (`cover_at_ms`) and a token that changes whenever the picture does
 * (`art`); a card handed neither asks for an address that never moves when the cover does. Every
 * row of this stand-in server carries both, so the first card's picture must name both.
 */
describe('the Tags wall hands each card its cover moment', () => {
	it("puts the row's moment and token on the cover's address", async () => {
		const host = await wall('/tags');
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
describe('the Tags wall opened cold at a row', () => {
	it('keeps the place the link names when the first card measures, and asks once', async () => {
		await measuredWall(`/tags?from=tag${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(window.location.search).toBe(`?from=tag${NAMED}&near=${NAMED}`);
	});
});

describe('the words in the address', () => {
	it('asks for the Tags whose names hold the words anywhere', async () => {
		await wall('/tags?q=dusk');
		const asked = server.asks.find((one) => one.prefix === 'dusk');
		expect(asked, 'the words never reached the list route').toBeTruthy();
		expect(asked?.anywhere).toBe('true');
	});
});
