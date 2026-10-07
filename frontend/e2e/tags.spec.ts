import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/* Tagging and rating, in a real browser, against the real server.
 *
 * The tag half runs against a real database: a tag made here is really written, so the assertions
 * are about what the server accepted rather than about what a mock was told. The grid is served
 * from an intercepted response because what is under test is the controls on a tile, not the
 * importing of media, and a fixed set of items makes the tile a test can point at findable.
 *
 * These share one server with every other spec file, so anything written here is named distinctly
 * and the run is serial: two files creating a tag called "beach" at the same time is not the thing being
 * tested.
 */

test.describe.configure({ mode: 'serial' });

const ASSETS = [
	{ id: 'a1', media_type: 'video', width: 1920, height: 1080, duration_ms: 95_000 },
	{ id: 'a2', media_type: 'video', width: 1080, height: 1920, duration_ms: 30_000 }
	/* `thumb` says the still has been built. Without it these read as assets still being imported,
	   and a tile that is still importing carries no controls at all, which is not what the stills
	   404ing below does, and a heart the app draws correctly would not be on the page. */
].map((asset) => ({ ...asset, favorite: false, rating: null, concealed: false, thumb: true }));

async function serveLibrary(page: import('@playwright/test').Page) {
	await page.route('**/api/assets?*', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: ASSETS, total: ASSETS.length, limit: 50, offset: 0 })
		});
	});
	// The stills would 404 against a library that does not exist; a tile draws its frame either way.
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* A Sift with no library root draws the first-run wizard over everything, modally, so every
	 * click below lands on it instead. Whether it appears depends on whether some other spec file
	 * has already added a root, which would make this file pass or fail on the order the suite ran in.
	 * Nothing here is about first run, so it is simply answered away. */
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

/**
 * Make a tag the way the wall offers it: Add opens the blank record on a screen of its own, the
 * same as on every other entity wall, and Save makes it.
 */
async function makeTag(page: Page, name: string): Promise<void> {
	await page.goto('/tags');
	await page.getByRole('button', { name: 'Add tag' }).click();
	await expect(page).toHaveURL(/\/tags\/new$/);
	await page.getByLabel('Name', { exact: true }).fill(name);
	await page.getByRole('main').getByRole('button', { name: 'Save', exact: true }).first().click();
}

/**
 * The wall of tags, narrowed to one name.
 *
 * The wall is paged to the screen and ordered by use, and a tag made a moment ago is on nothing:
 * among everything else this suite has made, it can sit on a page nobody is looking at.
 */
async function tagsNamed(page: Page, name: string): Promise<void> {
	await page.goto('/tags');
	await page.getByRole('searchbox', { name: 'Search tags' }).fill(name);
}

test('a tag can be made, renamed and deleted', async ({ page }) => {
	const original = `e2e-holiday-${Date.now()}`;
	const renamed = `${original}-renamed`;

	await makeTag(page, original);
	// Made, and taken to its own page.
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);
	await tagsNamed(page, original);
	await expect(page.getByText(original, { exact: false })).toBeVisible();

	/*
	 * Renaming is on the right-click menu, where every other wall keeps it. The card's click goes
	 * where the word promises (the library, narrowed to this tag), the same for every account,
	 * so the gesture does not mean two different things depending on who is signed in.
	 */
	const card = page.locator('.card', { hasText: original }).first();
	await card.click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rename' }).click();

	/* The wall's one rename box, a question with the name in it. Save is taken from the question
	   itself: the top bar carries a "Save" control too. */
	const asking = page.getByRole('alertdialog');
	await asking.getByLabel('Name', { exact: true }).fill(renamed);
	await asking.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByText(renamed, { exact: false })).toBeVisible();

	/* Deleting is on the same right-click menu, like every verb acting on a row; the card's
	   corners belong to the heart and the rating. */
	await page.locator('.card', { hasText: renamed }).first().click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Delete' }).click();
	await page.getByRole('button', { name: 'Delete tag' }).click();
	await expect(page.getByText(renamed, { exact: false })).toHaveCount(0);
});

test('a tag can be hearted and rated, and it survives a reload', async ({ page }) => {
	/*
	 * The heart and the stars on a TAG, like every other wall.
	 *
	 * The interesting question is not whether it draws. Both writes are optimistic and the store
	 * settles from the server's own answer, so the only thing worth asserting is that the server
	 * KEPT it and reads it back, which is what a reload asks. A test that pressed the heart and
	 * looked at the heart would pass against a card that never sent anything.
	 */
	const name = `e2e-opinion-${Date.now()}`;

	await makeTag(page, name);
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);
	await tagsNamed(page, name);

	const card = page.locator('.card', { hasText: name }).first();
	await expect(card).toBeVisible();

	await card.getByRole('button', { name: 'Add to favorites' }).click();
	// A fresh load rather than `reload`: the narrowing is the box's, so it is typed again.
	await tagsNamed(page, name);

	const back = page.locator('.card', { hasText: name }).first();
	await expect(back).toBeVisible();
	await expect(
		back.getByRole('button', { name: 'Remove from favorites' }),
		'the heart did not come back from the server'
	).toBeVisible();
});

test('the two opinion orders are offered on a wall of tags', async ({ page }) => {
	/* The same menu on all four walls whose things can be hearted or rated: People, Sites, Tags
	   and Collections. */
	await page.goto('/tags');

	const sort = page.getByRole('button', { name: /Sort by/ });
	await expect(sort).toBeEnabled();
	await sort.click();

	for (const order of ['Favorites first', 'Highest rated']) {
		await expect(
			page.getByRole('option', { name: order }),
			`a wall of tags does not offer ${order}`
		).toHaveCount(1);
	}
});

test('a second tag with the same name in another case is refused', async ({ page }) => {
	const name = `e2e-beach-${Date.now()}`;

	await makeTag(page, name);
	await expect(page).toHaveURL(/\/tags\/(?!new)[^/]+$/);

	await makeTag(page, name.toUpperCase());

	/* Refused, in words that say why, and still on the form with the name in it: a flat "could not
	   be saved" would leave somebody guessing at a name they can see is spelled right. */
	await expect(page.getByText("There's already a tag called")).toBeVisible();
	await expect(page).toHaveURL(/\/tags\/new$/);
});

test('the tag screen explains the gesture to somebody who has no tags yet', async ({ page }) => {
	/*
	 * What the screen says about the gesture: the empty state, which is the one place on `/tags`
	 * that names the drag. The promise that a drop moves nothing on disk lives in `drag-assign`'s
	 * own documentation and in the toast AFTER a drop ("No files moved."), not in front of the
	 * gesture.
	 *
	 * Served empty on purpose: other files in this suite create tags, so what a real one shows
	 * depends on the order the suite ran in.
	 */
	await page.route('**/api/tags*', (route) =>
		route.fulfill({ json: { items: [], total: 0, limit: 50, offset: 0 } })
	);
	await page.goto('/tags');

	await expect(page.getByText(/drag clips onto it/i)).toBeVisible();
});

test('the heart is on a tile and is saved, and the stars are not on a tile at all', async ({
	page
}) => {
	/*
	 * A tile carries the one-gesture opinion and not the five-target one.
	 *
	 * The rows are justified, so a portrait clip is about 120px across at a height the grid calls
	 * roomy, and a heart plus five stars is 102 before the duration chip in the same corner is
	 * counted. Stars there either overflow or shrink into five small targets on the picture in the
	 * one place somebody is skimming rather than deciding, where landing on three instead of four
	 * is a silent wrong answer. Rating lives on the asset itself, which has room for it.
	 *
	 * Both halves are asserted. Without the second this passes with the stars put back.
	 */
	await serveLibrary(page);

	const favorite = page.waitForRequest(
		(request) => request.url().includes('/favorite') && request.method() === 'PUT'
	);

	await page.goto('/browse');
	const tile = page.locator('.tile-frame').first();
	await tile.hover();

	await tile.getByRole('button', { name: 'Add to favorites' }).click();
	expect((await favorite).postDataJSON()).toEqual({ favorite: true });

	await expect(tile.getByRole('radio', { name: '4 stars' })).toHaveCount(0);
});

test('using a control on a tile does not open the player', async ({ page }) => {
	/* The controls sit over a tile whose whole surface opens the player. This is the interaction
	 * that breaks first if the overlay is ever put inside the button: a button nested in a
	 * button, where the outer one swallows every press meant for the inner.
	 *
	 * The heart, because it is what a tile carries. What matters is not which control it is:
	 * reaching for the furniture must not start playing the thing the furniture is on.
	 */
	await serveLibrary(page);
	await page.goto('/browse');

	const tile = page.locator('.tile-frame').first();
	await tile.hover();
	await tile.getByRole('button', { name: 'Add to favorites' }).click();

	await expect(page).toHaveURL(/\/browse/);
});
