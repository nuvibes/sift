import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* Picking things out of the grid, in a real browser.
 *
 * Every part of the selection MODEL is covered as arithmetic (what a ctrl-click adds, what a
 * shift-click spans), and all of it can pass while the gestures do nothing on screen. What the
 * unit environment cannot have is a pointer: no real press with a duration, no real modifier on a
 * real click, and no native drag to be interrupted by.
 *
 * A press that becomes a drag is the specific hazard. The tiles are draggable, and a browser
 * starts a drag from a press that moves a pixel or two, which cancels the pointer stream the
 * long press is counting on. That is one test below, and it is the reason the others exist.
 */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null },
	{ id: 'a4', media_type: 'video', width: 1920, height: 1080, duration_ms: 5000 },
	{ id: 'a5', media_type: 'video', width: 1600, height: 900, duration_ms: 12_000 },
	{ id: 'a6', media_type: 'image', width: 800, height: 1200, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

/* A real picture, and it is load-bearing rather than tidy.
 *
 * A tile whose still 404s draws the missing-preview placeholder, so there is no <img> in it, and
 * an <img> is exactly what a browser starts a native drag from. Every gesture below would pass
 * against a grid of empty boxes and prove nothing about the grid people use. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

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

/** How many tiles are drawn as chosen. The ring is the only thing that says so on screen. */
const picked = (page: Page) => page.locator('.tile-frame .ring');

/** What the selection bar says, which is the other half of "the selection is real". */
const bar = (page: Page) => page.getByRole('region', { name: 'Selection' });

async function openGrid(page: Page) {
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

/** The middle of one tile, in page coordinates. */
async function centreOf(page: Page, index: number) {
	const box = (await page.locator('.tile').nth(index).boundingBox())!;
	return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('holding a tile picks it', async ({ page }) => {
	await openGrid(page);

	const spot = await centreOf(page, 0);
	await page.mouse.move(spot.x, spot.y);
	await page.mouse.down();
	// Longer than the press threshold, and held perfectly still: this is the gesture working at its
	// easiest. If it fails here it fails everywhere.
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(picked(page)).toHaveCount(1);
	await expect(bar(page)).toContainText('1 file selected');
});

test('and the press that picked it does not also open it', async ({ page }) => {
	// A long press ends in a pointerup, and a pointerup on a button is a click. Without the guard
	// the gesture selects and then immediately opens what it selected.
	await openGrid(page);

	const spot = await centreOf(page, 0);
	await page.mouse.move(spot.x, spot.y);
	await page.mouse.down();
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(picked(page)).toHaveCount(1);
	expect(new URL(page.url()).pathname, 'the press opened the asset as well').toBe('/browse');
});

test('a press that turns into a drag does not pick anything', async ({ page }) => {
	/* The other half of the same gesture.
	 *
	 * A tile is draggable, so a press that wanders becomes a native drag, and a drag cancels
	 * the pointer stream underneath it. Dragging must not leave a selection behind: somebody who
	 * meant to drop a clip on a tag and let go somewhere harmless should not find it picked.
	 */
	await openGrid(page);

	const from = await centreOf(page, 0);
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(from.x + 120, from.y + 40, { steps: 10 });
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(picked(page)).toHaveCount(0);
});

test('ctrl-click picks one, and picks a second without losing the first', async ({ page }) => {
	await openGrid(page);

	await page
		.locator('.tile')
		.nth(0)
		.click({ modifiers: ['Control'] });
	await expect(picked(page)).toHaveCount(1);

	await page
		.locator('.tile')
		.nth(2)
		.click({ modifiers: ['Control'] });
	await expect(picked(page)).toHaveCount(2);
	await expect(bar(page)).toContainText('2 files selected');

	// And it takes one back out again, which is the half that separates it from a plain click.
	await page
		.locator('.tile')
		.nth(2)
		.click({ modifiers: ['Control'] });
	await expect(picked(page)).toHaveCount(1);
});

test('shift-click picks the run between two tiles', async ({ page }) => {
	await openGrid(page);

	await page
		.locator('.tile')
		.nth(0)
		.click({ modifiers: ['Control'] });
	await page
		.locator('.tile')
		.nth(3)
		.click({ modifiers: ['Shift'] });

	await expect(picked(page)).toHaveCount(4);
	await expect(bar(page)).toContainText('4 files selected');

	// Dragging the run back in shrinks it rather than leaving what it already reached.
	await page
		.locator('.tile')
		.nth(1)
		.click({ modifiers: ['Shift'] });
	await expect(picked(page)).toHaveCount(2);
});

test('once something is picked a plain click adds instead of opening', async ({ page }) => {
	// Something being selected IS the mode: there is no button to press to get into it, and no
	// modifier to hold once you are.
	await openGrid(page);

	await page
		.locator('.tile')
		.nth(0)
		.click({ modifiers: ['Control'] });
	await page.locator('.tile').nth(1).click();

	await expect(picked(page)).toHaveCount(2);
	expect(new URL(page.url()).pathname, 'a click in a selection opened an asset').toBe('/browse');
});

test('clearing the bar puts every ring away', async ({ page }) => {
	await openGrid(page);

	await page
		.locator('.tile')
		.nth(0)
		.click({ modifiers: ['Control'] });
	await page
		.locator('.tile')
		.nth(1)
		.click({ modifiers: ['Control'] });
	await expect(picked(page)).toHaveCount(2);

	await page.getByRole('button', { name: 'Clear selection' }).click();

	await expect(picked(page)).toHaveCount(0);
	await expect(bar(page)).toBeHidden();
});

/*
 * What a selection actually DOES, which is a different claim from what it looks like.
 *
 * Everything above proves the gestures pick things. These three prove the actions run over what was
 * picked: a selection of six must not act on only the one clicked first while the bar says six.
 *
 * Asserted by watching the requests, because that is the only place the answer is unambiguous. A
 * screen can look right after acting on one of six.
 */

/** Pick the first `count` tiles, and wait for the bar to agree. */
async function pickFirst(page: Page, count: number) {
	for (let index = 0; index < count; index += 1) {
		await page
			.locator('.tile')
			.nth(index)
			.click({ modifiers: ['ControlOrMeta'] });
	}
	await expect(bar(page)).toContainText(`${count} files selected`);
}

test('the sharing panel opens on every picked file, not on the first', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/sharing/users', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([{ id: 'g1', username: 'sam', role: 'guest' }])
		})
	);
	const asked: string[] = [];
	await page.route('**/api/sharing?*', (route) => {
		asked.push(new URL(route.request().url()).searchParams.get('object_id') ?? '');
		return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
	});

	await pickFirst(page, 3);
	/* Behind the bar's three dots: the bar names "Add to", the rating and the one that deletes,
	   and keeps everything else one press away. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Share' }).click();

	await expect(page.locator('.share-sheet')).toBeVisible();
	expect(asked.sort()).toEqual(['a1', 'a2', 'a3']);
	// ...and it counts them rather than naming one of them.
	await expect(page.locator('.share-sheet')).toContainText('3 files');
});

test('and Apply writes a grant for each of them', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/sharing/users', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([{ id: 'g1', username: 'sam', role: 'guest' }])
		})
	);
	await page.route('**/api/sharing?*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
	);
	const written: string[] = [];
	await page.route('**/api/sharing', (route) => {
		if (route.request().method() !== 'PUT') return route.continue();
		written.push(String(route.request().postDataJSON().object_id));
		return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
	});

	await pickFirst(page, 3);
	/* Behind the bar's three dots: the bar names "Add to", the rating and the one that deletes,
	   and keeps everything else one press away. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Share' }).click();
	await expect(page.locator('.share-sheet')).toBeVisible();

	// Nothing is written by the press itself. That is the other half of the claim.
	await page
		.locator('.share-sheet')
		.getByRole('listitem')
		.filter({ hasText: 'sam' })
		.getByRole('button', { name: 'Share', exact: true })
		.click();
	expect(written).toEqual([]);

	await page
		.locator('.share-sheet')
		.getByRole('button', { name: /^Apply/ })
		.click();
	await expect(page.locator('.share-sheet')).toBeHidden();
	expect(written.sort()).toEqual(['a1', 'a2', 'a3']);
});

test('the menu favourites every picked file, not the one under the pointer', async ({ page }) => {
	/* Favorite acts on the whole selection, like every action in that menu: it is the cheapest of
	 * the actions to prove and uses the same `targetIds` answer as the others.
	 */
	await openGrid(page);
	/* ONE request naming all three, with the ids in the body rather than in the address. */
	let hearted: string[] = [];
	await page.route('**/api/assets/favorite', (route) => {
		hearted = route.request().postDataJSON().asset_ids;
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: hearted.length, skipped: 0, vault_locked: false })
		});
	});

	await pickFirst(page, 3);
	await page.locator('.tile').first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Add to' }).hover();
	await page.getByRole('menuitem', { name: 'Favorites', exact: true }).click();

	await expect.poll(() => hearted.length).toBe(3);
	expect([...hearted].sort()).toEqual(['a1', 'a2', 'a3']);
});

test('a selection lets go once its action has run', async ({ page }) => {
	/* The bar and the rings go once Favorite has run, so the job reads as done and the next click
	 * somewhere else is not still acting on six things.
	 */
	await openGrid(page);
	await page.route('**/api/assets/favorite', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 3, skipped: 0, vault_locked: false })
		})
	);

	await pickFirst(page, 3);
	/* The bar's "Add to" is ONE button opening onto the five places a file can go: five
	   buttons side by side would be most of a strip that scrolls sideways. The row opens the
	   SHEET rather than a flyout, because a bar addresses a set and a sheet can tick several at
	   once; see `FileVerbs`. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Favorites' }).click();

	await expect(bar(page)).toBeHidden();
	await expect(picked(page)).toHaveCount(0);
});

test('the bar reads: what is picked, the rest of the question, then what can be done', async ({
	page
}) => {
	/*
	 * THE ORDER, read off the bar's own text: the count, the offer to take the whole query, and
	 * then the verbs ("what is picked, then what can be done"), with select-all to the left of Add
	 * to. Read off the text rather than off a class, so it is the reading order somebody's eye
	 * takes and not an arrangement of boxes that happens to produce it.
	 *
	 * The bar carries no word "Rating"; the star still says what it is to a screen reader, which is
	 * asserted where the rating is driven, below.
	 */
	await openGrid(page);
	await pickFirst(page, 3);

	const text = (await bar(page).innerText()).replace(/\s+/g, ' ');
	expect(text).toContain('3 files selected');
	expect(text.indexOf('3 files selected')).toBeLessThan(text.indexOf('Select all'));
	expect(text.indexOf('Select all')).toBeLessThan(text.indexOf('Add to'));
	expect(text, 'the rating is the mark alone in the bar').not.toContain('Rating');
});

/*
 * The rest of the verbs, each driven from the bar and each asserted on the request it sends.
 *
 * Favorite above proves the bar addresses the whole selection. These prove the others do too, and
 * that they send what they say they send, which is a different claim. Every verb the bar offers
 * is driven except Share, which has its own two tests above.
 */

test('the stars rate every picked file with the value chosen', async ({ page }) => {
	await openGrid(page);
	const rated: { ids: string[]; rating: number | null }[] = [];
	await page.route('**/api/assets/rating', (route) => {
		const sent = route.request().postDataJSON();
		rated.push({ ids: sent.asset_ids, rating: sent.rating });
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: sent.asset_ids.length, skipped: 0, vault_locked: false })
		});
	});

	await pickFirst(page, 3);
	// The stars are behind a button rather than spread along the bar, and the flyout they open is
	// portalled to the end of the document, so it is reached from the page, not from the bar.
	await bar(page).getByRole('button', { name: 'Rating' }).click();
	await page
		.getByRole('group', { name: 'Rating' })
		.getByRole('button', { name: '3 stars' })
		.click();

	// ONE request for the three of them, naming all three.
	await expect.poll(() => rated.length).toBe(1);
	expect([...rated[0].ids].sort()).toEqual(['a1', 'a2', 'a3']);
	// One value for the set, not a nudge each.
	/* A rating is STORED out of ten and DRAWN on the account's scale, which is five by default.
	   So pressing the third star writes 6, not 3, and a fixture or a reply written in stars is
	   a test measuring the conversion backwards. */
	expect(new Set(rated.map((one) => one.rating))).toEqual(new Set([6]));
});

test('Hide hides every picked file rather than the first', async ({ page }) => {
	await openGrid(page);
	const hidden: { ids: string[]; vault: boolean }[] = [];
	await page.route('**/api/assets/vault', (route) => {
		const sent = route.request().postDataJSON();
		hidden.push({ ids: sent.asset_ids, vault: sent.vault });
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: sent.asset_ids.length, skipped: 0, vault_locked: false })
		});
	});

	await pickFirst(page, 3);
	/* Behind the bar's three dots: the bar names "Add to", the rating and the one that deletes,
	   and keeps everything else one press away. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Hide' }).click();

	// ONE request for the three of them, naming all three.
	await expect.poll(() => hidden.length).toBe(1);
	expect([...hidden[0].ids].sort()).toEqual(['a1', 'a2', 'a3']);
	expect(hidden[0].vault).toBe(true);
});

test('Add to Collection puts every picked file in the one chosen', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/collections*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			// A page, not a bare list: the collections route pages.
			body: JSON.stringify({
				items: [{ id: 'c1', name: 'Summer', item_count: 0 }],
				total: 1,
				limit: 50,
				offset: 0
			})
		});
	});
	let written: { asset_ids: string[] } | null = null;
	await page.route('**/api/collections/c1/items', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 3 })
		});
	});

	await pickFirst(page, 3);
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Collection' }).click();
	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	expect(written).toBeNull();
	await flyout.getByRole('menuitemcheckbox', { name: 'Summer' }).click();

	await expect.poll(() => written).not.toBeNull();
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2', 'a3']);
});

test('Delete asks first, and then deletes every picked file', async ({ page }) => {
	/* The one verb that destroys something, so the confirm is half of what is being asserted: no
	 * request may go out on the press that opens the dialog. */
	await openGrid(page);
	/* ONE request for the whole selection, not one per file: a DELETE per id would be a round
	   trip per file, each awaited before the next. The assertion is stronger than a count of
	   requests: it is the ids that were sent. */
	const asked: string[][] = [];
	await page.route('**/api/assets/delete', (route) => {
		asked.push((route.request().postDataJSON() as { asset_ids: string[] }).asset_ids);
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ removed: 3, refused: 0, reason: null })
		});
	});

	await pickFirst(page, 3);
	await bar(page).getByRole('button', { name: 'Remove' }).click();

	// Nothing is destroyed by opening the question.
	expect(asked).toEqual([]);

	await page.getByRole('button', { name: 'Remove from Sift' }).click();

	await expect.poll(() => asked.length).toBe(1);
	expect([...asked[0]].sort()).toEqual(['a1', 'a2', 'a3']);
});

test('Save downloads every picked file, not just the first', async ({ page }) => {
	/* Watched as downloads rather than as requests. Saving builds an anchor and clicks it, so what
	 * comes out the other end is a browser download rather than a fetch, and counting requests would
	 * count the wrong thing.
	 *
	 * The file is ASKED FOR before it is claimed, though, and that ask is an ordinary HEAD: a
	 * selection with one file that has been moved off the disk since it was indexed is the ordinary
	 * case on a shared library, and the toast says how many of them went. These three ids are
	 * fixtures the real server has never heard of, so without an answer here every one of them is
	 * reported as gone and nothing is downloaded at all. */
	await openGrid(page);
	await page.route('**/api/assets/*/save-to-device', (route) =>
		route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'a file' })
	);
	const asked: string[] = [];
	page.on('download', (download) => {
		asked.push(download.url().split('/assets/')[1].split('/')[0]);
	});

	await pickFirst(page, 3);
	/* Behind the bar's three dots. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: /^(Save|Download)/ }).click();

	await expect.poll(() => asked.length).toBe(3);
	expect(asked.sort()).toEqual(['a1', 'a2', 'a3']);
});

test('Recently viewed offers the same verbs as the grid', async ({ page }) => {
	/* Two surfaces, one declaration.
	 *
	 * Recently viewed is the shared grid narrowed to `viewed=yes`, so picking files there offers
	 * the same verbs as picking them in the grid. Asserted as an equality rather than as a list,
	 * so a verb added to one is added to both or this fails.
	 */
	await openGrid(page);

	// The grid's bar first. Run task and a stash-box's row under Auto-enrich are drawn from lists
	// the first bar asks the server for, so the bar is read once they have arrived.
	await pickFirst(page, 2);
	await expect(bar(page).getByRole('button').filter({ hasText: 'Run task' })).toBeVisible();
	const fromGrid = await bar(page).getByRole('button').allTextContents();
	await page.getByRole('button', { name: 'Clear selection' }).click();

	// Then the other screen's, over its own tiles. Same endpoint, narrowed. There is no list of
	// its own behind this screen, which is what lets it page.
	await page.goto('/recent');
	const watched = page.locator('.frame-body-inner');
	await expect(watched.locator('.tile').first()).toBeVisible();
	for (let index = 0; index < 2; index += 1) {
		await watched
			.locator('.tile')
			.nth(index)
			.click({ modifiers: ['ControlOrMeta'] });
	}
	await expect(bar(page)).toContainText('2 files selected');
	// A page of its own, so the same lists are asked for again and the bar fills as they arrive.
	await expect.poll(() => bar(page).getByRole('button').allTextContents()).toEqual(fromGrid);
	const fromWatched = await bar(page).getByRole('button').allTextContents();

	expect(fromWatched).toEqual(fromGrid);
	// And it is not the empty agreement of two bars that offer nothing.
	expect(fromWatched.length).toBeGreaterThan(3);
});
