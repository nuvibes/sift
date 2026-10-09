/* Surfaces that appear really do animate: the unit environment has no animation machinery. It
 * asserts that something runs, not how far it moves, which is a taste that will be adjusted. */
import { type Browser, type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/** What the document is animating right now, by the element each one is on. */
async function running(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		document
			.getAnimations()
			.map((animation) => {
				const target = (animation.effect as KeyframeEffect | null)?.target;
				return target ? `${target.tagName.toLowerCase()}.${target.className}` : '';
			})
			.filter(Boolean)
	);
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the settings panel and its veil are animated in, not switched on', async ({ page }) => {
	await page.goto('/browse');
	await page.locator('nav.rail a.item').first().waitFor();

	// From the rail, the way somebody reaches it mid-task.
	await page.getByRole('link', { name: 'Settings', exact: true }).click();

	const animating = await running(page);
	expect(animating.join(' ')).toContain('veil');
	expect(animating.join(' ')).toContain('panel');
});

test('and a toast arrives rather than blinking into existence', async ({ page }) => {
	/* A toast is the only place an undo is offered, so it must not pop in unseen. A tag created
	   with the rest of its record refused raises one. */
	await page.route('**/api/tags', (route) =>
		route.request().method() === 'POST'
			? route.fulfill({ json: { id: 'motion-tag', name: 'motion-check' } })
			: route.fallback()
	);
	await page.route('**/api/tags/motion-tag**', (route) =>
		route.fulfill({ status: 500, body: '{}' })
	);
	await page.goto('/tags/new');
	await page.getByLabel('Name', { exact: true }).fill('motion-check');
	await page.getByRole('main').getByRole('button', { name: 'Save', exact: true }).first().click();

	await expect(page.locator('.toast')).toBeVisible();
	const animating = await running(page);
	expect(animating.join(' ')).toContain('toast');
});

/* The menu every right-click opens (`ContextMenu`), portalled and animated by `rise`. Recorded by
 * LISTENING for `animationstart`: at 140ms the animation is over before a sample is taken. */

/* `rise` is shared with tooltips, so only the menu's own animations count. */
const MENU = '.ui-menu';

/** Every animation the menu has started since this was armed, by name. */
async function recordArrivals(page: Page): Promise<void> {
	await page.evaluate((menu) => {
		const started: string[] = [];
		(window as unknown as { arrivals: string[] }).arrivals = started;
		document.addEventListener(
			'animationstart',
			(event) => {
				if (event.target instanceof Element && event.target.matches(menu)) {
					started.push((event as AnimationEvent).animationName);
				}
			},
			true
		);
	}, MENU);
}

async function arrivals(page: Page): Promise<string[]> {
	return page.evaluate(() => (window as unknown as { arrivals: string[] }).arrivals ?? []);
}

/* Polled: `animationstart` fires a frame after the menu is visible to Playwright. A menu that
 * animates nothing still fails at the assertion timeout. */
async function waitForArrival(page: Page, what: string): Promise<void> {
	await expect
		.poll(async () => (await arrivals(page)).some((one) => one.endsWith(what)), {
			message: `nothing named ${what} ever started`
		})
		.toBe(true);
}

/* Reduced motion sets `--rise` to nothing, so the same animation is a fade there. */
async function riseOf(page: Page): Promise<string> {
	return page
		.locator(MENU)
		.first()
		.evaluate((menu) => getComputedStyle(menu).getPropertyValue('--rise').trim());
}

test('the right-click menu arrives, rather than being there between two frames', async ({
	page
}) => {
	await page.goto('/browse');
	const row = page.locator('nav.rail a.item').first();
	await row.waitFor();
	await recordArrivals(page);

	await row.click({ button: 'right' });
	await expect(page.getByRole('menu').first()).toBeVisible();

	await waitForArrival(page, 'rise');
	expect(await riseOf(page), 'the menu arrived without rising').not.toBe('0px');
});

test('and it animates ONCE, not again whenever something inside it redraws', async ({ page }) => {
	/* bits-ui keeps the content mounted, so an arrival replayed on re-render would flicker the menu
	   as each row highlights. */
	await page.goto('/browse');
	const row = page.locator('nav.rail a.item').first();
	await row.waitFor();
	await recordArrivals(page);

	await row.click({ button: 'right' });
	await expect(page.getByRole('menu').first()).toBeVisible();
	await waitForArrival(page, 'rise');

	const items = page.getByRole('menuitem');
	await items.first().hover();
	await items.nth(1).hover();
	await items.first().hover();

	const arrived = (await arrivals(page)).filter((one) => one.endsWith('rise'));
	expect(arrived, 'the menu re-animated while it was open').toHaveLength(1);
});

/* Sift's own switch defaults to ANIMATING, since Windows turns reduced motion on for reasons that
 * are often not the person's; "Follow Windows" is one press away. Both directions are checked. */

/** Open the app with Sift's own motion answer already chosen. */
async function openWith(browser: Browser, choice: 'system' | 'full' | 'reduce') {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	// Before any of the app's scripts, so the first paint is already right.
	await page.addInitScript((value) => {
		window.localStorage.setItem('sift.motion', value);
	}, choice);
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.goto('/browse');
	await page.locator('nav.rail a.item').first().waitFor();
	return { context, page };
}

/** Whether anything is TRAVELLING; a fade is not travel. */
async function travelling(page: Page): Promise<boolean> {
	return page.evaluate(() =>
		document.getAnimations().some((animation) => {
			const frames = (animation.effect as KeyframeEffect | null)?.getKeyframes() ?? [];
			return frames.some((frame) => typeof frame.transform === 'string');
		})
	);
}

test('nothing travels for somebody who asked Sift to follow a machine wanting less movement', async ({
	browser
}) => {
	/* The panel still opens; what goes is the travel. */
	const { context, page } = await openWith(browser, 'system');

	await page.getByRole('link', { name: 'Settings', exact: true }).click();

	/* The panel first, or "nothing travelling" is read off an empty page. */
	await expect(page.locator('.panel')).toBeVisible();
	const moving = await travelling(page);

	expect(moving, 'something is still travelling').toBe(false);
	expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe('reduce');

	await context.close();
});

test('Sift animates for somebody who asked it to, even on a machine wanting less movement', async ({
	browser
}) => {
	/* The machine asks for reduced motion and the person said "Always animate": Sift moves. */
	const { context, page } = await openWith(browser, 'full');

	expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe('full');
	const railTransition = await page.evaluate(() => {
		const rail = document.querySelector('.rail');
		return rail === null ? null : getComputedStyle(rail).transitionDuration;
	});
	expect(railTransition, 'the sidebar has no transition to run').not.toBe('0s');
	expect(railTransition).not.toBeNull();

	await context.close();
});

test('the right-click menu fades rather than rising when less movement is wanted', async ({
	browser
}) => {
	/* The menu fades rather than vanishing: the arrival stays, the travel goes. */
	const { context, page } = await openWith(browser, 'system');
	const row = page.locator('nav.rail a.item').first();
	await row.waitFor();
	await recordArrivals(page);

	await row.click({ button: 'right' });
	await expect(page.getByRole('menu').first()).toBeVisible();

	await waitForArrival(page, 'rise');
	expect(await riseOf(page), 'the menu still travelled on a machine wanting less').toBe('0px');

	await context.close();
});
