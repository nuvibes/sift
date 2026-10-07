import { expect, test } from './test';
import { signInAsAdmin } from './admin';

/*
 * Nothing is cut off when the window gets smaller.
 *
 * ## The failure this exists for
 *
 * The page frame is a grid, and a column left implicit sizes itself to its content. And a track
 * sized by its content widens and never narrows. Make the window smaller and the frame's box comes
 * down while its column stays where it was, and because the layer above it clips rather than
 * scrolls, the right-hand column of pictures and the pager along the bottom are cut off with no
 * scrollbar. It feeds itself: the wall lays its rows out to the width it measures inside that
 * column, so the rows hold the column open. Widening the rail does it too, because that narrows the
 * same box without the window changing at all.
 *
 * ## Why it resizes rather than opening small
 *
 * THIS IS THE LOAD-BEARING PART OF THE TEST. Opened directly at 900px everything passes: the
 * content-sized column is sized a single time against a small box and is small. The fault only
 * appears when a box that was WIDE is asked to be narrow, so every case here loads wide and then
 * shrinks.
 *
 * ## What counts as a failure
 *
 * Anything with a real width whose right edge is past the right edge of the window. One pixel of
 * tolerance for sub-pixel rounding, which at fractional display scaling is ordinary; the fault this
 * catches is hundreds of pixels across hundreds of elements, so the threshold is not where the
 * sensitivity lies.
 */

const PAGES = ['/browse', '/favorites', '/tags', '/people', '/collections', '/downloads'];

/* The band where the rail collapses because the window is narrow rather than because somebody asked
 * for it. 1000 is inside it; 1400 is not. */
const NARROW_RAIL = { width: 1000, height: 800 };
const WIDE_RAIL = { width: 1400, height: 800 };

/** Wide enough to open at, then two sizes a real window is dragged down to. */
const WIDE = { width: 1600, height: 900 };
const NARROWER = [
	{ width: 1200, height: 800 },
	{ width: 960, height: 700 }
];

test('no screen spills past the window after it is made smaller', async ({ page }) => {
	await signInAsAdmin(page);

	/*
	 * A WALL WITH PICTURES ON IT, and this is the difference between a gate and a green light.
	 *
	 * The track that would not shrink is held open BY THE ROWS OF TILES, so an empty wall passes
	 * with the fix taken out. The fixture is a real page of files, in mixed shapes, because the
	 * wall justifies each row to the width it measured, which is precisely the content whose
	 * width becomes the floor the frame will not go below.
	 */
	const SHAPES = [
		{ media_type: 'video', width: 3840, height: 2160, duration_ms: 95_000 },
		{ media_type: 'video', width: 1080, height: 1920, duration_ms: 41_000 },
		{ media_type: 'image', width: 2400, height: 1600, duration_ms: 0 },
		{ media_type: 'gif', width: 640, height: 640, duration_ms: 2040 }
	];
	/* `thumb: true` says the still has been BUILT, which is not the same question as whether the
	 * image URL answers. Without it every tile draws as an import still coming in, which is a
	 * different and much narrower box. See e2e/browse.spec.ts, which needs the same field. */
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
	/* A real one-pixel image rather than a 404: a tile whose still fails draws the missing-preview
	 * placeholder, which is a different box again. */
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

	/** Everything sticking out to the right, named well enough to fix without re-measuring. */
	const spilling = () =>
		page.evaluate(() => {
			const out: { tag: string; cls: string; right: number }[] = [];
			for (const el of Array.from(document.querySelectorAll('*'))) {
				const box = el.getBoundingClientRect();
				if (box.width <= 2) continue;
				if (box.right <= window.innerWidth + 1) continue;
				/* A shut drawer waits just past the edge it slides in from, unseen: nothing a person
				   can see or scroll to is spilling. */
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
		// Opened wide first, on purpose. See the note above: shrinking is the whole test.
		await expect(page.locator('main')).toBeVisible();
		/* Wait for the wall to have actually laid rows out at the WIDE size. Measuring before the
		 * tiles are placed is measuring an empty screen, which proves nothing. */
		await page.waitForTimeout(800);

		for (const size of NARROWER) {
			await page.setViewportSize(size);
			/* A resize is answered by a ResizeObserver and then by a re-layout of the wall, so the
			 * measurement has to be taken after the browser has drawn, not in the same frame the
			 * viewport changed in, where every box still holds its old rectangle. */
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

/*
 * The rail's contents fit inside the rail.
 *
 * The brand is drawn as the WORDMARK when the rail is wide and as the MARK alone when it is narrow,
 * and that choice is made in script. A narrow rail decided by a media query alone would shrink the
 * rail while the component went on drawing the wordmark: a logo wider than the rail, hanging out
 * over the library.
 *
 * So the measurement here is deliberately of the BRAND against the RAIL rather than of any rule: it
 * does not care how the rail came to be narrow, only that what is in it fits.
 */
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
