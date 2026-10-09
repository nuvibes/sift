/* The desktop window's title strip and the inset it gives back, measured beside the browser.
 * The bridge the shell injects is injected here too: that is what makes this the desktop case. */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

async function asTheDesktopApp(page: Page): Promise<void> {
	await page.addInitScript(() => {
		(window as unknown as { sift: unknown }).sift = {
			isDesktop: true,
			setTitleBar: async () => true
		};
	});
}

const box = async (page: Page, selector: string) => (await page.locator(selector).boundingBox())!;

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test.describe('in a browser', () => {
	test('there is no strip, and nothing is moved to make room for one', async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		await expect(page.locator('.window-bar')).toHaveCount(1);
		// Hidden by the stylesheet, not left out by the layout.
		await expect(page.locator('.window-bar')).toBeHidden();

		const shell = await box(page, '.shell');
		expect(shell.y).toBe(0);
		expect(Math.round(shell.height)).toBe(900);
	});
});

test.describe('inside the desktop window', () => {
	test.beforeEach(async ({ page }) => {
		await asTheDesktopApp(page);
	});

	test("a strip of Sift's own runs across the top", async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const bar = await box(page, '.window-bar');
		expect(bar.y).toBe(0);
		expect(Math.round(bar.width)).toBe(1400);
		// The 36 the shell gives the caption overlay, which centres its buttons in it.
		expect(Math.round(bar.height)).toBe(36);
	});

	test('and everything else starts below it rather than under it', async ({ page }) => {
		// Padding, not a child's margin: a margin would collapse through and add a scrollbar.
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const shell = await box(page, '.shell');
		expect(Math.round(shell.y)).toBe(0);
		expect(Math.round(shell.height)).toBe(900);

		const content = await box(page, '.content');
		expect(content.y).toBeGreaterThanOrEqual(36);
	});

	test('and the content panel is inset from the top and the right again', async ({ page }) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const shell = await box(page, '.shell');
		const content = await box(page, '.content');

		expect(Math.round(content.y - shell.y)).toBe(36 + 8);
		expect(Math.round(shell.x + shell.width - (content.x + content.width))).toBe(8);
	});

	test('and its top-right corner is curved rather than squared off for the buttons', async ({
		page
	}) => {
		await page.goto('/browse');
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });

		const radius = await page
			.locator('.content')
			.evaluate((el) => getComputedStyle(el).borderTopRightRadius);

		expect(radius).not.toBe('0px');
	});

	// Every fixed box anchored to the window's top must clear the strip.
	test('and a dialog over the app lines up with the page, not with the window', async ({
		page
	}) => {
		await page.route('**/api/assets?*', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					items: [
						{
							id: 'm1',
							media_type: 'video',
							width: 1920,
							height: 1080,
							duration_ms: 1000,
							favorite: false,
							rating: null,
							concealed: false,
							original_filename: 'clip.mp4',
							thumb: true
						}
					],
					total: 1,
					limit: 50,
					offset: 0
				})
			})
		);
		await page.route('**/api/assets/m1', (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					id: 'm1',
					media_type: 'video',
					width: 1920,
					height: 1080,
					duration_ms: 1000,
					favorite: false,
					rating: null,
					concealed: false,
					original_filename: 'clip.mp4',
					thumb: true
				})
			})
		);
		await page.route('**/api/assets/*/thumb*', (route) => route.fulfill({ status: 404 }));
		await page.route('**/api/assets/*/preview*', (route) => route.fulfill({ status: 404 }));

		await page.goto('/browse');
		const topbarBottom = await page
			.locator('.topbar')
			.evaluate((el) => el.getBoundingClientRect().bottom);
		await page.locator('.tile').first().click();
		const sheet = page.getByRole('dialog');
		await expect(sheet).toBeVisible();
		await sheet.evaluate(
			(element) =>
				new Promise<void>((done) => {
					let last = '';
					const look = () => {
						const at = element.getBoundingClientRect();
						const now = `${at.top}x${at.width}`;
						if (now === last) return done();
						last = now;
						requestAnimationFrame(look);
					};
					requestAnimationFrame(look);
				})
		);
		const sheetTop = (await sheet.boundingBox())!.y;

		/* The browser test's claim, made with the strip on, where the offset is not zero. */
		expect(
			Math.abs(sheetTop - topbarBottom),
			`the topbar ends at ${topbarBottom} and the dialog starts at ${sheetTop}`
		).toBeLessThan(2);
	});

	test('and the settings panel clears the strip on a window short enough to matter', async ({
		page
	}) => {
		// 5vh of 700 is 35, one pixel under the 36px strip; opened as a panel from the rail.
		await page.setViewportSize({ width: 1400, height: 700 });
		await page.goto('/browse');

		// The rail drawn means the router is ready; an earlier click loads `/settings` as a page.
		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible({ timeout: 20_000 });
		await page.getByRole('link', { name: 'Settings', exact: true }).click();

		const panel = page.getByRole('dialog', { name: 'Settings' });
		await expect(panel).toBeVisible();

		// Settled, then read once: polling would accept a frame from the entrance.
		await panel.evaluate(
			(element) =>
				new Promise<void>((done) => {
					let last = '';
					const look = () => {
						const at = element.getBoundingClientRect();
						const now = `${at.top}x${at.height}`;
						if (now === last) return done();
						last = now;
						requestAnimationFrame(look);
					};
					requestAnimationFrame(look);
				})
		);

		expect((await panel.boundingBox())!.y).toBeGreaterThanOrEqual(36);
	});

	/* A signed-out card centres on the room below the strip, read off the card itself. */
	test('and a signed-out screen starts below the strip too', async ({ page }) => {
		await page.context().clearCookies();
		await page.goto('/login');
		await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();

		const page_ = await box(page, '.page');
		expect(Math.round(page_.y)).toBe(0);
		expect(Math.round(page_.height)).toBe(900);

		const card = await box(page, '.page .card');
		expect(card.y).toBeGreaterThanOrEqual(36);
		const over = card.y - 36;
		const under = 900 - (card.y + card.height);
		expect(Math.abs(over - under)).toBeLessThanOrEqual(1);
	});
});
