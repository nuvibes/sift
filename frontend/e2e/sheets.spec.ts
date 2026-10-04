import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* The four sheets and the overlay, driven rather than read.
 *
 * Each of these is a screen: it has a way in, a way out, and something it does. The unit tests
 * assert that the markup is rendered from one declaration, which proves the two surfaces cannot
 * drift apart and proves nothing about whether pressing anything works. What is here is the other
 * half: open it the way a person does, act, and watch what goes out to the server.
 *
 * A dismiss is asserted as well as an action, and deliberately. A sheet that cannot be got out of
 * is a trap on a screen, and it is the half nobody notices until it happens to them.
 */

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 },
	{ id: 'a3', media_type: 'image', width: 1000, height: 1000, duration_ms: null }
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false, thumb: true }));

/* A real picture. A tile whose still 404s draws the missing-preview placeholder instead, and a
   screen of placeholders passes any "this control is absent" assertion for the wrong reason. */
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
		root_id: 'r1'
	},
	{
		id: 'f2',
		name: 'Stills',
		path: '/media/Stills',
		rel_path: 'Stills',
		parent_id: null,
		root_id: 'r1'
	}
];

/* Managed, because Move is offered only into a root Sift was handed read-write. An unmanaged root
   leaves the verb off the bar entirely, which is the behaviour rather than a fixture detail. */
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
	await page.route('**/api/library/folders', (route) =>
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

	/* And it has to be ON THE SCREEN, which is a different claim. An overlay drawn under the
	 * centred modal dialog's class name would inherit its half-width shift and fixed width and
	 * land half off the left edge: visible, focused, unusable, and passing every assertion
	 * above.
	 */
	await page.waitForTimeout(500); // past the arrive transition, or this measures it mid-flight
	const view = page.viewportSize()!;
	const box = (await overlay.boundingBox())!;
	expect(box.x, 'the overlay starts off the left edge').toBeGreaterThanOrEqual(0);
	expect(box.x + box.width, 'the overlay runs past the right edge').toBeLessThanOrEqual(view.width);
	expect(box.y, 'the overlay starts above the top edge').toBeGreaterThanOrEqual(0);
	/* Centred, near enough, but on the space the grid occupies, NOT on the window. The rail is
	 * fixed to the left edge and the overlay belongs to what is beside it, so measuring against the
	 * window would demand it hang partly under the rail to look "centred". The two margins inside
	 * that space should match rather than one being the whole width. */
	const rail = (await page.getByRole('navigation', { name: 'Main' }).boundingBox())!;
	const contentStart = rail.x + rail.width;
	// clientWidth, not the viewport: it stops where the scrollbar starts, and the layout is centred
	// on the space it can actually draw in. Measuring to the viewport charges the overlay for the
	// scrollbar and reads as an off-centre box that is exactly centred.
	const contentEnd = await page.evaluate(() => document.documentElement.clientWidth);
	const leftMargin = box.x - contentStart;
	const rightMargin = contentEnd - (box.x + box.width);
	expect(leftMargin, 'the overlay starts left of the content it belongs to').toBeGreaterThanOrEqual(
		0
	);
	/* Twelve pixels, not two. The overlay sits eight pixels further from the rail than from the
	 * right edge, and that asymmetry is not what this test is for. The fault it guards puts
	 * hundreds of pixels of the overlay off the screen. A tolerance this size still catches that
	 * and does not fail on a gutter.
	 */
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
	/* Ctrl-F belongs to the box when there is a box. This is the rule that stops the application's
	 * shortcut firing over a search somebody is halfway through writing. */
	await openGrid(page);
	const field = page.locator('header input').first();
	await field.click();
	await field.type('bea');

	await page.keyboard.press('ControlOrMeta+f');

	await expect(page.getByRole('dialog', { name: 'Search' })).toHaveCount(0);
});

/* --- the folder browser ----------------------------------------------------------------
 *
 * It is a MODE of Browse rather than a sheet over it: the same screen looked at a different
 * way, with where you are in the address. So the way out is the button that turned it on rather
 * than Escape, and there is no folder-shaped container to find.
 */

/**
 * The folders in the folder being looked at, which is all the explorer is: a band above the wall,
 * not a page frame of its own: a frame would make it a second screen beside the one it is a mode
 * of.
 */
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

	// The trail is the only thing on screen that says where you are, and it is the SHARED one in the
	// page header. A page does not draw a trail of its own.
	await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Clips');
});

test('and walking into a folder points the WALL at it, without leaving', async ({ page }) => {
	/*
	 * Pressing a folder both walks into it and points the wall at it, which is what a file manager
	 * has always done in one gesture: the wall under the folders IS the open folder. There is no
	 * separate "Browse here" verb that would leave the explorer for the tiles and lose the folders
	 * beside it.
	 */
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

	/* Still in the explorer. Asked of the CONTROL rather than of the band: a folder with nothing
	   inside it draws no folders at all, which is what a file manager does and is indistinguishable
	   from the band being gone. The pressed folder button is what says which of Browse's two ways of
	   looking is on. */
	await expect(page.getByRole('button', { name: 'Back to tiles' })).toBeVisible();
	await expect(page).toHaveURL(new RegExp(`[?&]folders=${FOLDERS[0].id}`));
	// And the wall really asked for that folder, which is the half no address can prove.
	await expect.poll(() => asked.some((one) => one.includes(`in=${FOLDERS[0].id}`))).toBe(true);
});

test('and the same button turns it off again without going anywhere', async ({ page }) => {
	await openGrid(page);
	const before = page.url();

	await page.getByRole('button', { name: 'Folders' }).click();
	await expect(explorer(page)).toBeVisible();

	// One button, not two: the control that turned the explorer on is the control that turns it off,
	// and it is the same folder in the same place wearing a pressed state.
	await page.getByRole('button', { name: 'Back to tiles' }).click();

	await expect(explorer(page)).toHaveCount(0);
	/*
	 * WAITED FOR, because the address is mid-flight at this instant.
	 *
	 * Turning the explorer off changes the question, so the wall takes its remembered row out of
	 * the address and puts a fresh one back when its page lands: two writes, a request apart.
	 * Read between them the address is the bare `/browse`, which is neither where it started nor
	 * where it ends up. The write is a `replaceState` (see `lib/grid/anchor.ts`) and takes effect
	 * at once.
	 *
	 * Polling is right here and would be wrong one line up: this is a value that ARRIVES and stays,
	 * not one moving through a range. It still fails if the row never comes back.
	 */
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
			// A page, not a bare list: the tag route pages with the rest of the entity walls, and
			// a store handed an array reads no rows at all.
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Tag' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	// Nothing is written by opening it; the press on a row is the write.
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
			// A page, not a bare list: the tag route pages with the rest of the entity walls, and
			// a store handed an array reads no rows at all.
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Tag' }).click();
	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();

	// Escape twice: once for the flyout, once for the menu it hangs from.
	await page.keyboard.press('Escape');
	await page.keyboard.press('Escape');

	await expect(flyout).toHaveCount(0);
	await expect(page.getByRole('menu')).toHaveCount(0);
	expect(written).toEqual([]);
	// And the selection is still there, because nothing was done to it.
	await expect(bar(page)).toContainText('3 files selected');
});

test('a person nobody has heard of can be made from the flyout and filed under', async ({
	page
}) => {
	/*
	 * Making somebody from the flyout files the clip under them on the same press.
	 *
	 * Otherwise filing a clip under somebody the library does not know means leaving, making them
	 * on the People screen, coming back and finding the clip again, so most of the time the clip
	 * stays filed under nobody. Their other names and links are still the People screen's to fill
	 * in, which is why the answer is a way through to that page rather than a second person editor
	 * in this flyout.
	 *
	 * Making something IS the answer to the flyout's question, so it closes: leaving it open,
	 * waiting for a confirm would be a dead end in which the person exists with no faces and no
	 * files and nothing on screen says so.
	 */
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
	await bar(page).getByRole('button', { name: 'Add to' }).click();
	await page.getByRole('menuitem', { name: 'Person' }).click();

	const flyout = page.locator('.pick');
	await expect(flyout).toBeVisible();
	// The flyout's one box is the list's narrowing box.
	await page.getByLabel('Filter the people list').fill('Orla Fennimore');
	await flyout.getByRole('menuitem', { name: 'Create Orla Fennimore' }).click();

	await expect.poll(() => made).not.toBeNull();
	expect(made!.name).toBe('Orla Fennimore');

	// Made AND filed, on the one press, for every picked file.
	await expect.poll(() => written).not.toBeNull();
	expect(written!.person_ids).toEqual(['p9']);
	expect(written!.asset_ids.slice().sort()).toEqual(['a1', 'a2']);

	// And the flyout is gone, because its question has been answered.
	await expect(flyout).toBeHidden();
});

test('Add to Person goes through the same flyout to the people endpoint', async ({ page }) => {
	/* The same component behind a third verb. What differs is only where the answer is sent, which
	 * is the thing worth checking once per verb rather than once per flyout. */
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
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
	/* A file came from where it came from, so this only ever adds. Correcting a wrong one is done on
	 * the file's own record, one file at a time, where what is being corrected is on the screen. */
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
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
	/* The bar's "Add to" is ONE button opening onto the places a file can go: five buttons side
	   by side would be most of a strip that scrolls sideways. Each row opens a flyout over the
	   whole set, and a press on one of its rows writes to every picked file at once; see
	   `FileVerbs`. */
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
	/* A destination filled in for somebody is a destination they did not pick, and this writes to a
	 * real disk. The button being dead until then is the whole guard. */
	await openGrid(page);

	await pickFirst(page, 2);
	/* Behind the bar's three dots: the bar names "Add to", the rating and the one that deletes,
	   and keeps everything else one press away. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 2 files' }).click();
	await page.getByRole('menuitem', { name: 'Move' }).click();

	// Scoped to the dialog: the bar's own verb reads "Move" too, so the page-wide name is ambiguous.
	const confirm = page.getByRole('alertdialog').getByRole('button', { name: 'Move', exact: true });
	await expect(confirm).toBeDisabled();

	/* Ticked, and still refused. The sheet has two reasons to hold the button now (no destination,
	   and no acknowledgement), so leaving both unanswered would let this pass on either one and it
	   would stop being a test about destinations at all. */
	await understand(page);
	await expect(confirm).toBeDisabled();
});

/* Tick the box that says what a move does, if this account is still being asked.
 *
 * Ticking it is what turns the asking off for good, so whether it is there depends on what the
 * account has done before, and these run against a shared test server in whatever state the
 * last run left it. Absent means already acknowledged, which is a state the sheet is meant to have. */
async function understand(page: Page): Promise<boolean> {
	const box = page.getByRole('checkbox', { name: /moves files on my disk/i });
	if ((await box.count()) === 0) return false;
	await box.check();
	return true;
}

test('and choosing one moves every picked file into it', async ({ page }) => {
	await openGrid(page);
	/* Confirming with the box ticked saves "stop asking" for this account, which would change the
	   install these run against, and the acknowledgement would then be missing from every later
	   run, quietly taking the check above with it. Answered here instead of written, and recorded,
	   because the box BEING what saves the preference is the whole design and is asserted nowhere
	   else: the jsdom tests cannot reach an enabled Move button without driving the destination
	   picker, and this is the only place the two halves meet. */
	const acknowledged: unknown[] = [];
	await page.route('**/api/settings', (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		acknowledged.push(route.request().postDataJSON());
		return route.fulfill({ status: 204, body: '' });
	});
	/* One request for the whole selection: the ids and the folder, answered with what changed. */
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
	/* Behind the bar's three dots: the bar names "Add to", the rating and the one that deletes,
	   and keeps everything else one press away. See `barShape`. */
	await bar(page).getByRole('button', { name: 'More for 2 files' }).click();
	await page.getByRole('menuitem', { name: 'Move' }).click();

	await page.getByLabel('Move to').click();
	await page.getByRole('option', { name: /Stills/ }).click();
	const wasAsked = await understand(page);
	await page.getByRole('alertdialog').getByRole('button', { name: 'Move', exact: true }).click();

	await expect.poll(() => moved.length).toBe(2);
	expect(moved.map((one) => one.id).sort()).toEqual(['a1', 'a2']);
	expect(new Set(moved.map((one) => one.folder))).toEqual(new Set(['f2']));

	/* And the acknowledgement was saved by CONFIRMING, not by ticking. If the sheet no longer asked
	   this account there was nothing to tick and nothing to save, which is the same design seen from
	   the other side. */
	if (wasAsked) {
		await expect
			.poll(() => acknowledged)
			.toEqual([{ values: { 'library.confirm_file_moves': false } }]);
	}
});
