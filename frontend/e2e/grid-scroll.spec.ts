import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* A wheel notch moves the wall by what it was pushed: rows (~180px) are not snap points for a
 * ~100px notch. Snapping is the compositor's, so a browser. */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

// Mixed proportions, so the rows are real rows.
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

	// Three notches: a snap need not show on the first.
	for (const step of [1, 2, 3]) {
		const before = await scrollTop(page);
		await page.mouse.wheel(0, 100);
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

	// A snap arrives late, so the resting position is read again.
	await page.waitForTimeout(700);
	expect(await scrollTop(page)).toBe(settled);
});
