import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Nothing is cut off when the window gets smaller. A content-sized grid column widens and never
 * narrows, and the clipping layer above shows no scrollbar. Every case loads WIDE and then shrinks:
 * opened small, the fault does not appear. A pixel of tolerance for rounding.
 */

const PAGES = ['/browse', '/favorites', '/tags', '/people', '/collections', '/downloads'];

/* The rail collapses because the window is narrow: 1000 is inside that band, 1400 is not. */
const NARROW_RAIL = { width: 1000, height: 800 };
const WIDE_RAIL = { width: 1400, height: 800 };

/** Opened wide, then dragged down to two sizes. */
const WIDE = { width: 1600, height: 900 };
const NARROWER = [
	{ width: 1200, height: 800 },
	{ width: 960, height: 700 }
];

test('no screen spills past the window after it is made smaller', async ({ page }) => {
	await signInAsAdmin(page);

	/* Tiles in mixed shapes: the justified rows are what hold the column open, so an empty wall
	 * passes without the fix. */
	const SHAPES = [
		{ media_type: 'video', width: 3840, height: 2160, duration_ms: 95_000 },
		{ media_type: 'video', width: 1080, height: 1920, duration_ms: 41_000 },
		{ media_type: 'image', width: 2400, height: 1600, duration_ms: 0 },
		{ media_type: 'gif', width: 640, height: 640, duration_ms: 2040 }
	];
	/* `thumb: true`, or every tile draws as a narrower import box (as in browse.spec.ts). */
	const items = Array.from({ length: 48 }, (_, index) => ({
		...SHAPES[index % SHAPES.length],
		id: `fixture-${String(index).padStart(3, '0')}`,
		favorite: false,
		rating: 6,
		concealed: false,
		thumb: true
	}));
	const page1 = { items, total: items.length, limit: 50, offset: 0 };

	await page.route('**/api/assets?*', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(page1) })
	);
	/* A real pixel: a failed still draws a different box again. */
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'image/png',
			body: Buffer.from(
				'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
				'base64'
			)
		})
	);
	await page.route('**/api/search?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ ...page1, applied: [] })
		})
	);

	/** Everything sticking out to the right, named well enough to fix. */
	const spilling = () =>
		page.evaluate(() => {
			const out: { tag: string; cls: string; right: number }[] = [];
			for (const el of Array.from(document.querySelectorAll('*'))) {
				const box = el.getBoundingClientRect();
				if (box.width <= 2) continue;
				if (box.right <= window.innerWidth + 1) continue;
				/* A shut drawer waits past the edge, unseen. */
				if (getComputedStyle(el).visibility === 'hidden') continue;
				out.push({
					tag: el.tagName,
					cls: String((el as HTMLElement).className ?? '').slice(0, 60),
					right: Math.round(box.right)
				});
			}
			return out;
		});

	for (const path of PAGES) {
		await page.setViewportSize(WIDE);
		await page.goto(path);
		// Opened wide first: shrinking is the whole test.
		await expect(page.locator('main')).toBeVisible();
		/* Rows laid out at the WIDE size first. */
		await page.waitForTimeout(800);

		for (const size of NARROWER) {
			await page.setViewportSize(size);
			/* After the ResizeObserver and the wall's re-layout have drawn. */
			await page.waitForTimeout(400);
			const over = await spilling();
			expect(
				over,
				`${path} at ${size.width}px: ${over.length} elements past the right edge, ` +
					`first ${over[0]?.tag}.${over[0]?.cls} ending at ${over[0]?.right}px`
			).toEqual([]);
		}
	}
});

/* The brand switches to the mark in script, so a rail narrowed by CSS alone would hang the
 * wordmark over the library: measured, the brand against the rail. */
test('nothing in the rail hangs out of the rail, at either width', async ({ page }) => {
	await signInAsAdmin(page);

	const fits = () =>
		page.evaluate(() => {
			const rail = document.querySelector('nav.rail');
			if (rail === null) return null;
			const edge = rail.getBoundingClientRect();
			const over = [];
			for (const el of Array.from(rail.querySelectorAll('*'))) {
				const box = el.getBoundingClientRect();
				if (box.width <= 0) continue;
				if (box.right > edge.right + 1 || box.left < edge.left - 1) {
					over.push({
						tag: el.tagName,
						cls: String(el.className ?? '').slice(0, 40),
						right: Math.round(box.right)
					});
				}
			}
			return { railRight: Math.round(edge.right), railWidth: Math.round(edge.width), over };
		});

	for (const size of [WIDE_RAIL, NARROW_RAIL]) {
		await page.setViewportSize(size);
		await page.goto('/browse');
		await expect(page.locator('nav.rail')).toBeVisible();
		await page.waitForTimeout(600);

		const seen = await fits();
		expect(seen, 'there is no rail to measure').not.toBeNull();
		expect(
			seen?.over,
			`at ${size.width}px the rail is ${seen?.railWidth}px and ${seen?.over.length} of its own ` +
				`children stick out of it, first ${seen?.over[0]?.tag}.${seen?.over[0]?.cls}`
		).toEqual([]);
	}
});
