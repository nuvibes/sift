import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { rewrite } from './routes';

/* The download manager, in a browser, against the real server.
 *
 * The page is three regions (the head with the Cookies door and the queue's Pause, the Add box,
 * the queue), the queue one list narrowed by a strip of state chips, and a row that offers the one fix its
 * state has.
 *
 * ## The word is Cookies
 *
 * Nothing on this page speaks of account logins, only cookies. That is a claim about what a
 * person READS, so a browser is the only place it can really be checked: a unit test sees one
 * component's strings and this sees the assembled page, the rail and the shell around it. The
 * last test in this file is that claim, made against the whole document.
 *
 * ## What a browser is here for
 *
 * That the screen is real rather than a placeholder; that the paste box is one control doing two
 * different jobs depending on what is in it, and sends the right one; and that the list refuses a
 * caller who is not signed in. Running a download end to end is not tested here (the job that
 * fetches hands its file to the shared import pipeline), so the rows below are SERVED to the
 * page rather than really queued: this is a test about what the screen draws and what it sends.
 */

/** One row of the queue, with every field the wire carries, so nothing arrives `undefined`.
 *
 * Spelled out rather than cast from a partial for the reason the unit fixtures are: a cast
 * compiles and then hands the row a missing field, which draws as a blank line rather than
 * failing as a missing field. */
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

/** Serve the queue a fixed list, so a state can be looked at without a fetcher ever running. */
async function serve(page: Page, downloads: Record<string, unknown>[]): Promise<void> {
	/* By path, not by glob: the list is read with its narrowing in the query string
	   (`?limit=50&offset=0&show=all&sort=newest`), and a glob ending at `downloads` matches none of
	   those addresses. */
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

/*
 * The fixture's Site, known to Sift.
 *
 * The rows above name `one-site`, and a screen that names a Site reads its name from the supported
 * list (`nameOf`) rather than trusting whatever a row carried, so a row from a Site Sift has never
 * heard of is drawn by its key. The real list is asked and kept whole; this Site is added to it, in
 * the wire's own shape.
 */
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

/*
 * Somewhere for a download to land.
 *
 * With no download folder set and no Site given one, the paste box asks where a download goes
 * before it sends anything (`Destinations.hasNowhereFor`) rather than let the server refuse it,
 * and this suite's library has no folders at all. The real answer is asked and kept; only the
 * default folder is filled in.
 */
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

	/* The REGION rather than the sentence. What has to be true is that a queue with nothing in
	   it still says something (a heading with nothing under it reads as a screen that failed
	   to load), and the words are the page's to choose, so asserting exact sentences would only
	   break when they are rewritten. */
	const said = await page.locator('main p, main li').allInnerTexts();
	expect(
		said.join(' ').trim().length,
		'the empty queue says nothing about being empty'
	).toBeGreaterThan(20);
});

test('it offers one box to paste links into, and the button counts what is in it', async ({
	page
}) => {
	/* The button carrying the number is the Add region's whole promise: a paste of forty is one act
	   with one press, and the press has to say how many it is about to take before it takes them.
	   One link is one download and the button does not count out loud for it. */
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
	/* The tabs every entity page and Organize draw, each a real address, and the counts are
	   the reason for them: "Needs you" is the one thing on this page somebody has to act on. It
	   gathers the row waiting for cookies AND the failed one, because a failure is waiting on
	   somebody to press Try again (`needsYou` in the queue store), so it counts two of these three. */
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

	// The counts, read off the tabs themselves rather than off the rows they narrow to.
	await expect(tabs.getByRole('link', { name: /Needs you/ })).toContainText('2');
	await expect(tabs.getByRole('link', { name: /All/ })).toContainText('3');

	// And the tab showing is the address, so a reload lands on it.
	await tabs.getByRole('link', { name: /Failed/ }).click();
	await expect(page).toHaveURL(/\/downloads\?show=failed$/);
});

test('every row stands its columns at the same x', async ({ page }) => {
	/* One grid of fixed tracks per row: what a row holds (a fix button, two glyphs or none, a
	   long name or a short one) cannot move where its neighbours' columns are. Read off the
	   drawn page, which is the only place a layout exists. The columns stand beside the name only
	   on a wide window (below `FACTS_UNDER_THE_NAME` they go on a line under it), so it is
	   measured at a width that draws them. */
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
	/* Every cell by the edge it is aligned on (a cell the list aligns to the end of its track
	   is as wide as what it holds, so its start moves with its words and its end does not), and
	   the edge each fact is aligned on: the status badge's start, and the end of the moment and
	   of the fix, which stand against the row's actions. */
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

/*
 * THE QUEUE REACHES THE RIGHT EDGE, like every other wall.
 *
 * What anybody can see on a row at rest reaches the edge the list does. The last thing on a row is
 * the arrow that opens it (`DataRow` draws it at the end of every row that folds, always visible),
 * with the figures in front of it. Held here as the measurement: at rest, each row's last visible
 * thing ends within one row's padding of the frame's content edge, and so do the tabs' line and the
 * search.
 */
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
	// The pointer is kept off the list, so the glyphs are hidden as they are at rest.
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
	// Within the row's own padding (12px) and a pixel of rounding.
	expect(edges.ends.length, JSON.stringify(edges)).toBe(2);
	for (const end of edges.ends) {
		expect(edges.content - end, JSON.stringify(edges)).toBeLessThanOrEqual(13);
	}
});

test('a row that is waiting says it is waiting for cookies, and offers to take them', async ({
	page
}) => {
	/* The one state on this page that a person can do something about.
	   The row says what is missing in the words the thing has (cookies), and the fix is on the
	   row rather than three screens away in Settings. */
	await signInAsAdmin(page);
	await knowOneSite(page);
	await serve(page, [row({ status: 'blocked', filename: 'later.mp4' })]);
	await page.goto('/downloads');

	await expect(page.getByText('Waiting for cookies')).toBeVisible();

	await page.getByRole('button', { name: 'Add cookies' }).click();

	const sheet = page.getByRole('dialog');
	await expect(sheet).toBeVisible();
	await expect(sheet.getByText('Cookies', { exact: true }).first()).toBeVisible();
	// Opened FROM a row, so it already knows which Site is asking.
	await expect(sheet).toContainText('One Site');
});

test('the Sites pane opens the same sheet, by its own door', async ({ page }) => {
	/* One component, three doors: the head of this page, a blocked row, and Settings. The
	   failure this is about is the third door being a second implementation of the same form,
	   with its own wording and its own read-back. */
	await signInAsAdmin(page);
	await page.goto('/settings/sites');

	await page.getByRole('button', { name: 'Cookies' }).first().click();

	/* By its name: Settings is itself a dialog, open behind the sheet, so an unnamed dialog is
	   two of them. */
	const sheet = page.getByRole('dialog', { name: 'Cookies' });
	await expect(sheet).toBeVisible();
	await expect(sheet.getByText('Cookies', { exact: true }).first()).toBeVisible();
});

test('nothing on the page calls it a login', async ({ page }) => {
	/* The word asserted where it can actually be broken: over the whole assembled document,
	   chips, rows, head and all. Every other check in this file names a string it expects to
	   find; this one is the only shape that catches the string nobody expected. */
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

/*
 * THE BULK PASTE BOX.
 *
 * It is one control doing two different things depending on what is in it, and the difference is
 * invisible: one line goes to `/downloads` and is one download; several lines go to
 * `/downloads/links`, which is one request that answers how many were taken and which were refused.
 * A loop over the single route would look identical on screen and would lose the other lines the
 * moment one of them was refused.
 */
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
	/*
	 * The trap is a silent double-queue: if the whole paste stayed in the box, where somebody goes
	 * to fix the one line that was refused, pressing Download again would queue every good line a
	 * second time with nothing on screen saying so.
	 */
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
