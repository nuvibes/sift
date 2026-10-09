import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Picking things out of the grid with a real pointer: the model is covered as arithmetic, the
 * gestures only here. A press that wanders becomes a native drag, which cancels a long press. */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null },
	{ id: 'a4', media_type: 'video', width: 1920, height: 1080, duration_ms: 5000 },
	{ id: 'a5', media_type: 'video', width: 1600, height: 900, duration_ms: 12_000 },
	{ id: 'a6', media_type: 'image', width: 800, height: 1200, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false }));

/* A real picture: an <img> is what a browser starts a native drag from. */
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

/** What the selection bar says: the other half of "the selection is real". */
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
	// Past the threshold and perfectly still: the easiest case.
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(picked(page)).toHaveCount(1);
	await expect(bar(page)).toContainText('1 file selected');
});

test('and the press that picked it does not also open it', async ({ page }) => {
	// A pointerup on a button is a click, so without the guard it selects and then opens.
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
	/* A press that becomes a drag must leave nothing picked. */
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

	// And it takes one back out, unlike a plain click.
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

	// Dragged back, the run shrinks.
	await page
		.locator('.tile')
		.nth(1)
		.click({ modifiers: ['Shift'] });
	await expect(picked(page)).toHaveCount(2);
});

test('once something is picked a plain click adds instead of opening', async ({ page }) => {
	// Something selected IS the mode: no button to enter it, no modifier to hold.
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

/* The actions run over everything picked, not just the first: asserted on the requests, since a
 * screen can look right after acting on one of six. */

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
	/* Behind the bar's three dots (`barShape`). */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Share' }).click();

	await expect(page.locator('.share-sheet')).toBeVisible();
	expect(asked.sort()).toEqual(['a1', 'a2', 'a3']);
	// It counts them rather than naming one.
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
	/* Behind the bar's three dots. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Share' }).click();
	await expect(page.locator('.share-sheet')).toBeVisible();

	// The press itself writes nothing.
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
	/* Favorite uses the same `targetIds` as every action in the menu. */
	await openGrid(page);
	/* ONE request naming all three, the ids in the body. */
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
	/* The bar and rings go once Favorite has run, so the next click acts on nothing stale. */
	await openGrid(page);
	await page.route('**/api/assets/favorite', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 3, skipped: 0, vault_locked: false })
		})
	);

	await pickFirst(page, 3);
	/* "Add to" opens a SHEET here, since a bar addresses a set (`FileVerbs`). */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Favorites' }).click();

	await expect(bar(page)).toBeHidden();
	await expect(picked(page)).toHaveCount(0);
});

test('the bar reads: what is picked, the rest of the question, then what can be done', async ({
	page
}) => {
	/* The order, read off the bar's text: the count, take the whole query, then the verbs, with
	 * select-all left of Add to. */
	await openGrid(page);
	await pickFirst(page, 3);

	const text = (await bar(page).innerText()).replace(/\s+/g, ' ');
	expect(text).toContain('3 files selected');
	expect(text.indexOf('3 files selected')).toBeLessThan(text.indexOf('Select all'));
	expect(text.indexOf('Select all')).toBeLessThan(text.indexOf('Add to'));
	expect(text, 'the rating is the mark alone in the bar').not.toContain('Rating');
});

/* Every other verb on the bar, each asserted on the request it sends. */

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
	// Behind a button, in a flyout portalled to the end of the document.
	await bar(page).getByRole('button', { name: 'Rating' }).click();
	await page
		.getByRole('group', { name: 'Rating' })
		.getByRole('button', { name: '3 stars' })
		.click();

	// ONE request naming all three.
	await expect.poll(() => rated.length).toBe(1);
	expect([...rated[0].ids].sort()).toEqual(['a1', 'a2', 'a3']);
	// One value for the set: the third of five stars is 6 of ten.
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
	/* Behind the bar's three dots. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: 'Hide' }).click();

	// ONE request naming all three.
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
			// A page, not a bare list.
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
	/* "Add to", then the flyout over the whole set. */
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
	/* The one destructive verb: no request may go out on the press that opens the confirm. */
	await openGrid(page);
	/* ONE request with the ids sent, not a DELETE per file. */
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

	// Opening the question destroys nothing.
	expect(asked).toEqual([]);

	await page.getByRole('button', { name: 'Remove from Sift' }).click();

	await expect.poll(() => asked.length).toBe(1);
	expect([...asked[0]].sort()).toEqual(['a1', 'a2', 'a3']);
});

test('Save downloads every picked file, not just the first', async ({ page }) => {
	/* Watched as browser downloads, not requests. Each file is asked for first with a HEAD, so
	 * these fixture ids are answered here or all of them read as gone. */
	await openGrid(page);
	await page.route('**/api/assets/*/save-to-device', (route) =>
		route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'a file' })
	);
	const asked: string[] = [];
	page.on('download', (download) => {
		asked.push(download.url().split('/assets/')[1].split('/')[0]);
	});

	await pickFirst(page, 3);
	/* Behind the bar's three dots. */
	await bar(page).getByRole('button', { name: 'More for 3 files' }).click();
	await page.getByRole('menuitem', { name: /^(Save|Download)/ }).click();

	await expect.poll(() => asked.length).toBe(3);
	expect(asked.sort()).toEqual(['a1', 'a2', 'a3']);
});

test('Recently viewed offers the same verbs as the grid', async ({ page }) => {
	/* Recently viewed is the grid narrowed to `viewed=yes`, so its bar must equal the grid's. */
	await openGrid(page);

	// Read once the lists the bar asks the server for have landed.
	await pickFirst(page, 2);
	await expect(bar(page).getByRole('button').filter({ hasText: 'Run task' })).toBeVisible();
	const fromGrid = await bar(page).getByRole('button').allTextContents();
	await page.getByRole('button', { name: 'Clear selection' }).click();

	// The same endpoint, narrowed.
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
	// Its own page asks for the same lists again.
	await expect.poll(() => bar(page).getByRole('button').allTextContents()).toEqual(fromGrid);
	const fromWatched = await bar(page).getByRole('button').allTextContents();

	expect(fromWatched).toEqual(fromGrid);
	// Not two bars agreeing on nothing.
	expect(fromWatched.length).toBeGreaterThan(3);
});
