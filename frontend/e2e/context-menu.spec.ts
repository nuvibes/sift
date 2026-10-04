import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* The right-click menu, in a real browser.
 *
 * It cannot be driven anywhere else: the menu is portalled out of the grid, it opens on a real
 * contextmenu event, and whether a submenu closes is the library's own behaviour rather than
 * anything in this repo's markup. A unit environment has none of that.
 */

/* One unrated and one already rated. The second is what makes "No rating" appear at all: the row
 * only exists when there is something to take back, and the list here is a fixed answer, so a
 * rating set by the test above is not read back into it. */
const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000, rating: null },
	{ id: 'a2', media_type: 'image', width: 1000, height: 1000, duration_ms: null, rating: 6 }
].map((asset) => ({ ...asset, favorite: false, concealed: false }));

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** Every rating written while the page was open, newest last. */
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
	/* One address for one file and for a selection alike: the ids travel in the body. */
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

	// The submenu opens on hover, which is the library's behaviour and the one people use.
	await page.getByRole('menuitem', { name: 'Rating' }).hover();
	const four = page.getByRole('menuitemradio', { name: '4 stars' });
	await expect(four).toBeVisible();

	await four.click();

	// Both halves matter. The rating landing proves the row still does its job; the menu closing is
	// the other half, and a row that writes without closing passes the first half alone.
	/* A rating is STORED out of ten and DRAWN on the account's scale, which is five by default.
	   So pressing the third star writes 6, not 3, and a fixture or a reply written in stars is
	   a test measuring the conversion backwards. */
	await expect.poll(() => written).toEqual([8]);
	await expect(page.getByRole('menu')).toHaveCount(0);
});

test('taking a rating away also closes the menu', async ({ page }) => {
	await openGrid(page);

	// The second tile is the one that arrives with a rating on it.
	await page.locator('.tile').nth(1).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rating' }).hover();

	// The one it already has is marked, which is the other half of a radio group being the right
	// component for this: the menu says what the answer is now, not just what the answers are.
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

	// Not at the top level. This half is what would fail if the grouping were quietly
	// dropped and the rows put back. The half below would still pass, because the rows exist.
	await expect(menu(page).getByRole('menuitem', { name: 'Person', exact: true })).toHaveCount(0);
	await expect(menu(page).getByRole('menuitem', { name: 'Favorites', exact: true })).toHaveCount(0);

	// On hover, which is the library's behaviour and the one people use.
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	// The five walls a file can belong to and the heart, in the order the rail lists them; see the
	// test below, which says why Tag is among them.
	for (const place of ['Person', 'Site', 'Collection', 'Photo Set', 'Tag', 'Favorites']) {
		await expect(page.getByRole('menuitem', { name: place, exact: true })).toBeVisible();
	}

	/*
	 * A picking verb opens out into the LIST ITSELF: `VerbMenuItems` draws a `PickMenu` for any
	 * verb carrying `pick` and a plain row for any that does not, so the row is a sub-menu with a
	 * box to narrow it and a tick beside each name, and several can be picked in one opening.
	 *
	 * The sheet is not gone from the application: the action bar over a selection still opens it,
	 * which is what `VerbPick` is beside `run` for. This menu is simply not where it appears.
	 */
	await page.getByRole('menuitem', { name: 'Collection', exact: true }).hover();
	await expect(page.getByLabel('Filter the collections list')).toBeVisible();
	await expect(page.locator('.pick-sheet')).toHaveCount(0);
});

test('tagging is one of the places a file goes, not a row in front of them', async ({ page }) => {
	/*
	 * Tag is inside the group rather than a row of its own. The rows under "Add to" are the list
	 * itself (one hover, then the tags, with a tick beside each), so the cost of the group is
	 * one gesture and not a dialog, and with all six behaving the same way a seventh standing
	 * outside would read as the odd one out rather than as the important one.
	 */
	await openGrid(page);

	await page.locator('.tile').first().click({ button: 'right' });
	const words = (await menu(page).getByRole('menuitem').allInnerTexts()).map((line) => line.trim());

	// Add to is a row of the menu (after Similar to this, which opens a wall rather than filing
	// anything), and Tag is never a row of its own beside it.
	expect(words.some((word) => word.includes('Add to'))).toBe(true);
	expect(words.some((word) => word === 'Tag')).toBe(false);
});
