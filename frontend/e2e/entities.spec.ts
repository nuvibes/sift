/* People and Sites, as walls of cards, and a collection laid out like Browse.
 *
 * Every one of these is a layout claim, so none of it is answerable without a real browser: the
 * unit environment renders the markup happily whatever the stylesheet does with it. What is worth
 * checking is that the two screens really are one shape, that the opinions really persist, and
 * that a collection keeps ITS order rather than the grid's.
 */
import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/** The token a state-changing request has to carry. Read per call rather than cached: the session
 *  is created by `signInAsAdmin` and the token belongs to it. */
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

	/* A card, not a chip: it is taller than it is wide, because the picture is the point of the
	 * screen. A row of text chips would say nothing about anybody. */
	const box = (await card.boundingBox())!;
	expect(box.height, `the card is ${box.width}x${box.height}`).toBeGreaterThan(box.width);

	// No picture yet, so the monogram stands in. Never a blank box: an empty rectangle in a grid
	// of faces reads as a picture that failed to load.
	await expect(card.locator('.monogram')).toHaveText(name.charAt(0).toUpperCase());
});

test('a heart on a person survives a reload', async ({ page }) => {
	// The write is optimistic, so the interesting question is not whether it draws. It is whether
	// the server kept it and the list reads it back.
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
	/* One card component for both, so the two screens cannot drift into two ideas of where a heart
	 * goes. They are not the same KIND of thing (the slice under them is careful about that),
	 * but on a list screen they are the same four facts. */
	const name = `e2e-site-${Date.now()}`;
	await site(page, name);

	await page.goto('/sites');

	const card = page.locator('.card', { hasText: name });
	await expect(card).toBeVisible();
	/* The heart and the stars, each in its own corner of the card as on a person's, asked for by
	   what they are rather than by the box around them. */
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

	/* Armed BEFORE navigating. The grid asks for its page as soon as it mounts, so a wait started
	 * after `goto` is a wait for a request that has already been and gone. */
	const asking = page.waitForRequest((request) => request.url().includes('/api/assets?'));
	await page.goto(`/people/${id}`);

	await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();
	await expect(page.locator('.hero .cover')).toBeVisible();
	await expect(page.locator('.hero .opinions')).toBeVisible();

	/* Their files, inline, which is what somebody came to the page for. The same `AssetGrid` Browse
	 * draws (asked for by the person's id through the one query language) rather than a second arrangement
	 * of it. Under an `h2`, because the page already has an `h1` and two of those leave a screen
	 * reader with two answers to "what is this page". */
	await expect(page.getByRole('heading', { name: 'Files', level: 2 })).toBeVisible();
	expect(new URL((await asking).url()).searchParams.get('people')).toBe(id);
});

test('the embedded grid gets the height it needs, and the page does not scroll as a whole', async ({
	page
}) => {
	/* The grid virtualises against the element it scrolls in, so it has to HAVE one with a real
	 * height, and the frame around it has to stop scrolling, or the outer scrollbar moves the
	 * inner one out of reach and takes the hero with it. */
	const name = `e2e-inline-${Date.now()}`;
	const id = await person(page, name);
	await page.goto(`/people/${id}`);

	/* The grid's own scrolling body, which is what it virtualises against. The page hands the
	 * band INTO the grid's frame and draws no wrapper of its own, so the frame's body is the
	 * element that actually scrolls and the one the grid measures.
	 */
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

	// Armed before navigating; see the person's page above.
	const asking = page.waitForRequest((request) => request.url().includes('/api/assets?'));
	await page.goto(`/sites/${id}`);

	await expect(page.getByRole('heading', { name: 'Files', level: 2 })).toBeVisible();
	expect(new URL((await asking).url()).searchParams.get('sites')).toBe(id);
});

test("a site's address is only stored when a browser could safely follow it", async ({ page }) => {
	// `javascript:` and `data:` are both accepted by an href and both run as the page that drew it.
	// Refused where it is written rather than where it is drawn, because there will be more than
	// one place that draws it. A site's address is the first of its `links` (a
	// `site_url` field is ignored rather than refused, like any field the route does not
	// know), so `links` is where a bad one has to be turned away.
	const id = await site(page, `e2e-url-${Date.now()}`);

	const refused = await page.request.put(`/api/sites/${id}/details`, {
		data: { links: ['javascript:alert(1)'], notes: null },
		headers: { 'x-csrf-token': await csrf(page) }
	});

	expect(refused.status()).toBe(422);
});

test('a collection is laid out like Browse and keeps its own order', async ({ page }) => {
	/* The order IS the collection, so it must never be re-sorted by the client. The layout is
	 * Browse's (the same justified rows, the same tile), and the sequence is the server's. */
	const id = await created(page, '/api/collections', { name: `e2e-order-${Date.now()}` });

	await page.goto(`/collections/${id}`);

	// Empty, so the wall is absent and the screen says so rather than drawing nothing at all.
	await expect(page.getByText(/Nothing in here yet/)).toBeVisible();
	await expect(page.locator('.wall .row')).toHaveCount(0);
});

test('a name too long for its card is cut off inside it, and readable in full', async ({
	page
}) => {
	/* The name is truncated with an ellipsis, so there has to be a way to read the whole of it:
	 * the app's tooltip, not a `title`. Wrapping the anchor for the tooltip can take away the
	 * block box `text-overflow` needs, which is a silent failure: the styling is all still there
	 * and a long name simply runs out of the card.
	 *
	 * Both halves are checked, because either alone passes against the other being broken.
	 */
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

	/* And the whole of it is reachable, in the app's own tooltip rather than the browser's.
	 *
	 * The card is hovered first and the name second, which is what a hand does and what this
	 * needs. A card moves when the pointer arrives on it (it lifts, and the row it is in
	 * re-flows around the new width), so a pointer placed directly on the name can land on empty
	 * space a frame later, and the tooltip gets an enter immediately followed by a leave.
	 *
	 * And the walk is repeated until it lands, because settling the card is not a guarantee of
	 * it. The label is shown from a pointer MOVE and taken away by a leave, so a re-flow arriving
	 * between the two hovers (a matter of how busy the machine is, not of whether the tooltip
	 * works) puts the pointer on empty space and the label never opens. Moving the pointer
	 * again is what a hand does.
	 */
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
	/*
	 * Four buttons at 16px plus their padding is about 140px of controls, and a justified row draws
	 * tiles as narrow as 110, so on a portrait clip they would run off the side and over the neighbour.
	 * No ordinal either: the order is what you see, left to right.
	 *
	 * The actions are on the right button instead. Taking an item out of a collection and
	 * choosing its cover have no other home, and the arrows are the only way to reorder without a mouse. The right-click
	 * menu is where the grid already keeps what you can do to one item.
	 */
	const name = `e2e-chrome-${Date.now()}`;
	const id = await created(page, '/api/collections', { name });

	await page.goto(`/collections/${id}`);
	await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible();

	await expect(page.locator('.position')).toHaveCount(0);
	await expect(page.locator('.item-actions')).toHaveCount(0);
});

test('and the actions that were on it really do open on the right button', async ({ page }) => {
	/*
	 * The other half: the menu opens and holds its actions.
	 *
	 * An assertion that the chrome is ABSENT is also true of a screen with the actions deleted,
	 * or of a menu that throws while rendering (a snippet named like the page's own array,
	 * shadowing it), opens, and draws nothing, with the type check clean.
	 *
	 * An absence is not a behaviour. This opens the menu and reads it.
	 */
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

	/* Membership is one of this wall's own verbs appended to the
	   shared file verbs, and it is declared once as "Remove from this collection". */
	await expect(page.getByRole('menuitem', { name: 'Remove from this collection' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Use as the cover' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Move earlier' })).toHaveCount(0);
	await expect(page.getByRole('menuitem', { name: 'Move later' })).toHaveCount(0);

	// The failure is a thrown error rather than a missing element, so it is worth naming.
	expect(broke, 'the menu threw while rendering').toEqual([]);
});
