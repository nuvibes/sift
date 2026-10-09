/* The asset modal's size and what sits behind it: geometry and compositing need a layout engine. */
import { type Locator, type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { settled } from './settled';

const CLIP = {
	id: 'm1',
	media_type: 'photo',
	width: 1600,
	height: 900,
	duration_ms: null,
	favorite: false,
	rating: null,
	concealed: false,
	// Present and empty, as the server answers for a file nothing has enriched.
	enriched_by: [],
	original_filename: 'holiday.jpg'
};

async function serveLibrary(page: Page) {
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
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/m1/view', (route) => route.fulfill({ status: 204, body: '' }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the popup is a quarter bigger than the 900px base', async ({ page }) => {
	// 900px plus a quarter, at a viewport wide enough that the vw cap never binds.
	await serveLibrary(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();
	await settled(sheet);
	const box = (await sheet.boundingBox())!;
	expect(box.width, `the dialog was ${box.width}px wide`).toBeGreaterThan(1100);
	expect(box.width).toBeLessThan(1150);
});

test('the popup lines up with the line under the search bar, not the card above it', async ({
	page
}) => {
	// Level with the line under the top bar, not a gap below it and not a top bar higher.
	await serveLibrary(page);
	await page.goto('/browse');

	const topbarBottom = await page
		.locator('.topbar')
		.evaluate((el) => el.getBoundingClientRect().bottom);

	await page.locator('.tile').first().click();
	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();

	await settled(sheet);

	const sheetTop = (await sheet.boundingBox())!.y;

	expect(
		Math.abs(sheetTop - topbarBottom),
		`the topbar ends at ${topbarBottom} and the dialog starts at ${sheetTop}`
	).toBeLessThan(2);
});

test('the grid behind the popup is softened, not just darkened', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	await expect(page.getByRole('dialog')).toBeVisible();
	const veil = page.locator('.veil');
	const blur = await veil.evaluate((el) => getComputedStyle(el).backdropFilter);
	expect(blur, 'the veil has no backdrop-filter at all').not.toBe('none');
	expect(blur).toContain('blur');
});

test('a tile clicked and clicked away from is not left outlined', async ({ page }) => {
	// Closed with the mouse, the tile gets focus back but no ring, which would look stuck.
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.click(); // opened with the mouse
	await expect(page.getByRole('dialog')).toBeVisible();

	// Beside the sheet, where the veil is exposed.
	await page.locator('.veil').click({ position: { x: 8, y: 450 } }); // closed with the mouse
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el === document.activeElement),
		'focus was pushed back onto the tile after a mouse-only session'
	).toBe(false);
	expect(
		await tile.evaluate((el) => el.matches(':focus-visible')),
		'the clicked tile kept its blue outline after the modal closed'
	).toBe(false);
});

test('a keyboard user is put back on the tile they opened', async ({ page }) => {
	// Opened by keyboard, focus returns visibly; how it was opened decides, not how it was closed.
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.focus();
	await page.keyboard.press('Enter');
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el === document.activeElement),
		'a keyboard user was dropped off their tile'
	).toBe(true);
});

test('and a mouse user pressing Escape is not given a focus ring', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile').first();
	await tile.click();
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	expect(
		await tile.evaluate((el) => el.matches(':focus-visible')),
		'Escape after a click left the tile outlined'
	).toBe(false);
});

/* The record is a tablist of three panes in the shipped page; the split is `RecordGrid`'s test. */
test('the record is three panes and each draws its own half', async ({ page }) => {
	await serveLibrary(page);
	// The invented file's history would 404; empty is the ordinary answer.
	await page.route('**/api/assets/m1/history*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[],"total":0}' })
	);
	await page.goto('/browse');
	await page.locator('.tile').first().click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();

	const strip = sheet.getByRole('tablist', { name: "What this file's record says" });
	await expect(strip.getByRole('tab')).toHaveCount(3);
	// Every opening starts on About, so this does not depend on an earlier test.

	await expect(sheet.getByRole('tabpanel')).toContainText('Title');
	await expect(
		sheet.getByRole('tabpanel').getByRole('button', { name: 'Add Title' })
	).toBeVisible();

	await strip.getByRole('tab', { name: /^Media/ }).click();
	await expect(sheet.getByRole('tabpanel')).toContainText('Dimensions');
	await expect(sheet.getByRole('tabpanel')).not.toContainText('Title');

	// Read when the pane opens, not with the file.
	await strip.getByRole('tab', { name: /^History/ }).click();
	await expect(sheet.getByRole('tabpanel')).toContainText('Nothing has been recorded');
});
