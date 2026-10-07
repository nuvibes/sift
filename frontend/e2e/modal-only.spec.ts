import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Screens that are panels are ONLY ever panels.
 *
 * An asset and settings each have a real address, and that address answers one way: as a panel,
 * whether it is reached from inside the app, opened directly or refreshed. Two answers would make
 * one address two screens, and refreshing while watching something would replace the player with
 * a different one.
 *
 * This is the rule, exercised the only way it can be: in a browser, across a real reload.
 */

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

test('an asset address opened cold is the panel, not a page', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/asset/m1');

	await expect(page.getByRole('dialog')).toBeVisible();
	// Still the address it was asked for. A panel that reached itself by changing the address would
	// break every link anybody has sent.
	expect(new URL(page.url()).pathname).toBe('/asset/m1');
});

test('and refreshing on it brings the panel straight back', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.reload();

	await expect(page.getByRole('dialog')).toBeVisible();
	expect(new URL(page.url()).pathname).toBe('/asset/m1');
});

test('closing an asset opened cold lands on the library, not on nothing', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/asset/m1');
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.keyboard.press('Escape');

	// The library is behind it, so leaving the panel is an ordinary step back rather than a dead end.
	await expect.poll(() => new URL(page.url()).pathname).toBe('/browse');
	await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('a settings address opened cold is the panel, not a page', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/settings/playback');

	await expect(page.getByRole('dialog')).toBeVisible();
	expect(new URL(page.url()).pathname).toBe('/settings/playback');
});

test('and refreshing with settings open brings the panel back', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/settings/playback');
	await expect(page.getByRole('dialog')).toBeVisible();

	await page.reload();

	await expect(page.getByRole('dialog')).toBeVisible();
	expect(new URL(page.url()).pathname).toBe('/settings/playback');
});

test('a section that does not exist still says so', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/settings/not-a-section');

	// No panel onto nothing. The address is wrong and saying so is more use than opening the first
	// section as though it had been asked for.
	await expect(page.getByText("That address isn't a settings page.")).toBeVisible();
	await expect(page.getByRole('dialog')).toHaveCount(0);
});
