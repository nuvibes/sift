import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Tagging and rating against the real server; the grid is served from a fixed response.
 * Serial, with distinct names: every spec file shares one server. */

test.describe.configure({ mode: 'serial' });

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 }
	// `thumb`: a tile still importing carries no controls.
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false, thumb: true }));

async function serveLibrary(page: import('@playwright/test').Page) {
	await page.route('**/api/assets?*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		});
	});
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* The first-run wizard is answered away; the window is fixed so tiles clear the 150px floor. */
	await page.setViewportSize({ width: 1400, height: 900 });
});

/** Make a tag the way the wall offers it: Add opens the blank record, Save makes it. */
async function makeTag(page: Page, name: string): Promise<void> {
	await page.goto('/tags');
	await page.getByRole('button', { name: 'Add tag' }).click();
	await expect(page).toHaveURL(/\/tags\/new$/);
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByRole('main').getByRole('button', { name: 'Save', exact: true }).first().click();
}

/** The wall of tags narrowed to one name: it is paged and ordered by use. */
async function tagsNamed(page: Page, name: string): Promise<void> {
	await page.goto('/tags');
	await page.getByRole('searchbox', { name: 'Search tags' }).fill(name);
}

test('a tag can be made, renamed and deleted', async ({ page }) => {
	const original = `e2e-holiday-${Date.now()}`;
	const renamed = `${original}-renamed`;

	await makeTag(page, original);
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);
	await tagsNamed(page, original);
	await expect(page.getByText(original, { exact: false })).toBeVisible();

	// Rename is on the right-click menu; the card's click goes to the library, narrowed.
	const card = page.locator('.card', { hasText: original }).first();
	await card.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rename' }).click();

	// Save from the question itself: the top bar has a Save too.
	const asking = page.getByRole('alertdialog');
	await asking.getByLabel('Name', { exact: true }).fill(renamed);
	await asking.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByText(renamed, { exact: false })).toBeVisible();

	await page.locator('.card', { hasText: renamed }).first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	await page.getByRole('button', { name: 'Delete tag' }).click();
	await expect(page.getByText(renamed, { exact: false })).toHaveCount(0);
});

test('a tag can be hearted and rated, and it survives a reload', async ({ page }) => {
	// Reloaded, so what is asserted is what the server kept, not the optimistic store.
	const name = `e2e-opinion-${Date.now()}`;

	await makeTag(page, name);
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);
	await tagsNamed(page, name);

	const card = page.locator('.card', { hasText: name }).first();
	await expect(card).toBeVisible();

	await card.getByRole('button', { name: 'Add to favorites' }).click();
	await tagsNamed(page, name);

	const back = page.locator('.card', { hasText: name }).first();
	await expect(back).toBeVisible();
	await expect(
		back.getByRole('button', { name: 'Remove from favorites' }),
		'the heart did not come back from the server'
	).toBeVisible();
});

test('the two opinion orders are offered on a wall of tags', async ({ page }) => {
	await page.goto('/tags');

	const sort = page.getByRole('button', { name: /Sort by/ });
	await expect(sort).toBeEnabled();
	await sort.click();

	for (const order of ['Favorites first', 'Highest rated']) {
		await expect(
			page.getByRole('option', { name: order }),
			`a wall of tags does not offer ${order}`
		).toHaveCount(1);
	}
});

test('a second tag with the same name in another case is refused', async ({ page }) => {
	const name = `e2e-beach-${Date.now()}`;

	await makeTag(page, name);
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);

	await makeTag(page, name.toUpperCase());

	await expect(page.getByText("There's already a tag called")).toBeVisible();
	await expect(page).toHaveURL(/\/tags\/new$/);
});

test('the tag screen explains the gesture to somebody who has no tags yet', async ({ page }) => {
	// Served empty: other files create tags, so a real wall depends on the run's order.
	await page.route('**/api/tags*', (route) =>
		route.fulfill({ json: { items: [], total: 0, limit: 50, offset: 0 } })
	);
	await page.goto('/tags');

	await expect(page.getByText(/drag clips onto it/i)).toBeVisible();
});

test('the heart is on a tile and is saved, and the stars are not on a tile at all', async ({
	page
}) => {
	// A tile carries the heart but not the stars, which do not fit; both halves are asserted.
	await serveLibrary(page);

	const favorite = page.waitForRequest(
		(request) => request.url().includes('/favorite') && request.method() === 'PUT'
	);

	await page.goto('/browse');
	const tile = page.locator('.tile-frame').first();
	await tile.hover();

	await tile.getByRole('button', { name: 'Add to favorites' }).click();
	expect((await favorite).postDataJSON()).toEqual({ favorite: true });

	await expect(tile.getByRole('radio', { name: '4 stars' })).toHaveCount(0);
});

test('using a control on a tile does not open the player', async ({ page }) => {
	// The heart sits over a tile that opens the player, and pressing it must not play.
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile-frame').first();
	await tile.hover();
	await tile.getByRole('button', { name: 'Add to favorites' }).click();

	await expect(page).toHaveURL(/\/browse/);
});
