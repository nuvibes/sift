import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Recently viewed is the grid with one filter, so its tiles must BEHAVE like grid tiles: selection,
 * a plain click that opens, the sharing marks and their switch. The width checks live in
 * `browse.spec.ts`. The library is intercepted.
 */

/** One transparent pixel, as PNG. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const WATCHED = [
	{
		id: 'r1',
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 95_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		shared: true,
		restricted: false
	},
	{
		id: 'r2',
		media_type: 'video',
		width: 1080,
		height: 1920,
		duration_ms: 30_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		shared: false,
		restricted: true
	},
	{
		id: 'r3',
		media_type: 'image',
		width: 1000,
		height: 1000,
		duration_ms: null,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		shared: false,
		restricted: false
	}
];

/** Every request this screen makes, by what it was narrowed by. */
const asked: string[] = [];

async function serve(page: Page, { marks = true } = {}) {
	asked.length = 0;
	/* The grid pointed at `viewed=yes`, with no endpoint of its own, which lets it page. */
	await page.route('**/api/assets?*', (route) => {
		asked.push(route.request().url());
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: WATCHED,
				total: WATCHED.length,
				limit: 60,
				offset: 0,
				complete: true
			})
		});
	});
	/* A still that fails to load draws only the missing-image glyph: no clock, no mark. */
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));

	// Without touching the real account's settings.
	await page.route('**/api/settings', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				sections: [
					{
						name: 'Appearance',
						settings: [
							/* Three answers, not a switch: always / hover / never. */
							{ key: 'appearance.tile.sharing', value: marks ? 'always' : 'never' }
						]
					}
				]
			})
		});
	});
}

const wall = (page: Page) => page.locator('.frame-body-inner');
const tiles = (page: Page) => wall(page).locator('.tile');

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('it asks the library for what this account has opened, and nothing else', async ({ page }) => {
	/* `viewed=yes` is what the Filters panel sends and `viewed:` parses to: one definition. */
	await serve(page);
	await page.goto('/recent');
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	expect(asked.length).toBeGreaterThan(0);
	expect(asked.every((url) => url.includes('viewed=yes'))).toBe(true);
});

test('it draws one tile per thing watched, starting where its title does', async ({ page }) => {
	/* The row's inset lines up with the screen's title. */
	await serve(page);
	await page.goto('/recent');

	await expect(wall(page)).toBeVisible();
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	const title = await page
		.getByRole('heading', { name: 'Recently viewed', level: 1 })
		.boundingBox();
	const first = await tiles(page).first().boundingBox();
	expect(title).not.toBeNull();
	expect(first).not.toBeNull();
	expect(Math.abs((first?.x ?? 0) - (title?.x ?? 0))).toBeLessThanOrEqual(1);
});

test('its tiles can be picked, one at a time and in a run', async ({ page }) => {
	/* The gesture is shared with the grid, so this guards against drift. */
	await serve(page);
	await page.goto('/recent');
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	await tiles(page)
		.first()
		.click({ modifiers: ['Control'] });
	await expect(page.getByText('1 file selected')).toBeVisible();

	await tiles(page)
		.nth(2)
		.click({ modifiers: ['Shift'] });
	await expect(page.getByText('3 files selected')).toBeVisible();
});

test('a plain click on one opens it rather than picking it', async ({ page }) => {
	// With nothing selected, a click is still "open this".
	await serve(page);
	await page.goto('/recent');
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	await tiles(page).first().click();

	await expect(page.getByRole('dialog')).toBeVisible();
});

test('what has been shared wears a mark, and the setting takes it off', async ({ page }) => {
	await serve(page);
	await page.goto('/recent');

	/* One shared and one restricted, of three: `.mark`, a badge with its own press, counted so a
	   rename goes red rather than quiet. */
	await expect(wall(page).locator('.marks .mark')).toHaveCount(2);
	await expect(wall(page).locator('.mark.restricted')).toHaveCount(1);

	await serve(page, { marks: false });
	await page.reload();
	await expect(tiles(page)).toHaveCount(WATCHED.length);
	await expect(wall(page).locator('.marks .mark')).toHaveCount(0);
});
