import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Recently viewed, in a real browser.
 *
 * It is the media grid with one filter on it, so it pages, wears the same tile controls, and puts
 * its pager where every other pager is.
 *
 * What these tests are about is why they are here rather than in a unit file: a row of things that
 * LOOK like grid tiles has to BEHAVE like grid tiles: selection, a plain click that opens, the
 * sharing marks and the switch that takes them off. All three need a layout engine and a pointer.
 *
 * The grid's own width and ring-room checks are asked once, in `browse.spec.ts`; asking them a
 * second time here would be a second implementation of one check.
 *
 * The library is intercepted rather than imported. What is under test is the screen, and building a
 * watch history through the import pipeline and the player would make this a test of those.
 */

/** One transparent pixel, as PNG. Small enough to write out, real enough for a browser to decode. */
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

/** Every request this screen makes, and what the page it asked for was narrowed by. */
const asked: string[] = [];

async function serve(page: Page, { marks = true } = {}) {
	asked.length = 0;
	/* The library, narrowed. This screen is the grid pointed at `viewed=yes`. There is no endpoint
	   of its own behind it, which is what lets it page. */
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
	/* A real picture, and it has to be real.
	 *
	 * `thumb: true` above is what keeps the tile out of the importing shimmer, and it is not enough
	 * on its own: a still that fails to LOAD puts the tile into its no-preview state, which draws
	 * the missing-image glyph and nothing else: no clock, and no sharing mark. Serving one pixel
	 * is what makes the tile a finished tile with things drawn on it. */
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));

	// The marks' setting, answered without touching the real account's settings.
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
	/* The screen adds no language of its own: `viewed=yes` is what the Filters panel sends and what
	   `viewed:` in the box parses to. Asked any other way this is a second definition of "recently
	   viewed" that can drift from the one somebody can type. */
	await serve(page);
	await page.goto('/recent');
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	expect(asked.length).toBeGreaterThan(0);
	expect(asked.every((url) => url.includes('viewed=yes'))).toBe(true);
});

test('it draws one tile per thing watched, starting where its title does', async ({ page }) => {
	/* The row pays for its own inset and lines up with the screen's own title, rather than
	 * sitting hard against the window edge; asserted rather than eyeballed.
	 */
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
	/* Selection is the other half of "these look like grid tiles": the gesture is shared with the
	 * grid, so this also guards against the two drifting.
	 */
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
	// The other side of the same gesture, and the one a wrong capture-phase handler breaks: with
	// nothing selected, a click is still "open this".
	await serve(page);
	await page.goto('/recent');
	await expect(tiles(page)).toHaveCount(WATCHED.length);

	await tiles(page).first().click();

	await expect(page.getByRole('dialog')).toBeVisible();
});

test('what has been shared wears a mark, and the setting takes it off', async ({ page }) => {
	await serve(page);
	await page.goto('/recent');

	/* One shared and one restricted, of three. The marks are their own controls (pressing one
	   opens the sharing panel), so they are their own elements beside the picture rather than
	   spans inside the tile's button.

	   `.mark`, not `.chip`: a chip is pressed or removed, and a mark is a badge over a picture
	   with a scrim behind it. This counts something it expects to find, so a rename goes red
	   rather than quiet. */
	await expect(wall(page).locator('.marks .mark')).toHaveCount(2);
	await expect(wall(page).locator('.mark.restricted')).toHaveCount(1);

	await serve(page, { marks: false });
	await page.reload();
	await expect(tiles(page)).toHaveCount(WATCHED.length);
	await expect(wall(page).locator('.marks .mark')).toHaveCount(0);
});
