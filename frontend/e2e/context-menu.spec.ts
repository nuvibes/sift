import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The right-click menu: portalled, opened by a real contextmenu event, with the library's own
 * submenu behaviour. */

/* The rated one makes "No rating" appear; the list is fixed, so writes are not read back. */
const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000, rating: null },
	{ id: 'a2', media_type: 'image', width: 1000, height: 1000, duration_ms: null, rating: 6 }
].map((asset) => ({ ...asset, favorite: false, concealed: false }));

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const written: number[] = [];

async function openGrid(page: Page) {
	written.length = 0;
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	/* One address for a file and a selection: the ids travel in the body. */
	await page.route('**/api/assets/rating', async (route) => {
		const sent = route.request().postDataJSON() as { asset_ids: string[]; rating: number | null };
		written.push(sent.rating as number);
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: sent.asset_ids.length, skipped: 0, vault_locked: false })
		});
	});
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

const menu = (page: Page) => page.getByRole('menu').first();

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('a rating picked in the menu is written, and the menu goes away', async ({ page }) => {
	await openGrid(page);

	await page.locator('.tile').first().click({ button: 'right' });
	await expect(menu(page)).toBeVisible();

	// The submenu opens on hover, as people use it.
	await page.getByRole('menuitem', { name: 'Rating' }).hover();
	const four = page.getByRole('menuitemradio', { name: '4 stars' });
	await expect(four).toBeVisible();

	await four.click();

	// The write and the close, both. Stored out of ten and drawn out of five, so four stars is 8.
	await expect.poll(() => written).toEqual([8]);
	await expect(page.getByRole('menu')).toHaveCount(0);
});

test('taking a rating away also closes the menu', async ({ page }) => {
	await openGrid(page);

	// The second tile has a rating.
	await page.locator('.tile').nth(1).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rating' }).hover();

	// The current answer is marked: a radio group.
	await expect(page.getByRole('menuitemradio', { name: '3 stars' })).toHaveAttribute(
		'aria-checked',
		'true'
	);

	await page.getByRole('menuitem', { name: 'No rating' }).click();

	await expect.poll(() => written).toEqual([null]);
	await expect(page.getByRole('menu')).toHaveCount(0);
});

test('the places a file can be filed sit under one row that opens out', async ({ page }) => {
	await openGrid(page);

	await page.locator('.tile').first().click({ button: 'right' });
	await expect(menu(page)).toBeVisible();

	// Not at the top level: this half fails if the grouping is dropped.
	await expect(menu(page).getByRole('menuitem', { name: 'Person', exact: true })).toHaveCount(0);
	await expect(menu(page).getByRole('menuitem', { name: 'Favorites', exact: true })).toHaveCount(0);

	// On hover.
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	// The five walls a file can belong to and the heart, in the rail's order.
	for (const place of ['Person', 'Site', 'Collection', 'Photo Set', 'Tag', 'Favorites']) {
		await expect(page.getByRole('menuitem', { name: place, exact: true })).toBeVisible();
	}

	/* A picking verb opens into the list itself (`VerbMenuItems` draws a `PickMenu`); the sheet
	 * stays on the action bar over a selection. */
	await page.getByRole('menuitem', { name: 'Collection', exact: true }).hover();
	await expect(page.getByLabel('Filter the collections list')).toBeVisible();
	await expect(page.locator('.pick-sheet')).toHaveCount(0);
});

test('tagging is one of the places a file goes, not a row in front of them', async ({ page }) => {
	/* Tag is inside the group: all six open the list the same way. */
	await openGrid(page);

	await page.locator('.tile').first().click({ button: 'right' });
	const words = (await menu(page).getByRole('menuitem').allInnerTexts()).map((line) => line.trim());

	// Add to is a row (after Similar to this); Tag never stands beside it.
	expect(words.some((word) => word.includes('Add to'))).toBe(true);
	expect(words.some((word) => word === 'Tag')).toBe(false);
});
