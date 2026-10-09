import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* What is true of every screen, served as an install serves it. Signed in first: a signed-out
 * visitor gets the sign-in screen, which has no shell. */

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('the app starts', async ({ page }) => {
	// A refused bootstrap script still returns a good 200; only a browser sees nothing run.
	const refusals: string[] = [];
	page.on('console', (message) => {
		const text = message.text();
		if (/content security policy|refused to execute/i.test(text)) refusals.push(text);
	});

	await page.goto('/browse');

	await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
	expect(refusals, 'the browser refused to run the app').toEqual([]);
});

test('nothing is fetched from another host', async ({ page }) => {
	// Offline: anything from a CDN would tell a third party who opens Sift.
	const offsite: string[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost') offsite.push(request.url());
	});

	await page.goto('/browse');
	await page.waitForLoadState('networkidle');

	expect(offsite, 'the app reached off this machine').toEqual([]);
});

test('the tab title never carries what is on screen', async ({ page }) => {
	// A tab title shows in the tab strip and history even when the page does not.
	await page.goto('/asset/01HXTESTTESTTESTTESTTESTTE');
	await expect(page).toHaveTitle('Sift');
});

test.describe('a link is the state', () => {
	test('a filtered address survives being opened cold', async ({ page }) => {
		// Filters live in the address, opened as a recipient would. A combobox: it owns a listbox.
		await page.goto('/browse?q=holiday&rating=4&sort=added');

		await expect(page).toHaveURL('/browse?q=holiday&rating=4&sort=added');
		await expect(page.getByRole('combobox', { name: 'Search' })).toHaveValue('holiday');
	});

	test('and survives a reload', async ({ page }) => {
		await page.goto('/browse?q=holiday&rating=4');
		await page.reload();

		await expect(page).toHaveURL('/browse?q=holiday&rating=4');
		await expect(page.getByRole('combobox', { name: 'Search' })).toHaveValue('holiday');
	});

	test('every route in the app resolves', async ({ page }) => {
		// The tree exists, so nobody invents a route again in a different shape.
		for (const path of [
			'/browse',
			'/favorites',
			'/collections',
			'/people',
			'/sites',
			'/tags',
			'/hidden',
			'/settings',
			'/settings/playback',
			'/settings/library',
			'/settings/tasks'
		]) {
			const response = await page.goto(path);
			expect(response?.status(), `${path} did not resolve`).toBe(200);
			await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
		}
	});
});

test.describe('the chrome adapts', () => {
	test('the rail carries labels on a wide window', async ({ page }) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto('/browse');

		await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
		await expect(page.getByRole('link', { name: 'Browse', exact: true })).toBeVisible();
	});

	test('and becomes tabs on a phone', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto('/browse');

		// Both halves: a rail that stayed would overlap, and no tabs would leave no navigation.
		const nav = page.getByRole('navigation', { name: 'Main' });
		await expect(nav).toBeVisible();
		await expect(page.getByRole('link', { name: 'More' })).toBeVisible();
	});
});

test('every control can be reached by keyboard, and shows where it is', async ({ page }) => {
	// A visible focus ring is required, and easy to remove by accident.
	await page.goto('/browse');

	// The shell renders once the server says who this is.
	await expect(page.getByRole('navigation', { name: 'Main' }).first()).toBeVisible();

	// A click leaves nothing focus-visible, so the Tab after it is a real keyboard arrival.
	await page.locator('body').click();
	await page.keyboard.press('Tab');

	const focused = await page.evaluate(() => {
		const element = document.activeElement;
		if (!element || element === document.body) return null;
		return {
			keyboardArrival: element.matches(':focus-visible'),
			ring: getComputedStyle(element).boxShadow
		};
	});

	expect(focused, 'Tab focused nothing').not.toBeNull();
	expect(focused?.keyboardArrival, 'the browser does not count this as a keyboard arrival').toBe(
		true
	);
	expect(focused?.ring, 'the focused control has no visible focus ring').not.toBe('none');
});

test('settings scrolls its section list and its section separately', async ({ page }) => {
	// Two scrollers, so the section list stays on screen; a cascade that can silently lose.
	await page.setViewportSize({ width: 1280, height: 700 });
	await page.goto('/settings/appearance');

	const shell = page.locator('.settings');
	await expect(shell).toBeVisible();

	expect((await shell.boundingBox())!.height).toBeLessThanOrEqual(700);

	// Found by walking up from the scrolled content, so this checks the rule, not `Scroller`.
	const overflow = await shell.evaluate((element) => {
		const scrollerAround = (start: HTMLElement | null): string => {
			for (let at = start; at; at = at.parentElement) {
				const how = getComputedStyle(at).overflowY;
				if (how === 'auto' || how === 'scroll') return how;
			}
			return 'none';
		};
		return {
			nav: scrollerAround(element.querySelector('.sections')),
			pane: scrollerAround(element.querySelector('.pane')),
			frame: getComputedStyle(element.parentElement as HTMLElement).overflowY,
			// Two different ones: one wrapping both is the fault.
			separate:
				element.querySelectorAll('[data-scroll-area-viewport]').length >= 2 &&
				element.querySelector('.sections')?.closest('[data-scroll-area-viewport]') !==
					element.querySelector('.pane')?.closest('[data-scroll-area-viewport]')
		};
	});

	expect(overflow.nav === 'auto' || overflow.nav === 'scroll').toBe(true);
	expect(overflow.pane === 'auto' || overflow.pane === 'scroll').toBe(true);
	expect(overflow.separate, 'the list and the section share one scroller').toBe(true);
	// The frame does not scroll, or the outer bar moves the inner two out of reach.
	expect(overflow.frame, 'the frame is still scrolling as well').toBe('hidden');
});
