import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The walls of uniform cards: how big a page they ask for, and whether the server will take it.
 *
 * ## Why this is a browser test and not a unit one
 *
 * The page size is MEASURED off a real screen. A jsdom document has no layout, so the measurement
 * falls back to a fixed number and the interesting case never happens.
 *
 * The interesting case is a big monitor. On one, a wall measures room for well over a hundred cards
 * and asks for them, and a route that capped `limit` lower would refuse the request as malformed
 * before any handler ran, and the screen would report the refusal as the thing being gone: a
 * statement about somebody's library, on a screen where nothing is wrong.
 *
 * So the width below is not decoration. At 1024 the measured page stays under sixty and the whole
 * fault is invisible.
 *
 * ## Two halves, because neither is enough alone
 *
 * **The client asks big**: proven against a mocked wall, because a wall needs cards on it before
 * it has anything to measure. An empty library measures nothing and falls back to sixty, which is a
 * test that passes while proving the opposite of what it claims.
 *
 * **The server takes big**: proven by asking the real thing, unmocked, for the largest page the
 * client could ever want.
 */

/** Wide and tall enough that a measured page is well over sixty. */
const BIG = { width: 2560, height: 1440 };

/** A window that plainly holds fewer cards, so "sized to the screen" has two readings to compare. */
const SMALL = { width: 1024, height: 700 };

/** The largest page any wall will ask for. `MAX_PAGE_SIZE` on the server, `SERVER_PAGE_CAP` here. */
const CEILING = 200;

/** Every `limit`, `offset` and `from` the client asked for, in order. */
type Ask = { limit: number; offset: number; from: string | null };

/** A People wall with enough cards on it to measure. */
function peopleWall(page: Page, count: number): Ask[] {
	const asks: Ask[] = [];
	void page.route('**/api/people?*', async (route) => {
		const url = new URL(route.request().url());
		const limit = Number(url.searchParams.get('limit') ?? '0');
		const offset = Number(url.searchParams.get('offset') ?? '0');
		asks.push({ limit, offset, from: url.searchParams.get('from') });
		await route.fulfill({
			json: {
				items: Array.from({ length: Math.max(0, Math.min(limit, count - offset)) }, (_, i) => ({
					id: `p${offset + i}`,
					name: `Person ${offset + i}`,
					vault: false,
					asset_count: 3,
					cover_asset_id: null,
					cover_track_id: null,
					favorite: false,
					rating: null,
					shared: false,
					restricted: false
				})),
				total: count,
				limit,
				offset
			}
		});
	});
	return asks;
}

test.beforeEach(async ({ page }) => {
	await page.setViewportSize(BIG);
});

/**
 * The measured page the People wall settles on at this window size, read off its pager.
 *
 * Read off the SCREEN rather than off the requests, because the requests do not say it. The first
 * ask is the fallback (the wall has nothing to measure until a card is drawn), and once it has
 * measured, the wall asks only for what the rows it already holds do not cover (`CardPaging.fill`):
 * a screen that holds more asks for the remainder, and one that holds fewer cuts the rows it has
 * and asks for nothing. So the last request is a remainder or the fallback, and neither is the page.
 * The pager's readout ("1-64 of 500") is.
 */
async function settledPage(page: Page): Promise<number> {
	await page.goto('/people');
	// A real card on screen is what the measurement reads, so waiting for one is waiting for the
	// thing under test. Never `networkidle`: Sift holds a live connection and it never settles.
	await expect(page.locator('.card').first()).toBeVisible();
	const readout = page.getByRole('button', { name: 'Go to a position' });
	const last = async () => {
		const found = (await readout.innerText()).replace(/\s+/g, ' ').match(/^1-(\d+) of/);
		return found ? Number(found[1]) : -1;
	};
	// Two readings that agree is what "settled" means: reading before the measurement lands is
	// reading the fallback.
	let seen = -2;
	for (let tries = 0; tries < 20; tries += 1) {
		const now = await last();
		if (now === seen && now > 0) break;
		seen = now;
		await page.waitForTimeout(150);
	}
	return seen;
}

test('a wall asks for a page sized to the screen, not a fixed one', async ({ page }) => {
	/*
	 * Two windows, because that is the claim: the page size FOLLOWS the screen. One window can only
	 * say the wall asked for some number, and every fixed number satisfies that. "More than sixty"
	 * is not the claim: sixty is the fallback, and the cards are large enough (the ladder starts at
	 * 260px) that even a 2560x1440 window holds well under sixty of them.
	 */
	const asks = peopleWall(page, 500);
	await signInAsAdmin(page);

	await page.setViewportSize(BIG);
	const big = await settledPage(page);
	// And the server was asked for all of it, however the asks were split.
	expect(Math.max(...asks.map((ask) => ask.offset + ask.limit))).toBeGreaterThanOrEqual(big);

	await page.setViewportSize(SMALL);
	const small = await settledPage(page);

	expect(
		big,
		`A ${BIG.width}x${BIG.height} window asked for ${big} cards and a ${SMALL.width}x${SMALL.height} ` +
			`one asked for ${small}. The page size is not following the screen.`
	).toBeGreaterThan(small);

	const biggest = Math.max(...asks.map((ask) => ask.limit));
	expect(biggest, 'never above the one ceiling every paged route declares').toBeLessThanOrEqual(
		CEILING
	);
});

test('every wall endpoint accepts the largest page a screen could ask for', async ({ page }) => {
	/*
	 * The half asked of the real server.
	 *
	 * A 422 here is the fault: the framework refuses the request before any handler runs, and the
	 * screen in front of somebody reports that refusal as the thing they were looking at being
	 * gone. Checked per endpoint rather than by driving each screen, because driving them needs a
	 * library with faces and folders in it and this needs nothing: an empty answer is still an
	 * answer.
	 */
	await signInAsAdmin(page);

	const walls = [
		'/api/people',
		'/api/faces/groups',
		'/api/faces/identified/people',
		'/api/suggestions'
	];

	const refused: string[] = [];
	for (const wall of walls) {
		const answer = await page.request.get(`${wall}?limit=${CEILING}`);
		if (answer.status() === 422) refused.push(`${wall} -> 422`);
	}

	expect(
		refused,
		'a wall refused the largest page the client can ask for, so on a big monitor every one of ' +
			'its requests fails (see tests/gates/test_one_page_ceiling.py)'
	).toEqual([]);
});

test('the address carries where a wall was left, and a new question forgets it', async ({
	page
}) => {
	/*
	 * The two halves have to be one test, because the second is only interesting given the first.
	 * An anchor that is never written cannot be wrongly re-read, so a test for the forgetting alone
	 * passes against a screen where the whole feature is missing.
	 */
	const asks = peopleWall(page, 500);

	await signInAsAdmin(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();

	// Written into the address once a page has landed.
	await expect(page).toHaveURL(/[?&]from=/);

	// A new question starts at the top. The anchor in the address is one WE wrote, for the question
	// being asked at the time. Honoured again it would start a search four hundred rows down.
	asks.length = 0;
	await page.getByRole('searchbox', { name: 'Search people' }).fill('zzzz');
	await expect.poll(() => asks.length, { timeout: 10_000 }).toBeGreaterThan(0);

	expect(asks[0].from, 'a new search was anchored to the previous one').toBeNull();
});
