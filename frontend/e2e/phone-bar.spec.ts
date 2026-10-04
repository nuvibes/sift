import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * The top bar and the wall headers at a phone's width, and the top bar at the desktop window's
 * narrowest.
 *
 * ## The two phones
 *
 * 393 and 412 wide are the two phones the layout is photographed on: an iPhone and a Pixel. At
 * that width the bar is a row of squares (the search, one control for Filter and Sort, Hidden,
 * Add), the search opens as a field the width of the screen, and Filter and Sort open as a sheet
 * from the bottom edge. Beside the squares the field would be squeezed to 72px, its words cut
 * at three letters.
 *
 * ## The snap width
 *
 * 960 is half of a 1920 screen, which is where the desktop window lands when it is snapped to one
 * side, and the narrowest the window will go. The bar can come apart there (a control dropping to
 * a second row) and Theater can leave the rail. So the claim is pinned at that width: every control on
 * the bar on one row inside it, and Theater still a row of the rail.
 *
 * Measured in a browser rather than in the unit suite, because the claims are about pixels and the
 * unit environment lays nothing out.
 */

const PHONES = [
	{ name: 'iPhone', width: 393, height: 659 },
	{ name: 'Pixel', width: 412, height: 839 }
] as const;

const SNAP = { width: 960, height: 900 };

/** Every visible control on the top bar, as boxes. */
async function barControls(page: Page) {
	return page.locator('header.topbar button:visible').evaluateAll((buttons) =>
		buttons.map((one) => {
			const box = one.getBoundingClientRect();
			return {
				name: one.getAttribute('aria-label') ?? one.textContent?.trim() ?? '',
				left: box.left,
				right: box.right,
				top: box.top,
				bottom: box.bottom
			};
		})
	);
}

/** The bar's own box. */
async function barBox(page: Page) {
	const box = await page.locator('header.topbar').boundingBox();
	if (!box) throw new Error('no top bar on the page');
	return box;
}

/** Every control inside the bar's box and on one row: one centre line, within a pixel. */
async function expectOneRow(page: Page, where: string) {
	await page.evaluate(() => document.fonts.ready.then(() => undefined));
	const bar = await barBox(page);
	const controls = await barControls(page);
	expect(controls.length, `nothing on the bar at ${where}`).toBeGreaterThan(2);
	const middles = controls.map((one) => Math.round((one.top + one.bottom) / 2));
	for (const one of controls) {
		expect(one.left, `${one.name} starts off the bar at ${where}`).toBeGreaterThanOrEqual(
			bar.x - 1
		);
		expect(one.right, `${one.name} runs off the bar at ${where}`).toBeLessThanOrEqual(
			bar.x + bar.width + 1
		);
		expect(one.bottom, `${one.name} hangs below the bar at ${where}`).toBeLessThanOrEqual(
			bar.y + bar.height + 1
		);
	}
	expect(Math.max(...middles) - Math.min(...middles), `two rows at ${where}`).toBeLessThanOrEqual(
		1
	);
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

for (const phone of PHONES) {
	test.describe(`at a phone's width (${phone.name}, ${phone.width})`, () => {
		test.beforeEach(async ({ page }) => {
			await page.setViewportSize({ width: phone.width, height: phone.height });
		});

		test('the bar is one row of squares with no search field on it', async ({ page }) => {
			await page.goto('/browse');
			await expect(page.getByRole('button', { name: 'Search', exact: true })).toBeVisible();
			await expect(page.locator('header.topbar .search')).toHaveCount(0);
			await expect(page.locator('header.topbar [aria-label="Filter"]')).toHaveCount(0);
			await expectOneRow(page, `${phone.width} on Browse`);
			const wide = await page.evaluate(() => document.documentElement.scrollWidth);
			expect(wide, 'the page scrolls sideways').toBeLessThanOrEqual(phone.width);
		});

		test('the search square opens a field the width of the screen', async ({ page }) => {
			await page.goto('/browse');
			await page.getByRole('button', { name: 'Search', exact: true }).click();
			const field = page.getByRole('dialog', { name: 'Search' }).locator('input').first();
			await expect(field).toBeFocused();
			/* The overlay arrives on a motion; measured mid-way it is 98% of its width. */
			await page
				.getByRole('dialog', { name: 'Search' })
				.evaluate((dialog) =>
					Promise.all(dialog.getAnimations({ subtree: true }).map((one) => one.finished))
				);
			const box = await page
				.getByRole('dialog', { name: 'Search' })
				.locator('.search')
				.boundingBox();
			expect(box!.width, 'the field is not the width of the screen').toBeGreaterThanOrEqual(
				phone.width - 2 * 16 - 1
			);
			/* 16px or larger, or the phone's browser zooms the page into the field when it is
			   pressed. */
			const size = await field.evaluate((one) => parseFloat(getComputedStyle(one).fontSize));
			expect(size).toBeGreaterThanOrEqual(16);
		});

		test('Filter and Sort open one sheet holding both', async ({ page }) => {
			await page.goto('/browse');
			await page.getByRole('button', { name: 'Filter and sort' }).click();
			const sheet = page.locator('aside.drawer[aria-label="Filter and sort"]');
			await expect(sheet).toBeVisible();
			/* The one shared chooser (`Select`), the desktop's own Sort control: its trigger is a button. */
			await expect(sheet.getByRole('button', { name: 'Sort by' })).toBeVisible();
			await expect(sheet.locator('.bar-panel')).toBeVisible();
			const box = await sheet.boundingBox();
			expect(Math.round(box!.x)).toBe(0);
			expect(Math.round(box!.width)).toBe(phone.width);
			await page.keyboard.press('Escape');
			await expect(sheet).toBeHidden();
		});

		test("a wall's Add stays whole on the line under its title", async ({ page }) => {
			for (const wall of ['/people', '/sites', '/tags', '/collections']) {
				await page.goto(wall);
				const add = page.locator('.wall-controls button').last();
				await expect(add).toBeVisible();
				const box = await add.boundingBox();
				const find = await page.locator('.wall-controls .find').boundingBox();
				expect(box!.x + box!.width, `Add is cut on ${wall}`).toBeLessThanOrEqual(phone.width);
				expect(
					Math.abs(box!.y + box!.height / 2 - (find!.y + find!.height / 2)),
					`Add left the box's line on ${wall}`
				).toBeLessThanOrEqual(1);
			}
		});
	});
}

test.describe('at the snap width (960, half of a 1920 screen)', () => {
	test.beforeEach(async ({ page }) => {
		await page.setViewportSize(SNAP);
	});

	test('the bar stays one row on every screen, and Theater stays in the rail', async ({ page }) => {
		for (const screen of ['/browse', '/people', '/tags', '/theater', '/settings']) {
			await page.goto(screen);
			await expectOneRow(page, `960 on ${screen}`);
			await expect(
				page.locator('#main-rail a[href="/theater"]'),
				`Theater left the rail on ${screen}`
			).toBeVisible();
		}
	});
});
