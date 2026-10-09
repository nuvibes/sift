import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/* The same app at every size: the furniture keeps its pixels, nothing floating reaches under
 * the rail, and the pager touches the last row (`PageFrame`). Four widths, from the forced
 * narrow rail at 1024 to a 3440 monitor. */

const WIDTHS = [1024, 1440, 2560, 3440] as const;

/* A pageable library served by intercepting the API, in mixed shapes so the fill loop is tested. */
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
	// Otherwise the tile draws as still importing.
	thumb: true,
	original_filename: `file-${index}.jpg`
}));

async function serveLibrary(page: Page) {
	await page.route('**/api/assets?*', async (route) => {
		const asked = new URL(route.request().url());
		const limit = Number(asked.searchParams.get('limit') ?? 50);
		const anchor = asked.searchParams.get('from');
		// Answers at the anchor's position, as the server does; a top-up reads on from `after`.
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
	// A real element, not `networkidle`: the live feed keeps the network busy forever.
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

			// Press and hold past `--press-hold` (300ms) starts a selection.
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

			expect(floating!.x + floating!.width).toBeLessThanOrEqual(width + SLACK);
		});
	}
});

test.describe('the pager', () => {
	// Where the pager sits is `pager-and-marks.spec.ts`; here, what shares its corner.
	test('is not covered by the selection bar', async ({ page }) => {
		// The selection bar must clear the pager; asserted as boxes so a failure names the overlap.
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
		// Settled: the bar rises as it arrives.
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

		// A position, not a page number, so it can be shared.
		await expect(pager.getByRole('button', { name: 'Go to a position' })).toContainText(/of \d/);
	});
});

test.describe('the selection bar holds still while a page arrives', () => {
	/* The bar sits outside the body that animates, whose transform would re-anchor it to the
	 * content. Sampled every frame. */
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

		// A page turn empties the selection and the bar leaves; a far jump is the fault.
		const flung = frames.filter((y) => y !== null && Math.abs(y - resting) > 40);
		expect(
			flung,
			`The bar rests at ${resting} and was drawn at ${[...new Set(flung)].join(', ')} while the ` +
				"page arrived. It is inside the box that animates, so the body's transform re-anchored it."
		).toEqual([]);
	});
});
