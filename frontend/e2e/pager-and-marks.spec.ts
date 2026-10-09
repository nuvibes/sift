/*
 * Where the pager sits, and what a tile's badges look like: geometry, which a component test
 * would agree with in any arrangement. The pager is a footer track OUTSIDE the scroller and below
 * it. The marks lead with the count, keep one size at every notch, and draw the crossed-out eye
 * solid only where the file itself is hidden.
 */
import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { rewrite } from './routes';
import { tileSizeSlider } from './tile-size';

/** Every screen with a pager on it. */
const WALLS = ['/browse', '/favorites', '/people', '/sites', '/tags', '/collections'] as const;

function files(count: number) {
	return Array.from({ length: count }, (_, at) => ({
		id: `f${at}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true
	}));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	/* A file wall with nothing on it draws no pager, and this server's library starts empty. */
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: files(3), total: 3, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
});

/** The scrolling body (`.frame-body`, named stable in `PageFrame`) and the footer. */
async function frame(page: Page) {
	return page.evaluate(() => {
		const box = (selector: string) => {
			const element = document.querySelector(selector);
			if (!element) return null;
			const rect = element.getBoundingClientRect();
			return { top: Math.round(rect.top), bottom: Math.round(rect.bottom) };
		};
		return {
			body: box('.frame-body'),
			footer: box('.frame-footer'),
			/* A pager INSIDE the scroller is the failure. */
			inside: Boolean(document.querySelector('.frame-body .pager, .frame-body [class*="pager"]'))
		};
	});
}

test('the pager is below the scrolling body, on every wall', async ({ page }) => {
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();

		const { body, footer, inside } = await frame(page);
		expect(body, `${wall} has no scrolling body`).not.toBeNull();
		expect(footer, `${wall} has no footer`).not.toBeNull();

		expect(inside, `${wall} draws its pager inside the scroller`).toBe(false);
		/* Below, not merely present: a collapsed or misplaced track is still "visible". */
		expect(footer!.top, `${wall} draws its pager above the body`).toBeGreaterThanOrEqual(
			body!.bottom - 1
		);
	}
});

test('the frame reaches the bottom of the window on every wall', async ({ page }) => {
	/* The frame is `block-size: 100%`, so any sibling after it steals its height from the bottom.
	 * Asked of where the frame ends, since the shell clips and the document never scrolls. */
	for (const wall of [...WALLS, '/recent']) {
		await page.goto(wall);
		await expect(page.locator('.frame')).toBeVisible();

		const bottom = await page.evaluate(() =>
			Math.round(document.querySelector('.frame')!.getBoundingClientRect().bottom)
		);
		const height = page.viewportSize()!.height;
		expect(
			height - bottom,
			`${wall} ends ${height - bottom}px above the window, so something is drawn outside the frame`
		).toBeLessThanOrEqual(1);
	}
});

test('the pager lands in the same place on every wall, to the pixel', async ({ page }) => {
	/* One spot on every wall, though the headers above differ in height. */
	const bottoms = new Map<string, number>();
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();
		const { footer } = await frame(page);
		bottoms.set(wall, footer!.top);
	}

	const seen = [...new Set(bottoms.values())];
	expect(
		seen.length,
		`the pager starts at different heights: ${[...bottoms].map(([w, y]) => `${w}=${y}`).join(', ')}`
	).toBe(1);
});

test('the footer band is the pager and nothing else', async ({ page }) => {
	/* The pager's own height is the gap under the last row; padding the track too would count it
	 * twice. Compared to each other, not to a figure. */
	for (const wall of WALLS) {
		await page.goto(wall);
		await expect(page.locator('.frame-footer')).toBeVisible();

		const { track, pager } = await page.evaluate(() => {
			const height = (selector: string) => {
				const element = document.querySelector(selector);
				return element ? Math.round(element.getBoundingClientRect().height) : -1;
			};
			return { track: height('.frame-footer'), pager: height('.pager') };
		});

		expect(pager, `${wall} draws no pager`).toBeGreaterThan(0);
		expect(track - pager, `${wall} pads ${track - pager}px around its pager`).toBe(0);
	}
});

test('a short page keeps the pager at the bottom, not under the last row', async ({ page }) => {
	/* A short wall: the pager stays in the bottom quarter rather than climbing to the last row. */
	await page.goto('/browse');
	await expect(page.locator('.tile')).toHaveCount(3);
	await expect(page.locator('.frame-footer')).toBeVisible();

	const { footer } = await frame(page);
	const height = page.viewportSize()!.height;
	expect(footer!.top, 'the pager climbed up to the content').toBeGreaterThan(height * 0.75);
});

/* The marks are mocked: the fixture library has none, and a wall with no `.marks` cannot fail.
 * Under test is the ROW, not where its facts came from. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

/** The marks along a tile's top (`TOP_ROW` in `Tile`). */
const TOP_MARKS = [
	'appearance.tile.pinned',
	'appearance.tile.views',
	'appearance.tile.o_count',
	'appearance.tile.sharing',
	'appearance.tile.hidden'
];

/** A wall of files carrying every mark, so the row is at its widest. */
async function wallOfMarkedTiles(page: Page, count = 24) {
	const items = Array.from({ length: count }, (_, at) => ({
		id: `m${at}`,
		media_type: 'video',
		width: 1920,
		height: 1080,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		/* Every other tile is shared, so the row's length varies tile to tile. */
		shared: at % 2 === 0,
		shared_here: at % 2 === 0,
		hidden: at % 3 === 0,
		hidden_here: at % 6 === 0,
		views: at + 1
	}));
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items, total: count, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();

	/* A notch up: at the default these tiles fall under 140px, where marks are not drawn. */
	await (await tileSizeSlider(page)).fill('2');
	await expect(page.locator('.marks').first()).toBeVisible();
}

test('the count is the first mark on a tile, whatever else is drawn', async ({ page }) => {
	/* The count comes first, as ORDER within the row, so it does not move with the marks beside it.
	 * The account's answers are pinned so every mark waits for the hover alike (`restingFirst`). */
	await page.route('**/api/settings', async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		await rewrite<{ sections: { settings?: { key: string; value: unknown }[] }[] }>(
			route,
			(body) => {
				for (const section of body.sections) {
					for (const one of section.settings ?? []) {
						if (TOP_MARKS.includes(one.key)) one.value = 'hover';
					}
				}
			}
		);
	});
	await wallOfMarkedTiles(page);

	/* Through the tooltip wrapper that wraps every mark. */
	const rows = await page.evaluate(() =>
		[...document.querySelectorAll('.marks')].map((row) =>
			[...row.children].map((child) => {
				const mark = child.classList.contains('mark') ? child : child.querySelector('.mark');
				return (mark ?? child).className.toString();
			})
		)
	);

	expect(rows.length, 'no tile on this wall carries any mark').toBeGreaterThan(4);
	/* Or "the count is first" is true of identical rows. */
	expect(
		new Set(rows.map((row) => row.length)).size,
		'every row is the same shape'
	).toBeGreaterThan(1);
	for (const row of rows) {
		expect(row[0], `the count is not first in [${row.join(', ')}]`).toContain('seen');
	}
});

test('the marks are the same size at every notch of the slider', async ({ page }) => {
	/* One size at every notch that draws the marks (below 140px they are gone, a different
	 * decision). 1440 wide, where notches 1 and 2 straddle 180px with both drawing a mark. */
	await page.setViewportSize({ width: 1440, height: 900 });
	await wallOfMarkedTiles(page);
	const slider = await tileSizeSlider(page);
	await expect(slider).toBeEnabled();

	const heights: number[] = [];
	/* Zero removes the marks rather than resizing them. */
	for (const notch of [1, 2, 3]) {
		await slider.fill(String(notch));
		await page.waitForTimeout(300);
		/* The mark, not its wrapper, which takes its child's height. */
		const height = await page.evaluate(() => {
			const mark = document.querySelector('.marks .mark');
			return mark ? Math.round(mark.getBoundingClientRect().height) : 0;
		});
		if (height > 0) heights.push(height);
	}

	expect(heights.length, 'no notch drew a mark at all').toBeGreaterThan(1);
	expect(new Set(heights).size, `the marks measured ${heights.join(', ')} across the notches`).toBe(
		1
	);
});

test('the vault mark is solid only where the file itself is the one hidden', async ({ page }) => {
	/* Solid only from `hidden_here`: the fixture sets `hidden` on more tiles, so reading `hidden`
	 * alone fails. The window fits all twenty-four tiles the claim rests on. */
	await page.setViewportSize({ width: 1600, height: 1200 });
	await wallOfMarkedTiles(page);

	const eyes = await page.evaluate(() =>
		[...document.querySelectorAll('.mark.vaulted')].map((mark) => {
			const glyph = mark.querySelector('.icon, [class*="icon"]');
			const weight = glyph ? getComputedStyle(glyph).fontVariationSettings : '';
			return { filled: /FILL.\s*1/.test(weight) };
		})
	);

	expect(eyes.length, 'no tile carries a vault mark').toBeGreaterThan(2);
	const solid = eyes.filter((eye) => eye.filled).length;
	expect(solid, 'every vault mark is solid, so the fill says nothing').toBeLessThan(eyes.length);
	expect(solid, 'no vault mark is solid, so the fill says nothing').toBeGreaterThan(0);
});
