import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { tileSizeSlider } from './tile-size';

/* The grid, drawing real tiles, in a real browser.
 *
 * Everything else about the layout is tested as arithmetic, which is the right place for it: the
 * unit test environment has no layout engine, so it can only ever check the numbers. What it cannot
 * check is whether those numbers reach the screen: the width of a tile is measured from a real
 * element, applied through a real style property, and served under a policy that silently drops a
 * style attribute written into markup. Every one of those steps is invisible to a unit test and
 * fails without an error.
 *
 * The library is supplied by intercepting the API rather than by importing files, because what is
 * under test is the drawing rather than the importing, and a fixed set of proportions makes the
 * arithmetic checkable from the outside.
 */

/* One at a time, for this file only.
 *
 * These tests share one admin, one library and one grid, and several of them CHANGE it: hiding an
 * item, hearting one, rating one, emptying it to check the empty state. Run in parallel they read
 * each other's edits, and the way it surfaces is a different three failing on every run: a tile
 * measured while another test was hiding it, an "empty library" that is not empty because another
 * test just added to it.
 *
 * Non-deterministic failures are the tell. A wrong assertion fails the same way twice.
 */
test.describe.configure({ mode: 'serial' });

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null },
	{ id: 'a4', media_type: 'video', width: 1920, height: 1080, duration_ms: 5000 },
	{ id: 'a5', media_type: 'video', width: 1600, height: 900, duration_ms: 12_000 },
	{ id: 'a6', media_type: 'image', width: 800, height: 1200, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

async function serveLibrary(page: import('@playwright/test').Page, items = ASSETS) {
	await page.route('**/api/assets?*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: items.length, limit: 50, offset: 0 })
		});
	});
	// The stills would 404 against a library that does not exist; a tile draws its frame either way.
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/*
	 * One window for the whole file, and it is not the runner's default.
	 *
	 * Rows are justified, so a tile's drawn height depends on the WINDOW as much as on the size
	 * setting, and the grid strips a tile's heart and rating below 150px on purpose. At the
	 * default 720px window these fixtures settle at 141px, which is inside that band, so every test
	 * here that reaches for a control on a tile was reaching for something not drawn. Set once, so
	 * the file cannot depend on which test ran before it.
	 */
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the grid draws a tile for every item', async ({ page }) => {
	await serveLibrary(page);
	await page.goto('/browse');

	await expect(page.locator('.tile')).toHaveCount(ASSETS.length);
});

test('tiles are given real, different widths', async ({ page }) => {
	/*
	 * The failure this exists for: the policy the app is served under refuses a style attribute
	 * written into markup, and refuses it silently. Every tile would come out the same width, the
	 * page would look like a broken layout algorithm, and the type check, the unit tests and the
	 * build would all be green.
	 */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const widths = await page
		.locator('.tile')
		.evaluateAll((tiles) =>
			tiles.map((tile) => (tile as HTMLElement).getBoundingClientRect().width)
		);

	expect(widths.length).toBeGreaterThan(1);
	for (const width of widths) expect(width).toBeGreaterThan(20);
	expect(
		new Set(widths.map(Math.round)).size,
		'every tile came out the same width'
	).toBeGreaterThan(1);
});

test('a portrait item is narrower than a landscape one of the same height', async ({ page }) => {
	// True proportions, which is the whole reason for justified rows rather than uniform cards.
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const boxes = await page.locator('.tile').evaluateAll((tiles) =>
		tiles.map((tile) => {
			const rect = (tile as HTMLElement).getBoundingClientRect();
			return { width: rect.width, height: rect.height };
		})
	);

	const landscape = boxes[0];
	const portrait = boxes[1];
	expect(portrait.width).toBeLessThan(landscape.width);
	// Same row, so the same height. Allow a pixel for rounding.
	if (Math.abs(portrait.height - landscape.height) < 2) {
		expect(portrait.width / portrait.height).toBeLessThan(1);
		expect(landscape.width / landscape.height).toBeGreaterThan(1);
	}
});

test('a row fills the space it has, and does not exceed it', async ({ page }) => {
	/*
	 * Measured against the *content* box: `clientWidth` includes padding, so comparing a row
	 * against it would compare the layout to the same wrong number the layout used, and agree with
	 * itself.
	 */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const measured = await page.evaluate(() => {
		const row = document.querySelector('.row');
		// The frame's body, which is the ONE scrolling box on the screen. A second scroller inside
		// it would be two nested regions.
		const scroller = document.querySelector('.frame-body') as HTMLElement | null;
		if (!row || !scroller) return null;
		const style = getComputedStyle(scroller);
		const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
		const tiles = [...row.querySelectorAll('.tile')];
		const used = tiles.reduce((total, tile) => total + tile.getBoundingClientRect().width, 0);
		return { used, content: scroller.clientWidth - padding, count: tiles.length };
	});

	expect(measured).not.toBeNull();
	if (measured && measured.count > 1) {
		expect(measured.used).toBeGreaterThan(measured.content * 0.5);
		expect(measured.used, 'the row is wider than the space it sits in').toBeLessThanOrEqual(
			measured.content + 2
		);
	}
});

test('the grid never scrolls sideways', async ({ page }) => {
	/*
	 * What the row measurement above is really protecting. A row laid out wider than its container
	 * grows a horizontal scrollbar and clips the last tile of every row, and because the grid is
	 * the one screen that scrolls itself, nothing else in the app would show it.
	 */
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const overflow = await page.evaluate(() => {
		// The frame's body: the one scrolling box on the screen. See the note in the test above.
		const scroller = document.querySelector('.frame-body') as HTMLElement;
		return scroller.scrollWidth - scroller.clientWidth;
	});

	expect(overflow, 'the grid overflows its own width').toBeLessThanOrEqual(0);
});

test('a still image is never asked for a preview clip', async ({ page }) => {
	// An image has no clip, so the request can only ever 404, and a refusal that is not remembered
	// is re-sent every time the tile crosses the viewport.
	const asked: string[] = [];
	await page.route('**/api/assets/*/preview', (route) => {
		asked.push(new URL(route.request().url()).pathname);
		return route.fulfill({ status: 404 });
	});
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.waitForTimeout(1000);

	// a3 and a6 are images.
	expect(asked.some((path) => path.includes('/a3/') || path.includes('/a6/'))).toBe(false);
});

test('an empty library says so instead of showing nothing at all', async ({ page }) => {
	await serveLibrary(page, []);
	await page.goto('/browse');

	// A library with no folders is offered its first folder, not told it is empty.
	await expect(page.getByText('Sift has no folders yet.')).toBeVisible();
	await expect(page.locator('.tile')).toHaveCount(0);
});

test('the grid asks for one page and then stops', async ({ page }) => {
	/*
	 * An effect that calls a loader which reads the state it writes re-runs itself, and the grid
	 * would request the same page for as long as the tab is open. Nothing about the screen looks
	 * wrong while it happens.
	 */
	const asked: string[] = [];
	await page.route('**/api/assets?*', async (route) => {
		asked.push(new URL(route.request().url()).search);
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		});
	});
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	/* A connection that says nothing. The grid reads again whenever the server announces a change
	   to the library, which is right, and the tests running beside this one change it: left
	   connected, their announcements are counted here as the loop. */
	await page.routeWebSocket('**/api/live/stream**', () => {});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.waitForTimeout(2000);

	// The queries themselves, not just how many: an extra ask with DIFFERENT parameters is the grid
	// settling on a size, and an extra ask with the same ones is the loop this test exists for.
	expect(asked.length, `the grid kept asking: ${asked.join(' | ')}`).toBeLessThanOrEqual(2);
});

test('a hidden item is a locked tile with no picture in it', async ({ page }) => {
	// The server sends no dimensions and no bytes for a concealed asset; the tile must not try to
	// fetch any, and must not say what it is.
	await serveLibrary(page, [
		{
			id: 'hidden',
			media_type: '',
			width: null,
			height: null,
			duration_ms: null,
			favorite: false,
			rating: null,
			concealed: true
		} as unknown as (typeof ASSETS)[number]
	]);
	await page.goto('/browse');

	await expect(page.locator('.tile.concealed')).toHaveCount(1);
	await expect(page.locator('.tile img')).toHaveCount(0);
	await expect(page.locator('.tile video')).toHaveCount(0);
});

/* The tile's overlay, which is a layout question and therefore only answerable here.
 *
 * The unit environment has no layout engine, so every assertion about what fits in a tile passes
 * there whatever the stylesheet does.
 */
const SHAPES = [
	{ id: 'wide', media_type: 'video', width: 3840, height: 1080, duration_ms: 95_000 },
	{ id: 'tall', media_type: 'video', width: 600, height: 1600, duration_ms: 81_000 },
	{ id: 'anim', media_type: 'gif', width: 400, height: 400, duration_ms: 2040 }
	/* `thumb` says the still has been BUILT, and it is not the same question as whether the image
	   URL answers. A tile with no still yet is a tile still being imported, and it deliberately
	   carries no controls. So leaving this off served three shapes that the grid drew as imports
	   and quietly took the heart and the duration off every one of them. Serving the picture below
	   is not enough on its own; this is the field the component reads. */
	/* `rating: 6` is THREE stars. A rating is stored out of ten and drawn on the account's scale,
   which is five by default, so a fixture written in stars is a fixture describing the display
   while the wire carries the storage. */
].map((asset) => ({ ...asset, favorite: false, rating: 6, concealed: false, thumb: true }));

/* A real one-pixel image. The badge and the overlay live in the branch that has a picture, so a
 * tile whose still 404s draws the missing-preview placeholder and neither is on screen to check. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

async function serveShapes(page: import('@playwright/test').Page) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: SHAPES, total: SHAPES.length, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test('a tile carries a heart and a rating, and neither leaves the tile', async ({ page }) => {
	/*
	 * No stars on a tile.
	 *
	 * The rows are justified, so tiles in one row share a height and differ wildly in width. A
	 * portrait clip at a height the grid calls roomy is about 120px across, and a heart plus five
	 * stars is 102 before the duration chip in the same corner is counted, so stars would run
	 * over the neighbouring tile, and a narrow form leaves five small targets on a picture in the
	 * one place somebody is skimming rather than deciding.
	 *
	 * So the stars are not here and the number stays. What is checked is that the row still fits
	 * the narrowest tile the grid will draw, and that the stars really are absent rather than
	 * merely narrow.
	 */
	/*
	 * A window tall enough for the tile to HAVE controls, said explicitly.
	 *
	 * The grid strips the heart and the rating below a tile height of 150px, deliberately: at the
	 * smallest setting the picture is the whole point and a mark nobody can read is only taking up
	 * picture. Rows are justified, so the drawn height is a function of the WINDOW as much as of the
	 * size setting: at the runner's default 720px window these three proportions settle at 141px,
	 * inside that band. Without a viewport this asked whether a row that is not drawn fits inside
	 * the tile, and the answer was that it is nought pixels tall.
	 *
	 * The other side of the threshold is asserted at the end rather than assumed. The window itself
	 * is set for the whole file; see the note on `beforeEach`.
	 */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);

	/*
	 * Settled before anything is measured, and not by waiting a fixed time.
	 *
	 * The rows are justified from a measurement of the wall, and the wall is measured again whenever
	 * the box it is in changes, including the frame after this window is resized. A run on a loaded
	 * machine can reach the loop below while the wall is still laid out for the window this test did
	 * not ask for, which comes out as tiles a third of the width they settle at. Two readings that
	 * agree is what "it has stopped" means here.
	 */
	await expect
		.poll(async () => {
			const first = await page
				.locator('.tile-frame')
				.first()
				.evaluate((el) => el.clientWidth);
			await new Promise((settle) => setTimeout(settle, 120));
			const second = await page
				.locator('.tile-frame')
				.first()
				.evaluate((el) => el.clientWidth);
			return first === second && first > 0;
		})
		.toBe(true);

	const frames = page.locator('.tile-frame');
	for (let index = 0; index < (await frames.count()); index += 1) {
		const frame = frames.nth(index);
		await frame.hover();
		const row = frame.locator('.controls-row');
		await expect(row).toBeVisible();

		const tile = (await frame.boundingBox())!;
		const controls = (await row.boundingBox())!;
		const width = Math.round(tile.width);

		expect(
			controls.x + controls.width,
			`the controls ran past the right edge of a ${width}px tile`
		).toBeLessThanOrEqual(tile.x + tile.width + 1);

		// Not present at all, at any width. Every SHAPE is rated 3, so a tile that still drew stars
		// would draw them here.
		await expect(frame.locator('.stars'), `stars on a ${width}px tile`).toHaveCount(0);

		/* And the fact survives the control going: a rated file still says so without being opened.
		 * The star is written as its codepoint because every file in this repository is plain ASCII
		 * and the gate that enforces it reads test files too. */
		await expect(frame.locator('.rating')).toHaveText(`3${String.fromCodePoint(0x2605)}`);

		// The measurement above is only worth anything on a tile the controls are drawn on at all.
		expect(
			Math.round(tile.height),
			'the tile is inside the band where the grid strips its chrome, so nothing was measured'
		).toBeGreaterThan(150);
	}

	/*
	 * And the other side of that threshold, so the stripping is a decision this file holds rather
	 * than a reason it quietly measures nothing.
	 *
	 * On a short window the same three shapes settle well under 150px, and at that size the tile is
	 * chrome-free on purpose: no heart to press and no rating chip. The picture is what somebody is
	 * skimming, and a control too small to hit is worse than one that is not there.
	 *
	 * 700 is chosen by measurement across twelve window heights, 1280 wide, where Filter and Sort
	 * sit on the screen's row (`main` is the window less 127 at every height here):
	 *
	 *   600 -> 160   620 -> 170   640 -> 180   660 -> 160   680 -> 160   700 -> 137
	 *   720 -> 144   740 -> 151   760 -> 157   800 -> 171   840 -> 160   880 -> 146
	 *
	 * The heart is gone at 137, 144 and 146 and present at every other one. It is not monotonic,
	 * because a justified row's height is one screenful divided by however many whole rows fit,
	 * so losing a little height can drop a row and make every remaining row TALLER.
	 *
	 * PICK THIS NUMBER BY MEASURING, NEVER BY REASONING ABOUT IT. The three assertions under it are
	 * what this test is for; the height is only how it gets into the band where they mean anything.
	 */
	await page.setViewportSize({ width: 1280, height: 700 });
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);
	const small = page.locator('.tile-frame').first();
	await small.hover();
	expect(Math.round((await small.boundingBox())!.height)).toBeLessThanOrEqual(150);
	await expect(small.getByRole('button', { name: 'Add to favorites' })).toHaveCount(0);
	// Hidden rather than absent: the rule takes it out of the picture with `display: none`, which
	// leaves the element in the document. A count would be 1 either way.
	await expect(small.locator('.rating')).toBeHidden();

	// Put back, because this file is serial and the next test would otherwise inherit a window this
	// one narrowed for four assertions.
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('a GIF is labelled rather than timed', async ({ page }) => {
	// A length is the least useful thing to say about a GIF (it loops and has nothing to seek
	// with), and it hid the one thing worth knowing, which is whether the tile is a clip or a loop.
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(SHAPES.length);

	const badges = await page.locator('.duration').allTextContents();
	expect(badges).toContain('GIF');
	expect(badges).toContain('1:35');
	expect(badges, 'a GIF was still timed').not.toContain('0:02');
});

test('a picked tile under the pointer draws one ring, not two', async ({ page }) => {
	/* Both signals are accent-coloured. The hover lift carries a 2px OUTSET ring and the
	 * selection a 3px INSET one, so a selected tile with the pointer on it would draw two blue
	 * circles with the pulled-in picture in the gap between them. The selection's is the one that
	 * survives: the pointer already says where the pointer is.
	 */
	await serveShapes(page);
	await page.goto('/browse');
	const frame = page.locator('.tile-frame').first();
	await expect(frame).toBeVisible();

	// Ctrl-click picks without opening. The tile stays under the pointer afterwards, which is the
	// whole situation: it is where the pointer is at the end of every press that selects.
	await frame.click({ modifiers: ['Control'] });
	await expect(frame.locator('.ring')).toHaveCount(1);

	/* Read once the shadow has stopped moving. The tile transitions its box-shadow, so a value
	 * taken straight after the click is caught mid-animation: the accent layer is there but
	 * still `rgba(0,0,0,0) 0 0 0 0`, which matches nothing and would pass against the fault.
	 * Waiting for two identical reads is what makes it a measurement.
	 */
	/* Read after the transition, not during it.
	 *
	 * The tile animates its box-shadow, and a value taken straight after the click is caught
	 * mid-flight: the accent layer is present but still `rgba(0,0,0,0) 0 0 0 0`, which matches
	 * nothing and would pass against the fault. A fixed wait rather than a poll, because a poll
	 * retries until it passes and the wrong answer comes FIRST here: the honest thing is to wait
	 * out a known duration and then look once.
	 */
	await page.waitForTimeout(600);
	const shadow = await frame
		.locator('.tile')
		.evaluate((element) => getComputedStyle(element).boxShadow);

	// One layer: the lift's drop shadow. Two means its accent ring came back alongside `.ring`.
	expect(shadow, `the hover ring is still drawn: ${shadow}`).not.toMatch(/0px 0px 0px 2px/);
	expect(shadow, 'the tile stopped lifting altogether').toContain('18px');
});

test('the top row has room to rise into', async ({ page }) => {
	/* A scroller with `overflow-y: auto` clips on BOTH axes (the other axis computes to `auto`
	 * with it), so anything a tile draws outside its own box is cut off at the scroller's edge.
	 * The accent ring sits 2px out and the hover lift scales the tile up past that again, so the
	 * top row needs room above it. The sides always have room.
	 */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	// The frame's body, which is the ONE scrolling box on the screen. The room at the top is a
	// margin on the first row.
	const scroller = (await page.locator('.frame-body').boundingBox())!;
	const first = (await page.locator('.tile-frame').first().boundingBox())!;

	expect(
		first.y - scroller.y,
		'the top row is flush with the scroller, so its ring is clipped'
	).toBeGreaterThanOrEqual(5);
});

test('the heart goes at the smallest grid size, and only there', async ({ page }) => {
	/*
	 * "Take the hearts off at the smallest size" is a threshold, and a threshold is where the bugs
	 * are. The size control offers the four notches on the ladder, and rows are JUSTIFIED, so a row asked
	 * for 180 routinely settles at 160. A rule written at 180px therefore strips the heart from the
	 * second-smallest step as well: the default grid on a 1280px window draws 160px tiles, and they
	 * would lose their hearts.
	 *
	 * Both ends are checked, because either alone passes against the wrong number.
	 */
	await serveShapes(page);
	await page.setViewportSize({ width: 1280, height: 900 });
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const shownAt = async (step: string, selector: string) => {
		await (await tileSizeSlider(page)).fill(step);
		const frame = page.locator('.tile-frame').first();
		await frame.hover();
		const height = (await frame.boundingBox())!.height;
		const el = frame.locator(selector).first();
		if ((await el.count()) === 0) return { height, shown: false };
		const shown = await el.evaluate((node) => getComputedStyle(node).display !== 'none');
		return { height, shown };
	};

	// Step 0 is 120: the smallest the control offers. Neither the heart nor the rating chip shows.
	const smallHeart = await shownAt('0', '.controls-row button');
	const smallRating = await shownAt('0', '.rating');
	expect(smallHeart.shown, `heart still drawn on a ${smallHeart.height}px tile`).toBe(false);
	expect(smallRating.shown, `rating chip still drawn on a ${smallRating.height}px tile`).toBe(
		false
	);

	// Step 1 is 180, and it keeps its heart however the row justifies.
	const next = await shownAt('1', '.controls-row button');
	expect(next.shown, `heart missing from a ${next.height}px tile`).toBe(true);
});

test('the right-click menu offers more than delete, and keeps delete last and admin-only', async ({
	page
}) => {
	/*
	 * The menu carries the ordinary actions (favorite, copy a link, hide) with the destructive
	 * one last and behind a separator, and the two admin actions gated so a guest is not shown a
	 * door the server will refuse.
	 *
	 * Open is deliberately not among them: clicking a tile already opens it, so the row could only
	 * repeat the click that got you here.
	 */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.locator('.tile').first().click({ button: 'right' });

	for (const label of ['Add to', 'Copy link', 'Hide', 'Remove']) {
		await expect(page.getByRole('menuitem', { name: label })).toBeVisible();
	}
	// Asserted from a menu proven to be drawn by the loop above, so this is an absence rather than
	// a menu that never opened.
	await expect(page.getByRole('menuitem', { name: 'Open' })).toHaveCount(0);

	// Remove is the destructive one and wears the colour that means so.
	const deleteItem = page.getByRole('menuitem', { name: 'Remove' });
	await expect(deleteItem).toHaveClass(/destructive/);
});

test('favouriting from the menu saves it, and the heart on the tile agrees', async ({ page }) => {
	// The menu and the tile's own heart are two ways to the same state, so they must not disagree.
	let saved: unknown = null;
	await serveShapes(page);
	/* The heart is written for a SELECTION, even when the selection is the one file under the
	   pointer: one address and one request whether it names one file or four hundred. */
	await page.route('**/api/assets/favorite', async (route) => {
		saved = route.request().postDataJSON();
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 1, skipped: 0, vault_locked: false })
		});
	});

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile').first().click({ button: 'right' });
	// The heart is one of the five places a file can be put, so it is inside the group.
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Favorites', exact: true }).click();

	expect(saved).toEqual({ asset_ids: ['wide'], favorite: true });
	// The tile's own heart is filled now: same state, reached the other way.
	const frame = page.locator('.tile-frame').first();
	await frame.hover();
	await expect(frame.locator('.controls-row button').first()).toHaveClass(/on/);
});

test('copying a link puts the asset address on the clipboard', async ({ page, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page.locator('.tile').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Copy link' }).click();

	const copied = await page.evaluate(() => navigator.clipboard.readText());
	expect(copied).toMatch(/\/asset\/wide$/);
});

test('the heart and the chip sit on the same line', async ({ page }) => {
	/*
	 * The heart and the duration chip line up by their CENTRES.
	 *
	 * The heart's tap padding is dropped on a tile, so its glyph is not pushed below the chip. The
	 * two boxes are different heights (the heart is 16px, the row it sits in is 22 because the
	 * rating chip beside it sets that, and the duration chip is 22), and the row centres its
	 * contents, so the heart's box sits 3px up from a shared bottom edge, exactly half the
	 * difference.
	 *
	 * That is correct: both boxes hold ink centred inside them, so what lines up is the middle. An
	 * assertion on bottoms would pass only by a size-dependent coincidence. Aligning two boxes of
	 * different heights by an edge is what puts their ink out of line.
	 */
	await serveShapes(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	const frame = page.locator('.tile-frame').first();
	await frame.hover();

	const seen = await frame.evaluate((el) => {
		const middle = (selector: string) => {
			const box = (el.querySelector(selector) as HTMLElement).getBoundingClientRect();
			return { middle: box.top + box.height / 2, height: box.height };
		};
		return { chip: middle('.duration'), heart: middle('.controls-row button') };
	});

	/* Under a pixel. Both boxes are sized from type, so their middles land on fractions that
	   differ by hundredths, which nobody can see and no stylesheet can dictate. A visible
	   misalignment is several pixels and fails this by a wide margin. */
	expect(
		Math.abs(seen.chip.middle - seen.heart.middle),
		`heart and chip are not on one line: chip ${seen.chip.height}px, heart ${seen.heart.height}px`
	).toBeLessThanOrEqual(1);

	/* And they really are different heights, or the check above is trivially true and would go on
	   passing if somebody bottom-aligned the row again. */
	expect(seen.chip.height, 'the two boxes are the same height').not.toBe(seen.heart.height);
});

test('the grid scrolls to its own last row and its paginator', async ({ page }) => {
	/* The bottom row and the pager must be reachable.
	 *
	 * The shell lays its children out so the grid's `height: 100%` is the area below whatever
	 * sits above it; laid out as a block, the last rows would end up below the window under
	 * `overflow: hidden`, and nothing could scroll to them because the grid's own scroller had
	 * already ended. The rule is about anything above the grid (the bar across the top is there),
	 * so what is set up is the grid.
	 */
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });

	const items = Array.from({ length: 60 }, (_, n) => ({
		id: `s${n}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true
	}));
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 200, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/*
	 * Scrolled to the bottom and HELD there, rather than once.
	 *
	 * The wall lays its rows out from measurements, so rows are still being added for a frame or two
	 * after the first tile is visible, and on a loaded machine that is long enough to scroll to a
	 * bottom that then moves. The last row is appended below the fold, the assertion reads a ratio of
	 * nought, and the same test passes on its own every time. Scroll until scrolling changes nothing.
	 */
	const toTheBottom = async () =>
		page.evaluate(async () => {
			const scroller = document.querySelector('.frame-body')!;
			let landed = -1;
			for (let tries = 0; tries < 30 && landed !== scroller.scrollTop; tries += 1) {
				landed = scroller.scrollTop;
				scroller.scrollTop = scroller.scrollHeight;
				await new Promise(requestAnimationFrame);
				await new Promise(requestAnimationFrame);
			}
			return { bottom: scroller.getBoundingClientRect().bottom, window: window.innerHeight };
		});

	// The scrolling box ends where the window does, rather than running on underneath it.
	const room = await toTheBottom();
	expect(room.bottom).toBeLessThanOrEqual(room.window + 1);

	/*
	 * ...so the pager is on screen, and so is the last row THE PAGE DRAWS.
	 *
	 * Not the last `.tile`. The grid asks for more files than a page draws (how many fill a row
	 * cannot be known before laying them out), and the surplus belongs to the next page. The last
	 * row drawn is what has to be reachable.
	 *
	 * The pager is the last thing in the scrolling content, so reaching the bottom is what puts it
	 * on screen: the bottom of the wall has to be reachable, and the way off the page has to be at
	 * it.
	 */
	await expect(page.getByRole('navigation', { name: 'Pages' })).toBeInViewport();
	// Once more, because everything above took time and a row may have arrived during it.
	await toTheBottom();
	await expect(page.locator('.row').last()).toBeInViewport();
});

test('the vault mark does not depend on the sharing-marks switch', async ({ page }) => {
	/* Turning the sharing marks off must not take the Hidden mark away.
	 *
	 * They are two different facts wearing the same corner. One is about who can reach a file and
	 * is a preference an admin can turn off; the other is that the file is in the vault and is
	 * being shown anyway, which is not a preference at all. It is the reason the tile looks
	 * ordinary while the vault is open, and it is what stops somebody sharing something they had
	 * forgotten was hidden.
	 *
	 * The switch is flipped for real rather than mocked: a preference is stored per account and
	 * read through one shared store, so mocking the read would test the mock.
	 *
	 * The key is `appearance.tile.sharing` and it takes always / hover / never, like every mark
	 * on a tile.
	 */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'v1',
			media_type: 'video',
			width: 1920,
			height: 1080,
			duration_ms: 61_000,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			shared: true,
			shared_here: true,
			hidden: true
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	// Both marks while the switch is on.
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);
	await expect(page.locator('.marks .mark')).toHaveCount(2);

	/* What the account held before, so the end of this test puts back exactly that. The account
	   is shared by every spec on this server, so an answer left behind here is a preference every
	   later wall is drawn under. */
	const before = await page.evaluate(async () => {
		const answer = await fetch('/api/settings', { credentials: 'same-origin' });
		const { sections } = (await answer.json()) as {
			sections: { settings?: { key: string; value: unknown }[] }[];
		};
		return sections
			.flatMap((section) => section.settings ?? [])
			.find((one) => one.key === 'appearance.tile.sharing')?.value;
	});
	expect(before, 'the account has no answer for the sharing mark').toBeTruthy();

	await page.evaluate(async () => {
		const me = await (await fetch('/api/auth/me', { credentials: 'same-origin' })).json();
		await fetch('/api/settings', {
			method: 'PUT',
			credentials: 'same-origin',
			headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf_token },
			body: JSON.stringify({ values: { 'appearance.tile.sharing': 'never' } })
		});
	});
	await page.reload();
	await expect(page.locator('.tile').first()).toBeVisible();

	// The sharing one has gone and the vault one has not.
	await expect(page.locator('.marks .mark')).toHaveCount(1);
	await expect(page.locator('.mark.vaulted')).toHaveCount(1);

	// Put it back, or every later test on this server runs against a changed preference.
	await page.evaluate(async (value) => {
		const me = await (await fetch('/api/auth/me', { credentials: 'same-origin' })).json();
		await fetch('/api/settings', {
			method: 'PUT',
			credentials: 'same-origin',
			headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf_token },
			body: JSON.stringify({ values: { 'appearance.tile.sharing': value } })
		});
	}, before);
});

test('a rating set on the asset screen lands on its tile immediately', async ({ page }) => {
	/* Star something and the tile must say so without a reload.
	 *
	 * Stars are set on the asset's own screen, which opens OVER the grid, so the grid keeps the
	 * row it had loaded unless told. It is a one-field change and deliberately not the "what you
	 * may see has changed" signal, which every scoped list re-reads from the server when it
	 * moves: that would be a page fetch per press of the rating control.
	 */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'r9',
			media_type: 'image',
			width: 1000,
			height: 1000,
			duration_ms: null,
			favorite: false,
			rating: null,
			concealed: false,
			thumb: true,
			original_filename: 'star-me.jpg'
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/r9', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ ...items[0], added_at: 0, view_count: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	/* A rating is STORED out of ten and DRAWN on the account's scale, which is five by default.
	   So pressing the third star writes 6, not 3, and a fixture or a reply written in stars is
	   a test measuring the conversion backwards. */
	/* The whole reply, because the grid finds the row BY the file the answer names. A fixture
	   carrying only what the control can change leaves `asset_id` undefined, no tile matches, and
	   the failure lands on the tile at the end of the test, which reads as "the tile did not
	   update" and sends somebody looking at the grid. */
	await page.route('**/api/assets/*/rating', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ asset_id: 'r9', favorite: false, rating: 8, views: 0 })
		})
	);

	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	// Nothing on the tile yet, which is what makes the assertion below mean something.
	await expect(page.locator('.rating')).toHaveCount(0);

	await page.locator('.tile').first().click();
	await expect(page.getByRole('dialog')).toBeVisible();
	/* A chip that opens a chooser, not a row of stars. A row would be one target per star (ten of
	   them at the scale a person can turn on), so it is one chip to press and a short list to
	   pick from, and the answers are buttons carrying `pressed` rather than radios. */
	await page.getByRole('button', { name: 'This file: not rated' }).click();
	const fourth = page.getByRole('button', { name: '4 stars', exact: true });
	/* Waited for by its answer, not by its appearance.
	 *
	 * The control fills in as soon as it is pressed, and what updates the tile is the reply. So on
	 * a loaded machine the dialog can be closed in between, and the grid never hears. That fails at
	 * the end, on the tile, which reads as "the tile did not update" and sends somebody looking at
	 * the grid rather than at the timing. */
	const answered = page.waitForResponse((reply) => reply.url().includes('/rating'));
	await fourth.click();
	await answered;
	/* And confirmed on the screen the press was made on, before leaving it. Without this, a press
	 * that never registered (the control is there a frame before its handler is) fails the same
	 * way and in the same misleading place. */
	await expect(page.getByRole('button', { name: 'This file: 4 out of 5' })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog')).toHaveCount(0);

	// On the tile, without a reload. The star is written by codepoint: this repo's source is ASCII.
	await expect(page.locator('.rating')).toHaveText(`4${String.fromCharCode(9733)}`);
});

test('the tooltip over a tile colours its status word', async ({ page }) => {
	/* The marks on every wall split the sentence in two: the status word, which carries the
	 * colour, and the explanation after it, which does not. Passing the whole thing as one flat
	 * string would make Shared and Restricted read identically on hover. Asserted as a COLOUR
	 * rather than as a class, because what matters is that the words look different.
	 */
	await signInAsAdmin(page);

	const items = [
		{
			id: 'shared-1',
			media_type: 'video',
			width: 800,
			height: 600,
			thumb: true,
			shared: true,
			shared_here: true
		},
		{
			id: 'restricted-1',
			media_type: 'video',
			width: 800,
			height: 600,
			thumb: true,
			restricted: true,
			restricted_here: true
		}
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: items.length, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);

	await page.goto('/browse');
	await expect(page.locator('.marks .mark')).toHaveCount(2);

	const inkOf = async (index: number): Promise<string> => {
		// Hovering the tile first and the chip second: the tile lifts on hover, so going straight at
		// the chip lands the pointer where the chip is about to not be.
		await page.locator('.tile-frame').nth(index).hover();
		await page.locator('.marks .mark').nth(index).hover();
		const lead = page.locator('[role="tooltip"] .lead');
		await expect(lead).toBeVisible();
		return lead.evaluate((node) => getComputedStyle(node).color);
	};

	const shared = await inkOf(0);
	const restricted = await inkOf(1);

	expect(shared, 'Shared and Restricted are the same colour over a tile').not.toBe(restricted);
});

test('every piece of furniture on a tile is one size, at every size of tile', async ({ page }) => {
	/*
	 * The clock, the sharing marks, the times-opened count and the rating chip are the same object
	 * in four places (a small box on a scrim over somebody's photograph), and they read one set
	 * of tokens, which resolve ONCE: the same size on the smallest tile that draws them as on the
	 * largest.
	 *
	 * ## No step-down by tile size
	 *
	 * A notch of the size control is a TARGET the justified grid quantises, not a tile height, so
	 * which notches fall either side of a size threshold depends on the window width. Furniture
	 * that stepped down below a tile size would follow the WINDOW rather than the setting.
	 *
	 * Both ends are still measured, because the failure is possible in the other direction too: a
	 * rule written on `.tile-frame`, the tile's own query CONTAINER, matches nothing (a rule
	 * inside a container query only matches DESCENDANTS of its container), so every declaration is
	 * discarded silently. Measuring a tile at each end is the only way to know either way.
	 */
	await signInAsAdmin(page);
	await serveShapes(page);

	const sizes = async () =>
		page.evaluate(() => {
			const of = (selector: string) => {
				const found = document.querySelector(selector);
				if (!found) return null;
				const style = getComputedStyle(found);
				return {
					height: Math.round(found.getBoundingClientRect().height),
					radius: style.borderTopLeftRadius,
					size: style.fontSize,
					weight: style.fontWeight
				};
			};
			return {
				tile: Math.round(document.querySelector('.tile-frame')!.getBoundingClientRect().height),
				clock: of('.duration'),
				mark: of('.mark'),
				rating: of('.rating')
			};
		});

	/** The tile height this window settles at, once the wall has finished re-justifying.
	 *
	 * Two readings that agree is what "settled" means here, the same way `wall-paging.spec.ts`
	 * decides a wall has stopped asking: a resize re-justifies asynchronously, and reading between
	 * the old layout and the new one measures neither. */
	const settledTile = async () => {
		let seen = -1;
		for (let tries = 0; tries < 25; tries += 1) {
			const now = (await sizes()).tile;
			if (now === seen) return now;
			seen = now;
			await page.waitForTimeout(100);
		}
		return seen;
	};

	/*
	 * The window is searched for rather than written down.
	 *
	 * A justified row's height is one screenful divided by however many whole rows fit, so it does
	 * not move monotonically with the window, and every change to the furniture above or below
	 * the wall moves the whole curve. A fixed window goes stale the next time the furniture
	 * changes, and then the test fails saying "the tile is below the band where its chrome is
	 * drawn", which is true of the window it picked and says nothing about the furniture.
	 *
	 * What the test needs is a window that lands the tile in a band, not any particular window. So
	 * it asks for one. A height that cannot be found is still a failure, and a loud one, because
	 * then there is genuinely no window where the claim can be checked.
	 */
	const HEIGHTS = [1080, 1000, 920, 1160, 860, 1240, 800, 1320];

	/** A window whose tile lands inside `(low, high]`, searched by height. Null when none does. */
	const windowWhereTileIs = async (width: number, low: number, high: number) => {
		for (const height of HEIGHTS) {
			await page.setViewportSize({ width, height });
			const tile = await settledTile();
			if (tile > low && tile <= high) return { height, tile };
		}
		return null;
	};

	/* Big enough for every piece to be drawn: the marks go below 140 and the rating below 150, both
	   on purpose, so a window that strips them measures nothing. */
	await page.setViewportSize({ width: 1600, height: HEIGHTS[0] });
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile-frame').first().hover();
	await expect(page.locator('.rating').first()).toBeVisible();

	const big = await windowWhereTileIs(1600, 180, Number.POSITIVE_INFINITY);
	expect(big, 'no window height in the list draws a tile over 180px at 1600 wide').not.toBeNull();
	await page.locator('.tile-frame').first().hover();
	await expect(page.locator('.rating').first()).toBeVisible();

	const roomy = await sizes();
	expect(
		roomy.tile,
		'the window is too short to draw a tile with its furniture on'
	).toBeGreaterThan(180);
	for (const [what, piece] of Object.entries(roomy)) {
		if (what === 'tile' || !piece || typeof piece === 'number') continue;
		expect(piece.height, `${what} is not the full-size height`).toBe(22);
		expect(piece.size, `${what} is set at a different size from its neighbours`).toBe(
			roomy.clock!.size
		);
		expect(piece.weight, `${what} is set at a different weight from its neighbours`).toBe(
			roomy.clock!.weight
		);
	}

	/*
	 * And the other end of the size control, where every piece must be the SAME.
	 *
	 * The window lands the tile inside a narrow band: under 180, where a size step-down would show,
	 * and over 150, where the rating chip is still drawn at all. Below 150 the chip is in the
	 * document with no box, and a height of nought passes nothing: it measures an element that is
	 * not on screen.
	 *
	 * The window is searched for rather than written down, for the reason given above the first
	 * half.
	 */
	const narrow = await windowWhereTileIs(1280, 150, 180);
	expect(
		narrow,
		'no window height in the list lands a tile between 150 and 180 at 1280 wide'
	).not.toBeNull();
	await page.locator('.tile-frame').first().hover();

	const small = await sizes();
	expect(small.tile, 'the tile is below the band where its chrome is drawn').toBeGreaterThan(150);
	for (const [what, piece] of Object.entries(small)) {
		if (what === 'tile' || !piece || typeof piece === 'number') continue;
		/*
		 * Compared to the ROOMY measurement rather than to a number written here. If the furniture's
		 * height token is ever changed this keeps asking the right question instead of asserting a
		 * stale figure. The claim is that the two are equal, not that either is 22.
		 */
		const big = roomy[what as keyof typeof roomy] as { height: number; size: string };
		expect(piece.height, `${what} is a different height on a small tile`).toBe(big.height);
		expect(piece.size, `${what} is a different type size on a small tile`).toBe(big.size);
	}
});
