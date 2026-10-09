import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The RECENT group's Clear control sits in the heading and must not take the result rows' hover
 * slab, which a row-hover rule would draw on any `<button>` in a list item. */

test('the recent Clear control is not styled as a result row', async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	// Mocked, so a RECENT group with Clear is always there whatever the account's history.
	await page.route('**/api/search/suggest*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				token: null,
				filters: [
					{ field: 'tags', label: 'Tags', hint: 'Anything you have tagged', example: 'tags:beach' }
				],
				matches: [],
				recent: [{ kind: 'query', subject: 'tags:beach', label: 'tags:beach' }],
				replace_from: 0,
				for_query: ''
			})
		})
	);
	await page.goto('/browse');

	const box = page.locator('input[type="search"]');
	await box.click();
	await expect(page.locator('.popover')).toBeVisible();

	const clear = page.getByRole('button', { name: 'Clear' });
	await expect(clear).toBeVisible();
	await clear.hover();

	// Transparent: it did not take the row highlight.
	const bg = await clear.evaluate((el) => getComputedStyle(el).backgroundColor);
	expect(bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent', `Clear lit up as a row: ${bg}`).toBe(
		true
	);

	// And a real row DOES, so this is not passing because nothing highlights. Polled: the hover
	// steps over `--dur-instant`, and an immediate read is where it starts from.
	const row = page.locator('.popover li:not(.head) > button').first();
	await row.hover();
	await expect
		.poll(() => row.evaluate((el) => getComputedStyle(el).backgroundColor), {
			message: 'a result row did not light up'
		})
		.not.toBe('rgba(0, 0, 0, 0)');
});

test('a filter typed into the box becomes a chip, exclusions and choices included', async ({
	page
}) => {
	/* The box makes chips of what is being typed, or a typed `OR` would serialise after the chips
	 * and join nothing. The server does the reading. */
	await signInAsAdmin(page);
	await page.goto('/browse');

	const box = page.getByRole('combobox', { name: 'Search' });

	/* Typed key by key: `fill` skips the moment `-tags:` is recognized and the `-` is not. */
	await box.click();
	await box.pressSequentially('-tags:');

	/* A pending chip cannot hold "and not", so an excluded field stays text. */
	await box.pressSequentially('beach');
	await box.press(' ');

	// The `-` went into the chip.
	await expect(box).toHaveValue('');

	const chip = page.locator('.search .chip').first();
	await expect(chip).toBeVisible();
	await expect(chip).toContainText('not beach');
	// Out of the text, or it would be in the query twice.
	await expect(box).toHaveValue('');

	await box.fill('tags:beach OR tags:sunset');
	await box.press(' ');

	await expect(page.locator('.search .chip').filter({ hasText: 'or' })).toBeVisible();
});

test('an exclusion built from the dropdown keeps its minus', async ({ page }) => {
	/* A `-`, a space, then the field picked off the list: a chip for the field alone would drop the
	 * exclusion. `-tags` in one run is not a token the server offers a filter for. */
	await signInAsAdmin(page);

	/* Answered here: for `- tag` the server's word starts after the minus, for `-` alone it does
	 * not, and the rows look identical. The row for tags exists only for the right answer, so a
	 * press landing early times out rather than emptying the box. */
	await page.route('**/api/search/suggest*', (route) => {
		const asked = new URL(route.request().url()).searchParams.get('q');
		void route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				token: null,
				filters:
					asked === '- tag'
						? [
								{
									field: 'tags',
									label: 'Tags',
									hint: 'Anything you have tagged',
									example: 'tags:beach'
								}
							]
						: [],
				matches: [],
				recent: [],
				replace_from: 2,
				for_query: asked
			})
		});
	});
	await page.goto('/browse');

	const box = page.getByRole('combobox', { name: 'Search' });

	/* Filled in one go, not clicked or typed: each question is an answer the press could read out
	 * of order, and this test is about what the box does with an answer. */
	const answered = page.waitForResponse(
		(one) =>
			one.url().includes('/api/search/suggest') &&
			new URL(one.url()).searchParams.get('q') === '- tag'
	);
	await box.fill('- tag');
	await expect(page.locator('.popover')).toBeVisible();
	await expect(box).toHaveValue('- tag');
	await answered;

	const tagsFilter = page.locator('.popover li:not(.head) > button').filter({ hasText: 'Tags' });
	await expect(tagsFilter.first()).toBeVisible();
	await tagsFilter.first().click();

	/* The whole token stays text, written in canonical form with the space closed up. */
	await expect(page.locator('.chip.pending')).toHaveCount(0);
	await expect(box).toHaveValue('-tags:');

	await box.pressSequentially('beach');
	await box.press(' ');

	await expect(page.locator('.chip').first()).toContainText('not beach');
	await expect(box).toHaveValue('');
});

test('picking somebody out of the dropdown is remembered, and remembered as what they are', async ({
	page
}) => {
	/* Going to a person is recorded in Recent as what the thing IS, never its address: a stored
	 * route breaks the day it moves. */
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/search/suggest*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				token: null,
				filters: [],
				matches: [
					{
						value: 'Orla Fennimore',
						detail: null,
						count: 3,
						field: 'people',
						opens: null,
						id: 'p9'
					}
				],
				recent: [],
				replace_from: 0,
				for_query: 'orla'
			})
		})
	);
	let kept: { kind: string; subject: string; label: string } | null = null;
	await page.route('**/api/search/history', (route) => {
		if (route.request().method() !== 'POST') return route.continue();
		kept = route.request().postDataJSON();
		return route.fulfill({ status: 204, body: '' });
	});
	await page.route('**/api/people/p9*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: 'p9', name: 'Orla Fennimore', aliases: [], links: [] })
		})
	);
	await page.goto('/browse');

	const box = page.getByRole('combobox', { name: 'Search' });
	await box.fill('orla');
	await page.getByRole('option', { name: /Orla Fennimore/ }).click();

	await expect.poll(() => kept).not.toBeNull();
	expect(kept).toEqual({ kind: 'people', subject: 'p9', label: 'Orla Fennimore' });
	await expect(page).toHaveURL(/\/people\/p9$/);
});

test('a remembered person goes back to them, rather than searching for their name', async ({
	page
}) => {
	// The row says Orla Fennimore, and pressing it means what it meant.
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/search/suggest*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				token: null,
				filters: [],
				matches: [],
				recent: [
					{ kind: 'people', subject: 'p9', label: 'Orla Fennimore' },
					{ kind: 'query', subject: 'sunset', label: 'sunset' }
				],
				replace_from: 0,
				for_query: ''
			})
		})
	);
	await page.route('**/api/people/p9*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ id: 'p9', name: 'Orla Fennimore', aliases: [], links: [] })
		})
	);
	await page.goto('/browse');

	await page.getByRole('combobox', { name: 'Search' }).click();
	await expect(page.locator('.popover')).toBeVisible();

	/* It says it leaves before it is pressed, with the matches band's mark. */
	const person = page.getByRole('option', { name: /Orla Fennimore/ });
	const search = page.getByRole('option', { name: /sunset/ });
	expect(await person.locator('.goes').count(), 'a remembered person did not say it leaves').toBe(
		1
	);
	expect(await search.locator('.goes').count(), 'a remembered search claimed to leave').toBe(0);

	await person.click();

	await expect(page).toHaveURL(/\/people\/p9$/);

	// The control: a remembered SEARCH still runs as a search.
	await page.goto('/browse');
	await page.getByRole('combobox', { name: 'Search' }).click();
	await page.getByRole('option', { name: /sunset/ }).click();
	await expect(page).toHaveURL(/\/browse\?q=sunset$/);
});
