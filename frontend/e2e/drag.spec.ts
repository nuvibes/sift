import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Dragging inside the app is never an import.
 *
 * The window is the drop target for adding media, which is the right design (hunting for a drop
 * zone is friction), and it has one hard edge: the app's OWN drags happen in the same window. A
 * browser fills any drag begun on a picture or a link with `text/uri-list` and `text/plain` of its
 * own accord, pointing at whatever was dragged, and those are exactly the types an import arms on.
 * So dragging a tile onto a tag, or nudging a link by a few pixels, would raise a full-window "Drop
 * to add" offering to download the thing already on the screen, and letting go would queue a job
 * for it that then failed.
 *
 * It is worse than a stray dialog. The overlay covers the window, so the gesture underneath it
 * (a long press picking a tile out of the grid) looks broken rather than interrupted.
 *
 * None of this is answerable without a real browser: a drag is the browser's own state machine, and
 * the unit environment has neither the machine nor the data transfer it fills in.
 */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

/* A real picture. A tile whose still 404s has no <img> in it, and an <img> is what a browser starts
 * a drag from. So without this the test drags nothing and passes against the bug. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** The overlay, which must never appear for a drag that began in here. */
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
	// An anchor hands the browser a `text/uri-list` of its own href; the overlay must not arm on
	// that, or every link in the app is an import waiting to happen.
	await page.goto('/browse');
	await expect(page.locator('nav.rail a.item').first()).toBeVisible();

	const drop = await dragFrom(page, 'nav.rail a.item');
	await expect(dropTarget(page)).toBeHidden();

	await drop();
	await expect(dropTarget(page)).toBeHidden();
});

test('dragging the brand does not offer to import it', async ({ page }) => {
	// The logo is an image inside a link, which is both ways of arming the overlay at the same time.
	await page.goto('/browse');

	const drop = await dragFrom(page, 'a.brand');
	await expect(dropTarget(page)).toBeHidden();

	await drop();
	await expect(dropTarget(page)).toBeHidden();
});

test('an internal drag queues nothing', async ({ page }) => {
	/* The consequence, rather than the appearance. Letting go over the overlay would hand the dragged
	 * URL to the importer, which would queue a download of the app's own thumbnail and fail it. */
	const asked: string[] = [];
	await page.route('**/api/capture/**', (route) => {
		asked.push(route.request().url());
		return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
	});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const drop = await dragFrom(page, '.tile');
	await drop();

	// Give anything that was going to be sent time to be sent.
	await page.waitForTimeout(500);
	expect(asked, 'an internal drag asked the server to capture something').toEqual([]);
});

test('the overlay still appears for a drag that came from outside', async ({ page }) => {
	/* The other side of the guard, and the reason it is written as "did a drag start in here?"
	 * rather than as a list of types to refuse. A file dragged in from the desktop begins with no
	 * dragstart in this document at all, and it must still be offered a place to land: a guard
	 * that turned the whole feature off would pass every test above. */
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.evaluate(() => {
		const data = new DataTransfer();
		data.items.add(new File(['x'], 'holiday.mp4', { type: 'video/mp4' }));
		window.dispatchEvent(new DragEvent('dragenter', { dataTransfer: data, bubbles: true }));
	});

	await expect(dropTarget(page)).toBeVisible();
});
