import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* A mouse wheel turns up and down, yet a sideways strip ("Who is in this", `FacesInThis`)
 * must still move under it; only a browser measures overflow and applies a wheel. */

const CLIP = {
	id: 'm1',
	media_type: 'photo',
	width: 1600,
	height: 900,
	duration_ms: null,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'holiday.jpg'
};

const FACES = Array.from({ length: 40 }, (_, index) => ({
	track_id: `t${index}`,
	asset_id: 'm1',
	person_id: null,
	person_name: null,
	pile_id: null,
	pile_status: null,
	started_ms: index * 1000,
	ended_ms: index * 1000 + 500,
	confidence: 0.9,
	art: null,
	attribution: null,
	is_reference: false,
	locked: false,
	teachable: true
}));

const LOOKALIKES = Array.from({ length: 40 }, (_, index) => ({
	id: `like-${index}`,
	media_type: 'photo',
	width: 800,
	height: 800,
	duration_ms: null,
	art: null
}));

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

async function openTheSheet(page: Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/m1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CLIP) })
	);
	await page.route('**/api/assets/*/faces', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FACES) })
	);
	await page.route('**/api/faces/*/crop*', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/similar', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ tier: 'looks', items: LOOKALIKES })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/m1/view', (route) => route.fulfill({ status: 204, body: '' }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.getByRole('dialog')).toBeVisible();

	const strip = page.locator('.faces [data-scroll-area-viewport]');
	await expect(strip).toBeAttached();
	await expect
		.poll(() => strip.evaluate((el) => el.scrollWidth - el.clientWidth))
		.toBeGreaterThan(0);
	// On screen: a wheel goes to whatever is under the pointer.
	await strip.scrollIntoViewIfNeeded();
	await expect(strip).toBeVisible();
	return strip;
}

// The sheet's own scroller, the strip's ancestor.
const sheet = (page: Page) =>
	page.locator('.sheet > .scroll-root [data-scroll-area-viewport]').first();

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('an ordinary wheel moves a sideways strip sideways', async ({ page }) => {
	const strip = await openTheSheet(page);

	// Turned again until the turn lands.
	await expect(async () => {
		await strip.hover();
		await page.mouse.wheel(0, 300);
		expect(await strip.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
	}).toPass({ timeout: 20_000 });
});

test('a strip already at its end hands the wheel back rather than swallowing it', async ({
	page
}) => {
	/* Once spent, the strip hands the wheel back to the page, so it can be scrolled past. Pushed
	 * up from its start, where the sheet above has room to take the turn. */
	const strip = await openTheSheet(page);
	await strip.evaluate((el) => el.scrollIntoView({ block: 'start' }));

	await strip.hover();

	expect(await strip.evaluate((el) => el.scrollLeft)).toBe(0);
	const sheetBefore = await sheet(page).evaluate((el) => el.scrollTop);
	expect(
		sheetBefore,
		'the sheet should have somewhere to scroll for this to mean anything'
	).toBeGreaterThan(0);

	await page.mouse.wheel(0, -400);
	await page.waitForTimeout(120);

	expect(await strip.evaluate((el) => el.scrollLeft)).toBe(0);
	// The sheet took the turn instead.
	const sheetAfter = await sheet(page).evaluate((el) => el.scrollTop);
	expect(sheetAfter).toBeLessThan(sheetBefore);
});
