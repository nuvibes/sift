/* Arranging the sidebar, in a browser, and surviving a reload.
 *
 * None of this is answerable without one. The arrangement is read out of the browser's own storage
 * before the first frame is drawn, which is the whole reason it is stored there rather than on the
 * account, and "before the first frame" is a claim only a real page load can settle.
 */
import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

const ORDER = 'sift.rail.order';
const HIDDEN = 'sift.rail.hidden';

/** The destinations the sidebar is showing, top to bottom. */
const shown = (page: Page) =>
	page
		.locator('nav.rail a.item')
		.evaluateAll((links) => links.map((link) => link.getAttribute('href')));

/** The rail has drawn. Every read of it has to wait for this: the application mounts the whole
 *  shell in the browser, so a query fired the instant a navigation resolves finds an empty page. */
const drawn = (page: Page) => expect(page.locator('nav.rail a.item').first()).toBeVisible();

/*
 * The centre of a row, which is the one point on it a wiggle does not move.
 *
 * While the rail is being arranged its rows rotate a little, back and forth, about their own
 * centres. That is deliberate and it is what says the rail is in a mode, but it means the browser
 * never reports a row as having come to rest, and the ready-to-be-clicked check every convenient
 * helper runs first waits for exactly that. A rotation leaves the centre of the thing rotating
 * where it was, so a pointer driven straight to that point lands where a hand would.
 */
async function centre(page: Page, name: string): Promise<{ x: number; y: number }> {
	const box = (await page.getByRole('link', { name, exact: true }).boundingBox())!;
	return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/**
 * The rail is in its arranging mode and can be pressed.
 *
 * Rearrange is chosen from a menu, and a menu leaves a clear sheet over the window while it
 * animates out (`PageShield`), so a press in that moment closes nothing and drags nothing. A hand
 * is never that quick; a test is, so it waits for the sheet to go before it grips a row.
 */
async function arranging(page: Page): Promise<void> {
	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	await expect(page.locator('.page-shield')).toHaveCount(0);
}

/** Each test's copy of the account's arrangement, the one `ownRail` answers with. */
const accounts = new WeakMap<Page, Record<string, string | null>>();

/**
 * Put an arrangement on the account and in the browser before anything loads, the way a previous
 * visit would have left both. The account's copy is the truth and the browser's only a cache of
 * it, so planting the cache alone would be a browser out of step with its account, which the rail
 * corrects to the account's copy as soon as it answers.
 */
async function remember(page: Page, order: string | null, hidden: string | null): Promise<void> {
	Object.assign(accounts.get(page) ?? {}, { 'rail.order': order, 'rail.hidden': hidden });
	await page.addInitScript(
		([orderKey, hiddenKey, storedOrder, storedHidden]) => {
			if (storedOrder === null) localStorage.removeItem(orderKey as string);
			else localStorage.setItem(orderKey as string, storedOrder as string);
			if (storedHidden === null) localStorage.removeItem(hiddenKey as string);
			else localStorage.setItem(hiddenKey as string, storedHidden as string);
		},
		[ORDER, HIDDEN, order, hidden]
	);
}

/*
 * A rail this test has to itself.
 *
 * The arrangement follows the ACCOUNT, and every spec in this suite signs in as the same one. So a
 * test that moves a row leaves it moved for whichever test runs next, in parallel and equally one
 * after another: failures that move between runs and pass alone, which no amount of re-running
 * settles. Clearing the account first is worse: the clear is itself a write to the shared thing, so
 * a test starting up wipes the rail of one already running.
 *
 * Answered instead the way the rest of this file answers a shared server: the account's copy is
 * intercepted, and each test gets its own. Held in this closure rather than discarded, because two
 * of these tests are ABOUT the arrangement surviving a reload, and a stub that forgot on every
 * request would quietly turn those into tests of nothing. The browser's own copy needs no such
 * care: `localStorage` belongs to the context, and every test gets a fresh one.
 */
async function ownRail(page: Page): Promise<void> {
	const held: Record<string, string | null> = {};
	accounts.set(page, held);

	await page.route('**/api/settings/interface', async (route) => {
		const request = route.request();
		if (request.method() === 'PUT') {
			const sent = request.postDataJSON() as { state?: Record<string, string | null> };
			Object.assign(held, sent.state ?? {});
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ state: held })
		});
	});
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await ownRail(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('a row moved with the keyboard stays moved', async ({ page }) => {
	/* The keyboard path, not the drag, and deliberately: a reorder that only a mouse can perform is
	 * a feature half the ways of using this application do not have. */
	await page.goto('/browse');
	await drawn(page);

	const before = await shown(page);
	/* Where it starts is READ rather than written down here: a test that fails when the DEFAULT
	   changes is a test about the default, not about the keyboard. */
	const startedAt = before.indexOf('/collections');
	expect(startedAt, 'Collections is not on the shipped rail').toBeGreaterThan(0);

	await page.getByRole('link', { name: 'Collections', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();

	await page.getByRole('link', { name: 'Collections', exact: true }).focus();
	await page.keyboard.press('ArrowUp');

	// Up exactly one place, with everything else in the order it already was.
	const moved = [...before];
	moved.splice(startedAt, 1);
	moved.splice(startedAt - 1, 0, '/collections');

	await expect.poll(() => shown(page)).toEqual(moved);

	await page.reload();
	await drawn(page);

	expect(await shown(page)).toEqual(moved);
});

test('a row can cross the rule, because the rule is a position and not a wall', async ({
	page
}) => {
	// The ways of looking at a library sit above it and the places you go to below it, and that is
	// the arrangement Sift ships with rather than a boundary anybody has to respect.
	await remember(
		page,
		'browse,collections,people,sites,favorites,--,tags,downloads,hidden,settings,profile',
		null
	);
	await page.goto('/browse');

	const footer = page.locator('nav.rail .footer');
	await expect(footer.locator('a[href="/tags"]')).toBeVisible();
});

test('a row put away stays away, and comes back from Appearance', async ({ page }) => {
	await page.goto('/browse');

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Hide' }).click();

	await expect(page.locator('nav.rail a[href="/tags"]')).toHaveCount(0);

	await page.reload();
	await expect(page.locator('nav.rail a[href="/tags"]')).toHaveCount(0);

	// The only way back, which is why it exists: a row that is not on the sidebar cannot be pressed
	// on the sidebar.
	await page.goto('/settings/appearance');
	await page.getByRole('switch', { name: 'Show Tags in the sidebar' }).click();

	await expect(page.locator('nav.rail a[href="/tags"]')).toHaveCount(1);
});

test('Settings cannot be put away, because it is the way back', async ({ page }) => {
	await page.goto('/browse');

	await page.getByRole('link', { name: 'Settings', exact: true }).click({ button: 'right' });
	await expect(page.getByRole('menuitem', { name: 'Hide' })).toBeDisabled();
	await page.keyboard.press('Escape');

	await page.goto('/settings/appearance');
	// No switch for it at all: a control that removes the way back is not a control to offer.
	await expect(page.getByRole('switch', { name: 'Show Settings in the sidebar' })).toHaveCount(0);
});

test('reset puts back both the order and everything hidden', async ({ page }) => {
	/*
	 * What reset restores is read off a rail that has not been touched, rather than written out
	 * here. A second copy of the shipped arrangement would stop agreeing the day the rail gained a
	 * row, and fail while reset was working perfectly.
	 */
	await page.goto('/browse');
	await drawn(page);
	const shipped = await shown(page);

	await remember(
		page,
		'tags,browse,collections,people,sites,favorites,--,downloads,hidden,settings,profile',
		'favorites'
	);
	await page.goto('/settings/appearance');

	// The planted arrangement really is on screen, or reset would have nothing to undo and this
	// would pass on a rail that was never disarranged.
	await expect.poll(async () => (await shown(page))[0]).toBe('/tags');

	/* A row of Appearance: the name says what is reset, and the button is the verb. */
	await page
		.locator('.row')
		.filter({ hasText: 'Reset the sidebar' })
		.getByRole('button', { name: 'Reset the sidebar', exact: true })
		.click();

	await expect.poll(() => shown(page)).toEqual(shipped);
});

test('nothing on the sidebar can be dragged until rearranging is asked for', async ({ page }) => {
	/* The reason the mode exists. A browser drags an anchor by default, so a sidebar of plain links
	 * is a sidebar whose rows get picked up by a click with a little drift in it, on a control
	 * somebody presses dozens of times a day. */
	await page.goto('/browse');
	await drawn(page);

	const draggable = await page
		.locator('nav.rail a.item')
		.evaluateAll((links) => links.map((link) => link.getAttribute('draggable')));

	expect(draggable.length).toBeGreaterThan(0);
	expect(draggable.every((value) => value === 'false')).toBe(true);
});

test('the first painted sidebar is the stored one, never a default corrected afterwards', async ({
	page
}) => {
	/*
	 * The claim the whole storage decision rests on.
	 *
	 * An arrangement kept on the account arrives from the server a moment after the page does, so
	 * the sidebar would draw one way and jump to the other on every single load. Kept in the browser
	 * it is readable before the first frame, and this is the difference, measured rather than
	 * assumed: every arrangement this page ever painted is recorded, and there must be exactly one.
	 *
	 * A test that only checked the order after the page settled would pass just as happily on a
	 * sidebar that drew the default first and corrected itself, which is the exact bug.
	 */
	/*
	 * The arrangement to plant is built FROM the shipped one rather than written out beside it. A
	 * list that names fewer destinations than the rail has gets its missing rows put back by the
	 * store's repair, and the test would then look for an arrangement of the wrong length. Reading
	 * the rail first plants a real rearrangement of whatever Sift ships, and keeps testing the one
	 * thing this is about: that the first sidebar painted is already the right one.
	 */
	await page.goto('/browse');
	await drawn(page);

	const rowsIn = (selector: string) =>
		page.locator(selector).evaluateAll((rows) =>
			rows.map((row) => ({
				id: row.getAttribute('data-rail-row')!,
				href: row.getAttribute('href')!
			}))
		);

	const above = await rowsIn('nav.rail .group:not(.footer) a[data-rail-row]');
	const below = await rowsIn('nav.rail .footer a[data-rail-row]');
	expect(above.length, 'nothing above the rule to rearrange').toBeGreaterThan(1);

	// A real rearrangement: the last row of the top half brought to the front. Different from the
	// shipped order, which is what makes a sidebar that drew the default first visible here.
	const rotated = [above[above.length - 1], ...above.slice(0, -1)];
	const arrangement = [...rotated.map((row) => row.id), '--', ...below.map((row) => row.id)];
	const first = rotated[0];

	await remember(page, arrangement.join(','), null);

	// Installed before any of the application runs, so the very first sidebar it draws is seen.
	await page.addInitScript(() => {
		const painted: string[] = [];
		(window as unknown as { painted: string[] }).painted = painted;
		const record = () => {
			const rail = document.querySelector('nav.rail');
			if (!rail) return;
			const order = [...rail.querySelectorAll('a.item')]
				.map((link) => link.getAttribute('href'))
				.join(',');
			if (order && order !== painted[painted.length - 1]) painted.push(order);
		};
		new MutationObserver(record).observe(document, { childList: true, subtree: true });
	});

	await page.goto('/browse');
	await expect(page.locator(`nav.rail a[href="${first.href}"]`)).toBeVisible();

	const painted = await page.evaluate(() => (window as unknown as { painted: string[] }).painted);

	/* Only the complete arrangements are the question. A sidebar is built one row at a time and the
	 * observer sees it half-made, which is a partial list rather than a different order. What would
	 * be a flash is two DIFFERENT complete arrangements, the default and then the stored one. */
	const stored = [...rotated, ...below].map((row) => row.href).join(',');
	const drawnRows = above.length + below.length;
	const complete = painted.filter((order) => order.split(',').length === drawnRows);

	expect(complete.length).toBeGreaterThan(0);
	expect([...new Set(complete)]).toEqual([stored]);
});

test('Recently viewed put back at the bottom stays there, whatever an older copy in the browser says', async ({
	page
}) => {
	/*
	 * Put back where it ships, the rail is stored on the account as nothing at all. A window that
	 * still holds the arrangement from before (Recently viewed above Organize) must take the
	 * account's answer on its next load, and must not send its older copy up over it.
	 */
	await page.goto('/browse');
	await drawn(page);
	const shipped = await shown(page);
	expect(shipped.at(-1)).toBe('/recent');

	const older = shipped.filter((href) => href !== '/recent');
	older.splice(older.indexOf('/organize'), 0, '/recent');
	const ids = await page
		.locator('nav.rail a[data-rail-row]')
		.evaluateAll((rows) =>
			Object.fromEntries(
				rows.map((row) => [row.getAttribute('href'), row.getAttribute('data-rail-row')])
			)
		);
	const cached = older.map((href) => ids[href]);
	cached.splice(cached.indexOf('organize'), 0, '--');

	await page.addInitScript(
		([orderKey, order]) => localStorage.setItem(orderKey as string, order as string),
		[ORDER, cached.join(',')]
	);
	const sent: string[] = [];
	page.on('request', (request) => {
		if (request.url().endsWith('/api/settings/interface') && request.method() === 'PUT') {
			sent.push(request.postData() ?? '');
		}
	});

	await page.reload();
	await drawn(page);
	await expect.poll(() => shown(page)).toEqual(shipped);
	await page.reload();
	await drawn(page);
	await expect.poll(() => shown(page)).toEqual(shipped);
	expect(sent).toEqual([]);
});

test('holding a row down starts rearranging, and does not follow the link', async ({ page }) => {
	/* The gesture the mode is reached by, and the thing that must not happen while reaching it:
	 * a press long enough to mean "pick this up" ends in a click, and that click must not navigate.
	 * Somebody held the row to arrange it, not to go there. */
	await page.goto('/browse');
	await drawn(page);

	const row = page.getByRole('link', { name: 'Tags', exact: true });
	const box = (await row.boundingBox())!;

	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	// Longer than the hold, and the press is held still: a hold that only counts while the pointer
	// is moving would be a different gesture.
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	/* Still the same SCREEN, which is the claim, not the same address. The grid writes the row
	   it is opened at into the query once a page has landed, so `/browse` grows a `?from=` on
	   its own a moment after this test starts, and asserting the whole address would race that
	   write. */
	expect(new URL(page.url()).pathname).toBe('/browse');
});

test('and a row dragged onto another lands where it was dropped, and stays there', async ({
	page
}) => {
	// The gesture itself, end to end: enter the mode, move a row to the top, reload, still there.
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	const from = await centre(page, 'Tags');
	const target = (await page.getByRole('link', { name: 'Browse', exact: true }).boundingBox())!;

	// Onto the upper half of Browse, which is what puts it above rather than below.
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(target.x + target.width / 2, target.y + target.height * 0.25, {
		steps: 12
	});
	await page.mouse.up();

	await expect.poll(async () => (await shown(page))[0]).toBe('/tags');

	await page.reload();
	await drawn(page);
	expect((await shown(page))[0]).toBe('/tags');
});

test('picking a row up and putting it straight back leaves it where it was', async ({ page }) => {
	/* The gesture that means "never mind". A drop on the row it started from has to be consumed
	 * rather than ignored: the region underneath reads a drop on empty space as "put it at the end",
	 * so an ignored drop would travel down to it and move the row to the bottom of its half of the
	 * rail: the one arrangement nobody asked for. */
	await page.goto('/browse');
	await drawn(page);

	const before = await shown(page);

	await page.getByRole('link', { name: 'Browse', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	const from = await centre(page, 'Browse');
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	// A small wander that ends back inside the row it started in.
	await page.mouse.move(from.x, from.y + 6, { steps: 6 });
	await page.mouse.up();

	expect(await shown(page)).toEqual(before);
});

test('a row below the rule can be walked back above it, press after press', async ({ page }) => {
	/*
	 * Crossing the rule with the keyboard, more than once.
	 *
	 * The two halves of the rail are two separate loops, so a row that crosses is destroyed on one
	 * side and built again on the other. The element the handler was called on is left detached, and
	 * focusing a detached element focuses the body instead. The first press would work and every press
	 * after it go nowhere, which reads as a row that cannot come back above the rule at all. So this
	 * presses repeatedly and asserts the row keeps travelling.
	 */
	await page.goto('/browse');
	await drawn(page);

	// Settings ships below the rule and cannot be put away, so it is always there to move.
	await page.getByRole('link', { name: 'Settings', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await page.getByRole('link', { name: 'Settings', exact: true }).focus();

	const startedAt = (await shown(page)).indexOf('/settings');

	for (let press = 0; press < 3; press += 1) await page.keyboard.press('ArrowUp');

	const landedAt = (await shown(page)).indexOf('/settings');
	expect(landedAt, 'the row stopped travelling after the first press').toBeLessThan(startedAt - 1);

	// And the focus went with it, or the next press would go nowhere.
	await expect(page.getByRole('link', { name: 'Settings', exact: true })).toBeFocused();
});

test('the empty space above the rule takes a drop', async ({ page }) => {
	/*
	 * The gap between the last row of the top half and the rule belongs to a drop zone. A margin is
	 * not part of any element's hit area, so a drop there would land on the rail, which takes no
	 * drop.
	 *
	 * The window is made taller than the default on purpose: at 1280x720 the destinations above the
	 * rule fill the half and leave no empty space in it, and this test is about what happens in
	 * that space.
	 */
	await page.setViewportSize({ width: 1400, height: 1024 });
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Settings', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	/* A point inside the top half, below every row in it. That the point EXISTS is half of what is
	   being tested: the group has to reach past its last row, or there is no blank space in it to
	   aim at and the gesture has nowhere to happen. */
	const group = (await page.locator('nav.rail .group:not(.footer)').boundingBox())!;
	const rows = page.locator('nav.rail .group:not(.footer) a.item');
	const last = (await rows.last().boundingBox())!;
	const blank = group.y + group.height - (last.y + last.height);
	expect(blank, 'the top half does not reach past its last row').toBeGreaterThan(20);

	const gap = { x: group.x + group.width / 2, y: last.y + last.height + blank / 2 };

	const from = await centre(page, 'Settings');
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(gap.x, gap.y, { steps: 10 });
	await page.mouse.up();

	// Above the rule now, which is what the top half means.
	const order = await shown(page);
	const settingsAt = order.indexOf('/settings');
	const dividerAt = await page
		.locator('nav.rail .group:not(.footer) a.item')
		.evaluateAll((links) => links.length);
	expect(settingsAt, 'settings did not come back above the rule').toBeLessThan(dividerAt);
});

/*
 * The three landings, each dropped into and then WOBBLED before letting go.
 *
 * The wobble is the whole test. Every landing moves rows, and moving rows moves the zone the
 * pointer is standing in: the band worst of all, because a row crossing the rule makes the bottom
 * half a row taller and drags the band about forty pixels up the rail. A small tremor after a
 * correct landing must not be read as a new instruction. A test that let go without moving could
 * not see it.
 */
/*
 * Twelve pixels each way, and the size is the point: further than any travel threshold a landing
 * guard might use (a two-pixel wobble can slide under one while a real hand does not), and still
 * inside a forty-pixel band, so a drop that survives this survives a person.
 */
const WOBBLE = 12;

/** The rows in the bottom half, which is what "below the rule" means. */
const inFooter = (page: Page) =>
	page
		.locator('nav.rail .footer a.item')
		.evaluateAll((links) => links.map((link) => link.getAttribute('href')));

async function startArranging(page: Page, row: string): Promise<void> {
	await page.getByRole('link', { name: row, exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);
}

/** Pick `row` up, carry it to a point, tremble, and let go. */
async function dragTo(page: Page, row: string, to: { x: number; y: number }): Promise<void> {
	const from = await centre(page, row);
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(to.x, to.y, { steps: 12 });
	// The rail reflows in answer to the landing. Long enough for it to finish moving underneath.
	await page.waitForTimeout(400);
	await page.mouse.move(to.x, to.y + WOBBLE, { steps: 2 });
	await page.waitForTimeout(200);
	await page.mouse.up();
}

test('a row dropped in the band between the halves stays just below the rule', async ({ page }) => {
	await page.goto('/browse');
	await drawn(page);

	await startArranging(page, 'Tags');

	const band = (await page.locator('nav.rail .zone.between').boundingBox())!;
	await dragTo(page, 'Tags', { x: band.x + band.width / 2, y: band.y + band.height / 2 });

	// FIRST below the rule, which is the one thing the band means. Landing anywhere else below it
	// (the end of the bottom half in particular) is the failure this covers.
	await expect.poll(async () => (await inFooter(page))[0]).toBe('/tags');

	await page.reload();
	await drawn(page);
	expect((await inFooter(page))[0]).toBe('/tags');
});

test('the drop zones can be reached by a pointer even while the page is inert', async ({
	page
}) => {
	/*
	 * The drop zones must be hit-testable while arranging.
	 *
	 * A menu here is modal: while one is open it puts `pointer-events: none` on `<body>`, which is
	 * how a modal stops the page behind it being clicked. Rearranging is reached THROUGH that menu,
	 * so for a window after choosing it the page is still inert, and the menu re-enables its own
	 * triggers, which every rail row is. So the rows stay live while the drop zones, plain divs,
	 * would not be: `elementFromPoint` in the middle of the band would return the document, and a
	 * row dropped there would go back where it came from.
	 *
	 * Asserted as REACHABILITY rather than by dragging, because that is the property that matters
	 * and a hit test cannot flake. The drags either side of this cover the gesture.
	 *
	 * Taller than the default window, for the reason the blank-space test above gives.
	 */
	await page.setViewportSize({ width: 1400, height: 1024 });
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	/* The menu's clear sheet stands over the window while the menu animates out, and it is a DIV
	   too, so it is waited out first; what may NOT linger past it is the page's own
	   `pointer-events: none`, which is what this reads. */
	await expect(page.locator('.page-shield')).toHaveCount(0);

	const reach = await page.evaluate(() => {
		const band = document.querySelector('nav.rail .zone.between')!;
		const rows = [...document.querySelectorAll('nav.rail .group:not(.footer) a.item')];
		const box = band.getBoundingClientRect();
		const last = rows[rows.length - 1].getBoundingClientRect();
		const at = (x: number, y: number) => document.elementFromPoint(x, y)?.tagName ?? 'NOTHING';
		return {
			band: at(box.left + box.width / 2, box.top + box.height / 2),
			blank: at(box.left + box.width / 2, (last.bottom + box.top) / 2)
		};
	});

	// DIV either way: the band itself, and the half it sits under. The document would be what
	// "nothing is there to drop onto" looks like from here.
	expect(reach.band, 'the band is not hit-testable').toBe('DIV');
	expect(reach.blank, 'the empty space above the band is not hit-testable').toBe('DIV');
});

test('a drop in the gap BETWEEN two zones still lands somewhere', async ({ page }) => {
	/*
	 * Every pixel of the rail belongs to a zone.
	 *
	 * If the zones each answered only for themselves, the space between them (the band's own
	 * margins, the few pixels either side of the rule) would belong to nothing: a `dragover`
	 * there reaches the rail, which takes no drop, so no `drop` event is delivered. Because a
	 * landing waits for the drop, the row would spring back to where it started.
	 *
	 * Aimed two pixels above the band's top edge, inside its margin. What it lands on matters less
	 * than that it lands.
	 */
	await page.goto('/browse');
	await drawn(page);

	const before = await shown(page);
	await startArranging(page, 'Tags');

	const band = (await page.locator('nav.rail .zone.between').boundingBox())!;
	await dragTo(page, 'Tags', { x: band.x + band.width / 2, y: band.y - 2 });

	const after = await shown(page);
	expect(after, 'the drop did nothing at all').not.toEqual(before);
});

test('a row dropped in the empty space of a half stays at the end of that half', async ({
	page
}) => {
	/* The same failure in the other direction: a row arriving at the end of the bottom half makes
	   that half taller, which moves the band and the space above it under a still hand. */
	await page.goto('/browse');
	await drawn(page);

	await startArranging(page, 'Browse');

	const footer = (await page.locator('nav.rail .footer').boundingBox())!;
	const rows = page.locator('nav.rail .footer a.item');
	const last = (await rows.last().boundingBox())!;
	const blank = footer.y + footer.height - (last.y + last.height);

	await dragTo(page, 'Browse', {
		x: footer.x + footer.width / 2,
		y: last.y + last.height + Math.min(blank / 2, 24)
	});

	const below = await inFooter(page);
	expect(below[below.length - 1]).toBe('/browse');
});

test('the drop zones do not change size while a row is in the air', async ({ page }) => {
	/*
	 * The measurement behind the rule. A zone that grows under the pointer moves the target the hand
	 * is already aiming at, on a rail whose halves are shifting anyway. It may light up however it
	 * likes; it may not take up more room.
	 */
	await page.goto('/browse');
	await drawn(page);

	/*
	 * Carried by the row that is ALREADY first below the rule, and that is what makes this readable.
	 *
	 * Landing it there again changes no arrangement, so nothing reflows and the band stays exactly
	 * where the pointer found it, which leaves the size the only thing that could have moved. Any
	 * other row would land somewhere real, shift the halves, and take the band out from under the
	 * pointer, so a size measured afterwards would be a measurement of the wrong moment.
	 */
	const below = await inFooter(page);
	const firstBelow = below[0];
	const label = await page.locator(`nav.rail a[href="${firstBelow}"] .label`).innerText();

	await startArranging(page, label);

	const band = page.locator('nav.rail .zone.between');
	const resting = (await band.boundingBox())!;

	const from = await centre(page, label);
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(resting.x + resting.width / 2, resting.y + resting.height / 2, {
		steps: 12
	});

	// Lit up, so this is a live drag over a live zone rather than a still rail measured twice.
	await expect(band).toHaveClass(/over/);

	// And exactly the size it was at rest. The strip may change colour however it likes; it may not
	// take up more room, because the room it takes is where the hand is already pointing.
	const lit = (await band.boundingBox())!;
	expect(lit.height).toBe(resting.height);
	expect(lit.y).toBe(resting.y);

	await page.mouse.up();
});

test('the rows move while a row is being dragged, not when it is let go', async ({ page }) => {
	/*
	 * The arrangement follows the pointer DURING the drag, so a long drag is not done blind.
	 *
	 * What is asserted is that the arrangement has already changed while the button is still down,
	 * not that the row reached a particular place. A native drag does not deliver a `dragover`
	 * per step of a synthetic move (Chromium throttles them), so where a row gets to mid-drag
	 * depends on where two or three events happen to fall. Where a row ENDS UP is the drop test
	 * above, which lets go and then asserts. This one is about the difference between during and
	 * after.
	 */
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	const before = await shown(page);
	const started = before.indexOf('/tags');
	expect(started, 'Tags is not on the rail to drag').toBeGreaterThan(0);

	const from = await centre(page, 'Tags');
	const target = (await page.getByRole('link', { name: 'Browse', exact: true }).boundingBox())!;

	// A tenth of the way down rather than a quarter: a quarter is exactly where the row's dead band
	// ends, and the rows wiggle while the rail is being arranged, so that box is a rotated one
	// whose height changes as it turns, and aiming at the boundary asks which way it was leaning.
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(target.x + target.width / 2, target.y + target.height * 0.1, {
		steps: 12
	});

	// Still holding it. The arrangement has already followed the pointer up the rail.
	await expect
		.poll(async () => (await shown(page)).indexOf('/tags'), {
			message: 'the rail did not move until the row was let go'
		})
		.toBeLessThan(started);

	await page.mouse.up();
});

test('the sidebar can be rearranged from Appearance as well', async ({ page }) => {
	/*
	 * The screen that lists every row, including the ones put away.
	 *
	 * Rearranging on the sidebar itself cannot reach a row that has been hidden: it is not there to
	 * be dragged. This screen can see the whole order, so it is the one place the arrangement is
	 * completely editable, and it draws the rule as a row so a destination can be moved between the
	 * two halves from here too.
	 */
	await page.goto('/settings/appearance');
	await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();

	const order = () =>
		page
			.locator('.rows li')
			.evaluateAll((rows) => rows.map((row) => row.querySelector('.what')?.textContent?.trim()));

	const before = await order();
	const second = before[1];
	expect(second, 'not enough rows to rearrange').toBeTruthy();

	// Alt with an arrow is the keyboard's version of dragging the handle.
	await page.locator('.rows li').nth(1).locator('.handle').focus();
	await page.keyboard.press('Alt+ArrowUp');

	await expect.poll(async () => (await order())[0]).toBe(second);

	// Remembered by the browser, so it survives a reload.
	await page.reload();
	await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();
	expect((await order())[0]).toBe(second);
});
