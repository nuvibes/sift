import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* A wheel notch moves the wall by what it was pushed, and no further.
 *
 * Rows are not scroll snap points: a row is about 180px tall and a wheel notch about 100px, so a
 * notch would always land inside the pull of a boundary and either spring back or jump a whole
 * row.
 *
 * Only a browser can answer this. Snapping is the compositor's, not the markup's: the scroll
 * position after a wheel event is the one thing no unit environment has an opinion about.
 */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/* Enough to overflow the body several times over, in mixed proportions so the rows are real rows
 * rather than a single column of squares. */
const ASSETS = Array.from({ length: 60 }, (_, index) => ({
	id: `a${index}`,
	media_type: index % 3 === 2 ? 'image' : 'video',
	width: index % 2 ? 1080 : 1920,
	height: index % 2 ? 1920 : 1080,
	duration_ms: index % 3 === 2 ? null : 30_000,
	favorite: false,
	rating: null,
	concealed: false
}));

async function openGrid(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: 600, limit: 100, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

const scrollTop = (page: Page) =>
	page.locator('.frame-body').evaluate((element) => element.scrollTop);

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 900 });
});

test('one wheel notch moves the wall by one wheel notch', async ({ page }) => {
	await openGrid(page);
	await page.mouse.move(800, 500);

	// Three in a row, because a snap need not show on every notch: the first can look
	// correct and the next spring back to where it began.
	for (const step of [1, 2, 3]) {
		const before = await scrollTop(page);
		await page.mouse.wheel(0, 100);
		// Long enough for a snap to have finished pulling, had there been one.
		await page.waitForTimeout(600);
		const moved = (await scrollTop(page)) - before;
		expect(moved, `notch ${step} moved ${moved}px`).toBeGreaterThan(80);
		expect(moved, `notch ${step} moved ${moved}px`).toBeLessThan(120);
	}
});

test('the wall does not come to rest anywhere it was not pushed', async ({ page }) => {
	await openGrid(page);
	await page.mouse.move(800, 500);

	await page.mouse.wheel(0, 100);
	await page.waitForTimeout(600);
	const settled = await scrollTop(page);

	// Nothing moves it afterwards. A snap arrives late, so a position read immediately after the
	// wheel can be right while the one a person actually sees is not.
	await page.waitForTimeout(700);
	expect(await scrollTop(page)).toBe(settled);
});
