import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* A drag begun inside the app, which the browser fills with URLs, never arms the import overlay. */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

/* A real picture: a browser starts a drag from the tile's <img>. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const dropTarget = (page: Page) => page.getByText('Drop to add');

async function serveLibrary(page: Page) {
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
}

/** Drag from the middle of one thing to somewhere else, slowly enough for the browser to notice. */
async function dragFrom(page: Page, what: string, by = { x: 160, y: 90 }) {
	const box = (await page.locator(what).first().boundingBox())!;
	const from = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(from.x + by.x, from.y + by.y, { steps: 12 });
	return () => page.mouse.up();
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await serveLibrary(page);
});

test('dragging a tile does not offer to import it', async ({ page }) => {
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const drop = await dragFrom(page, '.tile');
	await expect(dropTarget(page)).toBeHidden();

	await drop();
	await expect(dropTarget(page)).toBeHidden();
});

test('dragging a link in the rail does not offer to import it', async ({ page }) => {
	await page.goto('/browse');
	await expect(page.locator('nav.rail a.item').first()).toBeVisible();

	const drop = await dragFrom(page, 'nav.rail a.item');
	await expect(dropTarget(page)).toBeHidden();

	await drop();
	await expect(dropTarget(page)).toBeHidden();
});

test('dragging the brand does not offer to import it', async ({ page }) => {
	await page.goto('/browse');

	const drop = await dragFrom(page, 'a.brand');
	await expect(dropTarget(page)).toBeHidden();

	await drop();
	await expect(dropTarget(page)).toBeHidden();
});

test('an internal drag queues nothing', async ({ page }) => {
	const asked: string[] = [];
	await page.route('**/api/capture/**', (route) => {
		asked.push(route.request().url());
		return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
	});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const drop = await dragFrom(page, '.tile');
	await drop();

	await page.waitForTimeout(500);
	expect(asked, 'an internal drag asked the server to capture something').toEqual([]);
});

test('the overlay still appears for a drag that came from outside', async ({ page }) => {
	/* A file from outside starts no dragstart here, and is still offered a place to land. */
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.evaluate(() => {
		const data = new DataTransfer();
		data.items.add(new File(['x'], 'holiday.mp4', { type: 'video/mp4' }));
		window.dispatchEvent(new DragEvent('dragenter', { dataTransfer: data, bubbles: true }));
	});

	await expect(dropTarget(page)).toBeVisible();
});
