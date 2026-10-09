/* Arranging the sidebar and surviving a reload: the arrangement is read from browser storage
 * before the first frame, which only a real page load can show. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { settled } from './settled';

const ORDER = 'sift.rail.order';
const HIDDEN = 'sift.rail.hidden';

const shown = (page: Page) =>
	page
		.locator('nav.rail a.item')
		.evaluateAll((links) => links.map((link) => link.getAttribute('href') ?? ''));

/** The rail has drawn: the shell mounts in the browser, so an early query finds nothing. */
const drawn = (page: Page) => expect(page.locator('nav.rail a.item').first()).toBeVisible();

/* A row's centre: arranging rows wiggle about it, so they never report themselves at rest. */
async function centre(page: Page, name: string): Promise<{ x: number; y: number }> {
	const box = (await page.getByRole('link', { name, exact: true }).boundingBox())!;
	return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Arranging, with the menu's closing `PageShield` gone so a press reaches the rail. */
async function arranging(page: Page): Promise<void> {
	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	await expect(page.locator('.page-shield')).toHaveCount(0);
}

/** Each test's copy of the account's arrangement, the one `ownRail` answers with. */
const accounts = new WeakMap<Page, Record<string, string | null>>();

/** Plant an arrangement on the account and in the browser, the account's copy being the truth. */
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

/* Each test answers the account's arrangement itself: every spec shares one account, so a
 * real write leaks into the next test. Kept across requests for the reload tests. */
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
	// The keyboard path: a reorder only a mouse can do is half a feature.
	await page.goto('/browse');
	await drawn(page);

	const before = await shown(page);
	// Read, not written down, so a change to the default does not fail this.
	const startedAt = before.indexOf('/collections');
	expect(startedAt, 'Collections is not on the shipped rail').toBeGreaterThan(0);

	await page.getByRole('link', { name: 'Collections', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();

	await page.getByRole('link', { name: 'Collections', exact: true }).focus();
	await page.keyboard.press('ArrowUp');

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

	// The only way back for a row that is off the sidebar.
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
	// No switch: a control that removes the way back is not offered.
	await expect(page.getByRole('switch', { name: 'Show Settings in the sidebar' })).toHaveCount(0);
});

test('reset puts back both the order and everything hidden', async ({ page }) => {
	// Read off an untouched rail, so a new row does not fail a working reset.
	await page.goto('/browse');
	await drawn(page);
	const shipped = await shown(page);

	await remember(
		page,
		'tags,browse,collections,people,sites,favorites,--,downloads,hidden,settings,profile',
		'favorites'
	);
	await page.goto('/settings/appearance');

	// Known positive: the planted arrangement is on screen.
	await expect.poll(async () => (await shown(page))[0]).toBe('/tags');

	await page
		.locator('.row')
		.filter({ hasText: 'Reset the sidebar' })
		.getByRole('button', { name: 'Reset the sidebar', exact: true })
		.click();

	await expect.poll(() => shown(page)).toEqual(shipped);
});

test('nothing on the sidebar can be dragged until rearranging is asked for', async ({ page }) => {
	// A browser drags an anchor by default; plain links get picked up by a drifting click.
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
	/* Exactly one arrangement is ever painted: stored in the browser, it is ready before the first
	 * frame. Built from the shipped rail, so the store's repair leaves it whole. */
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

	// A real rearrangement, different from the shipped order.
	const rotated = [above[above.length - 1], ...above.slice(0, -1)];
	const arrangement = [...rotated.map((row) => row.id), '--', ...below.map((row) => row.id)];
	const first = rotated[0];

	await remember(page, arrangement.join(','), null);

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

	// Only complete arrangements count: the rail is built one row at a time.
	const stored = [...rotated, ...below].map((row) => row.href).join(',');
	const drawnRows = above.length + below.length;
	const complete = painted.filter((order) => order.split(',').length === drawnRows);

	expect(complete.length).toBeGreaterThan(0);
	expect([...new Set(complete)]).toEqual([stored]);
});

test('Recently viewed put back at the bottom stays there, whatever an older copy in the browser says', async ({
	page
}) => {
	// In the shipped order the account stores nothing, and an older window must not override that.
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
	// A long press to arrange ends in a click, and it must not navigate.
	await page.goto('/browse');
	await drawn(page);

	const row = page.getByRole('link', { name: 'Tags', exact: true });
	const box = (await row.boundingBox())!;

	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.waitForTimeout(700);
	await page.mouse.up();

	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	// The path only: the grid writes `?from=` into the query on its own.
	expect(new URL(page.url()).pathname).toBe('/browse');
});

test('and a row dragged onto another lands where it was dropped, and stays there', async ({
	page
}) => {
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	const from = await centre(page, 'Tags');
	const target = (await page.getByRole('link', { name: 'Browse', exact: true }).boundingBox())!;

	// The upper half of Browse puts it above, not below.
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
	// A drop on its own row is consumed, or the region below would move the row to the end.
	await page.goto('/browse');
	await drawn(page);

	const before = await shown(page);

	await page.getByRole('link', { name: 'Browse', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	const from = await centre(page, 'Browse');
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(from.x, from.y + 6, { steps: 6 });
	await page.mouse.up();

	expect(await shown(page)).toEqual(before);
});

test('a row below the rule can be walked back above it, press after press', async ({ page }) => {
	// Pressed again and again: a row that crosses the rule is rebuilt, and focus must follow it.
	await page.goto('/browse');
	await drawn(page);

	// Settings ships below the rule and cannot be put away.
	await page.getByRole('link', { name: 'Settings', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await page.getByRole('link', { name: 'Settings', exact: true }).focus();

	const startedAt = (await shown(page)).indexOf('/settings');

	for (let press = 0; press < 3; press += 1) await page.keyboard.press('ArrowUp');

	const landedAt = (await shown(page)).indexOf('/settings');
	expect(landedAt, 'the row stopped travelling after the first press').toBeLessThan(startedAt - 1);

	await expect(page.getByRole('link', { name: 'Settings', exact: true })).toBeFocused();
});

test('the empty space above the rule takes a drop', async ({ page }) => {
	// The gap under the top half is a drop zone; the taller window leaves blank space there.
	await page.setViewportSize({ width: 1400, height: 1024 });
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Settings', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await arranging(page);

	// The group reaches past its last row, or there is no blank space to aim at.
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

	const order = await shown(page);
	const settingsAt = order.indexOf('/settings');
	const dividerAt = await page
		.locator('nav.rail .group:not(.footer) a.item')
		.evaluateAll((links) => links.length);
	expect(settingsAt, 'settings did not come back above the rule').toBeLessThan(dividerAt);
});

/* Each landing is wobbled 12px before letting go: past any travel threshold, inside a
 * forty-pixel band, and moving rows move the zone under the pointer. */
const WOBBLE = 12;

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
	// Long enough for the rail to finish reflowing.
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

	// First below the rule, never the end of the bottom half.
	await expect.poll(async () => (await inFooter(page))[0]).toBe('/tags');

	await page.reload();
	await drawn(page);
	expect((await inFooter(page))[0]).toBe('/tags');
});

test('the drop zones can be reached by a pointer even while the page is inert', async ({
	page
}) => {
	/* The drop zones stay hit-testable: the menu that starts arranging leaves `pointer-events:
	 * none` on the body for a moment, and the zones are plain divs. */
	await page.setViewportSize({ width: 1400, height: 1024 });
	await page.goto('/browse');
	await drawn(page);

	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await expect(page.locator('nav.rail .put-away').first()).toBeVisible();
	// The menu's clear sheet is waited out; the body's `pointer-events: none` may not linger.
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

	expect(reach.band, 'the band is not hit-testable').toBe('DIV');
	expect(reach.blank, 'the empty space above the band is not hit-testable').toBe('DIV');
});

test('a drop in the gap BETWEEN two zones still lands somewhere', async ({ page }) => {
	// The gaps between zones belong to a zone, or a drop there springs back.
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
	// A row landing at the end of the bottom half moves the band under a still hand.
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
	// A lit zone may change colour, never its size: the hand is already aiming at it.
	await page.goto('/browse');
	await drawn(page);

	// The row already first below the rule, so landing it reflows nothing.
	const below = await inFooter(page);
	const firstBelow = below[0];
	const label = await page.locator(`nav.rail a[href="${firstBelow}"] .label`).innerText();

	await startArranging(page, label);

	const band = page.locator('nav.rail .zone.between');
	// Layout room, not the painted box: the lit band is scaled by paint.
	const room = () =>
		band.evaluate((el) => ({
			height: (el as HTMLElement).offsetHeight,
			top: (el as HTMLElement).offsetTop
		}));
	const resting = (await band.boundingBox())!;
	const before = await room();

	const from = await centre(page, label);
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(resting.x + resting.width / 2, resting.y + resting.height / 2, {
		steps: 12
	});

	await expect(band).toHaveClass(/over/);
	await settled(band);

	expect(await room()).toEqual(before);

	await page.mouse.up();
});

test('the rows move while a row is being dragged, not when it is let go', async ({ page }) => {
	// The arrangement follows the pointer during the drag; where it ends is the drop test above.
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

	// A tenth down: a quarter is the edge of the dead band, and the rows wiggle.
	await page.mouse.move(from.x, from.y);
	await page.mouse.down();
	await page.mouse.move(target.x + target.width / 2, target.y + target.height * 0.1, {
		steps: 12
	});

	await expect
		.poll(async () => (await shown(page)).indexOf('/tags'), {
			message: 'the rail did not move until the row was let go'
		})
		.toBeLessThan(started);

	await page.mouse.up();
});

test('the sidebar can be rearranged from Appearance as well', async ({ page }) => {
	// The one screen that lists hidden rows too, with the rule as a row.
	await page.goto('/settings/appearance');
	await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();

	const order = () =>
		page
			.locator('.rows li')
			.evaluateAll((rows) => rows.map((row) => row.querySelector('.what')?.textContent?.trim()));

	const before = await order();
	const second = before[1];
	expect(second, 'not enough rows to rearrange').toBeTruthy();

	// Alt with an arrow is the keyboard's drag.
	await page.locator('.rows li').nth(1).locator('.handle').focus();
	await page.keyboard.press('Alt+ArrowUp');

	await expect.poll(async () => (await order())[0]).toBe(second);

	await page.reload();
	await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();
	expect((await order())[0]).toBe(second);
});
