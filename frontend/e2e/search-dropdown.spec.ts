import { expect, test } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The dropdown under the search box, and its two kinds of row.
 *
 * A result row is a raised, rounded surface that lights up when pointed at. The RECENT group's
 * "Clear" control is not a result (it sits in the group heading), and it must not pick up the
 * row highlight: as a `<button>` inside a list item, a row-hover rule would match it and draw a
 * full-width slab a different shape from every real row.
 */

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
	// The dropdown's contents, mocked, so a RECENT group with a Clear control is always present,
	// independent of the shared account's history and of how fast a prior search was written.
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

	// A result row lights up to the raised surface colour; Clear must not. Read its background while
	// hovered and confirm it is transparent, i.e. it did not take the row highlight.
	const bg = await clear.evaluate((el) => getComputedStyle(el).backgroundColor);
	expect(bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent', `Clear lit up as a row: ${bg}`).toBe(
		true
	);

	// And a real result row, hovered, DOES take the highlight, so the test is not passing because
	// nothing highlights.
	//
	// POLLED: a row STEPS to its hover ground over `--dur-instant` rather than arriving there (the
	// app's hover rule), and a computed style read in the same breath as the pointer arriving is
	// the value the step STARTS from: the transparent one.
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
	/*
	 * The box makes chips out of what is being typed, not only out of a query already run:
	 * otherwise everything typed after the chips stays plain text, and because chips serialise in
	 * front of that text, a typed `OR` between two picked values would end up at the end of the
	 * query, joining nothing.
	 *
	 * The server does the reading. Nothing here knows what `-` or `OR` mean, which is the point.
	 */
	await signInAsAdmin(page);
	await page.goto('/browse');

	const box = page.getByRole('combobox', { name: 'Search' });

	/* An exclusion, TYPED a character at a time rather than filled.
	 *
	 * `fill` sets the value in one go, which skips the state that matters: typing `-tags:`
	 * reaches a moment where the field has been recognized and the `-` has not, and promoting the
	 * field to a chip there would leave the `-` behind as a stray word. Pressed key by key, this
	 * covers that state.
	 */
	await box.click();
	await box.pressSequentially('-tags:');

	/* The state that matters. The server has had time to say that the caret is inside a `tags:`
	 * filter, and drawing the field as a chip here would leave the `-` outside it as a stray
	 * word, an exclusion split in half. A pending chip is a field waiting for a value and has
	 * nowhere to hold "and not", so an excluded field stays text.
	 */
	await box.pressSequentially('beach');
	await box.press(' ');

	// And the `-` went into the chip rather than being left behind.
	await expect(box).toHaveValue('');

	const chip = page.locator('.search .chip').first();
	await expect(chip).toBeVisible();
	await expect(chip).toContainText('not beach');
	// And it is out of the text, or it would be in the query twice.
	await expect(box).toHaveValue('');

	// A choice, in one chip rather than two.
	await box.fill('tags:beach OR tags:sunset');
	await box.press(' ');

	await expect(page.locator('.search .chip').filter({ hasText: 'or' })).toBeVisible();
});

test('an exclusion built from the dropdown keeps its minus', async ({ page }) => {
	/*
	 * A `-`, then the field picked off the list.
	 *
	 * Drawing the field as a chip on its own would leave the `-` outside it as a word (`-
	 * [tags:]` on screen), which serialises back to `tags:beach`: the exclusion silently dropped,
	 * "not this" quietly become "this".
	 *
	 * A space after the minus, because that is what makes the rest of it a bare word the server
	 * offers a filter for. `-tags` in one run is not a token it recognizes, so the list never opens
	 * on it and this path is the only way to reach the state.
	 */
	await signInAsAdmin(page);

	/*
	 * The dropdown is answered here rather than by the server, and that is the point rather than a
	 * convenience. The first test in this file mocks it for a related reason.
	 *
	 * A filter row acts on whatever answer the box is currently HOLDING, and the rows for `-` and
	 * for `- tag` are identical to look at. The two answers are not: for `- tag` the server says the
	 * word starts at position two, so the minus is in front of what gets replaced and survives; for
	 * `-` alone it says nought, so the minus is INSIDE what gets replaced and the box is rewritten
	 * to nothing at all. A press landing before the answer for what was typed has arrived therefore
	 * empties the box, on a loaded machine, in a test that passes every time on a quiet one.
	 *
	 * So the Tags row exists only while the answer is the one for `- tag`. Anything else offers
	 * nothing to press, and the test fails by timing out on a row that is not there rather than by
	 * quietly applying the wrong answer. The position is the server's own (`word_prefix` has its
	 * own tests), and what is under test here is what the BOX does with it.
	 */
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

	/*
	 * NOT clicked first. The dropdown asks the server as soon as it opens, with the box still
	 * empty, so clicking and then typing puts two questions in flight, and the row that gets
	 * pressed is read out of whichever answer the box is HOLDING at that moment: the empty one
	 * landing second rebuilds the list under the press, the row at that index is a different KIND,
	 * and pressing a filter runs a search instead. Filling focuses the field with text already in
	 * it, so there is one question and one answer and no window at all.
	 */

	/*
	 * Set in ONE go, and then the answer for it waited for by name.
	 *
	 * Typing it a character at a time asks five questions, and the row acts on whichever answer the
	 * box is HOLDING when the press lands. Waiting for the last answer is not enough on a loaded
	 * machine: an earlier one, for a prefix, can arrive after it and take its place, and the
	 * answer for a prefix replaces a different span of the text, so the press rewrites the box to
	 * something else entirely.
	 *
	 * One question has no out-of-order window. What this test is about is what the box does with an
	 * answer, not what it does with a keystroke.
	 */
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

	/* Not a chip on its own: a pending chip is a field waiting for a value, and it has nowhere to hold
	   the negation. So the whole token stays text until the parser can read it, and it is written in
	   the canonical form, with the space the field was reached through closed up. */
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
	/*
	 * Going to a person is something somebody did in the box, so Recent records it as well as what
	 * was typed and entered.
	 *
	 * What is kept is what the thing IS, never the address: an address is a route, and a route
	 * stored in a database is one that breaks the day it moves.
	 */
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
	// And it did what picking them always did.
	await expect(page).toHaveURL(/\/people\/p9$/);
});

test('a remembered person goes back to them, rather than searching for their name', async ({
	page
}) => {
	// A memory that replayed as something other than what was done is a second behaviour wearing
	// the first one's clothes: the row says Orla Fennimore, and pressing it has to mean what it meant.
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

	/* It SAYS it leaves, before it is pressed. This band holds rows that run a search and rows
	   that go to a thing, and a list where the only way to learn which is which is to press one is a
	   list people stop pressing. The mark is the same one the matches band carries. */
	const person = page.getByRole('option', { name: /Orla Fennimore/ });
	const search = page.getByRole('option', { name: /sunset/ });
	expect(await person.locator('.goes').count(), 'a remembered person did not say it leaves').toBe(
		1
	);
	expect(await search.locator('.goes').count(), 'a remembered search claimed to leave').toBe(0);

	await person.click();

	await expect(page).toHaveURL(/\/people\/p9$/);

	// The control: a remembered SEARCH still runs as a search, so the assertion above is about the
	// kind of row rather than about every remembered row having become a link.
	await page.goto('/browse');
	await page.getByRole('combobox', { name: 'Search' }).click();
	await page.getByRole('option', { name: /sunset/ }).click();
	await expect(page).toHaveURL(/\/browse\?q=sunset$/);
});
