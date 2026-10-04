import { expect, test as base, type Cookie, type Page } from '@playwright/test';

import { signIn } from './admin';
import { PRESSED_VIEWPORTS } from './widths';

/* The interface photographed and compared with the last photographs somebody accepted.
 *
 * Every entry of the design gallery, element by element, at the width the design is drawn for, and
 * the screens people spend their time on at every size the interface is checked at (`widths.ts`),
 * the Windows snap width among them. A unit test reads one computed style; this reads the
 * whole picture, so a token that moves, a rule that reaches a stranger's element or a row that
 * loses its spacing shows up as the screens it changed.
 *
 * It runs against a server somebody names, never one it starts, and only when all four are set:
 *
 *     VISUAL_URL          the server, for example http://127.0.0.1:5171
 *     VISUAL_USERNAME     an admin on it
 *     VISUAL_PASSWORD     that admin's password
 *     VISUAL_BASELINES    the folder the accepted photographs live in, one per operating system (win32, linux)
 *
 * The photographs show whatever library that server holds, so they belong beside that library's
 * own data and never in this tree. A screen the library has nothing for (no person to open, no
 * queue with work in it) is skipped and says which.
 *
 * Accepting a change is running it again with `--update-snapshots` after looking at the diff:
 *
 *     npx playwright test --project visual --update-snapshots
 */

const SERVER = process.env.VISUAL_URL;
const USERNAME = process.env.VISUAL_USERNAME;
const PASSWORD = process.env.VISUAL_PASSWORD;
const READY = Boolean(SERVER && USERNAME && PASSWORD && process.env.VISUAL_BASELINES);

/* One sign-in per worker, lent to every test as its cookies: the server takes one sign-in at a time
 * per name and address, and a photograph per test would otherwise be a queue of logins. */
const test = base.extend<object, { session: Cookie[] }>({
	session: [
		async ({ browser }, use) => {
			if (!READY) {
				await use([]);
				return;
			}
			const context = await browser.newContext({ baseURL: SERVER });
			const answer = await signIn(context.request, USERNAME as string, PASSWORD as string);
			expect(answer.ok(), `signing in to ${SERVER}: ${answer.status()}`).toBe(true);
			await use(await context.cookies());
			await context.close();
		},
		{ scope: 'worker' }
	],
	page: async ({ page, session }, use) => {
		await page.context().addCookies(session);
		await use(page);
	}
});

test.skip(!READY, 'set VISUAL_URL, VISUAL_USERNAME, VISUAL_PASSWORD and VISUAL_BASELINES');

/* A screen is photographed once it has stopped loading: no placeholder bone, nothing marked busy,
 * every picture decoded. A placeholder holds still, so the comparison's own retake cannot tell it
 * from the screen it stands for. Bounded, because a screen that is busy for as long as work runs
 * (a scan in progress) never gets there, and is then photographed as it is. */
async function settle(page: Page): Promise<void> {
	await page.waitForLoadState('load');
	await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
	await page.evaluate(() => document.fonts.ready);
	await page
		.waitForFunction(
			() =>
				!document.querySelector('.bone, [aria-busy="true"]') &&
				Array.from(document.images).every((image) => image.complete),
			undefined,
			{ timeout: 15_000 }
		)
		.catch(() => undefined);
}

/* The library's own pictures are masked: what is compared is the interface around them, and a
 * picture that changes because the library did is not a change to the interface. `also` masks
 * more, where a screen's content is chosen afresh on every visit. */
async function screen(page: Page, name: string, path: string, also?: string): Promise<void> {
	const pictures = 'main img, main video, [role="dialog"] img, [role="dialog"] video';
	for (const [at, size] of PRESSED_VIEWPORTS.entries()) {
		await page.setViewportSize(size);
		await page.goto(path);
		await settle(page);
		// The design width keeps the name its photographs always had; the others say their width.
		const called = at === 0 ? `screen-${name}.png` : `screen-${name}-${size.width}.png`;
		await expect.soft(page, `${name} at ${size.width}`).toHaveScreenshot(called, {
			maxDiffPixelRatio: 0.01,
			mask: [page.locator(also ? `${pictures}, ${also}` : pictures)]
		});
	}
}

/* The first link on a page that opens one of the things it lists, or null when the library has
 * none. A link to a form (`/people/new`) or to one of `besides` is not one of them. */
async function firstLink(
	page: Page,
	from: string,
	prefix: string,
	besides: readonly string[] = []
): Promise<string | null> {
	await page.goto(from);
	await settle(page);
	const hrefs = await page
		.locator(`a[href^="${prefix}"]`)
		.evaluateAll((links) => links.map((link) => link.getAttribute('href') ?? ''));
	const opens = (href: string) =>
		/^\/[a-z-]+\/[^/?#]+$/.test(href) && !href.endsWith('/new') && !besides.includes(href);
	return hrefs.find(opens) ?? null;
}

test('every entry of the design gallery', async ({ page }) => {
	test.setTimeout(15 * 60_000);
	await page.goto('/design');
	await settle(page);
	// The gallery is optional and a build without it has no /design page to photograph.
	const gallery = await page
		.locator('div.gallery')
		.first()
		.waitFor({ timeout: 10_000 })
		.then(
			() => true,
			() => false
		);
	test.skip(!gallery, `${SERVER} has no design gallery at /design`);
	/* The bar is fixed over the page, so an entry scrolled under it carries a slice of the bar at
	   whatever offset the scroll landed, and two loads of the same page differ by that slice.
	   Hidden, not removed: the layout it holds stays as it is. */
	await page.addStyleTag({ content: 'header.topbar { visibility: hidden; }' });
	const ids = await page
		.locator('section.specimen')
		.evaluateAll((sections) => sections.map((section) => section.id));
	expect(ids.length, 'the gallery drew no entries').toBeGreaterThan(0);
	for (const id of ids) {
		const entry = page.locator(`section.specimen[id="${id}"]`);
		await entry.scrollIntoViewIfNeeded();
		// Two frames, so a component that measures itself once it is on screen has done so.
		await page.evaluate(
			() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
		);
		// Animations stopped at their end, so an entry that arrives on a motion is photographed at rest.
		await expect.soft(entry, id).toHaveScreenshot(`design-${id}.png`, { animations: 'disabled' });
	}
});

/* Name, address, and what else to mask there. */
const SCREENS: readonly (readonly [string, string, string?])[] = [
	['browse', '/browse'],
	['people', '/people'],
	['sites', '/sites'],
	['collections', '/collections'],
	['tags', '/tags'],
	['organize', '/organize'],
	['insights', '/insights'],
	// The wall plays files picked at random, each cell sized to its file.
	['theater', '/theater', 'main .stalls'],
	['downloads', '/downloads'],
	['activity', '/settings/tasks?show=now'],
	['settings-general', '/settings/general'],
	['settings-appearance', '/settings/appearance'],
	['settings-folders', '/settings/library'],
	['settings-faces', '/settings/faces'],
	['settings-tasks', '/settings/tasks'],
	['empty-search', '/browse?q=nothing-in-any-library-matches-this']
];

for (const [name, path, also] of SCREENS) {
	test(`the ${name} screen`, async ({ page }) => {
		await screen(page, name, path, also);
	});
}

test('a person page', async ({ page }) => {
	const person = await firstLink(page, '/people', '/people/');
	test.skip(person === null, 'this library has no person to open');
	await screen(page, 'person', person as string);
});

test('a queue on Organize', async ({ page }) => {
	const queue = await firstLink(page, '/organize', '/organize/', ['/organize/decisions']);
	test.skip(queue === null, 'this library has no queue to open');
	await screen(page, 'queue', queue as string);
});

test('a file page', async ({ page }) => {
	const answer = await page.request.get('/api/assets?limit=1');
	const items = answer.ok() ? ((await answer.json()).items as { id: string }[]) : [];
	test.skip(items.length === 0, 'this library has no file to open');
	await screen(page, 'file', `/asset/${items[0].id}`);
});

test('the sign-in screen', async ({ page }) => {
	await page.context().clearCookies();
	await screen(page, 'sign-in', '/login');
});
