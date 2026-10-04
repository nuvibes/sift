// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * ONE FIRST PAGE on the People wall.
 *
 * The first measurement trims the rows held (see `CardPaging.fill`), so the wall does not ask for
 * its first page at the unmeasured size and then again at the size it measured; the landing asks
 * nothing (`CardPaging.land`), so a cold `?from=` link keeps its place rather than re-asking for
 * offset 0; and the next visit asks at the measured size because the wall is named.
 *
 * The mocks are the Sites wall's (`../sites/anchoring.test.ts`), one noun over. `measuredWall`
 * stands in for the layout jsdom never does.
 *
 * THE FEWEST CARDS THAT SHOW IT, and the arithmetic every test here rests on (600px cards, 8 to a
 * page; a list of 17 with pages at 0, 8 and 16; a dozen rows for a first-page test), are in
 * `$lib/design/testing-walls`, once, with the Sites, Photo Sets, Collections and Tags walls.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Wall from './+page.svelte';
import { PEOPLE_PER_PAGE, people } from '$lib/people/people.svelte';
import { MEASURED, SHORT, wallHarness } from '$lib/design/testing-walls';

/*
 * The cold link names row 8 of the 17: the first request, at the unmeasured size, answers rows 8 to
 * 16, nine of them, and when the first card measures they are trimmed to the 8 of the middle page,
 * which row 8 begins. So this link lands mid-list and trims more rows than it keeps, where the other
 * walls' cold links land on their last page.
 */
const NAMED = MEASURED;

const server = vi.hoisted(() => ({ asks: [] as Record<string, unknown>[], whole: 0 }));
const at = vi.hoisted(() => ({ url: new URL('http://localhost/people') }));
const router = vi.hoisted(() => ({ replaced: [] as string[] }));

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn(async (path: string, options?: { query?: Record<string, unknown> }) => {
			const query = options?.query ?? {};
			if (path !== '/people') return { items: [], total: 0, limit: 50, offset: 0 };
			server.asks.push(query);
			// The server resolves a row into its position in the scoped, ordered list. Here the row
			// is named after its position, so the stand-in can answer the same question.
			const named = String(query.from ?? '');
			const offset = named ? Number(named.replace('one', '')) : Number(query.offset ?? 0);
			const limit = Number(query.limit ?? 24);
			return {
				items: Array.from(
					{ length: Math.max(0, Math.min(limit, server.whole - offset)) },
					(_x, index) => ({
						id: `one${offset + index}`,
						name: `Model ${offset + index}`,
						cover_asset_id: 'cover1',
						cover_at_ms: 4200,
						art: 'stamp',
						cover_upload_id: null,
						icon: null,
						site_url: null,
						notes: null,
						asset_count: 3,
						people_count: 0,
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

const { wall, measuredWall, leave } = wallHarness(Wall, { server, router, at }, people);

beforeEach(() => {
	people.sort = 'largest';
});

describe('the People wall asks for its first page once', () => {
	it('trims the page it holds when the first card is measured, and asks nothing more', async () => {
		server.whole = SHORT;
		await measuredWall('/people');
		expect(server.asks.map((ask) => [Number(ask.offset), Number(ask.limit)])).toEqual([
			[0, PEOPLE_PER_PAGE]
		]);
	});

	it('asks at the measured size on the next visit, and only once', async () => {
		server.whole = SHORT;
		await measuredWall('/people');
		// The rows go with the wall (`leave`), or the next visit measures a held card at once.
		leave();
		await wall('/people');
		expect(server.asks.map((ask) => Number(ask.limit))).toEqual([MEASURED]);
	});

	it('keeps the place a cold link names when the first card measures, and asks once', async () => {
		const host = await measuredWall(`/people?from=one${NAMED}`);
		expect(server.asks).toHaveLength(1);
		expect(server.asks[0].from).toBe(`one${NAMED}`);
		expect(host.textContent).toContain(`Model ${NAMED}`);
		expect(window.location.search).toBe(`?from=one${NAMED}&near=${NAMED}`);
	});
});

/*
 * THE CARD'S COVER ADDRESS CARRIES THE ROW'S MOMENT AND TOKEN. The listing row names the moment a
 * video cover was taken at (`cover_at_ms`) and a token that changes whenever the picture does
 * (`art`); a card handed neither asks for an address that never moves when the cover does. Every
 * row of this stand-in server carries both, so the first card's picture must name both.
 */
describe('the People wall hands each card its cover moment', () => {
	it("puts the row's moment and token on the cover's address", async () => {
		const host = await wall('/people');
		const src = host.querySelector('img.picture')?.getAttribute('src') ?? '';
		expect(src).toMatch(/\?v=stamp\.cover1\.4200$/);
	});
});
