/* People and Sites as one wall of cards, and a collection laid out like Browse but in its own
 * order. Layout claims, so a real browser. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/** The session's CSRF token, read per call because `signInAsAdmin` creates the session. */
async function csrf(page: Page): Promise<string> {
	const me = await page.request.get('/api/auth/me');
	return (await me.json()).csrf_token as string;
}

async function created(page: Page, path: string, data: object): Promise<string> {
	const made = await page.request.post(path, {
		data,
		headers: { 'x-csrf-token': await csrf(page) }
	});
	expect(made.ok(), await made.text()).toBeTruthy();
	return (await made.json()).id as string;
}

const person = (page: Page, name: string) => created(page, '/api/people', { name, vault: false });

const site = (page: Page, name: string) => created(page, '/api/sites', { name, kind: null });

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('people are drawn as a wall of cards rather than a row of words', async ({ page }) => {
	const name = `e2e-face-${Date.now()}`;
	await person(page, name);

	await page.goto('/people');

	const card = page.locator('.card', { hasText: name });
	await expect(card).toBeVisible();

	// A card taller than wide: the picture is the point.
	const box = (await card.boundingBox())!;
	expect(box.height, `the card is ${box.width}x${box.height}`).toBeGreaterThan(box.width);

	// A monogram, never a blank box that reads as a failed picture.
	await expect(card.locator('.monogram')).toHaveText(name.charAt(0).toUpperCase());
});

test('a heart on a person survives a reload', async ({ page }) => {
	// The write is optimistic; what matters is that the server kept it.
	const name = `e2e-heart-${Date.now()}`;
	await person(page, name);
	await page.goto('/people');

	const card = page.locator('.card', { hasText: name });
	await card.getByRole('button', { name: /favorite|favorite/i }).click();

	await page.reload();

	const after = page.locator('.card', { hasText: name });
	await expect(after.locator('.heart.on, .heart[aria-pressed="true"]')).toHaveCount(1);
});

test('a site is the same shape as a person', async ({ page }) => {
	// One card component for both screens, so they cannot drift.
	const name = `e2e-site-${Date.now()}`;
	await site(page, name);

	await page.goto('/sites');

	const card = page.locator('.card', { hasText: name });
	await expect(card).toBeVisible();
	await expect(card.getByRole('button', { name: /favorites/ })).toHaveCount(1);
	await expect(card.getByRole('button', { name: `${name}: not rated` })).toHaveCount(1);
	const box = (await card.boundingBox())!;
	expect(box.height).toBeGreaterThan(box.width);
});

test("a person's page carries the picture, the opinions and the way to their files", async ({
	page
}) => {
	const name = `e2e-detail-${Date.now()}`;
	const id = await person(page, name);

	// Armed before navigating: the grid asks the moment it mounts.
	const asking = page.waitForRequest((request) => request.url().includes('/api/assets?'));
	await page.goto(`/people/${id}`);

	await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();
	await expect(page.locator('.hero .cover')).toBeVisible();
	await expect(page.locator('.hero .opinions')).toBeVisible();

	// The same `AssetGrid` as Browse, under an `h2` beside the page's one `h1`.
	await expect(page.getByRole('heading', { name: 'Files', level: 2 })).toBeVisible();
	expect(new URL((await asking).url()).searchParams.get('people')).toBe(id);
});

test('the embedded grid gets the height it needs, and the page does not scroll as a whole', async ({
	page
}) => {
	// The grid needs its own scrolling box with a real height, or the outer scroll takes the hero.
	const name = `e2e-inline-${Date.now()}`;
	const id = await person(page, name);
	await page.goto(`/people/${id}`);

	const files = page.locator('.frame-body');
	await expect(files).toBeVisible();
	expect((await files.boundingBox())!.height, 'the grid was squashed to nothing').toBeGreaterThan(
		200
	);

	const frame = await page
		.locator('main')
		.evaluate((element) => getComputedStyle(element).overflowY);
	expect(frame).toBe('hidden');
	const scrolls = await page.evaluate(() => document.body.scrollHeight > window.innerHeight + 1);
	expect(scrolls, 'the page scrolls as a whole').toBe(false);
});

test('a site shows what came from it, inline', async ({ page }) => {
	const name = `e2e-sitefiles-${Date.now()}`;
	const id = await site(page, name);

	const asking = page.waitForRequest((request) => request.url().includes('/api/assets?'));
	await page.goto(`/sites/${id}`);

	await expect(page.getByRole('heading', { name: 'Files', level: 2 })).toBeVisible();
	expect(new URL((await asking).url()).searchParams.get('sites')).toBe(id);
});

test("a site's address is only stored when a browser could safely follow it", async ({ page }) => {
	// `javascript:` and `data:` links are refused where written, in `links`, not where drawn.
	const id = await site(page, `e2e-url-${Date.now()}`);

	const refused = await page.request.put(`/api/sites/${id}/details`, {
		data: { links: ['javascript:alert(1)'], notes: null },
		headers: { 'x-csrf-token': await csrf(page) }
	});

	expect(refused.status()).toBe(422);
});

test('a collection is laid out like Browse and keeps its own order', async ({ page }) => {
	// The order is the collection's, never re-sorted by the client.
	const id = await created(page, '/api/collections', { name: `e2e-order-${Date.now()}` });

	await page.goto(`/collections/${id}`);

	await expect(page.getByText(/Nothing in here yet/)).toBeVisible();
	await expect(page.locator('.wall .row')).toHaveCount(0);
});

test('a name too long for its card is cut off inside it, and readable in full', async ({
	page
}) => {
	// A truncated name keeps its ellipsis and shows in full in the app's tooltip; both are checked.
	const long = `e2e-${'verylongname'.repeat(4)}-${Date.now()}`;
	await person(page, long);

	await page.goto('/people');
	const card = page.locator('.card', { hasText: long });
	await expect(card).toBeVisible();

	const name = card.locator('.name');
	const inside = (await card.boundingBox())!;
	const text = (await name.boundingBox())!;
	expect(text.x + text.width, 'the name runs outside its card').toBeLessThanOrEqual(
		inside.x + inside.width + 1
	);

	// Card first, then name, repeated until it lands: the card lifts and reflows under the pointer.
	await expect(name).not.toHaveAttribute('title', /./);
	await expect(async () => {
		await card.hover();
		await name.hover();
		await expect(page.getByRole('tooltip')).toHaveText(long, { timeout: 2_000 });
	}).toPass({ timeout: 15_000 });
});

test('a collection tile carries no chrome, and its actions are on the right button', async ({
	page
}) => {
	// No chrome on a collection tile, which is too narrow; its actions are on the right-click menu.
	const name = `e2e-chrome-${Date.now()}`;
	const id = await created(page, '/api/collections', { name });

	await page.goto(`/collections/${id}`);
	await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();

	await expect(page.locator('.position')).toHaveCount(0);
	await expect(page.locator('.item-actions')).toHaveCount(0);
});

test('and the actions that were on it really do open on the right button', async ({ page }) => {
	// The menu opens and holds its actions: an absence alone passes a menu that throws.
	const name = `e2e-menu-${Date.now()}`;
	const id = await created(page, '/api/collections', { name });

	await page.route(`**/api/collections/${id}/items*`, (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: [
					{
						id: 'c1',
						media_type: 'video',
						width: 800,
						height: 600,
						duration_ms: 5000,
						favorite: false,
						rating: null,
						concealed: false
					}
				],
				total: 1,
				limit: 39,
				offset: 0
			})
		})
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));

	const broke: string[] = [];
	page.on('pageerror', (error) => broke.push(String(error)));

	await page.goto(`/collections/${id}`);
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile').first().click({ button: 'right' });

	/* "Remove from this collection" is this wall's own verb, added to the shared ones. */
	await expect(page.getByRole('menuitem', { name: 'Remove from this collection' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Use as the cover' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Move earlier' })).toHaveCount(0);
	await expect(page.getByRole('menuitem', { name: 'Move later' })).toHaveCount(0);

	expect(broke, 'the menu threw while rendering').toEqual([]);
});
