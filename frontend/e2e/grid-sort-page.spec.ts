import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* Sort and pagination against a mocked library: the client's requests, not the SQL. */

const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

function library(page: Page, count: number) {
	const asks: { offset: number; limit: number; sort: string; from: string | null }[] = [];
	return page
		.route('**/api/assets?*', async (route) => {
			const url = new URL(route.request().url());
			const limit = Number(url.searchParams.get('limit') ?? '50');
			// A top-up continues from `after`, as the server does; by offset it repeats a block.
			const after = url.searchParams.get('after');
			const offset =
				after === null ? Number(url.searchParams.get('offset') ?? '0') : Number(after.slice(1)) + 1;
			// A missing offset is not 0, or an anchored request would pass as the first page.
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

	expect(asks.at(-1)?.sort).toBe('newest');

	/* A menu on the bar, so options in a listbox. */
	await page.getByRole('button', { name: 'Sort by' }).click();
	await page.getByRole('option', { name: 'Name A-Z', exact: true }).click();

	await expect.poll(() => asks.at(-1)?.sort).toBe('name_az');

	await page.reload();
	await expect(page.locator('.tile').first()).toBeVisible();
	expect(asks.at(-1)?.sort).toBe('name_az');
});

test('a library that fits on one page still says how much of it there is', async ({ page }) => {
	// The pager is always drawn, even below one page.
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
	// No page numbers: a page's size depends on the screen, so it reports a position.
	const asks = await library(page, 400);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	const pager = page.getByRole('navigation', { name: 'Pages' });
	await expect(pager).toBeVisible();
	await expect(pager.getByRole('button', { name: /^Page \d/ })).toHaveCount(0);

	expect(asks[0].offset).toBe(0);
	const readout = pager.getByRole('button', { name: 'Go to a position' });
	await expect(readout).toContainText('of 400');

	const before = await readout.innerText();
	await pager.getByRole('button', { name: 'Next page' }).click();
	await expect(readout).not.toHaveText(before);
	await expect.poll(() => asks.at(-1)?.offset).toBeGreaterThan(0);

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

	// A new sort starts at the top; read its first request, since later ones are top-ups.
	await expect.poll(() => asks.length).toBeGreaterThan(before);
	expect(asks[before]).toMatchObject({ offset: 0, sort: 'oldest', from: null });
});

test('a new search starts at the top, not where the last one was left', async ({ page }) => {
	// The address's anchor is honoured once on arrival, not by a later search.
	const asks = await library(page, 400);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	await page
		.getByRole('navigation', { name: 'Pages' })
		.getByRole('button', { name: 'Next page' })
		.click();
	await expect.poll(() => asks.at(-1)?.offset).toBeGreaterThan(0);
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).not.toBeNull();

	const before = asks.length;
	await page.goto('/browse?q=something-else');

	await expect.poll(() => asks.length).toBeGreaterThan(before);
	expect(asks[before]).toMatchObject({ offset: 0, from: null });
});
