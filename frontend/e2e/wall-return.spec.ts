import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { removePeople, seedPeople } from './seed';
import { pressCrumb } from './trail';

/*
 * Leaving a wall and coming back to it, and turning its page.
 *
 * 1. A wall's crumb press restores the page AND the scroll box. Two pages forward on People, the
 *    body scrolled, a person opened, the crumb "People" pressed: the same page and the same scroll
 *    as the browser's own Back gives. Parity with Back is the claim, not a raw offset, because
 *    Back restores through the page snapshot to the ANCHORED tile
 *    (`components/shell/page-scroll.ts`), which can differ from the pixel offset left. A test that
 *    asserted the raw offset would call a correct crumb wrong.
 *
 * 2. A page turn from the foot pager lands at the top of the scroll box.
 *
 * The box that scrolls is `.frame-body` (`PageFrame`), never the window: `window.scrollY` is nought
 * on every wall, so a check on the window cannot fail.
 *
 * Real people through the real API, many enough that a page of them overflows the box, and removed
 * afterwards. Serial: the two tests share the seeded wall.
 */

test.describe.configure({ mode: 'serial' });

const STEM = `e2e-wall-${Date.now()}`;
let seeded: string[] = [];

/** Short enough that a page of cards overflows the body at the smallest notch. */
const VIEW = { width: 1280, height: 640 };

test.beforeAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	seeded = await seedPeople(page, STEM, 140);
	await page.close();
});

test.afterAll(async ({ browser }) => {
	const page = await browser.newPage();
	await signInAsAdmin(page);
	await removePeople(page, seeded);
	await page.close();
});

test.beforeEach(async ({ page }) => {
	await page.setViewportSize(VIEW);
	await signInAsAdmin(page);
});

/** The pager's readout, "25-36", which is what "the same page" means to somebody looking. */
async function range(page: Page): Promise<string> {
	const where = page.locator('.frame-footer button.where .range');
	await expect(where).toBeVisible();
	return (await where.innerText()).replace(/\s+/g, '');
}

async function scrollTop(page: Page): Promise<number> {
	return page.evaluate(() => Math.round(document.querySelector('.frame-body')!.scrollTop));
}

/** Wait until the body's scroll position stops moving, and return it. */
async function settledTop(page: Page): Promise<number> {
	let last = -1;
	for (let tries = 0; tries < 30; tries += 1) {
		const now = await scrollTop(page);
		if (now === last) return now;
		last = now;
		await page.waitForTimeout(200);
	}
	return last;
}

/** Wait until the pager's readout stops changing, and return it. */
async function settledRange(page: Page): Promise<string> {
	let last = '';
	for (let tries = 0; tries < 30; tries += 1) {
		const now = await range(page);
		if (now === last) return now;
		last = now;
		await page.waitForTimeout(200);
	}
	return last;
}

async function scrollBodyTo(page: Page, y: number): Promise<number> {
	await page.evaluate((to) => {
		document.querySelector('.frame-body')!.scrollTop = to;
	}, y);
	return settledTop(page);
}

/** Two pages forward from the front of the People wall, with a body that can scroll. */
async function twoPagesForward(page: Page): Promise<string> {
	await page.goto('/people');
	await expect(page.locator('.frame-body a[href^="/people/"]').first()).toBeVisible();
	const first = await settledRange(page);
	const next = page.getByRole('button', { name: 'Next page' });
	await next.click();
	await expect.poll(() => range(page)).not.toBe(first);
	const second = await settledRange(page);
	await next.click();
	await expect.poll(() => range(page)).not.toBe(second);
	const third = await settledRange(page);

	const room = await page.evaluate(() => {
		const body = document.querySelector('.frame-body')!;
		return body.scrollHeight - body.clientHeight;
	});
	expect(
		room,
		'a page of people does not overflow the body, so there is no scroll to keep'
	).toBeGreaterThan(100);
	return third;
}

/**
 * Open a person by the name on a card already on screen, so the click itself does not scroll the body.
 * Pressed at its middle by coordinates: Playwright's `click` scrolls an element into view first,
 * which would move the very position this journey is about.
 */
async function openVisiblePerson(page: Page): Promise<void> {
	const href = await page.evaluate(() => {
		const body = document.querySelector('.frame-body')!.getBoundingClientRect();
		const links = [
			...document.querySelectorAll<HTMLAnchorElement>('.frame-body a.name[href^="/people/"]')
		];
		const shown = links.find((link) => {
			const box = link.getBoundingClientRect();
			return box.height > 0 && box.top >= body.top && box.bottom <= body.bottom;
		});
		return shown ? shown.getAttribute('href') : null;
	});
	expect(href, 'no name of a person wholly inside the scrolled body').not.toBeNull();
	const box = (await page.locator(`.frame-body a.name[href="${href}"]`).first().boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page).toHaveURL(new RegExp(`${href}(\\?|$)`));
	await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toBeVisible();
}

test("a wall's crumb brings back the page and the scroll, exactly as the browser's Back does", async ({
	page
}) => {
	const left = await twoPagesForward(page);
	const scrolled = await scrollBodyTo(page, 400);
	expect(scrolled, 'the body did not scroll').toBeGreaterThan(100);

	// The reference: the browser's own Back.
	await openVisiblePerson(page);
	await page.goBack();
	await expect(page).toHaveURL(/\/people(\?|$)/);
	const backRange = await settledRange(page);
	const backTop = await settledTop(page);
	expect(backRange, 'Back did not return to the page that was left').toBe(left);
	expect(
		backTop,
		'Back did not keep the scroll either, so parity would prove nothing'
	).toBeGreaterThan(0);

	// The same journey through the crumb, from the same place.
	await scrollBodyTo(page, 400);
	await openVisiblePerson(page);
	await pressCrumb(page, 'People');
	await expect(page).toHaveURL(/\/people(\?|$)/);
	const crumbRange = await settledRange(page);
	const crumbTop = await settledTop(page);

	expect(crumbRange, 'the crumb returned to a different page of the wall').toBe(backRange);
	expect(
		Math.abs(crumbTop - backTop),
		`the crumb restored the body to ${crumbTop}px where Back restored it to ${backTop}px`
	).toBeLessThanOrEqual(2);
});

test('a page turn from the foot pager lands at the top of the body', async ({ page }) => {
	await page.goto('/people');
	await expect(page.locator('.frame-body a[href^="/people/"]').first()).toBeVisible();
	const first = await settledRange(page);
	const scrolled = await scrollBodyTo(page, 400);
	expect(scrolled, 'the body did not scroll, so the turn has nothing to reset').toBeGreaterThan(
		100
	);

	await page.getByRole('button', { name: 'Next page' }).click();
	await expect.poll(() => range(page)).not.toBe(first);
	await settledRange(page);
	expect(await settledTop(page), 'the next page opened part-way down').toBe(0);
});
