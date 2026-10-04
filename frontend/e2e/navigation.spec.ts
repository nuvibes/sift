import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';

/*
 * Two things about moving between screens that only a browser can answer.
 *
 * **A link click must not replace the document.** A callback registered in the root layout can stop
 * SvelteKit intercepting links altogether: every navigation becomes a full page load, every module
 * store dies on every click, and the application looks completely normal. No unit test and no spec
 * that starts from a fresh page can tell a routed navigation from a reload, so this file is what
 * stands between the project and that.
 *
 * **A wall you step back to must be where you left it.** Sift scrolls inside a box rather than
 * scrolling the window, so the browser's own restoration reaches none of it. The position is kept
 * per history entry through SvelteKit's snapshot (see `page-scroll.ts`), and the only place the
 * whole chain exists at once is a real browser: the layout exports it, the frame says which box it
 * is, and the router decides when to ask.
 */

/** A People wall with more cards on it than a window can hold.
 *
 * `from` is answered as well as `offset`, because a wall arriving with an anchor in its address
 * sends ONE of the two and never both (see `CardPaging.query`). A mock that reads only `offset`
 * answers every anchored arrival with the front of the wall, which is the front of the wall being
 * restored correctly and is indistinguishable from the anchor having been thrown away. The ids
 * here are `p<position>`, so the position an anchor names is the number in its own name; a real
 * server resolves it against the ordered list instead, which is why the client does not.
 */
function peopleWall(page: Page, count = 200) {
	void page.route('**/api/people?*', async (route) => {
		const url = new URL(route.request().url());
		const limit = Number(url.searchParams.get('limit') ?? '0');
		const from = url.searchParams.get('from');
		const offset =
			from === null
				? Number(url.searchParams.get('offset') ?? '0')
				: Number(from.replace(/^p/, '') || '0');
		await route.fulfill({
			json: {
				items: Array.from({ length: Math.max(0, Math.min(limit, count - offset)) }, (_, i) => ({
					id: `p${offset + i}`,
					name: `Person ${offset + i}`,
					vault: false,
					asset_count: 3,
					cover_asset_id: null,
					cover_track_id: null,
					favorite: false,
					rating: null,
					shared: false,
					restricted: false
				})),
				total: count,
				limit,
				offset
			}
		});
	});
}

const wall = (page: Page) => page.locator('.frame-body').first();

/** Which row is at the top of the wall, named by where its card leads. See the journey below. */
const topRow = (page: Page) =>
	wall(page).evaluate((element) => {
		const box = element.getBoundingClientRect();
		const card = [...element.querySelectorAll('.card')].find(
			(one) => one.getBoundingClientRect().bottom > box.top + 4
		);
		return card?.querySelector('a')?.getAttribute('href') ?? null;
	});
const scrollTop = (page: Page) => wall(page).evaluate((element) => element.scrollTop);

/** A mark on the window itself. A full page load is the only thing that can take it away. */
async function markTheDocument(page: Page) {
	await page.evaluate(() => {
		(window as unknown as { __sameDocument?: boolean }).__sameDocument = true;
	});
}

const stillTheSameDocument = (page: Page) =>
	page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument === true);

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1440, height: 900 });
});

/*
 * `(\?|$)` on every `/browse` assertion in this file.
 *
 * A wall writes its own paging cursor into the address once its first page lands. See
 * `lib/grid/anchor.ts`, which does it deliberately and says why. So `/browse` becomes
 * `/browse?from=01M1...` a moment after arriving, and an assertion anchored with `$` asserts the
 * absence of something the application is designed to write: it passes only by winning a race, and
 * loses it when everything is slower.
 *
 * What these tests are ABOUT is the scroll position and whether the document reloaded. Which
 * address they are on is only how they say they got there, and the pathname is what that means.
 */
test('a rail link is a navigation, not a page load', async ({ page }) => {
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await markTheDocument(page);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);

	expect(await stillTheSameDocument(page), 'the rail link reloaded the document').toBe(true);
});

test('a plain anchor is a navigation, not a page load', async ({ page }) => {
	/* The bare case, with no component of Sift's between the click and the router. It is the one
	   that says the ROUTER stopped working rather than any one screen.

	   Appended INSIDE `.app-root`: SvelteKit listens for clicks on the element it was mounted
	   into, which is `.app-root`, so a link appended to `document.body` is outside the
	   application and is meant to reload: it is not evidence of anything. */
	await page.goto('/browse');
	/* Wait for the screen itself, not for the chrome. Sift renders nothing on the server, so a
	   `.frame-body` on the page means the client has started, and until it has, the router has no
	   click listener and a link is an ordinary browser navigation. Marking before that measures the
	   race rather than the router. */
	await expect(wall(page)).toBeAttached();
	await markTheDocument(page);

	await page.evaluate(() => {
		const link = document.createElement('a');
		link.href = '/people';
		link.textContent = 'to people';
		link.id = 'a-bare-link';
		document.querySelector('.app-root')?.appendChild(link);
	});
	await page.locator('#a-bare-link').click();
	await expect(page).toHaveURL(/\/people(\?|$)/);

	expect(await stillTheSameDocument(page), 'a plain link reloaded the document').toBe(true);
});

test('a wall you step back to is where you left it', async ({ page }) => {
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();

	await wall(page).evaluate((element) => {
		element.scrollTop = 420;
	});
	await expect.poll(() => scrollTop(page)).toBe(420);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);

	await page.goBack();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	await expect.poll(() => scrollTop(page), { timeout: 5000 }).toBe(420);
});

test('a wall you arrive at fresh starts at the top', async ({ page }) => {
	/* The other half of the claim, and the one that stops "restore" meaning "always jump somewhere".
	   Leaving a wall part-scrolled and coming back to it by the RAIL is a new visit, not a step
	   back, and it must start where a new visit starts. */
	peopleWall(page);
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await wall(page).evaluate((element) => {
		element.scrollTop = 420;
	});
	await expect.poll(() => scrollTop(page)).toBe(420);

	await page.locator('nav[aria-label="Main"] a[data-rail-row="browse"]').click();
	await expect(page).toHaveURL(/\/browse(\?|$)/);
	await page.locator('nav[aria-label="Main"] a[data-rail-row="people"]').click();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	await page.waitForTimeout(1800); // longer than the restore holds for
	expect(await scrollTop(page)).toBe(0);
});

test('a page you turned to is the page you come back to', async ({ page }) => {
	/*
	 * Opening a person and pressing the trail comes back to the same PAGE of rows.
	 *
	 * The crumb follows the wall's REMEMBERED ADDRESS, which carries `?from=<row>`, rather than its
	 * own bare href. `goto` restores no scroll offset, so inside a page taller than the window you
	 * land at that page's top, on any library; the promise is at page level, so the test is.
	 *
	 * Two things have to hold at once and neither is visible to a unit test: the crumb goes to the
	 * remembered address rather than following its own bare href, and the wall that address names
	 * comes back with the same rows on it.
	 *
	 * NOTHING HERE SCROLLS, deliberately. A page turned is a page the wall draws from its own top,
	 * and pressing a card that is already wholly on screen leaves it there. Playwright scrolls a
	 * target into view before clicking it, and any scroll it makes moves the very wall this test is
	 * about.
	 */
	peopleWall(page);
	/*
	 * Answered with the person that was asked for, which a fixed answer here is not, and that is
	 * what makes this test able to fail for the right reason. A detail screen corrects its own
	 * address to the id the server hands back, so a mock answering `p5` to every request would step
	 * the browser from `/people/p16` to `/people/p5`: a change of PATHNAME, which `noteAddress`
	 * records as leaving a screen. The wall's remembered address would be overwritten with the
	 * person's own and the wall would come back at `?from=p0`: a failure of the fixture that
	 * reads exactly like the mechanism being broken.
	 */
	void page.route('**/api/people/p*', (route) => {
		const id = new URL(route.request().url()).pathname.split('/').at(-1) ?? 'p0';
		return route.fulfill({
			json: {
				id,
				name: `Person ${id.replace(/^p/, '')}`,
				vault: false,
				asset_count: 3,
				cover_asset_id: null,
				cover_track_id: null,
				favorite: false,
				rating: null,
				shared: false,
				restricted: false
			}
		});
	});
	await page.goto('/people');
	await expect(page.locator('.card').first()).toBeVisible();
	await expect.poll(() => topRow(page)).toBe('/people/p0');

	await page.getByRole('button', { name: 'Next page' }).click();

	/*
	 * WHICH row the second page starts at is read off the wall rather than typed here: a page is as
	 * many cards as this window holds, and that is measured off a real card. A number written in
	 * would be right on one screen size and quietly wrong on every other.
	 */
	await expect.poll(() => topRow(page)).not.toBe('/people/p0');
	const wasShowing = await topRow(page);
	expect(wasShowing, 'the wall never turned a page').not.toBeNull();
	const anchor = wasShowing?.split('/').pop();
	await expect.poll(() => new URL(page.url()).searchParams.get('from')).toBe(anchor);
	const address = new URL(page.url()).search;

	/* Pressed on a card that is wholly inside the scrolling box, for the reason above. */
	const pressable = await wall(page).evaluate((element) => {
		const box = element.getBoundingClientRect();
		return [...element.querySelectorAll('.card')].findIndex((card) => {
			const at = card.getBoundingClientRect();
			return at.top >= box.top + 4 && at.bottom <= box.bottom - 4;
		});
	});
	expect(pressable, 'no card was wholly on screen to press').toBeGreaterThanOrEqual(0);

	await page.locator('.card').nth(pressable).click();
	await expect(page).toHaveURL(/\/people\/p/);

	/*
	 * What the crumb is about to read, asserted before it is pressed.
	 *
	 * The two halves of this journey fail in the same place and want opposite repairs (the wall
	 * was never remembered, or it was and the crumb ignored it), and without this the only thing
	 * the failure says is "the wall came back at the front". `noteAddress` writes this, and its one
	 * rule is that a change of QUERY is standing still and a change of PATHNAME is leaving: so a
	 * detail screen that corrects its own address to a different path would overwrite this with
	 * itself, and the crumb would have nothing to return to.
	 */
	const remembered = await page.evaluate(() => ({
		cameFrom: sessionStorage.getItem('sift:came-from'),
		here: sessionStorage.getItem('sift:here')
	}));
	expect(
		remembered.cameFrom ?? '',
		`the wall was not remembered as the screen before this one: ${JSON.stringify(remembered)}`
	).toContain(`/people${address}`);

	await page.locator('nav[aria-label="Breadcrumb"] a').first().click();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	await expect(page.locator('.card').first()).toBeVisible();

	/*
	 * The two halves of the claim, and the floor underneath them is what makes this falsifiable: a
	 * wall that came back to the front carries no anchor at all and has `p0` at the top.
	 */
	expect(new URL(page.url()).search, 'the page the wall was on was thrown away').toBe(address);
	await expect.poll(() => topRow(page), { timeout: 5000 }).toBe(wasShowing);
});
