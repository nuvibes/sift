/* The surfaces that arrive really do arrive.
 *
 * A transition declared on an element is invisible to every check that does not run a browser: the
 * unit environment has no animation machinery at all, so a component with its transition deleted
 * renders exactly as one that still has it. This is the gate for that: it asks the browser what
 * it is animating at the moment a thing appears, which is a question only the browser can answer.
 *
 * It deliberately asserts that something is running rather than what it looks like. How far the
 * selection bar rises is a taste decision that will be adjusted; that it rises at all is the
 * behaviour, and a test pinned to the number would fail on every adjustment and teach whoever hit
 * it to stop reading this file.
 */
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

	// Opened from the rail, which is how somebody reaches it in the middle of something else.
	await page.getByRole('link', { name: 'Settings', exact: true }).click();

	const animating = await running(page);
	expect(animating.join(' ')).toContain('veil');
	expect(animating.join(' ')).toContain('panel');
});

test('and a toast arrives rather than blinking into existence', async ({ page }) => {
	/* Toasts are the only place an undo is ever offered, so one that appears between two frames is
	 * one somebody's eye can miss entirely, and with it, the only chance to take an action back. */
	/* Anything that raises one will do. A tag made on its own screen with the rest of its record
	   refused is the shortest: the tag is made and the toast says the rest could not be saved. */
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

/* THE MENU EVERY RIGHT-CLICK IN THE APPLICATION OPENS.
 *
 * `ContextMenu` is the most-used surface in Sift. It is portalled out of whatever opened it, its
 * arrival is a CSS animation rather than a transition (`rise`, the one every small surface opening
 * shares), and both of those are invisible to the unit environment: a component with the
 * animation deleted renders identically to one that still has it.
 *
 * The rail's own row menu is the trigger, because the rail is on every screen and needs no
 * library behind it: a menu reached through the grid would be a test about assets that happens to
 * open a menu.
 *
 * Recorded by LISTENING rather than by sampling. `--dur-fast` is 140ms, so asking the document
 * what it is animating after waiting for the menu to be visible is a race the fast machine loses:
 * the animation is over. An `animationstart` listener catches it whenever it happens, and it
 * answers the harder question as well: WHICH animation, and on which element.
 */

/* The menu's own surface. `rise` is shared (a tooltip under the pointer runs it too), so only
   an animation the menu itself starts is counted as the menu's. */
const MENU = '.ui-menu';

/** Every animation the menu has STARTED since this was armed, by name. */
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

/* POLLED, NOT SAMPLED, AND THAT IS THE WHOLE SHAPE OF THIS.
 *
 * `animationstart` fires on the frame AFTER the element gets its style, and a menu is visible to
 * Playwright the moment it is attached, so reading the list straight after `toBeVisible()` reads
 * it before the animation has begun, and answers "nothing animated" about a menu that is animating.
 * A fast machine fails it exactly that way, while a test that happens to hover three times before
 * reading passes.
 *
 * The wait is bounded by the suite's own assertion timeout, so a menu that genuinely animates
 * nothing still fails: later, and no less certainly. */
async function waitForArrival(page: Page, what: string): Promise<void> {
	await expect
		.poll(async () => (await arrivals(page)).some((one) => one.endsWith(what)), {
			message: `nothing named ${what} ever started`
		})
		.toBe(true);
}

/* How far the menu's arrival travels: `rise` moves it by `--rise`, which reduced motion sets to
   nothing, so the same animation is a fade there. */
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
	/* The rule this is for is the reason the animation is written the way it is: bits-ui keeps the
	   content mounted, and an arrival that replayed on every re-render would flicker the whole menu
	   each time a row highlighted under the pointer. Moving between two rows is the cheapest
	   re-render there is. */
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

/*
 * WHO DECIDES WHETHER THE APP MOVES, and it is not simply the operating system.
 *
 * There are two answers and the person owns both. Sift's own switch defaults to ANIMATING, because
 * Windows turns its reduced-motion preference on for reasons that often have nothing to do with the
 * person (a battery saver, a remote session, a setting nobody remembers choosing), and a library
 * that arrives silent reads as broken rather than as considerate. "Follow Windows" is one press
 * away for anybody who wants the careful behaviour.
 *
 * Both directions are checked here. A reduced-motion rule written as a media query asking Windows
 * directly cannot be overruled by anything, so "Full motion" would change nothing.
 */

/** Open the app with Sift's own motion answer already chosen, as a returning visit would have it. */
async function openWith(browser: Browser, choice: 'system' | 'full' | 'reduce') {
	const context = await browser.newContext({ reducedMotion: 'reduce' });
	const page = await context.newPage();
	// Before any script of the app's runs, so the very first paint is already the right answer.
	await page.addInitScript((value) => {
		window.localStorage.setItem('sift.motion', value);
	}, choice);
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
	await page.goto('/browse');
	await page.locator('nav.rail a.item').first().waitFor();
	return { context, page };
}

/** Whether anything on the page is currently TRAVELLING. A fade is not travel. */
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
	/* The panel still opens and the veil still dims; what goes is the travel. So this asserts the
	 * settings panel is THERE and is not moving, which is the whole of what reduced motion means,
	 * not that the feature switched itself off. */
	const { context, page } = await openWith(browser, 'system');

	await page.getByRole('link', { name: 'Settings', exact: true }).click();

	/* The panel FIRST, and the order is the whole of this. Asked what is travelling before the panel
	   is known to be there, this reads a page with nothing on it yet and answers "nothing", which
	   is the assertion below passing for the one reason that says nothing about reduced motion. On a
	   loaded machine that is the ordinary case rather than the rare one. Sampled here, the panel is
	   attached and visible, which is the START of the animation it would be running if the travel
	   had not been taken away. */
	await expect(page.locator('.panel')).toBeVisible();
	const moving = await travelling(page);

	expect(moving, 'something is still travelling').toBe(false);
	expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe('reduce');

	await context.close();
});

test('Sift animates for somebody who asked it to, even on a machine wanting less movement', async ({
	browser
}) => {
	/* The machine is asking for reduced motion in this context and the person has said "Always
	 * animate", which is the default. Sift moves: the reduced-motion rules read the resolved
	 * answer, so this setting overrules the operating system rather than being overruled by it.
	 */
	const { context, page } = await openWith(browser, 'full');

	expect(await page.evaluate(() => document.documentElement.dataset.motion)).toBe('full');
	// The rail is the widest single movement in the shell.
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
	/* The menu's own answer to the setting is a fade rather than an absence: it still arrives,
	   because a surface that appears between two frames is one the eye misses, and what goes is
	   the travel. The others in this file check that travel stops; this one checks that the
	   arrival stays. */
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
