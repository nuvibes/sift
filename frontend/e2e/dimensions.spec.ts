import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * The same app at every screen size, laid out and measured.
 *
 * Three things only a laid-out page can prove:
 *
 *   1. **The furniture is the same number of pixels on every screen.** The rail, the top bar, the
 *      page footer and the controls do not grow with the window; what a bigger window buys is more
 *      content in it. `src/lib/design/dimensions.test.ts` proves nothing is WRITTEN as a share of
 *      the screen. Only a browser can say what a thing actually measured.
 *
 *   2. **Nothing that floats over a screen reaches under the rail.** A bar that is `position: fixed`
 *      and centred at `left: 50%` is centred on the WINDOW rather than the app. The static gate
 *      refuses the habit; this one refuses the outcome, which is what somebody sees.
 *
 *   3. **There is no band of empty ground above the pager.** The pager sits at the end of the
 *      content, under the last row, rather than pinned in a footer (which would leave a strip of
 *      nothing on every page whose rows do not fill the window). What is checked is that the two
 *      are touching; the pager moving between pages is the accepted side of the trade. See
 *      `PageFrame`.
 *
 * ## Why four widths
 *
 * 1024 is where the rail is forced narrow, 1440 is an ordinary laptop, 2560 and 3440 are wide
 * monitors. A fault that only shows at one of them is exactly the fault this rule exists for, and a
 * single-width test would pass on every one of them.
 */

const WIDTHS = [1024, 1440, 2560, 3440] as const;

/*
 * A library big enough to page, served by intercepting the API.
 *
 * Intercepted rather than imported for the reason the other grid specs do it: what is under test is
 * the LAYOUT, and a fixed set of proportions makes a page's shape something that can be asserted on
 * rather than something that depends on whatever files happen to be on the machine.
 *
 * Mixed shapes on purpose: widescreen, portrait, square. A page of one shape would fill in a
 * predictable number of files and would pass whether or not the fill loop worked.
 */
const SHAPES = [
	{ width: 1920, height: 1080 },
	{ width: 1080, height: 1920 },
	{ width: 1000, height: 1000 },
	{ width: 1600, height: 900 }
];

const LIBRARY = Array.from({ length: 400 }, (_, index) => ({
	id: `e2e-${String(index).padStart(4, '0')}`,
	media_type: 'image',
	...SHAPES[index % SHAPES.length],
	duration_ms: null,
	favorite: false,
	rating: null,
	concealed: false,
	// Without this the tile is drawn as still importing, so an assertion about what is on screen
	// passes for the wrong reason.
	thumb: true,
	original_filename: `file-${index}.jpg`
}));

async function serveLibrary(page: Page) {
	await page.route('**/api/assets?*', async (route) => {
		const asked = new URL(route.request().url());
		const limit = Number(asked.searchParams.get('limit') ?? 50);
		const anchor = asked.searchParams.get('from');
		// The real route resolves an anchor to the position the file sits at, and answers with that
		// offset rather than with the one it was sent. A mock that echoed the request back would let
		// a broken cursor pass.
		// A top-up names the last row the grid holds (`after`) and reads on from it, as the server
		// does: one seek on the sort's index rather than an offset.
		const after = asked.searchParams.get('after');
		const at = anchor
			? Math.max(
					0,
					LIBRARY.findIndex((item) => item.id === anchor)
				)
			: after
				? LIBRARY.findIndex((item) => item.id === after) + 1
				: Number(asked.searchParams.get('offset') ?? 0);

		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				items: LIBRARY.slice(at, at + limit),
				total: LIBRARY.length,
				limit,
				offset: at
			})
		});
	});
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
}

/** How far a measurement may drift and still count as the same. Sub-pixel layout, and rounding. */
const SLACK = 1;

/** The chrome whose size must not depend on the window, and how to find each piece. */
const FURNITURE: readonly { name: string; selector: string; axis: 'width' | 'height' }[] = [
	{ name: 'the rail', selector: 'nav[aria-label="Main"]', axis: 'width' },
	{ name: 'the pager', selector: 'nav[aria-label="Pages"]', axis: 'height' }
];

async function open(page: Page, width: number, path = '/browse') {
	await page.setViewportSize({ width, height: 900 });
	await signInAsAdmin(page);
	await serveLibrary(page);
	await page.goto(path);
	/*
	 * Waited on a real element rather than on `networkidle`.
	 *
	 * The app holds a live connection for the job feed, so the network is NEVER idle and every one
	 * of these would time out at thirty seconds, which reads as the page failing to load rather than as
	 * the wrong thing being waited for.
	 */
	await expect(page.locator('nav[aria-label="Pages"]')).toBeVisible();
}

async function box(page: Page, selector: string) {
	const found = page.locator(selector).first();
	if ((await found.count()) === 0) return null;
	return found.boundingBox();
}

test.describe('the furniture is the same size on every screen', () => {
	for (const piece of FURNITURE) {
		test(`${piece.name} measures the same at every width`, async ({ page }) => {
			const measured: { width: number; size: number }[] = [];

			for (const width of WIDTHS) {
				await open(page, width);
				const found = await box(page, piece.selector);
				if (found) measured.push({ width, size: found[piece.axis] });
			}

			expect(measured.length, `${piece.name} was not on screen at every width`).toBe(WIDTHS.length);

			const first = measured[0].size;
			for (const each of measured) {
				expect(
					Math.abs(each.size - first),
					`${piece.name} is ${each.size} at ${each.width} and ${first} at ${measured[0].width}. ` +
						'A bigger screen shows more, never bigger.'
				).toBeLessThanOrEqual(SLACK);
			}
		});
	}
});

test.describe('nothing floating reaches under the rail', () => {
	for (const width of WIDTHS) {
		test(`the selection bar clears the rail at ${width}`, async ({ page }) => {
			await open(page, width);

			const tile = page.locator('[role="listitem"]').first();
			await expect(tile).toBeVisible();

			// Press and hold is how a selection starts: the same gesture on every surface. 300ms
			// is `--press-hold`; a little over it, so this is never a race with the threshold.
			await tile.hover();
			await page.mouse.down();
			await page.waitForTimeout(450);
			await page.mouse.up();

			const bar = page.locator('[role="region"][aria-label="Selection"]');
			await expect(bar).toBeVisible();

			const rail = await box(page, 'nav[aria-label="Main"]');
			const floating = await bar.boundingBox();
			expect(floating).not.toBeNull();

			if (rail) {
				expect(
					floating!.x,
					`The selection bar starts at ${floating!.x} and the rail ends at ${rail.x + rail.width}. ` +
						'It is centred on the window instead of on the app.'
				).toBeGreaterThanOrEqual(rail.x + rail.width - SLACK);
			}

			// ...and it does not run off the other end either: a ceiling measured against the
			// window is too generous.
			expect(floating!.x + floating!.width).toBeLessThanOrEqual(width + SLACK);

			// Whether it also clears the pager is asked below, on its own, because that is a rule about
			// two pieces of furniture rather than about this one.
		});
	}
});

test.describe('the pager', () => {
	/*
	 * WHERE the pager sits is `e2e/pager-and-marks.spec.ts` and is deliberately not repeated here:
	 * one rule, one place. What belongs in this file is what the pager has to share the bottom of the
	 * screen WITH, which is geometry between two components and is exactly what this file is for.
	 */
	test('is not covered by the selection bar', async ({ page }) => {
		/*
		 * They want the same corner. The selection bar is a rounded slab over the bottom of the
		 * screen; if it does not clear a pager in a footer track, every button on the pager is
		 * visible, enabled and unpressable, and a wall with anything picked cannot be paged at all.
		 * Playwright says so in as many words ("intercepts pointer events"), and a person just
		 * presses twice and gives up.
		 *
		 * Asserted as boxes rather than by pressing, so the failure names the overlap instead of
		 * arriving as a thirty-second timeout.
		 */
		await open(page, 1280);

		const pagerBox = await box(page, 'nav[aria-label="Pages"]');
		expect(pagerBox, 'there is no pager on this wall').not.toBeNull();

		const tile = page.locator('[role="listitem"]').first();
		await expect(tile).toBeVisible();
		await tile.hover();
		await page.mouse.down();
		await page.waitForTimeout(450);
		await page.mouse.up();

		const bar = page.locator('[role="region"][aria-label="Selection"]');
		await expect(bar).toBeVisible();
		// Settled, not merely visible: the bar rises as it arrives, so its first frame is above where
		// it comes to rest and a measurement taken then is a picture of the movement.
		await page.waitForTimeout(400);
		const barBox = (await bar.boundingBox())!;

		expect(
			barBox.y + barBox.height,
			`The selection bar ends at ${Math.round(barBox.y + barBox.height)} and the pager starts at ` +
				`${Math.round(pagerBox!.y)}. The bar is drawn over the pager, so the way out of the page ` +
				'cannot be pressed while anything is picked.'
		).toBeLessThanOrEqual(pagerBox!.y + SLACK);
	});

	test('says where you are rather than which page you are on', async ({ page }) => {
		await open(page, 1440);
		const pager = page.locator('nav[aria-label="Pages"]');
		await expect(pager).toBeVisible();

		// A position, not a page number: "page 3" is a different set of files on a different
		// window, so it could not be shared or bookmarked.
		await expect(pager.getByRole('button', { name: 'Go to a position' })).toContainText(/of \d/);
	});
});

test.describe('the selection bar holds still while a page arrives', () => {
	/*
	 * A page arriving fades and rises the frame's body, and for the length of that movement the
	 * body carries a transform. An element with a transform on it is the containing block for
	 * everything absolutely positioned inside it. So a bar anchored to the bottom of the SCREEN,
	 * rendered in the body, would re-anchor to the bottom of the CONTENT for those frames and
	 * flicker out of existence at the moment somebody is looking at what they have picked. The bar
	 * is rendered in the frame's floating slot, outside the box that moves.
	 *
	 * Sampled every frame rather than before-and-after, because before-and-after cannot see it.
	 */
	test('rather than jumping to the bottom of the content and back', async ({ page }) => {
		await open(page, 1440);

		const tile = page.locator('.tile-frame').first();
		await tile.hover();
		await page.mouse.down();
		await page.waitForTimeout(450);
		await page.mouse.up();

		const bar = page.locator('[role="region"][aria-label="Selection"]');
		await expect(bar).toBeVisible();
		await page.waitForTimeout(300);
		const resting = Math.round((await bar.boundingBox())!.y);

		const sampling = page.evaluate(async () => {
			const seen: (number | null)[] = [];
			for (let frame = 0; frame < 30; frame += 1) {
				const found = document.querySelector('[role="region"][aria-label="Selection"]');
				seen.push(found ? Math.round(found.getBoundingClientRect().y) : null);
				await new Promise(requestAnimationFrame);
			}
			return seen;
		});
		await page
			.getByRole('navigation', { name: 'Pages' })
			.getByRole('button', { name: 'Next page' })
			.click();
		const frames = await sampling;

		/*
		 * A page turn empties the selection (the picked files are not on the new page), so the bar
		 * legitimately LEAVES here, fading and sliding a few pixels as anything leaving does, and
		 * then it is gone. That small movement is allowed. What is not is the fault: the bar
		 * drawn seven hundred pixels down the page, at the bottom of the content, and coming back.
		 */
		const flung = frames.filter((y) => y !== null && Math.abs(y - resting) > 40);
		expect(
			flung,
			`The bar rests at ${resting} and was drawn at ${[...new Set(flung)].join(', ')} while the ` +
				"page arrived. It is inside the box that animates, so the body's transform re-anchored it."
		).toEqual([]);
	});
});
