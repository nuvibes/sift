import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Sort and pagination, the two ways the grid's shape is chosen.
 *
 * Both are proven against a mocked library rather than a real one: what is under test is the
 * client asking for the right page in the right order and drawing the control that changes them,
 * not the SQL that answers, which has its own tests server-side.
 */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** A library of `count` fake assets, and a record of every page the client asked for. */
function library(page: Page, count: number) {
	const asks: { offset: number; limit: number; sort: string; from: string | null }[] = [];
	return page
		.route('**/api/assets?*', async (route) => {
			const url = new URL(route.request().url());
			const limit = Number(url.searchParams.get('limit') ?? '50');
			/* A top-up continues from the last row the grid holds (`after=<id>`) rather than
			   naming an offset, the way the server reads it: one seek on the sort's index. The
			   ids here are `a<index>`, so the block after `a98` starts at 99. Answered by offset
			   alone, a top-up would be handed the first block again and the grid would throw on
			   the duplicate keys. */
			const after = url.searchParams.get('after');
			const offset =
				after === null ? Number(url.searchParams.get('offset') ?? '0') : Number(after.slice(1)) + 1;
			/* `from` is recorded, and a MISSING offset is not treated as 0. Otherwise a
			   request anchored to a file would look exactly like a request for the first page,
			   and a grid that started a new search at the old search's position would pass this
			   file. */
			asks.push({
				offset,
				limit,
				sort: url.searchParams.get('sort') ?? '(none)',
				from: url.searchParams.get('from')
			});
			const items = Array.from(
				{ length: Math.max(0, Math.min(limit, count - offset)) },
				(_, i) => ({
					id: `a${offset + i}`,
					media_type: 'image',
					width: 1000,
					height: 1000,
					duration_ms: null,
					favorite: false,
					rating: null,
					concealed: false
				})
			);
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ items, total: count, limit, offset })
			});
		})
		.then(() => asks);
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
});

test('the grid sends the chosen sort, and remembers it across a reload', async ({ page }) => {
	const asks = await library(page, 10);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	// The default is newest first.
	expect(asks.at(-1)?.sort).toBe('newest');

	/* Ordering is a MENU on the bar: one of a list, picked and done (see
	   `screen-bar.svelte.ts`), so these are options in a listbox rather than buttons in a strip. */
	await page.getByRole('button', { name: 'Sort by' }).click();
	await page.getByRole('option', { name: 'Name A-Z', exact: true }).click();

	await expect.poll(() => asks.at(-1)?.sort).toBe('name_az');

	// Remembered in the browser: a reload starts where it was left, not back at the default.
	await page.reload();
	await expect(page.locator('.tile').first()).toBeVisible();
	expect(asks.at(-1)?.sort).toBe('name_az');
});

test('a library that fits on one page still says how much of it there is', async ({ page }) => {
	/*
	 * The pager is always drawn, even below one page: it says WHERE YOU ARE as well as how to move,
	 * and "1-10 of 10" is worth saying. The steps are simply disabled, and the bottom of the
	 * screen does not change shape as a search narrows.
	 */
	await library(page, 10);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const pager = page.getByRole('navigation', { name: 'Pages' });
	await expect(pager).toBeVisible();
	await expect(pager.getByRole('button', { name: 'Go to a position' })).toContainText('of 10');
	await expect(pager.getByRole('button', { name: 'Next page' })).toBeDisabled();
	await expect(pager.getByRole('button', { name: 'Previous page' })).toBeDisabled();
});

test('a library past one page moves by position, not by page number', async ({ page }) => {
	/*
	 * There are no page numbers, and that is the point rather than an omission. A page
	 * holds as many files as fill its rows, so "page 3" is a different set of files on a laptop than
	 * on a monitor, so a link to it would land the recipient somewhere else. The pager moves by pages
	 * and reports a POSITION, which means the same thing on every screen.
	 */
	const asks = await library(page, 400);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const pager = page.getByRole('navigation', { name: 'Pages' });
	await expect(pager).toBeVisible();
	await expect(pager.getByRole('button', { name: /^Page \d/ })).toHaveCount(0);

	// The first fetch starts at the beginning.
	expect(asks[0].offset).toBe(0);
	const readout = pager.getByRole('button', { name: 'Go to a position' });
	await expect(readout).toContainText('of 400');

	// Forward: the next page starts after the one on screen, wherever that ended.
	const before = await readout.innerText();
	await pager.getByRole('button', { name: 'Next page' }).click();
	await expect(readout).not.toHaveText(before);
	await expect.poll(() => asks.at(-1)?.offset).toBeGreaterThan(0);

	// ...and back to the beginning is reachable in one press, from anywhere.
	await pager.getByRole('button', { name: 'First page' }).click();
	await expect(readout).toContainText('1-');
});

test('changing the sort returns to the beginning', async ({ page }) => {
	const asks = await library(page, 400);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const pager = page.getByRole('navigation', { name: 'Pages' });
	await pager.getByRole('button', { name: 'Next page' }).click();
	await expect.poll(() => asks.at(-1)?.offset).toBeGreaterThan(0);

	const before = asks.length;
	await page.getByRole('button', { name: 'Sort by' }).click();
	await page.getByRole('option', { name: 'Oldest first', exact: true }).click();

	/*
	 * A position in one order is not a position in another: a new sort starts at the top, from
	 * the top, and not from an anchor left in the address by the page that was on screen.
	 *
	 * The FIRST request of the new question, not the last. A page is filled by asking for an
	 * estimate and topping up if it fell short, so the last request of a page routinely starts part
	 * way into it, and `at(-1)` would be reading the top-up.
	 */
	await expect.poll(() => asks.length).toBeGreaterThan(before);
	expect(asks[before]).toMatchObject({ offset: 0, sort: 'oldest', from: null });
});

test('a new search starts at the top, not where the last one was left', async ({ page }) => {
	/*
	 * The address carries the file a page starts at, and the grid writes it there as each page
	 * lands. So by the time somebody types a new search there is an anchor in the bar, belonging
	 * to the question they have just stopped asking.
	 *
	 * Read again, it starts the new search at the old one's position: four hundred files down a
	 * list of things that have nothing to do with it, with nothing on screen saying why. The anchor
	 * is honoured exactly once, on arrival, and taken out of the address after that.
	 */
	const asks = await library(page, 400);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page
		.getByRole('navigation', { name: 'Pages' })
		.getByRole('button', { name: 'Next page' })
		.click();
	await expect.poll(() => asks.at(-1)?.offset).toBeGreaterThan(0);
	// The grid put the page's first file in the address, which is what makes the link shareable.
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).not.toBeNull();

	const before = asks.length;
	await page.goto('/browse?q=something-else');

	// The first request of the new question, for the reason given in the test above.
	await expect.poll(() => asks.length).toBeGreaterThan(before);
	expect(asks[before]).toMatchObject({ offset: 0, from: null });
});
