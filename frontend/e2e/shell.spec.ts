import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The shell, in a browser, served the way it is served in an install.
 *
 * These are the things that are true of every screen and are therefore nobody's screen to test: the
 * app starts at all, an address means what it says, and the chrome adapts. Each of them can break in
 * a way that no unit test sees, because each of them is about the whole thing being assembled.
 *
 * Every one of them signs in first, because the shell is a signed-in thing: somebody who is not
 * signed in is sent to the sign-in screen, which has no rail, no search box and no tabs. That the
 * redirect happens is tested where the sign-in screens are.
 */

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('the app starts', async ({ page }) => {
	// Worth stating plainly, because the way this fails is total and quiet: the policy refuses the
	// bootstrap script, nothing runs, and the server still returns a perfectly good 200 with a page
	// in it. Every check that does not use a browser passes.
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
	// The offline promise, from the browser's side rather than by reading the build. A font, an icon
	// set or a script pulled from a CDN would hand a third party the address of everyone who opens
	// Sift, every time they open it.
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
	// The vault hides things from someone glancing at the screen, and a tab title is on screen even
	// when its page is not: in the tab strip, in the window switcher, and in history afterwards.
	await page.goto('/asset/01HXTESTTESTTESTTESTTESTTE');
	await expect(page).toHaveTitle('Sift');
});

test.describe('a link is the state', () => {
	test('a filtered address survives being opened cold', async ({ page }) => {
		// Filters live in the address, not in a variable, so that a view can be sent to someone. This
		// is that being true rather than intended: the URL is opened directly, as a recipient would.
		//
		// The box is a combobox: it owns the suggestions listbox under it, and ARIA puts that role on
		// the input rather than on a wrapper. Asking for a searchbox would match nothing at all.
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
		// Stubs, mostly. The point is that the tree exists: a route nobody created is a route the next
		// person invents again, in a different shape.
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

		// The rail is gone and the tabs are there. Both halves: a rail that stayed would overlap the
		// content, and tabs that never appeared would leave a phone with no navigation at all.
		const nav = page.getByRole('navigation', { name: 'Main' });
		await expect(nav).toBeVisible();
		await expect(page.getByRole('link', { name: 'More' })).toBeVisible();
	});
});

test('every control can be reached by keyboard, and shows where it is', async ({ page }) => {
	// Someone using the keyboard has to be able to see what is focused. This is not a preference: an
	// invisible focus ring makes the app unusable rather than merely awkward, and it is exactly the
	// thing that gets removed by accident to tidy up an outline.
	await page.goto('/browse');

	// Wait for the shell before pressing anything. It renders once the server has said who this is,
	// and until then the page is empty. Tab would move focus to nothing and this would fail for a
	// reason that has nothing to do with focus rings.
	await expect(page.getByRole('navigation', { name: 'Main' }).first()).toBeVisible();

	// Click first, so the keystrokes land in the page rather than in the browser's own chrome. The
	// click itself leaves nothing focus-visible (that is what focus-visible means: it answers "did
	// they arrive here by keyboard"), so Tab from here is a real keyboard arrival.
	await page.locator('body').click();
	await page.keyboard.press('Tab');

	// Asked of the browser directly rather than through a selector, because the question is about
	// what the browser decided: which element it focused, whether it counts that as a keyboard
	// arrival, and what it computed for the ring.
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
	/* Two scrollers, so reading down a long section does not carry the section LIST up and off
	 * the screen: the list is navigation. Only checkable in a browser: the override that takes
	 * the scrolling off the frame is a cascade question, and it can silently lose.
	 */
	await page.setViewportSize({ width: 1280, height: 700 });
	await page.goto('/settings/appearance');

	const shell = page.locator('.settings');
	await expect(shell).toBeVisible();

	// The shell is exactly as tall as the frame, so nothing is left for the frame to scroll.
	expect((await shell.boundingBox())!.height).toBeLessThanOrEqual(700);

	/* The scrolling element is the shared scroll region's viewport inside each column, not the
	   column itself (see `Scroller`). Found by walking up from the thing being scrolled rather than
	   by naming the library's own attribute, so this keeps checking the RULE (these two scroll, and
	   separately) rather than the current mechanism. */
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
			// Two, and two DIFFERENT ones: one scroller wrapping both would satisfy the checks above
			// and be exactly the fault this test was written for.
			separate:
				element.querySelectorAll('[data-scroll-area-viewport]').length >= 2 &&
				element.querySelector('.sections')?.closest('[data-scroll-area-viewport]') !==
					element.querySelector('.pane')?.closest('[data-scroll-area-viewport]')
		};
	});

	expect(overflow.nav === 'auto' || overflow.nav === 'scroll').toBe(true);
	expect(overflow.pane === 'auto' || overflow.pane === 'scroll').toBe(true);
	expect(overflow.separate, 'the list and the section share one scroller').toBe(true);
	// And the frame does not, or there are three scrollbars and the outer one moves the inner two
	// out of reach.
	expect(overflow.frame, 'the frame is still scrolling as well').toBe('hidden');
});
