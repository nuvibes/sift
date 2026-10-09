import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { trailWords } from './trail';

/* The four sheets and the overlay, driven: open each the way a person does, act, watch what goes
 * to the server, and get out again, since a sheet with no way out is a trap. */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false, thumb: true }));

/* A real picture: placeholders pass an absence assertion for the wrong reason. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

const FOLDERS = [
	{
		id: 'f1',
		name: 'Clips',
		path: '/media/Clips',
		rel_path: 'Clips',
		parent_id: null,
		root_id: 'r1',
		writable: true
	},
	{
		id: 'f2',
		name: 'Stills',
		path: '/media/Stills',
		rel_path: 'Stills',
		parent_id: null,
		root_id: 'r1',
		writable: true
	}
];

/* Managed: Move is offered only into a root Sift was handed read-write. */
const ROOTS = [{ id: 'r1', name: 'Media', managed: true }];

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
	await page.route('**/api/library/roots', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ roots: ROOTS })
		})
	);
	await page.route(
		(url) => url.pathname === '/api/library/folders',
		(route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ folders: FOLDERS })
			})
	);
}

async function openGrid(page: Page) {
	await serveLibrary(page);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
}

/** Pick the first `count` tiles, and wait for the bar to agree it has them. */
async function pickFirst(page: Page, count: number) {
	for (let index = 0; index < count; index += 1) {
		await page
			.locator('.tile')
			.nth(index)
			.click({ modifiers: ['ControlOrMeta'] });
	}
	await expect(page.getByRole('region', { name: 'Selection' })).toContainText(
		`${count} files selected`
	);
}

const bar = (page: Page) => page.getByRole('region', { name: 'Selection' });

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

/* --- the search overlay ---------------------------------------------------------------- */

test('ctrl-F opens the search overlay and puts the caret in it', async ({ page }) => {
	await openGrid(page);

	await page.keyboard.press('ControlOrMeta+f');

	const overlay = page.getByRole('dialog', { name: 'Search' });
	await expect(overlay).toBeVisible();
	// The caret has to land in the box, or the shortcut has saved nobody a keystroke.
	await expect(overlay.locator('input').first()).toBeFocused();

	/* And ON THE SCREEN: under the centred dialog's class it would land half off the left edge. */
	await page.waitForTimeout(500); // past the arrive transition, or this measures it mid-flight
	const view = page.viewportSize()!;
	const box = (await overlay.boundingBox())!;
	expect(box.x, 'the overlay starts off the left edge').toBeGreaterThanOrEqual(0);
	expect(box.x + box.width, 'the overlay runs past the right edge').toBeLessThanOrEqual(view.width);
	expect(box.y, 'the overlay starts above the top edge').toBeGreaterThanOrEqual(0);
	/* Centred on the space beside the rail, not on the window. */
	const rail = (await page.getByRole('navigation', { name: 'Main' }).boundingBox())!;
	const contentStart = rail.x + rail.width;
	// `clientWidth` stops at the scrollbar, which the viewport does not.
	const contentEnd = await page.evaluate(() => document.documentElement.clientWidth);
	const leftMargin = box.x - contentStart;
	const rightMargin = contentEnd - (box.x + box.width);
	expect(leftMargin, 'the overlay starts left of the content it belongs to').toBeGreaterThanOrEqual(
		0
	);
	/* Twelve pixels: the fault puts hundreds off screen, and the gutter is uneven by eight. */
	expect(Math.abs(leftMargin - rightMargin)).toBeLessThan(12);
});

test('and escape puts it away again', async ({ page }) => {
	await openGrid(page);
	await page.keyboard.press('ControlOrMeta+f');
	const overlay = page.getByRole('dialog', { name: 'Search' });
	await expect(overlay).toBeVisible();

	await page.keyboard.press('Escape');

	await expect(overlay).toHaveCount(0);
});

test('and it stays out of the way of somebody already typing', async ({ page }) => {
	/* Ctrl-F belongs to a box that has focus, not to the application. */
	await openGrid(page);
	const field = page.locator('header input').first();
	await field.click();
	await field.type('bea');

	await page.keyboard.press('ControlOrMeta+f');

	await expect(page.getByRole('dialog', { name: 'Search' })).toHaveCount(0);
});

/* --- the folder browser: a MODE of Browse, left by the button that turned it on ---------- */

/** The folders in the folder being looked at: a band above the wall, not a frame of its own. */
const explorer = (page: Page) => page.locator('.band');

test('the folder browser opens on the library and walks into a folder', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/assets?**in=f1**', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS.slice(0, 1), total: 1, limit: 50, offset: 0 })
		})
	);

	await page.getByRole('button', { name: 'Folders' }).click();

	const folders = explorer(page);
	await expect(folders).toBeVisible();
	await expect(folders.getByText('Clips')).toBeVisible();
	await expect(folders.getByText('Stills')).toBeVisible();

	await folders.getByRole('button', { name: 'Clips', exact: true }).click();

	// The shared trail in the page header is what says where you are.
	await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toBeVisible();
	await expect.poll(() => trailWords(page)).toContain('Clips');
});

test('and walking into a folder points the WALL at it, without leaving', async ({ page }) => {
	/* Pressing a folder walks into it and points the wall at it, as a file manager does. */
	await openGrid(page);
	const asked: string[] = [];
	await page.route('**/api/assets?**', (route) => {
		asked.push(route.request().url());
		return route.fallback();
	});

	await page.getByRole('button', { name: 'Folders' }).click();
	const folders = explorer(page);
	await expect(folders).toBeVisible();

	await folders.getByRole('button', { name: 'Clips', exact: true }).click();

	/* Asked of the control: an empty folder draws no band at all. */
	await expect(page.getByRole('button', { name: 'Back to tiles' })).toBeVisible();
	await expect(page).toHaveURL(new RegExp(`[?&]folders=${FOLDERS[0].id}`));
	// And the wall really asked for that folder.
	await expect.poll(() => asked.some((one) => one.includes(`in=${FOLDERS[0].id}`))).toBe(true);
});

test('and the same button turns it off again without going anywhere', async ({ page }) => {
	await openGrid(page);
	const before = page.url();

	await page.getByRole('button', { name: 'Folders' }).click();
	await expect(explorer(page)).toBeVisible();

	// The control that turned the explorer on turns it off.
	await page.getByRole('button', { name: 'Back to tiles' }).click();

	await expect(explorer(page)).toHaveCount(0);
	/* Polled: the wall removes its row from the address and writes a fresh one when its page lands
	 * (`lib/grid/anchor.ts`), so a read between them sees the bare `/browse`. */
	await expect.poll(() => page.url()).toBe(before);
});

/* --- the pick flyout ------------------------------------------------------------------- */

test('Tag opens the pick flyout and tags every picked file', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/tags*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			// A page, not a bare list.
			body: JSON.stringify({
				items: [{ id: 't1', name: 'beach', asset_count: 0 }],
				total: 1,
				limit: 50,
				offset: 0
			})
		});
	});
	let written: { asset_ids: string[]; tag_ids: string[] } | null = null;
	await page.route('**/api/assets/tags', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 3 })
		});
	});

	await pickFirst(page, 3);
	/* One "Add to" opens the places a file can go; a row's flyout writes to every picked file
	   (`FileVerbs`). */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Tag' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	// Opening writes nothing; the press on a row is the write.
	expect(written).toBeNull();
	await flyout.getByRole('menuitemcheckbox', { name: 'beach' }).click();

	await expect.poll(() => written).not.toBeNull();
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2', 'a3']);
	expect(written!.tag_ids).toEqual(['t1']);
});

test('and closing the pick flyout writes nothing at all', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/tags*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			// A page, not a bare list.
			body: JSON.stringify({
				items: [{ id: 't1', name: 'beach', asset_count: 0 }],
				total: 1,
				limit: 50,
				offset: 0
			})
		});
	});
	const written: unknown[] = [];
	await page.route('**/api/assets/tags', (route) => {
		written.push(route.request().postDataJSON());
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 0 })
		});
	});

	await pickFirst(page, 3);
	/* "Add to", then the flyout. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Tag' }).click();
	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();

	// Once for the flyout, once for its menu.
	await page.keyboard.press('Escape');
	await page.keyboard.press('Escape');

	await expect(flyout).toHaveCount(0);
	await expect(page.getByRole('menu')).toHaveCount(0);
	expect(written).toEqual([]);
	// Nothing was done to the selection.
	await expect(bar(page)).toContainText('3 files selected');
});

test('a person nobody has heard of can be made from the flyout and filed under', async ({
	page
}) => {
	/* Creating somebody from the flyout files the clips under them on the same press, and closes
	 * it: the rest of their record is the People screen's. */
	await openGrid(page);
	await page.route('**/api/people*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0 })
		});
	});
	let made: { name: string } | null = null;
	await page.route('**/api/people', (route) => {
		if (route.request().method() !== 'POST') return route.continue();
		made = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: 'p9', name: 'Orla Fennimore', vault: false })
		});
	});
	let written: { asset_ids: string[]; person_ids: string[] } | null = null;
	await page.route('**/api/assets/people', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 2 })
		});
	});

	await pickFirst(page, 2);
	/* "Add to", then the flyout. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Person' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	// The flyout's one box is the list's narrowing box.
	await page.getByLabel('Filter the people list').fill('Orla Fennimore');
	await flyout.getByRole('menuitem', { name: 'Create Orla Fennimore' }).click();

	await expect.poll(() => made).not.toBeNull();
	expect(made!.name).toBe('Orla Fennimore');

	// Created AND filed, for every picked file.
	await expect.poll(() => written).not.toBeNull();
	expect(written!.person_ids).toEqual(['p9']);
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2']);

	// Its question answered, the flyout goes.
	await expect(flyout).toBeHidden();
});

test('Add to Person goes through the same flyout to the people endpoint', async ({ page }) => {
	/* The same component behind a third verb: only where the answer goes differs. */
	await openGrid(page);
	await page.route('**/api/people*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [{ id: 'p1', name: 'Ada Lovelace' }], total: 1 })
		});
	});
	let written: { asset_ids: string[]; person_ids: string[] } | null = null;
	await page.route('**/api/assets/people', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 2 })
		});
	});

	await pickFirst(page, 2);
	/* "Add to", then the flyout. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Person' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	expect(written).toBeNull();
	await flyout.getByRole('menuitemcheckbox', { name: 'Ada Lovelace' }).click();

	await expect.poll(() => written).not.toBeNull();
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2']);
	expect(written!.person_ids).toEqual(['p1']);
});

/* --- the move sheet -------------------------------------------------------------------- */

test('Add to Site says where a selection came from', async ({ page }) => {
	/* This only ever adds; a wrong one is corrected on the file's own record. */
	await openGrid(page);
	await page.route('**/api/sites*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: [{ id: 't1', name: 'Northlight', asset_count: 0 }],
				total: 1,
				limit: 50,
				offset: 0
			})
		});
	});
	let written: { asset_ids: string[]; site_ids: string[] } | null = null;
	await page.route('**/api/assets/sites', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 2 })
		});
	});

	await pickFirst(page, 2);
	/* "Add to", then the flyout. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Site' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	expect(written).toBeNull();
	await flyout.getByRole('menuitemcheckbox', { name: 'Northlight' }).click();

	await expect.poll(() => written).not.toBeNull();
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2']);
	expect(written!.site_ids).toEqual(['t1']);
});

test('Add to Photo Set puts every picked file into the one chosen', async ({ page }) => {
	await openGrid(page);
	await page.route('**/api/photo-sets*', (route) => {
		if (route.request().method() !== 'GET') return route.continue();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: [{ id: 's1', name: 'Beach shoot', origin: 'by_hand' }],
				total: 1,
				limit: 200,
				offset: 0
			})
		});
	});
	let written: { asset_ids: string[] } | null = null;
	await page.route('**/api/photo-sets/s1/items', (route) => {
		written = route.request().postDataJSON();
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: 2 })
		});
	});

	await pickFirst(page, 2);
	/* "Add to", then the flyout. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Photo Set' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	expect(written).toBeNull();
	await flyout.getByRole('menuitemcheckbox', { name: 'Beach shoot' }).click();

	await expect.poll(() => written).not.toBeNull();
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2']);
});

test('Move refuses to go until a folder has been chosen', async ({ page }) => {
	/* This writes to a real disk, so the button is dead until a destination is picked. */
	await openGrid(page);

	await pickFirst(page, 2);
	/* Behind the bar's three dots (`barShape`). */
	await bar(page).getByRole('button', { name: 'More for 2 files' }).click();
	await page.getByRole('menuitem', { name: 'Move' }).click();

	// Scoped to the dialog: the bar's verb reads "Move" too.
	const confirm = page.getByRole('alertdialog').getByRole('button', { name: 'Move', exact: true });
	await expect(confirm).toBeDisabled();

	/* Ticked and still refused, so this stays about destinations. */
	await understand(page);
	await expect(confirm).toBeDisabled();
});

/* Tick the acknowledgement if this account is still asked; absent means already acknowledged. */
async function understand(page: Page): Promise<boolean> {
	const box = page.getByRole('checkbox', { name: /moves files on my disk/i });
	if ((await box.count()) === 0) return false;
	await box.check();
	return true;
}

test('and choosing one moves every picked file into it', async ({ page }) => {
	await openGrid(page);
	/* Confirming with the box ticked saves "stop asking", so it is answered here and recorded: the
	   only place the confirm and the preference meet. */
	const acknowledged: unknown[] = [];
	await page.route('**/api/settings', (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		acknowledged.push(route.request().postDataJSON());
		return route.fulfill({ status: 204, body: '' });
	});
	/* One request for the whole selection. */
	const moved: { id: string; folder: string }[] = [];
	await page.route('**/api/assets/move', (route) => {
		const body = route.request().postDataJSON() as { asset_ids: string[]; folder_id: string };
		for (const id of body.asset_ids) moved.push({ id, folder: String(body.folder_id) });
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ changed: body.asset_ids.length, skipped: 0, reason: null })
		});
	});

	await pickFirst(page, 2);
	/* Behind the bar's three dots. */
	await bar(page).getByRole('button', { name: 'More for 2 files' }).click();
	await page.getByRole('menuitem', { name: 'Move' }).click();

	await page.getByLabel('Move to').click();
	await page.getByRole('option', { name: /Stills/ }).click();
	const wasAsked = await understand(page);
	await page.getByRole('alertdialog').getByRole('button', { name: 'Move', exact: true }).click();

	await expect.poll(() => moved.length).toBe(2);
	expect(moved.map((one) => one.id).sort()).toEqual(['a1', 'a2']);
	expect(new Set(moved.map((one) => one.folder))).toEqual(new Set(['f2']));

	/* Saved by CONFIRMING, not by ticking. */
	if (wasAsked) {
		await expect
			.poll(() => acknowledged)
			.toEqual([{ values: { 'library.confirm_file_moves': false } }]);
	}
});
