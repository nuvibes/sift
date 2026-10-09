import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { rewrite } from './routes';

/*
 * The download manager against the real server: the screen is real, the paste box sends one link
 * or many the right way, and the word is Cookies across the whole assembled page. The rows are
 * SERVED rather than queued: this is about what the screen draws and sends.
 */

/** One row of the queue with every field the wire carries: a cast draws a missing field blank. */
function row(over: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: 'one',
		status: 'done',
		url: 'https://one-site.test/clip',
		shown_url: 'one-site.test/clip',
		filename: 'clip.mp4',
		remembered_filename: null,
		site: 'One Site',
		site_key: 'one-site',
		site_name: 'One Site',
		site_id: null,
		username: null,
		creator_scope: null,
		person_id: null,
		asset_id: null,
		dest_folder_id: null,
		error: null,
		error_code: null,
		error_tier: null,
		sentence: null,
		via: null,
		size_bytes: null,
		progress: null,
		created_at: 0,
		finished_at: null,
		...over
	};
}

/** Serve the queue a fixed list. */
async function serve(page: Page, downloads: Record<string, unknown>[]): Promise<void> {
	/* By path: the list carries its narrowing in the query string. */
	await page.route(
		(url) => url.pathname === '/api/downloads',
		async (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			await route.fulfill({
				json: {
					downloads,
					total: downloads.length,
					summary: { running: 0, queued: 0, bytes_per_second: 0, seconds_left: null }
				}
			});
		}
	);
}

/* The fixture's Site, added to the real supported list: an unknown Site is drawn by its key. */
async function knowOneSite(page: Page): Promise<void> {
	await page.route('**/api/supported-sites', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		await rewrite<Record<string, unknown>[]>(route, (sites) => {
			sites.push({
				bulk: false,
				cookies: 'required',
				cookies_why: 'It shows its files only to a signed-in browser.',
				cookies_with_a_tool: null,
				default_naming: '{title}',
				hosts: ['one-site.test'],
				key: 'one-site',
				media: ['video'],
				name: 'One Site',
				name_words: ['one', 'site'],
				names_creators: false,
				supported: true,
				tested: false,
				walls: []
			});
		});
	});
}

/* A default download folder, or the paste box asks where to put it first
 * (`Destinations.hasNowhereFor`). */
async function aDownloadFolder(page: Page): Promise<void> {
	await page.route('**/api/site-options', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		await rewrite<{ default: Record<string, unknown> }>(route, (options) => {
			options.default.dest_folder_id = 'downloads-folder';
		});
	});
}

test('the download manager is a screen, and an empty one says so', async ({ page }) => {
	await signInAsAdmin(page);
	await serve(page, []);
	await page.goto('/downloads');

	await expect(page.getByRole('heading', { name: 'Downloads' })).toBeVisible();

	/* The region, not the sentence: an empty queue still says something. */
	const said = await page.locator('main p, main li').allInnerTexts();
	expect(
		said.join(' ').trim().length,
		'the empty queue says nothing about being empty'
	).toBeGreaterThan(20);
});

test('it offers one box to paste links into, and the button counts what is in it', async ({
	page
}) => {
	/* The button counts what it is about to take; one link is not counted out loud. */
	await signInAsAdmin(page);
	await serve(page, []);
	await page.goto('/downloads');

	const box = page.getByLabel('Paste a link');
	await expect(box).toBeVisible();
	await expect(page.getByRole('button', { name: /^Download$/ })).toBeVisible();

	await box.fill('https://one-site.test/a\nhttps://one-site.test/b');
	await expect(page.getByRole('button', { name: /^Download 2$/ })).toBeVisible();
});

test('the queue is narrowed by state tabs that say how many are in each state', async ({
	page
}) => {
	/* "Needs you" gathers the cookies row AND the failed one (`needsYou`): two of three. */
	await signInAsAdmin(page);
	await serve(page, [
		row(),
		row({ id: 'two', status: 'blocked', filename: 'later.mp4' }),
		row({ id: 'three', status: 'failed', filename: 'gone.mp4', sentence: 'That link is dead.' })
	]);
	await page.goto('/downloads');

	const tabs = page.getByRole('navigation', { name: 'Which downloads to show' });
	for (const label of ['All', 'Active', 'Needs you', 'Done', 'Failed']) {
		await expect(tabs.getByRole('link', { name: new RegExp(label) })).toBeVisible();
	}

	// Read off the tabs themselves.
	await expect(tabs.getByRole('link', { name: /Needs you/ })).toContainText('2');
	await expect(tabs.getByRole('link', { name: /All/ })).toContainText('3');

	// The tab is the address, so a reload lands on it.
	await tabs.getByRole('link', { name: /Failed/ }).click();
	await expect(page).toHaveURL(/\/downloads\?show=failed$/);
});

test('every row stands its columns at the same x', async ({ page }) => {
	/* Fixed tracks per row, so a row's contents cannot move its neighbours' columns. Wide enough
	   that the columns stand beside the name (`FACTS_UNDER_THE_NAME`). */
	await page.setViewportSize({ width: 1600, height: 900 });
	await signInAsAdmin(page);
	await serve(page, [
		row(),
		row({ id: 'two', status: 'blocked', filename: 'a-much-longer-name-for-a-file.mp4' }),
		row({ id: 'three', status: 'failed', filename: 'gone.mp4', sentence: 'That link is dead.' }),
		row({ id: 'four', status: 'queued', filename: 'next.mp4' })
	]);
	await page.goto('/downloads');

	const list = page.getByRole('list', { name: 'Downloads' });
	await expect(list.locator('li')).toHaveCount(4);
	/* Every cell by the edge it is aligned on. */
	const columns = await list.evaluate((ul) =>
		[...ul.querySelectorAll(':scope > li')].map((li) => {
			const box = (selector: string) => li.querySelector(selector)?.getBoundingClientRect();
			const tracks = [...li.querySelectorAll('.row.columned > .cell')].map((cell) => {
				const { left, right } = cell.getBoundingClientRect();
				return cell.classList.contains('end') ? `end ${Math.round(right)}` : Math.round(left);
			});
			return {
				tracks,
				status: Math.round(box('.status')?.left ?? -1),
				when: Math.round(box('.when')?.right ?? -1),
				fix: Math.round(box('.fix')?.right ?? -1)
			};
		})
	);
	expect(columns[0].tracks.length, 'the rows declared no columns').toBeGreaterThan(3);
	for (const one of columns) expect(one).toEqual(columns[0]);
});

/* The queue reaches the right edge like every wall: each row's last visible thing at rest (the
 * fold arrow, `DataRow`), the tabs' line and the search end within a row's padding of it. */
test('the rows, the tabs and the search reach the frame right gutter', async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
	await serve(page, [
		row(),
		row({ id: 'two', status: 'failed', filename: 'gone.mp4', sentence: 'That link is dead.' })
	]);
	await page.goto('/downloads');
	const list = page.getByRole('list', { name: 'Downloads' });
	await expect(list.locator('li')).toHaveCount(2);
	// The glyphs hidden, as at rest.
	await page.mouse.move(0, 0);

	const edges = await page.evaluate(() => {
		const right = (element: Element | null) =>
			Math.round(element?.getBoundingClientRect().right ?? -1);
		const inner = document.querySelector('.frame-body-inner');
		const pad = inner ? parseFloat(getComputedStyle(inner).paddingRight) : 0;
		return {
			content: right(inner) - Math.round(pad),
			list: right(document.querySelector('ul[aria-label="Downloads"]')),
			ends: [...document.querySelectorAll('ul[aria-label="Downloads"] > li')].map((row) =>
				right(row.querySelector('.disclose') ?? row.querySelector('.figures'))
			),
			tabs: right(document.querySelector('.narrowing')),
			search: right(document.querySelector('.narrowing .find'))
		};
	});
	expect(edges.list, JSON.stringify(edges)).toBe(edges.content);
	expect(edges.tabs, JSON.stringify(edges)).toBe(edges.content);
	expect(edges.search, JSON.stringify(edges)).toBe(edges.content);
	// Within the row's padding (12px) and a pixel.
	expect(edges.ends.length, JSON.stringify(edges)).toBe(2);
	for (const end of edges.ends) {
		expect(edges.content - end, JSON.stringify(edges)).toBeLessThanOrEqual(13);
	}
});

test('a row that is waiting says it is waiting for cookies, and offers to take them', async ({
	page
}) => {
	/* The fix is on the row, in the thing's own word (cookies). */
	await signInAsAdmin(page);
	await knowOneSite(page);
	await serve(page, [row({ status: 'blocked', filename: 'later.mp4' })]);
	await page.goto('/downloads');

	await expect(page.getByText('Waiting for cookies')).toBeVisible();

	await page.getByRole('button', { name: 'Add cookies' }).click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();
	await expect(sheet.getByText('Cookies', { exact: true }).first()).toBeVisible();
	// Opened from a row, so it knows which Site.
	await expect(sheet).toContainText('One Site');
});

test('the Sites pane opens the same sheet, by its own door', async ({ page }) => {
	/* One component behind three doors, not a second form in Settings. */
	await signInAsAdmin(page);
	await page.goto('/settings/sites');

	await page.getByRole('button', { name: 'Cookies' }).first().click();

	/* By name: Settings is a dialog too, open behind it. */
	const sheet = page.getByRole('dialog', { name: 'Cookies' });
	await expect(sheet).toBeVisible();
	await expect(sheet.getByText('Cookies', { exact: true }).first()).toBeVisible();
});

test('nothing on the page calls it a login', async ({ page }) => {
	/* Over the whole document: the only shape that catches a string nobody expected. */
	await signInAsAdmin(page);
	await serve(page, [
		row(),
		row({ id: 'two', status: 'blocked', filename: 'later.mp4' }),
		row({ id: 'three', status: 'duplicate', filename: 'again.mp4' })
	]);
	await page.goto('/downloads');
	await expect(page.getByRole('heading', { name: 'Downloads' })).toBeVisible();

	const written = await page.locator('body').innerText();
	expect(written, 'the page still says login somewhere').not.toMatch(/\blogins?\b/i);
});

test.describe('who is turned away', () => {
	test('somebody signed out gets nothing from the list', async ({ request }) => {
		const listed = await request.get('/api/downloads');
		expect(listed.status()).toBe(401);
	});

	test('and cannot read the saved cookies either', async ({ request }) => {
		const connections = await request.get('/api/site-connections');
		expect(connections.status()).toBe(401);
	});
});

/* One line goes to `/downloads`; several go to `/downloads/links` as one request that says which
 * were refused. A loop would lose the rest the moment one was refused. */
test('several links pasted together go as one request, not one each', async ({ page }) => {
	await signInAsAdmin(page);
	await aDownloadFolder(page);

	const many: string[][] = [];
	const single: string[] = [];
	await page.route('**/api/downloads/links', async (route) => {
		many.push((route.request().postDataJSON() as { urls: string[] }).urls);
		await route.fulfill({ json: { queued: 3, duplicates: 0, refused: [], left_over: [] } });
	});
	await page.route('**/api/downloads', async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		single.push((route.request().postDataJSON() as { url: string }).url);
		await route.fulfill({ json: {} });
	});
	await serve(page, []);

	await page.goto('/downloads');
	await page
		.getByLabel('Paste a link')
		.fill('https://one-site.test/one\nhttps://one-site.test/two\nhttps://one-site.test/three');
	await page.getByRole('button', { name: /^Download 3$/ }).click();

	await expect.poll(() => many.length).toBe(1);
	expect(many[0]).toEqual([
		'https://one-site.test/one',
		'https://one-site.test/two',
		'https://one-site.test/three'
	]);
	expect(single, 'a paste went round the one-at-a-time route').toEqual([]);
});

test('what is left in the box after a paste is only what still needs doing', async ({ page }) => {
	/* Only the refused line stays in the box, or pressing again would queue the good ones twice. */
	await signInAsAdmin(page);
	await aDownloadFolder(page);

	await page.route('**/api/downloads/links', (route) =>
		route.fulfill({
			json: {
				queued: 1,
				duplicates: 0,
				refused: [{ url: 'not-a-link', reason: 'That is not an address Sift can read.' }],
				left_over: ['https://one-site.test/later']
			}
		})
	);
	await serve(page, []);

	await page.goto('/downloads');
	const box = page.getByLabel('Paste a link');
	await box.fill('https://one-site.test/taken\nnot-a-link\nhttps://one-site.test/later');
	await page.getByRole('button', { name: /^Download 3$/ }).click();

	await expect.poll(() => box.inputValue()).toBe('not-a-link\nhttps://one-site.test/later');
});
