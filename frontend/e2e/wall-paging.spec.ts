import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The card walls' page size: measured off a real screen (jsdom falls back to a fixed number), and
 * on a big monitor it asks for more than a route that capped `limit` low would take. The client
 * asking big is proven on a mocked wall; the server taking big against the real thing.
 */

/** Wide and tall enough that a measured page is well over sixty. */
const BIG = { width: 2560, height: 1440 };

/** Plainly fewer cards, so "sized to the screen" has two readings. */
const SMALL = { width: 1024, height: 700 };

/** The largest page a wall asks for (`MAX_PAGE_SIZE`, `SERVER_PAGE_CAP`). */
const CEILING = 200;

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
 * The page the People wall settles on, read off its pager ("1-64 of 500"): the requests are
 * the fallback and then remainders (`CardPaging.fill`), and neither is the page.
 */
async function settledPage(page: Page): Promise<number> {
	await page.goto('/people');
	// A card is what the measurement reads. Never `networkidle`: the live connection never settles.
	await expect(page.locator('.card').first()).toBeVisible();
	const readout = page.getByRole('button', { name: 'Go to a position' });
	const last = async () => {
		const found = (await readout.innerText()).replace(/\s+/g, ' ').match(/^1-(\d+) of/);
		return found ? Number(found[1]) : -1;
	};
	// Settled when two readings agree.
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
	/* Two windows, because the claim is that the page FOLLOWS the screen. */
	const asks = peopleWall(page, 500);
	await signInAsAdmin(page);

	await page.setViewportSize(BIG);
	const big = await settledPage(page);
	// And the server was asked for all of it.
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
	/* A 422 is the fault: refused before any handler runs, shown as the thing being gone. An empty
	 * answer is still an answer, so no library is needed. */
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
	/* One test: an anchor never written cannot be wrongly re-read. */
	const asks = peopleWall(page, 500);

	await signInAsAdmin(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();

	await expect(page).toHaveURL(/[?&]from=/);

	// A new question starts at the top, not at the anchor written for the old one.
	asks.length = 0;
	await page.getByRole('searchbox', { name: 'Search people' }).fill('zzzz');
	await expect.poll(() => asks.length, { timeout: 10_000 }).toBeGreaterThan(0);

	expect(asks[0].from, 'a new search was anchored to the previous one').toBeNull();
});
