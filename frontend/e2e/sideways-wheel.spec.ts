import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* A wheel over a strip that only scrolls sideways.
 *
 * There is nowhere else this can be checked. A wheel is a real input event carrying a distance on
 * two axes, whether the browser applies it is the browser's own decision, and a strip's overflow
 * is a measured layout: a unit environment reports every box as zero wide and would agree with
 * any implementation at all.
 *
 * A mouse has one wheel, it turns up and down, and a row wider than its box must still move under
 * it. The sideways strip on a file is "Who is in this" (`FacesInThis`, a `Scroller horizontal`),
 * so that is what is driven here.
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

/** Enough of them that the row is several times wider than the space it has. */
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

/** What the sheet shows as looking like this file, so it is drawn with every section it has. */
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
	/* BROUGHT ON SCREEN, because a wheel is delivered to whatever is under the pointer, and a
	   strip below the fold of the sheet is under nothing. `page.mouse.move` to a point off the
	   window lands on nothing at all and the turn goes to no element, which reads exactly like
	   the strip refusing the wheel. */
	await strip.scrollIntoViewIfNeeded();
	await expect(strip).toBeVisible();
	return strip;
}

/* The sheet's OWN scroller. There are two inside the dialog (the sheet and the strip under
   test), and this one is the strip's ANCESTOR, so it is first in document order whatever else the
   sheet grows. Anchored at `.sheet > .scroll-root` so it cannot resolve to a scroller that is not
   the sheet's at all. */
const sheet = (page: Page) =>
	page.locator('.sheet > .scroll-root [data-scroll-area-viewport]').first();

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('an ordinary wheel moves a sideways strip sideways', async ({ page }) => {
	const strip = await openTheSheet(page);

	// It answers the wheel at all, which is the whole claim. Turned again until the turn lands.
	await expect(async () => {
		await strip.hover();
		await page.mouse.wheel(0, 300);
		expect(await strip.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
	}).toPass({ timeout: 20_000 });
});

test('a strip already at its end hands the wheel back rather than swallowing it', async ({
	page
}) => {
	/*
	 * The half that keeps this from being a hijack. A strip inside a page that scrolls has to be
	 * something you can scroll PAST. So once it has nowhere left to go, the wheel is the page's
	 * again, and a strip with nothing to scroll never takes one in the first place.
	 *
	 * Pushed BACKWARDS from the start of the strip rather than forwards from its far end, and the
	 * two are the same claim: a strip is spent when it is against the end the wheel is pushing it
	 * towards, and its start is one of its two ends. Upwards is the direction that is certain to
	 * have somewhere to go once the sheet is scrolled down to the strip, which is done below: the
	 * sheet above it is all travel a handed-back turn can take.
	 */
	const strip = await openTheSheet(page);
	/* The sheet scrolled down to the strip, so there is sheet above it for a handed-back turn to
	   move. On screen is not enough: the strip sits near the top of the sheet's side, and with the
	   lookalikes a sideways strip of their own rather than a tall column, the sheet has no reason
	   to have scrolled at all. */
	await strip.evaluate((el) => el.scrollIntoView({ block: 'start' }));

	await strip.hover();

	// At the start, which is the end an upward turn pushes it towards.
	expect(await strip.evaluate((el) => el.scrollLeft)).toBe(0);
	const sheetBefore = await sheet(page).evaluate((el) => el.scrollTop);
	expect(
		sheetBefore,
		'the sheet should have somewhere to scroll for this to mean anything'
	).toBeGreaterThan(0);

	await page.mouse.wheel(0, -400);
	await page.waitForTimeout(120);

	// The strip swallowed nothing: it is still at its end.
	expect(await strip.evaluate((el) => el.scrollLeft)).toBe(0);
	// And the wheel went somewhere: the sheet it sits in took it.
	const sheetAfter = await sheet(page).evaluate((el) => el.scrollTop);
	expect(sheetAfter).toBeLessThan(sheetBefore);
});
