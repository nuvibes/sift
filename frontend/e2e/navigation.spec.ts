import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Moving between screens, which only a browser can answer: a link click must not reload the
 * document, and a wall stepped back to is where it was left (`page-scroll.ts`). */

/** A People wall larger than a window, answering `from` as well as `offset`. */
function peopleWall(page: Page, count = 200) {
	void page.route('**/api/people?*', async (route) => {
		const url = new URL(route.request().url());
		const limit = Number(url.searchParams.get('limit') ?? '0');
		const from = url.searchParams.get('from');
		const offset =
			from === null
				? Number(url.searchParams.get('offset') ?? '0')
				: Number(from.replace(/^p/, '') || '0');
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
}

const wall = (page: Page) => page.locator('.frame-body').first();

/** Which row is at the top of the wall, named by where its card leads. */
const topRow = (page: Page) =>
	wall(page).evaluate((element) => {
		const box = element.getBoundingClientRect();
		const card = [...element.querySelectorAll('.card')].find(
			(one) => one.getBoundingClientRect().bottom > box.top + 4
		);
		return card?.querySelector('a')?.getAttribute('href') ?? null;
	});
const scrollTop = (page: Page) => wall(page).evaluate((element) => element.scrollTop);

/** A mark on the window that only a full page load can remove. */
async function markTheDocument(page: Page) {
	await page.evaluate(() => {
		(window as unknown as { __sameDocument?: boolean }).__sameDocument = true;
	});
}

const stillTheSameDocument = (page: Page) =>
	page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument === true);

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1440, height: 900 });
});

// `(\?|$)` on `/browse`: a wall writes its paging cursor into the address (`lib/grid/anchor.ts`).
test('a rail link is a navigation, not a page load', async ({ page }) => {
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await markTheDocument(page);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);

	expect(await stillTheSameDocument(page), 'the rail link reloaded the document').toBe(true);
});

test('a plain anchor is a navigation, not a page load', async ({ page }) => {
	/* A bare link inside `.app-root`, where the router listens, with nothing of Sift in between. */
	await page.goto('/browse');
	// The client has started once the screen is drawn; before that a link is a plain navigation.
	await expect(wall(page)).toBeAttached();
	await markTheDocument(page);

	await page.evaluate(() => {
		const link = document.createElement('a');
		link.href = '/people';
		link.textContent = 'to people';
		link.id = 'a-bare-link';
		document.querySelector('.app-root')?.appendChild(link);
	});
	await page.locator('#a-bare-link').click();
	await expect(page).toHaveURL(/\/people(\?|$)/);

	expect(await stillTheSameDocument(page), 'a plain link reloaded the document').toBe(true);
});

test('a wall you step back to is where you left it', async ({ page }) => {
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();

	await wall(page).evaluate((element) => {
		element.scrollTop = 420;
	});
	await expect.poll(() => scrollTop(page)).toBe(420);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);

	await page.goBack();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	await expect.poll(() => scrollTop(page), { timeout: 5000 }).toBe(420);
});

test('a wall you arrive at fresh starts at the top', async ({ page }) => {
	// Coming back by the rail is a new visit and starts at the top.
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await wall(page).evaluate((element) => {
		element.scrollTop = 420;
	});
	await expect.poll(() => scrollTop(page)).toBe(420);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);
	await page.locator('nav[aria-label="Main"] a[data-rail-row="people"]').click();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	await page.waitForTimeout(1800); // longer than the restore holds for
	expect(await scrollTop(page)).toBe(0);
});

test('a page you turned to is the page you come back to', async ({ page }) => {
	/* Opening a person and pressing the trail returns to the same page of rows: the crumb follows
	 * the remembered `?from=` address. Nothing here scrolls, or the wall under test moves. */
	peopleWall(page);
	// Answers the asked id: a fixed id would change the path and overwrite the remembered address.
	void page.route('**/api/people/p*', (route) => {
		const id = new URL(route.request().url()).pathname.split('/').at(-1) ?? 'p0';
		return route.fulfill({
			json: {
				id,
				name: `Person ${id.replace(/^p/, '')}`,
				vault: false,
				asset_count: 3,
				cover_asset_id: null,
				cover_track_id: null,
				favorite: false,
				rating: null,
				shared: false,
				restricted: false
			}
		});
	});
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await expect.poll(() => topRow(page)).toBe('/people/p0');

	await page.getByRole('button', { name: 'Next page' }).click();

	// The second page's first row is read off the wall: page size depends on the window.
	await expect.poll(() => topRow(page)).not.toBe('/people/p0');
	const wasShowing = await topRow(page);
	expect(wasShowing, 'the wall never turned a page').not.toBeNull();
	const anchor = wasShowing?.split('/').pop();
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).toBe(anchor);
	const address = new URL(page.url()).search;

	const pressable = await wall(page).evaluate((element) => {
		const box = element.getBoundingClientRect();
		return [...element.querySelectorAll('.card')].findIndex((card) => {
			const at = card.getBoundingClientRect();
			return at.top >= box.top + 4 && at.bottom <= box.bottom - 4;
		});
	});
	expect(pressable, 'no card was wholly on screen to press').toBeGreaterThanOrEqual(0);

	await page.locator('.card').nth(pressable).click();
	await expect(page).toHaveURL(/\/people\/p/);

	// Asserted before the press, so a failure says which half broke.
	const remembered = await page.evaluate(() => ({
		cameFrom: sessionStorage.getItem('sift:came-from'),
		here: sessionStorage.getItem('sift:here')
	}));
	expect(
		remembered.cameFrom ?? '',
		`the wall was not remembered as the screen before this one: ${JSON.stringify(remembered)}`
	).toContain(`/people${address}`);

	await page.locator('nav[aria-label="Breadcrumb"] a').first().click();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	expect(new URL(page.url()).search, 'the page the wall was on was thrown away').toBe(address);
	await expect.poll(() => topRow(page), { timeout: 5000 }).toBe(wasShowing);
});
